#!/usr/bin/env python3
"""Build a normalized one-state-per-row inventory from JSON/JSONL claims."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _common import (
    assert_no_symlink,
    atomic_json,
    atomic_text,
    load_json,
    manifest_lines,
    read_csv,
    safe_workspace_path,
    sha256_file,
    validate_id,
    verify_phase_identity,
    write_csv,
)


FIELDS = [
    "inventory_id", "feature_id", "feature_name", "page_id", "page_name", "state_id",
    "state_name", "env_id", "evidence_id", "entry_condition", "transition_from_state_id",
    "predecessor_evidence_id", "action_summary", "expected_observable", "actual_observable",
    "code_refs", "business_rule_refs", "data_dependency_refs", "system_capability_refs",
    "third_party_dependency_refs", "asset_ids", "responsible_agent", "row_status", "rework_id",
    "reviewed_by", "reviewed_at",
]
REQUIRED = [
    "inventory_id", "feature_id", "feature_name", "page_id", "page_name", "state_id",
    "state_name", "env_id", "evidence_id", "entry_condition", "action_summary",
    "expected_observable", "actual_observable", "responsible_agent", "row_status",
]
REF_FIELDS = [
    "code_refs", "business_rule_refs", "data_dependency_refs", "system_capability_refs",
    "third_party_dependency_refs", "asset_ids",
]
STATUS = {"CAPTURED", "PENDING_CONFIRMATION", "REWORK", "SUPERSEDED"}


def read_claim_file(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        rows = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL at {path}:{number}: {exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"Claim must be an object at {path}:{number}")
            rows.append(value)
        return rows
    value = load_json(path)
    if isinstance(value, dict) and isinstance(value.get("records"), list):
        value = value["records"]
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError(f"Claim JSON must be an object, list, or records object: {path}")
    return value


def collect_claims(path: Path) -> list[dict[str, Any]]:
    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = sorted(
            candidate for candidate in path.rglob("*")
            if candidate.is_file() and candidate.suffix in {".json", ".jsonl"}
        )
    else:
        raise ValueError(f"Claims path does not exist: {path}")
    claims = []
    for file in files:
        claims.extend(read_claim_file(file))
    if not claims:
        raise ValueError("No inventory claims were found")
    return claims


def normalize_claim(raw: dict[str, Any]) -> dict[str, Any]:
    unknown = set(raw) - set(FIELDS)
    if unknown:
        raise ValueError(f"Unknown claim fields: {sorted(unknown)}")
    row = {field: raw.get(field, "") for field in FIELDS}
    for field in REQUIRED:
        if row[field] in (None, ""):
            raise ValueError(f"Missing required field {field}")
    for field in ("inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id"):
        validate_id(str(row[field]), field)
    if row["row_status"] not in STATUS:
        raise ValueError(f"Invalid row_status: {row['row_status']}")
    has_previous_state = bool(row["transition_from_state_id"])
    has_previous_evidence = bool(row["predecessor_evidence_id"])
    if has_previous_state != has_previous_evidence:
        raise ValueError("Transitions require both transition_from_state_id and predecessor_evidence_id")
    if has_previous_state:
        validate_id(str(row["transition_from_state_id"]), "transition_from_state_id")
        validate_id(str(row["predecessor_evidence_id"]), "predecessor_evidence_id")
    for field in REF_FIELDS:
        value = row[field]
        if value == "":
            row[field] = []
        elif not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"{field} must be an array of strings")
    if row["row_status"] == "CAPTURED" and not row["code_refs"]:
        raise ValueError("CAPTURED rows require at least one concrete code_refs path:line")
    asset_ids = row["asset_ids"]
    if not asset_ids:
        raise ValueError("Every inventory row requires asset_ids or the NONE_FOUND sentinel")
    if asset_ids != sorted(set(asset_ids)):
        raise ValueError("asset_ids must be sorted and contain no duplicates")
    if "NONE_FOUND" in asset_ids and asset_ids != ["NONE_FOUND"]:
        raise ValueError("NONE_FOUND cannot be mixed with real Asset-IDs")
    for asset_id in asset_ids:
        validate_id(asset_id, "Asset-ID")
    if row["reviewed_by"] or row["reviewed_at"]:
        raise ValueError("Claim authors cannot pre-fill final review fields")
    for field in FIELDS:
        if field not in REF_FIELDS and row[field] is None:
            row[field] = ""
        if field not in REF_FIELDS and not isinstance(row[field], str):
            raise ValueError(f"{field} must be a string")
    return row


def verify_code_ref(project_root: Path, reference: str) -> None:
    if ":" not in reference:
        raise ValueError(f"Code reference must use path:line: {reference}")
    relative_value, line_value = reference.rsplit(":", 1)
    try:
        line_number = int(line_value)
    except ValueError as exc:
        raise ValueError(f"Code reference has an invalid line number: {reference}") from exc
    relative = Path(relative_value)
    if relative.is_absolute() or ".." in relative.parts or line_number < 1:
        raise ValueError(f"Unsafe code reference: {reference}")
    path = (project_root / relative).resolve()
    try:
        path.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"Code reference escapes Android project: {reference}") from exc
    if not path.is_file():
        raise ValueError(f"Code reference file does not exist: {reference}")
    line_count = sum(1 for _ in path.open("r", encoding="utf-8", errors="replace"))
    if line_number > line_count:
        raise ValueError(f"Code reference line is outside the file: {reference}")


def verify_frozen_workspace(workspace: Path, manifest: dict[str, Any]) -> None:
    if sha256_file(workspace / "environments.json") != manifest.get("environment_registry_sha256"):
        raise ValueError("Frozen environment registry changed")
    verify_phase_identity(workspace, manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--claims", required=True)
    args = parser.parse_args()

    workspace_input = Path(args.workspace).expanduser().absolute()
    if workspace_input.is_symlink():
        parser.error("Workspace must not be a symbolic link")
    workspace = workspace_input.resolve()
    claims_path = Path(args.claims).expanduser().resolve()
    if not (workspace / "phase-manifest.json").is_file():
        parser.error("Not an initialized Android inventory workspace")
    if (workspace / "CLOSED").exists():
        parser.error("Phase 2 is CLOSED; inventory is read-only")

    try:
        phase_manifest = load_json(workspace / "phase-manifest.json")
        verify_frozen_workspace(workspace, phase_manifest)
        rows = [normalize_claim(raw) for raw in collect_claims(claims_path)]
        index_rows = read_csv(workspace / "evidence-index.csv")
        if len({row["evidence_id"] for row in index_rows}) != len(index_rows):
            raise ValueError("Evidence index contains duplicate Evidence-IDs")
        evidence_index = {row["evidence_id"]: row for row in index_rows}
        environments = load_json(workspace / "environments.json")
        env_ids = {env["env_id"] for env in environments.get("environments", [])}
        included_features = set(phase_manifest.get("included_features", []))
        project_root = Path(phase_manifest["android_project_root"]).resolve()
        inventory_ids: set[str] = set()
        evidence_ids: set[str] = set()
        row_keys: set[tuple[str, str, str, str]] = set()
        for row in rows:
            inventory_id = row["inventory_id"]
            evidence_id = row["evidence_id"]
            key = (row["feature_id"], row["page_id"], row["state_id"], row["env_id"])
            if inventory_id in inventory_ids:
                raise ValueError(f"Duplicate Inventory-ID: {inventory_id}")
            if evidence_id in evidence_ids:
                raise ValueError(f"One Evidence-ID cannot prove multiple rows: {evidence_id}")
            if key in row_keys:
                raise ValueError(f"Duplicate feature/page/state/environment row: {key}")
            inventory_ids.add(inventory_id)
            evidence_ids.add(evidence_id)
            row_keys.add(key)
            if row["env_id"] not in env_ids:
                raise ValueError(f"Unknown ENV-ID in claim: {row['env_id']}")
            if row["feature_id"] not in included_features:
                raise ValueError(f"Claim is outside the included feature scope: {row['feature_id']}")
            if row["responsible_agent"] not in phase_manifest.get("ownership", {}).get("runtime_state_agent_ids", []):
                raise ValueError(f"Claim owner is not a frozen runtime-state agent: {row['responsible_agent']}")
            for reference in row["code_refs"]:
                verify_code_ref(project_root, reference)
            index = evidence_index.get(evidence_id)
            expected_status = "SUPERSEDED" if row["row_status"] == "SUPERSEDED" else "SEALED"
            if not index or index.get("status") != expected_status:
                raise ValueError(f"Evidence is not sealed and indexed: {evidence_id}")
            for field in ("inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id"):
                if index.get(field, "") != row[field]:
                    raise ValueError(f"{evidence_id}: evidence index {field} does not match claim")
            expected_relative = f"evidence/{row['env_id']}/{row['page_id']}/{row['state_id']}/{evidence_id}"
            if index.get("relative_path") != expected_relative:
                raise ValueError(f"{evidence_id}: evidence index path is not canonical")
            evidence_path = safe_workspace_path(workspace, index["relative_path"])
            assert_no_symlink(evidence_path, workspace)
            if not (evidence_path / "COMMITTED").is_file():
                raise ValueError(f"Evidence is not committed: {evidence_id}")
            metadata = load_json(evidence_path / "metadata.json")
            if metadata.get("status") != "SEALED":
                raise ValueError(f"Evidence metadata is not SEALED: {evidence_id}")
            for field in ("inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id"):
                if str(metadata.get(field, "")) != str(row[field]):
                    raise ValueError(f"{evidence_id}: metadata {field} does not match claim")
            if row["predecessor_evidence_id"] != metadata.get("predecessor_evidence_id", ""):
                raise ValueError(f"{evidence_id}: predecessor evidence does not match metadata")
            if row["predecessor_evidence_id"]:
                predecessor_index = evidence_index.get(row["predecessor_evidence_id"])
                if not predecessor_index:
                    raise ValueError(f"Missing predecessor Evidence-ID: {row['predecessor_evidence_id']}")
                predecessor_path = safe_workspace_path(workspace, predecessor_index["relative_path"])
                predecessor_metadata = load_json(predecessor_path / "metadata.json")
                if predecessor_metadata.get("state_id") != row["transition_from_state_id"]:
                    raise ValueError(f"{evidence_id}: transition state does not match predecessor metadata")
                if predecessor_metadata.get("feature_id") != row["feature_id"]:
                    raise ValueError(f"{evidence_id}: predecessor belongs to another feature")
                if predecessor_metadata.get("env_id") != row["env_id"]:
                    raise ValueError(f"{evidence_id}: predecessor belongs to another environment")
    except ValueError as exc:
        parser.error(str(exc))

    rows.sort(key=lambda row: (row["feature_id"], row["page_id"], row["state_id"], row["env_id"], row["inventory_id"]))
    csv_rows = []
    for row in rows:
        csv_row = dict(row)
        for field in REF_FIELDS:
            csv_row[field] = json.dumps(row[field], ensure_ascii=False, separators=(",", ":"))
        csv_rows.append(csv_row)
    write_csv(workspace / "inventory.csv", FIELDS, csv_rows)
    atomic_json(workspace / "inventory.json", {"records": rows})
    atomic_text(
        workspace / "inventory-manifest.sha256",
        manifest_lines(workspace, ["inventory.json", "inventory.csv"]),
    )
    print(json.dumps({"rows": len(rows), "inventory": str(workspace / 'inventory.csv')}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
