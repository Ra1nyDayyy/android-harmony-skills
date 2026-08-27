#!/usr/bin/env python3
"""Compile and validate deterministic ArkTS page plans from frozen page contracts."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMPONENT_TYPES = {
    "linearlayout": "Column",
    "column": "Column",
    "relativelayout": "Stack",
    "constraintlayout": "Stack",
    "framelayout": "Stack",
    "stack": "Stack",
    "textview": "Text",
    "text": "Text",
    "button": "Button",
    "edittext": "TextInput",
    "textinput": "TextInput",
    "imageview": "Image",
    "image": "Image",
    "recyclerview": "List",
    "listview": "List",
    "list": "List",
    "scrollview": "Scroll",
    "scroll": "Scroll",
    "switch": "Toggle",
    # gmi bridge emits already-ArkTS names; double-mapping through this table
    # needs the ArkTS spellings present as keys as well (casefolded).
    "toggle": "Toggle",
    "checkbox": "Checkbox",
    "radiobutton": "Radio",
    "progressbar": "Progress",
    "webview": "Web",
    "view": "Row",
    "row": "Row",
}
CARRIERS = {
    "ROUTE_PAGE": "PAGE",
    "ARKUI_PAGE": "PAGE",
    "PAGE": "PAGE",
    "ROUTE": "PAGE",
    "DIALOG": "DIALOG",
    "ARKUI_DIALOG": "DIALOG",
    "POPUP": "POPUP",
    "SHEET": "SHEET",
    "WIDGET": "WIDGET",
}
CONSERVATION_FIELDS = (
    "source_geometry", "visible_text", "assets", "states", "events_actions",
    "entry_conditions", "transitions", "business_rules", "data_dependencies",
    "side_effects", "capability_dependencies", "source_refs", "gmi_fields", "gmi_options",
    "gmi_navigation", "gmi_motion", "behavior_bindings",
)


def _canonical_sha(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _nonempty_list(contract: dict[str, Any], field: str, page_id: str) -> list[Any]:
    value = contract.get(field)
    if not isinstance(value, list) or not value:
        raise ValueError(f"{page_id} has no frozen {field.replace('_', ' ')}")
    return value


def compile_arkts_page_plan(
    contract: dict[str, Any],
    contract_sha256: str,
) -> dict[str, Any]:
    if not isinstance(contract, dict):
        raise ValueError("Page acceptance contract must be an object")
    page_id = str(contract.get("page_id", ""))
    if not page_id or not SHA256_RE.fullmatch(contract_sha256):
        raise ValueError("Page-ID and frozen contract SHA-256 are required")
    states = _nonempty_list(contract, "states", page_id)
    # gmi honest baseline: a contract whose Android evidence is entirely
    # PENDING_RUNTIME_VERIFY has no frozen component facts by definition;
    # empty components are then truthful and parity relies on UiTest/business
    # assertions. Any accepted baseline keeps the non-empty invariant.
    evidence_hashes = contract.get("android_evidence_hashes")
    pending_only_baseline = (
        isinstance(evidence_hashes, list)
        and bool(evidence_hashes)
        and all(isinstance(record, dict) and record.get("pending_runtime_verify") is True for record in evidence_hashes)
    )
    if pending_only_baseline:
        components = contract.get("components")
        if not isinstance(components, list):
            raise ValueError(f"{page_id} has no frozen components")
    else:
        components = _nonempty_list(contract, "components", page_id)
    targets = _nonempty_list(contract, "phase3_targets", page_id)
    state_ids: list[str] = []
    for state in states:
        if not isinstance(state, dict) or not state.get("state_id") or not state.get("records"):
            raise ValueError(f"{page_id} has an incomplete frozen state")
        state_id = str(state["state_id"])
        if state_id in state_ids:
            raise ValueError(f"{page_id} has duplicate State-ID {state_id}")
        state_ids.append(state_id)
    target_by_state: dict[str, dict[str, str]] = {}
    for state_id in state_ids:
        matches = [row for row in targets if isinstance(row, dict) and str(row.get("state_id")) == state_id]
        identities = {
            (str(row.get("target_kind", "")), str(row.get("target_id", "")), str(row.get("harmony_module_id", "")))
            for row in matches
        }
        if len(identities) != 1 or any(not value for identity in identities for value in identity):
            raise ValueError(f"{page_id} {state_id} has missing or conflicting Phase 3 target")
        target_kind, target_id, module_id = next(iter(identities))
        target_by_state[state_id] = {
            "target_kind": target_kind,
            "target_id": target_id,
            "harmony_module_id": module_id,
        }
    android_carrier = str(contract.get("carrier_type", ""))
    if android_carrier not in {"PAGE", "DIALOG", "SHEET", "POPUP", "WIDGET"}:
        raise ValueError(f"{page_id} has no frozen Android carrier_type")
    target_carriers = {CARRIERS.get(target["target_kind"].upper(), "") for target in target_by_state.values()}
    if target_carriers != {android_carrier}:
        # Named-deviation tolerance (SKILL.md person-approval clause): the
        # contract's carrier_deviation block must match the computed pair
        # exactly; anything else stays fail-closed.
        deviation = contract.get("carrier_deviation")
        sanctioned = (
            isinstance(deviation, dict)
            and str(deviation.get("expected_carrier", "")).upper() == android_carrier
            and target_carriers == {str(deviation.get("provided_carrier", "")).upper()}
        )
        if not sanctioned:
            raise ValueError(
                f"{page_id} Phase 3 carrier {sorted(target_carriers)} differs from frozen Android carrier {android_carrier}"
            )
    component_plans: list[dict[str, Any]] = []
    locators: set[tuple[str, str]] = set()
    for component in components:
        if not isinstance(component, dict):
            raise ValueError(f"{page_id} has malformed frozen component")
        component_id = str(component.get("component_id", ""))
        source_type = str(component.get("type", component.get("component_type", "")))
        arkts_type = COMPONENT_TYPES.get(source_type.casefold())
        locator = ("ID", component_id)
        if not component_id or component.get("page_id") != page_id or locator in locators:
            raise ValueError(f"{page_id} has non-unique locator for required component {component_id!r}")
        if not arkts_type:
            raise ValueError(f"{page_id} has unmapped Android component type: {source_type!r}")
        locators.add(locator)
        component_plans.append({
            "component_id": component_id,
            "source_type": source_type,
            "arkts_type": arkts_type,
            "arkts_test_tag": component_id,
            "locator": {"strategy": "ID", "value": component_id},
            "source_record": copy.deepcopy(component),
            "source_record_sha256": _canonical_sha(component),
        })
    source_refs = {
        "code_map": copy.deepcopy(contract.get("code_map", [])),
        "android_evidence_hashes": copy.deepcopy(contract.get("android_evidence_hashes", [])),
    }
    plan: dict[str, Any] = {
        "schema_version": "arkts-page-plan-v1",
        "page_id": page_id,
        "page_name": str(contract.get("page_name", "")),
        "source_contract_sha256": contract_sha256,
        "ui_understanding_policy": "FROZEN_CONTRACT_ONLY_NO_FREE_INFERENCE",
        "carrier": {
            "android_carrier_type": android_carrier,
            "source_target_kinds": sorted({target["target_kind"] for target in target_by_state.values()}),
            "arkts_carrier": android_carrier,
            "state_targets": target_by_state,
        },
        "components": component_plans,
        "source_geometry": copy.deepcopy(contract.get("source_geometry", [])),
        "visible_text": copy.deepcopy(contract.get("visible_text", [])),
        "assets": copy.deepcopy(contract.get("assets", [])),
        "states": copy.deepcopy(states),
        "events_actions": copy.deepcopy(contract.get("interaction_bindings", [])),
        "entry_conditions": copy.deepcopy(contract.get("entry_conditions", [])),
        "transitions": copy.deepcopy(contract.get("transitions", [])),
        "business_rules": copy.deepcopy(contract.get("business_rules", [])),
        "data_dependencies": copy.deepcopy(contract.get("data_dependencies", [])),
        "side_effects": copy.deepcopy(contract.get("side_effects", [])),
        "capability_dependencies": copy.deepcopy(contract.get("system_capabilities", [])),
        "gmi_fields": copy.deepcopy(contract.get("gmi_fields", [])),
        "gmi_options": copy.deepcopy(contract.get("gmi_options", [])),
        "gmi_navigation": copy.deepcopy(contract.get("gmi_navigation", [])),
        "gmi_motion": copy.deepcopy(contract.get("gmi_motion", [])),
        "behavior_bindings": copy.deepcopy(contract.get("behavior_bindings", [])),
        "source_refs": source_refs,
        "required_h4env_ids": sorted(str(value) for value in contract.get("required_h4env_ids", [])),
        "comparison_policy": copy.deepcopy(contract.get("comparison_policy", {})),
    }
    plan["conservation_sha256"] = {
        "components": _canonical_sha(components),
        **{field: _canonical_sha(plan[field]) for field in CONSERVATION_FIELDS},
    }
    return plan


def validate_arkts_page_plan(
    plan: dict[str, Any],
    contract: dict[str, Any],
    contract_sha256: str,
) -> None:
    expected = compile_arkts_page_plan(contract, contract_sha256)
    if not isinstance(plan, dict) or set(plan) != set(expected):
        raise ValueError("ArkTS page plan field set differs from the frozen contract")
    for field in expected:
        if plan.get(field) != expected[field]:
            label = "components conservation" if field in {"components", "conservation_sha256"} else field
            raise ValueError(f"ArkTS page plan {label} differs from the frozen contract")
