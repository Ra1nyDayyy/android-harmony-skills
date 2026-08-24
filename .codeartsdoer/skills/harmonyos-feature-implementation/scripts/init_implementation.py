#!/usr/bin/env python3
"""Initialize governed Phase 4 from an issued controller work order."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from _common import (
    atomic_json,
    build_project_snapshot,
    csv_fieldnames,
    frozen_category_contracts,
    is_unresolved,
    join_multi,
    load_json,
    make_tree_read_only,
    parse_resolution,
    png_dimensions,
    read_csv,
    safe_relative_path,
    sha256_file,
    split_multi,
    utc_now,
    validate_actor,
    validate_id,
    write_csv,
)
from page_acceptance_contract import compile_page_contracts, publish_page_contracts
from prepare_uitest_probe import prepare_uitest_probe


SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSETS = SKILL_ROOT / "assets"
PHASE_NAME = "phase-04-harmony-implementation"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

STAGE4_ROLE_KEYS = (
    "implementation_lead_id",
    "visual_asset_agent_id",
    "verification_executor_id",
    "parity_acceptance_agent_id",
)
PHASE2_CLOSURE_EXCLUDES = {
    "closure-report.json", "closure-manifest.sha256", "CLOSED",
}
PHASE2_CLOSURE_DIR_EXCLUDES = {".locks", ".staging"}
PHASE3_CLOSURE_EXCLUDES = {
    "stage-03-gate-report.json", "stage-03-closure-manifest.sha256", "CLOSED",
}
PHASE2_ASSET_FIELDS = [
    "asset_id", "source_path", "archive_path", "sha256", "asset_type",
    "feature_ids", "page_ids", "state_ids", "created_by", "created_at",
    "reviewed_by", "reviewed_at", "status", "notes",
]
PHASE3_ASSET_FIELDS = [
    "asset_id", "phase2_archive_path", "asset_sha256", "asset_type",
    "feature_ids", "page_ids", "state_ids", "target_module_id", "target_path",
    "target_symbol", "planned_mode", "decision", "created_by", "status", "notes",
]
ASSET_MODE_DECISIONS = {
    "DIRECT_COPY": "COPY_UNCHANGED",
    "FORMAT_CONVERSION": "CONVERT_FORMAT",
    "RECREATE_FROM_PUBLIC_UI": "RECREATE_LATER",
}
STAGE4_INPUT_RELATIVES = {
    "phase2_closure_sha256": "phase-02-android-inventory/closure-report.json",
    "phase2_closure_manifest_sha256": "phase-02-android-inventory/closure-manifest.sha256",
    "phase2_closed_sha256": "phase-02-android-inventory/CLOSED",
    "phase2_inventory_sha256": "phase-02-android-inventory/inventory.csv",
    "phase2_evidence_index_sha256": "phase-02-android-inventory/evidence-index.csv",
    "phase2_asset_inventory_sha256": "phase-02-android-inventory/asset-inventory.csv",
    "phase2_asset_manifest_sha256": "phase-02-android-inventory/asset-package/manifest.sha256",
    "phase2_asset_committed_sha256": "phase-02-android-inventory/asset-package/COMMITTED",
    "phase2_static_pages_sha256": "phase-02-android-inventory/static-analysis/pages.json",
    "phase2_static_components_sha256": "phase-02-android-inventory/static-analysis/components.json",
    "phase2_static_events_sha256": "phase-02-android-inventory/static-analysis/events.json",
    "phase2_static_transitions_sha256": "phase-02-android-inventory/static-analysis/transitions.json",
    "phase2_runtime_observations_sha256": "phase-02-android-inventory/runtime-observations.json",
    "phase2_page_gate_sha256": "phase-02-android-inventory/page-gate-report.json",
    "phase2_advanced_analysis_sha256": "phase-02-android-inventory/static-analysis/advanced-analysis.json",
    "phase2_advanced_observations_sha256": "phase-02-android-inventory/advanced-observations.json",
    "phase2_advanced_gate_sha256": "phase-02-android-inventory/advanced-gate-report.json",
    "phase2_probe_index_sha256": "phase-02-android-inventory/probe-evidence-index.csv",
    "phase3_input_lock_sha256": "phase-03-harmony-scaffold/stage-03-input-lock.json",
    "phase3_gate_report_sha256": "phase-03-harmony-scaffold/stage-03-gate-report.json",
    "phase3_closure_manifest_sha256": "phase-03-harmony-scaffold/stage-03-closure-manifest.sha256",
    "phase3_closed_sha256": "phase-03-harmony-scaffold/CLOSED",
    "phase3_scaffold_snapshot_sha256": "phase-03-harmony-scaffold/scaffold-snapshot-manifest.json",
    "phase3_architecture_map_sha256": "phase-03-harmony-scaffold/architecture-map.csv",
    "phase3_module_registry_sha256": "phase-03-harmony-scaffold/module-registry.csv",
    "phase3_route_registry_sha256": "phase-03-harmony-scaffold/route-registry.csv",
    "phase3_surface_registry_sha256": "phase-03-harmony-scaffold/surface-registry.csv",
    "phase3_public_ui_registry_sha256": "phase-03-harmony-scaffold/public-ui-registry.csv",
    "phase3_capability_contracts_sha256": "phase-03-harmony-scaffold/capability-contracts.csv",
    "phase3_asset_registry_sha256": "phase-03-harmony-scaffold/asset-registry.csv",
    "phase3_advanced_obligations_sha256": "phase-03-harmony-scaffold/advanced-obligations.json",
    "phase3_henv_registry_sha256": "phase-03-harmony-scaffold/environments/henv-registry.csv",
}
BASE_CATEGORY_MAP = {
    "TOOLCHAIN": "TOOLCHAIN",
    "CLEAN_BUILD": "CLEAN_BUILD",
    "BUNDLE_CHECK": "BUNDLE_CHECK",
    "SIGNING_CHECK": "SIGNING_CHECK",
    "DEVICE_CHECK": "DEVICE",
    "CLEAN_INSTALL": "INSTALL",
    "LAUNCH": "LAUNCH",
    "SCREENSHOT_CAPTURE": "SCREENSHOT_CAPTURE",
}
SERIAL_CATEGORIES = {
    "BUNDLE_CHECK", "DEVICE_CHECK", "CLEAN_INSTALL", "SEED_RESET",
    "NETWORK_PROFILE", "PERMISSION_PROFILE", "LAUNCH", "NAVIGATE",
    "BUSINESS_ASSERT", "SCREENSHOT_CAPTURE", "UITEST_SNAPSHOT_CAPTURE",
}
BUNDLE_CATEGORIES = {
    "BUNDLE_CHECK", "SIGNING_CHECK", "CLEAN_INSTALL", "SEED_RESET", "PERMISSION_PROFILE",
    "LAUNCH", "NAVIGATE", "BUSINESS_ASSERT", "SCREENSHOT_CAPTURE",
    "UITEST_SNAPSHOT_CAPTURE",
}
BUSINESS_PROFILE_FIELDS = (
    "account_id", "account_role", "seed_data_id", "seed_reset_ref",
    "network_profile", "network_conditions_ref", "network_toggle_available",
    "locale", "theme", "font_scale", "timezone", "permissions_profile",
    "orientation",
)
SECRET_FIELD_RE = re.compile(
    r"(?i)^(?:password|passwd|passphrase|private[_-]?key|storepass|keypass|"
    r"api[_-]?token|access[_-]?token|client[_-]?secret|secret)$"
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def deterministic_id(prefix: str, *parts: str) -> str:
    return prefix + "-" + sha256_text("|".join(parts))[:20].upper()


def object_rows(path: Path, field: str, label: str) -> list[dict[str, Any]]:
    value = require_object(load_json(path), label)
    rows = value.get(field)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{label}.{field} must be an object array")
    return rows


def expected_carrier(page: dict[str, Any], mapping_type: str) -> str:
    kinds = {str(value).upper() for value in page.get("kinds", []) if str(value)}
    if any("BOTTOMSHEET" in kind or "BOTTOM_SHEET" in kind for kind in kinds):
        return "SHEET"
    if any("DIALOG" in kind for kind in kinds):
        return "DIALOG"
    if any("POPUP" in kind for kind in kinds):
        return "POPUP"
    if any("WIDGET" in kind for kind in kinds):
        return "EMBEDDED_SURFACE"
    if any("ACTIVITY" in kind for kind in kinds):
        return "PAGE"
    return "PAGE" if mapping_type == "ROUTE_PAGE" else "EMBEDDED_SURFACE"


def actual_scaffold_carrier(mapping_type: str, surface_kind: str) -> str:
    if mapping_type == "ROUTE_PAGE":
        return "PAGE"
    normalized = surface_kind.upper().replace("-", "_")
    if "BOTTOM" in normalized and "SHEET" in normalized:
        return "SHEET"
    if "DIALOG" in normalized:
        return "DIALOG"
    if "POPUP" in normalized or "MENU" in normalized:
        return "POPUP"
    return "EMBEDDED_SURFACE"


def source_row_key(row: dict[str, str]) -> str:
    fields = ("feature_id", "page_id", "state_id", "env_id", "evidence_id")
    values = [str(row.get(field, "")) for field in fields]
    if any(not value for value in values):
        raise ValueError(f"Inventory row lacks a source identity: {row.get('inventory_id', '')}")
    return "SROW-" + sha256_text("|".join(values))[:20].upper()


def canonical_input(value: str, label: str) -> Path:
    raw = Path(value).expanduser().absolute()
    if raw.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link: {raw}")
    try:
        return raw.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Cannot resolve {label}: {exc}") from exc


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def require_fields(row: dict[str, str], fields: tuple[str, ...], label: str) -> None:
    missing = [field for field in fields if is_unresolved(row.get(field, ""))]
    if missing:
        raise ValueError(f"{label} lacks required fields: {missing}")


def indexed(rows: list[dict[str, str]], key: str, label: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        value = str(row.get(key, ""))
        if not value or value in result:
            raise ValueError(f"Missing or duplicate {label} {key}: {value!r}")
        result[value] = row
    return result


def reject_embedded_secrets(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_FIELD_RE.fullmatch(str(key)) and item not in (None, "", False, [], {}):
                raise ValueError(f"Secret-bearing field is prohibited: {path}.{key}")
            reject_embedded_secrets(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_embedded_secrets(item, f"{path}[{index}]")
    elif isinstance(value, str) and (
        "-----BEGIN" in value.upper() or "PRIVATE KEY-----" in value.upper()
    ):
        raise ValueError(f"Embedded private-key material is prohibited: {path}")


def closure_manifest_text(
    root: Path,
    *,
    exact_excludes: set[str],
    directory_excludes: set[str] | None = None,
    exclude_temporary_names: bool = False,
) -> str:
    directory_excludes = directory_excludes or set()
    files: dict[str, Path] = {}
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in a closed phase: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        pure = PurePosixPath(relative)
        if relative in exact_excludes or any(part in directory_excludes for part in pure.parts):
            continue
        if exclude_temporary_names and path.name.endswith((".lock", ".tmp")):
            continue
        files[relative] = path
    return "".join(f"{sha256_file(files[name])}  {name}\n" for name in sorted(files))


def verify_closed_phases(phase2: Path, phase3: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    phase2_report = require_object(load_json(phase2 / "closure-report.json"), "Phase 2 closure")
    phase2_manifest = require_object(load_json(phase2 / "phase-manifest.json"), "Phase 2 manifest")
    actual_phase2 = closure_manifest_text(
        phase2,
        exact_excludes=PHASE2_CLOSURE_EXCLUDES,
        directory_excludes=PHASE2_CLOSURE_DIR_EXCLUDES,
        exclude_temporary_names=True,
    )
    stored_phase2 = (phase2 / "closure-manifest.sha256").read_text(encoding="utf-8")
    if stored_phase2 != actual_phase2:
        raise ValueError("Phase 2 closure manifest no longer exactly matches the workspace")
    if phase2_report.get("closure_manifest_sha256") != sha256_text(stored_phase2):
        raise ValueError("Phase 2 closure report references another manifest")
    if (phase2 / "CLOSED").read_text(encoding="utf-8").strip() != sha256_file(
        phase2 / "closure-report.json"
    ):
        raise ValueError("Phase 2 CLOSED marker does not bind closure-report.json")
    if (
        phase2_report.get("final_verdict") != "PASS"
        or phase2_report.get("evidence_chain_closed") is not True
        or phase2_manifest.get("status") != "CLOSED"
    ):
        raise ValueError("Phase 2 is not an exact closed PASS")

    phase3_report = require_object(
        load_json(phase3 / "stage-03-gate-report.json"), "Phase 3 gate report"
    )
    actual_phase3 = closure_manifest_text(phase3, exact_excludes=PHASE3_CLOSURE_EXCLUDES)
    stored_phase3 = (phase3 / "stage-03-closure-manifest.sha256").read_text(encoding="utf-8")
    if stored_phase3 != actual_phase3:
        raise ValueError("Phase 3 closure manifest no longer exactly matches the workspace")
    if (phase3 / "CLOSED").read_text(encoding="utf-8").strip() != sha256_file(
        phase3 / "stage-03-gate-report.json"
    ):
        raise ValueError("Phase 3 CLOSED marker does not bind stage-03-gate-report.json")
    if phase3_report.get("phase") != 3 or phase3_report.get("verdict") != "PASS" or phase3_report.get("errors"):
        raise ValueError("Phase 3 is not an exact closed PASS")
    return phase2_manifest, phase3_report


def parse_manifest(directory: Path, *, committed_id: str | None = None) -> dict[str, str]:
    manifest = directory / "manifest.sha256"
    committed = directory / "COMMITTED"
    if not manifest.is_file() or not committed.is_file():
        raise ValueError(f"Package is not committed: {directory}")
    entries: dict[str, str] = {}
    lines = manifest.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, start=1):
        if "  " not in line:
            raise ValueError(f"Malformed manifest line {number}: {manifest}")
        digest, relative = line.split("  ", 1)
        pure = PurePosixPath(relative)
        if (
            not SHA256_RE.fullmatch(digest)
            or pure.is_absolute()
            or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
            or relative in entries
            or relative in {"manifest.sha256", "COMMITTED"}
        ):
            raise ValueError(f"Unsafe or duplicate manifest entry: {relative!r}")
        artifact = safe_relative_path(directory, relative, "package artifact")
        if not artifact.is_file() or sha256_file(artifact) != digest:
            raise ValueError(f"Manifest hash differs: {artifact}")
        entries[relative] = digest
    actual: set[str] = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in a package: {path}")
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
    if actual != {*entries, "manifest.sha256", "COMMITTED"}:
        raise ValueError(f"Manifest does not exactly cover package files: {directory}")
    if committed_id is not None and committed.read_text(encoding="utf-8").strip() != committed_id:
        raise ValueError(f"COMMITTED marker does not bind {committed_id}")
    return entries


def directory_snapshot_facts(directory: Path) -> tuple[str, int, int]:
    entries: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in frozen input: {path}")
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(directory).as_posix(),
                    "sha256": sha256_file(path),
                    "size": path.stat().st_size,
                }
            )
    canonical = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return sha256_text(canonical), sum(item["size"] for item in entries), len(entries)


def verify_phase3_snapshot(phase3: Path, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    raw_entries = snapshot.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError("Phase 3 scaffold snapshot has no entries")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise ValueError("Phase 3 scaffold snapshot contains a non-object entry")
        relative = str(raw.get("path", ""))
        if relative in seen:
            raise ValueError(f"Duplicate Phase 3 snapshot path: {relative}")
        seen.add(relative)
        path = safe_relative_path(phase3, relative, "Phase 3 snapshot entry")
        if not path.is_file():
            raise ValueError(f"Phase 3 snapshot entry is not a file: {path}")
        entry = {"path": relative, "sha256": sha256_file(path), "size": path.stat().st_size}
        if entry["sha256"] != raw.get("sha256") or entry["size"] != raw.get("size"):
            raise ValueError(f"Phase 3 snapshot entry changed: {relative}")
        entries.append(entry)
    canonical = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if snapshot.get("entry_count") != len(entries) or snapshot.get("snapshot_sha256") != sha256_text(canonical):
        raise ValueError("Phase 3 scaffold snapshot digest/count differs")
    excluded = set(snapshot.get("excluded_generated_parts", []))
    project = phase3 / "harmony-project"
    actual_project: set[str] = set()
    for path in project.rglob("*"):
        relative = path.relative_to(project)
        if any(part in excluded for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in accepted project: {path}")
        if path.is_file():
            actual_project.add("harmony-project/" + relative.as_posix())
    snapshot_project = {item["path"] for item in entries if item["path"].startswith("harmony-project/")}
    if snapshot_project != actual_project:
        raise ValueError("Phase 3 snapshot does not exactly cover the accepted HarmonyOS project")
    return entries


def validate_asset_chain(
    phase2: Path,
    phase3: Path,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]], dict[str, Path]]:
    phase2_inventory_path = phase2 / "asset-inventory.csv"
    phase3_registry_path = phase3 / "asset-registry.csv"
    if csv_fieldnames(phase2_inventory_path) != PHASE2_ASSET_FIELDS:
        raise ValueError("Phase 2 asset-inventory.csv header differs from the frozen contract")
    if csv_fieldnames(phase3_registry_path) != PHASE3_ASSET_FIELDS:
        raise ValueError("Phase 3 asset-registry.csv header differs from the frozen contract")
    package = phase2 / "asset-package"
    package_entries = parse_manifest(package)
    if (package / "COMMITTED").read_text(encoding="utf-8") != sha256_file(
        package / "manifest.sha256"
    ) + "\n":
        raise ValueError("Phase 2 asset COMMITTED marker differs from its manifest")
    phase2_rows = indexed(read_csv(phase2_inventory_path), "asset_id", "Phase 2 asset")
    archived: dict[str, Path] = {}
    for asset_id, row in phase2_rows.items():
        validate_id(asset_id, "Asset-ID")
        require_fields(
            row,
            ("source_path", "archive_path", "sha256", "asset_type", "status"),
            f"Phase 2 asset {asset_id}",
        )
        source_relative = PurePosixPath(row["source_path"])
        expected_archive = f"asset-package/files/{asset_id}/{source_relative.name}"
        if (
            source_relative.is_absolute()
            or not source_relative.parts
            or any(part in {"", ".", ".."} for part in source_relative.parts)
            or row["archive_path"] != expected_archive
            or row["status"] != "REVIEWED"
            or not row.get("reviewed_by")
            or not row.get("reviewed_at")
        ):
            raise ValueError(f"Phase 2 asset path/lifecycle differs: {asset_id}")
        path = safe_relative_path(phase2, row["archive_path"], f"archived asset {asset_id}")
        package_relative = PurePosixPath(row["archive_path"]).relative_to("asset-package").as_posix()
        if (
            not SHA256_RE.fullmatch(row["sha256"])
            or package_entries.get(package_relative) != row["sha256"]
            or not path.is_file()
            or sha256_file(path) != row["sha256"]
        ):
            raise ValueError(f"Phase 2 asset hash chain differs: {asset_id}")
        archived[asset_id] = path
    if len(package_entries) != len(phase2_rows):
        raise ValueError("Phase 2 asset inventory and package are not one-to-one")

    phase3_rows = indexed(read_csv(phase3_registry_path), "asset_id", "Phase 3 asset")
    if set(phase2_rows) != set(phase3_rows):
        raise ValueError("Phase 3 asset registry does not exactly cover Phase 2 assets")
    for asset_id, placement in phase3_rows.items():
        source = phase2_rows[asset_id]
        require_fields(
            placement,
            (
                "phase2_archive_path", "asset_sha256", "asset_type", "target_module_id",
                "target_path", "target_symbol", "planned_mode", "decision", "status",
            ),
            f"Phase 3 asset {asset_id}",
        )
        mode = placement["planned_mode"]
        if (
            placement["status"] != "READY"
            or mode not in ASSET_MODE_DECISIONS
            or placement["decision"] != ASSET_MODE_DECISIONS[mode]
            or placement["phase2_archive_path"] != source["archive_path"]
            or placement["asset_sha256"] != source["sha256"]
            or placement["asset_type"] != source["asset_type"]
        ):
            raise ValueError(f"Phase 3 asset planning chain differs: {asset_id}")
        for field in ("feature_ids", "page_ids", "state_ids"):
            if sorted(split_multi(placement.get(field, ""))) != sorted(split_multi(source.get(field, ""))):
                raise ValueError(f"Phase 3 asset {field} differs: {asset_id}")
    return phase2_rows, phase3_rows, archived


def validate_android_evidence(
    phase2: Path,
    inventory: list[dict[str, str]],
    evidence_rows: list[dict[str, str]],
) -> dict[str, tuple[dict[str, str], Path]]:
    evidence_index = indexed(evidence_rows, "evidence_id", "Phase 2 evidence")
    referenced: dict[str, tuple[dict[str, str], Path]] = {}
    for row in inventory:
        evidence_id = row["evidence_id"]
        index_row = evidence_index.get(evidence_id)
        if not index_row or index_row.get("status") != "ACCEPTED":
            raise ValueError(f"Active inventory references non-ACCEPTED evidence: {evidence_id}")
        for field in ("inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id"):
            if index_row.get(field) != row.get(field):
                raise ValueError(f"Evidence index {field} differs for {evidence_id}")
        expected_relative = f"evidence/{row['env_id']}/{row['page_id']}/{row['state_id']}/{evidence_id}"
        if index_row.get("relative_path") != expected_relative:
            raise ValueError(f"Evidence path is noncanonical: {evidence_id}")
        evidence_dir = safe_relative_path(phase2, expected_relative, f"Android evidence {evidence_id}")
        if not evidence_dir.is_dir():
            raise ValueError(f"Evidence path is not a directory: {evidence_dir}")
        entries = parse_manifest(evidence_dir, committed_id=evidence_id)
        required = {"metadata.json", "screenshot.png", "layout.json"}
        if not required <= set(entries):
            raise ValueError(f"Android evidence lacks metadata/screenshot/layout: {evidence_id}")
        metadata = require_object(load_json(evidence_dir / "metadata.json"), f"metadata {evidence_id}")
        for field in ("evidence_id", "inventory_id", "feature_id", "page_id", "state_id", "env_id"):
            if str(metadata.get(field, "")) != row.get(field):
                raise ValueError(f"Android evidence metadata {field} differs: {evidence_id}")
        if (
            metadata.get("status") != "SEALED"
            or metadata.get("capture_tool") != "android-cli"
            or sha256_file(evidence_dir / "metadata.json") != index_row.get("metadata_sha256")
        ):
            raise ValueError(f"Android evidence metadata/status differs: {evidence_id}")
        layout = load_json(evidence_dir / "layout.json")
        if layout in ({}, [], None):
            raise ValueError(f"Android evidence layout tree is empty: {evidence_id}")
        png_dimensions(evidence_dir / "screenshot.png")
        referenced[evidence_id] = (index_row, evidence_dir)
    if len(referenced) != len(inventory):
        raise ValueError("Active inventory and Android evidence are not one-to-one")
    for row in evidence_rows:
        if row.get("status") not in {"ACCEPTED", "SUPERSEDED"}:
            raise ValueError(f"Unexpected Phase 2 evidence lifecycle: {row.get('evidence_id')}")
    return referenced


def normalize_contract_source(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        if not all(isinstance(item, dict) for item in value):
            raise ValueError("Asset conversion contract list contains a non-object")
        return list(value)
    if not isinstance(value, dict):
        raise ValueError("Asset conversion config must be an object or array")
    if "contract_id" in value:
        return [value]
    if "contracts" in value:
        contracts = value["contracts"]
        if not isinstance(contracts, list) or not all(isinstance(item, dict) for item in contracts):
            raise ValueError("asset conversion contracts must be an object array")
        return list(contracts)
    rows: list[dict[str, Any]] = []
    for contract_id, raw in value.items():
        if not isinstance(raw, dict):
            raise ValueError(f"Asset conversion contract must be an object: {contract_id}")
        row = dict(raw)
        if row.get("contract_id", contract_id) != contract_id:
            raise ValueError(f"Asset conversion contract key/id differs: {contract_id}")
        row["contract_id"] = contract_id
        rows.append(row)
    return rows


def validate_conversion_contracts(
    config_paths: list[str],
    phase2_assets: dict[str, dict[str, str]],
    phase3_assets: dict[str, dict[str, str]],
    lead: str,
    created_at: str,
) -> dict[str, Any]:
    required_keys = {
        "contract_id", "source_extensions", "target_extensions", "resolved_executable",
        "executable_sha256", "argv_template", "required_argv_tokens",
        "success_output_contains", "error_output_contains",
    }
    raw_contracts: list[dict[str, Any]] = []
    for raw_path in config_paths:
        path = canonical_input(raw_path, "asset conversion config")
        raw_contracts.extend(normalize_contract_source(load_json(path)))
    contracts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_contracts:
        if set(raw) != required_keys:
            raise ValueError(
                f"Asset conversion contract fields differ: missing={sorted(required_keys - set(raw))}, "
                f"extra={sorted(set(raw) - required_keys)}"
            )
        contract_id = validate_id(str(raw["contract_id"]), "conversion Contract-ID")
        if contract_id in seen:
            raise ValueError(f"Duplicate asset conversion Contract-ID: {contract_id}")
        seen.add(contract_id)
        executable_value = str(raw["resolved_executable"])
        executable = Path(executable_value).expanduser()
        if not executable.is_absolute():
            raise ValueError(f"{contract_id}: resolved_executable must be absolute")
        executable = executable.resolve(strict=True)
        if str(executable) != executable_value or not executable.is_file() or not os.access(executable, os.X_OK):
            raise ValueError(f"{contract_id}: executable must be canonical and executable")
        executable_sha = str(raw["executable_sha256"]).lower()
        if not SHA256_RE.fullmatch(executable_sha) or sha256_file(executable) != executable_sha:
            raise ValueError(f"{contract_id}: executable hash differs")
        normalized_arrays: dict[str, list[str]] = {}
        for field in (
            "source_extensions", "target_extensions", "argv_template", "required_argv_tokens",
            "success_output_contains", "error_output_contains",
        ):
            values = raw[field]
            if not isinstance(values, list) or not values or any(not isinstance(item, str) or not item for item in values):
                raise ValueError(f"{contract_id}.{field} must be a nonempty string array")
            normalized_arrays[field] = list(values)
        for field in ("source_extensions", "target_extensions"):
            values = normalized_arrays[field]
            if values != sorted(set(values)) or any(not re.fullmatch(r"\.[a-z0-9]+", item) for item in values):
                raise ValueError(f"{contract_id}.{field} must be sorted unique lowercase extensions")
        argv_template = normalized_arrays["argv_template"]
        if (
            argv_template[0] != executable_value
            or sum(token.count("{SOURCE}") for token in argv_template) != 1
            or sum(token.count("{TARGET}") for token in argv_template) != 1
            or any(
                placeholder not in {"{SOURCE}", "{TARGET}"}
                for token in argv_template
                for placeholder in re.findall(r"\{[^{}]+\}", token)
            )
        ):
            raise ValueError(
                f"{contract_id}: argv_template must bind the executable and contain SOURCE/TARGET exactly once"
            )
        missing_tokens = [token for token in normalized_arrays["required_argv_tokens"] if token not in argv_template]
        if missing_tokens:
            raise ValueError(f"{contract_id}: required argv tokens are absent from argv_template: {missing_tokens}")
        contracts.append(
            {
                "contract_id": contract_id,
                "source_extensions": normalized_arrays["source_extensions"],
                "target_extensions": normalized_arrays["target_extensions"],
                "resolved_executable": executable_value,
                "executable_sha256": executable_sha,
                "argv_template": argv_template,
                "required_argv_tokens": normalized_arrays["required_argv_tokens"],
                "success_output_contains": normalized_arrays["success_output_contains"],
                "error_output_contains": normalized_arrays["error_output_contains"],
            }
        )
    contracts.sort(key=lambda item: item["contract_id"])
    conversion_assets = [
        asset_id for asset_id, row in phase3_assets.items()
        if row.get("planned_mode") == "FORMAT_CONVERSION"
    ]
    used_contracts: set[str] = set()
    for asset_id in conversion_assets:
        source_extension = PurePosixPath(phase2_assets[asset_id]["archive_path"]).suffix.lower()
        target_extension = PurePosixPath(phase3_assets[asset_id]["target_path"]).suffix.lower()
        matches = [
            item for item in contracts
            if source_extension in item["source_extensions"]
            and target_extension in item["target_extensions"]
        ]
        if len(matches) != 1:
            raise ValueError(f"FORMAT_CONVERSION asset {asset_id} requires exactly one compatible frozen contract")
        used_contracts.add(matches[0]["contract_id"])
    if conversion_assets and not config_paths:
        raise ValueError("FORMAT_CONVERSION assets require --asset-conversion-config")
    if not conversion_assets and contracts:
        raise ValueError("Asset conversion contracts are prohibited when no FORMAT_CONVERSION asset exists")
    if used_contracts != {item["contract_id"] for item in contracts}:
        raise ValueError("Every frozen asset conversion contract must be used by a planned conversion")
    return {
        "schema_version": "1.0",
        "created_at": created_at,
        "locked_by": lead,
        "contracts": contracts,
    }


def validate_environment_config(
    config_path: Path,
    scope_envs: dict[str, dict[str, Any]],
    base_henvs: dict[str, tuple[dict[str, Any], str]],
    henv_rows: dict[str, dict[str, str]],
    lead: str,
    frozen_at: str,
) -> dict[str, Any]:
    config = require_object(load_json(config_path), f"H4ENV config {config_path}")
    reject_embedded_secrets(config)
    h4env_id = validate_id(str(config.get("h4env_id", "")), "H4ENV-ID")
    source_env_id = validate_id(str(config.get("source_android_env_id", "")), "source ENV-ID")
    base_henv_id = validate_id(str(config.get("base_henv_id", "")), "base HENV-ID")
    device_id = validate_id(str(config.get("device_id", "")), "HDEVICE-ID")
    if validate_actor(str(config.get("created_by", "")), "H4ENV creator") != lead:
        raise ValueError(f"{h4env_id}: created_by must be the frozen implementation lead")
    if config.get("required") is not True:
        raise ValueError(f"{h4env_id}: only required H4ENV configurations may be initialized")
    source_env = scope_envs.get(source_env_id)
    if not source_env:
        raise ValueError(f"{h4env_id}: unknown Android environment {source_env_id}")
    base_pair = base_henvs.get(base_henv_id)
    if not base_pair:
        raise ValueError(f"{h4env_id}: base HENV is not in the controller work order: {base_henv_id}")
    base, base_sha = base_pair
    henv_row = henv_rows.get(base_henv_id)
    if not henv_row or henv_row.get("status") != "FROZEN" or henv_row.get("environment_sha256") != base_sha:
        raise ValueError(f"{h4env_id}: base HENV registry/hash differs")
    devices = [item for item in base.get("devices", []) if isinstance(item, dict)]
    device = next((item for item in devices if item.get("device_id") == device_id), None)
    if not device:
        raise ValueError(f"{h4env_id}: unknown frozen device {device_id}")
    if (
        str(device.get("device_type", "")).lower() != "emulator"
        or device.get("required") is not True
        or device.get("screenshot_required") is not True
    ):
        raise ValueError(f"{h4env_id}: formal validation requires a required screenshot emulator")
    serial = str(device.get("serial", ""))
    application = base.get("application") if isinstance(base.get("application"), dict) else {}
    bundle_name = str(application.get("bundle_name", ""))
    if (
        not serial or not bundle_name
        or config.get("device_serial") != serial
        or config.get("bundle_name") != bundle_name
    ):
        raise ValueError(f"{h4env_id}: config must bind the exact frozen serial and Bundle")
    selector = config.get("device_selector_tokens")
    if (
        not isinstance(selector, list) or not selector
        or any(not isinstance(token, str) or not token for token in selector)
        or serial not in selector
    ):
        raise ValueError(f"{h4env_id}: device_selector_tokens do not bind the frozen serial")
    contracts = frozen_category_contracts(config)
    if os.environ.get("ANDROID_HARMONY_TEST_FIXTURES") != "1":
        for category, contract in contracts.items():
            parts = {part.lower() for part in Path(contract["resolved_executable"]).parts}
            if "tests" in parts or "fake_harmony.py" in str(contract["resolved_executable"]).lower():
                raise ValueError(
                    f"{h4env_id}: synthetic test executable is prohibited for formal evidence: {category}"
                )
    for category in SERIAL_CATEGORIES:
        if serial not in contracts[category]["required_argv_tokens"]:
            raise ValueError(f"{h4env_id}: {category} does not bind the exact frozen serial")
    for category in BUNDLE_CATEGORIES:
        if bundle_name not in contracts[category]["required_argv_tokens"]:
            raise ValueError(f"{h4env_id}: {category} does not bind the exact frozen Bundle")
    base_contracts = base.get("toolchain", {}).get("category_contracts", {})
    for category, base_category in BASE_CATEGORY_MAP.items():
        base_contract = base_contracts.get(base_category)
        if not isinstance(base_contract, dict):
            raise ValueError(f"{h4env_id}: base HENV lacks category {base_category}")
        if (
            contracts[category]["resolved_executable"] != base_contract.get("resolved_executable")
            or contracts[category]["executable_sha256"] != base_contract.get("executable_sha256")
        ):
            raise ValueError(f"{h4env_id}: {category} executable differs from base HENV {base_category}")
    comparison = config.get("comparison")
    if not isinstance(comparison, dict):
        raise ValueError(f"{h4env_id}: comparison policy is missing")
    width = comparison.get("screenshot_width")
    height = comparison.get("screenshot_height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ValueError(f"{h4env_id}: screenshot dimensions must be positive integers")
    if (width, height) != parse_resolution(str(device.get("resolution", ""))):
        raise ValueError(f"{h4env_id}: screenshot dimensions differ from the frozen emulator")
    bounds = comparison.get("content_bounds")
    if not isinstance(bounds, list) or len(bounds) != 4 or any(not isinstance(item, int) for item in bounds):
        raise ValueError(f"{h4env_id}: content_bounds must be four integers")
    x, y, content_width, content_height = bounds
    if (
        x < 0 or y < 0 or content_width <= 0 or content_height <= 0
        or x + content_width > width or y + content_height > height
    ):
        raise ValueError(f"{h4env_id}: content_bounds escape the screenshot")
    tolerance = comparison.get("geometry_tolerance_px")
    if not isinstance(tolerance, int) or tolerance < 0:
        raise ValueError(f"{h4env_id}: geometry_tolerance_px must be nonnegative")
    business_profile = {field: source_env.get(field) for field in BUSINESS_PROFILE_FIELDS}
    missing_business = [
        field for field, value in business_profile.items()
        if value is None or (isinstance(value, str) and not value.strip())
    ]
    if missing_business or not isinstance(business_profile["network_toggle_available"], bool):
        raise ValueError(f"{h4env_id}: Android business profile is incomplete: {missing_business}")
    if "business_profile" in config and config["business_profile"] != business_profile:
        raise ValueError(f"{h4env_id}: supplied business_profile differs from controller scope")
    return {
        "h4env_id": h4env_id,
        "source_android_env_id": source_env_id,
        "base_henv_id": base_henv_id,
        "device_id": device_id,
        "device_serial": serial,
        "bundle_name": bundle_name,
        "created_by": lead,
        "required": True,
        "frozen_at": frozen_at,
        "device_selector_tokens": selector,
        "category_contracts": contracts,
        "comparison": comparison,
        "business_profile": business_profile,
        "base_henv_sha256": base_sha,
        "base_application": application,
        "base_toolchain": base.get("toolchain"),
        "emulator": device,
    }


def copy_template_csv(target: Path, template_name: str) -> None:
    shutil.copyfile(ASSETS / template_name, target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--work-order", required=True)
    parser.add_argument("--implementation-lead", required=True)
    parser.add_argument("--environment-config", action="append", required=True)
    parser.add_argument("--asset-conversion-config", action="append", default=[])
    args = parser.parse_args()

    try:
        run_dir = canonical_input(args.run_dir, "migration run")
        work_order_path = canonical_input(args.work_order, "Phase 4 work order")
        lead = validate_actor(args.implementation_lead, "implementation lead")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if not run_dir.is_dir():
        parser.error(f"Migration run does not exist: {run_dir}")
    work_orders_root = (run_dir / "controller" / "work-orders").resolve()
    try:
        work_order_path.relative_to(work_orders_root)
    except ValueError:
        parser.error(f"Work order must be controller-owned below: {work_orders_root}")
    if work_order_path.parent != work_orders_root:
        parser.error("Phase 4 work order must be a direct controller/work-orders child")

    controller_dir = run_dir / "controller"
    phase2 = run_dir / "phase-02-android-inventory"
    phase3 = run_dir / "phase-03-harmony-scaffold"
    phase_dir = run_dir / PHASE_NAME
    if phase_dir.exists():
        parser.error(f"Phase 4 workspace already exists; overwrite is prohibited: {phase_dir}")
    try:
        scope_path = safe_relative_path(run_dir, "controller/scope.json", "controller scope")
        current_gate_path = safe_relative_path(run_dir, "controller/gate-report.json", "current controller Gate 3")
        scope = require_object(load_json(scope_path), "controller scope")
        work_order = require_object(load_json(work_order_path), "Phase 4 work order")
        registry_path = controller_dir / "work-order-registry.csv"
        registry_rows = read_csv(registry_path)
        work_order_id = validate_id(str(work_order.get("work_order_id", "")), "Phase 4 Work-Order-ID")
        if work_order_path.name != f"{work_order_id}.json":
            raise ValueError("Phase 4 work-order filename does not match Work-Order-ID")
        active_phase4 = [
            row for row in registry_rows
            if row.get("phase") == "4" and row.get("status", "").upper() != "SUPERSEDED"
        ]
        matches = [
            row for row in registry_rows
            if row.get("phase") == "4" and row.get("work_order_id") == work_order_id
        ]
        if len(active_phase4) != 1 or len(matches) != 1 or active_phase4[0] != matches[0]:
            raise ValueError("Controller must register exactly this one active Phase 4 work order")
        registry_row = matches[0]
        registered_path = safe_relative_path(
            run_dir, registry_row.get("relative_path", ""), "registered Phase 4 work order"
        )
        scope_sha = sha256_file(scope_path)
        work_order_sha = sha256_file(work_order_path)
        controller_actor = scope.get("ownership", {}).get("migration_controller_id")
        if (
            registered_path != work_order_path
            or registry_row.get("status") != "ISSUED"
            or registry_row.get("scope_sha256") != scope_sha
            or registry_row.get("work_order_sha256") != work_order_sha
            or registry_row.get("issued_by") != controller_actor
            or work_order.get("run_id") != scope.get("run_id")
            or work_order.get("phase") != 4
            or work_order.get("status") != "ISSUED"
            or work_order.get("issued_by") != controller_actor
            or work_order.get("scope_relative_path") != "controller/scope.json"
            or work_order.get("scope_sha256") != scope_sha
            or work_order.get("required_skill") != "harmonyos-feature-implementation"
            or work_order.get("business_implementation_allowed") is not True
            or work_order.get("mp4_allowed") is not False
        ):
            raise ValueError("Phase 4 work-order registration, identity, scope, or authority differs")
        ownership = work_order.get("ownership")
        if not isinstance(ownership, dict) or set(ownership) != set(STAGE4_ROLE_KEYS):
            raise ValueError("Phase 4 work order must freeze exactly four governance roles")
        role_values = [validate_actor(str(ownership[key]), key) for key in STAGE4_ROLE_KEYS]
        if len(role_values) != len(set(role_values)):
            raise ValueError("Phase 4 governance actors must be distinct")
        if ownership["implementation_lead_id"] != lead:
            raise ValueError("--implementation-lead differs from the controller work order")
        if work_order.get("included_features") != scope.get("migration_scope", {}).get("included_features"):
            raise ValueError("Phase 4 included feature scope differs from controller scope")
        if work_order.get("excluded_features") != scope.get("migration_scope", {}).get("excluded_features"):
            raise ValueError("Phase 4 excluded feature scope differs from controller scope")

        phase3_relative = str(work_order.get("upstream_phase3_work_order_relative_path", ""))
        phase3_work_order_path = safe_relative_path(run_dir, phase3_relative, "upstream Phase 3 work order")
        phase3_work_order = require_object(load_json(phase3_work_order_path), "Phase 3 work order")
        phase3_registry = [
            row for row in registry_rows
            if row.get("phase") == "3" and row.get("status", "").upper() != "SUPERSEDED"
        ]
        if (
            len(phase3_registry) != 1
            or phase3_registry[0].get("work_order_id") != phase3_work_order.get("work_order_id")
            or phase3_registry[0].get("relative_path") != phase3_relative
            or phase3_registry[0].get("work_order_sha256") != sha256_file(phase3_work_order_path)
            or work_order.get("upstream_phase3_work_order_id") != phase3_work_order.get("work_order_id")
            or work_order.get("upstream_phase3_work_order_sha256") != sha256_file(phase3_work_order_path)
        ):
            raise ValueError("Phase 4 work order is not bound to the one active Phase 3 work order")
        prior_actors: set[str] = set()
        for source_ownership in (scope.get("ownership", {}), phase3_work_order.get("ownership", {})):
            if not isinstance(source_ownership, dict):
                continue
            for value in source_ownership.values():
                if isinstance(value, str) and value:
                    prior_actors.add(value)
                elif isinstance(value, list):
                    prior_actors.update(item for item in value if isinstance(item, str) and item)
        if set(role_values) & prior_actors:
            raise ValueError("Phase 4 governance roles overlap frozen Phase 1-3 actors")

        gate_snapshot_path = safe_relative_path(
            run_dir,
            str(work_order.get("controller_gate3_snapshot_relative_path", "")),
            "controller-owned Gate 3 snapshot",
        )
        if (
            sha256_file(gate_snapshot_path) != work_order.get("controller_gate3_sha256")
            or sha256_file(current_gate_path) != work_order.get("controller_gate3_sha256")
            or current_gate_path.read_bytes() != gate_snapshot_path.read_bytes()
        ):
            raise ValueError("Current/frozen controller Gate 3 differs from the Phase 4 work order")
        frozen_gate = require_object(load_json(gate_snapshot_path), "frozen controller Gate 3")
        if frozen_gate.get("phase") != 3 or frozen_gate.get("verdict") != "PASS" or frozen_gate.get("errors"):
            raise ValueError("Frozen controller Gate 3 is not a complete PASS")

        gate_hash_before = sha256_file(current_gate_path)
        controller_validator = SKILL_ROOT.parent / "android-harmony-migration-controller" / "scripts" / "validate_gate.py"
        recheck = subprocess.run(
            [sys.executable, str(controller_validator), "--run-dir", str(run_dir), "--phase", "3"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=240,
            check=False,
        )
        if recheck.returncode != 0 or sha256_file(current_gate_path) != gate_hash_before:
            detail = recheck.stderr.strip() or recheck.stdout.strip()
            raise ValueError(f"Read-only Gate 3 recheck failed or changed controller state: {detail[:800]}")

        _phase2_manifest, phase3_report = verify_closed_phases(phase2, phase3)
        if phase3_report.get("source_snapshot_sha256") is None:
            raise ValueError("Phase 3 gate does not bind a scaffold snapshot")

        small_sources: list[tuple[str, Path, str]] = [
            ("controller-scope", scope_path, scope_sha),
            ("phase4-work-order", work_order_path, work_order_sha),
            ("controller-gate3-snapshot", gate_snapshot_path, str(work_order["controller_gate3_sha256"])),
            ("phase3-work-order", phase3_work_order_path, str(work_order["upstream_phase3_work_order_sha256"])),
        ]
        locked_sources: dict[str, Path] = {}
        for digest_key, expected_relative in STAGE4_INPUT_RELATIVES.items():
            relative_key = digest_key.removesuffix("_sha256") + "_relative_path"
            if work_order.get(relative_key) != expected_relative:
                raise ValueError(f"Phase 4 work order has noncanonical {relative_key}")
            source = safe_relative_path(run_dir, expected_relative, digest_key)
            digest = str(work_order.get(digest_key, ""))
            if not SHA256_RE.fullmatch(digest) or sha256_file(source) != digest:
                raise ValueError(f"Phase 4 work-order input changed: {digest_key}")
            label = digest_key.removesuffix("_sha256")
            locked_sources[label] = source
            small_sources.append((label, source, digest))

        static_pages = object_rows(locked_sources["phase2_static_pages"], "pages", "Phase 2 pages")
        static_components = object_rows(
            locked_sources["phase2_static_components"], "components", "Phase 2 components"
        )
        static_events = object_rows(locked_sources["phase2_static_events"], "events", "Phase 2 events")
        static_transitions = object_rows(
            locked_sources["phase2_static_transitions"], "transitions", "Phase 2 transitions"
        )
        runtime_observations = object_rows(
            locked_sources["phase2_runtime_observations"], "observations",
            "Phase 2 runtime observations",
        )
        page_gate = require_object(
            load_json(locked_sources["phase2_page_gate"]), "Phase 2 page gate"
        )
        if page_gate.get("machine_verdict") != "PASS" or page_gate.get("errors"):
            raise ValueError("Phase 2 deterministic page gate is not a complete PASS")
        advanced_gate = require_object(
            load_json(locked_sources["phase2_advanced_gate"]), "Phase 2 advanced gate"
        )
        advanced_obligations_value = require_object(
            load_json(locked_sources["phase3_advanced_obligations"]), "Phase 3 advanced obligations"
        )
        advanced_obligations = advanced_obligations_value.get("obligations")
        if (
            advanced_gate.get("machine_verdict") != "PASS"
            or advanced_gate.get("errors")
            or not isinstance(advanced_obligations, list)
            or any(not isinstance(row, dict) for row in advanced_obligations)
        ):
            raise ValueError("Phase 2 advanced analysis or Phase 3 obligation handoff is not a complete PASS")

        henv_records = work_order.get("phase3_henvs")
        if not isinstance(henv_records, list) or not henv_records:
            raise ValueError("Phase 4 work order lacks Phase 3 HENV records")
        base_henvs: dict[str, tuple[dict[str, Any], str]] = {}
        seen_small_sources = {source.resolve() for _, source, _ in small_sources}
        for raw in henv_records:
            if not isinstance(raw, dict):
                raise ValueError("Phase 4 HENV record must be an object")
            henv_id = validate_id(str(raw.get("henv_id", "")), "HENV-ID")
            if henv_id in base_henvs:
                raise ValueError(f"Duplicate Phase 3 HENV: {henv_id}")
            expected_relative = f"phase-03-harmony-scaffold/environments/{henv_id}/harmony-environment.json"
            if raw.get("relative_path") != expected_relative:
                raise ValueError(f"Noncanonical Phase 3 HENV path: {henv_id}")
            path = safe_relative_path(run_dir, expected_relative, f"Phase 3 HENV {henv_id}")
            digest = str(raw.get("sha256", ""))
            if not SHA256_RE.fullmatch(digest) or sha256_file(path) != digest:
                raise ValueError(f"Frozen Phase 3 HENV changed: {henv_id}")
            if path.resolve() in seen_small_sources:
                raise ValueError(f"Duplicate Phase 4 small input source: {path}")
            seen_small_sources.add(path.resolve())
            base_henvs[henv_id] = (require_object(load_json(path), f"HENV {henv_id}"), digest)
            small_sources.append((f"phase3-henv-{henv_id}", path, digest))

        inventory_all = read_csv(phase2 / "inventory.csv")
        inventory: list[dict[str, str]] = []
        for row in inventory_all:
            status = row.get("row_status")
            if status == "SUPERSEDED":
                continue
            if status != "REVIEWED":
                raise ValueError(f"Active Phase 2 inventory row is not REVIEWED: {row.get('inventory_id')}")
            require_fields(
                row,
                ("inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id"),
                "Phase 2 inventory row",
            )
            inventory.append(row)
        inventory_by_id = indexed(inventory, "inventory_id", "active inventory")
        if not inventory:
            raise ValueError("Phase 2 has no active REVIEWED inventory rows")
        included_features = list(work_order.get("included_features", []))
        if (
            not included_features
            or len(included_features) != len(set(included_features))
            or {row["feature_id"] for row in inventory} != set(included_features)
        ):
            raise ValueError("Active Phase 2 inventory does not exactly cover included features")
        evidence_sources = validate_android_evidence(phase2, inventory, read_csv(phase2 / "evidence-index.csv"))
        phase2_assets, phase3_assets, archived_assets = validate_asset_chain(phase2, phase3)
        referenced_assets: set[str] = set()
        for row in inventory:
            asset_ids = split_multi(row.get("asset_ids", ""))
            if "NONE_FOUND" in asset_ids:
                if asset_ids != ["NONE_FOUND"]:
                    raise ValueError(f"NONE_FOUND is mixed with Asset-IDs: {row['inventory_id']}")
                continue
            unknown = set(asset_ids) - set(phase2_assets)
            if unknown:
                raise ValueError(f"Inventory references unknown assets: {sorted(unknown)}")
            referenced_assets.update(asset_ids)
        if referenced_assets != set(phase2_assets):
            raise ValueError("Every frozen Phase 2 asset must be referenced by active inventory")

        phase3_snapshot = require_object(
            load_json(phase3 / "scaffold-snapshot-manifest.json"), "Phase 3 scaffold snapshot"
        )
        snapshot_entries = verify_phase3_snapshot(phase3, phase3_snapshot)
        if phase3_report.get("source_snapshot_sha256") != phase3_snapshot.get("snapshot_sha256"):
            raise ValueError("Phase 3 gate references another scaffold snapshot")

        architecture_rows = read_csv(phase3 / "architecture-map.csv")
        architecture = indexed(architecture_rows, "source_row_key", "Phase 3 architecture row")
        expected_row_keys = {source_row_key(row) for row in inventory}
        if set(architecture) != expected_row_keys:
            raise ValueError("Phase 3 architecture map does not exactly cover active inventory")
        modules = indexed(read_csv(phase3 / "module-registry.csv"), "harmony_module_id", "Harmony module")
        for module_id, row in modules.items():
            if row.get("status") != "READY":
                raise ValueError(f"Harmony module is not READY: {module_id}")
        routes = indexed(read_csv(phase3 / "route-registry.csv"), "route_id", "Harmony route")
        surfaces = indexed(read_csv(phase3 / "surface-registry.csv"), "surface_shell_id", "Harmony surface")
        static_pages_by_id: dict[str, dict[str, Any]] = {}
        for page in static_pages:
            page_id = str(page.get("page_id", ""))
            if not page_id or page_id in static_pages_by_id:
                raise ValueError(f"Phase 2 static page has an empty or duplicate Page-ID: {page_id!r}")
            static_pages_by_id[page_id] = page
        for source in inventory:
            if source["page_id"] not in static_pages_by_id:
                raise ValueError(f"Active inventory page is absent from static analysis: {source['page_id']}")
        for source in inventory:
            mapping = architecture[source_row_key(source)]
            for field in ("inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id"):
                if mapping.get(field) != source.get(field):
                    raise ValueError(f"Architecture mapping {field} differs: {source['inventory_id']}")
            if (
                mapping.get("mapping_status") != "SHELL_CREATED_PENDING_IMPLEMENTATION"
                or mapping.get("harmony_module_id") not in modules
            ):
                raise ValueError(f"Architecture mapping is not implementation-ready: {source['inventory_id']}")
            if mapping.get("mapping_type") == "ROUTE_PAGE":
                target = routes.get(mapping.get("route_id", ""))
                target_id = mapping.get("route_id", "")
            elif mapping.get("mapping_type") == "VISUAL_SURFACE":
                target = surfaces.get(mapping.get("surface_shell_id", ""))
                target_id = mapping.get("surface_shell_id", "")
            else:
                raise ValueError(f"Unsupported in-scope mapping type: {mapping.get('mapping_type')}")
            if (
                not target or target.get("status") != "READY"
                or target.get("harmony_module_id") != mapping.get("harmony_module_id")
                or target.get("page_id") != source.get("page_id") or not target_id
            ):
                raise ValueError(f"Architecture target binding differs: {source['inventory_id']}")
            android_carrier = expected_carrier(static_pages_by_id[source["page_id"]], mapping["mapping_type"])
            scaffold_carrier = actual_scaffold_carrier(
                mapping["mapping_type"], str((target or {}).get("surface_kind", ""))
            )
            if android_carrier != scaffold_carrier:
                raise ValueError(
                    f"Page carrier changed before implementation: {source['page_id']} "
                    f"requires {android_carrier}, scaffold provides {scaffold_carrier}"
                )
        for asset_id, placement in phase3_assets.items():
            if placement["target_module_id"] not in modules:
                raise ValueError(f"Asset references unknown Harmony module: {asset_id}")

        henv_rows = indexed(read_csv(phase3 / "environments" / "henv-registry.csv"), "henv_id", "HENV registry")
        scope_env_values = scope.get("environments")
        if not isinstance(scope_env_values, list) or not scope_env_values:
            raise ValueError("Controller scope has no Android environments")
        scope_envs = {str(item.get("env_id")): item for item in scope_env_values if isinstance(item, dict)}
        initialized_at = utc_now()
        environments: list[dict[str, Any]] = []
        h4env_ids: set[str] = set()
        for raw_path in args.environment_config:
            config_path = canonical_input(raw_path, "H4ENV config")
            normalized = validate_environment_config(
                config_path, scope_envs, base_henvs, henv_rows, lead, initialized_at
            )
            if normalized["h4env_id"] in h4env_ids:
                raise ValueError(f"Duplicate H4ENV-ID: {normalized['h4env_id']}")
            h4env_ids.add(normalized["h4env_id"])
            environments.append(normalized)
        required_source_envs = {row["env_id"] for row in inventory}
        mapped_source_envs = {item["source_android_env_id"] for item in environments}
        if required_source_envs != mapped_source_envs:
            raise ValueError(
                f"H4ENV mapping must exactly cover active Android environments; "
                f"missing={sorted(required_source_envs - mapped_source_envs)}, "
                f"extra={sorted(mapped_source_envs - required_source_envs)}"
            )
        conversion_registry = validate_conversion_contracts(
            args.asset_conversion_config, phase2_assets, phase3_assets, lead, initialized_at
        )
        components_by_page: dict[str, list[str]] = {}
        events_by_page: dict[str, list[str]] = {}
        transitions_by_page: dict[str, list[str]] = {}
        seen_static_ids: set[str] = set()
        for rows, id_field, page_field, output in (
            (static_components, "component_id", "page_id", components_by_page),
            (static_events, "event_id", "page_id", events_by_page),
            (static_transitions, "transition_id", "source_page_id", transitions_by_page),
        ):
            for row in rows:
                subject_id = str(row.get(id_field, ""))
                page_id = str(row.get(page_field, ""))
                if not subject_id or subject_id in seen_static_ids:
                    raise ValueError(f"Phase 2 static analysis has an empty or duplicate subject ID: {subject_id!r}")
                seen_static_ids.add(subject_id)
                if page_id in static_pages_by_id:
                    output.setdefault(page_id, []).append(subject_id)
        inventory_by_evidence = {row["evidence_id"]: row for row in inventory}
        observed_by_state: dict[tuple[str, str, str], dict[str, set[str]]] = {}
        subject_type_to_bucket = {
            "COMPONENT": "components", "EVENT": "events", "TRANSITION": "transitions",
        }
        for observation in runtime_observations:
            bucket = subject_type_to_bucket.get(str(observation.get("subject_type", "")))
            if not bucket:
                continue
            source = inventory_by_evidence.get(str(observation.get("after_evidence_id", "")))
            if source is None:
                raise ValueError(
                    f"Runtime observation is not bound to an active state: "
                    f"{observation.get('observation_id', '')}"
                )
            if (
                observation.get("page_id") != source["page_id"]
                or observation.get("env_id") != source["env_id"]
            ):
                raise ValueError("Runtime observation page/environment differs from its state evidence")
            key = (source["page_id"], source["state_id"], source["env_id"])
            observed_by_state.setdefault(
                key, {"components": set(), "events": set(), "transitions": set()}
            )[bucket].add(str(observation.get("subject_id", "")))
        obligations_by_feature: dict[str, list[dict[str, Any]]] = {}
        seen_obligation_ids: set[str] = set()
        for obligation in advanced_obligations:
            subject_id = str(obligation.get("subject_id", ""))
            if (
                not subject_id
                or subject_id in seen_obligation_ids
                or obligation.get("status") != "LOCKED_FOR_IMPLEMENTATION"
            ):
                raise ValueError(f"Phase 3 has an invalid advanced obligation: {subject_id!r}")
            seen_obligation_ids.add(subject_id)
            feature_ids = obligation.get("candidate_feature_ids")
            if not isinstance(feature_ids, list) or not feature_ids:
                raise ValueError(f"Advanced obligation has no Feature-ID: {subject_id}")
            for feature_id in feature_ids:
                if str(feature_id) in included_features:
                    obligations_by_feature.setdefault(str(feature_id), []).append(obligation)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        parser.error(str(exc))

    try:
        with tempfile.TemporaryDirectory(prefix=f".{PHASE_NAME}-", dir=run_dir) as temp_name:
            temp_dir = Path(temp_name)
            for name in (
                "inputs/upstream", "inputs/android-evidence", "inputs/phase2-assets/files",
                "environments", "feature-work-orders", "reviews", "asset-conversions",
                "builds", "evidence", "attempts", ".locks", ".staging", "harmony-project",
            ):
                (temp_dir / name).mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ASSETS / "attempt-ledger.template.csv", temp_dir / "attempt-ledger.csv")

            input_records: list[dict[str, Any]] = []
            for number, (label, source, digest) in enumerate(small_sources, start=1):
                suffix = source.suffix or ".bin"
                snapshot_relative = f"inputs/upstream/{number:02d}-{label}{suffix}"
                snapshot_temp = temp_dir / snapshot_relative
                shutil.copyfile(source, snapshot_temp)
                if sha256_file(snapshot_temp) != digest or snapshot_temp.stat().st_size != source.stat().st_size:
                    raise ValueError(f"Small input copy changed: {label}")
                input_records.append(
                    {
                        "label": label,
                        "source_path": str(source.resolve()),
                        "snapshot_path": str((phase_dir / snapshot_relative).resolve()),
                        "sha256": digest,
                        "size": source.stat().st_size,
                    }
                )

            android_records: list[dict[str, Any]] = []
            for evidence_id in sorted(evidence_sources):
                index_row, source = evidence_sources[evidence_id]
                snapshot_relative = f"inputs/android-evidence/{evidence_id}"
                snapshot_temp = temp_dir / snapshot_relative
                shutil.copytree(source, snapshot_temp)
                parse_manifest(snapshot_temp, committed_id=evidence_id)
                source_facts = directory_snapshot_facts(source)
                snapshot_facts = directory_snapshot_facts(snapshot_temp)
                if source_facts != snapshot_facts:
                    raise ValueError(f"Android evidence changed while copying: {evidence_id}")
                make_tree_read_only(snapshot_temp)
                android_records.append(
                    {
                        "evidence_id": evidence_id,
                        "inventory_id": index_row["inventory_id"],
                        "source_path": str(source.resolve()),
                        "snapshot_path": str((phase_dir / snapshot_relative).resolve()),
                        "manifest_sha256": sha256_file(snapshot_temp / "manifest.sha256"),
                        "metadata_sha256": sha256_file(snapshot_temp / "metadata.json"),
                        "screenshot_sha256": sha256_file(snapshot_temp / "screenshot.png"),
                        "layout_sha256": sha256_file(snapshot_temp / "layout.json"),
                        "sha256": snapshot_facts[0],
                        "size": snapshot_facts[1],
                        "file_count": snapshot_facts[2],
                    }
                )

            asset_file_records: list[dict[str, Any]] = []
            frozen_asset_paths: dict[str, Path] = {}
            for asset_id in sorted(phase2_assets):
                source = archived_assets[asset_id]
                snapshot_relative = f"inputs/phase2-assets/files/{asset_id}/{source.name}"
                snapshot_temp = temp_dir / snapshot_relative
                snapshot_temp.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, snapshot_temp)
                digest = phase2_assets[asset_id]["sha256"]
                if sha256_file(snapshot_temp) != digest or snapshot_temp.stat().st_size != source.stat().st_size:
                    raise ValueError(f"Frozen asset copy differs: {asset_id}")
                frozen_asset_paths[asset_id] = snapshot_temp
                asset_file_records.append(
                    {
                        "asset_id": asset_id,
                        "source_path": str(source.resolve()),
                        "snapshot_path": str((phase_dir / snapshot_relative).resolve()),
                        "sha256": digest,
                        "size": source.stat().st_size,
                    }
                )

            for entry in snapshot_entries:
                relative = entry["path"]
                if not relative.startswith("harmony-project/"):
                    continue
                project_relative = PurePosixPath(relative).relative_to("harmony-project")
                source = safe_relative_path(phase3, relative, "accepted HarmonyOS project file")
                target = temp_dir / "harmony-project" / Path(*project_relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                if sha256_file(target) != entry["sha256"] or target.stat().st_size != entry["size"]:
                    raise ValueError(f"Accepted HarmonyOS project copy differs: {relative}")

            h4env_rows: list[dict[str, Any]] = []
            h4env_lock_records: list[dict[str, Any]] = []
            for config in sorted(environments, key=lambda item: item["h4env_id"]):
                relative = f"environments/{config['h4env_id']}/phase4-environment.json"
                path = temp_dir / relative
                path.parent.mkdir(parents=True)
                atomic_json(path, config)
                digest = sha256_file(path)
                h4env_rows.append(
                    {
                        "h4env_id": config["h4env_id"],
                        "source_android_env_id": config["source_android_env_id"],
                        "base_henv_id": config["base_henv_id"],
                        "device_id": config["device_id"],
                        "environment_sha256": digest,
                        "frozen_by": lead,
                        "frozen_at": config["frozen_at"],
                        "required": "true",
                        "status": "FROZEN",
                    }
                )
                h4env_lock_records.append(
                    {
                        "h4env_id": config["h4env_id"],
                        "source_android_env_id": config["source_android_env_id"],
                        "base_henv_id": config["base_henv_id"],
                        "device_id": config["device_id"],
                        "relative_path": relative,
                        "sha256": digest,
                    }
                )
            write_csv(
                temp_dir / "environments" / "h4env-registry.csv",
                csv_fieldnames(ASSETS / "h4env-registry.template.csv"),
                h4env_rows,
            )

            asset_rows: list[dict[str, Any]] = []
            for asset_id in sorted(phase2_assets):
                source = phase2_assets[asset_id]
                placement = phase3_assets[asset_id]
                target = safe_relative_path(
                    temp_dir / "harmony-project",
                    placement["target_path"],
                    f"HarmonyOS asset target {asset_id}",
                    must_exist=False,
                )
                mode = placement["planned_mode"]
                target_sha = ""
                status = "PLANNED"
                migrated_by = ""
                if mode == "DIRECT_COPY":
                    if target.exists() and (not target.is_file() or sha256_file(target) != source["sha256"]):
                        raise ValueError(f"DIRECT_COPY target already contains different bytes: {asset_id}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.copyfile(frozen_asset_paths[asset_id], target)
                    target_sha = sha256_file(target)
                    if target_sha != source["sha256"]:
                        raise ValueError(f"DIRECT_COPY target bytes differ: {asset_id}")
                    status = "DIRECT_COPY_VERIFIED"
                    migrated_by = ownership["visual_asset_agent_id"]
                asset_rows.append(
                    {
                        "asset_id": asset_id,
                        "source_path": source["source_path"],
                        "archive_relative_path": f"inputs/phase2-assets/files/{asset_id}/{archived_assets[asset_id].name}",
                        "source_sha256": source["sha256"],
                        "file_type": source["asset_type"],
                        "feature_ids": join_multi(split_multi(source.get("feature_ids", ""))),
                        "page_ids": join_multi(split_multi(source.get("page_ids", ""))),
                        "state_ids": join_multi(split_multi(source.get("state_ids", ""))),
                        "target_module_id": placement["target_module_id"],
                        "target_resource_path": placement["target_path"],
                        "target_resource_symbol": placement["target_symbol"],
                        "migration_mode": mode,
                        "target_sha256": target_sha,
                        "conversion_record_id": "",
                        "conversion_record_sha256": "",
                        "verification_evidence_id": "",
                        "nativeization_decision_id": "",
                        "migrated_by": migrated_by,
                        "status": status,
                        "notes": "",
                    }
                )

            environments_by_source: dict[str, list[dict[str, Any]]] = {}
            for config in environments:
                environments_by_source.setdefault(config["source_android_env_id"], []).append(config)
            parity_rows: list[dict[str, Any]] = []
            visual_rows: list[dict[str, Any]] = []
            migration_units: list[dict[str, Any]] = []
            for source in sorted(inventory, key=lambda item: item["inventory_id"]):
                row_key = source_row_key(source)
                mapping = architecture[row_key]
                mapping_type = mapping["mapping_type"]
                target_id = mapping["route_id"] if mapping_type == "ROUTE_PAGE" else mapping["surface_shell_id"]
                android_width, android_height = parse_resolution(str(scope_envs[source["env_id"]].get("resolution", "")))
                source_asset_ids = [item for item in split_multi(source.get("asset_ids", "")) if item != "NONE_FOUND"]
                for config in sorted(environments_by_source[source["env_id"]], key=lambda item: item["h4env_id"]):
                    parity_id = deterministic_id("PAR", source["inventory_id"], config["h4env_id"])
                    visual_id = deterministic_id("VEL", parity_id, "PAGE_ROOT")
                    parity_rows.append(
                        {
                            "parity_id": parity_id,
                            "inventory_id": source["inventory_id"],
                            "feature_id": source["feature_id"],
                            "page_id": source["page_id"],
                            "state_id": source["state_id"],
                            "source_env_id": source["env_id"],
                            "android_evidence_id": source["evidence_id"],
                            "h4env_id": config["h4env_id"],
                            "source_row_key": row_key,
                            "harmony_module_id": mapping["harmony_module_id"],
                            "target_kind": mapping_type,
                            "target_id": target_id,
                            "harmony_source_refs": "[]",
                            "visual_element_ids": join_multi([visual_id]),
                            "asset_ids": join_multi(source_asset_ids),
                            "nativeization_decision_ids": "[]",
                            "harmony_evidence_id": "",
                            "implemented_by": "",
                            "status": "NOT_STARTED",
                            "notes": "",
                        }
                    )
                    page = static_pages_by_id[source["page_id"]]
                    surface_kind = str(surfaces.get(mapping.get("surface_shell_id", ""), {}).get("surface_kind", ""))
                    carrier = expected_carrier(page, mapping_type)
                    state_subjects = observed_by_state.get(
                        (source["page_id"], source["state_id"], source["env_id"]),
                        {"components": set(), "events": set(), "transitions": set()},
                    )
                    applicable_obligations = sorted(
                        {
                            str(item["subject_id"]): item
                            for item in obligations_by_feature.get(source["feature_id"], [])
                            if not str(item.get("page_id", ""))
                            or str(item.get("page_id")) == source["page_id"]
                        }.values(),
                        key=lambda item: str(item["subject_id"]),
                    )
                    migration_units.append(
                        {
                            "migration_unit_id": deterministic_id("MUNIT", parity_id),
                            "parity_id": parity_id,
                            "inventory_id": source["inventory_id"],
                            "feature_id": source["feature_id"],
                            "page_id": source["page_id"],
                            "state_id": source["state_id"],
                            "h4env_id": config["h4env_id"],
                            "android_entry_condition": source["entry_condition"],
                            "android_action_summary": source["action_summary"],
                            "android_expected_observable": source["expected_observable"],
                            "required_business_rule_ids": sorted(set(split_multi(source["business_rule_refs"]))),
                            "required_data_dependency_ids": sorted(set(split_multi(source["data_dependency_refs"]))),
                            "required_system_capability_ids": sorted(set(split_multi(source["system_capability_refs"]))),
                            "required_third_party_dependency_ids": sorted(
                                set(split_multi(source["third_party_dependency_refs"]))
                            ),
                            "expected_carrier": carrier,
                            "target_kind": mapping_type,
                            "target_id": target_id,
                            "scaffold_carrier": actual_scaffold_carrier(mapping_type, surface_kind),
                            "page_component_ids": sorted(set(components_by_page.get(source["page_id"], []))),
                            "page_event_ids": sorted(set(events_by_page.get(source["page_id"], []))),
                            "page_transition_ids": sorted(set(transitions_by_page.get(source["page_id"], []))),
                            "required_component_ids": sorted(state_subjects["components"]),
                            "component_locators": {
                                str(item["component_id"]): {
                                    "resource_id": str(item.get("resource_id", "")),
                                    "text": str(item.get("text", "")),
                                    "type": str(item.get("type", "")),
                                }
                                for item in static_components
                                if str(item.get("page_id", "")) == source["page_id"]
                            },
                            "required_event_ids": sorted(state_subjects["events"]),
                            "required_transition_ids": sorted(state_subjects["transitions"]),
                            "state_binding_basis": "PHASE2_AFTER_EVIDENCE",
                            "required_obligation_ids": [str(item["subject_id"]) for item in applicable_obligations],
                            "required_obligation_types": {
                                str(item["subject_id"]): str(item.get("subject_type", ""))
                                for item in applicable_obligations
                            },
                            "simplification_policy": "FORBIDDEN",
                            "native_optimization_policy": "INTERNAL_ONLY_UNLESS_APPROVED",
                            "max_automatic_repair_attempts": 2,
                        }
                    )
                    visual_rows.append(
                        {
                            "visual_element_id": visual_id,
                            "parity_id": parity_id,
                            "element_kind": "PAGE_ROOT",
                            "android_evidence_id": source["evidence_id"],
                            "asset_id": "",
                            "android_geometry": json.dumps(
                                {"x": 0, "y": 0, "width": android_width, "height": android_height},
                                separators=(",", ":"),
                            ),
                            "harmony_geometry": json.dumps(
                                dict(zip(("x", "y", "width", "height"), config["comparison"]["content_bounds"])),
                                separators=(",", ":"),
                            ),
                            "android_visual_spec": "{}",
                            "harmony_visual_spec": "{}",
                            "harmony_file": "",
                            "harmony_symbol": "",
                            "nativeization_decision_id": "",
                            "implemented_by": "",
                            "status": "NOT_STARTED",
                            "notes": "",
                        }
                    )

            feature_rows: list[dict[str, Any]] = []
            for feature_id in sorted(included_features):
                feature_inventory = [row for row in inventory if row["feature_id"] == feature_id]
                feature_rows.append(
                    {
                        "feature_id": feature_id,
                        "work_order_id": "",
                        "feature_owner_id": "",
                        "ui_agent_id": "",
                        "business_data_agent_id": "",
                        "native_capability_agent_id": "",
                        "asset_agent_id": ownership["visual_asset_agent_id"],
                        "source_inventory_ids": join_multi(row["inventory_id"] for row in feature_inventory),
                        "harmony_module_ids": join_multi(
                            architecture[source_row_key(row)]["harmony_module_id"] for row in feature_inventory
                        ),
                        "status": "NOT_STARTED",
                        "updated_by": lead,
                        "updated_at": initialized_at,
                        "notes": "",
                    }
                )

            capability_rows: list[dict[str, Any]] = []
            for row in read_csv(phase3 / "capability-contracts.csv"):
                require_fields(
                    row,
                    (
                        "capability_requirement_id", "capability_contract_id", "source_feature_id",
                        "harmony_module_id", "contract_file", "contract_symbol", "status",
                    ),
                    "Phase 3 capability contract",
                )
                if (
                    row["status"] != "READY"
                    or row["source_feature_id"] not in set(included_features)
                    or row["harmony_module_id"] not in modules
                ):
                    raise ValueError(f"Phase 3 capability contract is not implementation-ready: {row['capability_requirement_id']}")
                capability_rows.append(
                    {
                        "capability_requirement_id": row["capability_requirement_id"],
                        "capability_contract_id": row["capability_contract_id"],
                        "feature_id": row["source_feature_id"],
                        "harmony_module_id": row["harmony_module_id"],
                        "contract_file": row["contract_file"],
                        "contract_symbol": row["contract_symbol"],
                        "implementation_file": "",
                        "implementation_symbol": "",
                        "implemented_by": "",
                        "status": "NOT_STARTED",
                        "verification_evidence_ids": "[]",
                        "notes": "",
                    }
                )

            write_csv(
                temp_dir / "implementation-ledger.csv",
                csv_fieldnames(ASSETS / "implementation-ledger.template.csv"),
                feature_rows,
            )
            write_csv(
                temp_dir / "feature-work-order-registry.csv",
                csv_fieldnames(ASSETS / "feature-work-order-registry.template.csv"),
                [],
            )
            write_csv(
                temp_dir / "page-work-order-registry.csv",
                csv_fieldnames(ASSETS / "page-work-order-registry.template.csv"),
                [],
            )
            write_csv(
                temp_dir / "capability-work-order-registry.csv",
                csv_fieldnames(ASSETS / "capability-work-order-registry.template.csv"),
                [],
            )
            write_csv(temp_dir / "parity-map.csv", csv_fieldnames(ASSETS / "parity-map.template.csv"), parity_rows)
            write_csv(
                temp_dir / "visual-elements.csv",
                csv_fieldnames(ASSETS / "visual-elements.template.csv"),
                visual_rows,
            )
            write_csv(
                temp_dir / "asset-migration.csv",
                csv_fieldnames(ASSETS / "asset-migration.template.csv"),
                asset_rows,
            )
            write_csv(
                temp_dir / "capability-implementation.csv",
                csv_fieldnames(ASSETS / "capability-implementation.template.csv"),
                capability_rows,
            )
            for template, target in (
                ("nativeization-decisions.template.csv", "nativeization-decisions.csv"),
                ("evidence-index.template.csv", "evidence-index.csv"),
                ("rework-tickets.template.csv", "rework-tickets.csv"),
                ("acceptance-ledger.template.csv", "acceptance-ledger.csv"),
            ):
                copy_template_csv(temp_dir / target, template)
            shutil.copyfile(ASSETS / "asset-policy.template.json", temp_dir / "asset-policy.json")
            atomic_json(temp_dir / "asset-conversion-contracts.json", conversion_registry)
            atomic_json(
                temp_dir / "migration-unit-contracts.json",
                {"schema_version": 1, "units": migration_units},
            )
            page_contracts = compile_page_contracts(phase2, phase3, tuple(sorted(h4env_ids)))
            page_contract_registry = publish_page_contracts(page_contracts, temp_dir)
            if [str(row["page_id"]) for row in page_contract_registry] != sorted(
                {row["page_id"] for row in inventory}
            ):
                raise ValueError("Published page contracts do not exactly cover active Phase 2 pages")
            page_contract_by_id = {str(contract["page_id"]): contract for contract in page_contracts}
            write_csv(
                temp_dir / "page-implementation-ledger.csv",
                csv_fieldnames(ASSETS / "page-implementation-ledger.template.csv"),
                [
                    {
                        "page_id": row["page_id"],
                        "work_order_id": "",
                        "owner_id": "",
                        "ui_understanding_agent_id": "",
                        "codearts_task_id": "",
                        "contract_sha256": row["contract_sha256"],
                        "state_ids": join_multi(
                            str(state["state_id"])
                            for state in page_contract_by_id[str(row["page_id"])]["states"]
                        ),
                        "exclusive_code_paths": "[]",
                        "status": "NOT_STARTED",
                        "updated_at": "",
                    }
                    for row in page_contract_registry
                ],
            )
            page_contract_lock_records = [
                {
                    "page_id": row["page_id"],
                    "relative_path": row["relative_path"],
                    "sha256": row["contract_sha256"],
                }
                for row in page_contract_registry
            ]

            probe_generation = prepare_uitest_probe(temp_dir)
            probe_manifest = temp_dir / "ui-test-snapshot-generation-manifest.json"
            if Path(probe_generation["manifest"]) != probe_manifest:
                raise ValueError("UiTest snapshot generation returned a non-canonical manifest path")

            make_tree_read_only(temp_dir / "inputs")
            make_tree_read_only(temp_dir / "environments")
            if (temp_dir / "tools").is_dir():
                make_tree_read_only(temp_dir / "tools")
            make_tree_read_only(temp_dir / "page-contracts")
            make_tree_read_only(temp_dir / "arkts-page-plans")
            (temp_dir / "page-contract-registry.csv").chmod(0o444)
            input_lock = {
                "schema_version": "1.0",
                "stage": 4,
                "run_id": scope.get("run_id"),
                "created_at": initialized_at,
                "locked_by": lead,
                "work_order_id": work_order_id,
                "work_order_sha256": work_order_sha,
                "ownership": ownership,
                "controller_gate3_snapshot_sha256": work_order["controller_gate3_sha256"],
                "phase3_work_order_id": phase3_work_order["work_order_id"],
                "phase3_work_order_sha256": sha256_file(phase3_work_order_path),
                "inputs": input_records,
                "android_evidence": android_records,
                "phase2_asset_files": asset_file_records,
                "h4envs": h4env_lock_records,
                "phase2_inventory_ids": sorted(inventory_by_id),
                "phase2_asset_ids": sorted(phase2_assets),
                "required_h4env_ids": sorted(h4env_ids),
                "phase3_source_snapshot_sha256": phase3_snapshot["snapshot_sha256"],
                "asset_conversion_contracts_sha256": sha256_file(temp_dir / "asset-conversion-contracts.json"),
                "migration_unit_contracts_sha256": sha256_file(temp_dir / "migration-unit-contracts.json"),
                "page_contract_registry": {
                    "relative_path": "page-contract-registry.csv",
                    "sha256": sha256_file(temp_dir / "page-contract-registry.csv"),
                    "schema_sha256": sha256_file(ASSETS / "page-acceptance-contract.schema.json"),
                },
                "page_contracts": page_contract_lock_records,
                "ui_test_snapshot_generation": {
                    "relative_path": "ui-test-snapshot-generation-manifest.json",
                    "sha256": sha256_file(probe_manifest),
                    "generation_id": probe_generation["generation_id"],
                    "page_ids": probe_generation["page_ids"],
                    "probe_count": probe_generation["probe_count"],
                    "contract": "ui-test-snapshot-generation-v1",
                    "production_packaging": "FORBIDDEN",
                },
            }
            atomic_json(temp_dir / "stage-04-input-lock.json", input_lock)
            initial_snapshot = build_project_snapshot(temp_dir / "harmony-project")
            atomic_json(temp_dir / "initial-project-snapshot.json", initial_snapshot)
            atomic_json(
                temp_dir / "phase-manifest.json",
                {
                    "schema_version": "1.0",
                    "run_id": scope.get("run_id"),
                    "project_id": scope.get("project_id"),
                    "phase": 4,
                    "status": "IN_PROGRESS",
                    "initialized_at": initialized_at,
                    "work_order_id": work_order_id,
                    "work_order_sha256": work_order_sha,
                    "work_order_relative_path": registry_row["relative_path"],
                    "ownership": ownership,
                    "roles": {
                        "implementation_lead": ownership["implementation_lead_id"],
                        "asset_agent": ownership["visual_asset_agent_id"],
                        "verification_executor": ownership["verification_executor_id"],
                        "parity_checker": ownership["parity_acceptance_agent_id"],
                    },
                    "input_lock_sha256": sha256_file(temp_dir / "stage-04-input-lock.json"),
                    "initial_project_snapshot_sha256": initial_snapshot["snapshot_sha256"],
                    "asset_conversion_contracts_sha256": sha256_file(temp_dir / "asset-conversion-contracts.json"),
                    "migration_unit_contracts_sha256": sha256_file(temp_dir / "migration-unit-contracts.json"),
                    "page_contract_registry_sha256": sha256_file(temp_dir / "page-contract-registry.csv"),
                    "formal_evidence_device_type": "emulator",
                    "mp4_allowed": False,
                    "source_first_assets_required": True,
                },
            )
            for frozen_record in (
                temp_dir / "stage-04-input-lock.json",
                temp_dir / "phase-manifest.json",
                temp_dir / "initial-project-snapshot.json",
                temp_dir / "asset-conversion-contracts.json",
                temp_dir / "migration-unit-contracts.json",
                temp_dir / "page-contract-registry.csv",
                temp_dir / "ui-test-snapshot-generation-manifest.json",
            ):
                frozen_record.chmod(0o444)
            temp_dir.rename(phase_dir)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "workspace": str(phase_dir),
                "work_order_id": work_order_id,
                "h4env_ids": sorted(h4env_ids),
                "inventory_rows": len(inventory),
                "android_evidence_packages": len(android_records),
                "assets": len(asset_rows),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
