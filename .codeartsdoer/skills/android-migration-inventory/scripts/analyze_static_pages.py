#!/usr/bin/env python3
"""Build a deterministic Android page/UI/function candidate graph from frozen source."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from _common import atomic_json, atomic_text, load_json, manifest_lines, sha256_file, utc_now, write_csv


ANDROID = "{http://schemas.android.com/apk/res/android}"
SOURCE_SUFFIXES = {".kt", ".java"}
IGNORED_DIRS = {".git", ".gradle", ".idea", "build", "out", "node_modules"}
PAGE_BASES = (
    "Activity", "AppCompatActivity", "ComponentActivity", "Fragment", "DialogFragment",
    "BottomSheetDialogFragment",
)
COMPOSE_WIDGETS = {
    "Text", "Button", "IconButton", "TextButton", "OutlinedButton", "TextField",
    "OutlinedTextField", "Image", "Icon", "Checkbox", "Switch", "RadioButton",
    "Slider", "LazyColumn", "LazyRow", "Row", "Column", "Box", "Scaffold",
}

# Phase-2 双门槛契约：每个运行任务必须携带 verification_mode 与 review_tier。
#   SOURCE_ONLY   源码足以确定，不占模拟器时间（不进入运行队列）
#   RUNTIME_UI    必须在模拟器确认页面/弹窗/控件/文案/布局/状态
#   RUNTIME_EFFECT 必须验证真实结果（保存、删除、跳转、重启恢复、数据库变化）
#   REQUIRED      核心功能，100% 运行验证，未完成即阻断
#   REVIEW        低风险/同构重复项，未验证 ≤10% 留给人工复核
TASK_VERIFICATION = {
    "VERIFY_PAGE_DEFAULT_STATE": ("RUNTIME_UI", "REQUIRED"),
    "VERIFY_EVENT": ("RUNTIME_UI", "REQUIRED"),
    "VERIFY_TRANSITION": ("RUNTIME_UI", "REQUIRED"),
    "VERIFY_DYNAMIC_SURFACE": ("RUNTIME_UI", "REQUIRED"),
    "VERIFY_SIDE_EFFECT": ("RUNTIME_EFFECT", "REQUIRED"),
    "VERIFY_SCENARIO": ("RUNTIME_EFFECT", "REQUIRED"),
    # 普通视觉状态分支：同构重复，抽样归 REVIEW；当分支条件是编译期常量
    # （静态已证）时降级 SOURCE_ONLY：不进运行队列（gmi_runtime 按模式分流）、
    # 覆盖率分母排除（gmi_closure），completeness 记 RECORDED。
    "VERIFY_STATE_BRANCH": ("RUNTIME_UI", "REVIEW"),
}

# Compile-time constant grammar for the conservative SOURCE_ONLY downgrade.
# LITERAL  : signed int / float / quoted string / true / false / null
# CONST_ID : SCREAMING_SNAKE identifier (compile-time constant naming convention)
# Anything else (member access "a.b", calls "f()", templates "${}", camelCase
# locals such as isEmpty, enum words such as Loading, compound &&/||) is NOT
# provable at compile time and keeps RUNTIME_UI. Prefer missing cases over
# false positives.
_CT_LITERAL_RE = r"(?:-?\d+(?:\.\d+)?|'[^']*'|\"[^\"]*\"|true|false|null)"
_CT_CONST_ID_RE = r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+|[A-Z]{2,}[A-Z0-9]*"
# Shape 1: [!]boolean-literal / [!]SCREAMING_SNAKE constant as the whole condition.
# Shape 2: literal-or-constant on both sides of one comparison operator.
# A bare non-boolean literal (e.g. "2") is NOT a valid boolean condition and is
# deliberately rejected; numeric/string literals only qualify inside comparisons.
_CT_CONDITION_RE = re.compile(
    rf"^(?:!)?(?:true|false|{_CT_CONST_ID_RE})$"
    rf"|(?:{_CT_LITERAL_RE}|{_CT_CONST_ID_RE})"
    rf"\s*(?:==|!=|<=|>=|<|>)\s*(?:{_CT_LITERAL_RE}|{_CT_CONST_ID_RE})$"
)


def is_compile_time_constant_condition(expression: str | None) -> bool:
    """Conservatively decide whether a state-branch condition is compile-time constant.

    Accepted shapes (after whitespace normalisation):
      A) [!] boolean literals / SCREAMING_SNAKE constants -> "true", "!DEBUG"
      B) single literal-or-constant comparison -> "1 == 1", "'a' != \\"b\\"",
         "MAX_COUNT == 3", "MODE == READING", "FLAG_ON == true"
    Everything else (camelCase locals such as isEmpty or userReady, enum words
    such as Loading, member access, calls, ${} templates, compound &&/||, bare
    non-boolean literals) stays RUNTIME_UI. Prefer missing cases over false
    positives.
    """
    if not expression:
        return False
    normalized = re.sub(r"\s+", " ", expression).strip()
    if normalized in {"true", "false"}:
        return True
    return bool(_CT_CONDITION_RE.fullmatch(normalized))


def classify_task(task_type: str, state_condition: str | None = None) -> tuple[str, str]:
    mode, tier = TASK_VERIFICATION.get(task_type, ("RUNTIME_UI", "REQUIRED"))
    if task_type == "VERIFY_STATE_BRANCH" and is_compile_time_constant_condition(state_condition):
        return "SOURCE_ONLY", tier
    return mode, tier


def stable_id(prefix: str, *values: str) -> str:
    raw = "|".join(values)
    slug = re.sub(r"[^A-Z0-9]+", "-", values[-1].upper()).strip("-")[:44] or "UNKNOWN"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8].upper()
    return f"{prefix}-{slug}-{digest}"


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def camel_to_snake(value: str) -> str:
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", first).lower()


def binding_layouts(text: str) -> list[str]:
    names = set()
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9]*Binding)(?:::inflate|\.inflate)", text):
        names.add(camel_to_snake(match.group(1)[:-7]))
    for match in re.finditer(r"setContentView\s*\(\s*R\.layout\.([A-Za-z0-9_]+)", text):
        names.add(match.group(1))
    for match in re.finditer(r"\binflate\s*\(\s*R\.layout\.([A-Za-z0-9_]+)", text):
        names.add(match.group(1))
    # Generic reference binding: fragments often return/assign layout ids
    # (e.g. getLayoutResId() { return R.layout.document__fragment__edit; }).
    # Exclude framework layouts (android.R.layout.*) and any other package prefix.
    for match in re.finditer(r"(?<!\.)R\.layout\.([A-Za-z0-9_]+)", text):
        names.add(match.group(1))
    return sorted(names)


def parse_class_layout_bindings(text: str) -> dict[str, list[str]]:
    """Single-file class -> bound-layout names (pure, cache-friendly)."""
    layouts = binding_layouts(text)
    if not layouts:
        return {}
    return {
        match.group(1): layouts
        for match in re.finditer(r"\b(?:open\s+)?class\s+(\w+)", text)
    }


def parse_code_candidates(path_value: str, text: str) -> list[dict[str, Any]]:
    """Single-file declaration rows for full-code-candidates.json (pure, cache-friendly)."""
    rows: list[dict[str, Any]] = []
    for m in re.finditer(r"^\s*(?:class|object|interface|enum class|fun|val|var)\s+(\w+)", text, re.MULTILINE):
        line = line_number(text, m.start())
        code_ref = f"{path_value}:{line}"
        rows.append({
            "code_ref": code_ref, "file_path": path_value, "line": str(line), "symbol": m.group(1),
            "snippet": text[m.start():m.start()+120].split("\n")[0][:120],
            "source_ref": code_ref,
        })
    return rows


def class_layout_bindings(
    project: Path,
    per_file: dict[str, dict[str, Any]] | None = None,
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for path in source_files(project):
        path_value = rel(path, project)
        parsed = per_file.get(path_value) if per_file is not None else None
        if parsed is None or "class_layouts" not in parsed:
            parsed = {"class_layouts": parse_class_layout_bindings(
                path.read_text(encoding="utf-8", errors="replace")
            )}
        result.update(parsed["class_layouts"])
    return result


def source_candidates(root: Path) -> list[Path]:
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.suffix in SOURCE_SUFFIXES
        and not any(part in IGNORED_DIRS for part in path.relative_to(root).parts)
    )


def source_files(root: Path) -> list[Path]:
    return [path for path in source_candidates(root) if path.stat().st_size <= 2 * 1024 * 1024]


def source_scan_ledger(root: Path) -> dict[str, Any]:
    discovered = source_candidates(root)
    parsed = source_files(root)
    parsed_set = set(parsed)
    skipped = [
        {"path": rel(path, root), "reason": "FILE_TOO_LARGE", "size_bytes": path.stat().st_size}
        for path in discovered if path not in parsed_set
    ]
    return {
        "discovered_count": len(discovered),
        "parsed_count": len(parsed),
        "skipped_count": len(skipped),
        "skipped": skipped,
    }


def xml_files(root: Path, category: str) -> list[Path]:
    return sorted(
        path for path in root.rglob("*.xml")
        if f"res/{category}" in path.as_posix().replace("\\", "/")
        and not any(part in IGNORED_DIRS for part in path.relative_to(root).parts)
    )


def feature_candidates(path_value: str, symbol: str, features: list[str]) -> list[str]:
    if len(features) == 1:
        return features
    haystack = re.sub(r"[^a-z0-9]", "", f"{path_value} {symbol}".lower())
    matches = []
    for feature in features:
        tokens = [token.lower() for token in re.split(r"[-_.]", feature) if len(token) > 2 and token.upper() != "FEATURE"]
        if any(re.sub(r"[^a-z0-9]", "", token) in haystack for token in tokens):
            matches.append(feature)
    return sorted(set(matches))


def read_resources(project: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for path in xml_files(project, "values"):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        for child in root:
            name = child.attrib.get("name")
            if name and child.text:
                values[f"@{child.tag}/{name}"] = child.text.strip()
    return values


def resolve_value(value: str, resources: dict[str, str]) -> str:
    seen = set()
    current = value
    while current in resources and current not in seen:
        seen.add(current)
        current = resources[current]
    return current


NON_VISUAL_TAGS = {"layout", "data", "variable", "import"}
VISUAL_EXCLUDE_ATTRS = {"tools:context", "tools:layout"}

def scan_layouts(project: Path, resources: dict[str, str]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    layouts: dict[str, list[dict[str, Any]]] = {}
    issues: list[dict[str, Any]] = []
    for path in xml_files(project, "layout"):
        layout_name = path.stem
        raw_xml = path.read_text(encoding="utf-8", errors="replace")
        try:
            root = ET.fromstring(raw_xml)
        except ET.ParseError as exc:
            issues.append({"kind": "XML_PARSE_ERROR", "source_ref": f"{rel(path, project)}:1", "detail": str(exc)})
            continue
        rows: list[dict[str, Any]] = []
        search_offset = 0

        def is_visual_tag(tag: str) -> bool:
            return tag not in NON_VISUAL_TAGS

        def visit(node: ET.Element, parent_id: str, order: int) -> None:
            nonlocal search_offset
            tag = node.tag.rsplit("}", 1)[-1]
            # Skip DataBinding non-visual wrappers entirely
            if not is_visual_tag(tag):
                for child_order, child in enumerate(list(node), start=1):
                    visit(child, parent_id, child_order)
                return
            tag_match = re.search(rf"<(?:[A-Za-z0-9_.-]+:)?{re.escape(tag)}\b", raw_xml[search_offset:])
            node_offset = search_offset + tag_match.start() if tag_match else search_offset
            if tag_match:
                search_offset = search_offset + tag_match.end()
            raw_id = node.attrib.get(ANDROID + "id", "")
            name = raw_id.rsplit("/", 1)[-1] if "/" in raw_id else f"{tag}-{order}"
            source_path = rel(path, project)
            component_id = stable_id("COMP", layout_name, source_path, parent_id, name, str(order))
            attrs = {key.rsplit("}", 1)[-1]: resolve_value(value, resources) for key, value in sorted(node.attrib.items())}
            # Extract visual fidelity attributes (color/size/margin/radius/background) for ArkUI mapping
            fidelity_attrs = {k: v for k, v in attrs.items() if any(x in k for x in ["color", "textSize", "textStyle", "alpha", "background", "src", "tint", "radius", "stroke", "padding", "margin", "gravity", "maxLines", "maxLength", "hint", "inputType", "entries"])}
            rows.append({
                "component_id": component_id,
                "layout_name": layout_name,
                "layout_variant": source_path,
                "type": tag,
                "resource_id": name if raw_id else "",
                "parent_component_id": parent_id,
                "child_order": order,
                "text": attrs.get("text", attrs.get("hint", attrs.get("contentDescription", ""))),
                "width": attrs.get("layout_width", ""),
                "height": attrs.get("layout_height", ""),
                "visibility": attrs.get("visibility", "visible"),
                "clickable": attrs.get("clickable", "") or ("true" if "onClick" in attrs else "unknown"),
                "enabled_expression": attrs.get("enabled", "true"),
                "position_rules": {key: value for key, value in attrs.items() if key.startswith("layout_") or "constraint" in key.lower()},
                "event_bindings": {key: value for key, value in attrs.items() if key in {"onClick", "onLongClick"}},
                "attributes": attrs,
                "fidelity_attrs": fidelity_attrs,
                "is_visual": True,
                "source_ref": f"{rel(path, project)}:{line_number(raw_xml, node_offset)}",
                "confidence": "HIGH",
            })
            for child_order, child in enumerate(list(node), start=1):
                visit(child, component_id, child_order)

        visit(root, "", 1)
        layouts.setdefault(layout_name, []).extend(rows)
    return layouts, issues


def manifest_pages(project: Path) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for path in sorted(project.rglob("AndroidManifest.xml")):
        if any(part in IGNORED_DIRS for part in path.relative_to(project).parts):
            continue
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        package = root.attrib.get("package", "")
        for kind in ("activity",):
            for node in root.findall(f".//{kind}"):
                name = node.attrib.get(ANDROID + "name", "")
                if name.startswith("."):
                    name = package + name
                filters = []
                for intent in node.findall("intent-filter"):
                    actions = [item.attrib.get(ANDROID + "name", "") for item in intent.findall("action")]
                    categories = [item.attrib.get(ANDROID + "name", "") for item in intent.findall("category")]
                    data = [{key.rsplit("}", 1)[-1]: value for key, value in item.attrib.items()} for item in intent.findall("data")]
                    filters.append({"actions": actions, "categories": categories, "data": data})
                pages.append({
                    "symbol": name, "kind": kind.upper().replace("-", "_"), "manifest_entry": True,
                    "intent_filters": filters, "source_ref": f"{rel(path, project)}:1",
                })
        for node in root.findall(".//activity-alias"):
            target = node.attrib.get(ANDROID + "targetActivity", "")
            if target.startswith("."):
                target = package + target
            filters = []
            for intent in node.findall("intent-filter"):
                filters.append({
                    "actions": [item.attrib.get(ANDROID + "name", "") for item in intent.findall("action")],
                    "categories": [item.attrib.get(ANDROID + "name", "") for item in intent.findall("category")],
                    "data": [{key.rsplit("}", 1)[-1]: value for key, value in item.attrib.items()} for item in intent.findall("data")],
                    "alias": node.attrib.get(ANDROID + "name", ""),
                })
            if target:
                pages.append({
                    "symbol": target, "kind": "ACTIVITY_ALIAS_ENTRY", "manifest_entry": True,
                    "intent_filters": filters, "source_ref": f"{rel(path, project)}:1",
                })
    return pages


def navigation_resources(project: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str], str]:
    pages: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    action_map: dict[str, str] = {}
    start_destination_symbol = ""
    for path in xml_files(project, "navigation"):
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        destinations: dict[str, str] = {}
        start_dest_id = root.attrib.get("{http://schemas.android.com/apk/res-auto}startDestination", "").rsplit("/", 1)[-1]
        for node in root.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag not in {"activity", "fragment", "dialog"}:
                continue
            symbol = node.attrib.get(ANDROID + "name", "") or node.attrib.get(ANDROID + "id", "").rsplit("/", 1)[-1]
            nav_id = node.attrib.get(ANDROID + "id", "").rsplit("/", 1)[-1]
            if symbol:
                short_symbol = symbol.rsplit(".", 1)[-1]
                pages.append({
                    "symbol": short_symbol, "kind": f"NAV_{tag.upper()}",
                    "manifest_entry": False, "layout_names": [], "source_ref": f"{rel(path, project)}:1",
                })
                if nav_id:
                    destinations[nav_id] = short_symbol
        # Build action_id -> fragment_symbol map and source-aware transitions.
        for fragment_node in root.findall(".//fragment"):
            frag_symbol = fragment_node.attrib.get(ANDROID + "name", "").rsplit(".", 1)[-1]
            frag_nav_id = fragment_node.attrib.get(ANDROID + "id", "").rsplit("/", 1)[-1]
            for action in fragment_node.findall("action"):
                action_id = action.attrib.get(ANDROID + "id", "").rsplit("/", 1)[-1]
                target = action.attrib.get("{http://schemas.android.com/apk/res-auto}destination", "").rsplit("/", 1)[-1]
                target_symbol = destinations.get(target, target)
                if action_id:
                    action_map[action_id] = target_symbol
                if target:
                    transitions.append({
                        "source_symbol": frag_symbol, "target_symbol": target_symbol, "navigation_type": "NAVIGATION_XML",
                        "condition": "PENDING_RUNTIME_CONFIRMATION", "source_ref": f"{rel(path, project)}:1",
                    })
        if start_dest_id and start_dest_id in destinations and not start_destination_symbol:
            start_destination_symbol = destinations[start_dest_id]
    return pages, transitions, action_map, start_destination_symbol


def scan_sources(
    project: Path,
    action_map: dict[str, str] | None = None,
    per_file: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Aggregate per-file page/event/transition/state candidates.

    ``per_file`` (incremental mode) maps relative path -> cached parse result;
    a hit skips re-parsing that file. Aggregation itself is always recomputed.
    """
    if action_map is None:
        action_map = {}
    pages: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    class_re = re.compile(r"\b(?:open\s+)?class\s+(\w+)")
    compose_re = re.compile(r"@Composable(?:\s*\([^)]*\))?\s*(?:public\s+|private\s+|internal\s+)?fun\s+(\w+)\s*\(")
    click_re = re.compile(r"\b((?:[A-Za-z_]\w*\??\.)*[A-Za-z_]\w*)\??\.(set[A-Z]\w*(?:Listener|Operation)|addTextChangedListener)\b")
    compose_event_re = re.compile(r"\b([A-Z]\w*)\s*\([^)]{0,1200}?(onClick|onValueChange|onCheckedChange|doOnPreferenceClick|onChange)\s*=", re.DOTALL)
    navigate_re = re.compile(r"\bnavigate\s*\(\s*(?:R\.id\.)?[\"']?([A-Za-z0-9_/{.$}-]+)")
    state_re = re.compile(r"\b(?:if\s*\(([^)]+)\)|when\s*\(([^)]+)\)|(?:is|==)\s*(Loading|Success|Error|Empty))")
    binding_adapter_re = re.compile(r'@BindingAdapter\s*\(\s*"(?:[a-zA-Z_]+:)?(\w+)"')


    for path in source_files(project):
        path_value = rel(path, project)
        cached = per_file.get(path_value) if per_file is not None else None
        if (
            isinstance(cached, dict)
            and all(key in cached for key in ("pages", "events", "transitions", "states"))
        ):
            pages.extend(cached["pages"])
            events.extend(cached["events"])
            transitions.extend(cached["transitions"])
            states.extend(cached["states"])
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        file_pages: list[dict[str, Any]] = []
        file_events: list[dict[str, Any]] = []
        file_transitions: list[dict[str, Any]] = []
        file_states: list[dict[str, Any]] = []
        layouts_in_file = binding_layouts(text)
        compose_roots = sorted(set(re.findall(r"setContent\s*\{[\s\S]{0,8000}?\b([A-Z]\w*Screen)\s*\(", text)))
        class_matches = list(class_re.finditer(text))
        file_page_symbols = [
            item.group(1) for item in class_matches
            if item.group(1).endswith(("BottomSheetDialogFragment", "DialogFragment", "Fragment", "Activity", "Dialog"))
        ]
        remote_matches = list(re.finditer(r"RemoteViews\s*\([\s\S]{0,240}?R\.layout\.([A-Za-z0-9_]+)", text))
        source_symbol = file_page_symbols[0] if file_page_symbols else (
            f"Widget_{remote_matches[0].group(1)}" if remote_matches else "PENDING_SOURCE_BINDING"
        )

        for match in class_matches:
            symbol = match.group(1)
            suffix = next((candidate for candidate in ("BottomSheetDialogFragment", "DialogFragment", "Fragment", "Activity", "Dialog") if symbol.endswith(candidate)), "")
            if suffix:
                file_pages.append({
                    "symbol": symbol, "kind": suffix.upper(), "manifest_entry": False,
                    "layout_names": layouts_in_file, "compose_root_symbols": compose_roots,
                    "ui_declared": bool(layouts_in_file or compose_roots) or (
                        # Programmatic dialogs build their UI in code (AlertDialog);
                        # a *Dialog-suffixed class with dialog-framework usage is a UI surface.
                        suffix == "Dialog" and ("AlertDialog" in text or "DialogInterface" in text)
                    ),
                    "source_ref": f"{path_value}:{line_number(text, match.start())}",
                })
        for match in compose_re.finditer(text):
            symbol = match.group(1)
            # Naming conventions are hints, not a discovery boundary. Projects often use
            # names such as `SettingsContent`; excluding them silently shrinks Gate 2's
            # denominator and can make an incomplete inventory appear complete.
            if symbol.endswith("Preview"):
                continue
            body = text[match.end():match.end() + 12000]
            next_composable = body.find("@Composable")
            if next_composable >= 0:
                body = body[:next_composable]
            compose_calls = list(re.finditer(r"\b([A-Z]\w*(?:Component|Scaffold|Group|Text|Button|Field|Box|Row|Column|Divider|Image|Icon|Switch|Slider)?)\s*\(", body))
            if compose_calls:
                compose_components = []
                for widget_match in compose_calls:
                    compose_components.append({
                        "type": widget_match.group(1),
                        "source_ref": f"{path_value}:{line_number(text, match.end() + widget_match.start())}",
                    })
                file_pages.append({
                    "symbol": symbol, "kind": "COMPOSABLE", "manifest_entry": False,
                    "layout_names": [], "compose_components": compose_components,
                    "source_ref": f"{path_value}:{line_number(text, match.start())}",
                })
        # Generic @BindingAdapter method-range scan (kept as the discovery
        # mechanism for binding adapters; the sample-specific owner/target
        # symbol maps were removed, so no consumer rewrites symbols from here).
        binding_adapter_ranges: list[tuple[int, int, str]] = []
        for ba_match in binding_adapter_re.finditer(text):
            method_name = ba_match.group(1)
            # Find the fun definition that follows this annotation.
            fun_start = text.find("fun ", ba_match.end())
            if fun_start < 0 or fun_start - ba_match.end() > 200:
                continue
            # Estimate method body range: from fun_start to the next @BindingAdapter or EOF.
            next_ba = text.find("@BindingAdapter", fun_start)
            method_end = next_ba if next_ba > 0 else len(text)
            binding_adapter_ranges.append((fun_start, method_end, method_name))

        def infer_owner(pos: int, default: str) -> str:
            # Generic @BindingAdapter scanning is kept above; the sample-specific
            # owner-symbol mapping was removed (ghost-page risk). Unknown owners
            # keep the file-level source_symbol and go through normal host
            # resolution instead of being guessed onto a hard-coded page.
            return default

        def infer_target(pos: int, raw_target: str) -> str:
            # If the raw target is a resolved action_id, use the action map.
            if raw_target in action_map:
                return action_map[raw_target]
            return raw_target

        for match in click_re.finditer(text):
            file_events.append({
                "component_symbol": match.group(1), "event": match.group(2),
                "handler_excerpt": text[match.end():match.end() + 240].strip().split("\n", 1)[0],
                "source_symbol": infer_owner(match.start(), source_symbol),
                "source_ref": f"{path_value}:{line_number(text, match.start())}",
            })
        for match in remote_matches:
            layout_name = match.group(1)
            file_pages.append({
                "symbol": f"Widget_{layout_name}", "kind": "APP_WIDGET", "manifest_entry": False,
                "layout_names": [layout_name], "ui_declared": True,
                "source_ref": f"{path_value}:{line_number(text, match.start())}",
            })
        for match in compose_event_re.finditer(text):
            file_events.append({
                "component_symbol": match.group(1).replace("?.", "."), "event": match.group(2),
                "handler_excerpt": text[match.end():match.end() + 240].strip().split("\n", 1)[0],
                "source_symbol": source_symbol,
                "source_ref": f"{path_value}:{line_number(text, match.start())}",
            })
        for match in re.finditer(r"\b(onClick|onValueChange|onCheckedChange|doOnPreferenceClick|onChange)\s*=", text):
            prefix = text[max(0, match.start() - 1200):match.start()]
            owners = list(re.finditer(r"\b([A-Z]\w*)\s*\(", prefix))
            if owners:
                file_events.append({
                    "component_symbol": owners[-1].group(1), "event": match.group(1),
                    "handler_excerpt": text[match.end():match.end() + 240].strip().split("\n", 1)[0],
                    "source_symbol": source_symbol,
                    "source_ref": f"{path_value}:{line_number(text, match.start())}",
                })
        for match in navigate_re.finditer(text):
            raw_target = match.group(1)
            # Resolve action_id -> fragment symbol via the navigation graph map or BindingAdapter target.
            resolved_target = infer_target(match.start(), raw_target)
            file_transitions.append({
                "source_symbol": infer_owner(match.start(), source_symbol), "target_symbol": resolved_target, "navigation_type": "NAVIGATE",
                "condition": "PENDING_RUNTIME_CONFIRMATION",
                "source_ref": f"{path_value}:{line_number(text, match.start())}",
            })
        for match in re.finditer(r"\bstart(About|Customization)Activity\s*\(", text):
            file_transitions.append({
                "source_symbol": source_symbol, "target_symbol": match.group(1) + "Activity", "navigation_type": "COMMONS_EXTENSION",
                "condition": "PENDING_RUNTIME_CONFIRMATION",
                "source_ref": f"{path_value}:{line_number(text, match.start())}",
            })
        for match in re.finditer(r"\bIntent\s*\([^,\n]+,\s*([A-Z]\w*Activity)::class\.java", text):
            file_transitions.append({
                "source_symbol": source_symbol, "target_symbol": match.group(1), "navigation_type": "ACTIVITY_INTENT",
                "condition": "PENDING_RUNTIME_CONFIRMATION",
                "source_ref": f"{path_value}:{line_number(text, match.start())}",
            })
        external_calls = {
            "launchMoreAppsFromUsIntent": "EXTERNAL_MORE_APPS",
            "launchChangeAppLanguageIntent": "SYSTEM_APP_LANGUAGE_SETTINGS",
            "launchPurchaseThankYouIntent": "EXTERNAL_PURCHASE",
        }
        for call, target in external_calls.items():
            matches = list(re.finditer(rf"\b{call}\s*\(", text)) + list(re.finditer(rf"::{call}\b", text))
            for match in matches:
                if source_symbol == "PENDING_SOURCE_BINDING":
                    continue
                file_transitions.append({
                    "source_symbol": source_symbol, "target_symbol": target, "navigation_type": "EXTERNAL_SURFACE",
                    "condition": "PENDING_RUNTIME_CONFIRMATION",
                    "source_ref": f"{path_value}:{line_number(text, match.start())}",
                })
        for match in state_re.finditer(text):
            expression = next((value for value in match.groups() if value), "UNKNOWN")
            file_states.append({
                "expression": re.sub(r"\s+", " ", expression).strip()[:240],
                "source_symbol": infer_owner(match.start(), source_symbol),
                "source_ref": f"{path_value}:{line_number(text, match.start())}",
            })
        pages.extend(file_pages)
        events.extend(file_events)
        transitions.extend(file_transitions)
        states.extend(file_states)
        if per_file is not None:
            per_file.setdefault(path_value, {}).update({
                "pages": file_pages, "events": file_events,
                "transitions": file_transitions, "states": file_states,
                "class_layouts": parse_class_layout_bindings(text),
                "code_candidates": parse_code_candidates(path_value, text),
            })
    return pages, events, transitions, states


DYNAMIC_PATTERNS = {
    "REFLECTION": re.compile(r"\b(?:Class\.forName|getDeclared(?:Method|Field|Constructor)|\.invoke\s*\()"),
    "DYNAMIC_CODE": re.compile(r"\b(?:DexClassLoader|PathClassLoader|InMemoryDexClassLoader|loadDex)\b"),
    "WEBVIEW": re.compile(r"\b(?:WebView|loadUrl|loadDataWithBaseURL|evaluateJavascript|addJavascriptInterface)\b"),
    "DYNAMIC_NAVIGATION": re.compile(r"\b(?:setClassName|setComponent|Intent\s*\(\s*[A-Za-z_]\w*\s*\))"),
    "FEATURE_FLAG": re.compile(r"\b(?:FirebaseRemoteConfig|RemoteConfig|getBoolean\s*\(|featureFlag|feature_flag)\b", re.I),
    "REMOTE_UI": re.compile(r"\b(?:uiSchema|ui_schema|serverDriven|server_driven|dynamicLayout|dynamic_layout)\b", re.I),
}

SIDE_EFFECT_PATTERNS = {
    "DATABASE": re.compile(r"\b(?:RoomDatabase|SQLiteDatabase|SQLiteOpenHelper|@Insert|@Update|@Delete|\.insert\s*\(|\.update\s*\(|\.delete\s*\()"),
    "PREFERENCES": re.compile(r"\b(?:SharedPreferences|DataStore|PreferenceManager|\.edit\s*\(|\.put(?:String|Int|Long|Boolean|Float)\s*\()"),
    "FILE": re.compile(r"\b(?:FileOutputStream|openFileOutput|writeText\s*\(|writeBytes\s*\(|contentResolver\.openOutputStream)"),
    "CLIPBOARD": re.compile(r"\b(?:ClipboardManager|setPrimaryClip|getPrimaryClip)\b"),
    "NETWORK": re.compile(r"\b(?:OkHttpClient|Retrofit|HttpURLConnection|URLConnection|WebSocket|Socket\s*\(|@GET\s*\(|@POST\s*\(|\.enqueue\s*\()"),
    "BACKGROUND": re.compile(r"\b(?:WorkManager|JobScheduler|AlarmManager|startService|startForegroundService|enqueueUniqueWork)\b"),
    "NOTIFICATION": re.compile(r"\b(?:NotificationManager|NotificationManagerCompat|NotificationCompat\.Builder|\.notify\s*\()"),
    "PERMISSION": re.compile(r"\b(?:requestPermissions|RequestPermission|RequestMultiplePermissions|checkSelfPermission)\b"),
}


def scan_advanced_candidates(
    project: Path,
    page_ids_by_symbol: dict[str, str],
    features: list[str],
) -> dict[str, list[dict[str, Any]]]:
    dynamic: list[dict[str, Any]] = []
    side_effects: list[dict[str, Any]] = []
    scenarios: list[dict[str, Any]] = []

    def source_context(text: str) -> tuple[str, str]:
        symbols = re.findall(r"\b(?:class|object)\s+(\w+)", text)
        symbols.extend(re.findall(r"@Composable[\s\S]{0,160}?\bfun\s+(\w+)\s*\(", text))
        symbol = next((value for value in symbols if value in page_ids_by_symbol), symbols[0] if symbols else "PENDING_SOURCE_BINDING")
        return symbol, page_ids_by_symbol.get(symbol, "PENDING_SOURCE_BINDING")

    for path in source_files(project):
        text = path.read_text(encoding="utf-8", errors="replace")
        path_value = rel(path, project)
        symbol, page_id = source_context(text)
        for risk_type, pattern in DYNAMIC_PATTERNS.items():
            for match in pattern.finditer(text):
                source_ref = f"{path_value}:{line_number(text, match.start())}"
                risk_id = stable_id("DRISK", risk_type, source_ref)
                dynamic.append({
                    "risk_id": risk_id, "risk_type": risk_type, "page_id": page_id,
                    "source_symbol": symbol, "source_ref": source_ref,
                    "expression": text[match.start():match.start() + 180].split("\n", 1)[0].strip(),
                    "candidate_feature_ids": feature_candidates(path_value, symbol, features) or sorted(features),
                    "required_runtime_resolution": "DISCOVER_SURFACE_AND_BIND_EVIDENCE",
                    "status": "OPEN",
                })
        for effect_type, pattern in SIDE_EFFECT_PATTERNS.items():
            for match in pattern.finditer(text):
                source_ref = f"{path_value}:{line_number(text, match.start())}"
                candidate_id = stable_id("EFFECT", effect_type, source_ref)
                side_effects.append({
                    "candidate_id": candidate_id, "effect_type": effect_type, "page_id": page_id,
                    "source_symbol": symbol, "source_ref": source_ref,
                    "operation": text[match.start():match.start() + 180].split("\n", 1)[0].strip(),
                    "candidate_feature_ids": feature_candidates(path_value, symbol, features) or sorted(features),
                    "required_probe": effect_type, "status": "OPEN",
                })

    permissions: set[tuple[str, str]] = set()
    for manifest in sorted(project.rglob("AndroidManifest.xml")):
        if any(part in IGNORED_DIRS for part in manifest.relative_to(project).parts):
            continue
        try:
            root = ET.parse(manifest).getroot()
        except ET.ParseError:
            continue
        for node in root.findall("uses-permission"):
            name = node.attrib.get(ANDROID + "name", "")
            if name:
                permissions.add((name, f"{rel(manifest, project)}:1"))

    scenario_seeds: list[tuple[str, str, str, str, list[str]]] = []
    for item in dynamic:
        variants = {
            "WEBVIEW": ("REMOTE_SUCCESS", "REMOTE_EMPTY", "REMOTE_ERROR"),
            "REMOTE_UI": ("REMOTE_SUCCESS", "REMOTE_EMPTY", "REMOTE_ERROR"),
            "FEATURE_FLAG": ("FLAG_ENABLED", "FLAG_DISABLED"),
            "REFLECTION": ("DYNAMIC_TARGET_AVAILABLE", "DYNAMIC_TARGET_MISSING"),
            "DYNAMIC_CODE": ("MODULE_AVAILABLE", "MODULE_UNAVAILABLE"),
            "DYNAMIC_NAVIGATION": ("ROUTE_RESOLVES", "ROUTE_REJECTED"),
        }.get(item["risk_type"], ("DEFAULT",))
        for variant in variants:
            scenario_seeds.append((variant, item["page_id"], item["source_ref"], item["risk_id"], item["candidate_feature_ids"]))
    for item in side_effects:
        variants = {
            "NETWORK": ("NETWORK_NORMAL", "NETWORK_OFFLINE", "NETWORK_TIMEOUT", "SERVER_ERROR"),
            "DATABASE": ("DATA_EMPTY", "DATA_POPULATED", "DATA_BOUNDARY"),
            "PREFERENCES": ("FIRST_RUN", "RETURNING_USER"),
            "FILE": ("STORAGE_AVAILABLE", "STORAGE_FAILURE"),
            "CLIPBOARD": ("CLIPBOARD_EMPTY", "CLIPBOARD_POPULATED"),
            "BACKGROUND": ("BACKGROUND_COMPLETES", "PROCESS_RESTART"),
            "NOTIFICATION": ("NOTIFICATIONS_ALLOWED", "NOTIFICATIONS_DENIED"),
            "PERMISSION": ("PERMISSION_GRANTED", "PERMISSION_DENIED", "PERMISSION_PERMANENTLY_DENIED"),
        }.get(item["effect_type"], ("DEFAULT",))
        for variant in variants:
            scenario_seeds.append((variant, item["page_id"], item["source_ref"], item["candidate_id"], item["candidate_feature_ids"]))
    for permission, source_ref in sorted(permissions):
        for variant in ("PERMISSION_GRANTED", "PERMISSION_DENIED", "PERMISSION_PERMANENTLY_DENIED"):
            scenario_seeds.append((variant, "PENDING_SOURCE_BINDING", source_ref, permission, sorted(features)))

    seen_scenarios: set[tuple[str, str, str]] = set()
    for variant, page_id, source_ref, prerequisite, candidate_features in scenario_seeds:
        key = (variant, source_ref, prerequisite)
        if key in seen_scenarios:
            continue
        seen_scenarios.add(key)
        scenarios.append({
            "scenario_id": stable_id("SCENARIO", variant, source_ref, prerequisite),
            "scenario_type": variant, "page_id": page_id, "source_ref": source_ref,
            "prerequisite_id": prerequisite, "candidate_feature_ids": candidate_features,
            "reset_required": True, "status": "OPEN",
        })

    unique_dynamic = {(row["risk_type"], row["source_ref"]): row for row in dynamic}
    unique_effects = {(row["effect_type"], row["source_ref"]): row for row in side_effects}
    return {
        "dynamic_risks": sorted(unique_dynamic.values(), key=lambda row: row["risk_id"]),
        "side_effects": sorted(unique_effects.values(), key=lambda row: row["candidate_id"]),
        "scenarios": sorted(scenarios, key=lambda row: row["scenario_id"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--analyzed-by", required=True)
    parser.add_argument(
        "--incremental", action="store_true",
        help="reuse cached per-file parse results for sources whose sha256 is "
             "unchanged (scan-cache.json); aggregation is always recomputed; "
             "a missing/corrupt/stale cache falls back to a full parse",
    )
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    phase = load_json(workspace / "phase-manifest.json")
    if phase.get("phase") != 2 or phase.get("status") != "IN_PROGRESS":
        parser.error("Static analysis requires an open Phase 2 workspace")
    owner = phase.get("ownership", {}).get("code_map_agent_id")
    if args.analyzed_by != owner:
        parser.error("--analyzed-by must equal the frozen code-map agent")
    project = Path(str(phase.get("android_project_root", ""))).resolve()
    if not project.is_dir():
        parser.error("Frozen Android project root is unavailable")
    output = workspace / "static-analysis"
    if (output / "COMMITTED").exists():
        parser.error("Static analysis is already committed; create a new Phase 2 run to recapture")
    output.mkdir(parents=True, exist_ok=True)

    source_scan = source_scan_ledger(project)
    resources = read_resources(project)
    layouts, issues = scan_layouts(project, resources)
    for skipped in source_scan["skipped"]:
        issues.append({
            "kind": "SOURCE_SCAN_SKIPPED",
            "source_ref": f"{skipped['path']}:1",
            "detail": f"{skipped['reason']}: {skipped['size_bytes']} bytes",
        })
    pages = manifest_pages(project)
    nav_pages, nav_transitions, action_map, start_destination_symbol = navigation_resources(project)
    # Incremental rescan (B6): per-file parse cache keyed by relative path and
    # sha256. The cache context pins the analyzer revision and the navigation
    # action map so any change to either invalidates the whole cache. The
    # COMMITTED marker above still forbids re-running a committed package.
    cache_path = output / "scan-cache.json"
    cache_context = {
        "analyzer_sha256": sha256_file(Path(__file__).resolve()),
        "navigation_fingerprint": hashlib.sha256(
            json.dumps(action_map, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
    }
    file_cache: dict[str, dict[str, Any]] = {}
    if args.incremental and cache_path.is_file():
        try:
            loaded_cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded_cache = None
        if (
            isinstance(loaded_cache, dict)
            and loaded_cache.get("schema_version") == 1
            and loaded_cache.get("cache_context") == cache_context
            and isinstance(loaded_cache.get("files"), dict)
        ):
            file_cache = {
                str(key): value for key, value in loaded_cache["files"].items()
                if isinstance(value, dict)
            }
        # Any mismatch above silently degrades to a full parse (file_cache={}).
    per_file: dict[str, dict[str, Any]] = {}
    for path in source_files(project):
        pv = rel(path, project)
        if args.incremental:
            digest = sha256_file(path)
            cached_entry = file_cache.get(pv)
            if (
                isinstance(cached_entry, dict)
                and cached_entry.get("sha256") == digest
                and all(key in cached_entry for key in ("pages", "events", "transitions", "states"))
            ):
                per_file[pv] = cached_entry
                continue
            per_file[pv] = {"sha256": digest}
        else:
            per_file[pv] = {}
    source_pages, events, transitions, states = scan_sources(project, action_map, per_file)
    if args.incremental:
        atomic_json(cache_path, {
            "schema_version": 1, "cache_context": cache_context, "files": per_file,
        })
    pages.extend(nav_pages)
    transitions.extend(nav_transitions)
    events = list({(item["component_symbol"], item["event"], item["source_ref"]): item for item in events}.values())
    transitions = list({(item.get("source_symbol"), item["target_symbol"], item["source_ref"]): item for item in transitions}.values())
    class_layouts = class_layout_bindings(project, per_file)
    by_symbol: dict[str, dict[str, Any]] = {}
    for item in pages + source_pages:
        key = item["symbol"].rsplit(".", 1)[-1]
        current = by_symbol.setdefault(key, {
            "symbol": key, "kinds": [], "source_refs": [], "layout_names": [], "intent_filters": [],
            "compose_components": [], "compose_root_symbols": [], "manifest_entry": False, "ui_declared": False,
        })
        current["kinds"].append(item["kind"])
        current["source_refs"].append(item["source_ref"])
        current["layout_names"].extend(item.get("layout_names", []))
        current["intent_filters"].extend(item.get("intent_filters", []))
        current["compose_components"].extend(item.get("compose_components", []))
        current["compose_root_symbols"].extend(item.get("compose_root_symbols", []))
        current["manifest_entry"] = current["manifest_entry"] or item.get("manifest_entry", False)
        current["ui_declared"] = current["ui_declared"] or item.get("ui_declared", False) or bool(item.get("compose_components"))

    features = list(phase.get("included_features", []))
    page_rows: list[dict[str, Any]] = []
    component_rows: list[dict[str, Any]] = []
    code_rows: list[dict[str, Any]] = []
    runtime_tasks: list[dict[str, Any]] = []
    page_ids_by_symbol: dict[str, str] = {}
    hosted_compose_roots = {
        root_symbol for item in by_symbol.values() for root_symbol in item.get("compose_root_symbols", [])
    }
    for symbol, item in sorted(by_symbol.items()):
        if symbol in hosted_compose_roots and set(item["kinds"]) == {"COMPOSABLE"}:
            continue
        if not item["manifest_entry"] and not item["ui_declared"]:
            continue
        source_refs = sorted(set(item["source_refs"]))
        source_ref = next((value for value in source_refs if "AndroidManifest.xml" not in value), source_refs[0])
        page_id = stable_id("PAGE", symbol)
        candidate_features = feature_candidates(source_ref, symbol, features)
        if not candidate_features:
            candidate_features = sorted(features)
        confidence = "HIGH" if len(candidate_features) == 1 else "LOW"
        compose_components = list(item["compose_components"])
        for root_symbol in sorted(set(item["compose_root_symbols"])):
            compose_components.extend(by_symbol.get(root_symbol, {}).get("compose_components", []))
        page = {
            "page_id": page_id, "symbol": symbol, "kinds": sorted(set(item["kinds"])),
            "candidate_feature_ids": candidate_features, "layout_names": sorted(set(item["layout_names"])),
            "compose_root_symbols": sorted(set(item["compose_root_symbols"])),
            "intent_filters": item["intent_filters"], "source_refs": source_refs,
            "surface_disposition": "UI_BOUND" if page_id and (item["layout_names"] or compose_components) else "RUNTIME_BINDING_REQUIRED",
            "confidence": confidence,
        }
        collection_item_layouts: list[str] = []
        for value in source_refs:
            source_path = project / value.rsplit(":", 1)[0]
            if not source_path.is_file() or source_path.suffix not in SOURCE_SUFFIXES:
                continue
            source_text = source_path.read_text(encoding="utf-8", errors="replace")
            for class_name, bound_layouts in class_layouts.items():
                if class_name.endswith("Adapter") and re.search(rf"\b{re.escape(class_name)}\b", source_text):
                    collection_item_layouts.extend(bound_layouts)
        page["collection_item_layouts"] = sorted(set(collection_item_layouts))
        page_rows.append(page)
        page_ids_by_symbol[symbol] = page_id
        def attach_layout(layout_name: str, parent_id: str = "", context: str = "root", stack: tuple[str, ...] = ()) -> None:
            if layout_name in stack:
                return
            id_map = {
                row["component_id"]: stable_id("COMP", page_id, context, row["component_id"])
                for row in layouts.get(layout_name, [])
            }
            for component in layouts.get(layout_name, []):
                copied = dict(component)
                copied["component_id"] = id_map[component["component_id"]]
                copied["parent_component_id"] = id_map.get(component["parent_component_id"], parent_id)
                copied["page_id"] = page_id
                component_rows.append(copied)
                include_value = component.get("attributes", {}).get("layout", "")
                if component.get("type") == "include" and include_value.startswith("@layout/"):
                    included = include_value.rsplit("/", 1)[-1]
                    attach_layout(included, copied["component_id"], copied["component_id"], (*stack, layout_name))
                list_item_value = component.get("attributes", {}).get("listitem", "")
                if list_item_value.startswith("@layout/"):
                    item_layout = list_item_value.rsplit("/", 1)[-1]
                    attach_layout(item_layout, copied["component_id"], copied["component_id"], (*stack, layout_name))
                custom_type = component.get("type", "").rsplit(".", 1)[-1]
                for bound_layout in class_layouts.get(custom_type, []):
                    attach_layout(bound_layout, copied["component_id"], copied["component_id"], (*stack, layout_name))

        for layout_name in page["layout_names"]:
            attach_layout(layout_name)
        for layout_name in page["collection_item_layouts"]:
            attach_layout(layout_name, context="collection-item")
        for order, component in enumerate(compose_components, start=1):
            component_rows.append({
                "page_id": page_id,
                "component_id": stable_id("COMP", page_id, component["source_ref"], str(order)),
                "layout_name": "COMPOSE", "type": component["type"], "resource_id": "",
                "parent_component_id": "", "child_order": order, "text": "",
                "width": "RUNTIME_RESOLVED", "height": "RUNTIME_RESOLVED", "visibility": "RUNTIME_RESOLVED",
                "clickable": "unknown", "enabled_expression": "RUNTIME_RESOLVED", "position_rules": {},
                "event_bindings": {}, "attributes": {}, "source_ref": component["source_ref"], "confidence": "MEDIUM",
            })
        _vm, _rt = classify_task("VERIFY_PAGE_DEFAULT_STATE")
        runtime_tasks.append({
            "task_id": stable_id("RTASK", page_id, "DEFAULT"), "task_type": "VERIFY_PAGE_DEFAULT_STATE",
            "subject_id": page_id, "page_id": page_id, "candidate_feature_ids": candidate_features,
            "trigger": "AUTO_LAUNCH_OR_ROUTE", "expected": "Confirm page identity, full UI tree and final geometry",
            "status": "OPEN", "source_refs": page["source_refs"],
            "verification_mode": _vm, "review_tier": _rt,
        })
        path_value, line = source_ref.rsplit(":", 1)
        if len(candidate_features) == 1:
            code_rows.append({
                "code_ref": source_ref, "feature_id": candidate_features[0], "page_id": page_id,
                "state_candidate_id": stable_id("STATE", page_id, "DEFAULT"), "component_type": "page",
                "symbol": symbol, "file_path": path_value, "line": line,
                "coverage_disposition": "IN_SCOPE", "owner": owner, "status": "DISCOVERED",
                "notes": "Static candidate; runtime correlation required before VERIFIED",
            })

    # Deterministic host resolution for subjects defined in files without a
    # discoverable page class (shared helpers, base classes, adapters, holders,
    # programmatic dialog factories, manifest XML). Rule: bind to the single page
    # whose source file references the subject's primary class symbol. When zero
    # or multiple pages reference it, the subject is NOT guessed onto the
    # launcher page any more: it is explicitly blocked as PENDING_HOST and
    # listed in advanced-analysis.json needs_human_resolution for a human to
    # assign the host (validate_static_analysis downgrades those to WARNING).
    launcher_symbol = ""
    for page in page_rows:
        for flt in page.get("intent_filters", []):
            if (
                "android.intent.action.MAIN" in flt.get("actions", [])
                and "android.intent.category.LAUNCHER" in flt.get("categories", [])
            ):
                launcher_symbol = page["symbol"]
                break
        if launcher_symbol:
            break
    if not launcher_symbol and page_rows:
        launcher_symbol = sorted(row["symbol"] for row in page_rows)[0]
    host_file_texts: dict[str, str] = {}
    host_file_pages: dict[str, set[str]] = {}
    host_class_re = re.compile(r"\b(?:open\s+)?class\s+(\w+)")
    for path in source_files(project):
        pv = rel(path, project)
        text = path.read_text(encoding="utf-8", errors="replace")
        host_file_texts[pv] = text
        defined = {m.group(1) for m in host_class_re.finditer(text)} & set(page_ids_by_symbol)
        if defined:
            host_file_pages[pv] = defined
    # path -> {"page_id": str | None, "reason": str}
    host_resolution_cache: dict[str, dict[str, Any]] = {}

    def resolve_host_page_id(source_ref: str) -> str | None:
        path_value = source_ref.rsplit(":", 1)[0]
        if path_value in host_resolution_cache:
            return host_resolution_cache[path_value]["page_id"]
        resolved: str | None = None
        reason = "ZERO_HOST_REFERENCES"
        if not path_value.endswith(".xml"):
            text = host_file_texts.get(path_value, "")
            symbols = [m.group(1) for m in host_class_re.finditer(text)] if text else []
            stem = Path(path_value).stem
            main_symbol = stem if stem in symbols else (symbols[0] if symbols else "")
            hosts: set[str] = set()
            if main_symbol:
                pattern = re.compile(rf"\b{re.escape(main_symbol)}\b")
                for other_pv, other_text in host_file_texts.items():
                    if other_pv == path_value:
                        continue
                    if pattern.search(other_text):
                        hosts |= host_file_pages.get(other_pv, set())
            if len(hosts) == 1:
                resolved = page_ids_by_symbol.get(next(iter(hosts)))
            elif len(hosts) > 1:
                reason = "AMBIGUOUS_HOST_REFERENCES"
        host_resolution_cache[path_value] = {"page_id": resolved, "reason": reason}
        return resolved

    def host_page_id_or_block(source_ref: str) -> str:
        """Page-ID, or the explicit PENDING_HOST block marker."""
        resolved = resolve_host_page_id(source_ref)
        return "PENDING_HOST" if resolved is None else resolved

    resolved_events: list[dict[str, Any]] = []
    for item in events:
        page_id = page_ids_by_symbol.get(item.get("source_symbol", ""), "PENDING_SOURCE_BINDING")
        if page_id == "PENDING_SOURCE_BINDING":
            page_id = host_page_id_or_block(item["source_ref"])
        event_id = stable_id(
            "EVENT", page_id, item.get("component_symbol", ""), item.get("event", ""), item["source_ref"]
        )
        resolved = {**item, "event_id": event_id, "page_id": page_id}
        resolved_events.append(resolved)
        _vm, _rt = classify_task("VERIFY_EVENT")
        runtime_tasks.append({
            "task_id": stable_id("RTASK", event_id), "task_type": "VERIFY_EVENT",
            "subject_id": event_id, "page_id": page_id,
            "candidate_feature_ids": feature_candidates(
                item["source_ref"], item.get("source_symbol", ""), features
            ),
            "trigger": item.get("event", "UNKNOWN_EVENT"),
            "expected": f"Execute {item.get('component_symbol', 'component')} and capture the observable result",
            "status": "OPEN", "source_refs": [item["source_ref"]],
            "verification_mode": _vm, "review_tier": _rt,
        })
    events = resolved_events

    resolved_states: list[dict[str, Any]] = []
    seen_state_ids: set[str] = set()
    for item in states:
        page_id = page_ids_by_symbol.get(item.get("source_symbol", ""), "PENDING_SOURCE_BINDING")
        if page_id == "PENDING_SOURCE_BINDING":
            page_id = host_page_id_or_block(item["source_ref"])
        state_id = stable_id(
            "STATE", page_id, item.get("expression", ""), item["source_ref"]
        )
        if state_id in seen_state_ids:
            continue
        seen_state_ids.add(state_id)
        resolved = {**item, "state_id": state_id, "page_id": page_id}
        resolved_states.append(resolved)
        # B3: a compile-time-constant branch is statically proven; it carries
        # SOURCE_ONLY so gmi_runtime keeps it out of the emulator queue and
        # gmi_closure excludes it from the coverage denominator (RECORDED).
        _vm, _rt = classify_task("VERIFY_STATE_BRANCH", item.get("expression", ""))
        runtime_tasks.append({
            "task_id": stable_id("RTASK", state_id), "task_type": "VERIFY_STATE_BRANCH",
            "subject_id": state_id, "page_id": page_id,
            "candidate_feature_ids": feature_candidates(
                item["source_ref"], item.get("source_symbol", ""), features
            ),
            "trigger": "AUTO_SATISFY_CONDITION", "expected": item.get("expression", "UNKNOWN"),
            "status": "OPEN", "source_refs": [item["source_ref"]],
            "verification_mode": _vm, "review_tier": _rt,
        })
    states = resolved_states

    resolved_transitions: list[dict[str, Any]] = []
    for item in transitions:
        source_page_id = page_ids_by_symbol.get(item.get("source_symbol", ""), "PENDING_SOURCE_BINDING")
        if source_page_id == "PENDING_SOURCE_BINDING":
            source_page_id = host_page_id_or_block(item["source_ref"])
        target_page_id = page_ids_by_symbol.get(item.get("target_symbol", "")) or stable_id(
            "PAGE", item.get("target_symbol", "PENDING_TARGET_BINDING")
        )
        transition_id = stable_id(
            "TRANSITION", source_page_id, target_page_id, item["source_ref"]
        )
        resolved = {
            **item, "transition_id": transition_id, "source_page_id": source_page_id,
            "target_page_id": target_page_id,
        }
        resolved_transitions.append(resolved)
        _vm, _rt = classify_task("VERIFY_TRANSITION")
        runtime_tasks.append({
            "task_id": stable_id("RTASK", transition_id),
            "task_type": "VERIFY_TRANSITION",
            "subject_id": transition_id, "page_id": source_page_id,
            "candidate_feature_ids": feature_candidates(item["source_ref"], item["target_symbol"], features),
            "trigger": item["navigation_type"], "expected": f"Resolve and open target {item['target_symbol']}",
            "status": "OPEN", "source_refs": [item["source_ref"]],
            "verification_mode": _vm, "review_tier": _rt,
        })
    transitions = resolved_transitions
    advanced_page_ids = dict(page_ids_by_symbol)
    for page in page_rows:
        for root_symbol in page.get("compose_root_symbols", []):
            advanced_page_ids.setdefault(root_symbol, page["page_id"])
    component_owner_sets: dict[str, set[str]] = {}
    for component in component_rows:
        component_type = str(component.get("type", "")).rsplit(".", 1)[-1]
        if component_type:
            component_owner_sets.setdefault(component_type, set()).add(str(component.get("page_id", "")))
    for component_type, owners in component_owner_sets.items():
        if len(owners) == 1:
            advanced_page_ids.setdefault(component_type, next(iter(owners)))
    advanced = scan_advanced_candidates(project, advanced_page_ids, features)
    # Rebind advanced subjects that live in files without a discoverable page.
    for collection in ("dynamic_risks", "side_effects", "scenarios"):
        for item in advanced.get(collection, []):
            if item.get("page_id") == "PENDING_SOURCE_BINDING":
                item["page_id"] = host_page_id_or_block(item.get("source_ref", ""))
    _vm, _rt = classify_task("VERIFY_DYNAMIC_SURFACE")
    for item in advanced["dynamic_risks"]:
        runtime_tasks.append({
            "task_id": stable_id("RTASK", item["risk_id"]), "task_type": "VERIFY_DYNAMIC_SURFACE",
            "subject_id": item["risk_id"], "page_id": item["page_id"],
            "candidate_feature_ids": item["candidate_feature_ids"], "trigger": item["risk_type"],
            "expected": item["required_runtime_resolution"], "status": "OPEN",
            "source_refs": [item["source_ref"]],
            "verification_mode": _vm, "review_tier": _rt,
        })
    _vm, _rt = classify_task("VERIFY_SIDE_EFFECT")
    for item in advanced["side_effects"]:
        runtime_tasks.append({
            "task_id": stable_id("RTASK", item["candidate_id"]), "task_type": "VERIFY_SIDE_EFFECT",
            "subject_id": item["candidate_id"], "page_id": item["page_id"],
            "candidate_feature_ids": item["candidate_feature_ids"], "trigger": item["effect_type"],
            "expected": f"Capture a sealed {item['required_probe']} before/after probe",
            "status": "OPEN", "source_refs": [item["source_ref"]],
            "verification_mode": _vm, "review_tier": _rt,
        })
    _vm, _rt = classify_task("VERIFY_SCENARIO")
    for item in advanced["scenarios"]:
        runtime_tasks.append({
            "task_id": stable_id("RTASK", item["scenario_id"]), "task_type": "VERIFY_SCENARIO",
            "subject_id": item["scenario_id"], "page_id": item["page_id"],
            "candidate_feature_ids": item["candidate_feature_ids"], "trigger": item["scenario_type"],
            "expected": "Reset the environment, satisfy the scenario, and bind runtime evidence",
            "status": "OPEN", "source_refs": [item["source_ref"]],
            "verification_mode": _vm, "review_tier": _rt,
        })
    # Collect every subject blocked on PENDING_HOST for human assignment (C2).
    def _host_reason(source_ref: str) -> str:
        return host_resolution_cache.get(
            source_ref.rsplit(":", 1)[0], {}
        ).get("reason", "ZERO_HOST_REFERENCES")

    needs_human_resolution: list[dict[str, Any]] = []
    for item in events:
        if item.get("page_id") == "PENDING_HOST":
            needs_human_resolution.append({
                "subject_kind": "EVENT", "subject_id": item["event_id"],
                "source_ref": item["source_ref"], "reason": _host_reason(item["source_ref"]),
                "status": "OPEN",
            })
    for item in states:
        if item.get("page_id") == "PENDING_HOST":
            needs_human_resolution.append({
                "subject_kind": "STATE", "subject_id": item["state_id"],
                "source_ref": item["source_ref"], "reason": _host_reason(item["source_ref"]),
                "status": "OPEN",
            })
    for item in transitions:
        if item.get("source_page_id") == "PENDING_HOST":
            needs_human_resolution.append({
                "subject_kind": "TRANSITION", "subject_id": item["transition_id"],
                "source_ref": item["source_ref"], "reason": _host_reason(item["source_ref"]),
                "status": "OPEN",
            })
    for kind, id_key in (
        ("DYNAMIC_RISK", "risk_id"), ("SIDE_EFFECT", "candidate_id"), ("SCENARIO", "scenario_id"),
    ):
        for item in advanced.get(
            {"DYNAMIC_RISK": "dynamic_risks", "SIDE_EFFECT": "side_effects", "SCENARIO": "scenarios"}[kind], []
        ):
            if item.get("page_id") == "PENDING_HOST":
                needs_human_resolution.append({
                    "subject_kind": kind, "subject_id": item[id_key],
                    "source_ref": item["source_ref"], "reason": _host_reason(item["source_ref"]),
                    "status": "OPEN",
                })
    for issue in issues:
        subject_id = stable_id("DISCOVERY", issue["kind"], issue["source_ref"])
        _vm, _rt = classify_task(issue["kind"])
        runtime_tasks.append({
            "task_id": stable_id("RTASK", issue["source_ref"], issue["kind"]), "task_type": issue["kind"],
            "subject_id": subject_id,
            "page_id": "PENDING_SOURCE_BINDING", "candidate_feature_ids": [], "trigger": "AUTO_RESCAN",
            "expected": issue["detail"], "status": "OPEN", "source_refs": [issue["source_ref"]],
            "blocking_discovery_gap": True,
            "verification_mode": _vm, "review_tier": _rt,
        })

    # Build full project-index with asset/resource trace for candidate generation
    all_assets: list[dict[str, Any]] = []
    for path in sorted(project.rglob("*")):
        if path.is_file() and any(x in path.as_posix() for x in ["res/drawable", "res/values", "res/layout", "res/menu", "res/navigation", "res/anim", "res/mipmap"]):
            if any(part in IGNORED_DIRS for part in path.relative_to(project).parts):
                continue
            all_assets.append({"source_path": rel(path, project), "sha256": sha256_file(path)})
    fidelity_index = {
        "colors": {k: v for k, v in resources.items() if "color" in k.lower()},
        "strings": {k: v for k, v in resources.items() if k.startswith("@string/")},
        "layouts": list(layouts.keys()),
        "component_fidelity": [
            {"component_id": c["component_id"], "layout": c["layout_name"], "type": c["type"], "resource_id": c["resource_id"], "fidelity_attrs": c.get("fidelity_attrs", {}), "source_ref": c["source_ref"]}
            for c in component_rows
        ],
    }
    artifacts = {
        "project-index.json": {
            "schema_version": 1, "project_root": str(project), "source_revision": phase.get("source_revision"),
            "source_file_count": source_scan["parsed_count"], "source_scan": source_scan,
            "layout_count": len(layouts),
            "resource_value_count": len(resources), "generated_at": utc_now(), "generated_by": owner,
            "asset_count": len(all_assets),
            "assets": all_assets,
            "fidelity_index": fidelity_index,
            "launcher_page_symbol": launcher_symbol,
            "pending_host_resolution": {p: entry for p, entry in sorted(host_resolution_cache.items())},
        },
        "pages.json": {"schema_version": 1, "pages": page_rows},
        "components.json": {"schema_version": 1, "components": component_rows},
        "events.json": {"schema_version": 1, "events": events},
        "transitions.json": {"schema_version": 1, "transitions": transitions},
        "state-candidates.json": {"schema_version": 1, "states": states},
        "runtime-tasks.json": {"schema_version": 1, "tasks": runtime_tasks},
        "advanced-analysis.json": {
            "schema_version": 1, **advanced,
            "needs_human_resolution": needs_human_resolution,
        },
    }
    for name, value in artifacts.items():
        atomic_json(output / name, value)
    # Full code candidates: every kt/java file line with class/fun/val/var + binding adapters
    # (aggregated from the per-file parse pass above; identical for cache hits)
    full_code_candidates: list[dict[str, Any]] = []
    for path in source_files(project):
        entry = per_file.get(rel(path, project))
        if entry is not None:
            full_code_candidates.extend(entry.get("code_candidates", []))
    atomic_json(output / "full-code-candidates.json", {"schema_version": 1, "candidates": full_code_candidates, "count": len(full_code_candidates)})
    fields = [
        "code_ref", "feature_id", "page_id", "state_candidate_id", "component_type", "symbol",
        "file_path", "line", "coverage_disposition", "owner", "status", "notes",
    ]
    write_csv(output / "code-map.candidates.csv", fields, code_rows)
    # full-code-candidates.json is written above and must be covered by the tamper-evident manifest
    names = sorted([*artifacts, "code-map.candidates.csv", "full-code-candidates.json"])
    atomic_text(output / "manifest.sha256", manifest_lines(output, names))
    atomic_text(output / "COMMITTED", sha256_file(output / "manifest.sha256") + "\n")
    print(json.dumps({
        "workspace": str(workspace), "pages": len(page_rows), "components": len(component_rows),
        "events": len(events), "transitions": len(transitions), "state_candidates": len(states),
        "runtime_tasks": len(runtime_tasks), "committed": str(output / "COMMITTED"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
