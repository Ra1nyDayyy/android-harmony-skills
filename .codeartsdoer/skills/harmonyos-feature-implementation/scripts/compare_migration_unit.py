#!/usr/bin/env python3
"""Run and immutably seal deterministic comparison for one page-state attempt."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from compare_behavior import compare_behavior, compare_navigation, compare_side_effects
from compare_component_tree import compare_carrier, compare_components
from compare_geometry import compare_geometry
from compare_screenshot import compare_screenshot
from comparison_common import ComparisonResult, file_sha256, load_json, write_new_json


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


def _verified_evidence(directory: Path) -> tuple[dict[str, Any], dict[str, Any], list[Any], dict[str, Any], Path]:
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


def _android_evidence_id(contract: dict[str, Any], state_id: str) -> str:
    for state in contract.get("states", []):
        if isinstance(state, dict) and state.get("state_id") == state_id:
            records = state.get("records")
            if isinstance(records, list) and len(records) == 1 and isinstance(records[0], dict):
                evidence = records[0].get("android_evidence")
                if isinstance(evidence, dict) and str(evidence.get("evidence_id", "")):
                    return str(evidence["evidence_id"])
    raise ValueError(f"Page contract has no unique Android evidence for state: {state_id}")


def _expected_screenshot(contract: dict[str, Any], actual_dir: Path, evidence_id: str) -> Path:
    records = contract.get("android_evidence_hashes") if isinstance(contract.get("android_evidence_hashes"), list) else []
    record = next((item for item in records if isinstance(item, dict) and item.get("evidence_id") == evidence_id), None)
    if record is None:
        raise ValueError(f"Page contract lacks Android evidence hash: {evidence_id}")
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
    contract: dict[str, object], evidence_dir: Path, output_dir: Path
) -> list[ComparisonResult]:
    actual_dir = Path(evidence_dir).resolve(strict=True)
    output = Path(output_dir).absolute()
    if output.exists():
        raise ValueError(f"Comparison output exists; overwrite is prohibited: {output}")
    if not isinstance(contract, dict):
        raise ValueError("Page contract must be an object")
    metadata, snapshot, trace, assertions, actual_screenshot = _verified_evidence(actual_dir)
    if metadata.get("page_id") != contract.get("page_id"):
        raise ValueError("UiTest page differs from Phase 2 page contract")
    evidence_id = _android_evidence_id(contract, str(metadata.get("state_id", "")))
    expected_screenshot = _expected_screenshot(contract, actual_dir, evidence_id)

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=str(output.parent)))
    try:
        carrier = compare_carrier(contract, metadata)
        components = compare_components(contract, snapshot)
        geometry = compare_geometry(contract, snapshot)
        screenshot = compare_screenshot(contract, expected_screenshot, actual_screenshot, staging)
        behavior = compare_behavior(contract, metadata, assertions)
        side_effect = compare_side_effects(contract, assertions)
        navigation = compare_navigation(contract, trace)
        results = [carrier, components, geometry, screenshot, behavior, side_effect, navigation]
        for result in results:
            write_new_json(staging / "results" / f"{result.comparison_id}.json", result.to_dict())
        write_new_json(staging / "structural-diff.json", {"carrier": carrier.to_dict(), "component_tree": components.to_dict(), "navigation": navigation.to_dict()})
        write_new_json(staging / "geometry-diff.json", geometry.to_dict())
        write_new_json(staging / "pixel-diff.json", screenshot.to_dict())
        write_new_json(staging / "behavior-diff.json", {"behavior": behavior.to_dict(), "side_effect": side_effect.to_dict()})
        write_new_json(staging / "verdict.json", {
            "schema_version": "comparison-result-v1", "page_id": contract.get("page_id"),
            "state_id": metadata.get("state_id"), "machine_verdict": "PASS" if all(item.passed for item in results) else "FAIL",
            "results": [item.to_dict() for item in results],
        })
        _seal(staging)
        staging.rename(output)
        return results
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    try:
        contract = load_json(Path(args.contract), "page contract")
        results = compare_page_state(contract, Path(args.evidence_dir), Path(args.output_dir))
        print(json.dumps({"passed": all(item.passed for item in results), "results": [item.to_dict() for item in results]}, ensure_ascii=False, indent=2))
        return 0 if all(item.passed for item in results) else 1
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
