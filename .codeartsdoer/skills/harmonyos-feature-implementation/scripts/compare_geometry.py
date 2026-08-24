#!/usr/bin/env python3
"""Compare required component bounds after density normalization."""

from __future__ import annotations

import math
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
    if density is not None:
        if isinstance(density, (int, float)) and not isinstance(density, bool) and math.isfinite(float(density)) and float(density) > 0:
            return float(density)
        raise ValueError("Geometry density must be finite and positive")
    dpi = value.get("density_dpi")
    if dpi is not None and (not isinstance(dpi, (int, float)) or isinstance(dpi, bool) or not math.isfinite(float(dpi)) or float(dpi) <= 0):
        raise ValueError("Geometry density DPI must be finite and positive")
    result = float(dpi) / 160.0 if dpi is not None else 1.0
    if not math.isfinite(result) or result <= 0:
        raise ValueError("Geometry density must be finite and positive")
    return result


def _finite(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"Geometry {label} must be finite")
    return float(value)


def compare_geometry(contract: dict[str, Any], snapshot: dict[str, Any]) -> ComparisonResult:
    expected_rows = _geometry_rows(contract.get("source_geometry"))
    expected_by_id = {str(row["component_id"]): row for row in expected_rows}
    actual_rows = snapshot.get("components") if isinstance(snapshot.get("components"), list) else []
    actual_by_id = {str(row.get("component_id", "")): row for row in actual_rows if isinstance(row, dict)}
    source_root = expected_rows[0] if expected_rows else {}
    source_density = _density(source_root)
    actual_density = _density(snapshot)
    viewport = snapshot.get("viewport") if isinstance(snapshot.get("viewport"), dict) else {}
    region = snapshot.get("application_region") if isinstance(snapshot.get("application_region"), dict) else {}
    viewport_width = _finite(viewport.get("width", region.get("width", 0)), "viewport width") / actual_density
    viewport_height = _finite(viewport.get("height", region.get("height", 0)), "viewport height") / actual_density
    if viewport_width <= 0 or viewport_height <= 0:
        raise ValueError("Geometry viewport must be positive")
    tolerances = {
        "x": max(2.0, viewport_width * 0.005), "width": max(2.0, viewport_width * 0.005),
        "y": max(2.0, viewport_height * 0.005), "height": max(2.0, viewport_height * 0.005),
    }
    differences: list[dict[str, object]] = []
    normalized_expected: dict[str, dict[str, float]] = {}
    normalized_actual: dict[str, dict[str, float]] = {}
    for component_id, expected in sorted(expected_by_id.items()):
        normalized_expected[component_id] = {key: _finite(expected.get(key), f"{component_id}.{key}") / source_density for key in ("x", "y", "width", "height")}
        if normalized_expected[component_id]["width"] <= 0 or normalized_expected[component_id]["height"] <= 0:
            raise ValueError(f"Geometry expected bounds must be positive: {component_id}")
        actual = actual_by_id.get(component_id)
        bounds = actual.get("bounds") if isinstance(actual, dict) and isinstance(actual.get("bounds"), dict) else None
        if bounds is None:
            differences.append({"kind": "MISSING_BOUNDS", "component_id": component_id})
            continue
        normalized_actual[component_id] = {
            "x": _finite(bounds.get("left"), f"{component_id}.left") / actual_density,
            "y": _finite(bounds.get("top"), f"{component_id}.top") / actual_density,
            "width": (_finite(bounds.get("right"), f"{component_id}.right") - _finite(bounds.get("left"), f"{component_id}.left")) / actual_density,
            "height": (_finite(bounds.get("bottom"), f"{component_id}.bottom") - _finite(bounds.get("top"), f"{component_id}.top")) / actual_density,
        }
        if normalized_actual[component_id]["width"] <= 0 or normalized_actual[component_id]["height"] <= 0:
            raise ValueError(f"Geometry bounds must be positive: {component_id}")
        for field in ("x", "y", "width", "height"):
            delta = abs(normalized_expected[component_id][field] - normalized_actual[component_id][field])
            if delta > tolerances[field]:
                differences.append({"kind": "GEOMETRY_MISMATCH", "component_id": component_id, "field": field, "delta_dp": round(delta, 6), "tolerance_dp": round(tolerances[field], 6)})
    return comparison_result(
        "CMP-GEOMETRY", "geometry", normalized_expected, normalized_actual,
        {"source_density": source_density, "actual_density": actual_density, "compared_components": len(expected_by_id)}, differences,
    )
