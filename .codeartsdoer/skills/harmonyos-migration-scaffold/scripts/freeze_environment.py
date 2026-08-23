#!/usr/bin/env python3
"""Freeze one immutable HarmonyOS environment under a new HENV-ID."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from _common import (
    assert_no_secrets,
    atomic_json,
    load_json,
    parse_resolution,
    read_csv,
    safe_relative_path,
    sha256_file,
    unresolved,
    utc_now,
    validate_id,
    write_csv,
)


REQUIRED_COMMAND_CATEGORIES = (
    "TOOLCHAIN",
    "DEVICE",
    "BUNDLE_CHECK",
    "SIGNING_CHECK",
    "CLEAN_BUILD",
    "INSTALL",
    "LAUNCH",
    "ROUTE_SMOKE",
    "SCREENSHOT_CAPTURE",
)


def need(mapping: dict, key: str, label: str) -> object:
    value = mapping.get(key)
    if unresolved(value):
        raise ValueError(f"Missing or unresolved {label}")
    return value


def require_iso8601(value: str, label: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be ISO-8601: {value}") from exc


def resolve_frozen_executable(value: str) -> str:
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or len(candidate.parts) > 1:
        located = candidate
    else:
        resolved = shutil.which(value)
        if not resolved:
            raise ValueError(f"Executable is not on PATH: {value}")
        located = Path(resolved)
    if located.is_symlink():
        raise ValueError(f"Frozen executable must not be a symbolic link: {located}")
    if not located.is_file() or not os.access(located, os.X_OK):
        raise ValueError(f"Frozen executable is unavailable or not executable: {located}")
    try:
        canonical = located.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Cannot resolve frozen executable {located}: {exc}") from exc
    if not canonical.is_file() or not os.access(canonical, os.X_OK):
        raise ValueError(f"Canonical executable is unavailable: {canonical}")
    return str(canonical)


def freeze_category_contracts(toolchain: dict) -> None:
    contracts = toolchain.get("category_contracts")
    if not isinstance(contracts, dict) or set(contracts) != set(REQUIRED_COMMAND_CATEGORIES):
        raise ValueError(
            "toolchain.category_contracts must cover exactly the nine required command categories"
        )
    normalized: dict[str, dict] = {}
    canonical_executables: set[str] = set()
    for category in REQUIRED_COMMAND_CATEGORIES:
        contract = contracts.get(category)
        if not isinstance(contract, dict):
            raise ValueError(f"toolchain.category_contracts.{category} must be an object")
        executable = need(contract, "executable", f"category_contracts.{category}.executable")
        canonical = resolve_frozen_executable(str(executable))
        frozen = dict(contract)
        frozen["executable"] = canonical
        frozen["resolved_executable"] = canonical
        frozen["executable_sha256"] = sha256_file(Path(canonical))
        for key in ("required_argv_tokens", "success_output_contains", "error_output_contains"):
            values = need(frozen, key, f"category_contracts.{category}.{key}")
            if not isinstance(values, list) or not values or not all(
                isinstance(item, str) and item.strip() for item in values
            ):
                raise ValueError(
                    f"toolchain.category_contracts.{category}.{key} must be a non-empty string array"
                )
            frozen[key] = [item.strip() for item in values]
        normalized[category] = frozen
        canonical_executables.add(canonical)
    toolchain["category_contracts"] = normalized
    # Compatibility field for the verifier; it is derived, not independently configurable.
    toolchain["allowed_executables"] = sorted(canonical_executables)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--frozen-by", required=True)
    args = parser.parse_args()

    workspace_input = Path(args.workspace).expanduser().absolute()
    config_input = Path(args.config).expanduser().absolute()
    if workspace_input.is_symlink() or config_input.is_symlink():
        parser.error("Workspace and HENV config must not be symbolic links")
    workspace = workspace_input.resolve()
    config_path = config_input.resolve()
    try:
        manifest = load_json(workspace / "phase-manifest.json")
        config = load_json(config_path)
    except ValueError as exc:
        parser.error(str(exc))
    if manifest.get("phase") != 3:
        parser.error("Not an initialized Phase 3 workspace")
    if (workspace / "CLOSED").exists() or manifest.get("status") == "CLOSED":
        parser.error("Phase 3 workspace is CLOSED; a frozen environment cannot be added")
    if not isinstance(config, dict):
        parser.error("HENV config must be a JSON object")
    if not args.frozen_by.strip():
        parser.error("--frozen-by is required")
    ownership = manifest.get("ownership") if isinstance(manifest.get("ownership"), dict) else {}
    if args.frozen_by != ownership.get("architecture_lead_id"):
        parser.error("--frozen-by must equal the controller-assigned architecture lead")

    config = dict(config)
    config["run_id"] = manifest.get("run_id")
    config["frozen_by"] = args.frozen_by
    config["frozen_at"] = utc_now()
    try:
        henv_id = validate_id(str(need(config, "henv_id", "henv_id")), "HENV-ID")
        if str(need(config, "created_by", "created_by")) != args.frozen_by:
            raise ValueError("HENV created_by must equal --frozen-by")
        require_iso8601(str(need(config, "created_at", "created_at")), "created_at")
        for section in ("host", "toolchain", "application", "signing"):
            if not isinstance(config.get(section), dict):
                raise ValueError(f"HENV {section} must be an object")
        host = config["host"]
        for key in ("os", "os_version", "architecture"):
            need(host, key, f"host.{key}")
        toolchain = config["toolchain"]
        for key in (
            "deveco_studio_version", "harmonyos_sdk_api_target", "compatible_api",
            "build_tool_version", "package_manager_version", "runtime_version",
        ):
            need(toolchain, key, f"toolchain.{key}")
        freeze_category_contracts(toolchain)

        application = config["application"]
        for key in (
            "bundle_name", "product_name", "build_mode", "dependency_lock_file",
            "dependency_lock_sha256",
        ):
            need(application, key, f"application.{key}")
        lock_file = safe_relative_path(
            workspace / "harmony-project",
            str(application["dependency_lock_file"]),
            "dependency lock file",
        )
        if sha256_file(lock_file) != application["dependency_lock_sha256"]:
            raise ValueError("application.dependency_lock_sha256 does not match the project lock file")

        signing = config["signing"]
        for key in (
            "configuration_reference", "certificate_alias", "certificate_fingerprint_sha256",
            "certificate_expires_at", "secret_storage_reference", "secrets_embedded",
        ):
            if key == "secrets_embedded":
                if key not in signing:
                    raise ValueError("Missing signing.secrets_embedded")
            else:
                need(signing, key, f"signing.{key}")
        if signing.get("secrets_embedded") is not False:
            raise ValueError("signing.secrets_embedded must be false")
        fingerprint = re.sub(r"[^0-9A-Fa-f]", "", str(signing["certificate_fingerprint_sha256"]))
        if len(fingerprint) != 64:
            raise ValueError("Signing certificate SHA-256 fingerprint must contain 64 hex digits")
        signing["certificate_fingerprint_sha256"] = fingerprint.upper()
        require_iso8601(str(signing["certificate_expires_at"]), "signing.certificate_expires_at")

        conflict_scope = need(config, "bundle_conflict_check_scope", "bundle_conflict_check_scope")
        if conflict_scope not in {"LOCAL_DEVICE", "PLATFORM_REGISTRY"}:
            raise ValueError("bundle_conflict_check_scope must be LOCAL_DEVICE or PLATFORM_REGISTRY")
        classes = need(config, "target_device_classes", "target_device_classes")
        if not isinstance(classes, list) or not all(isinstance(item, str) and item for item in classes):
            raise ValueError("target_device_classes must be a non-empty string array")
        devices = need(config, "devices", "devices")
        if not isinstance(devices, list) or not devices:
            raise ValueError("At least one HENV device is required")
        device_ids: set[str] = set()
        baseline = 0
        required = 0
        screenshot_required = 0
        baseline_device: dict | None = None
        for index, device in enumerate(devices):
            if not isinstance(device, dict):
                raise ValueError(f"devices[{index}] must be an object")
            device_id = validate_id(str(need(device, "device_id", f"devices[{index}].device_id")), "HDEVICE-ID")
            if device_id in device_ids:
                raise ValueError(f"Duplicate HDEVICE-ID: {device_id}")
            device_ids.add(device_id)
            for key in ("device_type", "model", "serial", "os_version", "api_level", "resolution"):
                need(device, key, f"devices[{index}].{key}")
            parse_resolution(str(device["resolution"]))
            if (
                not isinstance(device.get("is_baseline"), bool)
                or not isinstance(device.get("required"), bool)
                or not isinstance(device.get("screenshot_required"), bool)
            ):
                raise ValueError(f"{device_id}: is_baseline, required, and screenshot_required must be boolean")
            baseline += int(device["is_baseline"])
            required += int(device["required"])
            screenshot_required += int(device["screenshot_required"])
            if device["is_baseline"]:
                baseline_device = device
            if device["screenshot_required"] and (
                device.get("required") is not True or str(device.get("device_type", "")).lower() != "emulator"
            ):
                raise ValueError(f"{device_id}: screenshot-required device must be a required emulator")
        if baseline != 1:
            raise ValueError(f"Exactly one baseline HDEVICE is required; found {baseline}")
        if required == 0:
            raise ValueError("At least one HDEVICE must be required")
        if screenshot_required == 0:
            raise ValueError("At least one frozen emulator must require screenshots")
        if not baseline_device or (
            baseline_device.get("required") is not True
            or baseline_device.get("screenshot_required") is not True
            or str(baseline_device.get("device_type", "")).lower() != "emulator"
        ):
            raise ValueError("Baseline HDEVICE must be a required screenshot-required emulator")
        assert_no_secrets(config)
        if unresolved(config):
            raise ValueError("HENV still contains unresolved placeholders")
    except ValueError as exc:
        parser.error(str(exc))

    registry_path = workspace / "environments" / "henv-registry.csv"
    try:
        registry = read_csv(registry_path)
    except ValueError as exc:
        parser.error(str(exc))
    if any(row.get("henv_id") == henv_id for row in registry):
        parser.error(f"HENV-ID already exists; overwrite is prohibited: {henv_id}")
    final_dir = workspace / "environments" / henv_id
    if final_dir.exists():
        parser.error(f"HENV directory already exists; overwrite is prohibited: {final_dir}")

    with tempfile.TemporaryDirectory(prefix=f".{henv_id}-", dir=workspace / "environments") as temp_name:
        temp_dir = Path(temp_name)
        environment_path = temp_dir / "harmony-environment.json"
        atomic_json(environment_path, config)
        environment_sha = sha256_file(environment_path)
        environment_path.chmod(0o444)
        temp_dir.rename(final_dir)

    registry.append(
        {
            "henv_id": henv_id,
            "environment_sha256": environment_sha,
            "frozen_by": args.frozen_by,
            "frozen_at": config["frozen_at"],
            "status": "FROZEN",
        }
    )
    write_csv(
        registry_path,
        ["henv_id", "environment_sha256", "frozen_by", "frozen_at", "status"],
        registry,
    )
    print(json.dumps({"henv_id": henv_id, "environment": str(final_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
