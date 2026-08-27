#!/usr/bin/env python3
"""Run and seal command-line verification for one HarmonyOS scaffold snapshot."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any

from _common import (
    assert_no_secrets,
    atomic_json,
    atomic_text,
    build_snapshot_manifest,
    command_output_verdict,
    join_multi,
    load_json,
    manifest_text,
    parse_resolution,
    png_dimensions,
    read_csv,
    run_command,
    safe_relative_path,
    sha256_file,
    utc_now,
    validate_hap,
    validate_id,
    write_csv,
)


CATEGORY_ORDER = (
    "TOOLCHAIN", "DEVICE", "BUNDLE_CHECK", "SIGNING_CHECK", "CLEAN_BUILD",
    "INSTALL", "LAUNCH", "ROUTE_SMOKE", "SCREENSHOT_CAPTURE",
)
REQUIRED_CATEGORIES = set(CATEGORY_ORDER)
CATEGORY_RANK = {category: index for index, category in enumerate(CATEGORY_ORDER)}
DEVICE_CATEGORIES = {
    "DEVICE", "BUNDLE_CHECK", "INSTALL", "LAUNCH", "ROUTE_SMOKE", "SCREENSHOT_CAPTURE",
}
PER_DEVICE_CATEGORIES = {"DEVICE", "BUNDLE_CHECK", "INSTALL", "LAUNCH", "ROUTE_SMOKE"}
SINGLETON_CATEGORIES = {"TOOLCHAIN", "SIGNING_CHECK", "CLEAN_BUILD"}
SCREENSHOT_TARGET_KINDS = {"ROUTE_PAGE", "VISUAL_SURFACE"}
REQUIRED_OWNERS = {
    "architecture_lead_id", "toolchain_agent_id", "navigation_agent_id",
    "public_ui_agent_id", "capability_contract_agent_id", "architecture_acceptance_agent_id",
}
SCREENSHOT_INDEX_FIELDS = [
    "screenshot_id", "verification_id", "henv_id", "device_id", "target_kind", "target_id",
    "feature_ids", "page_id", "page_shell_id", "smoke_command_id", "capture_command_id",
    "width", "height", "relative_path", "png_sha256", "captured_by", "captured_at", "status",
]



def resolved_executable(value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"Frozen executable must be an absolute resolved path: {value}")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Frozen executable is unavailable: {value}: {exc}") from exc
    if str(resolved) != value or not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ValueError(f"Frozen executable path is not canonical/executable: {value}")
    return resolved


def require_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{label} must be a non-empty array of non-empty strings")
    return list(value)



def file_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    stat_result = path.stat()
    return {
        "sha256": sha256_file(path),
        "size": stat_result.st_size,
        "mtime_ns": stat_result.st_mtime_ns,
    }


def make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            raise ValueError(f"Symbolic link is prohibited in HVER package: {path}")
        if path.is_file():
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        elif path.is_dir():
            path.chmod(
                stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP
                | stat.S_IROTH | stat.S_IXOTH
            )
    root.chmod(
        stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    )


def load_generated_result(
    path: Path, command: dict[str, Any], expected_bundle_name: str
) -> dict[str, Any]:
    value = load_json(path)
    if not isinstance(value, dict) or "results" in value or "result" in value:
        raise ValueError(f"ROUTE_SMOKE must generate one direct result object: {path}")
    target_field = "route_id" if command["target_kind"] == "ROUTE_PAGE" else "surface_shell_id"
    expected = {
        target_field: command["target_id"],
        "page_id": command["page_id"],
        "page_shell_id": command["page_shell_id"],
        "device_id": command["device_id"],
        "device_serial": command["serial"],
        "bundle_name": expected_bundle_name,
        "status": "PASS",
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ValueError(
                f"{command['command_id']}: generated smoke result {key}={value.get(key)!r}; "
                f"expected {expected_value!r}"
            )
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    plan_path = Path(args.plan).expanduser().resolve()
    project = workspace / "harmony-project"
    if (workspace / "CLOSED").exists():
        parser.error("Phase 3 is CLOSED; verification writes are prohibited")
    try:
        phase_manifest = load_json(workspace / "phase-manifest.json")
        plan = load_json(plan_path)
    except ValueError as exc:
        parser.error(str(exc))
    if phase_manifest.get("phase") != 3 or not project.is_dir():
        parser.error("Not an initialized Phase 3 workspace")
    if not isinstance(plan, dict):
        parser.error("Verification plan must be a JSON object")
    ownership = phase_manifest.get("ownership")
    if not isinstance(ownership, dict) or not REQUIRED_OWNERS.issubset(ownership):
        parser.error("phase-manifest.json lacks frozen Phase 3 ownership")
    if any(not str(ownership.get(key, "")).strip() for key in REQUIRED_OWNERS):
        parser.error("Frozen Phase 3 ownership contains an empty agent ID")
    try:
        assert_no_secrets(plan)
        verification_id = validate_id(str(plan.get("verification_id", "")), "HVER-ID")
        henv_id = validate_id(str(plan.get("henv_id", "")), "HENV-ID")
    except ValueError as exc:
        parser.error(str(exc))
    executed_by = str(plan.get("executed_by", "")).strip()
    if executed_by != str(ownership["toolchain_agent_id"]):
        parser.error("Verification executed_by must equal the frozen toolchain_agent_id")

    final_dir = workspace / "verification" / verification_id
    if final_dir.exists():
        parser.error(f"HVER-ID already exists; overwrite is prohibited: {verification_id}")

    environment_path = workspace / "environments" / henv_id / "harmony-environment.json"
    try:
        environment = load_json(environment_path)
        henv_registry = read_csv(workspace / "environments" / "henv-registry.csv")
    except ValueError as exc:
        parser.error(str(exc))
    henv_row = next((row for row in henv_registry if row.get("henv_id") == henv_id), None)
    if not henv_row or henv_row.get("status") != "FROZEN":
        parser.error(f"HENV-ID is not frozen: {henv_id}")
    if sha256_file(environment_path) != henv_row.get("environment_sha256"):
        parser.error(f"Frozen HENV has changed: {henv_id}")
    if environment.get("created_by") != ownership["architecture_lead_id"] or environment.get(
        "frozen_by"
    ) != ownership["architecture_lead_id"]:
        parser.error("HENV creator/freezer must equal the frozen architecture_lead_id")

    bundle_name = str(environment.get("application", {}).get("bundle_name", ""))
    if not bundle_name:
        parser.error("Frozen HENV has no application.bundle_name")

    toolchain = environment.get("toolchain", {})
    category_contracts = toolchain.get("category_contracts") if isinstance(toolchain, dict) else None
    if not isinstance(category_contracts, dict) or set(category_contracts) != REQUIRED_CATEGORIES:
        parser.error("HENV toolchain.category_contracts must cover exactly every required category")
    normalized_contracts: dict[str, dict[str, Any]] = {}
    try:
        for category in CATEGORY_ORDER:
            contract = category_contracts[category]
            if not isinstance(contract, dict):
                raise ValueError(f"Category contract must be an object: {category}")
            executable_text = str(contract.get("resolved_executable", ""))
            executable = resolved_executable(executable_text)
            frozen_hash = str(contract.get("executable_sha256", "")).lower()
            if len(frozen_hash) != 64 or sha256_file(executable) != frozen_hash:
                raise ValueError(f"Frozen executable hash differs for {category}: {executable}")
            normalized_contracts[category] = {
                "resolved_executable": str(executable),
                "executable_sha256": frozen_hash,
                "required_argv_tokens": require_string_list(
                    contract.get("required_argv_tokens"), f"{category}.required_argv_tokens"
                ),
                "success_output_contains": require_string_list(
                    contract.get("success_output_contains"), f"{category}.success_output_contains"
                ),
                "error_output_contains": require_string_list(
                    contract.get("error_output_contains"), f"{category}.error_output_contains"
                ),
            }
    except ValueError as exc:
        parser.error(str(exc))

    device_by_id = {
        str(device.get("device_id")): device
        for device in environment.get("devices", [])
        if isinstance(device, dict) and device.get("device_id")
    }
    required_devices = {
        device_id for device_id, device in device_by_id.items() if device.get("required") is True
    }
    screenshot_devices = {
        device_id for device_id, device in device_by_id.items() if device.get("screenshot_required") is True
    }
    if not required_devices or not screenshot_devices:
        parser.error("Frozen HENV requires devices and screenshot-required emulators")
    for device_id in screenshot_devices:
        device = device_by_id[device_id]
        if device_id not in required_devices or str(device.get("device_type", "")).lower() != "emulator":
            parser.error(f"Screenshot-required HDEVICE must be a required emulator: {device_id}")

    artifact_values = plan.get("artifact_paths")
    if not isinstance(artifact_values, list) or not artifact_values or any(
        not isinstance(item, str) or not item for item in artifact_values
    ):
        parser.error("Verification plan requires at least one relative HAP artifact path")
    try:
        artifact_paths = [
            safe_relative_path(project, item, "build artifact", must_exist=False)
            for item in artifact_values
        ]
    except ValueError as exc:
        parser.error(str(exc))
    if len(set(artifact_paths)) != len(artifact_paths):
        parser.error("Verification plan contains duplicate artifact paths")
    if any(path.suffix.lower() != ".hap" for path in artifact_paths):
        parser.error("Every verification artifact must have a .hap suffix")
    artifact_initial = {str(path): file_state(path) for path in artifact_paths}

    try:
        routes = read_csv(workspace / "route-registry.csv")
        surfaces = read_csv(workspace / "surface-registry.csv")
        architecture = read_csv(workspace / "architecture-map.csv")
    except ValueError as exc:
        parser.error(str(exc))
    route_by_id = {row.get("route_id", ""): row for row in routes if row.get("route_id")}
    surface_by_id = {
        row.get("surface_shell_id", ""): row for row in surfaces if row.get("surface_shell_id")
    }
    expected_targets = {
        (row.get("mapping_type", ""), row.get("route_id") or row.get("surface_shell_id", ""))
        for row in architecture if row.get("mapping_type") in SCREENSHOT_TARGET_KINDS
    }
    if any(not target_id for _, target_id in expected_targets):
        parser.error("Architecture mapping contains an empty route/surface target")

    commands = plan.get("commands")
    if not isinstance(commands, list) or not commands:
        parser.error("Verification plan requires commands")
    normalized_commands: list[dict[str, Any]] = []
    command_ids: set[str] = set()
    screenshot_ids: set[str] = set()
    output_paths: set[Path] = set()
    category_counts = {category: 0 for category in REQUIRED_CATEGORIES}
    category_devices: dict[str, set[str]] = {category: set() for category in PER_DEVICE_CATEGORIES}
    smoke_keys: set[tuple[str, str, str]] = set()
    screenshot_keys: set[tuple[str, str, str]] = set()
    last_rank = -1
    try:
        for index, command in enumerate(commands):
            if not isinstance(command, dict):
                raise ValueError(f"commands[{index}] must be an object")
            command_id = validate_id(str(command.get("command_id", "")), "Command-ID")
            if command_id in command_ids:
                raise ValueError(f"Duplicate Command-ID: {command_id}")
            command_ids.add(command_id)
            category = str(command.get("category", ""))
            if category not in REQUIRED_CATEGORIES:
                raise ValueError(f"Unknown verification category: {category}")
            rank = CATEGORY_RANK[category]
            if rank < last_rank:
                raise ValueError(
                    f"Command category order is invalid at {command_id}; required order is {CATEGORY_ORDER}"
                )
            last_rank = rank
            category_counts[category] += 1
            contract = normalized_contracts[category]
            argv = command.get("argv")
            if not isinstance(argv, list) or not argv or any(
                not isinstance(item, str) or not item for item in argv
            ):
                raise ValueError(f"{command_id}: argv must be a non-empty string array")
            if argv[0] != contract["resolved_executable"]:
                raise ValueError(f"{command_id}: executable does not match frozen {category} contract")
            missing_tokens = [token for token in contract["required_argv_tokens"] if token not in argv]
            if missing_tokens:
                raise ValueError(f"{command_id}: argv lacks frozen required tokens {missing_tokens}")
            assert_no_secrets(argv, f"commands[{index}].argv")
            device_id = str(command.get("device_id", ""))
            serial = ""
            if category in DEVICE_CATEGORIES:
                validate_id(device_id, "HDEVICE-ID")
                allowed_devices = screenshot_devices if category == "SCREENSHOT_CAPTURE" else required_devices
                if device_id not in allowed_devices:
                    raise ValueError(f"{command_id}: unknown/non-required HDEVICE-ID {device_id}")
                serial = str(device_by_id[device_id].get("serial", ""))
                if not serial or serial not in argv:
                    raise ValueError(f"{command_id}: argv must contain the exact frozen device serial")
                if category in PER_DEVICE_CATEGORIES:
                    category_devices[category].add(device_id)
            elif device_id:
                raise ValueError(f"{command_id}: {category} must not declare device_id")
            if category in {"BUNDLE_CHECK", "SIGNING_CHECK", "LAUNCH", "ROUTE_SMOKE"}:
                if bundle_name not in argv:
                    raise ValueError(
                        f"{command_id}: {category} argv must contain the exact frozen bundle name"
                    )
            cwd = safe_relative_path(project, str(command.get("cwd", ".")), "command cwd")
            if not cwd.is_dir():
                raise ValueError(f"{command_id}: command cwd is not a directory")
            normalized: dict[str, Any] = {
                "command_id": command_id,
                "category": category,
                "device_id": device_id,
                "serial": serial,
                "cwd": cwd,
                "argv": list(argv),
                "contract": contract,
            }
            if category == "INSTALL" and not any(
                relative in argv or str(path) in argv
                for relative, path in zip(artifact_values, artifact_paths)
            ):
                raise ValueError(f"{command_id}: INSTALL argv must bind a declared HAP artifact path")
            if category == "ROUTE_SMOKE":
                target_kind = str(command.get("target_kind", ""))
                if target_kind not in SCREENSHOT_TARGET_KINDS:
                    raise ValueError(f"{command_id}: unknown target_kind {target_kind}")
                target_id = validate_id(str(command.get("target_id", "")), "route/surface target ID")
                page_id = validate_id(str(command.get("page_id", "")), "Page-ID")
                page_shell_id = validate_id(str(command.get("page_shell_id", "")), "Page-Shell-ID")
                if (target_kind, target_id) not in expected_targets:
                    raise ValueError(f"{command_id}: target is not present in architecture-map.csv")
                registry_row = (
                    route_by_id.get(target_id) if target_kind == "ROUTE_PAGE"
                    else surface_by_id.get(target_id)
                )
                if not registry_row or registry_row.get("page_id") != page_id or registry_row.get(
                    "page_shell_id"
                ) != page_shell_id:
                    raise ValueError(f"{command_id}: target identity differs from its registry")
                result_relative = str(command.get("result_output_path", ""))
                result_path = safe_relative_path(
                    project, result_relative, f"smoke result output for {command_id}", must_exist=False
                )
                if result_path.exists() or result_path in output_paths:
                    raise ValueError(f"{command_id}: smoke output must be new and unique: {result_path}")
                if result_relative not in argv:
                    raise ValueError(f"{command_id}: argv must contain result_output_path exactly")
                output_paths.add(result_path)
                key = (target_kind, target_id, device_id)
                if key in smoke_keys:
                    raise ValueError(f"Duplicate route/surface smoke target: {key}")
                smoke_keys.add(key)
                normalized.update(
                    {
                        "target_kind": target_kind,
                        "target_id": target_id,
                        "page_id": page_id,
                        "page_shell_id": page_shell_id,
                        "result_output_relative": result_relative,
                        "result_output_path": result_path,
                    }
                )
            if category == "SCREENSHOT_CAPTURE":
                screenshot_id = validate_id(str(command.get("screenshot_id", "")), "HSCREEN-ID")
                if screenshot_id in screenshot_ids:
                    raise ValueError(f"Duplicate HSCREEN-ID: {screenshot_id}")
                screenshot_ids.add(screenshot_id)
                target_kind = str(command.get("target_kind", ""))
                if target_kind not in SCREENSHOT_TARGET_KINDS:
                    raise ValueError(f"{command_id}: unknown screenshot target_kind {target_kind}")
                target_id = validate_id(str(command.get("target_id", "")), "screenshot target ID")
                page_id = validate_id(str(command.get("page_id", "")), "Page-ID")
                page_shell_id = validate_id(str(command.get("page_shell_id", "")), "Page-Shell-ID")
                feature_ids = command.get("feature_ids")
                if not isinstance(feature_ids, list) or not feature_ids:
                    raise ValueError(f"{screenshot_id}: feature_ids must be a non-empty array")
                for feature_id in feature_ids:
                    validate_id(str(feature_id), "Feature-ID")
                smoke_command_id = validate_id(
                    str(command.get("smoke_command_id", "")), "ROUTE_SMOKE Command-ID"
                )
                smoke_command = next(
                    (item for item in normalized_commands if item["command_id"] == smoke_command_id), None
                )
                if not smoke_command or smoke_command["category"] != "ROUTE_SMOKE":
                    raise ValueError(f"{screenshot_id}: smoke_command_id must reference an earlier ROUTE_SMOKE")
                for field, value in (
                    ("device_id", device_id), ("target_kind", target_kind), ("target_id", target_id),
                    ("page_id", page_id), ("page_shell_id", page_shell_id),
                ):
                    if smoke_command.get(field) != value:
                        raise ValueError(f"{screenshot_id}: {field} differs from its ROUTE_SMOKE command")
                output_relative = str(command.get("output_path", ""))
                output_path = safe_relative_path(
                    project, output_relative, f"screenshot output for {screenshot_id}", must_exist=False
                )
                if output_path.exists() or output_path in output_paths:
                    raise ValueError(f"{screenshot_id}: screenshot output must be new and unique")
                if output_relative not in argv:
                    raise ValueError(f"{screenshot_id}: argv must contain output_path exactly")
                output_paths.add(output_path)
                key = (target_kind, target_id, device_id)
                if key in screenshot_keys:
                    raise ValueError(f"Duplicate screenshot target/device proof: {key}")
                screenshot_keys.add(key)
                normalized.update(
                    {
                        "screenshot_id": screenshot_id,
                        "target_kind": target_kind,
                        "target_id": target_id,
                        "feature_ids": [str(item) for item in feature_ids],
                        "page_id": page_id,
                        "page_shell_id": page_shell_id,
                        "smoke_command_id": smoke_command_id,
                        "output_path": output_path,
                        "output_relative": output_relative,
                    }
                )
            normalized_commands.append(normalized)
        missing = sorted(category for category, count in category_counts.items() if not count)
        if missing:
            raise ValueError(f"Verification plan lacks required categories: {missing}")
        for category in SINGLETON_CATEGORIES:
            if category_counts[category] != 1:
                raise ValueError(f"{category} must occur exactly once")
        for category in PER_DEVICE_CATEGORIES:
            if category_devices[category] != required_devices:
                raise ValueError(
                    f"{category} device coverage differs; expected={sorted(required_devices)}, "
                    f"actual={sorted(category_devices[category])}"
                )
        expected_smokes = {
            (target_kind, target_id, device_id)
            for target_kind, target_id in expected_targets for device_id in required_devices
        }
        if smoke_keys != expected_smokes:
            raise ValueError(
                f"ROUTE_SMOKE target coverage differs; missing={sorted(expected_smokes - smoke_keys)}, "
                f"extra={sorted(smoke_keys - expected_smokes)}"
            )
        expected_screenshots = {
            (target_kind, target_id, device_id)
            for target_kind, target_id in expected_targets for device_id in screenshot_devices
        }
        if screenshot_keys != expected_screenshots:
            raise ValueError(
                f"SCREENSHOT_CAPTURE target coverage differs; "
                f"missing={sorted(expected_screenshots - screenshot_keys)}, "
                f"extra={sorted(screenshot_keys - expected_screenshots)}"
            )
    except ValueError as exc:
        parser.error(str(exc))

    try:
        initial_snapshot = build_snapshot_manifest(workspace, henv_id)
    except ValueError as exc:
        parser.error(str(exc))

    verification_errors: list[str] = []
    command_records: list[dict[str, Any]] = []
    route_result_records: list[dict[str, Any]] = []
    surface_result_records: list[dict[str, Any]] = []
    artifact_records: list[dict[str, Any]] = []
    screenshot_records: list[dict[str, Any]] = []
    artifact_at_build: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(
        prefix=f".{verification_id}-", dir=workspace / "verification"
    ) as temp_name:
        temp_dir = Path(temp_name)
        logs = temp_dir / "logs"
        logs.mkdir()
        (temp_dir / "screenshots").mkdir()
        (temp_dir / "artifacts").mkdir()
        atomic_json(temp_dir / "scaffold-snapshot-manifest.json", initial_snapshot)

        for command in normalized_commands:
            contract = command["contract"]
            if sha256_file(Path(contract["resolved_executable"])) != contract["executable_sha256"]:
                verification_errors.append(
                    f"{command['command_id']}: frozen executable changed immediately before execution"
                )
                continue
            generated_path = command.get("result_output_path") or command.get("output_path")
            if generated_path is not None and generated_path.exists():
                verification_errors.append(
                    f"{command['command_id']}: declared output existed immediately before execution"
                )
                continue
            raw = run_command(command["argv"], command["cwd"], args.timeout)
            stdout = raw.pop("stdout")
            stderr = raw.pop("stderr")
            stdout_name = f"logs/{command['command_id']}.stdout.log"
            stderr_name = f"logs/{command['command_id']}.stderr.log"
            atomic_text(temp_dir / stdout_name, stdout)
            atomic_text(temp_dir / stderr_name, stderr)
            success_hits, error_hits = command_output_verdict(
                stdout, stderr, contract["success_output_contains"], contract["error_output_contains"]
            )
            output_ok = len(success_hits) == len(contract["success_output_contains"]) and not error_hits
            record = {
                **raw,
                "command_id": command["command_id"],
                "category": command["category"],
                "device_id": command["device_id"],
                "device_serial": command["serial"],
                "resolved_executable": contract["resolved_executable"],
                "executable_sha256": contract["executable_sha256"],
                "required_argv_tokens": contract["required_argv_tokens"],
                "success_output_contains": contract["success_output_contains"],
                "error_output_contains": contract["error_output_contains"],
                "success_output_matches": success_hits,
                "error_output_matches": error_hits,
                "stdout_path": stdout_name,
                "stdout_sha256": sha256_file(temp_dir / stdout_name),
                "stderr_path": stderr_name,
                "stderr_sha256": sha256_file(temp_dir / stderr_name),
                "command_verdict": (
                    "PASS" if raw.get("exit_code") == 0 and output_ok else "FAIL"
                ),
            }
            for key in (
                "target_kind", "target_id", "page_id", "page_shell_id", "result_output_relative",
                "screenshot_id", "smoke_command_id", "output_relative",
            ):
                if key in command:
                    record[key] = command[key]
            command_records.append(record)
            if record["command_verdict"] != "PASS":
                verification_errors.append(
                    f"{command['command_id']} ({command['category']}) failed: "
                    f"exit={raw.get('exit_code')}, success_marker={bool(success_hits)}, "
                    f"error_markers={error_hits}"
                )
                continue

            if command["category"] == "CLEAN_BUILD":
                for artifact in artifact_paths:
                    try:
                        current = file_state(artifact)
                        if current is None:
                            raise ValueError(f"CLEAN_BUILD did not create declared HAP: {artifact}")
                        previous = artifact_initial[str(artifact)]
                        if previous is not None and current == previous:
                            raise ValueError(f"CLEAN_BUILD did not create or update declared HAP: {artifact}")
                        validate_hap(artifact)
                        artifact_at_build[str(artifact)] = current
                    except ValueError as exc:
                        verification_errors.append(str(exc))
                record["artifact_states_after_clean_build"] = artifact_at_build

            if command["category"] == "ROUTE_SMOKE":
                try:
                    generated = load_generated_result(
                        command["result_output_path"], command, bundle_name
                    )
                    bound = dict(generated)
                    bound.update(
                        {
                            "verification_id": verification_id,
                            "henv_id": henv_id,
                            "target_kind": command["target_kind"],
                            "target_id": command["target_id"],
                            "command_id": command["command_id"],
                            "device_serial": command["serial"],
                            "bundle_name": environment.get("application", {}).get("bundle_name"),
                            "result_output_path": command["result_output_relative"],
                            "result_output_sha256": sha256_file(command["result_output_path"]),
                            "stdout_sha256": record["stdout_sha256"],
                            "stderr_sha256": record["stderr_sha256"],
                        }
                    )
                    if command["target_kind"] == "ROUTE_PAGE":
                        route_result_records.append(bound)
                    else:
                        surface_result_records.append(bound)
                except (ValueError, OSError) as exc:
                    verification_errors.append(str(exc))

            if command["category"] == "SCREENSHOT_CAPTURE":
                screenshot_id = command["screenshot_id"]
                try:
                    width, height = png_dimensions(command["output_path"])
                    expected_width, expected_height = parse_resolution(
                        str(device_by_id[command["device_id"]].get("resolution", ""))
                    )
                    if (width, height) not in {
                        (expected_width, expected_height), (expected_height, expected_width),
                    }:
                        raise ValueError(
                            f"{screenshot_id}: PNG dimensions {width}x{height} do not match frozen "
                            f"emulator resolution {expected_width}x{expected_height}"
                        )
                    screenshot_dir = temp_dir / "screenshots" / screenshot_id
                    screenshot_dir.mkdir()
                    screenshot_png = screenshot_dir / "screenshot.png"
                    shutil.copyfile(command["output_path"], screenshot_png)
                    screenshot_metadata = {
                        "screenshot_id": screenshot_id,
                        "verification_id": verification_id,
                        "henv_id": henv_id,
                        "device_id": command["device_id"],
                        "device_serial": command["serial"],
                        "bundle_name": environment.get("application", {}).get("bundle_name"),
                        "target_kind": command["target_kind"],
                        "target_id": command["target_id"],
                        "feature_ids": command["feature_ids"],
                        "page_id": command["page_id"],
                        "page_shell_id": command["page_shell_id"],
                        "smoke_command_id": command["smoke_command_id"],
                        "capture_command_id": command["command_id"],
                        "capture_executable": contract["resolved_executable"],
                        "capture_executable_sha256": contract["executable_sha256"],
                        "capture_stdout_sha256": record["stdout_sha256"],
                        "capture_stderr_sha256": record["stderr_sha256"],
                        "captured_by": executed_by,
                        "captured_at": record["finished_at"],
                        "width": width,
                        "height": height,
                        "png_sha256": sha256_file(screenshot_png),
                    }
                    atomic_json(screenshot_dir / "metadata.json", screenshot_metadata)
                    atomic_text(
                        screenshot_dir / "manifest.sha256",
                        manifest_text(screenshot_dir, ["metadata.json", "screenshot.png"]),
                    )
                    atomic_text(screenshot_dir / "COMMITTED", f"{screenshot_id} SEALED {utc_now()}\n")
                    screenshot_records.append(
                        {
                            "screenshot_id": screenshot_id,
                            "verification_id": verification_id,
                            "henv_id": henv_id,
                            "device_id": command["device_id"],
                            "target_kind": command["target_kind"],
                            "target_id": command["target_id"],
                            "feature_ids": join_multi(command["feature_ids"]),
                            "page_id": command["page_id"],
                            "page_shell_id": command["page_shell_id"],
                            "smoke_command_id": command["smoke_command_id"],
                            "capture_command_id": command["command_id"],
                            "width": width,
                            "height": height,
                            "relative_path": f"screenshots/{screenshot_id}",
                            "png_sha256": screenshot_metadata["png_sha256"],
                            "captured_by": executed_by,
                            "captured_at": record["finished_at"],
                            "status": "SEALED",
                        }
                    )
                except (ValueError, OSError) as exc:
                    verification_errors.append(str(exc))

        try:
            final_snapshot = build_snapshot_manifest(workspace, henv_id)
            if final_snapshot["snapshot_sha256"] != initial_snapshot["snapshot_sha256"]:
                verification_errors.append("Verification commands changed controlled source or registry files")
        except ValueError as exc:
            verification_errors.append(str(exc))

        for number, (relative, artifact) in enumerate(
            zip(artifact_values, artifact_paths), start=1
        ):
            try:
                final_state = file_state(artifact)
                build_state = artifact_at_build.get(str(artifact))
                if final_state is None or build_state is None or final_state != build_state:
                    raise ValueError(f"Declared HAP was absent or changed after CLEAN_BUILD: {artifact}")
                validate_hap(artifact)
                sealed_relative = f"artifacts/ART-{number:03d}-{artifact.name}"
                sealed_path = temp_dir / sealed_relative
                shutil.copyfile(artifact, sealed_path)
                validate_hap(sealed_path)
                artifact_records.append(
                    {
                        "path": relative,
                        "sha256": final_state["sha256"],
                        "size": final_state["size"],
                        "sealed_path": sealed_relative,
                        "sealed_sha256": sha256_file(sealed_path),
                        "produced_by_command_id": next(
                            record["command_id"] for record in command_records
                            if record["category"] == "CLEAN_BUILD"
                        ),
                    }
                )
            except (ValueError, StopIteration) as exc:
                verification_errors.append(str(exc))

        atomic_json(
            temp_dir / "route-results.json",
            {"verification_id": verification_id, "henv_id": henv_id, "results": route_result_records},
        )
        atomic_json(
            temp_dir / "surface-results.json",
            {"verification_id": verification_id, "henv_id": henv_id, "results": surface_result_records},
        )
        atomic_json(temp_dir / "artifact-manifest.json", {"artifacts": artifact_records})
        write_csv(temp_dir / "screenshot-index.csv", SCREENSHOT_INDEX_FIELDS, screenshot_records)

        preflight_categories = {"TOOLCHAIN", "DEVICE", "BUNDLE_CHECK", "SIGNING_CHECK"}
        preflight_commands = [
            {
                "command_id": record["command_id"],
                "category": record["category"],
                "device_id": record["device_id"],
                "command_verdict": record["command_verdict"],
                "executable_sha256": record["executable_sha256"],
                "stdout_sha256": record["stdout_sha256"],
                "stderr_sha256": record["stderr_sha256"],
            }
            for record in command_records if record["category"] in preflight_categories
        ]
        preflight_status = "PASS" if preflight_commands and all(
            item["command_verdict"] == "PASS" for item in preflight_commands
        ) else "FAIL"
        atomic_json(
            temp_dir / "deveco-preflight-report.json",
            {
                "henv_id": henv_id,
                "verdict": preflight_status,
                "verification_id": verification_id,
                "environment_sha256": sha256_file(environment_path),
                "bundle_conflict_check_scope": environment.get("bundle_conflict_check_scope"),
                "required_devices": sorted(required_devices),
                "commands": preflight_commands,
                "created_at": utc_now(),
            },
        )
        if preflight_status != "PASS":
            verification_errors.append("Frozen HarmonyOS CLI preflight command set is not PASS")

        status = "PASS" if not verification_errors else "FAIL"
        metadata = {
            "verification_id": verification_id,
            "henv_id": henv_id,
            "run_id": phase_manifest.get("run_id"),
            "work_order_id": phase_manifest.get("work_order_id"),
            "work_order_sha256": phase_manifest.get("work_order_sha256"),
            "executed_by": executed_by,
            "created_at": utc_now(),
            "status": status,
            "input_lock_sha256": sha256_file(workspace / "stage-03-input-lock.json"),
            "environment_sha256": sha256_file(environment_path),
            "source_snapshot_sha256": initial_snapshot["snapshot_sha256"],
            "category_order": list(CATEGORY_ORDER),
            "commands": command_records,
            "required_devices": sorted(required_devices),
            "screenshot_required_devices": sorted(screenshot_devices),
            "screenshot_ids": sorted(row["screenshot_id"] for row in screenshot_records),
            "route_result_count": len(route_result_records),
            "surface_result_count": len(surface_result_records),
            "errors": verification_errors,
        }
        atomic_json(temp_dir / "metadata.json", metadata)

        relative_files = [
            path.relative_to(temp_dir).as_posix()
            for path in temp_dir.rglob("*")
            if path.is_file()
            and path.relative_to(temp_dir).as_posix() not in {"manifest.sha256", "COMMITTED"}
        ]
        atomic_text(temp_dir / "manifest.sha256", manifest_text(temp_dir, relative_files))
        atomic_text(temp_dir / "COMMITTED", f"{verification_id} {status} {utc_now()}\n")
        temp_dir.rename(final_dir)
        make_tree_read_only(final_dir)

    build_report = {
        "status": status,
        "verification_id": verification_id,
        "henv_id": henv_id,
        "source_snapshot_sha256": initial_snapshot["snapshot_sha256"],
        "artifact_count": len(artifact_records),
        "artifacts": artifact_records,
        "clean_build_passed": any(
            record["category"] == "CLEAN_BUILD" and record["command_verdict"] == "PASS"
            for record in command_records
        ),
        "install_passed_devices": sorted(
            record["device_id"] for record in command_records
            if record["category"] == "INSTALL" and record["command_verdict"] == "PASS"
        ),
        "launch_passed_devices": sorted(
            record["device_id"] for record in command_records
            if record["category"] == "LAUNCH" and record["command_verdict"] == "PASS"
        ),
        "screenshot_required_devices": sorted(screenshot_devices),
        "screenshot_count": len(screenshot_records),
        "updated_at": utc_now(),
        "errors": verification_errors,
    }
    atomic_json(workspace / "build-report.json", build_report)
    print(json.dumps(build_report, ensure_ascii=False, indent=2))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
