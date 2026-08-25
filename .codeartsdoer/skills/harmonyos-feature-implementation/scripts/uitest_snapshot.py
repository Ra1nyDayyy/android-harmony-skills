#!/usr/bin/env python3
"""Validate formal, hash-bound UiTest snapshot evidence."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from _common import load_json, sha256_file


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
METADATA_FIELDS = {
    "schema_version", "probe_id", "page_id", "state_id", "bundle_name", "carrier",
    "target_id", "result_path", "result_sha256", "operation_trace_path",
    "operation_trace_sha256", "screenshot_path", "screenshot_sha256",
    "generation_manifest_sha256", "page_plan_sha256", "test_hap_sha256",
    "final_hap_sha256", "device_identity_sha256", "command_sha256",
}
COMPONENT_FIELDS = {
    "component_id", "type", "text", "bounds", "visible", "enabled", "clickable",
    "visibility_basis", "locator_strategy", "locator_value", "match_count",
}


def _regular_child(root: Path, name: str) -> Path:
    path = root / name
    if not path.is_file() or path.is_symlink() or path.parent.resolve() != root.resolve():
        raise ValueError(f"UiTest evidence file is missing or non-canonical: {name}")
    return path


def _hash(value: str, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value) or value == "0" * 64:
        raise ValueError(f"UiTest {label} is not a frozen SHA-256")
    return value


def validate_uitest_evidence(
    directory: Path,
    probe: dict[str, Any],
    *,
    page_id: str,
    state_id: str,
    bundle_name: str,
    carrier: str,
    target_id: str,
    generation_manifest_sha256: str,
    page_plan_sha256: str,
    test_hap_sha256: str,
    final_hap_sha256: str,
    device_identity_sha256: str,
    command_sha256: str,
    required_event_ids: set[str],
    required_transition_ids: set[str],
) -> dict[str, Any]:
    directory = Path(directory).resolve(strict=True)
    metadata_path = _regular_child(directory, "ui-test-snapshot-metadata.json")
    result_path = _regular_child(directory, "ui-test-snapshot.json")
    trace_path = _regular_child(directory, "ui-test-snapshot-operation-trace.json")
    screenshot_path = _regular_child(directory, "ui-test-snapshot.png")
    metadata = load_json(metadata_path)
    if not isinstance(metadata, dict) or set(metadata) != METADATA_FIELDS:
        raise ValueError("UiTest metadata field set differs")
    expected_scalars = {
        "schema_version": "ui-test-snapshot-evidence-v1",
        "probe_id": f"{page_id}::{state_id}", "page_id": page_id, "state_id": state_id,
        "bundle_name": bundle_name, "carrier": carrier, "target_id": target_id,
        "result_path": "ui-test-snapshot.json",
        "operation_trace_path": "ui-test-snapshot-operation-trace.json",
        "screenshot_path": "ui-test-snapshot.png",
        "generation_manifest_sha256": generation_manifest_sha256,
        "page_plan_sha256": page_plan_sha256,
        "test_hap_sha256": test_hap_sha256, "final_hap_sha256": final_hap_sha256,
        "device_identity_sha256": device_identity_sha256, "command_sha256": command_sha256,
    }
    for field, expected in expected_scalars.items():
        _hash(expected, field) if field.endswith("sha256") else None
        if metadata.get(field) != expected:
            raise ValueError(f"UiTest {field} differs from frozen execution")
    for field, path in (
        ("result_sha256", result_path),
        ("operation_trace_sha256", trace_path),
        ("screenshot_sha256", screenshot_path),
    ):
        _hash(str(metadata.get(field, "")), field)
        if metadata[field] != sha256_file(path):
            raise ValueError(f"UiTest {field} differs")
    if not isinstance(probe, dict) or probe.get("probe_id") != f"{page_id}::{state_id}":
        raise ValueError("UiTest generated probe identity differs")
    result = load_json(result_path)
    components = result.get("components") if isinstance(result, dict) else None
    if not isinstance(components, list) or result.get("probe_id") != probe["probe_id"]:
        raise ValueError("UiTest component snapshot identity differs")
    by_id: dict[str, dict[str, Any]] = {}
    by_locator: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for component in components:
        if not isinstance(component, dict) or set(component) != COMPONENT_FIELDS:
            raise ValueError("UiTest component snapshot field set differs")
        component_id = str(component.get("component_id", ""))
        locator = (str(component.get("locator_strategy", "")), str(component.get("locator_value", "")))
        if not component_id or component_id in by_id:
            raise ValueError("UiTest component snapshot has duplicate component identity")
        if component.get("match_count") != 1:
            raise ValueError(f"UiTest component locator is not unique: {component_id}")
        bounds = component.get("bounds")
        if (
            not isinstance(bounds, dict)
            or set(bounds) != {"left", "top", "right", "bottom"}
            or any(not isinstance(bounds[key], (int, float)) for key in bounds)
            or bounds["right"] <= bounds["left"] or bounds["bottom"] <= bounds["top"]
        ):
            raise ValueError(f"UiTest component bounds are invalid: {component_id}")
        if any(not isinstance(component[field], bool) for field in ("visible", "enabled", "clickable")):
            raise ValueError(f"UiTest component flags are invalid: {component_id}")
        if component.get("visible") is not True or component.get("visibility_basis") != "UNIQUE_MATCH_AND_VALID_BOUNDS":
            raise ValueError(f"UiTest component visibility evidence is invalid: {component_id}")
        by_id[component_id] = component
        by_locator.setdefault(locator, []).append(component)
    bindings: dict[str, dict[str, Any]] = {}
    required = probe.get("required_components")
    if not isinstance(required, list) or not required:
        raise ValueError("UiTest generated probe has no required components")
    for declaration in required:
        component_id = str(declaration.get("component_id", "")) if isinstance(declaration, dict) else ""
        locator = (
            str(declaration.get("locator_strategy", "")),
            str(declaration.get("locator_value", "")),
        ) if isinstance(declaration, dict) else ("", "")
        matches = by_locator.get(locator, [])
        if component_id not in by_id or len(matches) != 1 or matches[0].get("component_id") != component_id:
            raise ValueError(f"UiTest required component is missing or locator is not unique: {component_id}")
        if declaration.get("expected_text") not in (None, "") and by_id[component_id].get("text") != declaration["expected_text"]:
            raise ValueError(f"UiTest required component text differs: {component_id}")
        bindings[component_id] = by_id[component_id]
    trace = load_json(trace_path)
    if not isinstance(trace, list) or any(not isinstance(row, dict) for row in trace):
        raise ValueError("UiTest operation trace is malformed")
    observed = {"EVENT": set(), "TRANSITION": set()}
    for row in trace:
        subject_type = str(row.get("subject_type", ""))
        subject_id = str(row.get("subject_id", ""))
        if subject_type not in observed or not subject_id or subject_id in observed[subject_type]:
            raise ValueError("UiTest operation trace identity is invalid or duplicated")
        if not str(row.get("action", "")).strip() or not str(row.get("observable_result", "")).strip():
            raise ValueError(f"UiTest operation trace lacks action/result: {subject_id}")
        observed[subject_type].add(subject_id)
    if observed["EVENT"] != required_event_ids or observed["TRANSITION"] != required_transition_ids:
        raise ValueError("UiTest operation trace differs from frozen events/transitions")
    return {"metadata": metadata, "components": components, "component_bindings": bindings, "operation_trace": trace}
