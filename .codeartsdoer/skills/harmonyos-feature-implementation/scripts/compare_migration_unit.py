#!/usr/bin/env python3
"""Run and immutably seal deterministic comparison for one page-state attempt."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any

from compare_behavior import compare_behavior, compare_navigation, compare_side_effects
from compare_component_tree import compare_carrier, compare_components
from compare_geometry import compare_geometry
from compare_screenshot import compare_screenshot
from comparison_common import ComparisonResult, file_sha256, load_json, write_new_json


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _write_new_text(path: Path, value: str) -> None:
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _regular_evidence_file(directory: Path, name: str) -> Path:
    path = directory / name
    if not path.is_file() or path.is_symlink() or path.parent.resolve() != directory.resolve():
        raise ValueError(f"Missing or unsafe UiTest evidence file: {name}")
    return path


def _manifest_entries(directory: Path, label: str) -> dict[str, str]:
    manifest = _regular_evidence_file(directory, "manifest.sha256")
    committed = _regular_evidence_file(directory, "COMMITTED")
    manifest_sha = file_sha256(manifest)
    committed_text = committed.read_text(encoding="utf-8").strip()
    if committed_text != manifest_sha and f"manifest_sha256={manifest_sha}" not in committed_text:
        raise ValueError(f"{label} COMMITTED marker does not bind manifest.sha256")
    entries: dict[str, str] = {}
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        parts = line.split("  ", 1)
        if len(parts) != 2 or not SHA256_RE.fullmatch(parts[0]):
            raise ValueError(f"{label} manifest line is malformed: {number}")
        relative = Path(parts[1])
        if relative.is_absolute() or ".." in relative.parts or parts[1] in entries:
            raise ValueError(f"{label} manifest path is unsafe or duplicated: {parts[1]}")
        entries[parts[1]] = parts[0]
    if any(path.is_symlink() for path in directory.rglob("*")):
        raise ValueError(f"{label} contains a symbolic link")
    actual = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and path.name not in {"manifest.sha256", "COMMITTED"}
    }
    if set(entries) != actual:
        raise ValueError(f"{label} manifest file set differs")
    for relative, digest in entries.items():
        path = directory / relative
        if path.is_symlink() or file_sha256(path) != digest:
            raise ValueError(f"{label} manifest hash differs: {relative}")
    return entries


def _freeze_tree(directory: Path) -> None:
    for path in sorted(directory.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    directory.chmod(0o555)


def verify_comparison_output(directory: Path) -> dict[str, Any]:
    root = Path(directory).resolve(strict=True)
    _manifest_entries(root, "Comparison output")
    verdict = load_json(_regular_evidence_file(root, "verdict.json"), "comparison verdict")
    if not isinstance(verdict, dict) or verdict.get("schema_version") != "comparison-result-v1":
        raise ValueError("Comparison verdict schema differs")
    for field in ("page_contract_sha256", "page_contract_registry_sha256", "input_lock_sha256", "evidence_manifest_sha256"):
        if not SHA256_RE.fullmatch(str(verdict.get(field, ""))):
            raise ValueError(f"Comparison verdict lacks frozen {field}")
    results = verdict.get("results")
    required_categories = {"carrier", "component-tree", "geometry", "screenshot", "behavior", "side-effect", "navigation"}
    if not isinstance(results, list) or len(results) != 7:
        raise ValueError("Comparison verdict must contain seven deterministic results")
    result_ids = [str(row.get("comparison_id", "")) for row in results if isinstance(row, dict)]
    categories = [str(row.get("category", "")) for row in results if isinstance(row, dict)]
    if len(result_ids) != 7 or len(set(result_ids)) != 7 or set(categories) != required_categories:
        raise ValueError("Comparison verdict result identities/categories differ")
    for row in results:
        result_path = root / "results" / f"{row['comparison_id']}.json"
        if load_json(result_path, "sealed comparison result") != row:
            raise ValueError(f"Comparison result differs from verdict: {row['comparison_id']}")
    expected_verdict = "PASS" if all(row.get("passed") is True for row in results) else "FAIL"
    if verdict.get("machine_verdict") != expected_verdict:
        raise ValueError("Comparison machine verdict differs from result aggregation")
    required_files = {
        "behavior-diff.json", "diff.png", "geometry-diff.json", "overlay.png",
        "pixel-diff.json", "structural-diff.json", "verdict.json",
        *{f"results/{identity}.json" for identity in result_ids},
    }
    sealed_files = {
        path.relative_to(root).as_posix() for path in root.rglob("*")
        if path.is_file() and path.name not in {"manifest.sha256", "COMMITTED"}
    }
    if sealed_files != required_files:
        raise ValueError("Comparison sealed output file set differs")
    for path in root.rglob("*"):
        if path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"Comparison output is not recursively frozen: {path}")
    return verdict


def _verified_evidence(directory: Path) -> tuple[dict[str, Any], dict[str, Any], list[Any], dict[str, Any], Path]:
    _manifest_entries(directory, "UiTest evidence")
    metadata_path = _regular_evidence_file(directory, "ui-test-snapshot-metadata.json")
    metadata = load_json(metadata_path, "UiTest metadata")
    if not isinstance(metadata, dict) or metadata.get("schema_version") != "ui-test-snapshot-evidence-v1":
        raise ValueError("UiTest metadata schema differs")
    names = {
        "result": str(metadata.get("result_path", "")),
        "trace": str(metadata.get("operation_trace_path", "")),
        "screenshot": str(metadata.get("screenshot_path", "")),
    }
    expected_names = {
        "result": "ui-test-snapshot.json", "trace": "ui-test-snapshot-operation-trace.json",
        "screenshot": "ui-test-snapshot.png",
    }
    if names != expected_names:
        raise ValueError("UiTest evidence paths are not canonical")
    paths = {key: _regular_evidence_file(directory, value) for key, value in names.items()}
    for key, hash_field in (("result", "result_sha256"), ("trace", "operation_trace_sha256"), ("screenshot", "screenshot_sha256")):
        if metadata.get(hash_field) != file_sha256(paths[key]):
            raise ValueError(f"UiTest {hash_field} differs")
    snapshot = load_json(paths["result"], "UiTest component snapshot")
    trace = load_json(paths["trace"], "UiTest operation trace")
    assertions = load_json(_regular_evidence_file(directory, "assertions.json"), "functional assertions")
    if not isinstance(snapshot, dict) or not isinstance(trace, list) or not isinstance(assertions, dict):
        raise ValueError("UiTest comparison evidence has invalid JSON types")
    expected_probe = f"{metadata.get('page_id')}::{metadata.get('state_id')}"
    if snapshot.get("probe_id") != expected_probe or metadata.get("probe_id") != expected_probe:
        raise ValueError("UiTest page-state identity differs")
    return metadata, snapshot, trace, assertions, paths["screenshot"]


def _state_record(contract: dict[str, Any], state_id: str, source_env_id: str) -> dict[str, Any]:
    for state in contract.get("states", []):
        if isinstance(state, dict) and state.get("state_id") == state_id:
            records = state.get("records")
            matches = [row for row in records if isinstance(row, dict) and row.get("env_id") == source_env_id] if isinstance(records, list) else []
            if len(matches) == 1:
                evidence = matches[0].get("android_evidence")
                if isinstance(evidence, dict) and str(evidence.get("evidence_id", "")) and evidence.get("source_geometry"):
                    return matches[0]
    raise ValueError(f"Page contract has no unique evidence for state {state_id} and source environment {source_env_id}")


def _canonical_contract_sha256(contract: dict[str, Any]) -> str:
    payload = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _contract_locks(
    contract: dict[str, Any], contract_path: Path, registry_path: Path, input_lock_path: Path
) -> dict[str, str]:
    contract_file = Path(contract_path).resolve(strict=True)
    registry = Path(registry_path).resolve(strict=True)
    input_lock_file = Path(input_lock_path).resolve(strict=True)
    frozen_contract = load_json(contract_file, "frozen page contract")
    if frozen_contract != contract:
        raise ValueError("Supplied page contract differs from frozen contract file")
    contract_sha = _canonical_contract_sha256(contract)
    with registry.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    matches = [row for row in rows if row.get("page_id") == contract.get("page_id") and row.get("status") == "FROZEN"]
    if len(matches) != 1 or matches[0].get("contract_sha256") != contract_sha:
        raise ValueError("Page contract registry does not bind the frozen contract")
    expected_path = (registry.parent / str(matches[0].get("relative_path", ""))).resolve()
    if expected_path != contract_file:
        raise ValueError("Page contract path differs from registry")
    input_lock = load_json(input_lock_file, "Stage 4 input lock")
    registry_lock = input_lock.get("page_contract_registry") if isinstance(input_lock, dict) else None
    contract_locks = input_lock.get("page_contracts") if isinstance(input_lock, dict) else None
    if (
        not isinstance(registry_lock, dict)
        or registry_lock.get("sha256") != file_sha256(registry)
        or not isinstance(contract_locks, list)
        or len([row for row in contract_locks if isinstance(row, dict) and row.get("page_id") == contract.get("page_id") and row.get("sha256") == contract_sha and row.get("relative_path") == matches[0].get("relative_path")]) != 1
    ):
        raise ValueError("Stage 4 input lock does not bind the page contract registry and contract")
    return {
        "page_contract_sha256": contract_sha,
        "page_contract_registry_sha256": file_sha256(registry),
        "input_lock_sha256": file_sha256(input_lock_file),
    }


def _expected_screenshot(contract: dict[str, Any], actual_dir: Path, evidence_id: str) -> Path:
    records = contract.get("android_evidence_hashes") if isinstance(contract.get("android_evidence_hashes"), list) else []
    matches = [item for item in records if isinstance(item, dict) and item.get("evidence_id") == evidence_id]
    if len(matches) != 1:
        raise ValueError(f"Page contract lacks Android evidence hash: {evidence_id}")
    record = matches[0]
    if record.get("pending_runtime_verify") is True:
        # gmi honest baseline: no frozen Android evidence exists for this page.
        # Pixel-level comparison against Android is not a meaningful verdict
        # here; HEVD UiTest/business assertions carry the verification facts.
        raise ValueError(
            f"Android baseline is PENDING_RUNTIME_VERIFY for {evidence_id}; "
            "frozen-screenshot comparison is not applicable"
        )
    relative = Path(str(record.get("relative_path", f"inputs/android-evidence/{evidence_id}")))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Android evidence relative path is unsafe")
    candidates = []
    for root in (actual_dir, *actual_dir.parents):
        candidates.append(root / relative / "screenshot.png")
        candidates.append(root / "inputs" / "android-evidence" / evidence_id / "screenshot.png")
    screenshot = next((path for path in candidates if path.is_file() and not path.is_symlink()), None)
    if screenshot is None:
        raise ValueError(f"Frozen Android screenshot is missing: {evidence_id}")
    if record.get("screenshot_sha256") != file_sha256(screenshot):
        raise ValueError(f"Frozen Android screenshot hash differs: {evidence_id}")
    return screenshot


def _seal(staging: Path) -> None:
    files = sorted(
        (path for path in staging.rglob("*") if path.is_file() and path.name not in {"manifest.sha256", "COMMITTED"}),
        key=lambda path: path.relative_to(staging).as_posix(),
    )
    manifest = "".join(
        f"{file_sha256(path)}  {path.relative_to(staging).as_posix()}\n" for path in files
    )
    _write_new_text(staging / "manifest.sha256", manifest)
    _write_new_text(staging / "COMMITTED", file_sha256(staging / "manifest.sha256") + "\n")


def compare_page_state(
    contract: dict[str, object], evidence_dir: Path, output_dir: Path, *,
    state_id: str, source_env_id: str, contract_path: Path,
    registry_path: Path, input_lock_path: Path,
) -> list[ComparisonResult]:
    actual_dir = Path(evidence_dir).resolve(strict=True)
    output = Path(output_dir).absolute()
    if output.exists():
        raise ValueError(f"Comparison output exists; overwrite is prohibited: {output}")
    if not isinstance(contract, dict):
        raise ValueError("Page contract must be an object")
    locks = _contract_locks(contract, contract_path, registry_path, input_lock_path)
    metadata, snapshot, trace, assertions, actual_screenshot = _verified_evidence(actual_dir)
    if metadata.get("page_id") != contract.get("page_id") or metadata.get("state_id") != state_id:
        raise ValueError("UiTest page differs from Phase 2 page contract")
    selected_record = _state_record(contract, state_id, source_env_id)
    evidence_id = str(selected_record["android_evidence"]["evidence_id"])
    expected_screenshot = _expected_screenshot(contract, actual_dir, evidence_id)
    selected_contract = dict(contract)
    selected_contract["states"] = [{"state_id": state_id, "records": [selected_record]}]
    selected_contract["source_geometry"] = [selected_record["android_evidence"]["source_geometry"]]

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=str(output.parent)))
    try:
        carrier = compare_carrier(selected_contract, metadata)
        components = compare_components(selected_contract, snapshot)
        geometry = compare_geometry(selected_contract, snapshot)
        screenshot = compare_screenshot(selected_contract, snapshot, expected_screenshot, actual_screenshot, staging)
        behavior = compare_behavior(selected_contract, metadata, assertions)
        side_effect = compare_side_effects(selected_contract, assertions)
        navigation = compare_navigation(selected_contract, trace)
        results = [carrier, components, geometry, screenshot, behavior, side_effect, navigation]
        for result in results:
            write_new_json(staging / "results" / f"{result.comparison_id}.json", result.to_dict())
        write_new_json(staging / "structural-diff.json", {"carrier": carrier.to_dict(), "component_tree": components.to_dict(), "navigation": navigation.to_dict()})
        write_new_json(staging / "geometry-diff.json", geometry.to_dict())
        write_new_json(staging / "pixel-diff.json", screenshot.to_dict())
        write_new_json(staging / "behavior-diff.json", {"behavior": behavior.to_dict(), "side_effect": side_effect.to_dict()})
        write_new_json(staging / "verdict.json", {
            "schema_version": "comparison-result-v1", "page_id": contract.get("page_id"),
            "state_id": state_id, "source_env_id": source_env_id,
            **locks, "evidence_manifest_sha256": file_sha256(actual_dir / "manifest.sha256"),
            "machine_verdict": "PASS" if all(item.passed for item in results) else "FAIL",
            "results": [item.to_dict() for item in results],
        })
        _seal(staging)
        staging.rename(output)
        _freeze_tree(output)
        verify_comparison_output(output)
        return results
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--state-id", required=True)
    parser.add_argument("--source-env-id", required=True)
    parser.add_argument("--contract-registry", required=True)
    parser.add_argument("--input-lock", required=True)
    args = parser.parse_args()
    try:
        contract = load_json(Path(args.contract), "page contract")
        results = compare_page_state(
            contract, Path(args.evidence_dir), Path(args.output_dir),
            state_id=args.state_id, source_env_id=args.source_env_id,
            contract_path=Path(args.contract), registry_path=Path(args.contract_registry),
            input_lock_path=Path(args.input_lock),
        )
        print(json.dumps({"passed": all(item.passed for item in results), "results": [item.to_dict() for item in results]}, ensure_ascii=False, indent=2))
        return 0 if all(item.passed for item in results) else 1
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
