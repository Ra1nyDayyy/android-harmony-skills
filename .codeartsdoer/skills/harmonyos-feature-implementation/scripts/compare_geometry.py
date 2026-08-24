#!/usr/bin/env python3
"""Compare required component bounds after density normalization."""

from __future__ import annotations

from typing import Any

from comparison_common import ComparisonResult, comparison_result


def _geometry_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            rows.extend(_geometry_rows(item))
    elif isinstance(value, dict):
        if value.get("component_id") and all(key in value for key in ("x", "y", "width", "height")):
            rows.append(value)
        for child_key in ("children", "nodes", "components", "root"):
            if child_key in value:
                rows.extend(_geometry_rows(value[child_key]))
    return rows


def _density(value: dict[str, Any]) -> float:
    density = value.get("density")
    if isinstance(density, (int, float)) and density > 0:
        return float(density)
    dpi = value.get("density_dpi")
    return float(dpi) / 160.0 if isinstance(dpi, (int, float)) and dpi > 0 else 1.0


def compare_geometry(contract: dict[str, Any], snapshot: dict[str, Any]) -> ComparisonResult:
    expected_rows = _geometry_rows(contract.get("source_geometry"))
    expected_by_id = {str(row["component_id"]): row for row in expected_rows}
    actual_rows = snapshot.get("components") if isinstance(snapshot.get("components"), list) else []
    actual_by_id = {str(row.get("component_id", "")): row for row in actual_rows if isinstance(row, dict)}
    source_root = expected_rows[0] if expected_rows else {}
    source_density = _density(source_root)
    actual_density = _density(snapshot)
    viewport = snapshot.get("viewport") if isinstance(snapshot.get("viewport"), dict) else {}
    viewport_width = float(viewport.get("width", source_root.get("viewport_width", 0)) or 0) / actual_density
    viewport_height = float(viewport.get("height", source_root.get("viewport_height", 0)) or 0) / actual_density
    tolerances = {
        "x": max(2.0, viewport_width * 0.005), "width": max(2.0, viewport_width * 0.005),
        "y": max(2.0, viewport_height * 0.005), "height": max(2.0, viewport_height * 0.005),
    }
    differences: list[dict[str, object]] = []
    normalized_expected: dict[str, dict[str, float]] = {}
    normalized_actual: dict[str, dict[str, float]] = {}
    for component_id, expected in sorted(expected_by_id.items()):
        normalized_expected[component_id] = {key: float(expected[key]) / source_density for key in ("x", "y", "width", "height")}
        actual = actual_by_id.get(component_id)
        bounds = actual.get("bounds") if isinstance(actual, dict) and isinstance(actual.get("bounds"), dict) else None
        if bounds is None:
            differences.append({"kind": "MISSING_BOUNDS", "component_id": component_id})
            continue
        normalized_actual[component_id] = {
            "x": float(bounds.get("left", 0)) / actual_density,
            "y": float(bounds.get("top", 0)) / actual_density,
            "width": (float(bounds.get("right", 0)) - float(bounds.get("left", 0))) / actual_density,
            "height": (float(bounds.get("bottom", 0)) - float(bounds.get("top", 0))) / actual_density,
        }
        for field in ("x", "y", "width", "height"):
            delta = abs(normalized_expected[component_id][field] - normalized_actual[component_id][field])
            if delta > tolerances[field]:
                differences.append({"kind": "GEOMETRY_MISMATCH", "component_id": component_id, "field": field, "delta_dp": round(delta, 6), "tolerance_dp": round(tolerances[field], 6)})
    return comparison_result(
        "CMP-GEOMETRY", "geometry", normalized_expected, normalized_actual,
        {"source_density": source_density, "actual_density": actual_density, "compared_components": len(expected_by_id)}, differences,
    )
