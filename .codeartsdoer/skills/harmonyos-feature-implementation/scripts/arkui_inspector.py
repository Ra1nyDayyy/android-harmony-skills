#!/usr/bin/env python3
"""Validate and normalize ArkUI Inspector snapshots for formal Phase 4 evidence."""

from __future__ import annotations

SCRIPT_INTERFACE = "internal-module"
SCRIPT_INTERFACE_REASON = "Inspector normalization library imported by Phase 4 evidence and audit scripts."

import hashlib
import json
from typing import Any


INSPECTOR_SOURCE = "ARKUI_UI_CONTEXT"
INSPECTOR_APIS = {"getFilteredInspectorTree", "getFilteredInspectorTreeById"}


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _children(value: dict[str, Any]) -> list[Any]:
    for key in ("$children", "children"):
        children = value.get(key)
        if isinstance(children, list):
            return children
    return []


def _node_id(value: dict[str, Any], fallback: str) -> str:
    for key in ("$ID", "$id", "key", "id", "resourceId", "resource_id"):
        candidate = value.get(key)
        if isinstance(candidate, (str, int)) and str(candidate).strip():
            return str(candidate)
    return fallback


def _node_type(value: dict[str, Any]) -> str:
    for key in ("$type", "type", "componentType", "component_type"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return "UNKNOWN"


def _resource_id(value: dict[str, Any]) -> str:
    for key in ("resourceId", "resource_id", "id"):
        candidate = value.get(key)
        if isinstance(candidate, (str, int)) and str(candidate).strip():
            return str(candidate)
    return ""


def _text(value: dict[str, Any]) -> str:
    for key in ("content", "text", "value", "label"):
        candidate = value.get(key)
        if isinstance(candidate, (str, int, float, bool)):
            return str(candidate)
    return ""


def _rect(value: dict[str, Any]) -> dict[str, float] | None:
    for key in ("$rect", "rect", "bounds", "geometry"):
        rect = value.get(key)
        if not isinstance(rect, dict):
            continue
        aliases = {
            "x": ("x", "left"), "y": ("y", "top"),
            "width": ("width", "w"), "height": ("height", "h"),
        }
        result: dict[str, float] = {}
        for name, candidates in aliases.items():
            raw = next((rect.get(item) for item in candidates if item in rect), None)
            if not isinstance(raw, (int, float)):
                break
            result[name] = float(raw)
        if len(result) == 4 and result["width"] > 0 and result["height"] > 0:
            return result
    return None


def flatten_tree(raw_tree: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []

    def visit(item: Any, path: str, parent_id: str) -> None:
        if not isinstance(item, dict):
            return
        node_id = _node_id(item, path)
        node = {
            "inspector_node_id": node_id,
            "parent_inspector_node_id": parent_id,
            "type": _node_type(item),
            "resource_id": _resource_id(item),
            "text": _text(item),
            "bounds": _rect(item),
            "raw_sha256": canonical_sha256(item),
        }
        nodes.append(node)
        for index, child in enumerate(_children(item)):
            visit(child, f"{path}/{index}", node_id)

    visit(raw_tree, "root", "")
    return nodes


def validate_and_normalize(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("UI tree must be a JSON object")
    inspector = value.get("inspector")
    if not isinstance(inspector, dict):
        raise ValueError("UI tree lacks an ArkUI Inspector envelope")
    if inspector.get("schema_version") != 1:
        raise ValueError("ArkUI Inspector schema_version must be 1")
    if inspector.get("source") != INSPECTOR_SOURCE:
        raise ValueError("UI tree source is not ArkUI UIContext Inspector")
    if inspector.get("api") not in INSPECTOR_APIS:
        raise ValueError("UI tree uses an unsupported ArkUI Inspector API")
    if inspector.get("capture_mode") != "OHOS_TEST_BRIDGE":
        raise ValueError("ArkUI Inspector capture must come from the ohosTest bridge")
    if inspector.get("bridge_contract") != "arkui-inspector-bridge-v1":
        raise ValueError("ArkUI Inspector bridge contract differs")
    raw_tree = inspector.get("raw_tree")
    if not isinstance(raw_tree, dict) or not raw_tree:
        raise ValueError("ArkUI Inspector raw_tree is empty")
    digest = canonical_sha256(raw_tree)
    if inspector.get("raw_tree_sha256") != digest:
        raise ValueError("ArkUI Inspector raw_tree hash differs")
    nodes = flatten_tree(raw_tree)
    if not nodes:
        raise ValueError("ArkUI Inspector raw_tree contains no components")
    normalized = dict(value)
    normalized["inspector"] = dict(inspector)
    normalized["root"] = nodes[0]
    normalized["nodes"] = nodes
    normalized["inspector"]["normalized_nodes_sha256"] = canonical_sha256(nodes)
    root_bounds = nodes[0].get("bounds")
    if isinstance(root_bounds, dict):
        normalized["bounds"] = root_bounds
    return normalized


def validate_normalized(value: Any) -> dict[str, Any]:
    normalized = validate_and_normalize(value)
    if value.get("root") != normalized["root"] or value.get("nodes") != normalized["nodes"]:
        raise ValueError("UI tree normalized nodes differ from ArkUI Inspector raw_tree")
    inspector = value["inspector"]
    if inspector.get("normalized_nodes_sha256") != canonical_sha256(normalized["nodes"]):
        raise ValueError("ArkUI Inspector normalized node hash differs")
    if isinstance(normalized["root"].get("bounds"), dict) and value.get("bounds") != normalized["bounds"]:
        raise ValueError("UI tree bounds differ from the Inspector root bounds")
    return normalized


def validate_operation_snapshot(value: Any) -> None:
    if not isinstance(value, dict):
        raise ValueError("Operation snapshot must be an ArkUI Inspector envelope")
    validate_and_normalize({"inspector": value})


def bind_required_components(
    nodes: list[dict[str, Any]], required_ids: list[str], locators: dict[str, Any]
) -> dict[str, dict[str, str]]:
    bindings: dict[str, dict[str, str]] = {}
    for component_id in required_ids:
        locator = locators.get(component_id, {}) if isinstance(locators, dict) else {}
        resource_id = str(locator.get("resource_id", "")).strip()
        text = str(locator.get("text", "")).strip()
        component_type = str(locator.get("type", "")).strip().lower()
        matches: list[tuple[dict[str, Any], str]] = []
        if resource_id:
            matches = [(node, "RESOURCE_ID") for node in nodes if node.get("resource_id") == resource_id]
        if not matches and text:
            matches = [
                (node, "TEXT_TYPE_UNIQUE") for node in nodes
                if node.get("text") == text
                and (not component_type or str(node.get("type", "")).lower() == component_type)
            ]
        if len(matches) != 1:
            raise ValueError(
                f"ArkUI Inspector cannot uniquely bind Android component {component_id}: "
                f"matches={len(matches)}"
            )
        node, basis = matches[0]
        bindings[component_id] = {
            "inspector_node_id": str(node["inspector_node_id"]),
            "basis": basis,
        }
    return bindings
