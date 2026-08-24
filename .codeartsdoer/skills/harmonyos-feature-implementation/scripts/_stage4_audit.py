#!/usr/bin/env python3
"""Independent, read-only audit helpers for the Phase 4 acceptance gate."""

from __future__ import annotations

SCRIPT_INTERFACE = "internal-module"
SCRIPT_INTERFACE_REASON = "Read-only audit library imported by the Phase 4 validator."

import hashlib
import json
import re
import stat
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from uitest_snapshot import validate_uitest_evidence

from _common import (
    PHASE4_CATEGORY_ORDER,
    build_project_snapshot,
    command_passed,
    frozen_category_contracts,
    frozen_output_verdict,
    load_json,
    png_dimensions,
    safe_relative_path,
    selector_is_present,
    sha256_file,
    validate_frozen_command,
    validate_hap,
    verify_manifest,
)


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BUILD_SEQUENCE = list(PHASE4_CATEGORY_ORDER[:4])
EVIDENCE_SEQUENCE = list(PHASE4_CATEGORY_ORDER[4:])
REQUIRED_ASSERTION_KINDS = {"VISUAL_STATE", "BUSINESS_RESULT", "INTERACTION"}
SERIAL_CATEGORIES = {
    "BUNDLE_CHECK", "DEVICE_CHECK", "CLEAN_INSTALL", "SEED_RESET", "NETWORK_PROFILE",
    "PERMISSION_PROFILE", "LAUNCH", "NAVIGATE", "BUSINESS_ASSERT",
    "SCREENSHOT_CAPTURE", "UITEST_SNAPSHOT_CAPTURE",
}
SOURCE_EXTENSIONS = {".ets", ".ts", ".js", ".json", ".json5", ".c", ".cc", ".cpp", ".h", ".hpp"}
PROJECT_EXCLUDED_PARTS = {
    ".git", ".idea", ".hvigor", "build", "dist", "coverage", "node_modules",
    "oh_modules", "__pycache__", ".pytest_cache",
}


def add(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


def ownership_from(manifest: dict[str, Any]) -> dict[str, str]:
    value = manifest.get("ownership")
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    legacy = manifest.get("roles")
    if isinstance(legacy, dict):
        return {
            "implementation_lead_id": str(legacy.get("implementation_lead", "")),
            "visual_asset_agent_id": str(legacy.get("asset_agent", "")),
            "verification_executor_id": str(legacy.get("verification_executor", "")),
            "parity_acceptance_agent_id": str(legacy.get("parity_checker", "")),
        }
    return {}


def indexed(
    rows: list[dict[str, str]], key: str, label: str, errors: list[str]
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        value = row.get(key, "")
        if not value:
            add(errors, f"{label} row lacks {key}")
        elif value in result:
            add(errors, f"Duplicate {label} {key}: {value}")
        else:
            result[value] = row
    return result


def parse_iso(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is empty")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is not an ISO timestamp") from exc


def json_string_array(value: str, label: str, *, allow_empty: bool = True) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be a JSON string array") from exc
    if not isinstance(parsed, list) or any(not isinstance(item, str) or not item for item in parsed):
        raise ValueError(f"{label} must be a JSON string array")
    if not allow_empty and not parsed:
        raise ValueError(f"{label} must not be empty")
    if parsed != sorted(set(parsed)):
        raise ValueError(f"{label} must be sorted and contain no duplicates")
    return parsed


def package_files(directory: Path) -> set[str]:
    return {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and path.name not in {"manifest.sha256", "COMMITTED"}
    }


def package_summary(directory: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in package: {path}")
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(directory).as_posix(),
                    "sha256": sha256_file(path),
                    "size": path.stat().st_size,
                }
            )
    canonical = json.dumps(
        entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return {
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "size": sum(item["size"] for item in entries),
        "file_count": len(entries),
        "entries": entries,
    }


def require_read_only(root: Path, label: str) -> None:
    for path in (root, *root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"{label} contains a symbolic link: {path}")
        if path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"{label} contains a writable sealed path: {path}")


def verify_sealed_package(directory: Path, package_id: str, lifecycle: str) -> None:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError(f"Missing or unsafe sealed package: {directory}")
    failures = verify_manifest(directory, package_files(directory))
    if failures:
        raise ValueError("; ".join(failures))
    marker = directory / "COMMITTED"
    manifest = directory / "manifest.sha256"
    if not marker.is_file() or not manifest.is_file():
        raise ValueError(f"{package_id} lacks COMMITTED/manifest.sha256")
    expected = f"{package_id} {lifecycle} manifest_sha256={sha256_file(manifest)}"
    if not marker.read_text(encoding="utf-8").strip().startswith(expected):
        raise ValueError(f"{package_id} COMMITTED does not bind its manifest")
    require_read_only(directory, package_id)


def verify_android_package(directory: Path, evidence_id: str) -> dict[str, Any]:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError(f"Missing Android evidence package: {directory}")
    failures = verify_manifest(directory, package_files(directory))
    if failures:
        raise ValueError("; ".join(failures))
    marker = directory / "COMMITTED"
    if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != evidence_id:
        raise ValueError(f"Android evidence COMMITTED does not bind {evidence_id}")
    required = ("metadata.json", "screenshot.png", "layout.json", "steps.md")
    if any(not (directory / name).is_file() for name in required):
        raise ValueError(f"Android evidence {evidence_id} lacks required files")
    metadata = load_json(directory / "metadata.json")
    if metadata.get("evidence_id") != evidence_id or metadata.get("status") != "SEALED":
        raise ValueError(f"Android evidence metadata differs: {evidence_id}")
    png_dimensions(directory / "screenshot.png")
    if load_json(directory / "layout.json") in ({}, [], None):
        raise ValueError(f"Android layout is empty: {evidence_id}")
    require_read_only(directory, f"Android evidence {evidence_id}")
    return metadata


def manifest_entries(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if "  " not in line:
            raise ValueError(f"Malformed manifest line {path}:{number}")
        digest, name = line.split("  ", 1)
        pure = PurePosixPath(name)
        if (
            not SHA256_RE.fullmatch(digest) or pure.is_absolute() or ".." in pure.parts
            or not pure.parts or name in result
        ):
            raise ValueError(f"Unsafe manifest entry {path}:{number}")
        result[name] = digest
    return result


def verify_upstream_closure(
    root: Path,
    report_name: str,
    manifest_name: str,
    exact_excludes: set[str],
    dir_excludes: set[str] | None = None,
) -> None:
    report = root / report_name
    manifest = root / manifest_name
    closed = root / "CLOSED"
    if not report.is_file() or not manifest.is_file() or not closed.is_file():
        raise ValueError(f"Upstream closure is incomplete: {root}")
    if closed.read_text(encoding="utf-8") != sha256_file(report) + "\n":
        raise ValueError(f"Upstream CLOSED differs from its report: {root}")
    expected = manifest_entries(manifest)
    actual: dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symbolic link in upstream closure: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        name = relative.as_posix()
        if name in exact_excludes:
            continue
        if dir_excludes and any(part in dir_excludes for part in relative.parts):
            continue
        if path.name.endswith((".lock", ".tmp")):
            continue
        actual[name] = path
    if set(expected) != set(actual):
        raise ValueError(f"Upstream closure file set changed: {root}")
    for name, digest in expected.items():
        if sha256_file(actual[name]) != digest:
            raise ValueError(f"Upstream closure hash changed: {root}/{name}")


def closure_excluded(relative: Path) -> bool:
    if relative.as_posix() in {
        "stage-04-gate-report.json", "stage-04-closure-manifest.sha256", "CLOSED",
    }:
        return True
    if any(part in {".locks", ".staging", "__pycache__", ".pytest_cache"} for part in relative.parts):
        return True
    if relative.suffix in {".tmp", ".pyc"} or relative.name.endswith(".lock"):
        return True
    return bool(
        relative.parts and relative.parts[0] == "harmony-project"
        and any(part in PROJECT_EXCLUDED_PARTS for part in relative.parts[1:])
    )


def closure_manifest_text(workspace: Path) -> str:
    names: list[str] = []
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace)
        if closure_excluded(relative):
            continue
        if path.is_symlink():
            raise ValueError(f"Symbolic link in Phase 4 closure: {path}")
        if path.is_file():
            names.append(relative.as_posix())
    return "".join(f"{sha256_file(workspace / name)}  {name}\n" for name in names)


def validate_source_ref(project: Path, reference: str) -> Path:
    if ":" not in reference:
        raise ValueError(f"Source reference must use relative/path:line: {reference}")
    relative, line_value = reference.rsplit(":", 1)
    try:
        line = int(line_value)
    except ValueError as exc:
        raise ValueError(f"Source reference line is invalid: {reference}") from exc
    path = safe_relative_path(project, relative, "Harmony source reference")
    if not path.is_file() or line < 1:
        raise ValueError(f"Invalid Harmony source reference: {reference}")
    line_count = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace"))
    if line > line_count:
        raise ValueError(f"Harmony source reference exceeds file length: {reference}")
    return path


def validate_assertions(
    path: Path, bindings: dict[str, str]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    value = load_json(path)
    if not isinstance(value, dict):
        raise ValueError("Assertion evidence must be an object")
    for field, expected in bindings.items():
        if str(value.get(field, "")) != expected:
            raise ValueError(f"Assertion evidence {field} differs")
    raw = value.get("assertions")
    if not isinstance(raw, list) or not raw or any(not isinstance(item, dict) for item in raw):
        raise ValueError("Assertion evidence lacks assertions")
    result: dict[str, dict[str, Any]] = {}
    for item in raw:
        assertion_id = str(item.get("assertion_id", ""))
        if not assertion_id or assertion_id in result:
            raise ValueError("Assertion evidence has an empty/duplicate Assertion-ID")
        if (
            not str(item.get("kind", "")) or item.get("expected") in (None, "")
            or item.get("actual") in (None, "") or item.get("status") != "PASS"
        ):
            raise ValueError(f"Assertion is empty or failing: {assertion_id}")
        subjects = item.get("subject_ids", [])
        if not isinstance(subjects, list) or any(not isinstance(value, str) or not value for value in subjects):
            raise ValueError(f"Assertion subject_ids are invalid: {assertion_id}")
        result[assertion_id] = item
    kinds = {str(item.get("kind", "")) for item in raw}
    if REQUIRED_ASSERTION_KINDS - kinds:
        raise ValueError("Assertion evidence lacks required state assertions")
    return value, result


def validate_command_records(
    records: Any,
    expected_sequence: list[str],
    environment: dict[str, Any],
    package: Path,
    label: str,
) -> None:
    contracts = frozen_category_contracts(environment)
    if not isinstance(records, list) or len(records) != len(expected_sequence):
        raise ValueError(f"{label} command count differs")
    categories = [str(item.get("category", "")) for item in records if isinstance(item, dict)]
    if categories != expected_sequence:
        raise ValueError(f"{label} command sequence differs")
    command_ids: set[str] = set()
    selector = environment.get("device_selector_tokens", [])
    serial = str(environment.get("emulator", {}).get("serial", ""))
    bundle = str(environment.get("base_application", {}).get("bundle_name", ""))
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(f"{label} has a malformed command")
        command_id = str(record.get("command_id", ""))
        category = str(record.get("category", ""))
        if not command_id or command_id in command_ids:
            raise ValueError(f"{label} has an empty/duplicate Command-ID")
        command_ids.add(command_id)
        plan_argv, contract = validate_frozen_command(category, record.get("plan_argv"), contracts)
        argv = record.get("argv")
        if not isinstance(argv, list) or len(argv) != len(plan_argv) or argv[0] != contract["resolved_executable"]:
            raise ValueError(f"{label} {command_id} executed argv is invalid")
        for planned_token, actual_token in zip(plan_argv, argv):
            if not isinstance(actual_token, str) or not actual_token:
                raise ValueError(f"{label} {command_id} contains an empty executed argument")
            if "{" not in planned_token and actual_token != planned_token:
                raise ValueError(f"{label} {command_id} changed a non-placeholder argument")
            if planned_token.startswith("{") and planned_token.endswith("}") and actual_token == planned_token:
                raise ValueError(f"{label} {command_id} left an output/artifact placeholder unresolved")
        if record.get("resolved_executable") != contract["resolved_executable"] or record.get(
            "executable_sha256"
        ) != contract["executable_sha256"] or sha256_file(Path(contract["resolved_executable"])) != contract[
            "executable_sha256"
        ]:
            raise ValueError(f"{label} {command_id} executable binding differs")
        for field in (
            "required_argv_tokens", "success_output_contains", "error_output_contains"
        ):
            if record.get(field) != contract[field]:
                raise ValueError(f"{label} {command_id} contract field differs: {field}")
        if not command_passed(record) or record.get("command_verdict") != "PASS":
            raise ValueError(f"{label} {command_id} is not a live PASS")
        stdout = safe_relative_path(package, str(record.get("stdout_path", "")), "stdout log")
        stderr = safe_relative_path(package, str(record.get("stderr_path", "")), "stderr log")
        if (
            not stdout.is_file() or not stderr.is_file()
            or sha256_file(stdout) != record.get("stdout_sha256")
            or sha256_file(stderr) != record.get("stderr_sha256")
        ):
            raise ValueError(f"{label} {command_id} log hash differs")
        out_text = stdout.read_text(encoding="utf-8", errors="replace")
        err_text = stderr.read_text(encoding="utf-8", errors="replace")
        passed, successes, failures = frozen_output_verdict(out_text, err_text, contract)
        if (
            not passed or record.get("success_output_matches") != successes
            or record.get("error_output_matches") != failures or failures
        ):
            raise ValueError(f"{label} {command_id} output verdict differs")
        if category in SERIAL_CATEGORIES:
            if serial not in argv or not selector_is_present(argv, selector):
                raise ValueError(f"{label} {command_id} lacks the exact emulator selector")
        if category in {
            "BUNDLE_CHECK", "SIGNING_CHECK", "CLEAN_INSTALL", "SEED_RESET",
            "PERMISSION_PROFILE", "LAUNCH", "NAVIGATE", "BUSINESS_ASSERT",
            "SCREENSHOT_CAPTURE", "UITEST_SNAPSHOT_CAPTURE",
        } and bundle not in argv:
            raise ValueError(f"{label} {command_id} lacks the frozen Bundle")


def validate_hbuild(
    directory: Path,
    build_id: str,
    environment: dict[str, Any],
    expected_executor: str,
    input_lock_sha256: str,
    source_snapshot_sha256: str,
) -> tuple[dict[str, Any], str]:
    verify_sealed_package(directory, build_id, "PASS")
    metadata = load_json(directory / "metadata.json")
    artifact_manifest = load_json(directory / "artifact-manifest.json")
    if (
        metadata.get("hbuild_id") != build_id or metadata.get("status") != "PASS"
        or metadata.get("executed_by") != expected_executor
        or metadata.get("input_lock_sha256") != input_lock_sha256
        or metadata.get("source_snapshot_sha256") != source_snapshot_sha256
    ):
        raise ValueError(f"HBUILD identity/executor/snapshot differs: {build_id}")
    artifacts = artifact_manifest.get("artifacts") if isinstance(artifact_manifest, dict) else None
    if not isinstance(artifacts, list) or len(artifacts) != 1 or metadata.get("artifact_count") != 1:
        raise ValueError(f"HBUILD must contain exactly one HAP: {build_id}")
    artifact = artifacts[0]
    if not isinstance(artifact, dict) or metadata.get("primary_artifact") != artifact:
        raise ValueError(f"HBUILD primary artifact differs: {build_id}")
    path = safe_relative_path(directory, str(artifact.get("sealed_relative_path", "")), "sealed HAP")
    if (
        not path.is_file() or not str(path).lower().endswith(".hap")
        or sha256_file(path) != artifact.get("sha256") or path.stat().st_size != artifact.get("size")
    ):
        raise ValueError(f"HBUILD HAP hash/size differs: {build_id}")
    validate_hap(path)
    snapshot = load_json(directory / "source-snapshot.json")
    if snapshot.get("snapshot_sha256") != source_snapshot_sha256:
        raise ValueError(f"HBUILD source-snapshot.json differs: {build_id}")
    validate_command_records(metadata.get("commands"), BUILD_SEQUENCE, environment, directory, build_id)
    clean = next(item for item in metadata["commands"] if item.get("category") == "CLEAN_BUILD")
    state = clean.get("artifact_state_after_clean_build")
    if not isinstance(state, dict) or state.get("sha256") != artifact.get("sha256") or state.get("size") != artifact.get("size"):
        raise ValueError(f"HBUILD CLEAN_BUILD did not bind the final HAP: {build_id}")
    if artifact.get("produced_by_command_id") != clean.get("command_id"):
        raise ValueError(f"HBUILD artifact producer differs: {build_id}")
    return metadata, str(artifact["sha256"])


def validate_hevd(
    directory: Path,
    evidence_id: str,
    index: dict[str, str],
    parity: dict[str, str],
    environment: dict[str, Any],
    build: dict[str, Any],
    expected_executor: str,
    input_lock_sha256: str,
    source_snapshot_sha256: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    verify_sealed_package(directory, evidence_id, "SEALED")
    metadata_path = directory / "metadata.json"
    metadata = load_json(metadata_path)
    for field in (
        "parity_id", "inventory_id", "feature_id", "page_id", "state_id", "h4env_id",
        "android_evidence_id",
    ):
        if str(metadata.get(field, "")) != str(index.get(field, "")) or str(index.get(field, "")) != str(parity.get(field, "")):
            raise ValueError(f"HEVD {field} differs: {evidence_id}")
    if str(metadata.get("hbuild_id", "")) != str(index.get("hbuild_id", "")):
        raise ValueError(f"HEVD hbuild_id differs: {evidence_id}")
    primary = build.get("primary_artifact", {})
    if (
        metadata.get("evidence_id") != evidence_id or metadata.get("status") != "SEALED"
        or metadata.get("captured_by") != expected_executor
        or metadata.get("input_lock_sha256") != input_lock_sha256
        or metadata.get("source_snapshot_sha256") != source_snapshot_sha256
        or metadata.get("build_artifact_sha256") != primary.get("sha256")
        or metadata.get("device_type") != "emulator"
        or metadata.get("device_id") != environment.get("device_id")
        or metadata.get("device_serial") != environment.get("emulator", {}).get("serial")
        or metadata.get("bundle_name") != environment.get("base_application", {}).get("bundle_name")
        or metadata.get("target_kind") != parity.get("target_kind")
        or metadata.get("target_id") != parity.get("target_id")
        or sha256_file(metadata_path) != index.get("metadata_sha256")
    ):
        raise ValueError(f"HEVD identity/executor/artifact differs: {evidence_id}")
    screenshot = directory / "screenshot.png"
    width, height = png_dimensions(screenshot)
    comparison = environment.get("comparison", {})
    if (width, height) != (comparison.get("screenshot_width"), comparison.get("screenshot_height")):
        raise ValueError(f"HEVD screenshot resolution differs: {evidence_id}")
    if sha256_file(screenshot) != index.get("screenshot_sha256"):
        raise ValueError(f"HEVD screenshot hash differs: {evidence_id}")
    screenshot_meta = metadata.get("screenshot", {})
    if screenshot_meta.get("sha256") != sha256_file(screenshot) or screenshot_meta.get("width") != width or screenshot_meta.get("height") != height:
        raise ValueError(f"HEVD screenshot metadata differs: {evidence_id}")
    workspace = next(
        (parent for parent in directory.parents if (parent / "stage-04-input-lock.json").is_file()),
        None,
    )
    if workspace is None:
        raise ValueError(f"HEVD has no canonical Phase 4 workspace: {evidence_id}")
    input_lock = load_json(workspace / "stage-04-input-lock.json")
    generation_lock = input_lock.get("ui_test_snapshot_generation")
    generation_manifest_path = workspace / "ui-test-snapshot-generation-manifest.json"
    if (
        not isinstance(generation_lock, dict)
        or generation_lock.get("sha256") != sha256_file(generation_manifest_path)
        or generation_lock.get("contract") != "ui-test-snapshot-generation-v1"
    ):
        raise ValueError(f"HEVD UiTest generation lock differs: {evidence_id}")
    generation_manifest = load_json(generation_manifest_path)
    probe_id = f"{index['page_id']}::{index['state_id']}"
    probes = [
        row for row in generation_manifest.get("probes", [])
        if isinstance(row, dict) and row.get("probe_id") == probe_id
    ]
    plans = [
        row for row in generation_manifest.get("page_plans", [])
        if isinstance(row, dict) and row.get("page_id") == index["page_id"]
    ]
    if len(probes) != 1 or len(plans) != 1:
        raise ValueError(f"HEVD UiTest probe/page plan is missing: {evidence_id}")
    page_plan = safe_relative_path(workspace, str(plans[0].get("relative_path", "")), "ArkTS page plan")
    if not page_plan.is_file() or sha256_file(page_plan) != plans[0].get("sha256"):
        raise ValueError(f"HEVD UiTest page plan hash differs: {evidence_id}")
    test_hap = directory / "uitest-test.hap"
    if not test_hap.is_file():
        raise ValueError(f"HEVD UiTest HAP is missing: {evidence_id}")
    validate_hap(test_hap)
    uitest_commands = [
        row for row in metadata.get("commands", [])
        if isinstance(row, dict) and row.get("category") == "UITEST_SNAPSHOT_CAPTURE"
    ]
    if len(uitest_commands) != 1:
        raise ValueError(f"HEVD UiTest command differs: {evidence_id}")
    command_sha256 = hashlib.sha256(json.dumps(
        uitest_commands[0].get("argv"), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    device_identity_sha256 = hashlib.sha256(json.dumps(
        {"device_id": environment["device_id"], "serial": environment["emulator"]["serial"]},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    page_contract = load_json(workspace / "page-contracts" / f"{index['page_id']}.json")
    validate_uitest_evidence(
        directory, probes[0], page_id=index["page_id"], state_id=index["state_id"],
        bundle_name=str(environment["base_application"]["bundle_name"]),
        carrier=str(page_contract["carrier_type"]), target_id=str(parity["target_id"]),
        generation_manifest_sha256=sha256_file(generation_manifest_path),
        page_plan_sha256=sha256_file(page_plan), test_hap_sha256=sha256_file(test_hap),
        final_hap_sha256=str(primary["sha256"]),
        device_identity_sha256=device_identity_sha256, command_sha256=command_sha256,
        required_event_ids={
            str(row["event_id"]) for row in page_contract.get("interaction_bindings", [])
            if isinstance(row, dict) and row.get("event_id")
        },
        required_transition_ids={
            str(row["transition_id"]) for row in page_contract.get("transitions", [])
            if isinstance(row, dict) and row.get("transition_id")
        },
    )
    bindings = {
        "parity_id": parity["parity_id"], "hbuild_id": index["hbuild_id"],
        "h4env_id": index["h4env_id"], "device_id": str(environment["device_id"]),
        "device_serial": str(environment["emulator"]["serial"]),
        "bundle_name": str(environment["base_application"]["bundle_name"]),
    }
    _, assertions = validate_assertions(directory / "assertions.json", bindings)
    if metadata.get("assertions", {}).get("sha256") != sha256_file(directory / "assertions.json"):
        raise ValueError(f"HEVD assertions hash differs: {evidence_id}")
    uitest_meta = metadata.get("ui_test_snapshot", {})
    if (
        not isinstance(uitest_meta, dict)
        or uitest_meta.get("sha256") != sha256_file(directory / "ui-test-snapshot.json")
        or uitest_meta.get("metadata_sha256") != sha256_file(directory / "ui-test-snapshot-metadata.json")
        or uitest_meta.get("operation_trace_sha256") != sha256_file(directory / "ui-test-snapshot-operation-trace.json")
        or uitest_meta.get("screenshot_sha256") != sha256_file(directory / "ui-test-snapshot.png")
        or uitest_meta.get("test_hap_sha256") != sha256_file(test_hap)
        or uitest_meta.get("final_hap_sha256") != primary.get("sha256")
    ):
        raise ValueError(f"HEVD UiTest snapshot hashes differ: {evidence_id}")
    validate_command_records(metadata.get("commands"), EVIDENCE_SEQUENCE, environment, directory, evidence_id)
    result_categories = {
        "BUSINESS_ASSERT": ("assertions.json", directory / "assertions.json"),
        "SCREENSHOT_CAPTURE": ("screenshot.png", screenshot),
        "UITEST_SNAPSHOT_CAPTURE": ("ui-test-snapshot.json", directory / "ui-test-snapshot.json"),
    }
    for command in metadata["commands"]:
        expected = result_categories.get(command.get("category"))
        if expected and (
            command.get("result_path") != expected[0]
            or command.get("result_sha256") != sha256_file(expected[1])
        ):
            raise ValueError(f"HEVD command result hash differs: {evidence_id}")
    return metadata, assertions


def parse_object(value: str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    return parsed


def geometry_matches(left: dict[str, Any], right: dict[str, Any], tolerance: int) -> bool:
    return all(
        isinstance(left.get(field), (int, float))
        and isinstance(right.get(field), (int, float))
        and abs(float(left[field]) - float(right[field])) <= tolerance
        for field in ("x", "y", "width", "height")
    )


def scan_project(project: Path, forbidden_tokens: Iterable[str]) -> list[str]:
    errors: list[str] = []
    for path in project.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        relative = path.relative_to(project)
        if any(part in PROJECT_EXCLUDED_PARTS for part in relative.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in forbidden_tokens:
            if token and token in text:
                errors.append(f"Production source contains forbidden token {token!r}: {relative}")
    return errors
