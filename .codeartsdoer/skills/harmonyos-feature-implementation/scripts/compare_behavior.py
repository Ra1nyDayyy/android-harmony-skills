#!/usr/bin/env python3
"""Compare behavior, side effects, and navigation from factual values only."""

from __future__ import annotations

from typing import Any

from comparison_common import ComparisonResult, comparison_result


def _state_record(contract: dict[str, Any], state_id: str) -> dict[str, Any]:
    for state in contract.get("states", []):
        if isinstance(state, dict) and state.get("state_id") == state_id:
            records = state.get("records")
            if isinstance(records, list) and records and isinstance(records[0], dict):
                return records[0]
    return {}


def compare_behavior(contract: dict[str, Any], metadata: dict[str, Any], assertions: dict[str, Any]) -> ComparisonResult:
    expected = _state_record(contract, str(metadata.get("state_id", ""))).get("expected_observable")
    rows = assertions.get("assertions") if isinstance(assertions.get("assertions"), list) else []
    candidates = [row for row in rows if isinstance(row, dict) and row.get("kind") == "ANDROID_EXPECTED_OBSERVABLE"]
    actual = candidates[0].get("actual") if len(candidates) == 1 else None
    differences = [] if len(candidates) == 1 and actual == expected else [{"kind": "OBSERVABLE_MISMATCH", "expected": expected, "actual": actual, "candidate_count": len(candidates)}]
    return comparison_result("CMP-BEHAVIOR", "behavior", expected, actual, {"assertions_examined": len(rows)}, differences)


def compare_side_effects(contract: dict[str, Any], assertions: dict[str, Any]) -> ComparisonResult:
    expected_rows = [row for row in contract.get("side_effects", []) if isinstance(row, dict)]
    assertion_rows = assertions.get("assertions") if isinstance(assertions.get("assertions"), list) else []
    actual_by_id: dict[str, dict[str, Any]] = {}
    for row in assertion_rows:
        if not isinstance(row, dict) or row.get("kind") != "SIDE_EFFECT":
            continue
        for subject in row.get("subject_ids", []):
            actual_by_id[str(subject)] = row
    differences: list[dict[str, object]] = []
    actual_values: dict[str, Any] = {}
    for expected in sorted(expected_rows, key=lambda item: str(item.get("side_effect_id", ""))):
        side_effect_id = str(expected.get("side_effect_id", ""))
        row = actual_by_id.get(side_effect_id)
        actual = row.get("actual") if row else None
        actual_values[side_effect_id] = actual
        expected_hash = expected.get("expected_payload_sha256")
        actual_hash = actual.get("payload_sha256") if isinstance(actual, dict) else None
        if expected.get("operator") != "HASH_EQUALS" or actual_hash != expected_hash:
            differences.append({"kind": "SIDE_EFFECT_MISMATCH", "side_effect_id": side_effect_id, "expected_payload_sha256": expected_hash, "actual_payload_sha256": actual_hash})
    return comparison_result("CMP-SIDE-EFFECT", "side-effect", expected_rows, actual_values, {"required_side_effects": len(expected_rows)}, differences)


def compare_navigation(contract: dict[str, Any], trace: list[Any]) -> ComparisonResult:
    expected_rows = [row for row in contract.get("transitions", []) if isinstance(row, dict)]
    actual_by_id = {str(row.get("subject_id", "")): row for row in trace if isinstance(row, dict) and row.get("subject_type") == "TRANSITION"}
    fields = ("source_page_id", "source_state_id", "target_page_id", "target_state_id", "back_behavior", "carrier_type")
    differences: list[dict[str, object]] = []
    normalized_actual: list[dict[str, Any]] = []
    for expected in sorted(expected_rows, key=lambda item: str(item.get("transition_id", ""))):
        transition_id = str(expected.get("transition_id", ""))
        actual = actual_by_id.get(transition_id)
        if actual is None:
            differences.append({"kind": "MISSING_TRANSITION", "transition_id": transition_id})
            continue
        normalized_actual.append({"transition_id": transition_id, **{field: actual.get(field) for field in fields}})
        for field in fields:
            if expected.get(field) != actual.get(field):
                differences.append({"kind": "NAVIGATION_MISMATCH", "transition_id": transition_id, "field": field, "expected": expected.get(field), "actual": actual.get(field)})
    return comparison_result("CMP-NAVIGATION", "navigation", expected_rows, normalized_actual, {"required_transitions": len(expected_rows)}, differences)
