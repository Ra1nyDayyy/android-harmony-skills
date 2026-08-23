#!/usr/bin/env python3
"""Validate, finalize, and freeze the Android inventory evidence chain."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import stat
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from _common import (
    ASSET_INVENTORY_FIELDS,
    assert_no_symlink,
    assert_valid_json,
    assert_valid_png,
    atomic_json,
    atomic_text,
    load_json,
    manifest_lines,
    parse_resolution,
    read_csv,
    safe_workspace_path,
    sha256_file,
    utc_now,
    verify_environment_probe,
    verify_environment_attestation,
    verify_asset_chain,
    verify_phase_identity,
    write_csv,
)
from evaluate_page_gates import evaluate_page_gates
from evaluate_advanced_gates import evaluate_advanced_gates


FIELDS = [
    "inventory_id", "feature_id", "feature_name", "page_id", "page_name", "state_id",
    "state_name", "env_id", "evidence_id", "entry_condition", "transition_from_state_id",
    "predecessor_evidence_id", "action_summary", "expected_observable", "actual_observable",
    "code_refs", "business_rule_refs", "data_dependency_refs", "system_capability_refs",
    "third_party_dependency_refs", "asset_ids", "responsible_agent", "row_status", "rework_id",
    "reviewed_by", "reviewed_at",
]
REF_FIELDS = [
    "code_refs", "business_rule_refs", "data_dependency_refs", "system_capability_refs",
    "third_party_dependency_refs", "asset_ids",
]
INDEX_FIELDS = [
    "evidence_id", "inventory_id", "feature_id", "page_id", "state_id", "env_id",
    "captured_at", "relative_path", "metadata_sha256", "status", "supersedes_evidence_id",
]
ACCEPTANCE_FIELDS = ["inventory_id", "evidence_id", "decision", "reviewed_by", "reviewed_at"]
ANCHOR_FIELDS = [
    "anchor_id", "evidence_id", "run_id", "phase", "relative_path",
    "package_manifest_sha256", "metadata_sha256", "scope_sha256",
    "environment_registry_sha256", "anchored_at", "anchored_by", "status",
]
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CLI_ERROR_RE = re.compile(r"(?im)^\s*(?:error|failed|failure)\s*:")
CLOSURE_EXACT_EXCLUDES = {"closure-report.json", "closure-manifest.sha256", "CLOSED"}
CLOSURE_DIR_EXCLUDES = {".locks", ".staging"}
CATALOGS = {
    "business_rule_refs": ("business-rules.csv", "business_rule_id", "business_rule_agent_id"),
    "data_dependency_refs": ("data-dependencies.csv", "data_dependency_id", "data_dependency_agent_id"),
    "system_capability_refs": ("system-capabilities.csv", "system_capability_id", "data_dependency_agent_id"),
    "third_party_dependency_refs": ("third-party-dependencies.csv", "third_party_dependency_id", "data_dependency_agent_id"),
}
MIME_TYPES = {
    "screenshot.png": "image/png",
    "layout.json": "application/json",
    "layout-diff.json": "application/json",
    "steps.md": "text/markdown",
}


def add_error(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


def parse_iso(value: str, label: str, errors: list[str]) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        add_error(errors, f"Invalid UTC timestamp for {label}: {value!r}")


def parse_ref_array(row: dict[str, str], field: str, errors: list[str]) -> list[str]:
    try:
        value = json.loads(row.get(field, "[]") or "[]")
    except json.JSONDecodeError:
        add_error(errors, f"{row.get('inventory_id', '<unknown>')}: {field} is not JSON")
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        add_error(errors, f"{row.get('inventory_id', '<unknown>')}: {field} must be a string array")
        return []
    return value


def verify_code_ref(project_root: Path, reference: str, errors: list[str]) -> tuple[str, int] | None:
    if ":" not in reference:
        add_error(errors, f"Code reference must use path:line: {reference}")
        return None
    relative_value, line_value = reference.rsplit(":", 1)
    try:
        line_number = int(line_value)
    except ValueError:
        add_error(errors, f"Code reference has invalid line number: {reference}")
        return None
    relative = Path(relative_value)
    if relative.is_absolute() or ".." in relative.parts or line_number < 1:
        add_error(errors, f"Unsafe code reference: {reference}")
        return None
    path = (project_root / relative).resolve()
    try:
        path.relative_to(project_root)
    except ValueError:
        add_error(errors, f"Code reference escapes project: {reference}")
        return None
    if not path.is_file():
        add_error(errors, f"Code reference file does not exist: {reference}")
        return None
    line_count = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace"))
    if line_number > line_count:
        add_error(errors, f"Code reference line is outside the file: {reference}")
        return None
    return relative.as_posix(), line_number


def verify_manifest(directory: Path, expected_names: set[str], errors: list[str]) -> None:
    path = directory / "manifest.sha256"
    if not path.is_file():
        add_error(errors, f"Missing manifest: {path}")
        return
    entries: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if "  " not in line:
            add_error(errors, f"Malformed manifest line {path}:{number}")
            continue
        digest, name = line.split("  ", 1)
        pure = PurePosixPath(name)
        if not SHA256_RE.fullmatch(digest) or pure.is_absolute() or len(pure.parts) != 1 or name in entries:
            add_error(errors, f"Unsafe or duplicate manifest entry {path}:{number}")
            continue
        entries[name] = digest
    if set(entries) != expected_names:
        add_error(errors, f"Manifest file set differs in {directory}: {sorted(entries)}")
    for name in sorted(set(entries) & expected_names):
        artifact = directory / name
        if not artifact.is_file() or sha256_file(artifact) != entries[name]:
            add_error(errors, f"Manifest hash mismatch: {artifact}")


def require_read_only_evidence(evidence_dir: Path, errors: list[str], evidence_id: str) -> None:
    try:
        if evidence_dir.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            add_error(errors, f"{evidence_id}: sealed evidence directory is writable")
        for path in evidence_dir.iterdir():
            if path.is_file() and path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
                add_error(errors, f"{evidence_id}: sealed evidence file is writable: {path.name}")
    except OSError as exc:
        add_error(errors, f"{evidence_id}: cannot inspect evidence permissions: {exc}")


def validate_evidence_anchors(
    workspace: Path,
    phase_manifest: dict[str, Any],
    index_rows: list[dict[str, str]],
    errors: list[str],
) -> list[dict[str, str]]:
    registry_path = workspace.parent / "controller" / "evidence-anchor-registry.csv"
    try:
        with registry_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
    except OSError as exc:
        add_error(errors, f"Controller evidence-anchor registry is unavailable: {exc}")
        return []
    if fieldnames != ANCHOR_FIELDS:
        add_error(errors, "Controller evidence-anchor registry header differs")
    run_rows = [row for row in rows if row.get("run_id") == phase_manifest.get("run_id") and row.get("phase") == "2"]
    by_id: dict[str, dict[str, str]] = {}
    for row in run_rows:
        evidence_id = row.get("evidence_id", "")
        if not evidence_id or evidence_id in by_id:
            add_error(errors, f"Missing or duplicate controller evidence anchor: {evidence_id!r}")
        by_id[evidence_id] = row
    indexed_ids = {row.get("evidence_id", "") for row in index_rows}
    if set(by_id) != indexed_ids:
        add_error(errors, "Controller evidence anchors do not exactly match the evidence index")
    controller_id = phase_manifest.get("ownership", {}).get("migration_controller_id")
    for index in index_rows:
        evidence_id = index.get("evidence_id", "")
        anchor = by_id.get(evidence_id)
        if not anchor:
            continue
        expected_relative = (
            f"evidence/{index.get('env_id', '')}/{index.get('page_id', '')}/"
            f"{index.get('state_id', '')}/{evidence_id}"
        )
        try:
            evidence_dir = safe_workspace_path(workspace, expected_relative)
            manifest_digest = sha256_file(evidence_dir / "manifest.sha256")
            metadata_digest = sha256_file(evidence_dir / "metadata.json")
        except (OSError, ValueError) as exc:
            add_error(errors, f"{evidence_id}: cannot verify controller anchor: {exc}")
            continue
        if (
            anchor.get("anchor_id") != f"ANCH-{evidence_id}"
            or anchor.get("relative_path") != expected_relative
            or anchor.get("package_manifest_sha256") != manifest_digest
            or anchor.get("metadata_sha256") != metadata_digest
            or anchor.get("metadata_sha256") != index.get("metadata_sha256")
            or anchor.get("scope_sha256") != phase_manifest.get("scope_sha256")
            or anchor.get("environment_registry_sha256") != phase_manifest.get("environment_registry_sha256")
            or anchor.get("anchored_by") != controller_id
            or anchor.get("status") != "ANCHORED"
        ):
            add_error(errors, f"{evidence_id}: controller evidence anchor differs from the sealed package")
        parse_iso(anchor.get("anchored_at", ""), f"{evidence_id}.anchored_at", errors)
    return sorted(run_rows, key=lambda row: row.get("evidence_id", ""))


def closure_paths(workspace: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for path in workspace.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in Phase 2 package: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(workspace).as_posix()
        pure = PurePosixPath(relative)
        if relative in CLOSURE_EXACT_EXCLUDES or any(part in CLOSURE_DIR_EXCLUDES for part in pure.parts):
            continue
        if path.name.endswith((".lock", ".tmp")):
            continue
        paths[relative] = path
    return paths


def closure_manifest_text(workspace: Path) -> str:
    paths = closure_paths(workspace)
    return "".join(f"{sha256_file(paths[name])}  {name}\n" for name in sorted(paths))


def verify_closed_workspace(workspace: Path, expected_reviewer: str) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    try:
        report = load_json(workspace / "closure-report.json")
        manifest_text = (workspace / "closure-manifest.sha256").read_text(encoding="utf-8")
        marker = (workspace / "CLOSED").read_text(encoding="utf-8").strip()
    except (ValueError, OSError) as exc:
        return {}, [str(exc)]
    if report.get("reviewer_id") != expected_reviewer:
        add_error(errors, "Closed workspace reviewer differs from frozen coverage checker")
    if report.get("closure_manifest_sha256") != hashlib.sha256(manifest_text.encode("utf-8")).hexdigest():
        add_error(errors, "Closure manifest digest differs from closure report")
    if marker != sha256_file(workspace / "closure-report.json"):
        add_error(errors, "CLOSED marker differs from closure report")
    try:
        current = closure_manifest_text(workspace)
        if current != manifest_text:
            add_error(errors, "Closed Phase 2 package changed after final review")
    except ValueError as exc:
        add_error(errors, str(exc))
    try:
        phase_manifest = load_json(workspace / "phase-manifest.json")
        index_rows = read_csv(workspace / "evidence-index.csv")
        inventory_rows = read_csv(workspace / "inventory.csv")
        asset_rows = verify_asset_chain(
            workspace, phase_manifest, inventory_rows, require_reviewed=True
        )
        if report.get("asset_inventory_sha256") != sha256_file(workspace / "asset-inventory.csv"):
            add_error(errors, "Closure report has a different asset-inventory digest")
        if report.get("asset_package_manifest_sha256") != sha256_file(
            workspace / "asset-package" / "manifest.sha256"
        ):
            add_error(errors, "Closure report has a different asset-package manifest digest")
        if report.get("archived_assets") != len(asset_rows):
            add_error(errors, "Closure report asset count differs from asset inventory")
        anchor_rows = validate_evidence_anchors(workspace, phase_manifest, index_rows, errors)
        with (workspace / "evidence-anchors.snapshot.csv").open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            snapshot_fields = list(reader.fieldnames or [])
            snapshot_rows = list(reader)
        if snapshot_fields != ANCHOR_FIELDS or snapshot_rows != anchor_rows:
            add_error(errors, "Closed evidence-anchor snapshot differs from the controller registry")
        if report.get("evidence_anchor_snapshot_sha256") != sha256_file(workspace / "evidence-anchors.snapshot.csv"):
            add_error(errors, "Closure report has a different evidence-anchor snapshot digest")
    except (OSError, ValueError) as exc:
        add_error(errors, f"Closed evidence anchors cannot be verified: {exc}")
    return report, errors


def scan_prohibited_references(workspace: Path, errors: list[str]) -> None:
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".mp4":
            add_error(errors, f"MP4 is prohibited: {path}")
        if path.suffix.lower() in {".json", ".jsonl", ".csv", ".md", ".txt"} and path.stat().st_size < 10_000_000:
            text = path.read_text(encoding="utf-8", errors="replace").lower()
            if ".mp4" in text or "video/mp4" in text:
                add_error(errors, f"MP4 reference is prohibited: {path}")
            if "layout inspector" in text or "layoutinspector" in text:
                add_error(errors, f"Layout Inspector reference is prohibited: {path}")


def verify_inventory_pair(
    inventory_rows: list[dict[str, str]], inventory_json: dict[str, Any], errors: list[str]
) -> list[dict[str, Any]]:
    records = inventory_json.get("records")
    if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
        add_error(errors, "inventory.json must contain a records array")
        return []
    converted = []
    for row in inventory_rows:
        record: dict[str, Any] = {}
        for field in FIELDS:
            if field in REF_FIELDS:
                record[field] = parse_ref_array(row, field, errors)
            else:
                record[field] = row.get(field, "")
        converted.append(record)
    key = lambda row: str(row.get("inventory_id", ""))
    if sorted(converted, key=key) != sorted(records, key=key):
        add_error(errors, "inventory.csv and inventory.json are not semantically identical")
    return records


def validate_catalogs(
    workspace: Path,
    phase_manifest: dict[str, Any],
    inventory_rows: list[dict[str, str]],
    coverage_rows: list[dict[str, str]],
    environment_ids: set[str],
    errors: list[str],
) -> None:
    included = set(phase_manifest.get("included_features", []))
    ledger_by_feature: dict[str, dict[str, str]] = {}
    applicability: dict[str, set[str]] = {}
    for row in coverage_rows:
        feature_id = row.get("feature_id", "")
        if feature_id in ledger_by_feature:
            add_error(errors, f"Duplicate coverage ledger Feature-ID: {feature_id}")
        ledger_by_feature[feature_id] = row
        try:
            envs = json.loads(row.get("applicable_env_ids", ""))
        except json.JSONDecodeError:
            envs = None
        if not isinstance(envs, list) or not envs or not all(isinstance(item, str) for item in envs):
            add_error(errors, f"Coverage ledger has invalid applicable_env_ids: {feature_id}")
            envs = []
        unknown = set(envs) - environment_ids
        if unknown:
            add_error(errors, f"Coverage ledger references unknown environments for {feature_id}: {sorted(unknown)}")
        applicability[feature_id] = set(envs)
        if row.get("status") != "COMPLETE" or any(
            row.get(field, "").lower() != "true"
            for field in ("code_mapped", "runtime_states_captured", "business_rules_mapped", "data_dependencies_mapped")
        ):
            add_error(errors, f"Coverage ledger is not COMPLETE: {feature_id}")
        if not row.get("feature_name") or row.get("owner") != phase_manifest.get("ownership", {}).get("inventory_lead_id"):
            add_error(errors, f"Coverage ledger lacks feature_name/owner: {feature_id}")
    if set(ledger_by_feature) != included:
        add_error(errors, "Coverage ledger does not exactly match included Feature-IDs")

    active_rows = [row for row in inventory_rows if row.get("row_status") != "SUPERSEDED"]
    active_keys = {(row.get("feature_id"), row.get("page_id"), row.get("state_id"), row.get("env_id")) for row in active_rows}
    for feature_id in included:
        for env_id in applicability.get(feature_id, set()):
            if not any(key[0] == feature_id and key[3] == env_id for key in active_keys):
                add_error(errors, f"Feature/environment has no state inventory row: {feature_id}/{env_id}")

    project_root = Path(phase_manifest.get("android_project_root", "")).resolve()
    code_rows = read_csv(workspace / "catalogs" / "code-map.csv")
    code_refs: set[str] = set()
    for row in code_rows:
        reference = row.get("code_ref", "")
        if not reference or reference in code_refs:
            add_error(errors, f"Missing or duplicate code_ref in code map: {reference!r}")
        code_refs.add(reference)
        parsed = verify_code_ref(project_root, reference, errors)
        if parsed and (row.get("file_path"), row.get("line")) != (parsed[0], str(parsed[1])):
            add_error(errors, f"Code map path/line differs from code_ref: {reference}")
        if (
            row.get("feature_id") not in included
            or row.get("status") != "VERIFIED"
            or row.get("owner") != phase_manifest.get("ownership", {}).get("code_map_agent_id")
        ):
            add_error(errors, f"Code map row is outside scope or not VERIFIED: {reference}")
        disposition = row.get("coverage_disposition")
        if disposition == "IN_SCOPE":
            page_id, state_id = row.get("page_id", ""), row.get("state_candidate_id", "")
            if not page_id or not state_id:
                add_error(errors, f"IN_SCOPE code row lacks page/state: {reference}")
            for env_id in applicability.get(row.get("feature_id", ""), set()):
                key = (row.get("feature_id"), page_id, state_id, env_id)
                if key not in active_keys:
                    add_error(errors, f"Code state candidate lacks inventory evidence: {key}")
        elif disposition not in {"NON_VISUAL", "EXCLUDED_WITH_REASON", "DEAD_CODE_CANDIDATE"}:
            add_error(errors, f"Invalid code-map coverage disposition: {reference}")
        elif not row.get("notes"):
            add_error(errors, f"Non-in-scope code row needs a reason: {reference}")

    for row in active_rows:
        refs = parse_ref_array(row, "code_refs", errors)
        if not refs:
            add_error(errors, f"Active inventory row lacks code refs: {row.get('inventory_id')}")
        for reference in refs:
            if reference not in code_refs:
                add_error(errors, f"Inventory code ref is absent from code map: {reference}")

    for inventory_field, (filename, id_field, owner_field) in CATALOGS.items():
        rows = read_csv(workspace / "catalogs" / filename)
        ids: set[str] = set()
        catalog_features: set[str] = set()
        rows_by_id: dict[str, dict[str, str]] = {}
        for row in rows:
            identifier = row.get(id_field, "")
            if not identifier or identifier in ids:
                add_error(errors, f"Missing or duplicate {id_field}: {identifier!r}")
            ids.add(identifier)
            rows_by_id[identifier] = row
            catalog_features.add(row.get("feature_id", ""))
            if (
                row.get("feature_id") not in included
                or row.get("status") != "VERIFIED"
                or row.get("owner") != phase_manifest.get("ownership", {}).get(owner_field)
                or not row.get("notes")
            ):
                add_error(errors, f"Catalog row is outside scope or not VERIFIED: {identifier}")
            source_ref = row.get("source_ref", "")
            if filename == "business-rules.csv":
                required = ("page_id", "state_id", "condition", "outcome", "code_refs")
                if any(not row.get(field, "").strip() for field in required):
                    add_error(errors, f"Business-rule row lacks material findings: {identifier}")
                try:
                    rule_code_refs = json.loads(row.get("code_refs", ""))
                except json.JSONDecodeError:
                    rule_code_refs = None
                if not isinstance(rule_code_refs, list) or not rule_code_refs or not all(
                    isinstance(reference, str) and reference in code_refs for reference in rule_code_refs
                ):
                    add_error(errors, f"Business-rule row lacks verified code refs: {identifier}")
                if row.get("condition") == "NONE_FOUND" and not row.get("outcome", "").startswith("NO_RULE"):
                    add_error(errors, f"No-rule sentinel must use outcome NO_RULE...: {identifier}")
            else:
                if not source_ref or source_ref not in code_refs:
                    add_error(errors, f"Catalog row lacks a verified source_ref: {identifier}")
                if filename == "data-dependencies.csv":
                    required = ("dependency_type", "name", "direction", "source_ref", "sensitive", "migration_risk")
                    if any(not row.get(field, "").strip() for field in required):
                        add_error(errors, f"Data-dependency row lacks material findings: {identifier}")
                    if row.get("sensitive", "").lower() not in {"true", "false"}:
                        add_error(errors, f"Data-dependency sensitive must be true/false: {identifier}")
                    if row.get("dependency_type") == "NONE" and not (
                        row.get("name") == "NONE_FOUND" and row.get("direction") == "NONE"
                        and row.get("migration_risk", "").lower() == "none"
                    ):
                        add_error(errors, f"Invalid no-data-dependency sentinel: {identifier}")
                elif filename == "system-capabilities.csv":
                    required = ("capability_type", "name", "permission_or_api", "source_ref", "migration_risk")
                    if any(not row.get(field, "").strip() for field in required):
                        add_error(errors, f"System-capability row lacks material findings: {identifier}")
                    if row.get("capability_type") == "NONE" and not (
                        row.get("name") == "NONE_FOUND" and row.get("permission_or_api") == "NONE"
                        and row.get("migration_risk", "").lower() == "none"
                    ):
                        add_error(errors, f"Invalid no-system-capability sentinel: {identifier}")
                elif filename == "third-party-dependencies.csv":
                    required = ("name", "version", "purpose", "source_ref", "data_shared", "migration_risk")
                    if any(not row.get(field, "").strip() for field in required):
                        add_error(errors, f"Third-party row lacks material findings: {identifier}")
                    if row.get("data_shared", "").lower() not in {"true", "false"}:
                        add_error(errors, f"Third-party data_shared must be true/false: {identifier}")
                    if row.get("name") == "NONE_FOUND" and not (
                        row.get("version") == "NONE" and row.get("purpose") == "NONE"
                        and row.get("migration_risk", "").lower() == "none"
                    ):
                        add_error(errors, f"Invalid no-third-party sentinel: {identifier}")
        if catalog_features != included:
            add_error(errors, f"{filename} does not explicitly cover every included Feature-ID")
        for inventory_row in active_rows:
            references = parse_ref_array(inventory_row, inventory_field, errors)
            if not references:
                add_error(errors, f"Active inventory row lacks {inventory_field}: {inventory_row.get('inventory_id')}")
            for identifier in references:
                if identifier not in ids:
                    add_error(errors, f"Inventory reference is absent from {filename}: {identifier}")
                    continue
                catalog_row = rows_by_id[identifier]
                if catalog_row.get("feature_id") != inventory_row.get("feature_id"):
                    add_error(errors, f"Inventory/catalog feature differs for {identifier}")
                if filename == "business-rules.csv" and (
                    catalog_row.get("page_id") != inventory_row.get("page_id")
                    or catalog_row.get("state_id") != inventory_row.get("state_id")
                ):
                    add_error(errors, f"Inventory/business-rule page or state differs for {identifier}")


def verify_artifacts(evidence_dir: Path, metadata: dict[str, Any], expected: set[str], errors: list[str]) -> None:
    artifacts = metadata.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        add_error(errors, f"Evidence metadata has no artifacts: {evidence_dir}")
        return
    by_name: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            add_error(errors, f"Malformed artifact metadata: {evidence_dir}")
            continue
        name = artifact.get("relative_path")
        if name in by_name or name not in expected:
            add_error(errors, f"Unexpected or duplicate artifact metadata: {name!r}")
            continue
        by_name[name] = artifact
    if set(by_name) != expected:
        add_error(errors, f"Artifact metadata set differs: {evidence_dir}")
    for name in sorted(set(by_name) & expected):
        path = evidence_dir / name
        artifact = by_name[name]
        if artifact.get("mime_type") != MIME_TYPES[name]:
            add_error(errors, f"Artifact MIME differs: {path}")
        if artifact.get("sha256") != sha256_file(path) or artifact.get("size_bytes") != path.stat().st_size:
            add_error(errors, f"Artifact hash/size differs from sealed metadata: {path}")


def verify_command_record(record: dict[str, Any], label: str, errors: list[str]) -> list[str]:
    argv = record.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        add_error(errors, f"{label}: command argv is missing")
        return []
    combined = f"{record.get('stdout', '')}\n{record.get('stderr', '')}"
    if record.get("exit_code") != 0 or record.get("timed_out") is True or CLI_ERROR_RE.search(combined):
        add_error(errors, f"{label}: recorded command failed")
    return argv


def detect_cycle(predecessors: dict[str, str]) -> list[str] | None:
    for start in predecessors:
        seen: list[str] = []
        current = start
        while current:
            if current in seen:
                return seen[seen.index(current):] + [current]
            seen.append(current)
            current = predecessors.get(current, "")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument(
        "--decision", default="AUTO", choices=("AUTO", "PASS", "INCOMPLETE", "BLOCKED"),
        help="PASS is a legacy alias for AUTO; only deterministic gates can grant PASS",
    )
    parser.add_argument(
        "--attest-visual-review", action="store_true",
        help="Legacy advisory note; it never grants PASS",
    )
    parser.add_argument(
        "--attest-source-runtime-crosscheck", action="store_true",
        help="Legacy advisory note; it never grants PASS",
    )
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    workspace_input = Path(args.workspace).expanduser().absolute()
    if workspace_input.is_symlink():
        parser.error("Workspace must not be a symbolic link")
    workspace = workspace_input.resolve()
    errors: list[str] = []
    warnings: list[str] = []

    try:
        assert_no_symlink(workspace, workspace)
        phase_manifest = load_json(workspace / "phase-manifest.json")
        ownership = phase_manifest.get("ownership", {})
        expected_reviewer = ownership.get("coverage_checker_id", "")
    except ValueError as exc:
        parser.error(str(exc))

    if (workspace / "CLOSED").exists():
        report, closed_errors = verify_closed_workspace(workspace, expected_reviewer)
        if args.reviewer != expected_reviewer:
            closed_errors.append("--reviewer is not the frozen coverage checker")
        output = report if not closed_errors else {
            "final_verdict": "INCOMPLETE",
            "evidence_chain_closed": False,
            "errors": closed_errors,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if not closed_errors and report.get("final_verdict") == "PASS" else 1

    finalization_path = workspace / "FINALIZATION.json"
    try:
        finalization = load_json(finalization_path) if finalization_path.is_file() else None
    except ValueError as exc:
        finalization = None
        add_error(errors, str(exc))
    is_resuming_finalization = isinstance(finalization, dict)
    if is_resuming_finalization and finalization.get("reviewer_id") != expected_reviewer:
        add_error(errors, "Finalization journal reviewer differs from frozen coverage checker")

    if not expected_reviewer or args.reviewer != expected_reviewer:
        add_error(errors, "--reviewer must equal the frozen coverage checker")
    if args.reviewer in {
        ownership.get("migration_controller_id"),
        ownership.get("inventory_lead_id"),
        ownership.get("evidence_administrator_id"),
    }:
        add_error(errors, "Final reviewer must be independent of controller, lead, and evidence administrator")
    if args.decision == "PASS":
        warnings.append("Requested PASS was ignored; the final verdict is machine-computed")

    try:
        if sha256_file(workspace / "environments.json") != phase_manifest.get("environment_registry_sha256"):
            add_error(errors, "Frozen environment registry changed")
        scope_snapshot = verify_phase_identity(workspace, phase_manifest)
        environment_registry = load_json(workspace / "environments.json")
        inventory_rows = read_csv(workspace / "inventory.csv")
        inventory_json = load_json(workspace / "inventory.json")
        evidence_index_rows = read_csv(workspace / "evidence-index.csv")
        rechecks = read_csv(workspace / "rechecks.csv")
        coverage_rows = read_csv(workspace / "coverage-ledger.csv")
        asset_rows = read_csv(workspace / "asset-inventory.csv")
    except ValueError as exc:
        add_error(errors, str(exc))
        scope_snapshot, environment_registry, inventory_rows, inventory_json = {}, {}, [], {}
        evidence_index_rows, rechecks, coverage_rows, asset_rows = [], [], [], []

    try:
        asset_rows = verify_asset_chain(workspace, phase_manifest, inventory_rows)
        if not is_resuming_finalization and any(row.get("status") != "ARCHIVED" for row in asset_rows):
            add_error(errors, "Assets cannot be marked REVIEWED before the finalization journal")
    except (OSError, ValueError) as exc:
        add_error(errors, f"Asset handoff chain is invalid: {exc}")

    anchor_rows = validate_evidence_anchors(workspace, phase_manifest, evidence_index_rows, errors)

    # Atomic file replacement means a crash can leave whole old/new lifecycle files, never half files.
    # An IN_PROGRESS journal deterministically reapplies the reviewer-only lifecycle changes before revalidation.
    if (
        is_resuming_finalization
        and finalization.get("status") == "IN_PROGRESS"
        and finalization.get("reviewer_id") == expected_reviewer
        and isinstance(inventory_json.get("records"), list)
    ):
        resumed_at = str(finalization.get("reviewed_at", ""))
        for row in inventory_rows:
            if row.get("row_status") != "SUPERSEDED":
                row.update({"row_status": "REVIEWED", "reviewed_by": expected_reviewer, "reviewed_at": resumed_at})
        for record in inventory_json["records"]:
            if isinstance(record, dict) and record.get("row_status") != "SUPERSEDED":
                record.update({"row_status": "REVIEWED", "reviewed_by": expected_reviewer, "reviewed_at": resumed_at})
        write_csv(workspace / "inventory.csv", FIELDS, inventory_rows)
        atomic_json(workspace / "inventory.json", inventory_json)
        atomic_text(workspace / "inventory-manifest.sha256", manifest_lines(workspace, ["inventory.json", "inventory.csv"]))
        for row in asset_rows:
            row.update({
                "status": "REVIEWED", "reviewed_by": expected_reviewer,
                "reviewed_at": resumed_at,
            })
        write_csv(workspace / "asset-inventory.csv", ASSET_INVENTORY_FIELDS, asset_rows)
        for row in evidence_index_rows:
            if row.get("status") == "SEALED":
                row["status"] = "ACCEPTED"
        write_csv(workspace / "evidence-index.csv", INDEX_FIELDS, evidence_index_rows)
        write_csv(
            workspace / "acceptance-registry.csv",
            ACCEPTANCE_FIELDS,
            [
                {
                    "inventory_id": row["inventory_id"], "evidence_id": row["evidence_id"],
                    "decision": "ACCEPTED", "reviewed_by": expected_reviewer, "reviewed_at": resumed_at,
                }
                for row in inventory_rows if row.get("row_status") == "REVIEWED"
            ],
        )
        phase_manifest.update({"status": "CLOSED", "closed_at": resumed_at, "closed_by": expected_reviewer})
        atomic_json(workspace / "phase-manifest.json", phase_manifest)
        finalization = {
            "status": "COMPLETE", "reviewer_id": expected_reviewer, "reviewed_at": resumed_at,
            "inventory_sha256": sha256_file(workspace / "inventory.csv"),
            "asset_inventory_sha256": sha256_file(workspace / "asset-inventory.csv"),
            "asset_package_manifest_sha256": sha256_file(workspace / "asset-package" / "manifest.sha256"),
            "evidence_index_sha256": sha256_file(workspace / "evidence-index.csv"),
            "acceptance_registry_sha256": sha256_file(workspace / "acceptance-registry.csv"),
            "phase_manifest_sha256": sha256_file(workspace / "phase-manifest.json"),
        }
        atomic_json(finalization_path, finalization)

    allowed_phase_status = {"IN_PROGRESS", "CLOSED"} if is_resuming_finalization else {"IN_PROGRESS"}
    if phase_manifest.get("status") not in allowed_phase_status:
        add_error(errors, "Unclosed Phase 2 manifest has an invalid lifecycle status")
    if phase_manifest.get("run_id") != scope_snapshot.get("run_id"):
        add_error(errors, "Phase manifest and controller scope run IDs differ")
    if scope_snapshot.get("ownership") != ownership:
        add_error(errors, "Phase ownership differs from controller scope")
    if not inventory_rows:
        add_error(errors, "PASS requires at least one inventory row")
    verify_manifest_for_inventory = workspace / "inventory-manifest.sha256"
    if not verify_manifest_for_inventory.is_file():
        add_error(errors, "Missing inventory-manifest.sha256")
    else:
        expected_lines = manifest_lines(workspace, ["inventory.json", "inventory.csv"])
        if verify_manifest_for_inventory.read_text(encoding="utf-8") != expected_lines:
            add_error(errors, "Inventory manifest differs from inventory files")
    inventory_records = verify_inventory_pair(inventory_rows, inventory_json, errors)
    scan_prohibited_references(workspace, errors)

    environments = environment_registry.get("environments", []) if isinstance(environment_registry, dict) else []
    env_by_id = {env.get("env_id"): env for env in environments if isinstance(env, dict)}
    environment_ids = set(env_by_id)
    baseline_env_id = environment_registry.get("baseline_env_id") if isinstance(environment_registry, dict) else None
    if len([env for env in environments if env.get("is_baseline") is True]) != 1:
        add_error(errors, "Environment registry does not contain exactly one baseline")
    try:
        validate_catalogs(workspace, phase_manifest, inventory_rows, coverage_rows, environment_ids, errors)
    except ValueError as exc:
        add_error(errors, str(exc))
    try:
        page_gate_report = evaluate_page_gates(workspace, write_report=True)
        if page_gate_report.get("machine_verdict") != "PASS":
            add_error(errors, "Deterministic page gate is BLOCKED")
            for message in page_gate_report.get("errors", []):
                add_error(errors, f"Page gate: {message}")
    except (OSError, ValueError) as exc:
        page_gate_report = {"machine_verdict": "BLOCKED", "errors": [str(exc)], "pages": []}
        add_error(errors, f"Deterministic page gate failed: {exc}")
    try:
        advanced_gate_report = evaluate_advanced_gates(workspace, write_report=True)
        if advanced_gate_report.get("machine_verdict") != "PASS":
            add_error(errors, "Deterministic advanced gate is BLOCKED")
            for message in advanced_gate_report.get("errors", []):
                add_error(errors, f"Advanced gate: {message}")
    except (OSError, ValueError) as exc:
        advanced_gate_report = {"machine_verdict": "BLOCKED", "errors": [str(exc)]}
        add_error(errors, f"Deterministic advanced gate failed: {exc}")

    index_by_id: dict[str, dict[str, str]] = {}
    for row in evidence_index_rows:
        evidence_id = row.get("evidence_id", "")
        if not evidence_id or evidence_id in index_by_id:
            add_error(errors, f"Missing or duplicate evidence index ID: {evidence_id!r}")
        index_by_id[evidence_id] = row

    inventory_evidence: set[str] = set()
    row_keys: set[tuple[str, str, str, str]] = set()
    inventory_ids: set[str] = set()
    predecessors: dict[str, str] = {}
    metadata_by_evidence: dict[str, dict[str, Any]] = {}
    pending_count = 0
    active_inventory_rows: list[dict[str, str]] = []
    for row in inventory_rows:
        inventory_id = row.get("inventory_id", "")
        core_fields = ("feature_id", "page_id", "state_id", "env_id", "evidence_id")
        core = [row.get(name, "") for name in core_fields]
        if not inventory_id or inventory_id in inventory_ids:
            add_error(errors, f"Missing or duplicate Inventory-ID: {inventory_id!r}")
        inventory_ids.add(inventory_id)
        if any(not value for value in core):
            add_error(errors, f"Inventory row lacks a core ID: {inventory_id}")
            continue
        key = tuple(core[:4])
        if key in row_keys:
            add_error(errors, f"Duplicate feature/page/state/environment row: {key}")
        row_keys.add(key)
        evidence_id = row["evidence_id"]
        if evidence_id in inventory_evidence:
            add_error(errors, f"Evidence is referenced by multiple rows: {evidence_id}")
        inventory_evidence.add(evidence_id)
        status = row.get("row_status")
        if row.get("responsible_agent") not in ownership.get("runtime_state_agent_ids", []):
            add_error(errors, f"Inventory row is not owned by a frozen runtime-state agent: {inventory_id}")
        if status in {"PENDING_CONFIRMATION", "REWORK"}:
            pending_count += 1
        allowed_row_statuses = {"CAPTURED", "PENDING_CONFIRMATION", "REWORK", "SUPERSEDED"}
        if is_resuming_finalization:
            allowed_row_statuses.add("REVIEWED")
        if status not in allowed_row_statuses:
            add_error(errors, f"Invalid pre-closure inventory status: {status}")
        if status != "SUPERSEDED":
            active_inventory_rows.append(row)
        if status == "REVIEWED":
            if not is_resuming_finalization or row.get("reviewed_by") != expected_reviewer or not row.get("reviewed_at"):
                add_error(errors, f"Invalid resumed review fields: {inventory_id}")
        elif row.get("reviewed_by") or row.get("reviewed_at"):
            add_error(errors, f"Pre-closure row has unauthorized review fields: {inventory_id}")

        index = index_by_id.get(evidence_id)
        if not index:
            add_error(errors, f"Inventory references missing Evidence-ID: {evidence_id}")
            continue
        expected_index_status = (
            "SUPERSEDED" if status == "SUPERSEDED" else "ACCEPTED" if status == "REVIEWED" else "SEALED"
        )
        if index.get("status") != expected_index_status:
            add_error(errors, f"{evidence_id}: index status differs from lifecycle")
        for field in ("inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id"):
            if index.get(field, "") != row.get(field, ""):
                add_error(errors, f"{evidence_id}: index {field} differs from inventory")
        expected_relative = f"evidence/{row['env_id']}/{row['page_id']}/{row['state_id']}/{evidence_id}"
        if index.get("relative_path") != expected_relative:
            add_error(errors, f"{evidence_id}: index path is not canonical")
            continue
        try:
            evidence_dir = safe_workspace_path(workspace, expected_relative)
            assert_no_symlink(evidence_dir, workspace)
        except ValueError as exc:
            add_error(errors, str(exc))
            continue
        is_transition = bool(row.get("predecessor_evidence_id"))
        package_files = {path.name for path in evidence_dir.iterdir() if path.is_file()} if evidence_dir.is_dir() else set()
        required_files = {"screenshot.png", "layout.json", "steps.md", "metadata.json", "manifest.sha256", "COMMITTED"}
        if is_transition:
            required_files.add("layout-diff.json")
        if package_files != required_files:
            add_error(errors, f"{evidence_id}: evidence package file set differs: {sorted(package_files)}")
            continue
        require_read_only_evidence(evidence_dir, errors, evidence_id)
        if (evidence_dir / "COMMITTED").read_text(encoding="utf-8").strip() != evidence_id:
            add_error(errors, f"{evidence_id}: COMMITTED content differs")
        try:
            screenshot_size = assert_valid_png(evidence_dir / "screenshot.png")
            assert_valid_json(evidence_dir / "layout.json")
            if is_transition:
                assert_valid_json(evidence_dir / "layout-diff.json")
            if (evidence_dir / "steps.md").stat().st_size == 0:
                add_error(errors, f"{evidence_id}: steps.md is empty")
            metadata = load_json(evidence_dir / "metadata.json")
        except (ValueError, OSError) as exc:
            add_error(errors, f"{evidence_id}: {exc}")
            continue
        metadata_by_evidence[evidence_id] = metadata
        manifest_names = {"screenshot.png", "layout.json", "steps.md", "metadata.json"}
        artifact_names = {"screenshot.png", "layout.json", "steps.md"}
        if is_transition:
            manifest_names.add("layout-diff.json")
            artifact_names.add("layout-diff.json")
        verify_manifest(evidence_dir, manifest_names, errors)
        verify_artifacts(evidence_dir, metadata, artifact_names, errors)
        if sha256_file(evidence_dir / "metadata.json") != index.get("metadata_sha256"):
            add_error(errors, f"{evidence_id}: metadata hash differs from index")
        if metadata.get("status") != "SEALED" or metadata.get("capture_tool") != "android-cli":
            add_error(errors, f"{evidence_id}: metadata is not an Android CLI sealed package")
        if not metadata.get("android_cli_version"):
            add_error(errors, f"{evidence_id}: Android CLI version is missing")
        for field in ("inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id"):
            if str(metadata.get(field, "")) != str(row.get(field, "")):
                add_error(errors, f"{evidence_id}: metadata {field} differs from inventory")
        if metadata.get("captured_at") != index.get("captured_at"):
            add_error(errors, f"{evidence_id}: captured_at differs from index")
        parse_iso(str(metadata.get("issued_at", "")), f"{evidence_id}.issued_at", errors)
        parse_iso(str(metadata.get("captured_at", "")), f"{evidence_id}.captured_at", errors)
        if metadata.get("issued_by") != ownership.get("evidence_administrator_id"):
            add_error(errors, f"{evidence_id}: issuer is not the frozen evidence administrator")
        if metadata.get("captured_by") not in ownership.get("runtime_state_agent_ids", []):
            add_error(errors, f"{evidence_id}: collector is not a frozen runtime-state agent")
        env = env_by_id.get(row["env_id"])
        if not env or env.get("status") != "FROZEN":
            add_error(errors, f"{evidence_id}: unknown or unfrozen ENV-ID")
            env = {}
        try:
            if screenshot_size != parse_resolution(str(env.get("resolution", ""))):
                add_error(errors, f"{evidence_id}: screenshot dimensions differ from frozen environment")
        except ValueError as exc:
            add_error(errors, f"{evidence_id}: {exc}")
        identity_map = {
            "device_serial": "device_serial", "application_id": "application_id", "app_version": "app_version",
            "app_build": "app_build", "source_revision": "source_revision", "apk_sha256": "apk_sha256",
        }
        for metadata_field, env_field in identity_map.items():
            if metadata.get(metadata_field) != env.get(env_field):
                add_error(errors, f"{evidence_id}: metadata {metadata_field} differs from frozen environment")
        if metadata.get("environment_registry_sha256") != phase_manifest.get("environment_registry_sha256"):
            add_error(errors, f"{evidence_id}: environment digest differs")
        if metadata.get("scope_sha256") != phase_manifest.get("scope_sha256"):
            add_error(errors, f"{evidence_id}: scope digest differs")
        try:
            _, attestation_digest = verify_environment_attestation(workspace, env, phase_manifest)
            if metadata.get("environment_attestation_sha256") != attestation_digest:
                add_error(errors, f"{evidence_id}: environment readiness attestation digest differs")
        except ValueError as exc:
            add_error(errors, f"{evidence_id}: {exc}")

        commands = metadata.get("commands")
        if not isinstance(commands, list) or len(commands) < 3:
            add_error(errors, f"{evidence_id}: command records are incomplete")
            commands = []
        layout_index = screen_index = diff_index = version_index = None
        for command_index, command in enumerate(commands):
            if not isinstance(command, dict):
                add_error(errors, f"{evidence_id}: malformed command record")
                continue
            argv = verify_command_record(command, evidence_id, errors)
            lowered = [item.lower() for item in argv]
            joined = " ".join(lowered)
            if "layout inspector" in joined or "layoutinspector" in joined:
                add_error(errors, f"{evidence_id}: Layout Inspector is prohibited")
            if len(lowered) >= 2 and lowered[1] == "layout" and "--diff" not in lowered:
                layout_index = command_index
                if f"--device={env.get('device_serial', '')}" not in argv:
                    add_error(errors, f"{evidence_id}: layout command lacks frozen device selector")
            if len(lowered) >= 2 and lowered[1] == "layout" and "--diff" in lowered:
                diff_index = command_index
                if f"--device={env.get('device_serial', '')}" not in argv:
                    add_error(errors, f"{evidence_id}: layout diff lacks frozen device selector")
            if len(lowered) >= 3 and lowered[1:3] == ["screen", "capture"]:
                screen_index = command_index
                if f"--device={env.get('device_serial', '')}" not in argv:
                    add_error(errors, f"{evidence_id}: screenshot lacks frozen device selector")
            if lowered[1:] == ["--version"]:
                version_index = command_index
        if layout_index is None or screen_index is None or version_index is None:
            add_error(errors, f"{evidence_id}: missing full layout, screenshot, or CLI version command")
        elif not (layout_index < screen_index < version_index):
            add_error(errors, f"{evidence_id}: full layout/screenshot/version command order differs")
        if is_transition and (diff_index is None or layout_index is None or diff_index >= layout_index):
            add_error(errors, f"{evidence_id}: transition lacks a preceding layout diff command")
        device_record = metadata.get("device_verification")
        if not isinstance(device_record, dict):
            add_error(errors, f"{evidence_id}: device verification is missing")
        else:
            adb_argv = verify_command_record(device_record, f"{evidence_id}.device", errors)
            if len(adb_argv) < 3 or adb_argv[1:] != ["devices", "-l"]:
                add_error(errors, f"{evidence_id}: device verification command differs")
            matching = [line.split() for line in str(device_record.get("stdout", "")).splitlines()[1:] if line.split()[:1] == [str(env.get("device_serial", ""))]]
            if len(matching) != 1 or len(matching[0]) < 2 or matching[0][1] != "device":
                add_error(errors, f"{evidence_id}: device verification does not prove the frozen device")
        try:
            verify_environment_probe(metadata.get("environment_verification"), env)
        except (ValueError, RuntimeError) as exc:
            add_error(errors, f"{evidence_id}: {exc}")
        app_record = metadata.get("application_verification")
        if not isinstance(app_record, dict):
            add_error(errors, f"{evidence_id}: foreground application verification is missing")
        else:
            app_argv = verify_command_record(app_record, f"{evidence_id}.application", errors)
            expected_suffix = [
                "-s", str(env.get("device_serial", "")), "shell", "dumpsys", "activity", "activities",
            ]
            if app_argv[1:] != expected_suffix or str(env.get("application_id", "")) not in str(app_record.get("stdout", "")):
                add_error(errors, f"{evidence_id}: foreground application check differs")
        source_records = metadata.get("source_verification")
        if not isinstance(source_records, list) or len(source_records) != 2:
            add_error(errors, f"{evidence_id}: source verification is missing")
        else:
            for record in source_records:
                if isinstance(record, dict):
                    verify_command_record(record, f"{evidence_id}.source", errors)
            if source_records[0].get("stdout", "").strip() != phase_manifest.get("source_revision"):
                add_error(errors, f"{evidence_id}: source revision verification differs")
            if source_records[1].get("stdout", "").strip():
                add_error(errors, f"{evidence_id}: source worktree was dirty")

        predecessor = row.get("predecessor_evidence_id", "")
        previous_state = row.get("transition_from_state_id", "")
        if bool(predecessor) != bool(previous_state):
            add_error(errors, f"{evidence_id}: transition fields are incomplete")
        if predecessor:
            predecessors[evidence_id] = predecessor
            if metadata.get("predecessor_evidence_id") != predecessor:
                add_error(errors, f"{evidence_id}: predecessor differs from metadata")

    for evidence_id, predecessor in predecessors.items():
        current = metadata_by_evidence.get(evidence_id, {})
        previous = metadata_by_evidence.get(predecessor)
        if not previous:
            add_error(errors, f"{evidence_id}: predecessor is not a formal inventory evidence")
            continue
        row = next((item for item in inventory_rows if item.get("evidence_id") == evidence_id), {})
        if previous.get("state_id") != row.get("transition_from_state_id"):
            add_error(errors, f"{evidence_id}: transition state differs from predecessor")
        for field in ("feature_id", "env_id"):
            if previous.get(field) != current.get(field):
                add_error(errors, f"{evidence_id}: predecessor differs on {field}")
        try:
            current_dir = safe_workspace_path(workspace, index_by_id[evidence_id]["relative_path"])
            previous_dir = safe_workspace_path(workspace, index_by_id[predecessor]["relative_path"])
            if sha256_file(current_dir / "layout.json") == sha256_file(previous_dir / "layout.json") and sha256_file(current_dir / "screenshot.png") == sha256_file(previous_dir / "screenshot.png"):
                add_error(errors, f"{evidence_id}: transition is not observably different")
        except (KeyError, ValueError, OSError) as exc:
            add_error(errors, f"{evidence_id}: predecessor path is invalid: {exc}")

    cycle = detect_cycle(predecessors)
    if cycle:
        add_error(errors, f"Evidence predecessor cycle: {' -> '.join(cycle)}")

    # Superseded packages remain immutable audit evidence even when no longer in the current inventory.
    for evidence_id, index in list(index_by_id.items()):
        if evidence_id in metadata_by_evidence or index.get("status") != "SUPERSEDED":
            continue
        expected_relative = (
            f"evidence/{index.get('env_id', '')}/{index.get('page_id', '')}/"
            f"{index.get('state_id', '')}/{evidence_id}"
        )
        if index.get("relative_path") != expected_relative:
            add_error(errors, f"{evidence_id}: superseded package path is not canonical")
            continue
        try:
            evidence_dir = safe_workspace_path(workspace, expected_relative)
            metadata = load_json(evidence_dir / "metadata.json")
            transition = bool(metadata.get("predecessor_evidence_id"))
            required_files = {
                "screenshot.png", "layout.json", "steps.md", "metadata.json", "manifest.sha256", "COMMITTED",
            }
            manifest_names = {"screenshot.png", "layout.json", "steps.md", "metadata.json"}
            artifact_names = {"screenshot.png", "layout.json", "steps.md"}
            if transition:
                required_files.add("layout-diff.json")
                manifest_names.add("layout-diff.json")
                artifact_names.add("layout-diff.json")
            actual_files = {path.name for path in evidence_dir.iterdir() if path.is_file()}
            if actual_files != required_files:
                add_error(errors, f"{evidence_id}: superseded package file set differs")
                continue
            require_read_only_evidence(evidence_dir, errors, evidence_id)
            if (evidence_dir / "COMMITTED").read_text(encoding="utf-8").strip() != evidence_id:
                add_error(errors, f"{evidence_id}: superseded COMMITTED content differs")
            screenshot_size = assert_valid_png(evidence_dir / "screenshot.png")
            assert_valid_json(evidence_dir / "layout.json")
            if transition:
                assert_valid_json(evidence_dir / "layout-diff.json")
            verify_manifest(evidence_dir, manifest_names, errors)
            verify_artifacts(evidence_dir, metadata, artifact_names, errors)
            if sha256_file(evidence_dir / "metadata.json") != index.get("metadata_sha256"):
                add_error(errors, f"{evidence_id}: superseded metadata hash differs from index")
            if metadata.get("status") != "SEALED" or metadata.get("capture_tool") != "android-cli":
                add_error(errors, f"{evidence_id}: superseded metadata is not sealed Android CLI evidence")
            for field in ("inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id"):
                if str(metadata.get(field, "")) != str(index.get(field, "")):
                    add_error(errors, f"{evidence_id}: superseded metadata {field} differs from index")
            env = env_by_id.get(index.get("env_id", ""), {})
            try:
                if screenshot_size != parse_resolution(str(env.get("resolution", ""))):
                    add_error(errors, f"{evidence_id}: superseded screenshot dimensions differ")
                verify_environment_probe(metadata.get("environment_verification"), env)
            except (ValueError, RuntimeError) as exc:
                add_error(errors, f"{evidence_id}: superseded {exc}")
            for field in ("device_serial", "application_id", "app_version", "app_build", "source_revision", "apk_sha256"):
                if metadata.get(field) != env.get(field):
                    add_error(errors, f"{evidence_id}: superseded metadata {field} differs from environment")
            if metadata.get("environment_registry_sha256") != phase_manifest.get("environment_registry_sha256"):
                add_error(errors, f"{evidence_id}: superseded environment digest differs")
            if metadata.get("scope_sha256") != phase_manifest.get("scope_sha256"):
                add_error(errors, f"{evidence_id}: superseded scope digest differs")
            try:
                _, attestation_digest = verify_environment_attestation(workspace, env, phase_manifest)
                if metadata.get("environment_attestation_sha256") != attestation_digest:
                    add_error(errors, f"{evidence_id}: superseded environment readiness attestation digest differs")
            except ValueError as exc:
                add_error(errors, f"{evidence_id}: superseded {exc}")
            commands = metadata.get("commands")
            if not isinstance(commands, list) or len(commands) < 3:
                add_error(errors, f"{evidence_id}: superseded command records are incomplete")
            else:
                for command in commands:
                    if isinstance(command, dict):
                        verify_command_record(command, f"{evidence_id}.superseded", errors)
                    else:
                        add_error(errors, f"{evidence_id}: malformed superseded command record")
            metadata_by_evidence[evidence_id] = metadata
        except (ValueError, OSError) as exc:
            add_error(errors, f"{evidence_id}: invalid superseded package: {exc}")

    successors: dict[str, list[str]] = {}
    for evidence_id, metadata in metadata_by_evidence.items():
        supersedes = metadata.get("supersedes_evidence_id", "")
        index_supersedes = index_by_id.get(evidence_id, {}).get("supersedes_evidence_id", "")
        if supersedes != index_supersedes:
            add_error(errors, f"{evidence_id}: supersedes link differs from index")
        if supersedes:
            successors.setdefault(supersedes, []).append(evidence_id)
            old = index_by_id.get(supersedes)
            if not old or old.get("status") != "SUPERSEDED":
                add_error(errors, f"{evidence_id}: superseded evidence is missing or active")
            elif any(old.get(field) != index_by_id[evidence_id].get(field) for field in ("feature_id", "page_id", "state_id", "env_id")):
                add_error(errors, f"{evidence_id}: superseded evidence has different core IDs")
    for evidence_id, index in index_by_id.items():
        if index.get("status") in {"SEALED", "ACCEPTED"} and evidence_id not in inventory_evidence:
            add_error(errors, f"Orphan sealed evidence: {evidence_id}")
        elif index.get("status") == "SUPERSEDED" and len(successors.get(evidence_id, [])) != 1:
            add_error(errors, f"Superseded evidence lacks one successor: {evidence_id}")
        elif index.get("status") not in ({"SEALED", "ACCEPTED", "SUPERSEDED"} if is_resuming_finalization else {"SEALED", "SUPERSEDED"}):
            add_error(errors, f"Invalid pre-closure evidence index status: {index.get('status')}")

    indexed_paths = {row.get("relative_path", "") for row in evidence_index_rows}
    actual_evidence_dirs = {
        path.relative_to(workspace).as_posix()
        for path in (workspace / "evidence").rglob("EVD-*")
        if path.is_dir()
    } if (workspace / "evidence").is_dir() else set()
    if indexed_paths != actual_evidence_dirs:
        add_error(errors, "Evidence directory set and evidence index are not identical")

    rework_ids: set[str] = set()
    open_rechecks = 0
    open_critical = 0
    for row in rechecks:
        rework_id = row.get("rework_id", "")
        if not rework_id or rework_id in rework_ids:
            add_error(errors, f"Missing or duplicate rework ID: {rework_id!r}")
        rework_ids.add(rework_id)
        status = row.get("status", "").upper()
        if status in {"CLOSED", "SUPERSEDED"}:
            resolution = row.get("resolution_evidence_id", "")
            if row.get("closed_by") != expected_reviewer or not row.get("resolved_at") or not resolution:
                add_error(errors, f"Closed rework lacks checker closure or resolution evidence: {rework_id}")
            resolution_index = index_by_id.get(resolution, {})
            if resolution == row.get("evidence_id"):
                add_error(errors, f"Rework resolution must use a new Evidence-ID: {rework_id}")
            if not resolution_index or resolution_index.get("status") not in ({"SEALED", "ACCEPTED"} if is_resuming_finalization else {"SEALED"}):
                add_error(errors, f"Rework resolution evidence is not active and sealed: {rework_id}")
            elif any(resolution_index.get(field) != row.get(field) for field in ("feature_id", "page_id", "state_id", "env_id")):
                add_error(errors, f"Rework resolution evidence has different core IDs: {rework_id}")
            elif resolution_index.get("captured_at", "") <= row.get("opened_at", ""):
                add_error(errors, f"Rework resolution evidence predates the recheck: {rework_id}")
        else:
            open_rechecks += 1
            if row.get("severity", "").upper() == "CRITICAL":
                open_critical += 1
    if open_rechecks:
        add_error(errors, f"Open rechecks: {open_rechecks}")
    if pending_count:
        add_error(errors, f"Inventory has pending or rework rows: {pending_count}")

    expected_features = set(phase_manifest.get("included_features", []))
    covered_features = {row.get("feature_id") for row in active_inventory_rows}
    if covered_features != expected_features:
        add_error(errors, "Active inventory does not cover all included Feature-IDs")

    effective_decision = "PASS" if not errors else "INCOMPLETE"
    if args.decision in {"INCOMPLETE", "BLOCKED"}:
        effective_decision = args.decision
    evidence_chain_closed = effective_decision == "PASS" and not errors
    reviewed_at = str(finalization.get("reviewed_at")) if is_resuming_finalization else utc_now()
    reviewed_inventory_ids: list[str] = []

    if evidence_chain_closed:
        if not is_resuming_finalization:
            finalization = {
                "status": "IN_PROGRESS",
                "reviewer_id": args.reviewer,
                "reviewed_at": reviewed_at,
            }
            atomic_json(finalization_path, finalization)
        for row in inventory_rows:
            if row.get("row_status") != "SUPERSEDED":
                row["row_status"] = "REVIEWED"
                row["reviewed_by"] = args.reviewer
                row["reviewed_at"] = reviewed_at
                reviewed_inventory_ids.append(row["inventory_id"])
        records_by_id = {str(row.get("inventory_id")): row for row in inventory_records}
        for row in inventory_rows:
            record = records_by_id.get(row["inventory_id"])
            if record is not None and row["row_status"] == "REVIEWED":
                record["row_status"] = "REVIEWED"
                record["reviewed_by"] = args.reviewer
                record["reviewed_at"] = reviewed_at
        write_csv(workspace / "inventory.csv", FIELDS, inventory_rows)
        atomic_json(workspace / "inventory.json", {"records": inventory_records})
        atomic_text(workspace / "inventory-manifest.sha256", manifest_lines(workspace, ["inventory.json", "inventory.csv"]))
        for row in asset_rows:
            row.update({
                "status": "REVIEWED", "reviewed_by": args.reviewer,
                "reviewed_at": reviewed_at,
            })
        write_csv(workspace / "asset-inventory.csv", ASSET_INVENTORY_FIELDS, asset_rows)
        for row in evidence_index_rows:
            if row.get("status") == "SEALED" and row.get("evidence_id") in inventory_evidence:
                row["status"] = "ACCEPTED"
        write_csv(workspace / "evidence-index.csv", INDEX_FIELDS, evidence_index_rows)
        write_csv(
            workspace / "acceptance-registry.csv",
            ACCEPTANCE_FIELDS,
            [
                {
                    "inventory_id": row["inventory_id"],
                    "evidence_id": row["evidence_id"],
                    "decision": "ACCEPTED",
                    "reviewed_by": args.reviewer,
                    "reviewed_at": reviewed_at,
                }
                for row in inventory_rows if row.get("row_status") == "REVIEWED"
            ],
        )
        write_csv(workspace / "evidence-anchors.snapshot.csv", ANCHOR_FIELDS, anchor_rows)
        phase_manifest.update({"status": "CLOSED", "closed_at": reviewed_at, "closed_by": args.reviewer})
        atomic_json(workspace / "phase-manifest.json", phase_manifest)
        atomic_json(
            finalization_path,
            {
                "status": "COMPLETE",
                "reviewer_id": args.reviewer,
                "reviewed_at": reviewed_at,
                "inventory_sha256": sha256_file(workspace / "inventory.csv"),
                "asset_inventory_sha256": sha256_file(workspace / "asset-inventory.csv"),
                "asset_package_manifest_sha256": sha256_file(workspace / "asset-package" / "manifest.sha256"),
                "evidence_index_sha256": sha256_file(workspace / "evidence-index.csv"),
                "acceptance_registry_sha256": sha256_file(workspace / "acceptance-registry.csv"),
                "phase_manifest_sha256": sha256_file(workspace / "phase-manifest.json"),
            },
        )

    report: dict[str, Any] = {
        "run_id": phase_manifest.get("run_id"),
        "final_verdict": effective_decision,
        "evidence_chain_closed": evidence_chain_closed,
        "reviewer_role": "coverage-checker-agent",
        "reviewer_id": args.reviewer,
        "reviewed_at": reviewed_at,
        "baseline_env_id": baseline_env_id,
        "scope_sha256": phase_manifest.get("scope_sha256"),
        "environment_registry_sha256": phase_manifest.get("environment_registry_sha256"),
        "evidence_anchor_snapshot_sha256": (
            sha256_file(workspace / "evidence-anchors.snapshot.csv")
            if evidence_chain_closed else None
        ),
        "attest_visual_review": args.attest_visual_review,
        "attest_source_runtime_crosscheck": args.attest_source_runtime_crosscheck,
        "requested_decision": args.decision,
        "decision_source": "DETERMINISTIC_PAGE_ADVANCED_AND_EVIDENCE_GATES",
        "page_gate_verdict": page_gate_report.get("machine_verdict"),
        "page_gate_pages": page_gate_report.get("pages", []),
        "advanced_gate_verdict": advanced_gate_report.get("machine_verdict"),
        "advanced_gate_required_observations": advanced_gate_report.get("required_observations", 0),
        "advanced_gate_received_observations": advanced_gate_report.get("received_observations", 0),
        "inventory_rows": len(inventory_rows),
        "archived_assets": len(asset_rows),
        "asset_inventory_sha256": (
            sha256_file(workspace / "asset-inventory.csv")
            if (workspace / "asset-inventory.csv").is_file() else None
        ),
        "asset_package_manifest_sha256": (
            sha256_file(workspace / "asset-package" / "manifest.sha256")
            if (workspace / "asset-package" / "manifest.sha256").is_file() else None
        ),
        "indexed_evidence": len(evidence_index_rows),
        "reviewed_inventory_ids": reviewed_inventory_ids,
        "covered_feature_ids": sorted(feature for feature in covered_features if feature),
        "open_rechecks": open_rechecks,
        "open_critical_rechecks": open_critical,
        "pending_confirmations": pending_count,
        "errors": errors,
        "warnings": warnings,
        "notes": args.notes,
        "closure_manifest_sha256": None,
    }
    if evidence_chain_closed:
        snapshot = closure_manifest_text(workspace)
        report["closure_manifest_sha256"] = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
        atomic_text(workspace / "closure-manifest.sha256", snapshot)
        atomic_json(workspace / "closure-report.json", report)
        atomic_text(workspace / "CLOSED", sha256_file(workspace / "closure-report.json") + "\n")
    else:
        atomic_json(workspace / "closure-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if evidence_chain_closed else 1


if __name__ == "__main__":
    raise SystemExit(main())
