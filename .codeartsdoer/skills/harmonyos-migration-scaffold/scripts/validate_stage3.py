#!/usr/bin/env python3
"""Validate the exact HarmonyOS scaffold snapshot and issue the Phase 3 gate report."""

from __future__ import annotations

import argparse
import binascii
import json
import os
import re
import stat
import struct
import uuid
import zipfile
import zlib
from pathlib import Path
from typing import Any

from _common import (
    atomic_json,
    atomic_text,
    build_snapshot_manifest,
    csv_fieldnames,
    load_json,
    manifest_text,
    parse_resolution,
    read_csv,
    safe_relative_path,
    sha256_file,
    source_row_key,
    split_multi,
    utc_now,
    validate_id,
)


REQUIRED_PUBLIC_UI_TYPES = {
    "COLOR", "TYPOGRAPHY", "SPACING", "THEME", "PAGE_CONTAINER",
    "LOADING_SHELL", "EMPTY_SHELL", "ERROR_SHELL", "RESPONSIVE_RULE",
}
VALID_MAPPING_TYPES = {"ROUTE_PAGE", "VISUAL_SURFACE", "EXCLUDED_BY_SCOPE"}
SECRET_FILE_SUFFIXES = {".p12", ".pfx", ".jks", ".key", ".pem"}
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(password|passwd|passphrase|token|secret|private[_-]?key|storepass|keypass)\s*[:=]\s*['\"][^'\"]+['\"]"
)
CATEGORY_ORDER = (
    "TOOLCHAIN", "DEVICE", "BUNDLE_CHECK", "SIGNING_CHECK", "CLEAN_BUILD",
    "INSTALL", "LAUNCH", "ROUTE_SMOKE", "SCREENSHOT_CAPTURE",
)
CATEGORY_RANK = {category: index for index, category in enumerate(CATEGORY_ORDER)}
DEVICE_CATEGORIES = {
    "DEVICE", "BUNDLE_CHECK", "INSTALL", "LAUNCH", "ROUTE_SMOKE", "SCREENSHOT_CAPTURE",
}
PER_DEVICE_CATEGORIES = {"DEVICE", "BUNDLE_CHECK", "INSTALL", "LAUNCH", "ROUTE_SMOKE"}
SINGLETON_CATEGORIES = {"TOOLCHAIN", "SIGNING_CHECK", "CLEAN_BUILD"}
REQUIRED_OWNERS = {
    "architecture_lead_id", "toolchain_agent_id", "navigation_agent_id",
    "public_ui_agent_id", "capability_contract_agent_id", "architecture_acceptance_agent_id",
}
CLOSURE_EXCLUDED = {
    "stage-03-gate-report.json", "stage-03-closure-manifest.sha256", "CLOSED",
}
REWORK_ROUTES = {
    "ARCHITECTURE": ("architecture-lead", "architecture_lead_id"),
    "PLACEMENT": ("architecture-lead", "architecture_lead_id"),
    "ASSET": ("architecture-lead", "architecture_lead_id"),
    "DEPENDENCY": ("architecture-lead", "architecture_lead_id"),
    "INPUT": ("architecture-lead", "architecture_lead_id"),
    "TOOLCHAIN": ("toolchain-agent", "toolchain_agent_id"),
    "BUILD": ("toolchain-agent", "toolchain_agent_id"),
    "DEVICE": ("toolchain-agent", "toolchain_agent_id"),
    "BUNDLE": ("toolchain-agent", "toolchain_agent_id"),
    "SIGNING": ("toolchain-agent", "toolchain_agent_id"),
    "INSTALL": ("toolchain-agent", "toolchain_agent_id"),
    "LAUNCH": ("toolchain-agent", "toolchain_agent_id"),
    "ARTIFACT": ("toolchain-agent", "toolchain_agent_id"),
    "SCREENSHOT": ("toolchain-agent", "toolchain_agent_id"),
    "NAVIGATION": ("navigation-agent", "navigation_agent_id"),
    "ROUTE": ("navigation-agent", "navigation_agent_id"),
    "SURFACE": ("navigation-agent", "navigation_agent_id"),
    "MAPPING": ("navigation-agent", "navigation_agent_id"),
    "SMOKE": ("navigation-agent", "navigation_agent_id"),
    "PUBLIC_UI": ("public-ui-agent", "public_ui_agent_id"),
    "RESPONSIVE": ("public-ui-agent", "public_ui_agent_id"),
    "THEME": ("public-ui-agent", "public_ui_agent_id"),
    "CAPABILITY": ("capability-contract-agent", "capability_contract_agent_id"),
    "CONTRACT": ("capability-contract-agent", "capability_contract_agent_id"),
}
ASSET_INVENTORY_FIELDS = [
    "asset_id", "source_path", "archive_path", "sha256", "asset_type",
    "feature_ids", "page_ids", "state_ids", "created_by", "created_at",
    "reviewed_by", "reviewed_at", "status", "notes",
]
ASSET_REGISTRY_FIELDS = [
    "asset_id", "phase2_archive_path", "asset_sha256", "asset_type",
    "feature_ids", "page_ids", "state_ids", "target_module_id", "target_path",
    "target_symbol", "planned_mode", "decision", "created_by", "status", "notes",
]
ASSET_PLAN_DECISIONS = {
    "DIRECT_COPY": "COPY_UNCHANGED",
    "FORMAT_CONVERSION": "CONVERT_FORMAT",
    "RECREATE_FROM_PUBLIC_UI": "RECREATE_LATER",
}
ASSET_SYMBOL_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def full_png_dimensions(path: Path) -> tuple[int, int]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Cannot read PNG {path}: {exc}") from exc
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Invalid PNG signature: {path}")
    offset = 8
    ihdr: bytes | None = None
    idat = bytearray()
    saw_iend = False
    chunk_index = 0
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError(f"Truncated PNG chunk: {path}")
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError(f"Truncated PNG chunk payload: {path}")
        payload = data[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length:end])[0]
        actual_crc = binascii.crc32(payload, binascii.crc32(chunk_type)) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise ValueError(f"PNG chunk CRC mismatch: {path}")
        if chunk_index == 0 and chunk_type != b"IHDR":
            raise ValueError(f"PNG IHDR is not the first chunk: {path}")
        if chunk_type == b"IHDR":
            if ihdr is not None or length != 13:
                raise ValueError(f"Invalid PNG IHDR: {path}")
            ihdr = payload
        elif chunk_type == b"IDAT":
            idat.extend(payload)
        elif chunk_type == b"IEND":
            if length != 0:
                raise ValueError(f"Invalid PNG IEND: {path}")
            saw_iend = True
            offset = end
            break
        offset = end
        chunk_index += 1
    if not ihdr or not idat or not saw_iend or offset != len(data):
        raise ValueError(f"PNG stream is incomplete or has trailing bytes: {path}")
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    valid_depths = {
        0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8},
        4: {8, 16}, 6: {8, 16},
    }
    if (
        width <= 0 or height <= 0 or compression != 0 or filtering != 0 or interlace != 0
        or channels is None or bit_depth not in valid_depths[color_type]
    ):
        raise ValueError(f"Unsupported or invalid PNG IHDR: {path}")
    expected_size = height * (((width * channels * bit_depth + 7) // 8) + 1)
    try:
        decoded = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ValueError(f"Invalid PNG compressed image data: {path}: {exc}") from exc
    if len(decoded) != expected_size:
        raise ValueError(f"PNG decompressed image length differs: {path}")
    return width, height


def validate_hap(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not names or archive.testzip() is not None:
                raise ValueError(f"HAP ZIP payload is empty or corrupt: {path}")
            for name in names:
                candidate = Path(name)
                if candidate.is_absolute() or ".." in candidate.parts:
                    raise ValueError(f"HAP contains an unsafe member path: {name}")
            if not any(Path(name).name in {"module.json", "config.json"} for name in names):
                raise ValueError(f"HAP lacks module.json or config.json: {path}")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"Build artifact is not a valid HAP ZIP: {path}: {exc}") from exc


def check_locked_path_records(value: Any, label: str, errors: list[str]) -> None:
    """Recursively verify every input-lock object that binds path + sha256."""
    if isinstance(value, dict):
        if "path" in value and "sha256" in value:
            raw_path = value.get("path")
            expected = value.get("sha256")
            if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
                errors.append(f"Frozen {label}.path is not an absolute canonical path")
            else:
                path = Path(raw_path)
                try:
                    resolved = path.resolve(strict=True)
                    if str(resolved) != raw_path:
                        errors.append(f"Frozen {label}.path is not canonical: {raw_path}")
                    if not resolved.is_file():
                        errors.append(f"Frozen {label}.path is not a file: {raw_path}")
                    elif sha256_file(resolved) != expected:
                        errors.append(f"Frozen {label} has changed")
                except OSError as exc:
                    errors.append(f"Frozen {label} no longer exists: {raw_path}: {exc}")
            for extra_field in ("snapshot_path", "source_path"):
                if extra_field not in value:
                    continue
                extra_raw = value.get(extra_field)
                if not isinstance(extra_raw, str) or not Path(extra_raw).is_absolute():
                    errors.append(f"Frozen {label}.{extra_field} is not an absolute canonical path")
                    continue
                try:
                    extra = Path(extra_raw).resolve(strict=True)
                    if str(extra) != extra_raw or not extra.is_file():
                        errors.append(f"Frozen {label}.{extra_field} is not a canonical file")
                    elif sha256_file(extra) != expected:
                        errors.append(f"Frozen {label}.{extra_field} differs from its locked hash")
                except OSError as exc:
                    errors.append(f"Frozen {label}.{extra_field} no longer exists: {exc}")
        for key, item in value.items():
            check_locked_path_records(item, f"{label}.{key}", errors)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            check_locked_path_records(item, f"{label}[{index}]", errors)


def command_output_verdict(
    stdout: str, stderr: str, success_patterns: list[str], error_patterns: list[str]
) -> tuple[list[str], list[str]]:
    combined = stdout + "\n" + stderr
    combined_lower = combined.lower()
    return (
        [pattern for pattern in success_patterns if pattern in combined],
        [pattern for pattern in error_patterns if pattern.lower() in combined_lower],
    )


def asset_ref_array(value: str, label: str, errors: list[str]) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        errors.append(f"{label} must be a JSON string array")
        return []
    if (
        not isinstance(parsed, list) or not parsed
        or not all(isinstance(item, str) and item for item in parsed)
        or parsed != sorted(set(parsed))
    ):
        errors.append(f"{label} must be a non-empty sorted JSON string array")
        return []
    return parsed


def verify_tree_read_only(root: Path, errors: list[str]) -> None:
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            errors.append(f"Symbolic link is prohibited in sealed HVER: {path}")
        elif path.exists() and path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            errors.append(f"Sealed HVER path is writable: {path}")


def closure_manifest(workspace: Path) -> str:
    relative_names: list[str] = []
    for path in sorted(workspace.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic link is prohibited at Phase 3 closure: {path}")
        if path.is_file():
            relative = str(path.relative_to(workspace))
            if relative in CLOSURE_EXCLUDED:
                continue
            relative_names.append(relative)
    return manifest_text(workspace, relative_names)


def seal_workspace(workspace: Path) -> None:
    for path in sorted(workspace.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            raise ValueError(f"Symbolic link is prohibited at Phase 3 closure: {path}")
        if path.is_file():
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        elif path.is_dir():
            path.chmod(
                stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP
                | stat.S_IROTH | stat.S_IXOTH
            )
    workspace.chmod(
        stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    )


def text_file(path: Path, label: str, errors: list[str]) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"Cannot read {label} as UTF-8 text: {path}: {exc}")
        return ""


def verify_sealed_manifest(directory: Path, errors: list[str]) -> None:
    manifest = directory / "manifest.sha256"
    if not manifest.is_file() or not (directory / "COMMITTED").is_file():
        errors.append(f"Verification package is not committed: {directory}")
        return
    expected_paths: set[str] = set()
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), start=1):
        if "  " not in line:
            errors.append(f"Malformed verification manifest line {number}: {line}")
            continue
        expected, relative = line.split("  ", 1)
        if relative in expected_paths:
            errors.append(f"Duplicate verification manifest entry: {relative}")
            continue
        expected_paths.add(relative)
        try:
            path = safe_relative_path(directory, relative, "verification manifest artifact")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not path.is_file():
            errors.append(f"Verification manifest path is not a file: {path}")
        elif sha256_file(path) != expected:
            errors.append(f"Verification artifact hash mismatch: {path}")
    actual_paths = {
        str(path.relative_to(directory))
        for path in directory.rglob("*")
        if path.is_file()
        and str(path.relative_to(directory)) not in {"manifest.sha256", "COMMITTED"}
    }
    if expected_paths != actual_paths:
        errors.append(
            f"Verification manifest file set differs; missing={sorted(expected_paths - actual_paths)}, "
            f"extra={sorted(actual_paths - expected_paths)}"
        )


def detect_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(node: str) -> list[str] | None:
        if node in visiting:
            index = path.index(node)
            return path[index:] + [node]
        if node in visited:
            return None
        visiting.add(node)
        path.append(node)
        for dependency in graph.get(node, []):
            cycle = visit(dependency)
            if cycle:
                return cycle
        path.pop()
        visiting.remove(node)
        visited.add(node)
        return None

    for node in graph:
        cycle = visit(node)
        if cycle:
            return cycle
    return None


def result_index(
    results: list[dict[str, Any]], id_field: str, required_devices: set[str], label: str, errors: list[str]
) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for result in results:
        target_id = str(result.get(id_field, ""))
        device_id = str(result.get("device_id", ""))
        if not target_id or not device_id:
            errors.append(f"{label} result lacks {id_field} or device_id")
            continue
        key = (target_id, device_id)
        if key in indexed:
            errors.append(f"Duplicate {label} result: {target_id}/{device_id}")
        indexed[key] = result
        if device_id not in required_devices:
            errors.append(f"{label} result references a non-required device: {device_id}")
    return indexed


def verify_mapping_screenshots(
    row: dict[str, str],
    source_key: str,
    target_kind: str,
    target_id: str,
    screenshot_devices: set[str],
    screenshot_by_id: dict[str, dict[str, str]],
    used_screenshot_ids: set[str],
    errors: list[str],
) -> None:
    screenshot_ids = split_multi(row.get("screenshot_ids", ""))
    if len(screenshot_ids) != len(screenshot_devices):
        errors.append(
            f"{source_key}: screenshot reference count {len(screenshot_ids)} does not match "
            f"screenshot-required emulator count {len(screenshot_devices)}"
        )
    matched_devices: set[str] = set()
    for screenshot_id in screenshot_ids:
        screenshot = screenshot_by_id.get(screenshot_id)
        if not screenshot:
            errors.append(f"{source_key}: unknown Screenshot-ID {screenshot_id}")
            continue
        used_screenshot_ids.add(screenshot_id)
        if screenshot.get("target_kind") != target_kind or screenshot.get("target_id") != target_id:
            errors.append(f"{source_key}: {screenshot_id} proves a different route/surface target")
        if screenshot.get("page_id") != row.get("page_id"):
            errors.append(f"{source_key}: {screenshot_id} Page-ID differs from mapping")
        if screenshot.get("page_shell_id") != row.get("page_shell_id"):
            errors.append(f"{source_key}: {screenshot_id} Page-Shell-ID differs from mapping")
        if row.get("feature_id") not in split_multi(screenshot.get("feature_ids", "")):
            errors.append(f"{source_key}: {screenshot_id} does not bind the mapping Feature-ID")
        device_id = screenshot.get("device_id", "")
        if device_id not in screenshot_devices:
            errors.append(f"{source_key}: {screenshot_id} is not from a screenshot-required emulator")
        if device_id in matched_devices:
            errors.append(f"{source_key}: multiple screenshots reference the same emulator {device_id}")
        matched_devices.add(device_id)
    missing_devices = screenshot_devices - matched_devices
    if missing_devices:
        errors.append(f"{source_key}: screenshot evidence lacks emulators {sorted(missing_devices)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--henv-id", required=True)
    parser.add_argument("--verification-id", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--decision", required=True, choices=("PASS", "INCOMPLETE", "BLOCKED"))
    parser.add_argument("--attest-real-file-review", action="store_true")
    parser.add_argument("--attest-placeholder-boundaries", action="store_true")
    parser.add_argument("--attest-contract-only", action="store_true")
    parser.add_argument("--attest-dependency-review", action="store_true")
    parser.add_argument("--attest-runtime-smoke", action="store_true")
    parser.add_argument("--attest-screenshot-review", action="store_true")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    if (workspace / "CLOSED").exists():
        parser.error("Phase 3 is CLOSED; gate-report writes are prohibited")
    errors: list[str] = []
    warnings: list[str] = []
    try:
        henv_id = validate_id(args.henv_id, "HENV-ID")
        verification_id = validate_id(args.verification_id, "HVER-ID")
    except ValueError as exc:
        parser.error(str(exc))
    reviewer = args.reviewer.strip()
    if not reviewer:
        parser.error("--reviewer is required")

    try:
        phase_manifest = load_json(workspace / "phase-manifest.json")
        input_lock = load_json(workspace / "stage-03-input-lock.json")
        scope = load_json(Path(input_lock["controller_scope"]["path"]))
        closure = load_json(Path(input_lock["phase2_closure"]["path"]))
        inventory_path = Path(input_lock["phase2_inventory"]["path"])
        inventory = read_csv(inventory_path)
        phase2_asset_inventory_path = Path(input_lock["phase2_asset_inventory"]["path"])
        phase2_assets = read_csv(phase2_asset_inventory_path)
        phase2_gate_snapshot = load_json(workspace / "inputs" / "phase-02-gate-report.json")
    except (ValueError, KeyError, TypeError) as exc:
        errors.append(f"Cannot load frozen Phase 3 input: {exc}")
        phase_manifest, input_lock, scope, closure, inventory, phase2_gate_snapshot = {}, {}, {}, {}, [], {}
        phase2_assets = []

    if phase_manifest.get("phase") != 3:
        errors.append("Not an initialized Phase 3 workspace")
    ownership = phase_manifest.get("ownership")
    if not isinstance(ownership, dict) or not REQUIRED_OWNERS.issubset(ownership):
        errors.append("phase-manifest.json lacks frozen Phase 3 ownership")
        ownership = {}
    else:
        owner_values = [str(ownership.get(key, "")) for key in sorted(REQUIRED_OWNERS)]
        if any(not value for value in owner_values):
            errors.append("Frozen Phase 3 ownership contains an empty agent ID")
        if len(set(owner_values)) != len(owner_values):
            errors.append("Every frozen Phase 3 role must have a distinct agent ID")
        locked_ownership = input_lock.get("ownership")
        if locked_ownership != ownership:
            errors.append("phase-manifest ownership differs from stage-03-input-lock ownership")
    if ownership and reviewer != ownership.get("architecture_acceptance_agent_id"):
        parser.error("--reviewer must equal the frozen architecture_acceptance_agent_id")
    for field in ("work_order_id", "work_order_sha256"):
        if not phase_manifest.get(field) or phase_manifest.get(field) != input_lock.get(field):
            errors.append(f"Phase 3 {field} is missing or differs between manifest and input lock")
    if closure.get("final_verdict") != "PASS" or closure.get("evidence_chain_closed") is not True:
        errors.append("Frozen Phase 2 closure is not PASS")
    if phase2_gate_snapshot.get("phase") != 2 or phase2_gate_snapshot.get("verdict") != "PASS":
        errors.append("Frozen Phase 2 controller gate is not PASS")

    check_locked_path_records(input_lock, "input_lock", errors)
    gate_snapshot_path = workspace / "inputs" / "phase-02-gate-report.json"
    if gate_snapshot_path.is_file() and sha256_file(gate_snapshot_path) != input_lock.get("phase2_gate", {}).get("sha256"):
        errors.append("Copied Phase 2 gate snapshot differs from the input lock")

    all_inventory = inventory
    if len(all_inventory) != input_lock.get("phase2_inventory", {}).get("row_count"):
        errors.append("Frozen inventory total row count differs from the input lock")
    inventory = [
        row for row in all_inventory if row.get("row_status", "").upper() != "SUPERSEDED"
    ]
    inventory_by_key: dict[str, dict[str, str]] = {}
    for row in inventory:
        try:
            key = source_row_key(row)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if key in inventory_by_key:
            errors.append(f"Duplicate frozen Source-Row-Key: {key}")
        inventory_by_key[key] = row
    locked_keys = set(input_lock.get("phase2_inventory", {}).get("source_row_keys", []))
    if set(inventory_by_key) != locked_keys:
        errors.append("Frozen inventory Source-Row-Key set differs from the input lock")
    if len(inventory) != input_lock.get("phase2_inventory", {}).get(
        "active_row_count", input_lock.get("phase2_inventory", {}).get("row_count")
    ):
        errors.append("Frozen active inventory row count differs from the input lock")

    try:
        if csv_fieldnames(Path(input_lock["phase2_asset_inventory"]["path"])) != ASSET_INVENTORY_FIELDS:
            errors.append("Frozen Phase 2 asset-inventory header differs")
        phase2_assets_by_id: dict[str, dict[str, str]] = {}
        for row in phase2_assets:
            asset_id = row.get("asset_id", "")
            try:
                validate_id(asset_id, "Asset-ID")
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if asset_id == "NONE_FOUND" or asset_id in phase2_assets_by_id:
                errors.append(f"Sentinel or duplicate frozen Asset-ID: {asset_id!r}")
            phase2_assets_by_id[asset_id] = row
            if (
                row.get("status") != "REVIEWED"
                or row.get("reviewed_by") != scope.get("ownership", {}).get("coverage_checker_id")
            ):
                errors.append(f"Frozen Phase 2 asset is not REVIEWED: {asset_id}")
            for field in ("feature_ids", "page_ids", "state_ids"):
                asset_ref_array(row.get(field, ""), f"{asset_id}.{field}", errors)
        locked_asset_ids = input_lock.get("phase2_asset_inventory", {}).get("asset_ids", [])
        if (
            len(phase2_assets) != input_lock.get("phase2_asset_inventory", {}).get("row_count")
            or sorted(phase2_assets_by_id) != locked_asset_ids
        ):
            errors.append("Frozen Phase 2 asset inventory differs from the input lock")
        manifest_path = Path(input_lock["phase2_asset_package_manifest"]["path"])
        committed_path = Path(input_lock["phase2_asset_package_committed"]["path"])
        if committed_path.read_text(encoding="utf-8") != sha256_file(manifest_path) + "\n":
            errors.append("Frozen Phase 2 asset COMMITTED marker differs from its manifest")
        file_records = input_lock.get("phase2_asset_files", [])
        if not isinstance(file_records, list):
            errors.append("Frozen Phase 2 asset file records are invalid")
            file_records = []
        file_by_id = {
            str(record.get("asset_id")): record for record in file_records if isinstance(record, dict)
        }
        if len(file_by_id) != len(file_records) or set(file_by_id) != set(phase2_assets_by_id):
            errors.append("Frozen Phase 2 asset files are not one-to-one with asset inventory")
        for asset_id, asset in phase2_assets_by_id.items():
            record = file_by_id.get(asset_id, {})
            if (
                record.get("archive_path") != asset.get("archive_path")
                or record.get("sha256") != asset.get("sha256")
            ):
                errors.append(f"Frozen Phase 2 asset file record differs: {asset_id}")
        referenced_assets: set[str] = set()
        for inventory_row in inventory:
            inventory_id = inventory_row.get("inventory_id", "<unknown>")
            refs = asset_ref_array(
                inventory_row.get("asset_ids", ""), f"{inventory_id}.asset_ids", errors
            )
            if "NONE_FOUND" in refs:
                if refs != ["NONE_FOUND"]:
                    errors.append(f"{inventory_id}: NONE_FOUND cannot be mixed with Asset-IDs")
                continue
            for asset_id in refs:
                asset = phase2_assets_by_id.get(asset_id)
                if not asset:
                    errors.append(f"{inventory_id}: frozen Asset-ID is missing: {asset_id}")
                    continue
                for inventory_field, asset_field in (
                    ("feature_id", "feature_ids"), ("page_id", "page_ids"),
                    ("state_id", "state_ids"),
                ):
                    if inventory_row.get(inventory_field) not in asset_ref_array(
                        asset.get(asset_field, ""), f"{asset_id}.{asset_field}", errors
                    ):
                        errors.append(f"{inventory_id}: {asset_id} does not cover {inventory_field}")
                referenced_assets.add(asset_id)
        if referenced_assets != set(phase2_assets_by_id):
            errors.append("Frozen active inventory does not reference every Phase 2 asset")
    except (KeyError, OSError, TypeError, ValueError) as exc:
        errors.append(f"Cannot validate frozen Phase 2 assets: {exc}")
        phase2_assets_by_id = {}

    environment_path = workspace / "environments" / henv_id / "harmony-environment.json"
    verification_dir = workspace / "verification" / verification_id
    preflight_path = verification_dir / "deveco-preflight-report.json"
    try:
        henv_registry = read_csv(workspace / "environments" / "henv-registry.csv")
        environment = load_json(environment_path)
        preflight = load_json(preflight_path)
        verification = load_json(verification_dir / "metadata.json")
        snapshot = load_json(verification_dir / "scaffold-snapshot-manifest.json")
        route_results_doc = load_json(verification_dir / "route-results.json")
        surface_results_doc = load_json(verification_dir / "surface-results.json")
        screenshot_index = read_csv(verification_dir / "screenshot-index.csv")
        artifact_manifest = load_json(verification_dir / "artifact-manifest.json")
        build_report = load_json(workspace / "build-report.json")
    except ValueError as exc:
        errors.append(str(exc))
        henv_registry, environment, preflight, verification, snapshot = [], {}, {}, {}, {}
        route_results_doc, surface_results_doc, screenshot_index, artifact_manifest, build_report = {}, {}, [], {}, {}

    henv_row = next((row for row in henv_registry if row.get("henv_id") == henv_id), None)
    if not henv_row or henv_row.get("status") != "FROZEN":
        errors.append(f"HENV is not frozen: {henv_id}")
    elif environment_path.is_file() and sha256_file(environment_path) != henv_row.get("environment_sha256"):
        errors.append(f"Frozen HENV has changed: {henv_id}")
    if preflight.get("verdict") != "PASS" or preflight.get("henv_id") != henv_id:
        errors.append("DevEco/HarmonyOS environment preflight is not PASS for the selected HENV")
    if preflight.get("environment_sha256") != (sha256_file(environment_path) if environment_path.is_file() else None):
        errors.append("Preflight environment hash differs from the selected HENV")

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
    baseline_devices = {
        device_id for device_id, device in device_by_id.items() if device.get("is_baseline") is True
    }
    if len(baseline_devices) != 1:
        errors.append("Selected HENV must contain exactly one baseline device")
    for device_id in screenshot_devices:
        device = device_by_id[device_id]
        if device_id not in required_devices or str(device.get("device_type", "")).lower() != "emulator":
            errors.append(f"Screenshot-required HDEVICE is not a required emulator: {device_id}")
    if not screenshot_devices or not baseline_devices.issubset(screenshot_devices):
        errors.append("Baseline HDEVICE must be a screenshot-required frozen emulator")
    if verification.get("status") != "PASS":
        errors.append("Selected verification package is not PASS")
    if verification.get("verification_id") != verification_id or verification.get("henv_id") != henv_id:
        errors.append("Selected verification metadata identity does not match")
    if verification.get("input_lock_sha256") != (
        sha256_file(workspace / "stage-03-input-lock.json")
        if (workspace / "stage-03-input-lock.json").is_file() else None
    ):
        errors.append("Verification package references a different Phase 3 input lock")
    if verification.get("environment_sha256") != (
        sha256_file(environment_path) if environment_path.is_file() else None
    ):
        errors.append("Verification package references a different HENV")
    if environment.get("created_by") != ownership.get("architecture_lead_id") or environment.get(
        "frozen_by"
    ) != ownership.get("architecture_lead_id"):
        errors.append("HENV creator/freezer differs from frozen architecture_lead_id")
    if verification.get("executed_by") != ownership.get("toolchain_agent_id"):
        errors.append("HVER executor differs from frozen toolchain_agent_id")
    if verification.get("work_order_id") != phase_manifest.get("work_order_id") or verification.get(
        "work_order_sha256"
    ) != phase_manifest.get("work_order_sha256"):
        errors.append("HVER work-order identity differs from phase-manifest.json")
    if build_report.get("status") != "PASS" or build_report.get("verification_id") != verification_id:
        errors.append("build-report.json is not PASS for the selected HVER-ID")
    if not artifact_manifest.get("artifacts"):
        errors.append("Selected verification contains no built artifact hash")
    if set(verification.get("screenshot_required_devices", [])) != screenshot_devices:
        errors.append("Verification screenshot-required device set differs from HENV")
    if build_report.get("screenshot_count") != len(screenshot_index):
        errors.append("Build report screenshot count differs from sealed screenshot index")

    verify_sealed_manifest(verification_dir, errors)
    verify_tree_read_only(verification_dir, errors)
    try:
        current_snapshot = build_snapshot_manifest(workspace, henv_id)
        if current_snapshot.get("snapshot_sha256") != snapshot.get("snapshot_sha256"):
            errors.append("Current scaffold differs from the verified source snapshot")
        if verification.get("source_snapshot_sha256") != snapshot.get("snapshot_sha256"):
            errors.append("Verification metadata and snapshot manifest hashes differ")
    except ValueError as exc:
        errors.append(str(exc))

    mp4_files = [path for path in workspace.rglob("*") if path.is_file() and path.suffix.lower() == ".mp4"]
    if mp4_files:
        errors.append(f"MP4 is not accepted as formal Phase 3 evidence; found {len(mp4_files)} file(s)")

    project = workspace / "harmony-project"
    for path in project.rglob("*") if project.is_dir() else []:
        if not path.is_file():
            continue
        if path.suffix.lower() in SECRET_FILE_SUFFIXES:
            errors.append(f"Signing/private-key file is prohibited inside the project: {path}")
            continue
        if path.stat().st_size <= 2 * 1024 * 1024:
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if "-----BEGIN" in content.upper() and "PRIVATE KEY-----" in content.upper():
                errors.append(f"Private-key material is prohibited inside the project: {path}")
            if SECRET_ASSIGNMENT_RE.search(content):
                errors.append(f"Possible embedded credential is prohibited inside the project: {path}")

    try:
        policy = load_json(workspace / "dependency-policy.json")
        modules = read_csv(workspace / "module-registry.csv")
        architecture = read_csv(workspace / "architecture-map.csv")
        routes = read_csv(workspace / "route-registry.csv")
        surfaces = read_csv(workspace / "surface-registry.csv")
        public_ui = read_csv(workspace / "public-ui-registry.csv")
        capabilities = read_csv(workspace / "capability-contracts.csv")
        statuses = read_csv(workspace / "migration-status.csv")
        decisions = read_csv(workspace / "architecture-decisions.csv")
        asset_registry = read_csv(workspace / "asset-registry.csv")
        rework = read_csv(workspace / "rework-tickets.csv")
    except ValueError as exc:
        errors.append(str(exc))
        policy, modules, architecture, routes, surfaces, public_ui, capabilities, statuses, decisions, asset_registry, rework = (
            {}, [], [], [], [], [], [], [], [], [], []
        )

    layers = set(policy.get("layers", [])) if isinstance(policy.get("layers"), list) else set()
    allowed_edges = {
        (str(edge.get("from")), str(edge.get("to")))
        for edge in policy.get("allowed_edges", [])
        if isinstance(edge, dict)
    }
    placeholder_forbidden = policy.get("placeholder_forbidden_tokens", [])
    contract_forbidden = policy.get("contract_forbidden_tokens", [])
    if not layers or not allowed_edges:
        errors.append("dependency-policy.json lacks layers or allowed edges")
    if not isinstance(placeholder_forbidden, list) or not isinstance(contract_forbidden, list):
        errors.append("dependency-policy forbidden-token lists are invalid")
        placeholder_forbidden, contract_forbidden = [], []

    module_by_id: dict[str, dict[str, str]] = {}
    module_paths: dict[str, Path] = {}
    module_features: set[str] = set()
    graph: dict[str, list[str]] = {}
    for row in modules:
        module_id = row.get("harmony_module_id", "")
        try:
            validate_id(module_id, "Harmony-Module-ID")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if module_id in module_by_id:
            errors.append(f"Duplicate Harmony-Module-ID: {module_id}")
        module_by_id[module_id] = row
        if row.get("status") != "READY":
            errors.append(f"Module is not READY: {module_id}")
        if row.get("created_by") != ownership.get("toolchain_agent_id"):
            errors.append(f"Module creator differs from frozen toolchain_agent_id: {module_id}")
        if row.get("layer") not in layers:
            errors.append(f"Module has unknown layer: {module_id}/{row.get('layer')}")
        try:
            module_path = safe_relative_path(project, row.get("module_path", ""), f"module path for {module_id}")
            build_config = safe_relative_path(
                project, row.get("build_config_path", ""), f"build config for {module_id}"
            )
            if not module_path.is_dir() or not build_config.is_file():
                errors.append(f"Module path/build config is not real: {module_id}")
            else:
                module_paths[module_id] = module_path
        except ValueError as exc:
            errors.append(str(exc))
        features = split_multi(row.get("feature_ids", ""))
        module_features.update(features)
        dependencies = split_multi(row.get("declared_dependencies", ""))
        graph[module_id] = dependencies

    for module_id, dependencies in graph.items():
        for dependency in dependencies:
            if dependency == module_id:
                errors.append(f"Module depends on itself: {module_id}")
                continue
            if dependency not in module_by_id:
                errors.append(f"Module dependency does not exist: {module_id} -> {dependency}")
                continue
            edge = (module_by_id[module_id].get("layer", ""), module_by_id[dependency].get("layer", ""))
            if edge not in allowed_edges:
                errors.append(f"Dependency direction is not allowed: {module_id} -> {dependency} ({edge[0]} -> {edge[1]})")
    cycle = detect_cycle(graph)
    if cycle:
        errors.append(f"Module dependency cycle: {' -> '.join(cycle)}")

    included_features = set(input_lock.get("included_feature_ids", []))
    excluded_features = set(input_lock.get("excluded_feature_ids", []))
    missing_module_features = included_features - module_features
    if missing_module_features:
        errors.append(f"In-scope Feature-ID lacks module landing: {sorted(missing_module_features)}")

    try:
        if csv_fieldnames(workspace / "asset-registry.csv") != ASSET_REGISTRY_FIELDS:
            errors.append("asset-registry.csv header differs from the Phase 3 asset contract")
    except ValueError as exc:
        errors.append(str(exc))
    asset_registry_by_id: dict[str, dict[str, str]] = {}
    target_paths: set[str] = set()
    target_symbols: set[tuple[str, str]] = set()
    for row in asset_registry:
        asset_id = row.get("asset_id", "")
        try:
            validate_id(asset_id, "Asset-ID")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if asset_id in asset_registry_by_id:
            errors.append(f"Duplicate Phase 3 Asset-ID: {asset_id}")
        asset_registry_by_id[asset_id] = row
        source = phase2_assets_by_id.get(asset_id)
        if source is None:
            errors.append(f"Phase 3 asset registry references unknown Phase 2 asset: {asset_id}")
            continue
        for target_field, source_field in (
            ("phase2_archive_path", "archive_path"), ("asset_sha256", "sha256"),
            ("asset_type", "asset_type"), ("feature_ids", "feature_ids"),
            ("page_ids", "page_ids"), ("state_ids", "state_ids"),
        ):
            if row.get(target_field) != source.get(source_field):
                errors.append(f"{asset_id}: {target_field} differs from frozen Phase 2 asset")
        if row.get("created_by") != ownership.get("architecture_lead_id"):
            errors.append(f"{asset_id}: asset plan creator differs from architecture_lead_id")
        if row.get("status") != "READY":
            errors.append(f"{asset_id}: asset plan is not READY")
        planned_mode = row.get("planned_mode", "")
        if ASSET_PLAN_DECISIONS.get(planned_mode) != row.get("decision"):
            errors.append(f"{asset_id}: planned_mode/decision pair is invalid")
        module_id = row.get("target_module_id", "")
        module_path = module_paths.get(module_id)
        if module_path is None:
            errors.append(f"{asset_id}: target module is missing or invalid: {module_id}")
        target_path_value = row.get("target_path", "")
        if any(ord(character) < 32 for character in target_path_value) or "\\" in target_path_value:
            errors.append(f"{asset_id}: target_path contains unsafe characters")
        if target_path_value in target_paths:
            errors.append(f"Duplicate Phase 3 asset target_path: {target_path_value}")
        target_paths.add(target_path_value)
        try:
            target_path = safe_relative_path(
                project, target_path_value, f"asset target path for {asset_id}", must_exist=False
            )
            if module_path is not None:
                target_path.relative_to(module_path.resolve())
            relative_to_module = target_path.relative_to(module_path.resolve()).as_posix() if module_path else ""
            if not relative_to_module.startswith("src/main/resources/") or target_path.name in {"", ".", ".."}:
                errors.append(f"{asset_id}: target_path is not a HarmonyOS module resource landing")
        except (ValueError, OSError) as exc:
            errors.append(f"{asset_id}: unsafe target_path: {exc}")
        symbol = row.get("target_symbol", "")
        if not ASSET_SYMBOL_RE.fullmatch(symbol):
            errors.append(f"{asset_id}: target_symbol is invalid")
        symbol_key = (module_id, symbol)
        if symbol_key in target_symbols:
            errors.append(f"Duplicate asset target_symbol in module: {module_id}/{symbol}")
        target_symbols.add(symbol_key)
        if planned_mode == "DIRECT_COPY":
            source_suffix = Path(source.get("archive_path", "")).suffix.lower()
            if Path(target_path_value).suffix.lower() != source_suffix:
                errors.append(f"{asset_id}: DIRECT_COPY must preserve the source file suffix")
    if set(asset_registry_by_id) != set(phase2_assets_by_id):
        errors.append("Phase 3 asset registry is not one-to-one with frozen Phase 2 assets")

    route_by_id: dict[str, dict[str, str]] = {}
    for row in routes:
        route_id = row.get("route_id", "")
        try:
            validate_id(route_id, "Route-ID")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if route_id in route_by_id:
            errors.append(f"Duplicate Route-ID: {route_id}")
        route_by_id[route_id] = row
    surface_by_id: dict[str, dict[str, str]] = {}
    for row in surfaces:
        surface_id = row.get("surface_shell_id", "")
        try:
            validate_id(surface_id, "Surface-Shell-ID")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if surface_id in surface_by_id:
            errors.append(f"Duplicate Surface-Shell-ID: {surface_id}")
        surface_by_id[surface_id] = row

    route_results = result_index(
        route_results_doc.get("results", []) if isinstance(route_results_doc.get("results"), list) else [],
        "route_id", required_devices, "route", errors,
    )
    surface_results = result_index(
        surface_results_doc.get("results", []) if isinstance(surface_results_doc.get("results"), list) else [],
        "surface_shell_id", required_devices, "surface", errors,
    )

    category_contracts = environment.get("toolchain", {}).get("category_contracts", {})
    if not isinstance(category_contracts, dict) or set(category_contracts) != set(CATEGORY_ORDER):
        errors.append("Frozen HENV category_contracts does not exactly cover required command categories")
        category_contracts = {}
    command_by_id: dict[str, dict[str, Any]] = {}
    command_counts = {category: 0 for category in CATEGORY_ORDER}
    command_devices = {category: set() for category in PER_DEVICE_CATEGORIES}
    last_rank = -1
    project_resolved = project.resolve() if project.exists() else project
    bundle_name = str(environment.get("application", {}).get("bundle_name", ""))
    if not bundle_name:
        errors.append("Frozen HENV has no application.bundle_name")
    for command in verification.get("commands", []):
        if not isinstance(command, dict):
            errors.append("Verification metadata contains a non-object command record")
            continue
        command_id = str(command.get("command_id", ""))
        try:
            validate_id(command_id, "Command-ID")
        except ValueError as exc:
            errors.append(str(exc))
        if command_id in command_by_id:
            errors.append(f"Duplicate command record in verification metadata: {command_id}")
        command_by_id[command_id] = command
        category = str(command.get("category", ""))
        if category not in CATEGORY_RANK:
            errors.append(f"{command_id}: unknown command category {category}")
            continue
        if CATEGORY_RANK[category] < last_rank:
            errors.append(f"{command_id}: command category order differs from the required pipeline")
        last_rank = CATEGORY_RANK[category]
        command_counts[category] += 1
        if command.get("command_verdict") != "PASS" or command.get("exit_code") != 0 or command.get(
            "timed_out"
        ) is not False:
            errors.append(f"{command_id}: command record is not an untimed PASS")
        argv = command.get("argv")
        if not isinstance(argv, list) or not argv or any(
            not isinstance(item, str) or not item for item in argv
        ):
            errors.append(f"{command_id}: command argv is invalid")
            argv = []
        contract = category_contracts.get(category, {})
        if not isinstance(contract, dict):
            errors.append(f"{command_id}: frozen category contract is invalid")
            contract = {}
        executable = str(contract.get("resolved_executable", ""))
        executable_hash = str(contract.get("executable_sha256", "")).lower()
        required_tokens = contract.get("required_argv_tokens", [])
        success_patterns = contract.get("success_output_contains", [])
        error_patterns = contract.get("error_output_contains", [])
        if any(
            not isinstance(items, list) or not items
            or any(not isinstance(item, str) or not item for item in items)
            for items in (required_tokens, success_patterns, error_patterns)
        ):
            errors.append(f"{command_id}: frozen category contract string arrays are invalid")
            required_tokens, success_patterns, error_patterns = [], [], []
        if not Path(executable).is_absolute() or str(Path(executable).resolve()) != executable:
            errors.append(f"{command_id}: frozen executable path is not absolute/canonical")
        elif not Path(executable).is_file() or sha256_file(Path(executable)) != executable_hash:
            errors.append(f"{command_id}: frozen executable is missing or changed")
        if (
            not argv or argv[0] != executable
            or command.get("resolved_executable") != executable
            or command.get("executable_sha256") != executable_hash
        ):
            errors.append(f"{command_id}: command executable identity differs from frozen contract")
        if command.get("required_argv_tokens") != required_tokens or any(
            token not in argv for token in required_tokens
        ):
            errors.append(f"{command_id}: argv does not satisfy its frozen required tokens")
        if command.get("success_output_contains") != success_patterns or command.get(
            "error_output_contains"
        ) != error_patterns:
            errors.append(f"{command_id}: output patterns differ from frozen category contract")
        try:
            stdout_path = safe_relative_path(
                verification_dir, str(command.get("stdout_path", "")), f"stdout log for {command_id}"
            )
            stderr_path = safe_relative_path(
                verification_dir, str(command.get("stderr_path", "")), f"stderr log for {command_id}"
            )
            stdout = text_file(stdout_path, f"stdout log for {command_id}", errors)
            stderr = text_file(stderr_path, f"stderr log for {command_id}", errors)
            if sha256_file(stdout_path) != command.get("stdout_sha256"):
                errors.append(f"{command_id}: stdout log hash differs")
            if sha256_file(stderr_path) != command.get("stderr_sha256"):
                errors.append(f"{command_id}: stderr log hash differs")
            success_hits, error_hits = command_output_verdict(
                stdout, stderr, list(success_patterns), list(error_patterns)
            )
            if len(success_hits) != len(success_patterns) or error_hits:
                errors.append(f"{command_id}: sealed logs do not prove success or contain an error marker")
            if command.get("success_output_matches") != success_hits or command.get(
                "error_output_matches"
            ) != error_hits:
                errors.append(f"{command_id}: stored output-pattern matches differ from sealed logs")
        except (ValueError, OSError) as exc:
            errors.append(str(exc))
        try:
            cwd = Path(str(command.get("cwd", ""))).resolve(strict=True)
            cwd.relative_to(project_resolved)
            if not cwd.is_dir():
                raise ValueError("not a directory")
        except (OSError, ValueError) as exc:
            errors.append(f"{command_id}: command cwd is not a real project directory: {exc}")
        device_id = str(command.get("device_id", ""))
        if category in DEVICE_CATEGORIES:
            if device_id not in (screenshot_devices if category == "SCREENSHOT_CAPTURE" else required_devices):
                errors.append(f"{command_id}: command device is not allowed for {category}")
            serial = str(device_by_id.get(device_id, {}).get("serial", ""))
            if not serial or serial not in argv or command.get("device_serial") != serial:
                errors.append(f"{command_id}: command does not bind the frozen exact device serial")
            if category in PER_DEVICE_CATEGORIES:
                command_devices[category].add(device_id)
        elif device_id:
            errors.append(f"{command_id}: non-device category declares a device")
        if category in {"BUNDLE_CHECK", "SIGNING_CHECK", "LAUNCH", "ROUTE_SMOKE"}:
            if bundle_name not in argv:
                errors.append(
                    f"{command_id}: {category} argv does not bind the frozen bundle name"
                )
    if verification.get("category_order") != list(CATEGORY_ORDER):
        errors.append("HVER category_order differs from the required pipeline")
    for category in CATEGORY_ORDER:
        if not command_counts[category]:
            errors.append(f"HVER lacks command category: {category}")
    for category in SINGLETON_CATEGORIES:
        if command_counts[category] != 1:
            errors.append(f"HVER must contain exactly one {category} command")
    for category in PER_DEVICE_CATEGORIES:
        if command_devices[category] != required_devices:
            errors.append(f"HVER {category} device coverage differs from required HENV devices")

    all_result_items = [
        ("ROUTE_PAGE", "route_id", result)
        for result in (route_results_doc.get("results", []) if isinstance(route_results_doc.get("results"), list) else [])
    ] + [
        ("VISUAL_SURFACE", "surface_shell_id", result)
        for result in (
            surface_results_doc.get("results", [])
            if isinstance(surface_results_doc.get("results"), list) else []
        )
    ]
    bound_smoke_keys: set[tuple[str, str, str]] = set()
    for target_kind, id_field, result in all_result_items:
        if not isinstance(result, dict):
            errors.append("Route/surface aggregate contains a non-object result")
            continue
        command_id = str(result.get("command_id", ""))
        command = command_by_id.get(command_id)
        target_id = str(result.get(id_field, ""))
        device_id = str(result.get("device_id", ""))
        key = (target_kind, target_id, device_id)
        if key in bound_smoke_keys:
            errors.append(f"Duplicate bound route/surface result: {key}")
        bound_smoke_keys.add(key)
        if not command or command.get("category") != "ROUTE_SMOKE":
            errors.append(f"{target_id}/{device_id}: bound ROUTE_SMOKE command is missing")
            continue
        expected_bindings = {
            "target_kind": target_kind,
            "target_id": target_id,
            "page_id": result.get("page_id"),
            "page_shell_id": result.get("page_shell_id"),
            "device_id": device_id,
        }
        for field, value in expected_bindings.items():
            if command.get(field) != value:
                errors.append(f"{command_id}: aggregate result {field} differs from command")
        serial = str(device_by_id.get(device_id, {}).get("serial", ""))
        if result.get("device_serial") != serial or result.get("bundle_name") != bundle_name:
            errors.append(f"{command_id}: aggregate result lacks frozen serial/bundle binding")
        if result.get("verification_id") != verification_id or result.get("henv_id") != henv_id:
            errors.append(f"{command_id}: aggregate result HVER/HENV identity differs")
        if result.get("stdout_sha256") != command.get("stdout_sha256") or result.get(
            "stderr_sha256"
        ) != command.get("stderr_sha256"):
            errors.append(f"{command_id}: aggregate result log hashes differ from command")
        if result.get("result_output_path") != command.get("result_output_relative"):
            errors.append(f"{command_id}: aggregate result output path differs from command")
        try:
            output_path = safe_relative_path(
                project, str(result.get("result_output_path", "")), f"smoke output for {command_id}"
            )
            if sha256_file(output_path) != result.get("result_output_sha256"):
                errors.append(f"{command_id}: generated single-result JSON hash differs")
            generated = load_json(output_path)
            if not isinstance(generated, dict) or "results" in generated or "result" in generated:
                errors.append(f"{command_id}: generated result is not one direct JSON object")
            else:
                for field in (
                    id_field, "page_id", "page_shell_id", "device_id", "device_serial",
                    "bundle_name", "status",
                ):
                    if generated.get(field) != result.get(field):
                        errors.append(f"{command_id}: generated result {field} differs from aggregate")
        except ValueError as exc:
            errors.append(str(exc))

    artifacts = artifact_manifest.get("artifacts", [])
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("Selected verification contains no sealed HAP artifact")
        artifacts = []
    clean_build_ids = {
        command_id for command_id, command in command_by_id.items()
        if command.get("category") == "CLEAN_BUILD" and command.get("command_verdict") == "PASS"
    }
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            errors.append("Artifact manifest contains a non-object row")
            continue
        if artifact.get("produced_by_command_id") not in clean_build_ids:
            errors.append("Sealed HAP is not bound to the passing CLEAN_BUILD command")
        try:
            source = safe_relative_path(project, str(artifact.get("path", "")), "source HAP artifact")
            sealed = safe_relative_path(
                verification_dir, str(artifact.get("sealed_path", "")), "sealed HAP artifact"
            )
            validate_hap(source)
            validate_hap(sealed)
            source_hash = sha256_file(source)
            sealed_hash = sha256_file(sealed)
            if (
                source_hash != artifact.get("sha256")
                or sealed_hash != artifact.get("sealed_sha256")
                or source_hash != sealed_hash
                or source.stat().st_size != artifact.get("size")
            ):
                errors.append(f"HAP source/sealed copy identity differs: {artifact.get('path')}")
        except (ValueError, OSError) as exc:
            errors.append(str(exc))

    screenshot_by_id: dict[str, dict[str, str]] = {}
    for row in screenshot_index:
        screenshot_id = row.get("screenshot_id", "")
        try:
            validate_id(screenshot_id, "HSCREEN-ID")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if screenshot_id in screenshot_by_id:
            errors.append(f"Duplicate Screenshot-ID: {screenshot_id}")
        screenshot_by_id[screenshot_id] = row
        if row.get("verification_id") != verification_id or row.get("henv_id") != henv_id:
            errors.append(f"{screenshot_id}: screenshot index identity differs from selected HVER/HENV")
        if row.get("status") != "SEALED":
            errors.append(f"{screenshot_id}: screenshot is not SEALED")
        if row.get("device_id") not in screenshot_devices:
            errors.append(f"{screenshot_id}: screenshot did not come from a screenshot-required emulator")
        expected_relative = f"screenshots/{screenshot_id}"
        if row.get("relative_path") != expected_relative:
            errors.append(f"{screenshot_id}: screenshot package path is not canonical")
        try:
            screenshot_dir = safe_relative_path(
                verification_dir, row.get("relative_path", ""), f"screenshot package for {screenshot_id}"
            )
            verify_sealed_manifest(screenshot_dir, errors)
            screenshot_png = screenshot_dir / "screenshot.png"
            metadata = load_json(screenshot_dir / "metadata.json")
            width, height = full_png_dimensions(screenshot_png)
            expected_width, expected_height = parse_resolution(
                str(device_by_id[row["device_id"]].get("resolution", ""))
            )
            if (width, height) not in {
                (expected_width, expected_height), (expected_height, expected_width),
            }:
                errors.append(
                    f"{screenshot_id}: PNG dimensions {width}x{height} differ from frozen emulator "
                    f"resolution {expected_width}x{expected_height}"
                )
            if row.get("width") != str(width) or row.get("height") != str(height):
                errors.append(f"{screenshot_id}: screenshot index dimensions differ from PNG")
            png_hash = sha256_file(screenshot_png)
            if row.get("png_sha256") != png_hash or metadata.get("png_sha256") != png_hash:
                errors.append(f"{screenshot_id}: PNG hash differs from index or metadata")
            for field in (
                "screenshot_id", "verification_id", "henv_id", "device_id", "target_kind",
                "target_id", "page_id", "page_shell_id", "smoke_command_id",
                "capture_command_id", "captured_by",
            ):
                if str(metadata.get(field, "")) != str(row.get(field, "")):
                    errors.append(f"{screenshot_id}: metadata {field} differs from screenshot index")
            if sorted(metadata.get("feature_ids", [])) != sorted(split_multi(row.get("feature_ids", ""))):
                errors.append(f"{screenshot_id}: metadata Feature-ID set differs from screenshot index")
            screenshot_contract = category_contracts.get("SCREENSHOT_CAPTURE", {})
            if (
                metadata.get("capture_executable") != screenshot_contract.get("resolved_executable")
                or metadata.get("capture_executable_sha256")
                != screenshot_contract.get("executable_sha256")
            ):
                errors.append(f"{screenshot_id}: capture executable differs from frozen contract")
            serial = str(device_by_id.get(row.get("device_id", ""), {}).get("serial", ""))
            if metadata.get("device_serial") != serial or metadata.get("bundle_name") != bundle_name:
                errors.append(f"{screenshot_id}: screenshot lacks frozen serial/bundle binding")
            if metadata.get("captured_by") != verification.get("executed_by"):
                errors.append(f"{screenshot_id}: screenshot collector differs from HVER executor")
            if metadata.get("captured_by") == reviewer:
                errors.append(f"{screenshot_id}: final reviewer cannot be the screenshot collector")
        except (ValueError, OSError, KeyError) as exc:
            errors.append(f"{screenshot_id}: {exc}")
            continue
        command = command_by_id.get(row.get("capture_command_id", ""))
        if not command:
            errors.append(f"{screenshot_id}: capture command record is missing")
        elif (
            command.get("category") != "SCREENSHOT_CAPTURE"
            or command.get("command_verdict") != "PASS"
            or command.get("device_id") != row.get("device_id")
            or command.get("screenshot_id") != screenshot_id
            or command.get("target_kind") != row.get("target_kind")
            or command.get("target_id") != row.get("target_id")
        ):
            errors.append(f"{screenshot_id}: capture command record does not bind the screenshot identity")
        else:
            if (
                metadata.get("capture_stdout_sha256") != command.get("stdout_sha256")
                or metadata.get("capture_stderr_sha256") != command.get("stderr_sha256")
            ):
                errors.append(f"{screenshot_id}: screenshot capture log hashes differ from command")
            smoke = command_by_id.get(row.get("smoke_command_id", ""))
            if not smoke or smoke.get("category") != "ROUTE_SMOKE" or smoke.get(
                "command_verdict"
            ) != "PASS":
                errors.append(f"{screenshot_id}: referenced ROUTE_SMOKE command is missing/not PASS")
            else:
                for field in ("device_id", "target_kind", "target_id", "page_id", "page_shell_id"):
                    if str(smoke.get(field, "")) != str(row.get(field, "")):
                        errors.append(f"{screenshot_id}: {field} differs from its ROUTE_SMOKE command")

    if set(verification.get("screenshot_ids", [])) != set(screenshot_by_id):
        errors.append("HVER screenshot ID set differs from screenshot-index.csv")

    architecture_by_key: dict[str, dict[str, str]] = {}
    used_routes: set[str] = set()
    used_surfaces: set[str] = set()
    used_screenshot_ids: set[str] = set()
    shell_files: set[Path] = set()
    page_kinds: dict[str, str] = {}
    for row in architecture:
        key = row.get("source_row_key", "")
        if key in architecture_by_key:
            errors.append(f"Duplicate architecture Source-Row-Key: {key}")
        architecture_by_key[key] = row
        source = inventory_by_key.get(key)
        if not source:
            errors.append(f"Architecture mapping references unknown Source-Row-Key: {key}")
            continue
        for field in ("inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id"):
            if row.get(field) != source.get(field):
                errors.append(f"{key}: architecture {field} differs from frozen inventory")
        mapping_type = row.get("mapping_type", "")
        if mapping_type not in VALID_MAPPING_TYPES:
            errors.append(f"{key}: invalid mapping_type {mapping_type}")
            continue
        if row.get("mapped_by") != ownership.get("navigation_agent_id"):
            errors.append(f"{key}: mapper differs from frozen navigation_agent_id")
        if mapping_type == "EXCLUDED_BY_SCOPE":
            if row.get("feature_id") not in excluded_features:
                errors.append(f"{key}: exclusion was not authorized by Phase 1")
            if row.get("mapping_status") != "EXCLUDED_BY_SCOPE":
                errors.append(f"{key}: excluded mapping has wrong status")
            if row.get("screenshot_ids"):
                errors.append(f"{key}: excluded mapping must not claim screenshot evidence")
            continue
        page_id = row.get("page_id", "")
        previous_kind = page_kinds.setdefault(page_id, mapping_type)
        if previous_kind != mapping_type:
            errors.append(f"Page-ID mixes route/surface mapping kinds across states: {page_id}")
        if row.get("mapping_status") != "SHELL_CREATED_PENDING_IMPLEMENTATION":
            errors.append(f"{key}: visual mapping status is not SHELL_CREATED_PENDING_IMPLEMENTATION")
        module_id = row.get("harmony_module_id", "")
        if module_id not in module_by_id:
            errors.append(f"{key}: mapping references missing module {module_id}")
        if row.get("verification_id") != verification_id:
            errors.append(f"{key}: mapping does not reference selected HVER-ID")
        try:
            shell = safe_relative_path(project, row.get("shell_file", ""), f"shell file for {key}")
            if not shell.is_file():
                raise ValueError(f"Shell path is not a file: {shell}")
            shell_files.add(shell)
            content = text_file(shell, f"shell for {key}", errors)
            for literal in (
                row.get("feature_id", ""), row.get("page_id", ""), row.get("page_shell_id", ""),
            ):
                if literal and literal not in content:
                    errors.append(f"{key}: shell does not contain identity literal {literal}")
            for token in placeholder_forbidden:
                if token and token in content:
                    errors.append(f"{key}: page/surface shell contains forbidden token {token!r}")
        except ValueError as exc:
            errors.append(str(exc))
            continue

        if mapping_type == "ROUTE_PAGE":
            route_id = row.get("route_id", "")
            try:
                validate_id(route_id, "Route-ID")
                validate_id(row.get("page_shell_id", ""), "Page-Shell-ID")
            except ValueError as exc:
                errors.append(f"{key}: {exc}")
            used_routes.add(route_id)
            if row.get("surface_shell_id"):
                errors.append(f"{key}: ROUTE_PAGE must not set surface_shell_id")
            route = route_by_id.get(route_id)
            if not route:
                errors.append(f"{key}: route registry lacks {route_id}")
                continue
            if route_id not in content:
                errors.append(f"{key}: page shell does not contain Route-ID literal {route_id}")
            for field in ("page_id", "page_shell_id", "harmony_module_id"):
                if route.get(field) != row.get(field):
                    errors.append(f"{key}: route {field} differs from architecture mapping")
            if route.get("page_shell_file") != row.get("shell_file"):
                errors.append(f"{key}: route shell path differs from architecture mapping")
            if route.get("status") != "READY" or route.get("created_by") != ownership.get(
                "navigation_agent_id"
            ):
                errors.append(f"{route_id}: route is not READY or creator differs from navigation owner")
            try:
                registry_file = safe_relative_path(
                    project, route.get("registry_file", ""), f"route registry file for {route_id}"
                )
                registry_text = text_file(registry_file, f"route registry for {route_id}", errors)
                for literal in (route_id, route.get("registry_symbol", "")):
                    if literal and literal not in registry_text:
                        errors.append(f"{route_id}: real registry file lacks literal/symbol {literal}")
            except ValueError as exc:
                errors.append(str(exc))
            for device_id in required_devices:
                result = route_results.get((route_id, device_id))
                if not result or result.get("status") != "PASS":
                    errors.append(f"{route_id}: no passing route smoke on {device_id}")
                elif result.get("page_id") != row.get("page_id") or result.get("page_shell_id") != row.get("page_shell_id"):
                    errors.append(f"{route_id}: route smoke identity differs on {device_id}")
            verify_mapping_screenshots(
                row, key, "ROUTE_PAGE", route_id, screenshot_devices,
                screenshot_by_id, used_screenshot_ids, errors,
            )
        elif mapping_type == "VISUAL_SURFACE":
            surface_id = row.get("surface_shell_id", "")
            try:
                validate_id(surface_id, "Surface-Shell-ID")
                validate_id(row.get("page_shell_id", ""), "Page-Shell-ID")
            except ValueError as exc:
                errors.append(f"{key}: {exc}")
            used_surfaces.add(surface_id)
            if row.get("route_id"):
                errors.append(f"{key}: VISUAL_SURFACE must not set route_id")
            surface = surface_by_id.get(surface_id)
            if not surface:
                errors.append(f"{key}: surface registry lacks {surface_id}")
                continue
            if surface_id not in content:
                errors.append(f"{key}: visual surface shell does not contain Surface-Shell-ID literal {surface_id}")
            for field in ("page_id", "page_shell_id", "harmony_module_id"):
                if surface.get(field) != row.get(field):
                    errors.append(f"{key}: surface {field} differs from architecture mapping")
            if surface.get("surface_file") != row.get("shell_file"):
                errors.append(f"{key}: surface file differs from architecture mapping")
            if surface.get("status") != "READY" or surface.get("created_by") != ownership.get(
                "navigation_agent_id"
            ):
                errors.append(f"{surface_id}: surface is not READY or creator differs from navigation owner")
            surface_text = text_file(shell, f"surface for {surface_id}", errors)
            if surface.get("surface_symbol") and surface.get("surface_symbol") not in surface_text:
                errors.append(f"{surface_id}: surface file lacks registered symbol")
            for device_id in required_devices:
                result = surface_results.get((surface_id, device_id))
                if not result or result.get("status") != "PASS":
                    errors.append(f"{surface_id}: no passing surface smoke on {device_id}")
                elif result.get("page_id") != row.get("page_id") or result.get("page_shell_id") != row.get("page_shell_id"):
                    errors.append(f"{surface_id}: surface smoke identity differs on {device_id}")
            verify_mapping_screenshots(
                row, key, "VISUAL_SURFACE", surface_id, screenshot_devices,
                screenshot_by_id, used_screenshot_ids, errors,
            )

    if set(architecture_by_key) != set(inventory_by_key):
        missing = set(inventory_by_key) - set(architecture_by_key)
        extra = set(architecture_by_key) - set(inventory_by_key)
        errors.append(f"Architecture map is not one-to-one with inventory; missing={sorted(missing)}, extra={sorted(extra)}")
    if set(route_by_id) != used_routes:
        errors.append(f"Route registry has unmapped or missing routes: registry={sorted(route_by_id)}, used={sorted(used_routes)}")
    if set(surface_by_id) != used_surfaces:
        errors.append(
            f"Surface registry has unmapped or missing surfaces: registry={sorted(surface_by_id)}, used={sorted(used_surfaces)}"
        )
    expected_bound_smokes = {
        ("ROUTE_PAGE", target_id, device_id)
        for target_id in used_routes for device_id in required_devices
    } | {
        ("VISUAL_SURFACE", target_id, device_id)
        for target_id in used_surfaces for device_id in required_devices
    }
    if bound_smoke_keys != expected_bound_smokes:
        errors.append(
            f"Bound route/surface command evidence differs; "
            f"missing={sorted(expected_bound_smokes - bound_smoke_keys)}, "
            f"extra={sorted(bound_smoke_keys - expected_bound_smokes)}"
        )
    if set(screenshot_by_id) != used_screenshot_ids:
        errors.append(
            f"Screenshot index has orphan or unreferenced evidence: "
            f"indexed={sorted(screenshot_by_id)}, used={sorted(used_screenshot_ids)}"
        )

    public_types: set[str] = set()
    restricted_public_symbols: set[str] = set()
    public_ids: set[str] = set()
    for row in public_ui:
        foundation_id = row.get("foundation_id", "")
        if foundation_id in public_ids:
            errors.append(f"Duplicate public UI foundation ID: {foundation_id}")
        public_ids.add(foundation_id)
        foundation_type = row.get("foundation_type", "")
        public_types.add(foundation_type)
        if foundation_type not in REQUIRED_PUBLIC_UI_TYPES:
            errors.append(f"Unknown public UI foundation type: {foundation_type}")
        if row.get("harmony_module_id") not in module_by_id:
            errors.append(f"{foundation_id}: public UI references missing module")
        if row.get("status") != "READY" or row.get("created_by") != ownership.get("public_ui_agent_id"):
            errors.append(f"{foundation_id}: public UI is not READY or creator differs from public UI owner")
        may_bind = row.get("may_bind_to_placeholder", "").lower()
        if may_bind not in {"true", "false"}:
            errors.append(f"{foundation_id}: may_bind_to_placeholder must be true or false")
        if foundation_type in {"LOADING_SHELL", "EMPTY_SHELL", "ERROR_SHELL"}:
            if may_bind != "false":
                errors.append(f"{foundation_id}: common state shell cannot bind to a Phase 3 business placeholder")
            restricted_public_symbols.add(row.get("symbol", ""))
        try:
            path = safe_relative_path(project, row.get("file_path", ""), f"public UI file for {foundation_id}")
            content = text_file(path, f"public UI {foundation_id}", errors)
            if row.get("symbol") and row.get("symbol") not in content:
                errors.append(f"{foundation_id}: public UI file lacks registered symbol")
        except ValueError as exc:
            errors.append(str(exc))
    if public_types != REQUIRED_PUBLIC_UI_TYPES:
        errors.append(
            f"Public UI registry type coverage differs; missing={sorted(REQUIRED_PUBLIC_UI_TYPES - public_types)}, "
            f"extra={sorted(public_types - REQUIRED_PUBLIC_UI_TYPES)}"
        )
    for shell in shell_files:
        content = text_file(shell, "business placeholder", errors)
        for symbol in restricted_public_symbols:
            if symbol and symbol in content:
                errors.append(f"Phase 3 placeholder mounts common business-state shell {symbol}: {shell}")

    expected_requirements = set(input_lock.get("capability_requirement_ids", []))
    capability_by_requirement: dict[str, dict[str, str]] = {}
    for row in capabilities:
        requirement_id = row.get("capability_requirement_id", "")
        if requirement_id in capability_by_requirement:
            errors.append(f"Duplicate capability requirement mapping: {requirement_id}")
        capability_by_requirement[requirement_id] = row
        if requirement_id not in expected_requirements:
            errors.append(f"Unknown capability requirement: {requirement_id}")
        if row.get("harmony_module_id") not in module_by_id:
            errors.append(f"{requirement_id}: capability contract references missing module")
        if row.get("status") != "CONTRACT_CREATED_PENDING_IMPLEMENTATION":
            errors.append(f"{requirement_id}: capability status is not contract-created/pending implementation")
        if not row.get("capability_contract_id") or not row.get("contract_kind") or not row.get("contract_symbol"):
            errors.append(f"{requirement_id}: capability contract identity is incomplete")
        else:
            try:
                validate_id(row.get("capability_contract_id", ""), "Capability-Contract-ID")
            except ValueError as exc:
                errors.append(f"{requirement_id}: {exc}")
        if row.get("created_by") != ownership.get("capability_contract_agent_id"):
            errors.append(f"{requirement_id}: contract creator differs from capability owner")
        source_keys = split_multi(row.get("source_inventory_row_keys", ""))
        if row.get("source_kind") == "SCOPE_FEATURE":
            if source_keys:
                errors.append(f"{requirement_id}: scope-only capability must not invent inventory rows")
        elif not source_keys or any(key not in inventory_by_key for key in source_keys):
            errors.append(f"{requirement_id}: capability source inventory keys are missing or invalid")
        try:
            contract = safe_relative_path(project, row.get("contract_file", ""), f"contract file for {requirement_id}")
            content = text_file(contract, f"contract {requirement_id}", errors)
            if row.get("contract_symbol") and row.get("contract_symbol") not in content:
                errors.append(f"{requirement_id}: contract file lacks registered symbol")
            if not any(keyword in content for keyword in ("interface ", "type ", "enum ")):
                errors.append(f"{requirement_id}: contract file lacks interface/type/enum declaration")
            for token in contract_forbidden:
                if token and token in content:
                    errors.append(f"{requirement_id}: interface-only contract contains forbidden token {token!r}")
        except ValueError as exc:
            errors.append(str(exc))
    if set(capability_by_requirement) != expected_requirements:
        errors.append(
            f"Capability requirements are not closed; missing={sorted(expected_requirements - set(capability_by_requirement))}, "
            f"extra={sorted(set(capability_by_requirement) - expected_requirements)}"
        )

    expected_status_keys = set(inventory_by_key) | expected_requirements
    status_by_key: dict[str, dict[str, str]] = {}
    for row in statuses:
        source_key = row.get("source_key", "")
        if source_key in status_by_key:
            errors.append(f"Duplicate migration status source key: {source_key}")
        status_by_key[source_key] = row
        if source_key in inventory_by_key:
            if row.get("updated_by") != ownership.get("navigation_agent_id"):
                errors.append(f"{source_key}: inventory status updater differs from navigation owner")
            mapping = architecture_by_key.get(source_key, {})
            mapping_type = mapping.get("mapping_type")
            expected_status = (
                "EXCLUDED_BY_SCOPE" if mapping_type == "EXCLUDED_BY_SCOPE"
                else "SHELL_CREATED_PENDING_IMPLEMENTATION"
            )
            expected_target = (
                mapping.get("route_id") if mapping_type == "ROUTE_PAGE"
                else mapping.get("surface_shell_id") if mapping_type == "VISUAL_SURFACE"
                else ""
            )
        elif source_key in expected_requirements:
            if row.get("updated_by") != ownership.get("capability_contract_agent_id"):
                errors.append(f"{source_key}: capability status updater differs from capability owner")
            mapping = capability_by_requirement.get(source_key, {})
            expected_status = "CONTRACT_CREATED_PENDING_IMPLEMENTATION"
            expected_target = mapping.get("capability_contract_id", "")
        else:
            errors.append(f"Migration status references unknown source key: {source_key}")
            continue
        if row.get("status") != expected_status:
            errors.append(f"{source_key}: migration status {row.get('status')} should be {expected_status}")
        if row.get("target_id") != expected_target:
            errors.append(f"{source_key}: migration target_id does not match the real landing point")
        if row.get("status") in {"NOT_STARTED", "BLOCKED"}:
            errors.append(f"{source_key}: unresolved migration status prevents PASS")
    if set(status_by_key) != expected_status_keys:
        errors.append(
            f"Migration status coverage is incomplete; missing={sorted(expected_status_keys - set(status_by_key))}, "
            f"extra={sorted(set(status_by_key) - expected_status_keys)}"
        )

    for row in decisions:
        if row.get("decided_by") != ownership.get("architecture_lead_id"):
            errors.append(
                f"Architecture decision owner differs from frozen lead: "
                f"{row.get('decision_id', '<unknown>')}"
            )
        if row.get("status", "").upper() not in {"ACTIVE", "SUPERSEDED"}:
            errors.append(f"Architecture decision has invalid status: {row.get('decision_id', '<unknown>')}")

    creators = {
        row.get("created_by") for row in modules + routes + surfaces + public_ui + capabilities if row.get("created_by")
    }
    creators.update(row.get("mapped_by") for row in architecture if row.get("mapped_by"))
    creators.update(row.get("updated_by") for row in statuses if row.get("updated_by"))
    creators.update(row.get("decided_by") for row in decisions if row.get("decided_by"))
    creators.update(
        str(value) for value in (
            environment.get("created_by"), environment.get("frozen_by"),
            phase_manifest.get("architecture_lead"), input_lock.get("locked_by"),
        )
        if value
    )
    if reviewer in creators:
        errors.append("Architecture acceptance agent appears as a creator, mapper, or status updater")

    try:
        controller_rework = [
            row for row in read_csv(workspace.parent / "controller" / "rework-log.csv")
            if row.get("phase") == "3"
        ]
    except ValueError as exc:
        errors.append(str(exc))
        controller_rework = []
    local_ticket_ids = [row.get("ticket_id", "") for row in rework]
    controller_ticket_ids = [row.get("rework_id", "") for row in controller_rework]
    if len(local_ticket_ids) != len(set(local_ticket_ids)):
        errors.append("Phase 3 rework ledger contains duplicate Ticket-ID values")
    if len(controller_ticket_ids) != len(set(controller_ticket_ids)):
        errors.append("Controller Phase 3 rework ledger contains duplicate Ticket-ID values")
    if set(local_ticket_ids) != set(controller_ticket_ids):
        errors.append("Phase 3 rework ledger and controller mirror contain different Ticket-ID sets")

    open_blocking = [row for row in rework if row.get("status", "").upper() != "CLOSED"]
    if open_blocking:
        errors.append(f"Open Phase 3 rework tickets: {len(open_blocking)}")
    for row in rework:
        ticket_id = row.get("ticket_id", "")
        try:
            validate_id(ticket_id, "Rework Ticket-ID")
        except ValueError as exc:
            errors.append(str(exc))
        problem_type = row.get("problem_type", "").upper()
        route = REWORK_ROUTES.get(problem_type)
        if not route:
            errors.append(f"Unsupported Phase 3 rework problem type: {problem_type}")
        else:
            expected_role, owner_key = route
            if (
                row.get("responsible_role") != expected_role
                or row.get("responsible_agent") != ownership.get(owner_key)
            ):
                errors.append(f"Rework ticket differs from frozen routing: {ticket_id}")
        if row.get("severity") not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}:
            errors.append(f"Rework ticket has invalid severity: {ticket_id}")
        if row.get("status", "").upper() not in {"OPEN", "CLOSED"}:
            errors.append(f"Rework ticket has invalid status: {ticket_id}")
        if row.get("opened_by") != ownership.get("architecture_acceptance_agent_id"):
            errors.append(f"Rework ticket opener differs from acceptance owner: {ticket_id}")
        if row.get("confirmed_by") != ownership.get("architecture_lead_id"):
            errors.append(f"Rework ticket confirmer differs from architecture lead: {ticket_id}")
        failed_id = row.get("failed_verification_id", "")
        try:
            failed = load_json(workspace / "verification" / failed_id / "metadata.json")
            if (
                failed.get("status") != "FAIL"
                or failed.get("run_id") != phase_manifest.get("run_id")
                or failed.get("executed_by") != ownership.get("toolchain_agent_id")
            ):
                errors.append(f"Rework ticket is not bound to a failed HVER from this run: {ticket_id}")
        except ValueError as exc:
            errors.append(str(exc))

        controller_matches = [item for item in controller_rework if item.get("rework_id") == ticket_id]
        if len(controller_matches) != 1:
            errors.append(f"Rework ticket is not uniquely mirrored by controller: {ticket_id}")
            controller_row = {}
        else:
            controller_row = controller_matches[0]
            expected_mirror = {
                "created_at": row.get("opened_at", ""),
                "record_id": row.get("source_or_mapping_id", ""),
                "evidence_id": failed_id,
                "gate_rule": problem_type,
                "reason": row.get("notes", ""),
                "assigned_to": row.get("responsible_agent", ""),
            }
            for field, expected in expected_mirror.items():
                if controller_row.get(field, "") != expected:
                    errors.append(f"Controller rework mirror differs on {field}: {ticket_id}")
            if not controller_row.get("completion_condition", ""):
                errors.append(f"Controller rework mirror lacks completion condition: {ticket_id}")
        if row.get("status", "").upper() == "CLOSED":
            if row.get("closed_by") != ownership.get("architecture_acceptance_agent_id"):
                errors.append(f"Closed rework ticket has invalid closer: {ticket_id}")
            correction_id = row.get("correction_verification_id", "")
            if not correction_id or correction_id == row.get("failed_verification_id"):
                errors.append(f"Closed rework ticket lacks a new correction HVER: {ticket_id}")
            else:
                try:
                    correction = load_json(workspace / "verification" / correction_id / "metadata.json")
                    correction_marker = workspace / "verification" / correction_id / "COMMITTED"
                    if (
                        correction.get("status") != "PASS"
                        or correction.get("run_id") != phase_manifest.get("run_id")
                        or correction.get("executed_by") != ownership.get("toolchain_agent_id")
                        or correction.get("created_at", "") < row.get("opened_at", "")
                        or not correction_marker.is_file()
                        or not correction_marker.read_text(encoding="utf-8").strip().startswith(
                            f"{correction_id} PASS "
                        )
                    ):
                        errors.append(f"Closed rework ticket correction HVER is not PASS: {correction_id}")
                except (ValueError, OSError, UnicodeDecodeError) as exc:
                    errors.append(str(exc))
            if controller_row and (
                controller_row.get("status") != "CLOSED"
                or controller_row.get("resolved_at") != row.get("closed_at")
                or controller_row.get("resolution_evidence_id") != correction_id
                or controller_row.get("reviewed_by") != ownership.get("architecture_acceptance_agent_id")
            ):
                errors.append(f"Closed rework ticket controller resolution differs: {ticket_id}")
        elif controller_row and controller_row.get("status") != "REWORK":
            errors.append(f"Open rework ticket controller status differs: {ticket_id}")

    attestations = {
        "real_file_review": args.attest_real_file_review,
        "placeholder_boundaries": args.attest_placeholder_boundaries,
        "contract_only": args.attest_contract_only,
        "dependency_review": args.attest_dependency_review,
        "runtime_smoke": args.attest_runtime_smoke,
        "screenshot_review": args.attest_screenshot_review,
    }
    if args.decision == "PASS":
        missing_attestations = [name for name, value in attestations.items() if not value]
        if missing_attestations:
            errors.append(f"PASS requires acceptance attestations: {missing_attestations}")

    effective_decision = args.decision
    if args.decision == "PASS" and errors:
        effective_decision = "INCOMPLETE"
    gate_id = f"GATE3-{utc_now().replace('-', '').replace(':', '')}-{uuid.uuid4().hex[:6].upper()}"
    report = {
        "gate_id": gate_id,
        "phase": 3,
        "verdict": effective_decision,
        "reviewer_role": "architecture-acceptance-agent",
        "reviewer_id": reviewer,
        "reviewed_at": utc_now(),
        "run_id": phase_manifest.get("run_id"),
        "work_order_id": phase_manifest.get("work_order_id"),
        "work_order_sha256": phase_manifest.get("work_order_sha256"),
        "input_lock_sha256": (
            sha256_file(workspace / "stage-03-input-lock.json")
            if (workspace / "stage-03-input-lock.json").is_file() else None
        ),
        "henv_id": henv_id,
        "verification_id": verification_id,
        "source_snapshot_sha256": snapshot.get("snapshot_sha256"),
        "artifact_hashes": [item.get("sha256") for item in artifact_manifest.get("artifacts", [])],
        "counts": {
            "inventory_rows": len(inventory),
            "architecture_rows": len(architecture),
            "modules": len(modules),
            "routes": len(routes),
            "surfaces": len(surfaces),
            "screenshots": len(screenshot_index),
            "screenshot_required_emulators": len(screenshot_devices),
            "capability_contracts": len(capabilities),
            "assets": len(asset_registry),
            "migration_status_rows": len(statuses),
            "open_rework": len(open_blocking),
        },
        "attestations": attestations,
        "errors": errors,
        "warnings": warnings,
        "notes": args.notes,
    }
    gate_history = workspace / "gate-reports" / f"{gate_id}.json"
    atomic_json(gate_history, report)
    atomic_json(workspace / "stage-03-gate-report.json", report)
    if snapshot:
        atomic_json(workspace / "scaffold-snapshot-manifest.json", snapshot)
    if effective_decision == "PASS" and not errors:
        try:
            closure_value = closure_manifest(workspace)
            atomic_text(workspace / "stage-03-closure-manifest.sha256", closure_value)
            atomic_text(
                workspace / "CLOSED",
                sha256_file(workspace / "stage-03-gate-report.json") + "\n",
            )
            seal_workspace(workspace)
        except (OSError, ValueError) as exc:
            # A closure failure must never be reported as PASS. The mutable report is
            # deliberately excluded from the closure manifest so it can record this failure.
            errors.append(f"Cannot seal Phase 3 workspace: {exc}")
            report["verdict"] = "INCOMPLETE"
            report["errors"] = errors
            try:
                atomic_json(workspace / "stage-03-gate-report.json", report)
            except OSError:
                pass
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if effective_decision == "PASS" and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
