#!/usr/bin/env python3
"""Compare carrier and required component facts without trusting external status."""

from __future__ import annotations

from typing import Any

from comparison_common import ComparisonResult, comparison_result


TYPE_MAP = {
    "textview": "text", "edittext": "textinput", "imageview": "image",
    "recyclerview": "list", "checkbox": "checkbox", "switch": "toggle",
    "framelayout": "stack", "button": "button", "text": "text",
    "textinput": "textinput", "image": "image", "list": "list",
    "toggle": "toggle", "stack": "stack", "column": "column", "row": "row",
}


def normalized_type(component: dict[str, Any]) -> str:
    raw = str(component.get("type", component.get("class_name", ""))).split(".")[-1].lower()
    if raw == "linearlayout":
        return "row" if str(component.get("orientation", "")).lower() == "horizontal" else "column"
    return TYPE_MAP.get(raw, raw)


def compare_carrier(contract: dict[str, Any], metadata: dict[str, Any]) -> ComparisonResult:
    expected = str(contract.get("carrier_type", "")).upper()
    actual = str(metadata.get("carrier", "")).upper()
    differences = [] if expected == actual else [{"field": "carrier", "expected": expected, "actual": actual}]
    return comparison_result("CMP-CARRIER", "carrier", expected, actual, {}, differences)


def compare_components(contract: dict[str, Any], snapshot: dict[str, Any]) -> ComparisonResult:
    expected_rows = contract.get("components") if isinstance(contract.get("components"), list) else []
    actual_rows = snapshot.get("components") if isinstance(snapshot.get("components"), list) else []
    expected = {str(row.get("component_id", "")): row for row in expected_rows if isinstance(row, dict)}
    actual = {str(row.get("component_id", "")): row for row in actual_rows if isinstance(row, dict)}
    differences: list[dict[str, object]] = []
    for component_id in sorted(set(expected) - set(actual)):
        differences.append({"kind": "MISSING_COMPONENT", "component_id": component_id})
    for component_id in sorted(set(actual) - set(expected)):
        differences.append({"kind": "UNEXPECTED_COMPONENT", "component_id": component_id})
    for component_id in sorted(set(expected) & set(actual)):
        wanted, observed = expected[component_id], actual[component_id]
        fields = {
            "type": (normalized_type(wanted), normalized_type(observed)),
            "text": (str(wanted.get("text", "")), str(observed.get("text", ""))),
            "visible": (bool(wanted.get("visible", True)), observed.get("visible")),
            "enabled": (bool(wanted.get("enabled", True)), observed.get("enabled")),
            "clickable": (bool(wanted.get("clickable", False)), observed.get("clickable")),
        }
        for field, (wanted_value, observed_value) in fields.items():
            if wanted_value != observed_value:
                differences.append({"kind": "PROPERTY_MISMATCH", "component_id": component_id, "field": field, "expected": wanted_value, "actual": observed_value})
    normalized_expected = [{"component_id": key, "type": normalized_type(value)} for key, value in sorted(expected.items())]
    normalized_actual = [{"component_id": key, "type": normalized_type(value)} for key, value in sorted(actual.items())]
    return comparison_result(
        "CMP-COMPONENT-TREE", "component-tree", normalized_expected, normalized_actual,
        {"required_components": len(expected), "observed_components": len(actual)}, differences,
    )
