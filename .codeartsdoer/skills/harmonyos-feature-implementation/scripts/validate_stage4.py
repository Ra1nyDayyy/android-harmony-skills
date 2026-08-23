#!/usr/bin/env python3
"""Independently validate and seal governed Phase 4."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from _common import (
    atomic_json,
    atomic_text,
    build_project_snapshot,
    frozen_category_contracts,
    load_json,
    make_tree_read_only,
    read_csv,
    safe_relative_path,
    sha256_file,
    split_multi,
    utc_now,
    validate_actor,
    validate_id,
)
from _stage4_audit import (
    closure_manifest_text,
    geometry_matches,
    indexed,
    json_string_array,
    package_summary,
    require_read_only,
    scan_project,
    validate_hbuild,
    validate_hevd,
    validate_source_ref,
    verify_android_package,
    verify_sealed_package,
    verify_upstream_closure,
)


PHASE_NAME = "phase-04-harmony-implementation"
ROLE_KEYS = {
    "implementation_lead_id",
    "visual_asset_agent_id",
    "verification_executor_id",
    "parity_acceptance_agent_id",
}
INPUT_LOCK_KEYS = {
    "schema_version", "stage", "run_id", "created_at", "locked_by",
    "work_order_id", "work_order_sha256", "ownership",
    "controller_gate3_snapshot_sha256", "phase3_work_order_id",
    "phase3_work_order_sha256", "inputs", "android_evidence",
    "phase2_asset_files", "h4envs", "asset_conversion_contracts_sha256",
    "migration_unit_contracts_sha256",
    "phase2_inventory_ids", "phase2_asset_ids", "required_h4env_ids",
    "phase3_source_snapshot_sha256",
}
INPUT_RECORD_KEYS = {"label", "source_path", "snapshot_path", "sha256", "size"}
ANDROID_RECORD_KEYS = {
    "evidence_id", "inventory_id", "source_path", "snapshot_path",
    "manifest_sha256", "metadata_sha256", "screenshot_sha256",
    "layout_sha256", "sha256", "size", "file_count",
}
ASSET_LOCK_KEYS = {"asset_id", "source_path", "snapshot_path", "sha256", "size"}
H4ENV_LOCK_KEYS = {
    "h4env_id", "source_android_env_id", "base_henv_id", "device_id",
    "relative_path", "sha256",
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
FEATURE_ORDER_KEYS = {
    "schema_version", "work_order_id", "run_id", "phase", "feature_id",
    "status", "issued_at", "issued_by", "phase4_manifest_sha256",
    "stage04_input_lock_sha256", "ownership", "visual_asset_agent_id",
    "source_inventory_ids", "parity_ids", "harmony_module_ids", "targets",
    "required_h4env_ids", "asset_ids", "capability_requirement_ids",
    "capability_contract_ids", "exclusive_code_paths", "completion_conditions",
}
FEATURE_ACTOR_KEYS = {
    "feature_owner_id", "ui_agent_id", "business_data_agent_id",
    "native_capability_agent_id",
}
HREV_KEYS = {
    "parity_id", "visual_result", "functional_result", "asset_result",
    "reviewed_visual_element_ids", "differences", "notes", "review_id",
    "inventory_id", "android_evidence_id", "harmony_evidence_id",
    "android_manifest_sha256", "android_screenshot_sha256",
    "android_layout_sha256", "harmony_manifest_sha256",
    "harmony_screenshot_sha256", "harmony_ui_tree_sha256",
    "harmony_assertions_sha256", "reviewer_id", "reviewed_at", "decision",
    "attestations",
}
HREV_ATTESTATION_KEYS = {
    "opened_both_screenshots", "functional_results", "asset_provenance",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PLACEHOLDER_RE = re.compile(
    r"__(?:FILL|AUTO)(?:_[A-Z0-9_]+)?__|\b(?:TBD|PENDING_CONFIRMATION|MOCK_ONLY|STUB_ONLY|FAKE_DATA)\b"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def object_json(path: Path, label: str) -> dict[str, Any]:
    value = load_json(path)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def rows_by(path: Path, key: str, label: str) -> dict[str, dict[str, str]]:
    errors: list[str] = []
    result = indexed(read_csv(path), key, label, errors)
    if errors:
        raise ValueError("; ".join(errors))
    return result


ATTEMPT_LEDGER_FIELDS = [
    "execution_id", "parity_id", "evidence_id", "started_at", "executed_by",
    "previous_chain_sha256", "chain_sha256",
]


def validate_attempt_ledgers(workspace: Path) -> None:
    local_rows = read_csv(workspace / "attempt-ledger.csv")
    controller_rows = read_csv(workspace.parent / "controller" / "phase4-attempt-ledger.csv")
    require(local_rows == controller_rows, "Phase 4 attempt ledger differs from controller anchor")
    previous = "0" * 64
    seen_ids: set[str] = set()
    seen_evidence: set[str] = set()
    counts: dict[str, int] = {}
    for row in controller_rows:
        material = {field: row.get(field, "") for field in ATTEMPT_LEDGER_FIELDS[:-1]}
        expected = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        require(
            set(row) == set(ATTEMPT_LEDGER_FIELDS)
            and bool(row.get("execution_id")) and row["execution_id"] not in seen_ids
            and bool(row.get("evidence_id")) and row["evidence_id"] not in seen_evidence
            and row.get("previous_chain_sha256") == previous
            and row.get("chain_sha256") == expected,
            "Phase 4 attempt ledger hash chain or identity differs",
        )
        seen_ids.add(row["execution_id"])
        seen_evidence.add(row["evidence_id"])
        parity_id = row.get("parity_id", "")
        counts[parity_id] = counts.get(parity_id, 0) + 1
        previous = expected
    require(all(count <= 3 for count in counts.values()),
            "Phase 4 automatic execution budget exceeds initial attempt plus two repairs")
    evidence_ids = {row.get("evidence_id", "") for row in read_csv(workspace / "evidence-index.csv")}
    require(evidence_ids <= seen_evidence, "Phase 4 evidence index contains an unanchored execution")
    for evidence_id in seen_evidence - evidence_ids:
        require((workspace / "attempts" / f"ATT-{evidence_id}.json").is_file(),
                f"Anchored execution lacks evidence or failure package: {evidence_id}")


def require_csv_header(path: Path, expected: list[str], label: str) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        actual = next(csv.reader(handle), [])
    require(actual == expected, f"{label} header differs")


def canonical_string_list(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    require(
        isinstance(value, list)
        and (allow_empty or bool(value))
        and all(isinstance(item, str) and item for item in value),
        f"{label} must be a {'possibly empty ' if allow_empty else 'nonempty '}string array",
    )
    result = list(value)
    require(result == sorted(set(result)), f"{label} must be sorted and unique")
    return result


def actor_ids(value: Any) -> set[str]:
    result: set[str] = set()
    if not isinstance(value, dict):
        return result
    for item in value.values():
        if isinstance(item, str) and item:
            result.add(item)
        elif isinstance(item, list):
            result.update(entry for entry in item if isinstance(entry, str) and entry)
    return result


def source_row_key(row: dict[str, str]) -> str:
    material = "|".join(
        str(row.get(field, ""))
        for field in ("feature_id", "page_id", "state_id", "env_id", "evidence_id")
    )
    require(all(material.split("|")), f"Inventory row lacks source identity: {row.get('inventory_id', '')}")
    return "SROW-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20].upper()


def carrier_for(page: dict[str, Any], mapping_type: str) -> str:
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


def scaffold_carrier(mapping_type: str, surface_kind: str) -> str:
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


def object_array(path: Path, field: str, label: str) -> list[dict[str, Any]]:
    value = object_json(path, label)
    rows = value.get(field)
    require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows),
            f"{label}.{field} must be an object array")
    return rows


def validate_migration_unit_contracts(
    workspace: Path,
    inventory: dict[str, dict[str, str]],
    architecture: dict[str, dict[str, str]],
    parity: dict[str, dict[str, str]],
    context: dict[str, Any],
    input_lock: dict[str, Any],
    manifest: dict[str, Any],
) -> None:
    """Recompute every immutable Android-to-Harmony observable contract."""
    phase2: Path = context["phase2"]
    phase3: Path = context["phase3"]
    pages = {str(row.get("page_id", "")): row for row in object_array(
        phase2 / "static-analysis" / "pages.json", "pages", "Phase 2 pages"
    )}
    components = object_array(
        phase2 / "static-analysis" / "components.json", "components", "Phase 2 components"
    )
    events = object_array(
        phase2 / "static-analysis" / "events.json", "events", "Phase 2 events"
    )
    transitions = object_array(
        phase2 / "static-analysis" / "transitions.json", "transitions", "Phase 2 transitions"
    )
    observations = object_array(
        phase2 / "runtime-observations.json", "observations", "Phase 2 runtime observations"
    )
    inventory_by_evidence = {str(row.get("evidence_id", "")): row for row in inventory.values()}
    observed_by_state: dict[tuple[str, str, str], dict[str, set[str]]] = {}
    bucket_by_type = {"COMPONENT": "components", "EVENT": "events", "TRANSITION": "transitions"}
    for observation in observations:
        bucket = bucket_by_type.get(str(observation.get("subject_type", "")))
        if not bucket:
            continue
        source = inventory_by_evidence.get(str(observation.get("after_evidence_id", "")))
        require(source is not None, "A Phase 2 runtime observation is not bound to active evidence")
        require(
            observation.get("page_id") == source.get("page_id")
            and observation.get("env_id") == source.get("env_id"),
            "A Phase 2 runtime observation differs from its state evidence",
        )
        key = (str(source["page_id"]), str(source["state_id"]), str(source["env_id"]))
        observed_by_state.setdefault(
            key, {"components": set(), "events": set(), "transitions": set()}
        )[bucket].add(str(observation.get("subject_id", "")))
    obligation_doc = object_json(phase3 / "advanced-obligations.json", "Phase 3 obligations")
    obligations = obligation_doc.get("obligations")
    require(isinstance(obligations, list) and all(isinstance(row, dict) for row in obligations),
            "Phase 3 obligations must be an object array")
    contract_path = workspace / "migration-unit-contracts.json"
    contract_sha = sha256_file(contract_path)
    require(
        input_lock.get("migration_unit_contracts_sha256") == contract_sha
        and manifest.get("migration_unit_contracts_sha256") == contract_sha,
        "Migration-unit contracts are not bound by the Phase 4 input lock and manifest",
    )
    contract_doc = object_json(contract_path, "migration-unit contracts")
    units = contract_doc.get("units")
    require(contract_doc.get("schema_version") == 1 and isinstance(units, list)
            and all(isinstance(row, dict) for row in units),
            "Migration-unit contract schema differs")
    unit_by_parity = {str(row.get("parity_id", "")): row for row in units}
    require(len(unit_by_parity) == len(units) and set(unit_by_parity) == set(parity),
            "Migration-unit contracts do not exactly cover the parity map")
    surfaces: dict[str, dict[str, str]] = context["surfaces"]
    for parity_id, parity_row in parity.items():
        source = inventory.get(str(parity_row.get("inventory_id", "")))
        require(source is not None, f"Migration unit references unknown inventory: {parity_id}")
        mapping = architecture[source_row_key(source)]
        page_id = str(source.get("page_id", ""))
        page = pages.get(page_id)
        require(page is not None, f"Migration unit page is absent from static analysis: {page_id}")
        mapping_type = str(mapping.get("mapping_type", ""))
        target_id = str(
            mapping.get("route_id", "") if mapping_type == "ROUTE_PAGE"
            else mapping.get("surface_shell_id", "")
        )
        carrier = carrier_for(page, mapping_type)
        surface_kind = str(surfaces.get(str(mapping.get("surface_shell_id", "")), {}).get(
            "surface_kind", ""
        ))
        applicable = sorted(
            [
                row for row in obligations
                if source.get("feature_id") in row.get("candidate_feature_ids", [])
                and (not str(row.get("page_id", "")) or row.get("page_id") == page_id)
            ],
            key=lambda row: str(row.get("subject_id", "")),
        )
        state_subjects = observed_by_state.get(
            (page_id, str(source["state_id"]), str(source["env_id"])),
            {"components": set(), "events": set(), "transitions": set()},
        )
        expected = {
            "migration_unit_id": "MUNIT-" + hashlib.sha256(parity_id.encode("utf-8")).hexdigest()[:20].upper(),
            "parity_id": parity_id,
            "inventory_id": source["inventory_id"],
            "feature_id": source["feature_id"],
            "page_id": page_id,
            "state_id": source["state_id"],
            "h4env_id": parity_row["h4env_id"],
            "android_entry_condition": source["entry_condition"],
            "android_action_summary": source["action_summary"],
            "android_expected_observable": source["expected_observable"],
            "required_business_rule_ids": sorted(set(split_multi(source["business_rule_refs"]))),
            "required_data_dependency_ids": sorted(set(split_multi(source["data_dependency_refs"]))),
            "required_system_capability_ids": sorted(set(split_multi(source["system_capability_refs"]))),
            "required_third_party_dependency_ids": sorted(set(split_multi(source["third_party_dependency_refs"]))),
            "expected_carrier": carrier,
            "target_kind": mapping_type,
            "target_id": target_id,
            "scaffold_carrier": scaffold_carrier(mapping_type, surface_kind),
            "page_component_ids": sorted({str(row["component_id"]) for row in components if row.get("page_id") == page_id}),
            "page_event_ids": sorted({str(row["event_id"]) for row in events if row.get("page_id") == page_id}),
            "page_transition_ids": sorted({str(row["transition_id"]) for row in transitions if row.get("source_page_id") == page_id}),
            "required_component_ids": sorted(state_subjects["components"]),
            "component_locators": {
                str(row["component_id"]): {
                    "resource_id": str(row.get("resource_id", "")),
                    "text": str(row.get("text", "")),
                    "type": str(row.get("type", "")),
                }
                for row in components if row.get("page_id") == page_id
            },
            "required_event_ids": sorted(state_subjects["events"]),
            "required_transition_ids": sorted(state_subjects["transitions"]),
            "state_binding_basis": "PHASE2_AFTER_EVIDENCE",
            "required_obligation_ids": [str(row["subject_id"]) for row in applicable],
            "required_obligation_types": {
                str(row["subject_id"]): str(row.get("subject_type", "")) for row in applicable
            },
            "simplification_policy": "FORBIDDEN",
            "native_optimization_policy": "INTERNAL_ONLY_UNLESS_APPROVED",
            "max_automatic_repair_attempts": 2,
        }
        require(unit_by_parity[parity_id] == expected,
                f"Migration-unit observable contract differs from frozen Android facts: {parity_id}")
        require(carrier == expected["scaffold_carrier"],
                f"Phase 3 carrier changes Android semantics: {parity_id}")


def ensure_no_mp4_or_placeholders(workspace: Path) -> None:
    text_suffixes = {".json", ".json5", ".csv", ".md", ".txt", ".ets", ".ts", ".js", ".yaml", ".yml"}
    for path in workspace.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in Phase 4: {path}")
        if not path.is_file():
            continue
        if path.suffix.lower() == ".mp4":
            raise ValueError(f"MP4 is prohibited in Phase 4: {path}")
        if path.name == "asset-policy.json":
            continue
        if path.suffix.lower() in text_suffixes and not any(
            part in {"logs", "attempts", ".staging"} for part in path.parts
        ):
            text = path.read_text(encoding="utf-8", errors="replace")
            if PLACEHOLDER_RE.search(text):
                raise ValueError(f"Unresolved placeholder remains in Phase 4: {path}")


def validate_upstream_and_work_order(
    workspace: Path,
) -> tuple[
    Path, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, str],
    dict[str, dict[str, str]], dict[str, dict[str, str]], dict[str, dict[str, str]],
    dict[str, dict[str, str]], dict[str, dict[str, str]], dict[str, Any],
]:
    run_dir = workspace.parent
    require(workspace.name == PHASE_NAME and workspace.is_dir() and not workspace.is_symlink(),
            f"Workspace must be the canonical {PHASE_NAME} directory")
    controller = run_dir / "controller"
    phase2 = run_dir / "phase-02-android-inventory"
    phase3 = run_dir / "phase-03-harmony-scaffold"
    scope = object_json(controller / "scope.json", "controller scope")
    manifest = object_json(workspace / "phase-manifest.json", "Phase 4 manifest")
    input_lock = object_json(workspace / "stage-04-input-lock.json", "Phase 4 input lock")
    require(set(input_lock) == INPUT_LOCK_KEYS, "Phase 4 input-lock root keys differ")
    require(input_lock.get("schema_version") == "1.0" and input_lock.get("stage") == 4,
            "Phase 4 input-lock schema/stage differs")
    require(manifest.get("phase") == 4 and manifest.get("status") == "IN_PROGRESS",
            "Phase 4 manifest is not IN_PROGRESS")
    require(manifest.get("run_id") == scope.get("run_id") == input_lock.get("run_id"),
            "Phase 4 run identity differs from controller scope")

    ownership = input_lock.get("ownership")
    require(isinstance(ownership, dict) and set(ownership) == ROLE_KEYS,
            "Phase 4 ownership must contain exactly four roles")
    role_values = [validate_actor(str(ownership[key]), key) for key in sorted(ROLE_KEYS)]
    require(len(role_values) == len(set(role_values)), "Phase 4 governance roles must be distinct")
    require(
        manifest.get("ownership") == ownership
        and input_lock.get("locked_by") == ownership["implementation_lead_id"],
        "Phase 4 manifest/input-lock ownership differs",
    )
    input_lock_path = workspace / "stage-04-input-lock.json"
    input_lock_sha = sha256_file(input_lock_path)
    require(manifest.get("input_lock_sha256") == input_lock_sha,
            "Phase 4 manifest no longer binds the input lock")

    registry = read_csv(controller / "work-order-registry.csv")
    work_order_id = validate_id(str(input_lock.get("work_order_id", "")), "Phase 4 Work-Order-ID")
    active = [
        row for row in registry
        if row.get("phase") == "4" and row.get("status", "").upper() != "SUPERSEDED"
    ]
    require(len(active) == 1 and active[0].get("work_order_id") == work_order_id,
            "Controller must have exactly one active Phase 4 work order")
    registry_row = active[0]
    expected_relative = f"controller/work-orders/{work_order_id}.json"
    require(registry_row.get("relative_path") == expected_relative and registry_row.get("status") == "ISSUED",
            "Registered Phase 4 work-order path/status differs")
    work_order_path = safe_relative_path(run_dir, expected_relative, "Phase 4 work order")
    work_order = object_json(work_order_path, "Phase 4 work order")
    work_order_sha = sha256_file(work_order_path)
    scope_sha = sha256_file(controller / "scope.json")
    require(
        registry_row.get("work_order_sha256") == work_order_sha
        and registry_row.get("scope_sha256") == scope_sha
        and registry_row.get("issued_by") == scope.get("ownership", {}).get("migration_controller_id"),
        "Registered Phase 4 work order hash/scope/issuer differs",
    )
    require(
        work_order.get("work_order_id") == work_order_id
        and work_order.get("phase") == 4
        and work_order.get("status") == "ISSUED"
        and work_order.get("run_id") == scope.get("run_id")
        and work_order.get("scope_sha256") == scope_sha
        and work_order.get("ownership") == ownership
        and work_order.get("required_skill") == "harmonyos-feature-implementation"
        and work_order.get("issued_by") == scope.get("ownership", {}).get("migration_controller_id")
        and work_order.get("included_features") == scope.get("migration_scope", {}).get("included_features")
        and work_order.get("excluded_features") == scope.get("migration_scope", {}).get("excluded_features")
        and work_order.get("mp4_allowed") is False,
        "Phase 4 work-order identity, scope, ownership, or policy differs",
    )
    require(
        manifest.get("work_order_id") == work_order_id
        and manifest.get("work_order_sha256") == work_order_sha
        and input_lock.get("work_order_sha256") == work_order_sha,
        "Phase 4 manifest/input-lock work-order binding differs",
    )

    upstream_relative = str(work_order.get("upstream_phase3_work_order_relative_path", ""))
    phase3_order_path = safe_relative_path(run_dir, upstream_relative, "upstream Phase 3 work order")
    phase3_order = object_json(phase3_order_path, "upstream Phase 3 work order")
    require(
        phase3_order.get("phase") == 3
        and phase3_order.get("work_order_id") == work_order.get("upstream_phase3_work_order_id")
        and sha256_file(phase3_order_path) == work_order.get("upstream_phase3_work_order_sha256")
        and input_lock.get("phase3_work_order_id") == phase3_order.get("work_order_id")
        and input_lock.get("phase3_work_order_sha256") == sha256_file(phase3_order_path),
        "Phase 4 is not bound to the frozen Phase 3 work order",
    )
    prior_actors = actor_ids(scope.get("ownership")) | actor_ids(phase3_order.get("ownership"))
    require(not (set(role_values) & prior_actors), "Phase 4 governance actors reuse Phase 1-3 actors")

    gate_relative = str(work_order.get("controller_gate3_snapshot_relative_path", ""))
    gate_path = safe_relative_path(run_dir, gate_relative, "controller Gate 3 snapshot")
    require(
        gate_relative == f"controller/work-orders/{work_order_id}.phase-03-gate-report.json"
        and sha256_file(gate_path) == work_order.get("controller_gate3_sha256")
        and input_lock.get("controller_gate3_snapshot_sha256") == sha256_file(gate_path),
        "Controller Gate 3 snapshot differs from the Phase 4 lock",
    )
    gate = object_json(gate_path, "controller Gate 3 snapshot")
    require(gate.get("phase") == 3 and gate.get("verdict") == "PASS" and not gate.get("errors"),
            "Frozen controller Gate 3 is not a complete PASS")

    verify_upstream_closure(
        phase2, "closure-report.json", "closure-manifest.sha256",
        {"closure-report.json", "closure-manifest.sha256", "CLOSED"},
        {".locks", ".staging"},
    )
    verify_upstream_closure(
        phase3, "stage-03-gate-report.json", "stage-03-closure-manifest.sha256",
        {"stage-03-gate-report.json", "stage-03-closure-manifest.sha256", "CLOSED"},
    )

    expected_sources: dict[Path, str] = {
        (controller / "scope.json").resolve(): scope_sha,
        work_order_path.resolve(): work_order_sha,
        gate_path.resolve(): str(work_order["controller_gate3_sha256"]),
        phase3_order_path.resolve(): str(work_order["upstream_phase3_work_order_sha256"]),
    }
    for digest_key, relative in STAGE4_INPUT_RELATIVES.items():
        relative_key = digest_key.removesuffix("_sha256") + "_relative_path"
        require(work_order.get(relative_key) == relative,
                f"Phase 4 work order has noncanonical {relative_key}")
        source = safe_relative_path(run_dir, relative, digest_key)
        digest = str(work_order.get(digest_key, ""))
        require(SHA256_RE.fullmatch(digest) is not None and sha256_file(source) == digest,
                f"Frozen upstream input changed: {digest_key}")
        expected_sources[source.resolve()] = digest
    henv_records = work_order.get("phase3_henvs")
    require(isinstance(henv_records, list) and henv_records, "Phase 4 work order lacks Phase 3 HENVs")
    phase3_henvs: dict[str, dict[str, str]] = {}
    for raw in henv_records:
        require(isinstance(raw, dict), "Phase 3 HENV work-order record must be an object")
        henv_id = validate_id(str(raw.get("henv_id", "")), "Phase 3 HENV-ID")
        require(henv_id not in phase3_henvs, f"Duplicate Phase 3 HENV-ID: {henv_id}")
        relative = f"phase-03-harmony-scaffold/environments/{henv_id}/harmony-environment.json"
        require(raw.get("relative_path") == relative, f"Noncanonical Phase 3 HENV path: {henv_id}")
        source = safe_relative_path(run_dir, relative, f"Phase 3 HENV {henv_id}")
        digest = str(raw.get("sha256", ""))
        require(SHA256_RE.fullmatch(digest) is not None and sha256_file(source) == digest,
                f"Phase 3 HENV changed: {henv_id}")
        phase3_henvs[henv_id] = {"relative_path": relative, "sha256": digest}
        expected_sources[source.resolve()] = digest

    raw_inputs = input_lock.get("inputs")
    require(isinstance(raw_inputs, list), "Phase 4 input-lock inputs must be an array")
    seen_sources: dict[Path, dict[str, Any]] = {}
    seen_snapshots: set[Path] = set()
    labels: set[str] = set()
    upstream_root = (workspace / "inputs" / "upstream").resolve()
    for record in raw_inputs:
        require(isinstance(record, dict) and set(record) == INPUT_RECORD_KEYS,
                "Phase 4 small-input record keys differ")
        label = str(record.get("label", ""))
        require(label and label not in labels, f"Empty/duplicate small-input label: {label!r}")
        source_value = Path(str(record.get("source_path", ""))).expanduser()
        snapshot_value = Path(str(record.get("snapshot_path", ""))).expanduser()
        require(source_value.is_absolute() and snapshot_value.is_absolute(),
                "Small-input source/snapshot paths must be absolute")
        source = source_value.resolve()
        snapshot = snapshot_value.resolve()
        source.relative_to(run_dir)
        snapshot.relative_to(upstream_root)
        require(
            source not in seen_sources and snapshot not in seen_snapshots
            and source.is_file() and snapshot.is_file()
            and not source_value.is_symlink() and not snapshot_value.is_symlink(),
            "Small-input source/snapshot is missing, symbolic, or duplicated",
        )
        digest = str(record.get("sha256", ""))
        size = record.get("size")
        require(
            SHA256_RE.fullmatch(digest) is not None
            and sha256_file(source) == digest == sha256_file(snapshot)
            and source.stat().st_size == size == snapshot.stat().st_size,
            f"Small-input source/snapshot hash or size differs: {label}",
        )
        require(not (snapshot.stat().st_mode & 0o222), f"Small-input snapshot is writable: {label}")
        seen_sources[source] = record
        seen_snapshots.add(snapshot)
        labels.add(label)
    require(set(seen_sources) == set(expected_sources), "Phase 4 small-input set differs from work order")
    for source, digest in expected_sources.items():
        require(seen_sources[source].get("sha256") == digest,
                f"Phase 4 small-input record binds another digest: {source}")

    inventory = {
        key: row for key, row in rows_by(phase2 / "inventory.csv", "inventory_id", "Phase 2 inventory").items()
        if row.get("row_status") != "SUPERSEDED"
    }
    require(inventory and all(row.get("row_status") == "REVIEWED" for row in inventory.values()),
            "Every active Phase 2 inventory row must be REVIEWED")
    assets = rows_by(phase2 / "asset-inventory.csv", "asset_id", "Phase 2 asset inventory")
    phase3_assets = rows_by(phase3 / "asset-registry.csv", "asset_id", "Phase 3 asset registry")
    architecture = rows_by(phase3 / "architecture-map.csv", "source_row_key", "Phase 3 architecture map")
    modules = rows_by(phase3 / "module-registry.csv", "harmony_module_id", "Phase 3 module registry")
    routes = rows_by(phase3 / "route-registry.csv", "route_id", "Phase 3 route registry")
    surfaces = rows_by(phase3 / "surface-registry.csv", "surface_shell_id", "Phase 3 surface registry")
    phase3_gate = object_json(phase3 / "stage-03-gate-report.json", "Phase 3 gate report")
    phase3_snapshot = object_json(phase3 / "scaffold-snapshot-manifest.json", "Phase 3 snapshot")
    require(
        phase3_gate.get("phase") == 3 and phase3_gate.get("verdict") == "PASS"
        and not phase3_gate.get("errors")
        and phase3_gate.get("source_snapshot_sha256") == phase3_snapshot.get("snapshot_sha256")
        and input_lock.get("phase3_source_snapshot_sha256") == phase3_snapshot.get("snapshot_sha256"),
        "Phase 3 accepted source snapshot binding differs",
    )
    return (
        run_dir, scope, manifest, input_lock, ownership, inventory, assets, phase3_assets,
        architecture, modules, {
            "phase2": phase2, "phase3": phase3, "controller": controller,
            "work_order": work_order, "work_order_path": work_order_path,
            "phase3_order": phase3_order, "phase3_henvs": phase3_henvs,
            "routes": routes, "surfaces": surfaces,
        },
    )


def validate_locked_materials(
    workspace: Path,
    scope: dict[str, Any],
    input_lock: dict[str, Any],
    ownership: dict[str, str],
    inventory: dict[str, dict[str, str]],
    assets: dict[str, dict[str, str]],
    phase3_assets: dict[str, dict[str, str]],
    context: dict[str, Any],
) -> tuple[
    dict[str, Path], dict[str, dict[str, Any]], dict[str, dict[str, Any]], str
]:
    phase2: Path = context["phase2"]
    phase3: Path = context["phase3"]
    require(
        input_lock.get("phase2_inventory_ids") == sorted(inventory),
        "Phase 4 frozen Inventory-ID set differs from active Phase 2 inventory",
    )
    require(
        input_lock.get("phase2_asset_ids") == sorted(assets),
        "Phase 4 frozen Asset-ID set differs from Phase 2",
    )
    require(
        set(assets) == set(phase3_assets)
        and all(row.get("status") == "REVIEWED" for row in assets.values())
        and all(row.get("status") == "READY" for row in phase3_assets.values()),
        "Phase 2/3 asset coverage or lifecycle differs",
    )

    evidence_index = rows_by(
        phase2 / "evidence-index.csv", "evidence_id", "Phase 2 evidence index"
    )
    expected_evidence: dict[str, dict[str, str]] = {}
    for inventory_id, row in inventory.items():
        evidence_id = validate_id(str(row.get("evidence_id", "")), "Android Evidence-ID")
        require(evidence_id not in expected_evidence,
                f"Android Evidence-ID is reused by active inventory: {evidence_id}")
        evidence_row = evidence_index.get(evidence_id)
        require(
            evidence_row is not None
            and evidence_row.get("inventory_id") == inventory_id
            and evidence_row.get("status") == "ACCEPTED",
            f"Active inventory lacks an accepted Android evidence row: {inventory_id}",
        )
        expected_evidence[evidence_id] = evidence_row
    raw_android = input_lock.get("android_evidence")
    require(isinstance(raw_android, list), "Phase 4 android_evidence must be an array")
    records: dict[str, dict[str, Any]] = {}
    for raw in raw_android:
        require(isinstance(raw, dict) and set(raw) == ANDROID_RECORD_KEYS,
                "Phase 4 Android evidence lock record keys differ")
        evidence_id = validate_id(str(raw.get("evidence_id", "")), "Android Evidence-ID")
        require(evidence_id not in records, f"Duplicate Android evidence lock: {evidence_id}")
        records[evidence_id] = raw
    require(set(records) == set(expected_evidence),
            "Frozen Android evidence packages do not exactly cover active inventory")
    android_dirs: dict[str, Path] = {}
    for evidence_id, index_row in expected_evidence.items():
        record = records[evidence_id]
        source = Path(str(record.get("source_path", ""))).expanduser()
        snapshot = Path(str(record.get("snapshot_path", ""))).expanduser()
        require(source.is_absolute() and snapshot.is_absolute(),
                f"Android evidence paths must be absolute: {evidence_id}")
        source = source.resolve()
        snapshot = snapshot.resolve()
        expected_source = safe_relative_path(
            phase2, str(index_row.get("relative_path", "")), f"Android source evidence {evidence_id}"
        )
        expected_snapshot = (workspace / "inputs" / "android-evidence" / evidence_id).resolve()
        require(source == expected_source.resolve() and snapshot == expected_snapshot,
                f"Android evidence source/snapshot path differs: {evidence_id}")
        source_metadata = verify_android_package(source, evidence_id)
        snapshot_metadata = verify_android_package(snapshot, evidence_id)
        require(package_summary(source) == package_summary(snapshot),
                f"Android evidence snapshot bytes differ from source: {evidence_id}")
        summary = package_summary(snapshot)
        expected_hashes = {
            "manifest_sha256": sha256_file(snapshot / "manifest.sha256"),
            "metadata_sha256": sha256_file(snapshot / "metadata.json"),
            "screenshot_sha256": sha256_file(snapshot / "screenshot.png"),
            "layout_sha256": sha256_file(snapshot / "layout.json"),
            "sha256": summary["sha256"],
            "size": summary["size"],
            "file_count": summary["file_count"],
        }
        require(
            record.get("inventory_id") == index_row.get("inventory_id")
            and all(record.get(field) == value for field, value in expected_hashes.items())
            and source_metadata == snapshot_metadata
            and snapshot_metadata.get("inventory_id") == index_row.get("inventory_id"),
            f"Android evidence input-lock identity/hash differs: {evidence_id}",
        )
        android_dirs[evidence_id] = snapshot

    raw_assets = input_lock.get("phase2_asset_files")
    require(isinstance(raw_assets, list), "Phase 4 phase2_asset_files must be an array")
    locked_assets: dict[str, dict[str, Any]] = {}
    snapshots: set[Path] = set()
    for raw in raw_assets:
        require(isinstance(raw, dict) and set(raw) == ASSET_LOCK_KEYS,
                "Phase 4 asset lock record keys differ")
        asset_id = validate_id(str(raw.get("asset_id", "")), "Asset-ID")
        require(asset_id in assets and asset_id not in locked_assets,
                f"Unknown/duplicate frozen asset: {asset_id}")
        source_row = assets[asset_id]
        source_value = Path(str(raw.get("source_path", ""))).expanduser()
        snapshot_value = Path(str(raw.get("snapshot_path", ""))).expanduser()
        require(source_value.is_absolute() and snapshot_value.is_absolute(),
                f"Asset source/snapshot paths must be absolute: {asset_id}")
        source = source_value.resolve()
        snapshot = snapshot_value.resolve()
        expected_source = (phase2 / str(source_row.get("archive_path", ""))).resolve()
        expected_snapshot = (
            workspace / "inputs" / "phase2-assets" / "files" / asset_id / expected_source.name
        ).resolve()
        source.relative_to((phase2 / "asset-package").resolve())
        snapshot.relative_to((workspace / "inputs" / "phase2-assets").resolve())
        digest = str(raw.get("sha256", ""))
        require(
            source == expected_source and snapshot == expected_snapshot
            and snapshot not in snapshots and source.is_file() and snapshot.is_file()
            and not source_value.is_symlink() and not snapshot_value.is_symlink()
            and digest == source_row.get("sha256")
            and sha256_file(source) == digest == sha256_file(snapshot)
            and source.stat().st_size == raw.get("size") == snapshot.stat().st_size
            and not (snapshot.stat().st_mode & 0o222),
            f"Frozen asset path/hash/size differs: {asset_id}",
        )
        locked_assets[asset_id] = raw
        snapshots.add(snapshot)
    require(set(locked_assets) == set(assets),
            "Frozen Phase 2 asset files do not exactly cover asset inventory")

    env_registry = rows_by(
        workspace / "environments" / "h4env-registry.csv", "h4env_id", "H4ENV registry"
    )
    required_h4env_ids = canonical_string_list(
        input_lock.get("required_h4env_ids"), "required_h4env_ids"
    )
    require(set(env_registry) == set(required_h4env_ids),
            "H4ENV registry differs from frozen required H4ENV set")
    raw_h4envs = input_lock.get("h4envs")
    require(isinstance(raw_h4envs, list), "Phase 4 h4envs must be an array")
    lock_h4envs: dict[str, dict[str, Any]] = {}
    for raw in raw_h4envs:
        require(isinstance(raw, dict) and set(raw) == H4ENV_LOCK_KEYS,
                "Phase 4 H4ENV lock record keys differ")
        h4env_id = validate_id(str(raw.get("h4env_id", "")), "H4ENV-ID")
        require(h4env_id not in lock_h4envs, f"Duplicate H4ENV lock: {h4env_id}")
        lock_h4envs[h4env_id] = raw
    require(set(lock_h4envs) == set(required_h4env_ids),
            "Phase 4 H4ENV lock records differ from required H4ENV set")

    scope_envs = {
        str(item.get("env_id", "")): item
        for item in scope.get("environments", []) if isinstance(item, dict)
    }
    require(scope_envs, "Controller scope lacks frozen Android environments")
    environments: dict[str, dict[str, Any]] = {}
    for h4env_id in required_h4env_ids:
        record = lock_h4envs[h4env_id]
        row = env_registry[h4env_id]
        relative = f"environments/{h4env_id}/phase4-environment.json"
        require(record.get("relative_path") == relative,
                f"Noncanonical H4ENV lock path: {h4env_id}")
        path = safe_relative_path(workspace, relative, f"H4ENV {h4env_id}")
        environment = object_json(path, f"H4ENV {h4env_id}")
        base_henv_id = str(environment.get("base_henv_id", ""))
        base = context["phase3_henvs"].get(base_henv_id)
        require(
            record.get("sha256") == sha256_file(path)
            and record.get("source_android_env_id") == environment.get("source_android_env_id")
            and record.get("base_henv_id") == base_henv_id
            and record.get("device_id") == environment.get("device_id")
            and row.get("environment_sha256") == sha256_file(path)
            and row.get("status") == "FROZEN" and row.get("required") == "true"
            and row.get("frozen_by") == ownership["implementation_lead_id"]
            and row.get("source_android_env_id") == environment.get("source_android_env_id")
            and row.get("base_henv_id") == base_henv_id
            and row.get("device_id") == environment.get("device_id")
            and environment.get("h4env_id") == h4env_id
            and environment.get("source_android_env_id") in scope_envs
            and environment.get("created_by") == ownership["implementation_lead_id"]
            and base is not None
            and environment.get("base_henv_sha256") == base["sha256"]
            and str(environment.get("emulator", {}).get("device_type", "")).lower() == "emulator",
            f"Frozen H4ENV identity/ownership/base differs: {h4env_id}",
        )
        frozen_category_contracts(environment)
        environments[h4env_id] = environment
    required_source_envs = {row.get("env_id", "") for row in inventory.values()}
    mapped_source_envs = {env.get("source_android_env_id", "") for env in environments.values()}
    require(required_source_envs == mapped_source_envs,
            "H4ENV mapping does not exactly cover active Android environments")

    project_snapshot = build_project_snapshot(workspace / "harmony-project")
    source_snapshot_sha = str(project_snapshot.get("snapshot_sha256", ""))
    require(SHA256_RE.fullmatch(source_snapshot_sha) is not None,
            "Current Harmony project snapshot is invalid")
    return android_dirs, locked_assets, environments, source_snapshot_sha


def validate_mapping_and_feature_orders(
    workspace: Path,
    scope: dict[str, Any],
    manifest: dict[str, Any],
    input_lock: dict[str, Any],
    ownership: dict[str, str],
    inventory: dict[str, dict[str, str]],
    architecture: dict[str, dict[str, str]],
    modules: dict[str, dict[str, str]],
    environments: dict[str, dict[str, Any]],
    context: dict[str, Any],
) -> tuple[
    dict[str, dict[str, Any]], dict[str, dict[str, str]], dict[str, dict[str, str]]
]:
    phase3: Path = context["phase3"]
    routes: dict[str, dict[str, str]] = context["routes"]
    surfaces: dict[str, dict[str, str]] = context["surfaces"]
    expected_source_keys = {source_row_key(row) for row in inventory.values()}
    require(set(architecture) == expected_source_keys,
            "Phase 3 architecture map does not exactly cover active inventory")
    require(all(row.get("status") == "READY" for row in modules.values()),
            "A Phase 3 module is not READY")
    for inventory_id, source in inventory.items():
        row_key = source_row_key(source)
        mapping = architecture[row_key]
        for field in ("inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id"):
            require(mapping.get(field) == source.get(field),
                    f"Phase 3 mapping {field} differs: {inventory_id}")
        module_id = str(mapping.get("harmony_module_id", ""))
        require(
            mapping.get("mapping_status") == "SHELL_CREATED_PENDING_IMPLEMENTATION"
            and module_id in modules,
            f"Phase 3 mapping is not implementation-ready: {inventory_id}",
        )
        if mapping.get("mapping_type") == "ROUTE_PAGE":
            target_id = str(mapping.get("route_id", ""))
            target = routes.get(target_id)
        elif mapping.get("mapping_type") == "VISUAL_SURFACE":
            target_id = str(mapping.get("surface_shell_id", ""))
            target = surfaces.get(target_id)
        else:
            raise ValueError(f"Unsupported Phase 3 mapping type: {mapping.get('mapping_type')}")
        require(
            bool(target_id) and target is not None and target.get("status") == "READY"
            and target.get("harmony_module_id") == module_id
            and target.get("page_id") == source.get("page_id"),
            f"Phase 3 target binding differs: {inventory_id}",
        )

    parity = rows_by(workspace / "parity-map.csv", "parity_id", "Phase 4 parity map")
    validate_migration_unit_contracts(
        workspace, inventory, architecture, parity, context, input_lock, manifest
    )
    capabilities = rows_by(
        phase3 / "capability-contracts.csv",
        "capability_requirement_id",
        "Phase 3 capability contracts",
    )
    included = set(scope.get("migration_scope", {}).get("included_features", []))
    require(included and {row.get("feature_id", "") for row in inventory.values()} == included,
            "Included feature scope differs from active inventory")

    registry_path = workspace / "feature-work-order-registry.csv"
    require_csv_header(
        registry_path,
        ["work_order_id", "feature_id", "relative_path", "work_order_sha256",
         "issued_by", "issued_at", "status"],
        "Feature work-order registry",
    )
    registry_rows = read_csv(registry_path)
    active_rows = [row for row in registry_rows if row.get("status") != "SUPERSEDED"]
    require(len(active_rows) == len(included),
            "Feature work-order registry must contain one active row per included feature")
    by_feature: dict[str, dict[str, str]] = {}
    by_order: set[str] = set()
    for row in active_rows:
        feature_id = validate_id(str(row.get("feature_id", "")), "Feature-ID")
        order_id = validate_id(str(row.get("work_order_id", "")), "Feature Work-Order-ID")
        require(feature_id in included and feature_id not in by_feature and order_id not in by_order,
                f"Unknown/duplicate feature work order: {feature_id}/{order_id}")
        by_feature[feature_id] = row
        by_order.add(order_id)
    require(set(by_feature) == included, "Feature work-order feature coverage differs")

    manifest_sha = sha256_file(workspace / "phase-manifest.json")
    input_lock_sha = sha256_file(workspace / "stage-04-input-lock.json")
    work_orders: dict[str, dict[str, Any]] = {}
    exclusive_paths: dict[Path, str] = {}
    for feature_id in sorted(included):
        row = by_feature[feature_id]
        order_id = row["work_order_id"]
        relative = f"feature-work-orders/{order_id}.json"
        require(row.get("relative_path") == relative and row.get("status") == "ISSUED",
                f"Feature work-order registry path/status differs: {feature_id}")
        path = safe_relative_path(workspace, relative, f"feature work order {order_id}")
        order = object_json(path, f"feature work order {order_id}")
        require(set(order) == FEATURE_ORDER_KEYS,
                f"Feature work-order keys differ: {order_id}")
        require(
            row.get("work_order_sha256") == sha256_file(path)
            and row.get("issued_by") == ownership["implementation_lead_id"]
            and row.get("issued_at") == order.get("issued_at")
            and order.get("schema_version") == "1.0"
            and order.get("work_order_id") == order_id
            and order.get("run_id") == scope.get("run_id")
            and order.get("phase") == 4 and order.get("feature_id") == feature_id
            and order.get("status") == "ISSUED"
            and order.get("issued_by") == ownership["implementation_lead_id"]
            and order.get("phase4_manifest_sha256") == manifest_sha
            and order.get("stage04_input_lock_sha256") == input_lock_sha
            and order.get("visual_asset_agent_id") == ownership["visual_asset_agent_id"],
            f"Feature work-order identity/hash/issuer differs: {feature_id}",
        )
        actors = order.get("ownership")
        require(isinstance(actors, dict) and set(actors) == FEATURE_ACTOR_KEYS,
                f"Feature work-order ownership keys differ: {feature_id}")
        actor_values = {validate_actor(str(actors[key]), f"{feature_id}.{key}") for key in FEATURE_ACTOR_KEYS}
        require(
            not actor_values & {
                ownership["verification_executor_id"], ownership["parity_acceptance_agent_id"]
            },
            f"Feature implementer conflicts with verifier/reviewer: {feature_id}",
        )
        feature_inventory = {
            inventory_id: source
            for inventory_id, source in inventory.items() if source.get("feature_id") == feature_id
        }
        feature_parity = {
            parity_id: parity_row
            for parity_id, parity_row in parity.items() if parity_row.get("feature_id") == feature_id
        }
        feature_modules = sorted(
            {architecture[source_row_key(source)]["harmony_module_id"] for source in feature_inventory.values()}
        )
        feature_assets: set[str] = set()
        for source in feature_inventory.values():
            feature_assets.update(item for item in split_multi(source.get("asset_ids", "")) if item != "NONE_FOUND")
        feature_caps = {
            requirement_id: contract
            for requirement_id, contract in capabilities.items()
            if contract.get("source_feature_id") == feature_id
        }
        expected_targets: list[dict[str, str]] = []
        for source in feature_inventory.values():
            mapping = architecture[source_row_key(source)]
            mapping_type = str(mapping.get("mapping_type", ""))
            expected_targets.append(
                {
                    "source_row_key": source_row_key(source),
                    "harmony_module_id": str(mapping.get("harmony_module_id", "")),
                    "target_kind": mapping_type,
                    "target_id": str(
                        mapping.get("route_id", "")
                        if mapping_type == "ROUTE_PAGE"
                        else mapping.get("surface_shell_id", "")
                    ),
                }
            )
        expected_targets.sort(key=lambda item: json.dumps(item, sort_keys=True))
        actual_targets = order.get("targets")
        require(isinstance(actual_targets, list) and all(isinstance(item, dict) for item in actual_targets),
                f"Feature work-order targets are malformed: {feature_id}")
        actual_targets = sorted(actual_targets, key=lambda item: json.dumps(item, sort_keys=True))
        require(
            order.get("source_inventory_ids") == sorted(feature_inventory)
            and order.get("parity_ids") == sorted(feature_parity)
            and order.get("harmony_module_ids") == feature_modules
            and order.get("targets") == actual_targets == expected_targets
            and order.get("required_h4env_ids") == sorted(environments)
            and order.get("asset_ids") == sorted(feature_assets)
            and order.get("capability_requirement_ids") == sorted(feature_caps)
            and order.get("capability_contract_ids")
            == sorted({row.get("capability_contract_id", "") for row in feature_caps.values()}),
            f"Feature work-order frozen coverage differs: {feature_id}",
        )
        code_paths = canonical_string_list(
            order.get("exclusive_code_paths"), f"{feature_id}.exclusive_code_paths"
        )
        for relative_path in code_paths:
            path = safe_relative_path(
                workspace / "harmony-project", relative_path,
                f"exclusive code path for {feature_id}",
            ).resolve()
            for existing, other_feature in exclusive_paths.items():
                try:
                    path.relative_to(existing)
                    conflict = True
                except ValueError:
                    try:
                        existing.relative_to(path)
                        conflict = True
                    except ValueError:
                        conflict = False
                require(not conflict,
                        f"Feature code-path ownership overlaps: {feature_id}/{other_feature}")
            exclusive_paths[path] = feature_id
        conditions = order.get("completion_conditions")
        require(isinstance(conditions, list) and conditions
                and all(isinstance(item, str) and item.strip() for item in conditions),
                f"Feature work-order completion conditions are empty: {feature_id}")
        work_orders[feature_id] = order

    ledger = rows_by(
        workspace / "implementation-ledger.csv", "feature_id", "implementation ledger"
    )
    require(set(ledger) == included, "Implementation ledger feature coverage differs")
    for feature_id, row in ledger.items():
        order = work_orders[feature_id]
        actors = order["ownership"]
        require(
            row.get("work_order_id") == order.get("work_order_id")
            and all(row.get(key) == actors.get(key) for key in FEATURE_ACTOR_KEYS)
            and row.get("asset_agent_id") == ownership["visual_asset_agent_id"]
            and json_string_array(row.get("source_inventory_ids", ""), f"{feature_id}.source_inventory_ids", allow_empty=False)
            == order.get("source_inventory_ids")
            and json_string_array(row.get("harmony_module_ids", ""), f"{feature_id}.harmony_module_ids", allow_empty=False)
            == order.get("harmony_module_ids")
            and row.get("status") == "ACCEPTED"
            and row.get("updated_by") in {
                ownership["implementation_lead_id"], ownership["parity_acceptance_agent_id"]
            },
            f"Implementation ledger differs from frozen feature work order: {feature_id}",
        )
    return work_orders, ledger, parity


def collect_final_builds(
    workspace: Path,
    build_ids: list[str],
    environments: dict[str, dict[str, Any]],
    ownership: dict[str, str],
    input_lock_sha256: str,
    source_snapshot_sha256: str,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Validate the caller-selected final build set before the independent Gate 4 audit."""
    require(build_ids == sorted(set(build_ids)) and bool(build_ids),
            "--build-id values must be nonempty, sorted, and unique")
    builds: dict[str, dict[str, Any]] = {}
    build_by_environment: dict[str, str] = {}
    artifact_hashes: list[str] = []
    for build_id in build_ids:
        validate_id(build_id, "HBUILD-ID")
        directory = safe_relative_path(
            workspace, f"builds/{build_id}", f"HBUILD {build_id}"
        )
        require(directory.is_dir(), f"HBUILD directory is missing: {build_id}")
        metadata = object_json(directory / "metadata.json", f"HBUILD {build_id} metadata")
        h4env_id = validate_id(str(metadata.get("h4env_id", "")), f"{build_id} H4ENV-ID")
        require(h4env_id in environments and h4env_id not in build_by_environment,
                f"HBUILD set has an unknown or duplicate H4ENV: {h4env_id}")
        validated, artifact_sha256 = validate_hbuild(
            directory,
            build_id,
            environments[h4env_id],
            ownership["verification_executor_id"],
            input_lock_sha256,
            source_snapshot_sha256,
        )
        require(
            validated.get("h4env_id") == h4env_id
            and validated.get("environment_sha256")
            == sha256_file(workspace / "environments" / h4env_id / "phase4-environment.json"),
            f"HBUILD environment binding differs: {build_id}",
        )
        builds[build_id] = validated
        build_by_environment[h4env_id] = build_id
        artifact_hashes.append(artifact_sha256)
    require(set(build_by_environment) == set(environments),
            "Final HBUILD set must contain exactly one PASS build per required H4ENV")
    return builds, artifact_hashes


def build_candidate_report(
    workspace: Path,
    scope: dict[str, Any],
    manifest: dict[str, Any],
    ownership: dict[str, str],
    build_ids: list[str],
    artifact_hashes: list[str],
    source_snapshot_sha256: str,
    reviewer: str,
) -> dict[str, Any]:
    implementation = read_csv(workspace / "implementation-ledger.csv")
    parity = read_csv(workspace / "parity-map.csv")
    evidence = [
        row for row in read_csv(workspace / "evidence-index.csv")
        if row.get("status") == "SEALED"
    ]
    assets = read_csv(workspace / "asset-migration.csv")
    capabilities = read_csv(workspace / "capability-implementation.csv")
    nativeization = read_csv(workspace / "nativeization-decisions.csv")
    rework = read_csv(workspace / "rework-tickets.csv")
    open_rework = [row for row in rework if row.get("status") != "CLOSED"]
    require(not open_rework, f"Phase 4 still has open rework: {len(open_rework)}")
    return {
        "schema_version": "1.0",
        "phase": 4,
        "run_id": scope.get("run_id"),
        "work_order_id": manifest.get("work_order_id"),
        "verdict": "PASS",
        "final_verdict": "PASS",
        "implementation_chain_closed": True,
        "reviewer_role": "parity-acceptance-agent",
        "reviewer_id": reviewer,
        "reviewed_at": utc_now(),
        "input_lock_sha256": sha256_file(workspace / "stage-04-input-lock.json"),
        "source_snapshot_sha256": source_snapshot_sha256,
        "build_ids": build_ids,
        "artifact_hashes": artifact_hashes,
        "counts": {
            "features": len(implementation),
            "parity_rows": len(parity),
            "active_evidence": len(evidence),
            "assets": len(assets),
            "capabilities": len(capabilities),
            "nativeization_decisions": len(nativeization),
            "open_rework": 0,
        },
        "attestations": {
            "visual_review": True,
            "functional_parity": True,
            "asset_provenance": True,
            "nativeization_review": True,
        },
        "errors": [],
    }


def remove_candidate_outputs(paths: list[Path]) -> None:
    """Best-effort rollback for a failed pre-seal audit."""
    for path in reversed(paths):
        try:
            if path.exists() and path.is_file() and not path.is_symlink():
                path.chmod(0o600)
                path.unlink()
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument(
        "--build-id", action="append", required=True,
        help="Final PASS HBUILD-ID; repeat once per required H4ENV",
    )
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--decision", required=True, choices=("PASS",))
    parser.add_argument("--attest-visual-review", action="store_true")
    parser.add_argument("--attest-functional-parity", action="store_true")
    parser.add_argument("--attest-asset-provenance", action="store_true")
    parser.add_argument("--attest-nativeization-review", action="store_true")
    args = parser.parse_args()

    raw_workspace = Path(args.workspace).expanduser().absolute()
    candidate_paths: list[Path] = []
    try:
        require(not raw_workspace.is_symlink(), "Phase 4 workspace must not be a symbolic link")
        workspace = raw_workspace.resolve(strict=True)
        for name in ("stage-04-gate-report.json", "stage-04-closure-manifest.sha256", "CLOSED"):
            require(not (workspace / name).exists(),
                    f"Phase 4 already has a final or partial closure artifact: {name}")
        reviewer = validate_actor(args.reviewer, "parity acceptance reviewer")
        require(
            args.attest_visual_review
            and args.attest_functional_parity
            and args.attest_asset_provenance
            and args.attest_nativeization_review,
            "All four independent Phase 4 attestations are required",
        )
        ensure_no_mp4_or_placeholders(workspace)
        (
            run_dir, scope, manifest, input_lock, ownership, inventory, assets,
            phase3_assets, architecture, modules, context,
        ) = validate_upstream_and_work_order(workspace)
        require(reviewer == ownership["parity_acceptance_agent_id"],
                "Only the frozen parity acceptance agent may close Phase 4")
        validate_attempt_ledgers(workspace)
        android_dirs, locked_assets, environments, source_snapshot_sha256 = validate_locked_materials(
            workspace, scope, input_lock, ownership, inventory, assets, phase3_assets, context
        )
        # These checks deliberately run before the controller's independent Gate 4 audit.
        # They make feature ownership and frozen state coverage fail locally, at the authoring boundary.
        validate_mapping_and_feature_orders(
            workspace, scope, manifest, input_lock, ownership, inventory,
            architecture, modules, environments, context,
        )
        build_ids = sorted(args.build_id)
        _builds, artifact_hashes = collect_final_builds(
            workspace,
            build_ids,
            environments,
            ownership,
            sha256_file(workspace / "stage-04-input-lock.json"),
            source_snapshot_sha256,
        )
        report = build_candidate_report(
            workspace, scope, manifest, ownership, build_ids,
            artifact_hashes, source_snapshot_sha256, reviewer,
        )

        report_path = workspace / "stage-04-gate-report.json"
        closure_path = workspace / "stage-04-closure-manifest.sha256"
        closed_path = workspace / "CLOSED"
        candidate_paths = [report_path, closure_path, closed_path]
        atomic_json(report_path, report)
        atomic_text(closure_path, closure_manifest_text(workspace))
        atomic_text(closed_path, sha256_file(report_path) + "\n")

        controller_validator = (
            Path(__file__).resolve().parents[2]
            / "android-harmony-migration-controller" / "scripts" / "validate_gate.py"
        )
        require(controller_validator.is_file(),
                f"Controller Gate 4 validator is missing: {controller_validator}")
        audit = subprocess.run(
            [
                sys.executable, str(controller_validator), "--run-dir", str(run_dir),
                "--phase", "4",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=600,
            check=False,
        )
        if audit.returncode != 0:
            detail = audit.stderr.strip() or audit.stdout.strip()
            raise ValueError(f"Independent controller Gate 4 pre-seal audit failed: {detail[:4000]}")
        controller_report = json.loads(audit.stdout)
        require(isinstance(controller_report, dict) and controller_report.get("verdict") == "PASS"
                and not controller_report.get("errors"),
                "Independent controller Gate 4 pre-seal audit did not return PASS")

        make_tree_read_only(workspace)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as exc:
        remove_candidate_outputs(candidate_paths)
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
