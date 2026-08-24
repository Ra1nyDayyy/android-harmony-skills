#!/usr/bin/env python3
"""Compile immutable page acceptance contracts from frozen Phase 2/3 facts."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any


COMPARISON_POLICY = {
    "geometry_tolerance": "max(2dp, 0.5%)",
    "application_region_ssim": 0.98,
    "changed_pixel_ratio": 0.02,
    "required_element_masks": "STRICTER_REQUIRED_ELEMENT_MASKS",
}
REGISTRY_FIELDS = [
    "page_id", "page_name", "relative_path", "contract_sha256", "state_count",
    "feature_ids", "required_h4env_ids", "status",
]
CONTRACT_KEYS = {
    "schema_version", "page_id", "page_name", "feature_ids", "states", "components",
    "source_geometry", "assets", "visible_text", "interaction_bindings", "entry_conditions",
    "transitions", "code_map", "business_rules", "data_dependencies", "side_effects",
    "system_capabilities", "android_evidence_hashes", "phase3_targets", "required_h4env_ids",
    "comparison_policy",
}
CONTRACT_LIST_FIELDS = CONTRACT_KEYS - {"schema_version", "page_id", "page_name", "comparison_policy"}
PAGE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*$")


def canonical_contract_sha256(contract: dict[str, object]) -> str:
    """Hash UTF-8 canonical JSON using sorted keys and compact separators."""
    payload = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compile_page_contracts(
    phase2_workspace: Path,
    phase3_workspace: Path,
    required_h4env_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    """Return one canonical contract for every distinct non-empty Phase 2 page_id."""
    phase2_workspace = Path(phase2_workspace)
    phase3_workspace = Path(phase3_workspace)
    env_ids = _sorted_ids(required_h4env_ids, "required H4ENV-ID")
    if not env_ids:
        raise ValueError("At least one required H4ENV-ID is required")

    inventory = [row for row in _read_csv(phase2_workspace / "inventory.csv") if row.get("row_status") != "SUPERSEDED"]
    if not inventory:
        raise ValueError("Phase 2 has no active inventory rows")
    for row in inventory:
        _require(row, ("inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id"), "Phase 2 inventory")
        if row.get("row_status") not in {"", "REVIEWED"}:
            raise ValueError(f"Active inventory is not REVIEWED: {row['inventory_id']}")
    inventory_by_id = _index(inventory, "inventory_id", "Phase 2 inventory")

    pages = _object_rows(phase2_workspace / "static-analysis" / "pages.json", "pages", "Phase 2 pages")
    pages_by_id: dict[str, dict[str, object]] = {}
    for page in pages:
        page_id = str(page.get("page_id", ""))
        if not page_id:
            raise ValueError("Phase 2 static analysis has empty Page-ID")
        validate_page_id(page_id)
        if page_id in pages_by_id:
            raise ValueError(f"Phase 2 static analysis has duplicate Page-ID: {page_id}")
        pages_by_id[page_id] = page
    for row in inventory:
        validate_page_id(row["page_id"])
        if row["page_id"] not in pages_by_id:
            raise ValueError(f"{row['page_id']}: inventory page is absent from Phase 2 pages")

    components = _object_rows(phase2_workspace / "static-analysis" / "components.json", "components", "Phase 2 components")
    components_by_id = _index(components, "component_id", "Phase 2 component")
    events = _object_rows(phase2_workspace / "static-analysis" / "events.json", "events", "Phase 2 events")
    events_by_id = _index(events, "event_id", "Phase 2 event")
    transitions = _object_rows(phase2_workspace / "static-analysis" / "transitions.json", "transitions", "Phase 2 transitions")
    _index(transitions, "transition_id", "Phase 2 transition")
    state_candidates = _object_rows(phase2_workspace / "static-analysis" / "state-candidates.json", "states", "Phase 2 state")
    _index(state_candidates, "state_id", "Phase 2 state")
    for component in components:
        _require(component, ("component_id", "page_id"), "Phase 2 component")
        _validate_page_ref(component["page_id"], pages_by_id, "component")
    for event in events:
        _require(event, ("event_id", "page_id"), "Phase 2 event")
        _validate_page_ref(event["page_id"], pages_by_id, "event")
        component_id = str(event.get("component_id", ""))
        if component_id and component_id not in components_by_id:
            raise ValueError(f"event {event['event_id']} references orphan component {component_id}")
        if component_id and components_by_id[component_id].get("page_id") != event["page_id"]:
            raise ValueError(
                f"event {event['event_id']} references component {component_id} from "
                f"{components_by_id[component_id].get('page_id', '')}"
            )
    for transition in transitions:
        _require(transition, ("transition_id", "source_page_id", "target_page_id"), "Phase 2 transition")
        _validate_page_ref(transition["source_page_id"], pages_by_id, "transition source")
        _validate_page_ref(transition["target_page_id"], pages_by_id, "transition target")
        event_id = str(transition.get("event_id", ""))
        if event_id and event_id not in events_by_id:
            raise ValueError(f"transition {transition['transition_id']} references orphan event {event_id}")
        if event_id and events_by_id[event_id].get("page_id") != transition["source_page_id"]:
            raise ValueError(
                f"transition {transition['transition_id']} references event {event_id} from "
                f"{events_by_id[event_id].get('page_id', '')}"
            )
    inventory_state_keys = {(row["page_id"], row["state_id"]) for row in inventory}
    static_state_keys: set[tuple[str, str]] = set()
    for state in state_candidates:
        _require(state, ("state_id", "page_id"), "Phase 2 state")
        _validate_page_ref(state["page_id"], pages_by_id, "state")
        static_state_keys.add((state["page_id"], state["state_id"]))
        if (state["page_id"], state["state_id"]) not in inventory_state_keys:
            raise ValueError(f"{state['page_id']} {state['state_id']}: static state lacks inventory evidence")
    for page_id, state_id in sorted(inventory_state_keys - static_state_keys):
        raise ValueError(f"{page_id} {state_id}: inventory state lacks static state fact")

    evidence_rows = _index(_read_csv(phase2_workspace / "evidence-index.csv"), "evidence_id", "Phase 2 evidence")
    evidence_by_id: dict[str, dict[str, object]] = {}
    for row in inventory:
        page_id, evidence_id = row["page_id"], row["evidence_id"]
        evidence_index = evidence_rows.get(evidence_id)
        if evidence_index is None:
            raise ValueError(f"{page_id}: missing evidence {evidence_id}")
        for field in ("inventory_id", "feature_id", "page_id", "state_id", "env_id"):
            if evidence_index.get(field) != row.get(field):
                raise ValueError(f"{page_id}: evidence {evidence_id} differs for {field}")
        if evidence_index.get("status") not in {"", "ACCEPTED"}:
            raise ValueError(f"{page_id}: evidence {evidence_id} is not ACCEPTED")
        relative = evidence_index.get("relative_path") or f"evidence/{row['env_id']}/{page_id}/{row['state_id']}/{evidence_id}"
        evidence_dir = phase2_workspace / relative
        screenshot, layout, metadata = evidence_dir / "screenshot.png", evidence_dir / "layout.json", evidence_dir / "metadata.json"
        for label, path in (("screenshot", screenshot), ("layout", layout), ("metadata", metadata)):
            if not path.is_file():
                raise ValueError(f"{page_id}: evidence {evidence_id} lacks {label}")
        evidence_by_id[evidence_id] = {
            "evidence_id": evidence_id,
            "relative_path": Path(relative).as_posix(),
            "screenshot_sha256": _sha256_file(screenshot),
            "layout_sha256": _sha256_file(layout),
            "metadata_sha256": _sha256_file(metadata),
            "source_geometry": _load_value(layout, f"layout {evidence_id}"),
        }

    observations = _object_rows(phase2_workspace / "runtime-observations.json", "observations", "Phase 2 runtime observations")
    inventory_by_evidence = _index(inventory, "evidence_id", "active Phase 2 inventory evidence")
    observed_inventory_ids: set[str] = set()
    for observation in observations:
        if observation.get("subject_type") != "PAGE":
            continue
        evidence_id = str(observation.get("after_evidence_id", ""))
        source = inventory_by_evidence.get(evidence_id)
        if source is None:
            raise ValueError(f"runtime observation references unknown evidence {evidence_id}")
        observed_state_id = str(observation.get("state_id", ""))
        if (
            observation.get("page_id") != source["page_id"]
            or observation.get("env_id") != source["env_id"]
            or (observed_state_id and observed_state_id != source["state_id"])
        ):
            raise ValueError(
                f"{observation.get('page_id') or source['page_id']} "
                f"{observed_state_id or source['state_id']}: runtime observation/evidence binding differs"
            )
        observed_inventory_ids.add(source["inventory_id"])
    for row in inventory:
        if row["inventory_id"] not in observed_inventory_ids:
            raise ValueError(f"{row['page_id']} {row['state_id']}: runtime state is uncovered")

    assets = _index(_read_csv(phase2_workspace / "asset-inventory.csv"), "asset_id", "Phase 2 asset")
    code_map = _read_csv(phase2_workspace / "catalogs" / "code-map.csv")
    business_rules = _index(_read_csv(phase2_workspace / "catalogs" / "business-rules.csv"), "business_rule_id", "Phase 2 business rule")
    data_dependencies = _index(_read_csv(phase2_workspace / "catalogs" / "data-dependencies.csv"), "data_dependency_id", "Phase 2 data dependency")
    capabilities = _index(_read_csv(phase2_workspace / "catalogs" / "system-capabilities.csv"), "system_capability_id", "Phase 2 system capability")
    advanced = _load_json(phase2_workspace / "static-analysis" / "advanced-analysis.json", "Phase 2 advanced analysis")
    side_effects = _list_value(advanced, "side_effects", "Phase 2 advanced analysis")
    _index(side_effects, "side_effect_id", "Phase 2 side effect")

    modules = _index(_read_csv(phase3_workspace / "module-registry.csv"), "harmony_module_id", "Phase 3 module")
    architecture = _index(_read_csv(phase3_workspace / "architecture-map.csv"), "inventory_id", "Phase 3 architecture mapping")
    routes = _index(_read_csv(phase3_workspace / "route-registry.csv"), "route_id", "Phase 3 route")
    surfaces_path = phase3_workspace / "surface-registry.csv"
    surfaces = _index(_read_csv(surfaces_path), "surface_shell_id", "Phase 3 surface") if surfaces_path.is_file() else {}

    contracts: list[dict[str, object]] = []
    for page_id in sorted({row["page_id"] for row in inventory}):
        page_rows = sorted((row for row in inventory if row["page_id"] == page_id), key=lambda row: (row["state_id"], row["env_id"], row["inventory_id"]))
        page = pages_by_id[page_id]
        state_records: list[dict[str, object]] = []
        for state_id in sorted({row["state_id"] for row in page_rows}):
            source_rows = [row for row in page_rows if row["state_id"] == state_id]
            state_records.append({
                "state_id": state_id,
                "state_name": source_rows[0].get("state_name", ""),
                "records": [_state_record(row, evidence_by_id[row["evidence_id"]]) for row in source_rows],
            })

        mappings = [_phase3_target(row, architecture, modules, routes, surfaces) for row in page_rows]
        feature_ids = _unique_sorted([row["feature_id"] for row in page_rows] + _multi(page.get("candidate_feature_ids", [])))
        asset_ids = _unique_sorted([asset_id for row in page_rows for asset_id in _multi(row.get("asset_ids", "")) if asset_id != "NONE_FOUND"])
        _require_known(asset_ids, assets, page_id, "asset")
        rule_ids = _unique_sorted([rule for row in page_rows for rule in _multi(row.get("business_rule_refs", ""))])
        data_ids = _unique_sorted([item for row in page_rows for item in _multi(row.get("data_dependency_refs", ""))])
        capability_ids = _unique_sorted([item for row in page_rows for item in _multi(row.get("system_capability_refs", ""))])
        _require_known(rule_ids, business_rules, page_id, "business rule")
        _require_known(data_ids, data_dependencies, page_id, "data dependency")
        _require_known(capability_ids, capabilities, page_id, "system capability")
        contract: dict[str, object] = {
            "schema_version": "page-acceptance-contract-v1",
            "page_id": page_id,
            "page_name": str(page.get("page_name") or page_rows[0].get("page_name") or page.get("symbol", "")),
            "feature_ids": feature_ids,
            "states": state_records,
            "components": _records_for_page(components, page_id, "page_id", "component_id"),
            "source_geometry": [record["android_evidence"]["source_geometry"] for state in state_records for record in state["records"]],
            "assets": [assets[asset_id] for asset_id in asset_ids],
            "visible_text": sorted({str(component.get("text", "")) for component in components if component.get("page_id") == page_id and component.get("text")}),
            "interaction_bindings": _records_for_page(events, page_id, "page_id", "event_id"),
            "entry_conditions": [{"state_id": row["state_id"], "entry_condition": row.get("entry_condition", ""), "action_summary": row.get("action_summary", "")} for row in page_rows],
            "transitions": _records_for_page(transitions, page_id, "source_page_id", "transition_id"),
            "code_map": _records_for_page(code_map, page_id, "page_id", "code_ref"),
            "business_rules": [business_rules[item] for item in rule_ids],
            "data_dependencies": [data_dependencies[item] for item in data_ids],
            "side_effects": _side_effects_for_page(side_effects, page_id, feature_ids),
            "system_capabilities": [capabilities[item] for item in capability_ids],
            "android_evidence_hashes": [evidence_by_id[row["evidence_id"]] for row in page_rows],
            "phase3_targets": sorted(mappings, key=lambda item: (str(item["state_id"]), str(item["env_id"]))),
            "required_h4env_ids": env_ids,
            "comparison_policy": dict(COMPARISON_POLICY),
        }
        contracts.append(contract)
    return contracts


def publish_page_contracts(contracts: list[dict[str, object]], destination: Path) -> list[dict[str, object]]:
    """Validate a complete staged set and atomically publish its files and registry."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".page-contracts-", dir=destination) as temp_name:
        staged = Path(temp_name)
        contract_dir = staged / "page-contracts"
        contract_dir.mkdir()
        registry: list[dict[str, object]] = []
        for contract in sorted(contracts, key=lambda item: str(item["page_id"])):
            _validate_contract(contract)
            page_id = str(contract["page_id"])
            target = _contract_path(contract_dir, page_id)
            _write_json(target, contract)
            registry.append({
                "page_id": page_id,
                "page_name": contract["page_name"],
                "relative_path": f"page-contracts/{page_id}.json",
                "contract_sha256": canonical_contract_sha256(contract),
                "state_count": len(contract["states"]),
                "feature_ids": json.dumps(contract["feature_ids"], ensure_ascii=False, separators=(",", ":")),
                "required_h4env_ids": json.dumps(contract["required_h4env_ids"], separators=(",", ":")),
                "status": "FROZEN",
            })
        _write_csv(staged / "page-contract-registry.csv", registry)
        if len(registry) != len({row["page_id"] for row in registry}):
            raise ValueError("Page contract registry contains duplicate Page-ID")
        _publish_staged_set(staged, destination)
        return registry


def _phase3_target(row: dict[str, str], architecture: dict[str, dict[str, str]], modules: dict[str, dict[str, str]], routes: dict[str, dict[str, str]], surfaces: dict[str, dict[str, str]]) -> dict[str, str]:
    page_id = row["page_id"]
    mapping = architecture.get(row["inventory_id"])
    if mapping is None:
        raise ValueError(f"{page_id}: missing Phase 3 architecture mapping for {row['inventory_id']}")
    for field in ("feature_id", "page_id", "state_id", "env_id", "evidence_id"):
        if mapping.get(field) != row.get(field):
            raise ValueError(f"{page_id}: Phase 3 mapping differs for {field} on {row['inventory_id']}")
    module_id = mapping.get("harmony_module_id", "")
    if not module_id or module_id not in modules or modules[module_id].get("status") != "READY":
        raise ValueError(f"{page_id}: missing READY Phase 3 module {module_id}")
    kind = mapping.get("mapping_type", "")
    target_id = mapping.get("route_id", "") if kind == "ROUTE_PAGE" else mapping.get("surface_shell_id", "")
    targets = routes if kind == "ROUTE_PAGE" else surfaces if kind == "VISUAL_SURFACE" else {}
    target = targets.get(target_id)
    if target is None or target.get("status") != "READY" or target.get("page_id") != page_id or target.get("harmony_module_id") != module_id:
        label = "route" if kind == "ROUTE_PAGE" else "surface"
        raise ValueError(f"{page_id}: missing Phase 3 {label} {target_id}")
    return {"state_id": row["state_id"], "env_id": row["env_id"], "harmony_module_id": module_id, "target_kind": kind, "target_id": target_id}


def _state_record(row: dict[str, str], evidence: dict[str, object]) -> dict[str, object]:
    return {"inventory_id": row["inventory_id"], "feature_id": row["feature_id"], "env_id": row["env_id"], "entry_condition": row.get("entry_condition", ""), "action_summary": row.get("action_summary", ""), "expected_observable": row.get("expected_observable", ""), "android_evidence": evidence, "business_rule_ids": _sorted_ids(_multi(row.get("business_rule_refs", "")), "business rule"), "data_dependency_ids": _sorted_ids(_multi(row.get("data_dependency_refs", "")), "data dependency"), "system_capability_ids": _sorted_ids(_multi(row.get("system_capability_refs", "")), "system capability")}


def _records_for_page(rows: list[dict[str, object]], page_id: str, page_field: str, id_field: str) -> list[dict[str, object]]:
    return sorted((row for row in rows if str(row.get(page_field, "")) == page_id), key=lambda row: str(row[id_field]))


def _side_effects_for_page(rows: list[dict[str, object]], page_id: str, feature_ids: list[str]) -> list[dict[str, object]]:
    return sorted(
        (
            row for row in rows
            if str(row.get("page_id", "")) == page_id
            or (not row.get("page_id") and str(row.get("feature_id", "")) in feature_ids)
        ),
        key=lambda row: str(row["side_effect_id"]),
    )


def _validate_contract(contract: dict[str, object]) -> None:
    missing = sorted(CONTRACT_KEYS - set(contract))
    extras = sorted(set(contract) - CONTRACT_KEYS)
    if missing or extras:
        raise ValueError(
            f"Invalid page acceptance contract {contract.get('page_id', '')}: "
            f"missing={missing}, undeclared={extras}"
        )
    validate_page_id(str(contract.get("page_id", "")))
    if contract.get("schema_version") != "page-acceptance-contract-v1" or not isinstance(contract.get("page_name"), str):
        raise ValueError(f"Invalid page acceptance contract {contract['page_id']}: identity structure differs")
    for field in CONTRACT_LIST_FIELDS:
        value = contract.get(field)
        if not isinstance(value, list):
            raise ValueError(f"Invalid page acceptance contract {contract['page_id']}: {field} must be an array")
    if any(not isinstance(value, str) for value in contract["visible_text"]):
        raise ValueError(f"Invalid page acceptance contract {contract['page_id']}: visible_text must contain strings")
    for field in ("feature_ids", "required_h4env_ids"):
        if any(not isinstance(value, str) or not value for value in contract[field]):
            raise ValueError(f"Invalid page acceptance contract {contract['page_id']}: {field} must contain IDs")
    object_arrays = CONTRACT_LIST_FIELDS - {
        "feature_ids", "source_geometry", "visible_text", "required_h4env_ids",
    }
    for field in object_arrays:
        if any(not isinstance(value, dict) for value in contract[field]):
            raise ValueError(f"Invalid page acceptance contract {contract['page_id']}: {field} must contain objects")
    for state in contract["states"]:
        if set(state) != {"state_id", "state_name", "records"} or not state.get("state_id"):
            raise ValueError(f"Invalid page acceptance contract {contract['page_id']}: state structure differs")
        if not isinstance(state["state_name"], str) or not isinstance(state["records"], list):
            raise ValueError(f"Invalid page acceptance contract {contract['page_id']}: state record structure differs")
        if any(not isinstance(record, dict) for record in state["records"]):
            raise ValueError(f"Invalid page acceptance contract {contract['page_id']}: state records must contain objects")
    policy = contract.get("comparison_policy")
    if not isinstance(policy, dict) or policy != COMPARISON_POLICY:
        raise ValueError(f"Invalid page acceptance contract {contract['page_id']}: comparison_policy differs")


def validate_page_id(page_id: str) -> str:
    """Return a canonical Page-ID or fail before it can influence a path."""
    if not PAGE_ID_RE.fullmatch(page_id):
        raise ValueError(f"unsafe Page-ID: {page_id}")
    return page_id


def _contract_path(contract_dir: Path, page_id: str) -> Path:
    canonical_id = validate_page_id(page_id)
    target = (contract_dir / f"{canonical_id}.json").resolve()
    try:
        target.relative_to(contract_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"unsafe Page-ID output path: {page_id}") from exc
    return target


def _publish_staged_set(staged: Path, destination: Path) -> None:
    names = ("page-contracts", "page-contract-registry.csv")
    backup = Path(tempfile.mkdtemp(prefix=".page-contracts-backup-", dir=destination))
    moved_old: list[str] = []
    published: list[str] = []
    try:
        for name in names:
            target = destination / name
            if target.exists():
                os.replace(target, backup / name)
                moved_old.append(name)
        for name in names:
            os.replace(staged / name, destination / name)
            published.append(name)
    except OSError:
        for name in reversed(published):
            _remove_path(destination / name)
        for name in moved_old:
            os.replace(backup / name, destination / name)
        raise
    finally:
        shutil.rmtree(backup, ignore_errors=True)


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"Missing frozen input: {path}")
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _object_rows(path: Path, key: str, label: str) -> list[dict[str, object]]:
    return _list_value(_load_json(path, label), key, label)


def _load_json(path: Path, label: str) -> dict[str, object]:
    value = _load_value(path, label)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _load_value(path: Path, label: str) -> object:
    if not path.is_file():
        raise ValueError(f"Missing frozen input: {label}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for {label}: {exc}") from exc
    return value


def _list_value(value: dict[str, object], key: str, label: str) -> list[dict[str, object]]:
    rows = value.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{label} has invalid {key}")
    return rows


def _index(rows: list[dict[str, Any]], field: str, label: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = str(row.get(field, ""))
        if not value:
            raise ValueError(f"{label} has empty {field}")
        if value in indexed:
            raise ValueError(f"{label} has duplicate {field}: {value}")
        indexed[value] = row
    return indexed


def _require(row: dict[str, Any], fields: tuple[str, ...], label: str) -> None:
    missing = [field for field in fields if not str(row.get(field, ""))]
    if missing:
        raise ValueError(f"{label} lacks {', '.join(missing)}")


def _validate_page_ref(page_id: str, pages: dict[str, dict[str, Any]], label: str) -> None:
    if page_id not in pages:
        raise ValueError(f"{label} references orphan Page-ID {page_id}")


def _require_known(ids: list[str], known: dict[str, Any], page_id: str, label: str) -> None:
    for item in ids:
        if item not in known:
            raise ValueError(f"{page_id}: references missing {label} {item}")


def _multi(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    text = str(value or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            loaded = json.loads(text)
            if isinstance(loaded, list):
                return [str(item) for item in loaded if str(item)]
        except json.JSONDecodeError:
            pass
    for delimiter in ("|", ";", ","):
        text = text.replace(delimiter, "\n")
    return [item.strip() for item in text.splitlines() if item.strip()]


def _sorted_ids(values: object, label: str) -> list[str]:
    ids = [str(value) for value in values if str(value)]  # type: ignore[union-attr]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Duplicate {label}: {sorted(ids)}")
    return sorted(ids)


def _unique_sorted(values: list[str]) -> list[str]:
    return sorted({value for value in values if value})


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=REGISTRY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
