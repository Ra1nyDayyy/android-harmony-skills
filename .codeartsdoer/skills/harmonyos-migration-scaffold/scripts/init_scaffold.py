#!/usr/bin/env python3
"""Initialize Phase 3 from a controller work order and a closed Phase 2."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import re
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from _common import (
    atomic_json,
    csv_fieldnames,
    join_multi,
    load_json,
    read_csv,
    sha256_file,
    sha256_text,
    safe_relative_path,
    source_row_key,
    utc_now,
    validate_id,
    verify_closure_manifest,
    write_csv,
)


SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSETS = SKILL_ROOT / "assets"
CONTROLLER_VALIDATE = (
    SKILL_ROOT.parent / "android-harmony-migration-controller" / "scripts" / "validate_gate.py"
)
PHASE_NAME = "phase-03-harmony-scaffold"
PHASE2_CLOSURE_EXCLUDES = frozenset({"closure-report.json", "closure-manifest.sha256", "CLOSED"})
PHASE2_CLOSURE_DIR_EXCLUDES = frozenset({".locks", ".staging"})
PHASE3_ROLES = (
    "architecture_lead_id",
    "toolchain_agent_id",
    "navigation_agent_id",
    "public_ui_agent_id",
    "capability_contract_agent_id",
    "architecture_acceptance_agent_id",
)
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
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def parse_refs(value: str) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [item.strip() for item in value.split(";") if item.strip()]
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError(f"Inventory reference cell must be an array of strings: {value}")
    return [item for item in parsed if item]


def parse_asset_refs(value: str, label: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must be a JSON string array") from exc
    if (
        not isinstance(parsed, list) or not parsed
        or not all(isinstance(item, str) and item for item in parsed)
        or parsed != sorted(set(parsed))
    ):
        raise ValueError(f"{label} must be a non-empty sorted JSON string array")
    return parsed


def requirement_id(feature_id: str, source_kind: str, source_ref: str) -> str:
    digest = sha256_text(f"{feature_id}|{source_kind}|{source_ref}")[:20].upper()
    return f"HREQ-{digest}"


def copy_template_csv(temp_dir: Path, source_name: str, target_name: str) -> None:
    shutil.copyfile(ASSETS / source_name, temp_dir / target_name)


def canonical_input(value: str, label: str) -> Path:
    raw = Path(value).expanduser().absolute()
    if raw.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link: {raw}")
    return raw.resolve()


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def validate_phase3_ownership(work_order: dict[str, Any], scope: dict[str, Any]) -> dict[str, str]:
    ownership = require_object(work_order.get("ownership"), "Phase 3 work-order ownership")
    normalized: dict[str, str] = {}
    for role in PHASE3_ROLES:
        actor = ownership.get(role)
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError(f"Phase 3 work order is missing ownership.{role}")
        normalized[role] = actor.strip()
    if len(set(normalized.values())) != len(PHASE3_ROLES):
        raise ValueError("All six frozen Phase 3 role IDs must be distinct")
    previous = scope.get("ownership") if isinstance(scope.get("ownership"), dict) else {}
    previous_ids = {
        actor
        for value in previous.values()
        for actor in (value if isinstance(value, list) else [value])
        if isinstance(actor, str) and actor
    }
    overlap = sorted(set(normalized.values()) & previous_ids)
    if overlap:
        raise ValueError(f"Phase 3 roles must be independent from frozen Phase 1/2 roles: {overlap}")
    return normalized


def run_phase2_gate_recheck(run_dir: Path) -> dict[str, Any]:
    if not CONTROLLER_VALIDATE.is_file():
        raise ValueError(f"Controller gate validator is missing: {CONTROLLER_VALIDATE}")
    completed = subprocess.run(
        [sys.executable, str(CONTROLLER_VALIDATE), "--run-dir", str(run_dir), "--phase", "2"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
        check=False,
    )
    try:
        report = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"Controller Phase 2 recheck returned invalid output: {detail[:500]}") from exc
    if completed.returncode != 0 or not isinstance(report, dict) or report.get("verdict") != "PASS":
        detail = report.get("errors") if isinstance(report, dict) else completed.stderr.strip()
        raise ValueError(f"Controller Phase 2 recheck failed: {detail}")
    return report


def catalog_index(
    path: Path,
    id_field: str,
    sentinel: Callable[[dict[str, str]], bool],
) -> tuple[dict[str, dict[str, str]], set[str]]:
    rows = read_csv(path)
    indexed: dict[str, dict[str, str]] = {}
    sentinels: set[str] = set()
    for row in rows:
        identifier = row.get(id_field, "")
        if not identifier or identifier in indexed:
            raise ValueError(f"Missing or duplicate {id_field} in {path.name}: {identifier!r}")
        indexed[identifier] = row
        if sentinel(row):
            sentinels.add(identifier)
    return indexed, sentinels


def input_record(
    source: Path,
    snapshot: Path | None = None,
    *,
    use_snapshot_as_path: bool = False,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": str(snapshot if use_snapshot_as_path and snapshot is not None else source),
        "sha256": sha256_file(source),
    }
    if snapshot is not None:
        record["snapshot_path"] = str(snapshot)
    if use_snapshot_as_path:
        record["source_path"] = str(source)
    return record


def validate_phase2_assets(
    phase2: Path,
    phase2_manifest: dict[str, Any],
    active_inventory: list[dict[str, str]],
    expected_reviewer: str,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    inventory_path = phase2 / "asset-inventory.csv"
    package = phase2 / "asset-package"
    manifest_path = package / "manifest.sha256"
    committed_path = package / "COMMITTED"
    if csv_fieldnames(inventory_path) != ASSET_INVENTORY_FIELDS:
        raise ValueError("Phase 2 asset-inventory.csv header differs from the handoff contract")
    if not manifest_path.is_file() or not committed_path.is_file():
        raise ValueError("Phase 2 asset package is not committed")
    if committed_path.read_text(encoding="utf-8") != sha256_file(manifest_path) + "\n":
        raise ValueError("Phase 2 asset COMMITTED marker differs from manifest.sha256")
    manifest_entries: dict[str, str] = {}
    lines = manifest_path.read_text(encoding="utf-8").splitlines()
    if lines != sorted(lines, key=lambda line: line.split("  ", 1)[-1]):
        raise ValueError("Phase 2 asset manifest is not sorted")
    for number, line in enumerate(lines, start=1):
        if "  " not in line:
            raise ValueError(f"Malformed Phase 2 asset manifest line {number}")
        digest, relative = line.split("  ", 1)
        pure = PurePosixPath(relative)
        if (
            not SHA256_RE.fullmatch(digest) or pure.is_absolute() or len(pure.parts) != 3
            or pure.parts[0] != "files" or any(part in {"", ".", ".."} for part in pure.parts)
            or relative in manifest_entries
        ):
            raise ValueError(f"Unsafe or duplicate Phase 2 asset manifest entry: {relative!r}")
        artifact = safe_relative_path(package, relative, "Phase 2 archived asset")
        if not artifact.is_file() or sha256_file(artifact) != digest:
            raise ValueError(f"Phase 2 archived asset hash differs: {relative}")
        manifest_entries[relative] = digest
    actual = {
        path.relative_to(package).as_posix()
        for path in (package / "files").rglob("*") if path.is_file()
    }
    if actual != set(manifest_entries):
        raise ValueError("Phase 2 asset manifest does not exactly cover asset-package/files")

    rows = read_csv(inventory_path)
    by_id: dict[str, dict[str, str]] = {}
    file_records: list[dict[str, str]] = []
    creator = phase2_manifest.get("ownership", {}).get("code_map_agent_id")
    for row in rows:
        asset_id = validate_id(row.get("asset_id", ""), "Asset-ID")
        if asset_id == "NONE_FOUND" or asset_id in by_id:
            raise ValueError(f"Sentinel or duplicate Phase 2 Asset-ID: {asset_id!r}")
        source = PurePosixPath(row.get("source_path", ""))
        archive = PurePosixPath(row.get("archive_path", ""))
        if (
            source.is_absolute() or not source.parts or ".." in source.parts
            or archive.as_posix() != f"asset-package/files/{asset_id}/{source.name}"
        ):
            raise ValueError(f"Non-canonical Phase 2 asset path: {asset_id}")
        digest = row.get("sha256", "")
        relative = archive.relative_to("asset-package").as_posix()
        artifact = safe_relative_path(phase2, archive.as_posix(), "Phase 2 asset archive")
        if not SHA256_RE.fullmatch(digest) or manifest_entries.get(relative) != digest or sha256_file(artifact) != digest:
            raise ValueError(f"Phase 2 asset hash chain differs: {asset_id}")
        if (
            not row.get("asset_type")
            or row.get("created_by") != creator
            or row.get("status") != "REVIEWED"
            or row.get("reviewed_by") != expected_reviewer
            or not row.get("reviewed_at")
        ):
            raise ValueError(f"Phase 2 asset lifecycle or role differs: {asset_id}")
        for field in ("feature_ids", "page_ids", "state_ids"):
            refs = parse_asset_refs(row.get(field, ""), f"{asset_id}.{field}")
            for ref in refs:
                validate_id(ref, field)
        by_id[asset_id] = row
        file_records.append({
            "asset_id": asset_id,
            "archive_path": archive.as_posix(),
            "path": str(artifact),
            "sha256": digest,
        })
    if len(rows) != len(manifest_entries):
        raise ValueError("Phase 2 asset inventory and package are not one-to-one")

    referenced: set[str] = set()
    for row in active_inventory:
        inventory_id = row.get("inventory_id", "<unknown>")
        refs = parse_asset_refs(row.get("asset_ids", ""), f"{inventory_id}.asset_ids")
        if "NONE_FOUND" in refs:
            if refs != ["NONE_FOUND"]:
                raise ValueError(f"{inventory_id}: NONE_FOUND cannot be mixed with Asset-IDs")
            continue
        for asset_id in refs:
            asset = by_id.get(asset_id)
            if asset is None:
                raise ValueError(f"{inventory_id}: unknown Phase 2 Asset-ID {asset_id}")
            for inventory_field, asset_field in (
                ("feature_id", "feature_ids"), ("page_id", "page_ids"), ("state_id", "state_ids")
            ):
                if row.get(inventory_field) not in parse_asset_refs(
                    asset.get(asset_field, ""), f"{asset_id}.{asset_field}"
                ):
                    raise ValueError(f"{inventory_id}: {asset_id} does not cover {inventory_field}")
            referenced.add(asset_id)
    if referenced != set(by_id):
        raise ValueError("Every Phase 2 asset must be referenced by active inventory")
    return sorted(rows, key=lambda row: row["asset_id"]), sorted(file_records, key=lambda row: row["asset_id"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--work-order", required=True)
    parser.add_argument("--architecture-lead", required=True)
    args = parser.parse_args()

    try:
        run_dir = canonical_input(args.run_dir, "Migration run")
        work_order_path = canonical_input(args.work_order, "Phase 3 work order")
    except ValueError as exc:
        parser.error(str(exc))
    if not run_dir.is_dir():
        parser.error(f"Migration run does not exist: {run_dir}")
    work_orders_root = (run_dir / "controller" / "work-orders").resolve()
    try:
        work_order_path.relative_to(work_orders_root)
    except ValueError:
        parser.error(f"Work order must be controller-owned below: {work_orders_root}")

    scope_input = run_dir / "controller" / "scope.json"
    gate_source_input = run_dir / "controller" / "gate-report.json"
    controller_anchor_input = run_dir / "controller" / "evidence-anchor-registry.csv"
    registry_input = run_dir / "controller" / "work-order-registry.csv"
    for label, path in (
        ("controller scope", scope_input),
        ("controller gate", gate_source_input),
        ("controller evidence anchors", controller_anchor_input),
        ("controller work-order registry", registry_input),
    ):
        if path.is_symlink():
            parser.error(f"{label} must not be a symbolic link: {path}")
    scope_path = scope_input.resolve()
    gate_source_path = gate_source_input.resolve()
    controller_anchor_path = controller_anchor_input.resolve()
    phase2_input = run_dir / "phase-02-android-inventory"
    phase2 = phase2_input.resolve()
    if phase2_input.is_symlink() or phase2.parent != run_dir:
        parser.error("Phase 2 workspace must be the canonical run-owned directory")
    phase2_paths = {
        "closure": phase2 / "closure-report.json",
        "closure_manifest": phase2 / "closure-manifest.sha256",
        "closed": phase2 / "CLOSED",
        "phase_manifest": phase2 / "phase-manifest.json",
        "inventory": phase2 / "inventory.csv",
        "asset_inventory": phase2 / "asset-inventory.csv",
        "asset_manifest": phase2 / "asset-package" / "manifest.sha256",
        "asset_committed": phase2 / "asset-package" / "COMMITTED",
        "acceptance": phase2 / "acceptance-registry.csv",
        "evidence_index": phase2 / "evidence-index.csv",
        "anchor_snapshot": phase2 / "evidence-anchors.snapshot.csv",
        "data_catalog": phase2 / "catalogs" / "data-dependencies.csv",
        "system_catalog": phase2 / "catalogs" / "system-capabilities.csv",
        "third_party_catalog": phase2 / "catalogs" / "third-party-dependencies.csv",
    }

    try:
        run_manifest = require_object(load_json(run_dir / "run-manifest.json"), "run-manifest.json")
        scope = require_object(load_json(scope_path), "controller scope")
        gate = require_object(load_json(gate_source_path), "controller gate")
        work_order = require_object(load_json(work_order_path), "Phase 3 work order")
        closure = require_object(load_json(phase2_paths["closure"]), "Phase 2 closure")
        phase2_manifest = require_object(load_json(phase2_paths["phase_manifest"]), "Phase 2 manifest")
        inventory_all = read_csv(phase2_paths["inventory"])
        acceptance = read_csv(phase2_paths["acceptance"])
        evidence_index = read_csv(phase2_paths["evidence_index"])
        anchor_snapshot = read_csv(phase2_paths["anchor_snapshot"])
        controller_anchors_all = read_csv(controller_anchor_path)
    except ValueError as exc:
        parser.error(str(exc))

    if gate.get("phase") != 2 or gate.get("verdict") != "PASS":
        parser.error("A current controller Phase 2 PASS gate is required")
    gate_sha256_before = sha256_file(gate_source_path)
    try:
        gate_recheck = run_phase2_gate_recheck(run_dir)
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        parser.error(str(exc))
    if sha256_file(gate_source_path) != gate_sha256_before:
        parser.error("Read-only Phase 2 gate recheck unexpectedly changed controller state")
    if gate_recheck.get("scope_sha256") != sha256_file(scope_path):
        parser.error("Controller Phase 2 recheck is bound to a different scope")

    try:
        closure_manifest_value = verify_closure_manifest(
            phase2,
            phase2_paths["closure_manifest"],
            exact_excludes=PHASE2_CLOSURE_EXCLUDES,
            directory_excludes=PHASE2_CLOSURE_DIR_EXCLUDES,
        )
    except ValueError as exc:
        parser.error(f"Phase 2 closure is not immutable: {exc}")
    if closure.get("closure_manifest_sha256") != sha256_text(closure_manifest_value):
        parser.error("Phase 2 closure report references a different closure manifest")
    try:
        closed_value = phase2_paths["closed"].read_text(encoding="utf-8").strip()
    except OSError as exc:
        parser.error(f"Cannot read Phase 2 CLOSED marker: {exc}")
    if closed_value != sha256_file(phase2_paths["closure"]):
        parser.error("Phase 2 CLOSED marker does not bind the current closure report")
    if (
        closure.get("final_verdict") != "PASS"
        or closure.get("evidence_chain_closed") is not True
        or phase2_manifest.get("status") != "CLOSED"
    ):
        parser.error("Phase 2 closure, evidence chain, and phase manifest must all be closed PASS")
    if scope.get("run_id") != run_manifest.get("run_id") or scope.get("project_id") != run_manifest.get("project_id"):
        parser.error("Controller scope identity does not match run-manifest.json")
    if phase2_manifest.get("run_id") != scope.get("run_id") or phase2_manifest.get("ownership") != scope.get("ownership"):
        parser.error("Phase 2 manifest identity or ownership differs from controller scope")

    scope_sha256 = sha256_file(scope_path)
    work_order_sha256 = sha256_file(work_order_path)
    gate_snapshot_relative = Path(str(work_order.get("phase2_gate_snapshot_relative_path", "")))
    if (
        not gate_snapshot_relative.parts
        or gate_snapshot_relative.is_absolute()
        or ".." in gate_snapshot_relative.parts
    ):
        parser.error("Phase 3 work order has an unsafe Phase 2 gate-snapshot path")
    gate_work_order_snapshot = run_dir / gate_snapshot_relative
    if gate_work_order_snapshot.is_symlink():
        parser.error("Controller-issued Phase 2 gate snapshot must not be a symbolic link")
    gate_work_order_snapshot = gate_work_order_snapshot.resolve()
    try:
        gate_work_order_snapshot.relative_to(work_orders_root)
    except ValueError:
        parser.error("Phase 2 gate snapshot must be controller-owned beside the work order")
    if not gate_work_order_snapshot.is_file() or sha256_file(gate_work_order_snapshot) != gate_sha256_before:
        parser.error("Controller-issued Phase 2 gate snapshot is missing or differs from the current Gate 2 PASS")
    try:
        ownership = validate_phase3_ownership(work_order, scope)
    except ValueError as exc:
        parser.error(str(exc))
    if args.architecture_lead != ownership["architecture_lead_id"]:
        parser.error("--architecture-lead must equal the controller-assigned Phase 3 architecture lead")
    registry_matches = [
        row for row in read_csv(registry_input)
        if row.get("work_order_id") == work_order.get("work_order_id")
    ]
    expected_work_order_values = {
        "scope_sha256": scope_sha256,
        "phase2_gate_sha256": gate_sha256_before,
        "phase2_closure_sha256": sha256_file(phase2_paths["closure"]),
        "phase2_closure_manifest_sha256": sha256_file(phase2_paths["closure_manifest"]),
        "phase2_closed_sha256": sha256_file(phase2_paths["closed"]),
        "phase2_inventory_sha256": sha256_file(phase2_paths["inventory"]),
        "phase2_asset_inventory_sha256": sha256_file(phase2_paths["asset_inventory"]),
        "phase2_asset_manifest_sha256": sha256_file(phase2_paths["asset_manifest"]),
        "phase2_asset_committed_sha256": sha256_file(phase2_paths["asset_committed"]),
        "phase2_anchor_snapshot_sha256": sha256_file(phase2_paths["anchor_snapshot"]),
        "controller_anchor_registry_sha256": sha256_file(controller_anchor_path),
    }
    registry_relative = work_order_path.relative_to(run_dir).as_posix()
    if (
        work_order.get("phase") != 3
        or work_order.get("status") != "ISSUED"
        or work_order.get("run_id") != scope.get("run_id")
        or work_order.get("issued_by") != scope.get("ownership", {}).get("migration_controller_id")
        or work_order.get("required_skill") != "harmonyos-migration-scaffold"
        or work_order.get("included_features") != scope.get("migration_scope", {}).get("included_features")
        or work_order.get("excluded_features") != scope.get("migration_scope", {}).get("excluded_features")
        or work_order.get("ownership") != ownership
        or any(work_order.get(key) != value for key, value in expected_work_order_values.items())
        or len(registry_matches) != 1
        or registry_matches[0].get("phase") != "3"
        or registry_matches[0].get("relative_path") != registry_relative
        or registry_matches[0].get("scope_sha256") != scope_sha256
        or registry_matches[0].get("work_order_sha256") != work_order_sha256
        or registry_matches[0].get("issued_by") != scope.get("ownership", {}).get("migration_controller_id")
        or registry_matches[0].get("status") != "ISSUED"
    ):
        parser.error("Phase 3 work order is not the exact controller-registered frozen order")

    expected_reviewer = scope.get("ownership", {}).get("coverage_checker_id")
    if closure.get("reviewer_id") != expected_reviewer or closure.get("reviewer_role") != "coverage-checker-agent":
        parser.error("Phase 2 was not closed by the frozen coverage checker")
    if not inventory_all:
        parser.error("Phase 2 inventory is empty")
    active_inventory = [row for row in inventory_all if row.get("row_status") != "SUPERSEDED"]
    if not active_inventory or any(
        row.get("row_status") != "REVIEWED" or row.get("reviewed_by") != expected_reviewer
        for row in active_inventory
    ):
        parser.error("Every active Phase 2 inventory row must be REVIEWED by the frozen checker")
    try:
        phase2_assets, phase2_asset_files = validate_phase2_assets(
            phase2, phase2_manifest, active_inventory, str(expected_reviewer)
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if any(row.get("status") not in {"ACCEPTED", "SUPERSEDED"} for row in evidence_index):
        parser.error("Phase 2 evidence index contains a non-accepted lifecycle state")
    accepted_index = {
        (row.get("inventory_id"), row.get("evidence_id"))
        for row in evidence_index if row.get("status") == "ACCEPTED"
    }
    inventory_pairs = {(row.get("inventory_id"), row.get("evidence_id")) for row in active_inventory}
    accepted_pairs = {
        (row.get("inventory_id"), row.get("evidence_id"))
        for row in acceptance
        if row.get("decision") == "ACCEPTED" and row.get("reviewed_by") == expected_reviewer
    }
    if accepted_index != inventory_pairs or accepted_pairs != inventory_pairs:
        parser.error("Accepted evidence index and acceptance registry must exactly cover active inventory")
    controller_anchors = sorted(
        [
            row for row in controller_anchors_all
            if row.get("run_id") == scope.get("run_id") and row.get("phase") == "2"
        ],
        key=lambda row: row.get("evidence_id", ""),
    )
    if anchor_snapshot != controller_anchors:
        parser.error("Phase 2 anchor snapshot differs from the controller-owned evidence registry")
    if {row.get("evidence_id") for row in controller_anchors} != {
        row.get("evidence_id") for row in evidence_index
    }:
        parser.error("Controller evidence anchors do not exactly cover the Phase 2 evidence index")

    catalog_specs = {
        "data_dependency_refs": (
            phase2_paths["data_catalog"], "data_dependency_id", "DATA_DEPENDENCY",
            lambda row: row.get("dependency_type") == "NONE" and row.get("name") == "NONE_FOUND"
            and row.get("direction") == "NONE" and row.get("migration_risk", "").lower() == "none",
        ),
        "system_capability_refs": (
            phase2_paths["system_catalog"], "system_capability_id", "SYSTEM_CAPABILITY",
            lambda row: row.get("capability_type") == "NONE" and row.get("name") == "NONE_FOUND"
            and row.get("permission_or_api") == "NONE" and row.get("migration_risk", "").lower() == "none",
        ),
        "third_party_dependency_refs": (
            phase2_paths["third_party_catalog"], "third_party_dependency_id", "THIRD_PARTY_DEPENDENCY",
            lambda row: row.get("name") == "NONE_FOUND" and row.get("version") == "NONE"
            and row.get("purpose") == "NONE" and row.get("migration_risk", "").lower() == "none",
        ),
    }
    catalog_indexes: dict[str, tuple[dict[str, dict[str, str]], set[str], str]] = {}
    try:
        for field, (path, id_field, source_kind, sentinel) in catalog_specs.items():
            indexed, sentinels = catalog_index(path, id_field, sentinel)
            catalog_indexes[field] = (indexed, sentinels, source_kind)
    except ValueError as exc:
        parser.error(str(exc))

    migration_scope = scope.get("migration_scope", {})
    included = {str(item) for item in migration_scope.get("included_features", [])}
    excluded = {str(item) for item in migration_scope.get("excluded_features", [])}
    if not included:
        parser.error("Frozen included feature scope is empty")
    for feature_id in included | excluded:
        try:
            validate_id(feature_id, "Feature-ID in controller scope")
        except ValueError as exc:
            parser.error(str(exc))

    source_rows: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    seen_inventory_ids: set[str] = set()
    visual_features: set[str] = set()
    for row in active_inventory:
        try:
            key = source_row_key(row)
            for field in ("inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id"):
                validate_id(row.get(field, ""), field)
        except ValueError as exc:
            parser.error(str(exc))
        if key in seen_keys or row["inventory_id"] in seen_inventory_ids:
            parser.error(f"Duplicate active Phase 2 source row: {row['inventory_id']} / {key}")
        if row["feature_id"] not in included | excluded:
            parser.error(f"Inventory Feature-ID is outside frozen scope: {row['feature_id']}")
        for ref_field, (indexed, _sentinels, _kind) in catalog_indexes.items():
            try:
                refs = parse_refs(row.get(ref_field, ""))
            except ValueError as exc:
                parser.error(f"{row['inventory_id']}: {exc}")
            if not refs:
                parser.error(f"{row['inventory_id']} lacks explicit {ref_field}")
            for ref in refs:
                catalog_row = indexed.get(ref)
                if catalog_row is None:
                    parser.error(f"{row['inventory_id']}: unknown {ref_field} reference {ref}")
                if catalog_row.get("feature_id") != row["feature_id"]:
                    parser.error(f"{row['inventory_id']}: catalog Feature-ID differs for {ref}")
        seen_keys.add(key)
        seen_inventory_ids.add(row["inventory_id"])
        visual_features.add(row["feature_id"])
        source_rows.append({**row, "source_row_key": key})

    requirements: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in source_rows:
        if row["feature_id"] in excluded:
            continue
        for field, (_indexed, sentinels, source_kind) in catalog_indexes.items():
            for ref in parse_refs(row.get(field, "")):
                if ref in sentinels:
                    continue
                key = (row["feature_id"], source_kind, ref)
                requirement = requirements.setdefault(
                    key,
                    {
                        "capability_requirement_id": requirement_id(*key),
                        "source_kind": source_kind,
                        "source_feature_id": row["feature_id"],
                        "source_requirement_ref": ref,
                        "source_inventory_row_keys": set(),
                    },
                )
                requirement["source_inventory_row_keys"].add(row["source_row_key"])

    for feature_id in sorted(included - visual_features):
        key = (feature_id, "SCOPE_FEATURE", feature_id)
        requirements[key] = {
            "capability_requirement_id": requirement_id(*key),
            "source_kind": "SCOPE_FEATURE",
            "source_feature_id": feature_id,
            "source_requirement_ref": feature_id,
            "source_inventory_row_keys": set(),
        }

    phase_dir = run_dir / PHASE_NAME
    if phase_dir.exists():
        parser.error(f"Phase 3 workspace already exists; overwrite is prohibited: {phase_dir}")
    inputs_dir = phase_dir / "inputs"
    input_snapshots = {
        "controller_scope": inputs_dir / "controller-scope.json",
        "phase2_gate": inputs_dir / "phase-02-gate-report.json",
        "phase2_closure": inputs_dir / "phase-02-closure-report.json",
        "phase2_closure_manifest": inputs_dir / "phase-02-closure-manifest.sha256",
        "phase2_closed": inputs_dir / "phase-02-CLOSED",
        "phase2_phase_manifest": inputs_dir / "phase-02-phase-manifest.json",
        "phase2_inventory": inputs_dir / "phase-02-inventory.csv",
        "phase2_asset_inventory": inputs_dir / "phase-02-asset-inventory.csv",
        "phase2_asset_manifest": inputs_dir / "phase-02-asset-package-manifest.sha256",
        "phase2_asset_committed": inputs_dir / "phase-02-asset-package-COMMITTED",
        "phase2_acceptance": inputs_dir / "phase-02-acceptance-registry.csv",
        "phase2_evidence_index": inputs_dir / "phase-02-evidence-index.csv",
        "phase2_anchor_snapshot": inputs_dir / "phase-02-evidence-anchors.snapshot.csv",
        "controller_anchor_registry": inputs_dir / "controller-evidence-anchor-registry.csv",
        "phase3_work_order": inputs_dir / "phase-03-work-order.json",
        "phase2_data_catalog": inputs_dir / "catalogs" / "data-dependencies.csv",
        "phase2_system_catalog": inputs_dir / "catalogs" / "system-capabilities.csv",
        "phase2_third_party_catalog": inputs_dir / "catalogs" / "third-party-dependencies.csv",
    }
    initialized_at = utc_now()
    input_lock: dict[str, Any] = {
        "run_id": run_manifest.get("run_id"),
        "project_id": run_manifest.get("project_id"),
        "locked_at": initialized_at,
        "locked_by": args.architecture_lead,
        "work_order_id": work_order.get("work_order_id"),
        "work_order_sha256": work_order_sha256,
        "ownership": ownership,
        "phase2_baseline_env_id": closure.get("baseline_env_id"),
        "controller_scope": input_record(scope_path, input_snapshots["controller_scope"]),
        "phase2_gate": input_record(
            gate_work_order_snapshot, input_snapshots["phase2_gate"], use_snapshot_as_path=True
        ),
        "phase2_closure": input_record(phase2_paths["closure"], input_snapshots["phase2_closure"]),
        "phase2_closure_manifest": input_record(
            phase2_paths["closure_manifest"], input_snapshots["phase2_closure_manifest"]
        ),
        "phase2_closed": input_record(phase2_paths["closed"], input_snapshots["phase2_closed"]),
        "phase2_phase_manifest": input_record(
            phase2_paths["phase_manifest"], input_snapshots["phase2_phase_manifest"]
        ),
        "phase2_inventory": {
            **input_record(phase2_paths["inventory"], input_snapshots["phase2_inventory"]),
            "row_count": len(inventory_all),
            "active_row_count": len(source_rows),
            "source_row_keys": sorted(seen_keys),
        },
        "phase2_asset_inventory": {
            **input_record(
                phase2_paths["asset_inventory"], input_snapshots["phase2_asset_inventory"]
            ),
            "row_count": len(phase2_assets),
            "asset_ids": [row["asset_id"] for row in phase2_assets],
        },
        "phase2_asset_package_manifest": input_record(
            phase2_paths["asset_manifest"], input_snapshots["phase2_asset_manifest"]
        ),
        "phase2_asset_package_committed": input_record(
            phase2_paths["asset_committed"], input_snapshots["phase2_asset_committed"]
        ),
        "phase2_asset_files": phase2_asset_files,
        "phase2_acceptance": input_record(
            phase2_paths["acceptance"], input_snapshots["phase2_acceptance"]
        ),
        "phase2_evidence_index": input_record(
            phase2_paths["evidence_index"], input_snapshots["phase2_evidence_index"]
        ),
        "phase2_anchor_snapshot": input_record(
            phase2_paths["anchor_snapshot"], input_snapshots["phase2_anchor_snapshot"]
        ),
        "controller_anchor_registry": input_record(
            controller_anchor_path, input_snapshots["controller_anchor_registry"]
        ),
        "phase3_work_order": input_record(work_order_path, input_snapshots["phase3_work_order"]),
        "phase2_catalogs": {
            "data_dependencies": input_record(
                phase2_paths["data_catalog"], input_snapshots["phase2_data_catalog"]
            ),
            "system_capabilities": input_record(
                phase2_paths["system_catalog"], input_snapshots["phase2_system_catalog"]
            ),
            "third_party_dependencies": input_record(
                phase2_paths["third_party_catalog"], input_snapshots["phase2_third_party_catalog"]
            ),
        },
        "included_feature_ids": sorted(included),
        "excluded_feature_ids": sorted(excluded),
        "inventory_feature_ids": sorted(visual_features),
        "page_ids": sorted({row["page_id"] for row in source_rows}),
        "state_ids": sorted({row["state_id"] for row in source_rows}),
        "capability_requirement_ids": sorted(
            requirement["capability_requirement_id"] for requirement in requirements.values()
        ),
    }

    architecture_rows: list[dict[str, str]] = []
    migration_rows: list[dict[str, str]] = []
    for row in sorted(source_rows, key=lambda item: item["source_row_key"]):
        architecture_rows.append(
            {
                "source_row_key": row["source_row_key"], "inventory_id": row["inventory_id"],
                "feature_id": row["feature_id"], "page_id": row["page_id"],
                "state_id": row["state_id"], "env_id": row["env_id"],
                "evidence_id": row["evidence_id"], "mapping_type": "",
                "harmony_module_id": "", "route_id": "", "surface_shell_id": "",
                "page_shell_id": "", "shell_file": "", "screenshot_ids": "",
                "verification_id": "", "mapped_by": "", "mapping_status": "NOT_STARTED",
                "notes": "",
            }
        )
        migration_rows.append(
            {
                "source_kind": "INVENTORY_ROW", "source_key": row["source_row_key"],
                "feature_id": row["feature_id"], "page_id": row["page_id"],
                "state_id": row["state_id"], "target_id": "", "status": "NOT_STARTED",
                "updated_by": ownership["architecture_lead_id"], "updated_at": initialized_at,
                "notes": "",
            }
        )

    capability_rows: list[dict[str, str]] = []
    for requirement in sorted(requirements.values(), key=lambda item: item["capability_requirement_id"]):
        requirement_key = requirement["capability_requirement_id"]
        capability_rows.append(
            {
                "capability_requirement_id": requirement_key,
                "source_kind": requirement["source_kind"],
                "source_feature_id": requirement["source_feature_id"],
                "source_requirement_ref": requirement["source_requirement_ref"],
                "source_inventory_row_keys": join_multi(requirement["source_inventory_row_keys"]),
                "capability_contract_id": "", "harmony_module_id": "", "contract_kind": "",
                "contract_file": "", "contract_symbol": "", "created_by": "",
                "status": "NOT_STARTED", "notes": "",
            }
        )
        migration_rows.append(
            {
                "source_kind": "CAPABILITY_REQUIREMENT", "source_key": requirement_key,
                "feature_id": requirement["source_feature_id"], "page_id": "", "state_id": "",
                "target_id": "", "status": "NOT_STARTED",
                "updated_by": ownership["architecture_lead_id"], "updated_at": initialized_at,
                "notes": "",
            }
        )

    asset_registry_rows = [
        {
            "asset_id": row["asset_id"],
            "phase2_archive_path": row["archive_path"],
            "asset_sha256": row["sha256"],
            "asset_type": row["asset_type"],
            "feature_ids": row["feature_ids"],
            "page_ids": row["page_ids"],
            "state_ids": row["state_ids"],
            "target_module_id": "",
            "target_path": "",
            "target_symbol": "",
            "planned_mode": "",
            "decision": "",
            "created_by": "",
            "status": "NOT_STARTED",
            "notes": "",
        }
        for row in phase2_assets
    ]

    copy_sources = {
        "controller_scope": scope_path,
        "phase2_gate": gate_work_order_snapshot,
        "phase2_closure": phase2_paths["closure"],
        "phase2_closure_manifest": phase2_paths["closure_manifest"],
        "phase2_closed": phase2_paths["closed"],
        "phase2_phase_manifest": phase2_paths["phase_manifest"],
        "phase2_inventory": phase2_paths["inventory"],
        "phase2_asset_inventory": phase2_paths["asset_inventory"],
        "phase2_asset_manifest": phase2_paths["asset_manifest"],
        "phase2_asset_committed": phase2_paths["asset_committed"],
        "phase2_acceptance": phase2_paths["acceptance"],
        "phase2_evidence_index": phase2_paths["evidence_index"],
        "phase2_anchor_snapshot": phase2_paths["anchor_snapshot"],
        "controller_anchor_registry": controller_anchor_path,
        "phase3_work_order": work_order_path,
        "phase2_data_catalog": phase2_paths["data_catalog"],
        "phase2_system_catalog": phase2_paths["system_catalog"],
        "phase2_third_party_catalog": phase2_paths["third_party_catalog"],
    }
    with tempfile.TemporaryDirectory(prefix=f".{PHASE_NAME}-", dir=run_dir) as temp_name:
        temp_dir = Path(temp_name)
        for name in ("inputs", "environments", "verification", "gate-reports", "harmony-project"):
            (temp_dir / name).mkdir()
        (temp_dir / "inputs" / "catalogs").mkdir()
        for key, source in copy_sources.items():
            relative = input_snapshots[key].relative_to(phase_dir)
            shutil.copyfile(source, temp_dir / relative)
        atomic_json(temp_dir / "stage-03-input-lock.json", input_lock)

        copy_template_csv(temp_dir, "module-registry.template.csv", "module-registry.csv")
        copy_template_csv(temp_dir, "route-registry.template.csv", "route-registry.csv")
        copy_template_csv(temp_dir, "surface-registry.template.csv", "surface-registry.csv")
        copy_template_csv(temp_dir, "public-ui-registry.template.csv", "public-ui-registry.csv")
        copy_template_csv(temp_dir, "architecture-decisions.template.csv", "architecture-decisions.csv")
        copy_template_csv(temp_dir, "rework-tickets.template.csv", "rework-tickets.csv")
        shutil.copyfile(ASSETS / "dependency-policy.template.json", temp_dir / "dependency-policy.json")
        shutil.copyfile(ASSETS / "henv-registry.template.csv", temp_dir / "environments" / "henv-registry.csv")
        write_csv(
            temp_dir / "architecture-map.csv",
            csv_fieldnames(ASSETS / "architecture-map.template.csv"), architecture_rows,
        )
        write_csv(
            temp_dir / "capability-contracts.csv",
            csv_fieldnames(ASSETS / "capability-contracts.template.csv"), capability_rows,
        )
        write_csv(
            temp_dir / "migration-status.csv",
            csv_fieldnames(ASSETS / "migration-status.template.csv"), migration_rows,
        )
        write_csv(
            temp_dir / "asset-registry.csv",
            csv_fieldnames(ASSETS / "asset-registry.template.csv"), asset_registry_rows,
        )
        atomic_json(
            temp_dir / "phase-manifest.json",
            {
                "run_id": run_manifest.get("run_id"), "project_id": run_manifest.get("project_id"),
                "phase": 3, "status": "IN_PROGRESS", "initialized_at": initialized_at,
                "architecture_lead": ownership["architecture_lead_id"],
                "ownership": ownership, "work_order_id": work_order.get("work_order_id"),
                "work_order_sha256": work_order_sha256,
                "phase2_input_locked": True, "business_implementation_allowed": False,
                "gui_only_evidence_allowed": False, "mp4_required": False,
            },
        )
        atomic_json(
            temp_dir / "build-report.json",
            {"status": "NOT_RUN", "verification_id": None, "updated_at": initialized_at},
        )
        atomic_json(
            temp_dir / "stage-03-gate-report.json",
            {"phase": 3, "verdict": "NOT_RUN", "reviewer_role": "architecture-acceptance-agent"},
        )
        temp_dir.rename(phase_dir)

    print(json.dumps({
        "workspace": str(phase_dir), "work_order_id": work_order.get("work_order_id"),
        "inventory_rows": len(source_rows), "capability_requirements": len(capability_rows),
        "assets": len(asset_registry_rows),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
