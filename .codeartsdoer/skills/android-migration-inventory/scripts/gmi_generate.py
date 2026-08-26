# -*- coding: utf-8 -*-
"""gmi_generate -- generic candidate generation + coverage ledger + UNMAPPED gate.

Produces the 4 candidate CSVs (same schema as the TodoKotlin run), a per-file
coverage ledger, and enforces the "complete coverage" guarantee: every in-scope
scanned file must map to at least one candidate, otherwise it is flagged
UNMAPPED and the run fails (unless --allow-unmapped).
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import gmi_scan as scan

FEAT_CHOICES_HEADER = "IN_SCOPE|NON_VISUAL|OUT_OF_SCOPE"

# 12-category fidelity taxonomy (app-agnostic)
FIDELITY_CATEGORY = {
    "layout_width": "几何-尺寸", "layout_height": "几何-尺寸",
    "layout_margin": "几何-边距", "layout_marginTop": "几何-边距", "layout_marginBottom": "几何-边距",
    "layout_marginStart": "几何-边距", "layout_marginEnd": "几何-边距",
    "layout_marginLeft": "几何-边距", "layout_marginRight": "几何-边距",
    "padding": "几何-内距", "paddingStart": "几何-内距", "paddingEnd": "几何-内距",
    "paddingTop": "几何-内距", "paddingBottom": "几何-内距",
    "background": "视觉-形/色", "src": "视觉-图标", "tint": "视觉-色", "cardBackgroundColor": "视觉-色",
    "cardCornerRadius": "视觉-形", "foreground": "视觉-形/色",
    "textSize": "视觉-字", "textColor": "视觉-字", "textStyle": "视觉-字", "alpha": "视觉-字",
    "gravity": "几何-对齐", "hint": "视觉-字", "maxLines": "视觉-字", "fontFamily": "视觉-字",
    "inputType": "交互-输入", "entries": "交互-选项", "visibility": "状态", "clickable": "交互-点击",
    "checked": "状态", "enabled": "状态", "selected": "状态",
    "maxLength": "交互-输入",
    "layout_weight": "几何-尺寸", "layout_gravity": "几何-对齐",
    "scaleType": "视觉-形", "contentDescription": "可访问性", "importantForAccessibility": "可访问性",
    # position / alignment (gap2)
    "layout_alignParentEnd": "几何-定位", "layout_alignParentStart": "几何-定位",
    "layout_alignParentTop": "几何-定位", "layout_alignParentBottom": "几何-定位",
    "layout_alignEnd": "几何-定位", "layout_alignStart": "几何-定位",
    "layout_centerInParent": "几何-定位", "layout_toEndOf": "几何-定位", "layout_toStartOf": "几何-定位",
    "layout_below": "几何-定位", "layout_above": "几何-定位",
    "layout_centerVertical": "几何-定位", "layout_centerHorizontal": "几何-定位", "layout_alignBaseline": "几何-定位",
    # elevation / stroke / spacing / misc (gap6)
    "elevation": "视觉-形", "cardElevation": "视觉-形", "strokeWidth": "视觉-形", "strokeColor": "视觉-色",
    "translationX": "动画-位移", "translationY": "动画-位移",
    "srcCompat": "视觉-图标", "drawableTint": "视觉-色", "drawableStart": "视觉-图标",
    "drawableEnd": "视觉-图标", "drawableTop": "视觉-图标", "drawableBottom": "视觉-图标",
    "maxWidth": "几何-尺寸", "minWidth": "几何-尺寸", "minHeight": "几何-尺寸",
    "contentPadding": "几何-内距", "backgroundTint": "视觉-色", "textInputType": "交互-输入",
    # compose attributes
    "label": "字段-标签", "icon": "视觉-图标", "spacing": "几何-内距", "divider": "几何-分割",
    "title": "视觉-字", "subtitle": "视觉-字", "navigationIcon": "视觉-图标", "menu": "视觉-菜单",
}

GEOMETRY_KEYS = {
    "layout_width", "layout_height", "layout_weight", "layout_gravity", "layout_margin",
    "layout_marginTop", "layout_marginBottom", "layout_marginStart", "layout_marginEnd",
    "padding", "paddingStart", "paddingEnd", "paddingTop", "paddingBottom",
}
STATUS_KEYS = {"visibility", "clickable", "enabled", "checked", "selected"}


def _csv_write(path: Path, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as h:
            w = csv.DictWriter(h, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# features
# ---------------------------------------------------------------------------

FEATURE_ALIASES = {"LIST": "TODO-LIST", "ADD": "TODO-ADD", "UPDATE": "TODO-UPDATE", "MAIN": "APP-NAVIGATION", "NOTES": "TODO-LIST", "HOME": "TODO-LIST", "SETTINGS": "APP-NAVIGATION"}

def normalize_feature(symbol: str) -> str:
    s = re.sub(r"(Fragment|Activity|Screen|Page)$", "", symbol or "")
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").upper() or "MAIN"
    return FEATURE_ALIASES.get(s, s)


def derive_features(pages: List[Dict[str, Any]], has_data_layer: bool) -> List[str]:
    feats: List[str] = []
    seen = set()
    for p in pages:
        f = normalize_feature(p["symbol"])
        if f not in seen:
            seen.add(f)
            feats.append(f)
    # Keep legacy Todo names stable when present
    if any(f in ("TODO-LIST", "TODO-ADD", "TODO-UPDATE") for f in feats):
        for legacy in ["TODO-LIST", "TODO-ADD", "TODO-UPDATE", "TODO-DELETE", "ROOM-DATABASE"]:
            if legacy not in seen:
                # Only add if project actually has data layer / delete affordance
                if legacy == "TODO-DELETE" and not any("SwipeToDelete" in str(p) or "delete" in str(p).lower() for p in pages):
                    continue
                if legacy == "ROOM-DATABASE" and not has_data_layer:
                    continue
                seen.add(legacy)
                feats.append(legacy)
                if legacy == "TODO-LIST":
                    feats = [f for f in feats if f not in ("LIST",)]
        # Remove generic duplicates after aliasing
        feats = [f for f in feats if f not in ("LIST", "ADD", "UPDATE") or f in ("TODO-LIST", "TODO-ADD", "TODO-UPDATE")]
    if "APP-NAVIGATION" not in seen:
        feats.append("APP-NAVIGATION")
    if has_data_layer and "DATA" not in seen and "ROOM-DATABASE" not in seen:
        feats.append("DATA")
    return feats


def map_page_features(pages: List[Dict[str, Any]], features: List[str],
                      override: Dict[str, str]) -> None:
    low_features = {f.lower(): f for f in features}
    for p in pages:
        sym = p["symbol"]
        if sym in override:
            p["feature"] = override[sym]
            continue
        matched = None
        for lf, f in low_features.items():
            if lf and (lf in sym.lower() or sym.lower() in lf):
                matched = f
                break
        p["feature"] = matched or normalize_feature(sym) if normalize_feature(sym) in features else features[0]


def load_page_feature_override(path: Optional[str]) -> Dict[str, str]:
    if not path:
        return {}
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sym = (row.get("page_symbol") or row.get("symbol") or "").strip()
            feat = (row.get("feature_id") or row.get("feature") or "").strip()
            if sym and feat:
                out[sym] = feat
    return out


# ---------------------------------------------------------------------------
# states
# ---------------------------------------------------------------------------

def build_states(pages: List[Dict[str, Any]], rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    states: List[Dict[str, Any]] = []
    seen = set()
    for p in pages:
        src = p.get("source_refs", [""])[0] if p.get("source_refs") else ""
        sid = f"STATE-{p['page_id']}-DEFAULT"
        if sid not in seen:
            seen.add(sid)
            states.append({"page_id": p["page_id"], "expression": "DEFAULT",
                           "source_ref": src, "state_id": sid})
    # each business-rule condition is a candidate state (guards / data ops)
    for r in rules:
        expr = r["condition"][:80]
        sid = scan.state_id(r["page_id"], expr, r["source_ref"])
        if sid not in seen:
            seen.add(sid)
            states.append({"page_id": r["page_id"], "expression": expr,
                           "source_ref": r["source_ref"], "state_id": sid})
    return states


# ---------------------------------------------------------------------------
# candidate generation
# ---------------------------------------------------------------------------

def make_code_rows(code: List[Dict[str, Any]], comps: List[Dict[str, Any]],
                   pages: List[Dict[str, Any]], features: List[str]) -> List[Dict[str, Any]]:
    page_by_id = {p["page_id"]: p for p in pages}
    default_page = pages[0]["page_id"] if pages else ""
    default_feat = features[0] if features else ""
    data_feat = "DATA" if "DATA" in features else default_feat
    rows = []
    idx = 1
    for c in code:
        pid = c["page_id"] or default_page
        feat = page_by_id.get(pid, {}).get("feature", default_feat)
        # data-layer files (DAO/DB/Entity/Repo/ViewModel) suggest DATA feature
        fp = c["file_path"].lower()
        if "data" in fp or "dao" in fp or "database" in fp or "entity" in fp or "repository" in fp or "viewmodel" in fp:
            feat = data_feat
        rows.append({
            "candidate_id": f"CAND-CODE-{idx:04d}",
            "code_ref": c["source_ref"], "file_path": c["file_path"], "line": c["line"],
            "symbol": c["symbol"], "snippet": c["snippet"][:120],
            "suggested_feature": feat, "suggested_page": pid,
            "choices_feature": "|".join(features), "choices_disposition": FEAT_CHOICES_HEADER,
        })
        idx += 1
    for c in comps:
        pid = c["page_id"] or default_page
        feat = page_by_id.get(pid, {}).get("feature", default_feat)
        rows.append({
            "candidate_id": f"CAND-CODE-{idx:04d}",
            "code_ref": c["source_ref"], "file_path": c["layout"], "line": c["source_ref"].split(":", 1)[-1],
            "symbol": c["symbol_id"] if "symbol_id" in c else f"{c['type']}#{c['resource_id'] or c['component_id'][:8]}",
            "snippet": c["snippet"][:120],
            "suggested_feature": feat, "suggested_page": pid,
            "choices_feature": "|".join(features), "choices_disposition": FEAT_CHOICES_HEADER,
        })
        idx += 1
    return rows


def make_br_rows(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for i, r in enumerate(rules, start=1):
        rows.append({
            "candidate_id": f"CAND-BR-{i:04d}",
            "source_ref": r["source_ref"], "page_id": r["page_id"],
            "condition": r["condition"], "outcome_hint": r["outcome_hint"],
            "example_rule": r["example_rule"], "feature_hint": r["feature_hint"],
        })
    return rows


def _harmony_hint(attr: str) -> str:
    lk = attr.lower()
    if "color" in lk or "tint" in lk:
        return "color.json / ArkUI .fontColor/.backgroundColor"
    if "textsize" in lk or "textstyle" in lk or "alpha" in lk or "font" in lk or lk == "gravity":
        return "ArkUI .fontSize/.fontWeight/.opacity/.textAlign"
    if "background" in lk or "radius" in lk or "stroke" in lk:
        return "ArkUI .backgroundColor/.border/.borderRadius"
    if "src" in lk or "icon" in lk or "drawable" in lk:
        return "base/media/<vector>.svg / .icon"
    if "margin" in lk or "padding" in lk:
        return "ArkUI .margin/.padding"
    if "constraint" in lk or "guideline" in lk:
        return "ArkUI .constraint / Stack align"
    if "align" in lk or "gravity" in lk or "toendof" in lk or "tostartof" in lk or "below" in lk or "above" in lk or "center" in lk:
        return "ArkUI .position / .offset / Stack align"
    if "elevation" in lk:
        return "ArkUI .shadow/.elevation"
    if "layout_width" in lk or "layout_height" in lk or "weight" in lk:
        return "ArkUI .width/.height/.weight"
    if lk.startswith("event:"):
        return "ArkUI .onClick / router"
    if "visibility" in lk or "clickable" in lk or "enabled" in lk or "checked" in lk or "selected" in lk:
        return "ArkUI 状态/交互属性"
    if lk == "divider":
        return "ArkUI Divider()"
    if lk == "spacing":
        return "ArkUI List/Column .space()"
    if lk == "label":
        return "ArkUI 表单字段 label"
    return "ArkUI 对应属性"


def make_asset_rows(comps: List[Dict[str, Any]], assets: List[Dict[str, Any]],
                    menu_items: List[Dict[str, Any]] = None,
                    compose_comps: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    rows = []
    # component attribute rows (layout XML + compose nodes)
    all_comps = list(comps) + list(compose_comps or [])
    for idx, comp in enumerate(all_comps, start=1):
        attrs = comp.get("attributes", {})
        # fidelity-first, plus position rules if present
        merged = dict(attrs)
        for k, v in (comp.get("position_rules") or {}).items():
            if k not in merged:
                merged[k] = v
        for key, val in merged.items():
            short = key.split(":", 1)[-1] if ":" in key else key
            if short not in FIDELITY_CATEGORY and not short.startswith("event"):
                continue
            cat = FIDELITY_CATEGORY.get(short, "其他")
            rows.append({
                "candidate_id": f"CAND-ASSET-{idx:04d}-{short}",
                "layout": comp["layout"], "component_id": comp["component_id"],
                "type": comp["type"], "resource_id": comp["resource_id"],
                "android_attr": f"{short}={val}", "resolved_value": val,
                "fidelity_key": short, "category12": cat,
                "harmony_target_hint": _harmony_hint(short),
                "page_id": comp.get("page_id", ""),
                "choices_hint": "填Harmony对应值 / 选 保持/映射/忽略",
            })
    # menu item rows: icon linkage (gap1) — each menu item becomes a component row w/ icon+title+showAsAction
    for idx, mi in enumerate(menu_items or [], start=len(all_comps) + 1):
        comp = {
            "layout": mi["file"], "component_id": f"COMP-MENU-{mi['item_id'] or 'x'}",
            "type": "menu_item",
            "resource_id": mi["item_id"], "page_id": mi["page_id"],
            "attributes": {"icon": mi["icon"], "title": mi["title"], "showAsAction": mi["showAsAction"],
                           ("grade" if mi["showAsAction"] else "grade"): mi["grade"]},
        }
        for key, val in comp["attributes"].items():
            short = key if key in FIDELITY_CATEGORY else "menu_attr"
            cat = FIDELITY_CATEGORY.get(short, "几何-定位" if key == "grade" else "其他")
            rows.append({
                "candidate_id": f"CAND-ASSET-{idx:04d}-{key}",
                "layout": mi["file"], "component_id": comp["component_id"],
                "type": "menu_item", "resource_id": mi["item_id"],
                "android_attr": f"{key}={val}", "resolved_value": str(val),
                "fidelity_key": key, "category12": cat,
                "harmony_target_hint": "ArkUI 菜单条/Toolbar 图标" if key == "icon" else ("ArkUI showAsAction 对应 常驻/溢出" if key == "showAsAction" else "ArkUI 菜单项"),
                "page_id": mi["page_id"], "choices_hint": "填Harmony对应值 / 选 保持/映射/忽略",
            })
    # FILE_ASSET rows
    for a in assets:
        rows.append({
            "candidate_id": f"CAND-ASSET-FILE-{a['source_path'].replace('/', '-')}",
            "layout": a["source_path"], "component_id": "", "type": "FILE_ASSET",
            "resource_id": a["source_path"], "android_attr": a["source_path"],
            "resolved_value": a.get("sha256", "")[:8], "fidelity_key": "FILE",
            "category12": "", "harmony_target_hint": "mirror to harmony-project/entry/src/main/resources",
            "page_id": "", "choices_hint": "",
        })
    return rows


# page fields: ordered field list with icon linkage (gap4/gap5)
FIELD_XML_KEYWORDS = ("EditText", "TextView", "Button", "Spinner", "CheckBox", "Switch",
                      "RadioButton", "AutoCompleteTextView", "MultiAutoCompleteTextView",
                      "SeekBar", "RatingBar", "TextInputEditText", "MaterialSwitch", "com.google.android.material.textfield.TextInputEditText")

def make_page_field_rows(comps: List[Dict[str, Any]], pages: List[Dict[str, Any]],
                         compose_nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    page_by_id = {p["page_id"]: p for p in pages}
    # XML components (ordered by doc_order)
    for c in comps:
        t = c["type"]
        if not any(k in t for k in FIELD_XML_KEYWORDS):
            continue
        if not (c.get("resource_id") or c.get("text")):
            continue
        page = page_by_id.get(c.get("page_id", ""), {})
        icon = ""
        for k in ("drawableStart", "drawableEnd", "drawableTop", "drawableBottom", "src", "background"):
            v = (c.get("attributes") or {}).get(k, "")
            if v and v.startswith("@drawable") or (v and "drawable" in str(v)):
                icon = v
                break
        rows.append({
            "page_id": c.get("page_id", ""), "page_symbol": page.get("symbol", ""),
            "order_index": c.get("doc_order", 0), "field_id": c.get("resource_id") or f"{t}-no-id",
            "field_type": t.split(".")[-1], "field_label": c.get("text", ""),
            "icon_resource": icon, "layout_ref": c["layout"],
            "source_ref": c["source_ref"],
        })
    # compose field nodes (ordered)
    for n in compose_nodes or []:
        if n.get("kind") not in ("field", "canvas_field"):
            continue
        page = page_by_id.get(n.get("page_id", ""), {})
        attrs = n.get("attributes", {})
        rows.append({
            "page_id": n.get("page_id", ""), "page_symbol": page.get("symbol", ""),
            "order_index": n.get("order", 0), "field_id": n.get("resource_id") or f"compose-field-{n.get('order')}",
            "field_type": n["type"].split(":")[-1],
            "field_label": attrs.get("label", "") or n.get("text", ""),
            "icon_resource": ("R.drawable." + attrs["icon"]) if attrs.get("icon") else "",
            "layout_ref": n["layout"], "source_ref": n["source_ref"],
        })
    rows.sort(key=lambda r: (r["page_id"], r["order_index"]))
    return rows


# sub-options: Preference entries -> array items; compose when-branches; spinner entries (gap: 子选项)
def _page_for_source_hint(source: str, pages: List[Dict[str, Any]]) -> str:
    """Conservatively bind preference/menu facts to one page; ambiguity remains blocking."""
    stem = Path((source or "").split(":", 1)[0]).stem
    hint_tokens = {
        token for token in re.findall(r"[a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", stem).lower())
        if token not in {"preference", "preferences", "activity", "fragment", "screen", "page", "xml", "menu"}
    }
    ranked: List[tuple[int, str]] = []
    for page in pages:
        symbol = str(page.get("symbol", ""))
        page_tokens = {
            token for token in re.findall(r"[a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", symbol).lower())
            if token not in {"activity", "fragment", "screen", "page"}
        }
        score = len(hint_tokens & page_tokens)
        if score:
            ranked.append((score, str(page.get("page_id", ""))))
    if not ranked:
        return ""
    ranked.sort(reverse=True)
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return ""
    return ranked[0][1]


def make_field_options_rows(pref_rows: List[Dict[str, Any]],
                            arrays: Dict[str, List[str]],
                            to_opt_branches: Dict[str, List[str]],
                            branch_sources: Dict[str, str],
                            pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    i = 0
    # ① when-branch option sets first (e.g. SettingsDestination.LookAndFeel -> LookAndFeel())
    for arg, opts in to_opt_branches.items():
        if not opts or len(opts) < 2:
            continue
        for j, o in enumerate(opts, start=1):
            i += 1
            source_ref = branch_sources.get(arg, "")
            rows.append({
                "candidate_id": f"CAND-OPT-{i:04d}-{j:02d}",
                "page_id": _page_for_source_hint(source_ref or arg, pages), "group": f"when({arg})", "group_key": f"when:{arg}",
                "option_label": f"when({arg})", "option_type": "WHEN_BRANCH",
                "sub_option": str(o), "sub_option_index": j,
                "ref_key": str(o), "default_value": "", "summary": "",
                "source_ref": source_ref or f"when({arg})",
            })
    # ② Preference endpoint entries -> array items
    for p in pref_rows:
        i += 1
        options: List[str] = []
        if p.get("entries"):
            raw = p["entries"].replace("@array/", "").replace("@string/", "")
            options = arrays.get(raw, [])
            if not options:
                options = ["<array %s>" % raw]
        if not options and p.get("fragment"):
            options = ["<子屏 fragment %s>" % p["fragment"]]
        if not options and p.get("key"):
            opt = read_key_options(p["key"], to_opt_branches)
            if opt:
                options = opt
        if options:
            for j, o in enumerate(options, start=1):
                rows.append({
                    "candidate_id": f"CAND-OPT-{i:04d}-{j:02d}",
                    "page_id": _page_for_source_hint(p["file"], pages), "group": p["file"], "group_key": p.get("key", ""),
                    "option_label": p.get("title", "") or p.get("key", ""),
                    "option_type": p["tag"], "sub_option": str(o),
                    "sub_option_index": j, "ref_key": p.get("key", ""),
                    "default_value": p.get("defaultValue", ""),
                    "summary": p.get("summary", ""),
                    "source_ref": p.get("source_ref", ""),
                })
    return rows


def read_key_options(key: str, to_opt_branches: Dict[str, List[str]]) -> List[str]:
    return to_opt_branches.get(key.replace("@string/", ""), [])


def make_menu_option_rows(menu_when_rows: List[Dict[str, Any]], pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """when(item.itemId) menu branches -> option rows (菜单项 -> 行为/目标)."""
    rows = []
    for i, m in enumerate(menu_when_rows, start=1):
        block = m["block"]
        target = ""
        cls = re.search(r"([A-Za-z0-9_.]+::class)", block)
        if cls:
            target = cls.group(1).replace("::class", "")
        elif "launch" in block or "navigate" in block or "backStack.add" in block:
            target = "<launch/navigate>"
        elif "showMenu" in block or "PopupMenu" in block or "MenuState" in block:
            target = "<展开菜单>"
        rows.append({
            "candidate_id": f"CAND-OPT-{i:04d}-MENU",
            "page_id": _page_for_source_hint(m["file"], pages), "group": m["file"], "group_key": f"menu:{m['menu_id']}",
            "option_label": m["menu_id"], "option_type": "MENU_ITEM",
            "sub_option": target, "sub_option_index": 1,
            "ref_key": f"menu:{m['menu_id']}",
            "default_value": "", "summary": "",
            "source_ref": f"{m['file']}:{m['line']}",
        })
    return rows




def make_behavior_rows(behaviors: List[Dict[str, Any]],
                       pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """behavior flow rows -> candidates（P4 行为契约消费）。"""
    sym_by_id = {p["page_id"]: p["symbol"] for p in pages}
    rows = []
    for i, b in enumerate(behaviors, start=1):
        rows.append({
            "candidate_id": f"CAND-BEH-{i:04d}",
            "page_id": b.get("page_id", ""),
            "page_symbol": b.get("page_symbol", "") or sym_by_id.get(b.get("page_id", ""), ""),
            "event": b.get("event", ""), "action": b.get("action", ""),
            "params": b.get("params", ""), "data_target": b.get("data_target", ""),
            "side_effect": b.get("side_effect", ""), "source_ref": b.get("source_ref", ""),
        })
    return rows


def make_nav_relation_rows(rels: List[Dict[str, Any]],
                           pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    sym_by_id = {p["page_id"]: p["symbol"] for p in pages}
    for i, r in enumerate(rels, start=1):
        rows.append({
            "candidate_id": f"CAND-NAV-{i:04d}",
            "from_page_id": r["page_id"], "from_page_symbol": sym_by_id.get(r["page_id"], ""),
            "trigger": r["trigger"], "action": r["action"],
            "to_page_id": r["to_page_id"], "relation_type": r["relation_type"],
            "source_ref": r["source_ref"],
        })
    return rows


def make_risk_rows(probes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """risk probes -> candidate rows (备忘用，不参与覆盖台账)。"""
    rows = []
    for i, p in enumerate(probes, start=1):
        rows.append({
            "candidate_id": f"CAND-RISK-{i:04d}",
            "probe_id": p["probe_id"], "category": p["category"],
            "severity": p["severity"], "file": p["file"], "line": p["line"],
            "signal": p["signal"], "count": p.get("count", ""),
            "page_id": p.get("page_id", ""), "harmony_hint": p["harmony_hint"],
        })
    return rows


def make_palette_rows(pal: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """color palette (真值: hex+alpha) -> candidate rows."""
    rows = []
    for i, p in enumerate(pal, start=1):
        rows.append({
            "candidate_id": f"CAND-COLOR-{i:04d}",
            "color_name": p["name"], "hex": p["hex"], "alpha": p["alpha"],
            "kind": p["kind"], "file": p["file"], "line": p["line"],
        })
    return rows


def make_motion_rows(motion: List[Dict[str, Any]], pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """motion/动效 -> candidate rows."""
    rows = []
    sym_by_id = {p["page_id"]: p["symbol"] for p in pages}
    for i, m in enumerate(motion, start=1):
        rows.append({
            "candidate_id": f"CAND-MOTION-{i:04d}",
            "page_id": m["page_id"], "page_symbol": sym_by_id.get(m["page_id"], ""),
            "motion_type": m["motion_type"], "signal": m["signal"],
            "file": m["file"], "line": m["line"],
        })
    return rows


# 10 类缺口验收矩阵 (phase-2-completeness): 每页 × 10 类语义是否已录
COMPLETENESS_CATEGORIES = [
    ("图标", "icon"), ("位置", "position"), ("主次", "primary_secondary"),
    ("字段", "fields"), ("图标联动", "icon_binding"), ("版式", "layout"),
    ("依赖", "dependencies"), ("导航", "navigation"), ("动效", "motion"),
    ("颜色/真值", "color"),
]


def make_completeness_rows(pages: List[Dict[str, Any]],
                           comps: List[Dict[str, Any]], compose_nodes: List[Dict[str, Any]],
                           field_rows: List[Dict[str, Any]], nav_rows: List[Dict[str, Any]],
                           motion_rows: List[Dict[str, Any]], palette: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """每页 × 10 类：RECORDED / MISSING / N/A（无 UI 的纯容器页不检查 UI 类）。"""
    rows = []
    page_by_id = {p["page_id"]: p for p in pages}
    for p in pages:
        pid = p["page_id"]
        sym = p["symbol"]
        # 该页是否有 UI 节点（layout comps 或 compose 节点）
        ui_pages = set()
        for c in list(comps) + list(compose_nodes):
            ui_pages.add(c.get("page_id", ""))
        has_ui = pid in ui_pages
        # 哪些页有图标/绑定/版式信息
        icon_pages, bound_pages, layout_info_pages = set(), set(), set()
        for c in list(comps) + list(compose_nodes):
            attrs = c.get("attributes") or {}
            if attrs.get("icon"):
                icon_pages.add(c.get("page_id", ""))
                if c.get("page_id", "") in ui_pages:
                    bound_pages.add(c.get("page_id", ""))
            if (c.get("fidelity_attrs") or any(k in attrs for k in ("spacing", "divider", "shape", "size_dp", "blur_dp", "gradient"))):
                layout_info_pages.add(c.get("page_id", ""))
        field_page_rows = [r for r in field_rows if r.get("page_id") == pid]
        nav_page_rows = [r for r in nav_rows if r["from_page_id"] == pid]
        motion_page_rows = [r for r in motion_rows if r["page_id"] == pid]
        # 动效/色板/依赖为全工程属性，命中即全录
        has_motion_any = bool(motion_rows)

        checks = {
            "icon": (pid in icon_pages) if has_ui else None,
            "position": bool(any(
                a for c in list(comps) + list(compose_nodes) if c.get("page_id") == pid
                for a in (c.get("attributes") or {}) if "margin" in a or "align" in a or "gravity" in a or "weight" in a or "layout_gravity" in a or "constraint" in a),
            ) if has_ui else None,
            "primary_secondary": bool(nav_page_rows and any("menu" in str(r.get("trigger", "")).lower() for r in nav_page_rows)),
            "fields": len(field_page_rows) > 0,
            "icon_binding": pid in bound_pages if has_ui else None,
            "layout": pid in layout_info_pages if has_ui else None,
            "dependencies": bool(palette),
            "navigation": bool(nav_page_rows),
            "motion": has_motion_any or bool(motion_page_rows),
            "color": bool(palette),
        }
        for label, key in COMPLETENESS_CATEGORIES:
            v = checks.get(key)
            if v is None:
                status = "N/A"
                hint = f"{sym} 为纯容器页，不适用 {label} 检查"
            elif v:
                status = "RECORDED"
                hint = ""
            else:
                status = "MISSING"
                hint = f"{sym} 缺 {label} 语义，需补录"
            rows.append({
                "page_id": pid, "page_symbol": sym, "check_category": label, "check_key": key,
                "status": status, "hint": hint,
            })
    return rows


def make_pref_concat_rows(pref_rows: List[Dict[str, Any]], pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Preference tree -> page-fields compatible rows (grouped by file)."""
    rows = []
    for i, p in enumerate(pref_rows, start=1):
        if p["tag"] in PREF_ENDPOINT_SET:
            page_id = _page_for_source_hint(p["file"], pages)
            page = next((item for item in pages if item.get("page_id") == page_id), {})
            rows.append({
                "page_id": page_id, "page_symbol": page.get("symbol", ""),
                "order_index": p["level"] * 100 + i, "field_id": p.get("key", "") or f"pref-{i}",
                "field_type": p["tag"], "field_label": p.get("title", "") or p.get("key", ""),
                "icon_resource": p.get("icon", ""), "layout_ref": p["file"],
                "source_ref": p["source_ref"],
            })
    return rows


PREF_ENDPOINT_SET = {
    "Preference", "SwitchPreferenceCompat", "SwitchPreference", "ListPreference", "Dropdown",
    "SeekBarPreference", "TimePreference", "DatePreference", "EditTextPreference",
    "CheckBoxPreference", "RadioPreference", "IconPreference", "PasswordPreference",
}


def make_dep_rows(deps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """third-party-dependencies candidates (gap7): resolved g:a:v + scope + provenance."""
    rows = []
    seen = set()
    for d in deps:
        res = d.get("resolution", "")
        if res not in ("ALIAS", "DIRECT", "CATALOG", "ALIAS_UNRESOLVED"):
            continue
        key = (d.get("group", ""), d.get("artifact", ""), res)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "candidate_id": f"CAND-DEP-{len(rows)+1:04d}",
            "source_ref": d["source_ref"], "group": d.get("group", ""),
            "artifact": d.get("artifact", ""), "version": d.get("version", ""),
            "resolution": res, "scope": d.get("scope", ""),
            "condition": d["condition"], "example_rule": d["example_rule"],
            "feature_hint": d.get("feature_hint", "") or d.get("page_id", ""),
        })
    return rows


def make_inventory_rows(features: List[str], pages: List[Dict[str, Any]],
                        states: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Fix: page×state (not feature×state) to avoid 6× explosion. Feature is derived from page.
    page_by_id = {p["page_id"]: p for p in pages}
    rows = []
    env = "ENV-001"
    seen = set()
    # One DEFAULT per page
    for p in pages:
        sid = f"STATE-{p['page_id']}-DEFAULT"
        if sid not in seen:
            seen.add(sid)
            rows.append({
                "candidate_id": f"CAND-INV-{p['page_id']}-DEFAULT-{env}",
                "feature_id": p.get("feature", features[0] if features else ""),
                "page_id": p["page_id"],
                "state_id": sid, "state_expression": "DEFAULT",
                "env_id": env, "entry_condition": "App launched / navigate to page",
                "expected_observable": f"{p['symbol']} displayed",
                "source_ref": (p.get("source_refs", [""])[0] if p.get("source_refs") else ""),
            })
    # One row per distinct state (page×expression), feature from owning page
    for st in states:
        sid = st["state_id"]
        if sid in seen:
            continue
        seen.add(sid)
        pid = st["page_id"]
        feat = page_by_id.get(pid, {}).get("feature", features[0] if features else "")
        rows.append({
            "candidate_id": f"CAND-INV-{pid}-{sid.split('-')[-1]}-{env}",
            "feature_id": feat, "page_id": pid,
            "state_id": sid, "state_expression": st["expression"],
            "env_id": env, "entry_condition": f"Satisfy {st['expression'][:40]}",
            "expected_observable": f"State {st['expression'][:40]} visible",
            "source_ref": st["source_ref"],
        })
    return rows


# ---------------------------------------------------------------------------
# coverage ledger + gate
# ---------------------------------------------------------------------------

def build_ledger(files: List[Dict[str, Any]], candidates_by_file: Dict[str, List[str]],
                 pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    page_syms = {p["symbol"] for p in pages}
    ledger = []
    for f in files:
        rel, cat = f["rel"], f["category"]
        covering = sorted(set(candidates_by_file.get(rel, [])))
        if covering:
            stem = Path(rel).stem
            is_data_logic = ("/data/" in rel.lower() or "/viewmodel" in rel.lower()
                             or "/repository" in rel.lower() or "/dao" in rel.lower()
                             or "/adapter" in rel.lower() or "/utils" in rel.lower())
            if cat == "source" and stem not in page_syms and not is_data_logic:
                disposition = "NON_VISUAL"
            else:
                disposition = "IN_SCOPE"
            status = "COVERED"
        elif cat in scan.OUT_OF_SCOPE_CATEGORIES:
            disposition, status = "OUT_OF_SCOPE", "ACKNOWLEDGED"
        else:
            disposition, status = "UNMAPPED", "GAP"
        ledger.append({
            "file": rel, "category": cat, "disposition": disposition, "status": status,
            "covering_candidates": ";".join(covering[:5]) + (";..." if len(covering) > 5 else ""),
        })
    return ledger


# ---------------------------------------------------------------------------
# main generate
# ---------------------------------------------------------------------------

def generate(project: str, workspace: str, features: Optional[List[str]] = None,
             page_feature_csv: Optional[str] = None, allow_unmapped: bool = False,
             verbose: bool = False) -> Dict[str, Any]:
    project = Path(project).resolve()
    workspace = Path(workspace).resolve()
    if not project.is_dir():
        raise SystemExit(f"project not found: {project}")

    files = scan.scan_files(project)
    pages = scan.discover_pages(project, files)

    # Compose screens are pages too (they can't be discovered from manifest/class hierarchy)
    compose_screens = scan._compose_screens(files)
    known = {p["symbol"] for p in pages}
    for scr in compose_screens:
        if scr["symbol"] in known:
            continue
        pages.append({
            "symbol": scr["symbol"], "page_id": scan.page_id(scr["symbol"]),
            "kinds": ["ComposeScreen"], "source_refs": [scr["file"]],
            "layout_names": [], "is_start": False, "features": [],
        })
        known.add(scr["symbol"])
    has_data = scan.discover_data_layer(files)

    if features is None or not features:
        features = derive_features(pages, has_data)
    else:
        features = [f.strip() for f in features if f.strip()]
        if "APP-NAVIGATION" not in features:
            features.append("APP-NAVIGATION")
        if has_data and "DATA" not in features:
            features.append("DATA")

    override = load_page_feature_override(page_feature_csv)
    map_page_features(pages, features, override)

    code = scan.scan_code_symbols(files, pages)
    comps = scan.scan_layout_components(files, pages)
    # enrich component symbols/snippets for code-map
    for c in comps:
        c["symbol_id"] = f"{c['type']}#{c['resource_id'] or c['component_id'][:8]}"
        c["snippet"] = f"<{c['type']} id={c['resource_id']} text={c.get('text','')[:40]}>"
    rules = scan.scan_business_rules(files, pages)
    default_page = pages[0]["page_id"] if pages else ""
    for gr in rules:
        if not gr.get("page_id"):
            gr["page_id"] = default_page
        gr["feature_hint"] = gr["page_id"]
    # menu detail (gap1/2/3: icon/showAsAction/主次)
    menu_items = scan.scan_menus(files, pages)
    # compose detail (gap4/5/6: fields order/icons/spacers/dividers)
    compose_nodes = scan.scan_compose(files, pages, compose_screens)
    # shape/color/blur details per compose node (value-level)
    scan.scan_shape_details(compose_nodes)
    # build dependencies via version catalog (gap7)
    catalog = scan.scan_version_catalog(files)
    deps = scan.scan_gradle_deps(files, catalog)
    for d in deps:
        if not d.get("page_id"):
            d["page_id"] = default_page
        d["feature_hint"] = d["page_id"]
    assets = scan.scan_assets(files)
    states = build_states(pages, rules + deps)

    # preference tree (res/xml) + option arrays + nav relations (gap: 子选项 & 跳转/返回)
    pref_rows = scan.scan_preference_xml(files)
    arrays = scan.scan_string_arrays(files)
    to_branches, branch_sources, menu_when_rows = scan.scan_when_branches(files)
    nav_rels = scan.scan_nav_relations(files, pages)
    behaviors = scan.scan_behaviors(files, pages)
    risk_probes = scan.scan_risk_probes(files, pages)
    palette = scan.scan_color_palette(files)
    motion = scan.scan_motion(files, pages)

    code_rows = make_code_rows(code, comps, pages, features)
    br_rows = make_br_rows(rules + deps)
    asset_rows = make_asset_rows(comps, assets, menu_items, compose_nodes)
    inv_rows = make_inventory_rows(features, pages, states)
    field_rows = make_page_field_rows(comps, pages, compose_nodes) + make_pref_concat_rows(pref_rows, pages)
    dep_rows = make_dep_rows(deps)
    opt_rows = make_field_options_rows(pref_rows, arrays, to_branches, branch_sources, pages) + make_menu_option_rows(menu_when_rows, pages)
    nav_rows = make_nav_relation_rows(nav_rels, pages)
    behavior_rows = make_behavior_rows(behaviors, pages)
    risk_rows = make_risk_rows(risk_probes)
    pal_rows = make_palette_rows(palette)
    motion_rows = make_motion_rows(motion, pages)
    compl_rows = make_completeness_rows(pages, comps, compose_nodes, field_rows, nav_rows, motion_rows, palette)

    # candidates_by_file for the ledger
    cand_by_file: Dict[str, List[str]] = {}
    for r in code_rows:
        cand_by_file.setdefault(r["file_path"], []).append(r["candidate_id"])
    for r in br_rows:
        cand_by_file.setdefault(r["source_ref"].split(":", 1)[0], []).append(r["candidate_id"])
    for r in asset_rows:
        cand_by_file.setdefault(r["layout"], []).append(r["candidate_id"])
    for r in inv_rows:
        cand_by_file.setdefault(r["source_ref"].split(":", 1)[0], []).append(r["candidate_id"])
    for r in dep_rows:
        cand_by_file.setdefault(r["source_ref"].split(":", 1)[0], []).append(r["candidate_id"])
    for r in field_rows:
        cand_by_file.setdefault(r["layout_ref"] if r.get("layout_ref") else r.get("file", ""), []).append(f"FIELD-{r['order_index']}-{r['field_id']}")
    for r in opt_rows:
        cand_by_file.setdefault(r["source_ref"].split(":", 1)[0], []).append(r["candidate_id"])
    for r in nav_rows:
        cand_by_file.setdefault(r["source_ref"].split(":", 1)[0], []).append(r["candidate_id"])

    ledger = build_ledger(files, cand_by_file, pages)

    # write outputs
    out_cand = workspace / "candidates"
    out_cand.mkdir(parents=True, exist_ok=True)
    names = {
        "code-map.candidates.full.csv": code_rows,
        "business-rules.candidates.csv": br_rows,
        "asset-mapping.candidates.csv": asset_rows,
        "inventory.candidates.csv": inv_rows,
        "page-fields.candidates.csv": field_rows,
        "third-party-dependencies.candidates.csv": dep_rows,
        "field-options.candidates.csv": opt_rows,
        "navigation-relations.candidates.csv": nav_rows,
        "behavior.candidates.csv": behavior_rows,
        "risk-probes.candidates.csv": risk_rows,
        "color-palette.candidates.csv": pal_rows,
        "motion.candidates.csv": motion_rows,
        "phase-2-completeness.csv": compl_rows,
    }
    _csv_write(out_cand / "code-map.candidates.full.csv",
               ["candidate_id", "code_ref", "file_path", "line", "symbol", "snippet",
                "suggested_feature", "suggested_page", "choices_feature", "choices_disposition"], code_rows)
    _csv_write(out_cand / "business-rules.candidates.csv",
               ["candidate_id", "source_ref", "page_id", "condition", "outcome_hint",
                "example_rule", "feature_hint"], br_rows)
    _csv_write(out_cand / "asset-mapping.candidates.csv",
               ["candidate_id", "layout", "component_id", "type", "resource_id", "android_attr",
                "resolved_value", "fidelity_key", "category12", "harmony_target_hint", "page_id", "choices_hint"], asset_rows)
    _csv_write(out_cand / "inventory.candidates.csv",
               ["candidate_id", "feature_id", "page_id", "state_id", "state_expression",
                "env_id", "entry_condition", "expected_observable", "source_ref"], inv_rows)
    _csv_write(out_cand / "page-fields.candidates.csv",
               ["page_id", "page_symbol", "order_index", "field_id", "field_type",
                "field_label", "icon_resource", "layout_ref", "source_ref"], field_rows)
    _csv_write(out_cand / "third-party-dependencies.candidates.csv",
               ["candidate_id", "source_ref", "group", "artifact", "version", "resolution",
                "scope", "condition", "example_rule", "feature_hint"], dep_rows)
    _csv_write(out_cand / "field-options.candidates.csv",
               ["candidate_id", "page_id", "group", "group_key", "option_label", "option_type",
                "sub_option", "sub_option_index", "ref_key", "default_value", "summary", "source_ref"], opt_rows)
    _csv_write(out_cand / "navigation-relations.candidates.csv",
               ["candidate_id", "from_page_id", "from_page_symbol", "trigger", "action",
                "to_page_id", "relation_type", "source_ref"], nav_rows)
    _csv_write(out_cand / "behavior.candidates.csv",
               ["candidate_id", "page_id", "page_symbol", "event", "action", "params",
                "data_target", "side_effect", "source_ref"], behavior_rows)
    _csv_write(out_cand / "risk-probes.candidates.csv",
               ["candidate_id", "probe_id", "category", "severity", "file", "line",
                "signal", "count", "page_id", "harmony_hint"], risk_rows)
    _csv_write(out_cand / "color-palette.candidates.csv",
               ["candidate_id", "color_name", "hex", "alpha", "kind", "file", "line"], pal_rows)
    _csv_write(out_cand / "motion.candidates.csv",
               ["candidate_id", "page_id", "page_symbol", "motion_type", "signal", "file", "line"], motion_rows)
    _csv_write(out_cand / "phase-2-completeness.csv",
               ["page_id", "page_symbol", "check_category", "check_key", "status", "hint"], compl_rows)

    # coverage ledger
    out_cov = workspace / "coverage"
    out_cov.mkdir(parents=True, exist_ok=True)
    _csv_write(out_cov / "coverage-ledger.csv",
               ["file", "category", "disposition", "status", "covering_candidates"], ledger)

    # manifest.sha256
    mlines = []
    for name in sorted(names):
        digest = hashlib.sha256((out_cand / name).read_bytes()).hexdigest()
        mlines.append(f"{digest}  {name}")
    (out_cand / "manifest.sha256").write_text("\n".join(mlines) + "\n", encoding="utf-8")

    # summary json
    counts = {"code_candidates": len(code_rows), "business_rule_candidates": len(br_rows),
              "asset_mapping_candidates": len(asset_rows), "inventory_candidates": len(inv_rows),
              "page_field_candidates": len(field_rows),
              "dependency_candidates": len(dep_rows),
              "field_option_candidates": len(opt_rows),
              "nav_relation_candidates": len(nav_rows),
              "behavior_candidates": len(behavior_rows),
              "risk_probe_candidates": len(risk_rows),
              "color_palette_candidates": len(pal_rows),
              "motion_candidates": len(motion_rows),
              "completeness_rows": len(compl_rows)}
    (out_cand / "candidates.json").write_text(json.dumps({
        "schema_version": 1, "generated_at": "GENERIC",
        "project": str(project), "features": features,
        "pages": [p["symbol"] for p in pages],
        "counts": counts,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    # write phase-manifest for traceability
    # 重要：若 P1 已产出 phase-manifest.json（含 scope/features 决策），不得整体覆盖；
    # 只补充 gmi 观测字段，P1 决策字段原样保留。
    manifest_path = workspace / "phase-manifest.json"
    merged_manifest: Dict[str, Any] = {}
    if manifest_path.exists():
        try:
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                merged_manifest.update(existing)
        except ValueError:
            merged_manifest = {}
    merged_manifest.update({
        "android_project_root": str(project),
        "phase": 2, "status": "GENERATED", "generator": "gmi",
        "gmi_counts": counts,
        "gmi_pages": [p["symbol"] for p in pages],
    })
    if "included_features" not in merged_manifest:
        merged_manifest["included_features"] = features
    manifest_path.write_text(json.dumps(merged_manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    # gate
    unmapped = [l for l in ledger if l["status"] == "GAP"]
    covered = sum(1 for l in ledger if l["status"] == "COVERED")
    ack = sum(1 for l in ledger if l["status"] == "ACKNOWLEDGED")

    summary = {
        "project": str(project), "workspace": str(workspace),
        "files_scanned": len(files), "pages": len(pages), "features": features,
        "counts": counts,
        "coverage": {"total": len(ledger), "covered": covered, "out_of_scope": ack, "unmapped": len(unmapped)},
    }
    if verbose:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[gmi] project={project.name} files={len(files)} pages={len(pages)} "
          f"features={','.join(features)}")
    print(f"[gmi] candidates code={counts['code_candidates']} br={counts['business_rule_candidates']} "
          f"asset={counts['asset_mapping_candidates']} inventory={counts['inventory_candidates']}"
          f" fields={counts['page_field_candidates']} deps={counts['dependency_candidates']}"
          f" options={counts['field_option_candidates']} nav={counts['nav_relation_candidates']}"
          f" risk={counts['risk_probe_candidates']} colors={counts['color_palette_candidates']}"
          f" beh={counts['behavior_candidates']}"
          f" motion={counts['motion_candidates']} completeness={counts['completeness_rows']}")
    print(f"[gmi] coverage covered={covered} out_of_scope={ack} unmapped={len(unmapped)}")

    if unmapped and not allow_unmapped:
        print("\n[gmi] UNMAPPED FILES (no candidate, run with --allow-unmapped to accept):")
        for l in unmapped:
            print(f"  GAP  {l['file']}  [{l['category']}]")
        raise SystemExit(1)
    if unmapped:
        print(f"\n[gmi] WARNING: {len(unmapped)} UNMAPPED files accepted via --allow-unmapped.")
        for l in unmapped:
            print(f"  GAP  {l['file']}  [{l['category']}]")
    else:
        print("[gmi] OK: UNMAPPED=0 -- full coverage gate passed.")

    return summary
