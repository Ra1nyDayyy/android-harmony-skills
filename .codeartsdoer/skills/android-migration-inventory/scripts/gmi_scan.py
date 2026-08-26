# -*- coding: utf-8 -*-
"""gmi_scan -- generic Android project scanner for migration inventory.

App-agnostic: discovers pages from AndroidManifest / navigation XML / source,
extracts code symbols, layout components, assets and business rules from ANY
Android project. Pure stdlib, zero app-specific names.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# constants / helpers
# ---------------------------------------------------------------------------

EXCLUDE_DIRS = {
    ".git", ".idea", "build", ".gradle", "node_modules", "__pycache__",
    ".codeartsdoer", ".codegraph", ".arts", ".svn", "dist", "Pods", "captures",
    "Pods", ".kotlin", ".cxx", ".externalNativeBuild",
}

BINARY_EXTS = {
    ".apk", ".jar", ".aar", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico",
    ".ttf", ".otf", ".zip", ".dex", ".so", ".class", ".hprof", ".db", ".keystore", ".bin",
}

# categories that are allowed to be OUT_OF_SCOPE without any candidate
OUT_OF_SCOPE_CATEGORIES = {"test", "build", "metadata", "binary", "other", "empty"}

FRAGMENT_BASES = ("Fragment",)
ACTIVITY_BASES = (
    "android.app.Activity", "androidx.appcompat.app.AppCompatActivity",
    "androidx.activity.ComponentActivity", "android.app.AppCompatActivity",
    "androidx.fragment.app.FragmentActivity", "Activity",
)

XML_ATTR_RE = re.compile(r"([\w:.-]+)\s*=\s*\"([^\"]*)\"")
TAG_RE = re.compile(r"<([A-Za-z][\w.-]*)\b([^>]*?)(/?)>")

_KOTLIN_LANG = re.compile(
    r"^\s*(?:public|internal|private|protected|open|abstract|sealed|data|final|expect|actual|\s)*"
    r"(class|interface|enum\s+class|object|annotation\s+class)\s+([A-Za-z0-9_]+)"
)
_FUN_RE = re.compile(r"\bfun\s+(?:<[^>]+>\s*)?(?:[A-Za-z0-9_.<>\?\s]+\.)*([A-Za-z0-9_]+)\s*\(")
_VAL_RE = re.compile(r"^\s*(?:private|protected|internal|public|override|lateinit|const|\s)*\b(?:val|var)\s+(?:<[^>]+>\s*)?(?:[A-Za-z0-9_.<>\?\s]+\.)*([A-Za-z0-9_]+)\b")
_IF_RE = re.compile(r"\bif\s*\(([^()]*)\)")
_WHEN_RE = re.compile(r"\bwhen\s*\(([^()]*)\)")
_QUERY_RE = re.compile(r"@Query\s*\(\s*\"([^\"]*)\"\s*\)")
_DATA_OP_RE = re.compile(r"@(Insert|Update|Delete|Dao)\b")
_DATABINDING_RE = re.compile(r"@\{[^}]*\}")
_ONCLICK_RE = re.compile(r"android:onClick\s*=\s*\"([^\"]+)\"")
_INFLATE_RE = re.compile(r"R\.layout\.([A-Za-z0-9_]+)")
_FRAG_CLASS_RE = re.compile(r"^\s*(?:public\s+|internal\s+|private\s+|protected\s+|abstract\s+|open\s+|data\s+|sealed\s+|final\s+)*class\s+([A-Za-z0-9_]+)\s*\(?[^:]*:\s*([A-Za-z0-9_.]+)")
_ACT_CLASS_RE = re.compile(r"^\s*(?:public\s+|internal\s+|private\s+|protected\s+|abstract\s+|open\s+|final\s+)*class\s+([A-Za-z0-9_]+)\s*\(?[^:]*:\s*([A-Za-z0-9_.]+)")
_DAO_IFACE_RE = re.compile(r"^\s*(?:@\w+\s*)*\binterface\s+([A-Za-z0-9_]+)\b")
_DB_CLASS_RE = re.compile(r"@Database")


def stable_hash(text: str, length: int = 8) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length].upper()


def page_id(symbol: str) -> str:
    norm = re.sub(r"[^A-Za-z0-9]+", "-", symbol).upper().strip("-")
    return f"PAGE-{norm}-{stable_hash(symbol)}"


def state_id(page: str, expr: str, src: str = "") -> str:
    seed = f"{page}|{expr}|{src}"
    tag = "M" if "Manifest" in src or not src else "F"
    return f"STATE-{page}-{tag}-{stable_hash(seed)}"


def component_id(seed: str) -> str:
    return f"COMP-{stable_hash(seed, 8)}"


# ---------------------------------------------------------------------------
# scanned-file ledger
# ---------------------------------------------------------------------------

def classify(rel: str) -> str:
    low = rel.lower()
    if rel.endswith("AndroidManifest.xml"):
        return "manifest"
    if rel.endswith((".kt", ".java")):
        if "/test/" in low or "/androidtest/" in low or rel.endswith(("Test.kt", "Test.java")):
            return "test"
        return "source"
    if "/res/layout/" in low:
        return "layout"
    if "/res/drawable" in low:
        return "drawable"
    if "/res/menu/" in low:
        return "menu"
    if "/res/navigation/" in low:
        return "navigation"
    if "/res/values" in low:
        return "values"
    if "/res/anim" in low:
        return "anim"
    if "/res/mipmap" in low:
        return "mipmap"
    if "/res/" in low:
        return "resource"
    if low.endswith((".gradle", ".gradle.kts")) or low.startswith("gradlew") or "/gradle/wrapper/" in low or low in ("gradle.properties", "gradle-wrapper.properties"):
        return "build"
    ext = Path(rel).suffix.lower()
    if ext in BINARY_EXTS:
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico") and "/res/" not in low and "/src/" in low:
            return "appasset"
        return "binary"
    if low.endswith((".gitignore", ".md", "license", "readme", ".txt", ".editorconfig")) or rel.startswith("."):
        return "metadata"
    return "other"


def scan_files(project: Path) -> List[Dict[str, Any]]:
    files = []
    for root, dirs, names in os.walk(project):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for name in names:
            full = Path(root) / name
            rel = full.relative_to(project).as_posix()
            cat = classify(rel)
            # content-based: a .kt/.java file that only declares package/imports
            # (no code) carries no migration surface -> "empty"
            if cat == "source":
                text = read_text(str(full))
                code_lines = [ln for ln in text.splitlines()
                              if ln.strip() and not ln.lstrip().startswith(("package ", "import ", "@file", "///", "//", "/*", "*", "*/"))]
                if not code_lines:
                    cat = "empty"
            files.append({
                "rel": rel,
                "abs": str(full),
                "category": cat,
            })
    return sorted(files, key=lambda f: f["rel"])


def read_text(abs_path: str) -> str:
    try:
        return Path(abs_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# page discovery
# ---------------------------------------------------------------------------

def _strip_symbol(name: str) -> str:
    name = name.strip()
    if name.startswith("."):
        name = name.split(".")[-1]
    elif "." in name:
        name = name.rsplit(".", 1)[-1]
    return name


def discover_pages(project: Path, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    pages: Dict[str, Dict[str, Any]] = {}
    start_symbol: Optional[str] = None

    # 1) AndroidManifest activities
    for f in files:
        if f["category"] == "manifest":
            text = read_text(f["abs"])
            for m in TAG_RE.finditer(text):
                tag, attrs_txt, _ = m.groups()
                if tag in ("activity-alias", "uses-sdk", "application", "provider",
                           "receiver", "service", "uses-permission", "uses-library",
                           "supports-screens", "intent-filter", "action", "category",
                           "meta-data", "queries", "tool", "permission", "instrumentation",
                           "uses-feature", "data", "grant-uri-permission", "package"):
                    continue
                if tag != "activity":
                    continue
                attrs = dict(XML_ATTR_RE.findall(attrs_txt))
                name = _strip_symbol(attrs.get("android:name", ""))
                if not name:
                    continue
                blk = text[m.end():m.end() + 400]
                is_launcher = "android.intent.action.MAIN" in blk and "android.intent.category.LAUNCHER" in blk
                p = pages.setdefault(name, {
                    "symbol": name, "kinds": [], "source_refs": [], "layout_names": [],
                    "is_start": False, "features": [],
                })
                if "Activity" not in p["kinds"]:
                    p["kinds"].append("Activity")
                if f["rel"] not in p["source_refs"]:
                    p["source_refs"].append(f["rel"])
                if is_launcher:
                    p["is_start"] = True
                    start_symbol = name

    # 2) navigation XML fragments/activities
    for f in files:
        if f["category"] != "navigation":
            continue
        text = read_text(f["abs"])
        for m in TAG_RE.finditer(text):
            tag, attrs_txt, _ = m.groups()
            if tag not in ("fragment", "activity", "navigation"):
                continue
            attrs = dict(XML_ATTR_RE.findall(attrs_txt))
            name = _strip_symbol(attrs.get("android:name", "") or attrs.get("app:startDestination", ""))
            if not name or name.startswith("@"):
                continue
            p = pages.setdefault(name, {
                "symbol": name, "kinds": [], "source_refs": [], "layout_names": [],
                "is_start": False, "features": [],
            })
            kind = "StartDestination" if attrs.get("app:startDestination") else ("Fragment" if tag == "fragment" else "Activity")
            if kind not in p["kinds"]:
                p["kinds"].append(kind)
            if f["rel"] not in p["source_refs"]:
                p["source_refs"].append(f["rel"])
            if attrs.get("app:startDestination"):
                p["is_start"] = True
                start_symbol = name

    # 3) source classes extending Fragment/Activity
    for f in files:
        if f["category"] not in ("source",):
            continue
        text = read_text(f["abs"])
        lines = text.splitlines()
        for ln in lines:
            cm = _ACT_CLASS_RE.match(ln)
            if not cm:
                continue
            cls, base = cm.group(1), cm.group(2)
            base_short = base.rsplit(".", 1)[-1]
            if base_short.endswith(FRAGMENT_BASES) or base_short.endswith(ACTIVITY_BASES):
                p = pages.setdefault(cls, {
                    "symbol": cls, "kinds": [], "source_refs": [], "layout_names": [],
                    "is_start": False, "features": [],
                })
                kind = "Fragment" if base_short.endswith(FRAGMENT_BASES) else "Activity"
                if kind not in p["kinds"]:
                    p["kinds"].append(kind)
                if f["rel"] not in p["source_refs"]:
                    p["source_refs"].append(f["rel"])
                # layouts inflated by this class
                for im in _INFLATE_RE.finditer(text):
                    lay = im.group(1)
                    if lay not in p["layout_names"]:
                        p["layout_names"].append(lay)

    # stable ids + page_id
    out = []
    for sym, p in pages.items():
        pid = page_id(sym)
        p["page_id"] = pid
        # link layouts that contain a fragment tag with matching name, else use inflated
        p["layout_names"] = sorted(set(p.get("layout_names", [])))
        out.append(p)
    out.sort(key=lambda p: p["symbol"])
    if start_symbol:
        for p in out:
            if p["symbol"] == start_symbol:
                p["is_start"] = True
    return out


# ---------------------------------------------------------------------------
# code symbols + business rules
# ---------------------------------------------------------------------------

def scan_code_symbols(files: List[Dict[str, Any]], pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    page_by_symbol = {p["symbol"]: p for p in pages}
    cands = []
    for f in files:
        if f["category"] not in ("source",):
            continue
        text = read_text(f["abs"])
        lines = text.splitlines()
        owner = page_by_symbol.get(Path(f["rel"]).stem)
        for i, line in enumerate(lines, start=1):
            m = _KOTLIN_LANG.match(line)
            if m:
                kind, sym = m.group(1), m.group(2)
                cands.append({
                    "source_ref": f"{f['rel']}:{i}", "file_path": f["rel"], "line": str(i),
                    "symbol": sym, "snippet": line.strip(),
                    "symbol_kind": "class" if "class" in kind else kind.strip(),
                    "page_symbol": owner["symbol"] if owner else "",
                    "page_id": owner["page_id"] if owner else "",
                })
                continue
            fm = _FUN_RE.search(line)
            if fm:
                cands.append({
                    "source_ref": f"{f['rel']}:{i}", "file_path": f["rel"], "line": str(i),
                    "symbol": fm.group(1), "snippet": line.strip(), "symbol_kind": "fun",
                    "page_symbol": owner["symbol"] if owner else "",
                    "page_id": owner["page_id"] if owner else "",
                })
                continue
            vm = _VAL_RE.match(line)
            if vm:
                cands.append({
                    "source_ref": f"{f['rel']}:{i}", "file_path": f["rel"], "line": str(i),
                    "symbol": vm.group(1), "snippet": line.strip(), "symbol_kind": "property",
                    "page_symbol": owner["symbol"] if owner else "",
                    "page_id": owner["page_id"] if owner else "",
                })
    return cands


def scan_layout_components(files: List[Dict[str, Any]], pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every view element of every layout becomes a visual candidate — 12类 fidelity pre-extract."""
    page_by_layout = {}
    for p in pages:
        for lay in p.get("layout_names", []):
            page_by_layout[lay] = p["page_id"]
    FIDELITY_KEYS = {"layout_width","layout_height","layout_margin","layout_marginTop","layout_marginBottom","layout_marginStart","layout_marginEnd",
                     "padding","paddingStart","paddingEnd","paddingTop","paddingBottom","background","src","tint","cardBackgroundColor","cardCornerRadius",
                     "textSize","textColor","textStyle","alpha","gravity","hint","inputType","entries","visibility","clickable","enabled","maxLines","maxLength",
                     "layout_weight","layout_gravity","layout_marginLeft","layout_marginRight",
                     "layout_alignParentEnd","layout_alignParentStart","layout_alignParentTop","layout_alignParentBottom",
                     "layout_alignEnd","layout_alignStart","layout_centerInParent","layout_toEndOf","layout_toStartOf",
                     "layout_below","layout_above","layout_centerVertical","layout_centerHorizontal","layout_alignBaseline",
                     "elevation","translationX","translationY","foreground","srcCompat","drawableTint","drawableStart","drawableEnd",
                     "cardElevation","strokeWidth","strokeColor","maxWidth","minWidth","minHeight","minHeight",
                     "contentPadding","backgroundTint","textInputType","drawableTop","drawableBottom"}
    comps = []
    for f in files:
        if f["category"] != "layout":
            continue
        text = read_text(f["abs"])
        layout_name = Path(f["rel"]).stem
        pid = page_by_layout.get(layout_name, "")
        for idx, m in enumerate(TAG_RE.finditer(text), start=1):
            tag, attrs_txt, selfclose = m.groups()
            attrs = dict(XML_ATTR_RE.findall(attrs_txt))
            if tag in ("?xml", "layout", "data", "variable", "import", "include", "merge", "androidx.databinding.DataBindingUtil"):
                continue
            rid = (attrs.get("android:id") or "").replace("@+id/", "").replace("@id/", "")
            text_attr = attrs.get("android:text", attrs.get("android:hint",""))
            # Extract only fidelity-relevant attrs + constraints
            fidelity = {k: v for k, v in attrs.items() if k.split(":")[-1] in FIDELITY_KEYS or "constraint" in k or "layout_constraint" in k or k.startswith("app:card")}
            # Also capture position_rules style (constraint + layout_gravity)
            position_rules = {k: v for k, v in attrs.items() if "constraint" in k or "gravity" in k or "margin" in k or "align" in k or "toStartOf" in k or "toEndOf" in k or ("below" in k or "above" in k)}
            cid = component_id(f"{f['rel']}#{idx}#{tag}#{rid}")
            comps.append({
                "layout": f["rel"], "component_id": cid, "type": tag,
                "resource_id": rid, "attributes": attrs, "fidelity_attrs": fidelity,
                "position_rules": position_rules, "text": text_attr, "page_id": pid,
                "source_ref": f"{f['rel']}:{_line_of(text, m.start())}",
                "doc_order": idx,
            })
    return comps


def scan_business_rules(files: List[Dict[str, Any]], pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    page_by_symbol = {p["symbol"]: p for p in pages}
    page_by_layout = {}
    for p in pages:
        for lay in p.get("layout_names", []):
            page_by_layout[lay] = p["page_id"]
    default_page = pages[0]["page_id"] if pages else ""
    rules: List[Dict[str, Any]] = []
    seen = set()

    def owner_page(rel: str, category: str) -> str:
        stem = Path(rel).stem
        if stem in page_by_symbol:
            return page_by_symbol[stem]["page_id"]
        if category == "layout":
            return page_by_layout.get(stem, default_page)
        return default_page

    def add(src_ref: str, page: str, cond: str, outcome: str, example: str) -> None:
        key = (src_ref, cond)
        if key in seen:
            return
        seen.add(key)
        rules.append({
            "source_ref": src_ref, "page_id": page, "condition": cond,
            "outcome_hint": outcome, "example_rule": example, "feature_hint": page,
        })

    for f in files:
        rel, cat = f["rel"], f["category"]
        text = read_text(f["abs"])
        page = owner_page(rel, cat)
        if cat == "source":
            # DAO @Query -> SQL rules
            for m in _QUERY_RE.finditer(text):
                sql = " ".join(m.group(1).split())
                sql2 = sql.lower()
                line = _line_of(text, m.start())
                if "like" in sql2:
                    add(f"{rel}:{line}", page, "SQL: " + sql,
                        "搜索/筛选: LIKE 子查询", "LIKE 查询 @%s" % rel)
                elif "case when" in sql2 or "order by" in sql2:
                    add(f"{rel}:{line}", page, "SQL: " + sql,
                        "排序/分级查询", "ORDER BY/CASE WHEN @%s" % rel)
                else:
                    add(f"{rel}:{line}", page, "SQL: " + sql,
                        "数据查询 @Query", "@Query @%s" % rel)
            # @Insert/@Update/@Delete
            for m in _DATA_OP_RE.finditer(text):
                op = m.group(1)
                if op in ("Insert", "Update", "Delete"):
                    add(f"{rel}:{_line_of(text, m.start())}", page, f"@{op}",
                        f"数据写入操作 @{op}", f"@{op} @%s" % rel)
            # if/when guards -> state/rule expressions
            for rx, label in ((_IF_RE, "if"), (_WHEN_RE, "when")):
                for m in rx.finditer(text):
                    expr = m.group(1).strip()[:100]
                    if expr and len(expr) < 100:
                        add(f"{rel}:{_line_of(text, m.start())}", page, expr,
                            f"{label} 分支条件", f"{label} 条件 @%s" % rel)
        elif cat == "layout":
            for m in _DATABINDING_RE.finditer(text):
                expr = m.group(0)[:100]
                add(f"{rel}:{_line_of(text, m.start())}", page, f"data-binding {expr}",
                    "XML 数据绑定", "DataBinding @%s" % rel)
            for m in _ONCLICK_RE.finditer(text):
                add(f"{rel}:{_line_of(text, m.start())}", page,
                    f"event:onClick on {m.group(1)}", "点击事件", "onClick @%s" % rel)
        elif cat == "menu":
            for m in TAG_RE.finditer(text):
                if m.group(1) == "item":
                    attrs = dict(XML_ATTR_RE.findall(m.group(2)))
                    rid = (attrs.get("android:id") or "").replace("@+id/", "").replace("@id/", "")
                    icon = attrs.get("android:icon", attrs.get("app:icon", ""))
                    show = attrs.get("app:showAsAction", attrs.get("android:showAsAction", "ifRoom"))
                    grade = MENU_GRADE.get(show, f"{show}?")
                    add(f"{rel}:{_line_of(text, m.start())}", page,
                        f"menu item {rid} title={attrs.get('android:title','')} icon={icon} showAsAction={show}",
                        f"菜单项({grade}; showAsAction={show})",
                        f"菜单项 {attrs.get('android:title','')} icon={icon} 常驻/溢出:{grade} @{rel}")
    return rules


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


# ---------------------------------------------------------------------------
# assets
# ---------------------------------------------------------------------------

def scan_assets(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    assets = []
    for f in files:
        cat = f["category"]
        if cat in ("layout", "drawable", "menu", "navigation", "values", "anim", "mipmap", "resource", "appasset", "manifest"):
            sha = hashlib.sha256(Path(f["abs"]).read_bytes()).hexdigest()[:8]
            assets.append({"source_path": f["rel"], "sha256": sha})
    return assets


# ---------------------------------------------------------------------------
# menus: full item detail (icon / showAsAction / level 主次)
# ---------------------------------------------------------------------------

MENU_GRADE = {"always": "常驻(always)", "ifRoom": "常驻(ifRoom)", "withText": "常驻(withText)",
              "never": "溢出(never)", "collapseActionView": "溢出(never)"}


def _menu_page(rel: str, pages: List[Dict[str, Any]]) -> str:
    default_page = pages[0]["page_id"] if pages else ""
    core = Path(rel).stem
    if core.startswith("menu_"):
        core = core[len("menu_"):]
    core = re.sub(r"\W+", "", core).lower().replace("fragment", "").replace("activity", "")
    if not core:
        return default_page
    for p in pages:
        sym = re.sub(r"\W+", "", p["symbol"]).lower().replace("fragment", "").replace("activity", "")
        if sym and (core in sym or sym in core):
            return p["page_id"]
    return default_page


def scan_menus(files: List[Dict[str, Any]], pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Menu items with id/title/icon/showAsAction/level(主次) — gap1/gap2/gap3."""
    items = []
    for f in files:
        if f["category"] != "menu":
            continue
        text = read_text(f["abs"])
        rel = f["rel"]
        pid = _menu_page(rel, pages)
        # group spans (level-2 items)
        group_spans = []
        for g in TAG_RE.finditer(text):
            if g.group(1) == "group":
                start = g.start()
                end = text.find(">", g.end())
                if end < 0:
                    end = start + 60
                group_spans.append((start, end))
        # root spans for level detection (default level 1)
        root_spans = []
        for g in TAG_RE.finditer(text):
            if g.group(1) == "menu":
                root_spans.append((g.start(), g.end()))
        for m in TAG_RE.finditer(text):
            tag, attrs_txt, _ = m.groups()
            if tag != "item":
                continue
            attrs = dict(XML_ATTR_RE.findall(attrs_txt))
            rid = (attrs.get("android:id") or "").replace("@+id/", "").replace("@id/", "")
            show = attrs.get("app:showAsAction", attrs.get("android:showAsAction", "ifRoom"))
            level = 1
            for gs, ge in group_spans:
                if gs < m.start() < ge:
                    level = 2
                    break
            items.append({
                "file": rel, "page_id": pid, "item_id": rid,
                "title": attrs.get("android:title", ""),
                "icon": attrs.get("android:icon", attrs.get("app:icon", "")),
                "showAsAction": show, "grade": MENU_GRADE.get(show, f"{show}?"),
                "order": attrs.get("android:orderInCategory", ""),
                "checkable": attrs.get("android:checkable", "") != "",
                "grouped": bool(attrs.get("android:groupId", "")) or level == 2,
                "level": level,
                "source_ref": f"{rel}:{_line_of(text, m.start())}",
            })
    return items


# ---------------------------------------------------------------------------
# Compose scanning: screens / fields / icons / spacing / dividers
# ---------------------------------------------------------------------------

COMPOSE_UI_CALLS = (
    "Icon", "TextField", "OutlinedTextField", "BasicTextField", "Spacer",
    "HorizontalDivider", "VerticalDivider", "Divider", "Card", "ElevatedCard",
    "Switch", "Checkbox", "RadioButton", "Button", "OutlinedButton", "TextButton",
    "IconButton", "FloatingActionButton", "Scaffold", "TopAppBar", "BottomAppBar",
    "NavigationBar", "NavigationBarItem", "Slider", "DropdownMenu", "DropdownMenuItem",
    "AlertDialog", "ModalBottomSheet", "LazyColumn", "LazyRow", "Row", "Column", "Box",
    "Text", "Image", "AsyncImage", "OutlinedTextFieldValue", "TextArea", "TextFieldValue",
    "DatePicker", "TimePicker", "SegmentedButton", "SingleChoiceSegmentedButtonRow",
    "dataTable", "icons", "Chip", "FilterChip", "AssistChip", "TabRow", "Tab",
)

_COMPOSE_SCREEN_RE = re.compile(r"(?:@Composable[^\n]*\n\s*)?(?:@\w+\s*)*\bfun\s+([A-Za-z0-9_]+)\s*\(")
_COMPOSE_SCREEN_SUFFIX = ("Screen", "Page", "Dialog", "Route", "Wallpaper",
                           "Sheet", "BottomSheet", "Popup", "Picker", "Content", "Settings")
_COMPOSE_SCREEN_SKIP_PREFIX = ("Progress", "Demo", "AppIcon", "LanguageSelector")
_COMPOSE_FIELD_RE = re.compile(r"\b([A-Z]\w*(?:TextField|TextArea|TextInput|ConfigField|ConfigRow|FormRow|InputField|PickerField|ValueField|Editable|ItemRow|RowAdd|EditRow))\s*\(")
_COMPOSE_SPACER_RE = re.compile(r"\b([A-Z]\w*(?:Gap|Spacer))\s*\(")
_COMPOSE_DIVIDER_RE = re.compile(r"\b([A-Z]\w*Divider)\s*\(")
_COMPOSE_ICON_RE = re.compile(r"\b([A-Z]\w*Icon)\s*\(")
_COMPOSE_CARD_RE = re.compile(r"\b([A-Z]\w*(?:Card))\s*\(")
_LABEL_QRE = re.compile(r"(?:decorateText|title|label|placeholder|text|hint|name)\s*=\s*(?:stringResource\(R\.string\.([A-Za-z0-9_]+)\)|(@string/[A-Za-z0-9_]+)|[\"']([^\"']{1,60})[\"'])")
_ICON_RES_RE = re.compile(r"R\.drawable\.([A-Za-z0-9_]+)|R\.mipmap\.([A-Za-z0-9_]+)")
_ICON_IMPORT_RE = re.compile(r"(?:Icons|Materials)\s*\.\s*[A-Za-z]+\.\s*([A-Za-z0-9_]+)")
_SPACER_DP_RE = re.compile(r"(?:height|width|size|horizontal|vertical)\s*=\s*([\d.]+)\s*\.dp")
_ELEVATION_DP_RE = re.compile(r"(?:elevation|cardElevation)\s*=\s*([\d.]+)\s*\.dp")


def _brace_match(text: str, open_pos: int, open_ch: str, close_ch: str) -> int:
    depth = 0
    in_str: Optional[str] = None
    i = open_pos
    n = len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        else:
            if ch in ('"', "'"):
                in_str = ch
            elif ch == "/" and i + 1 < n and text[i + 1] == "/":
                while i < n and text[i] != "\n":
                    i += 1
                continue
            elif ch == "/" and i + 1 < n and text[i + 1] == "*":
                j = text.find("*/", i + 2)
                i = j + 2 if j > 0 else n
                continue
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return i
        i += 1
    return -1


def _compose_screens(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    screens = []
    for f in files:
        if f["category"] != "source":
            continue
        # Restrict page discovery to app module; skip generic ui-library generic components
        if not f["rel"].startswith("app/src/main/"):
            continue
        text = read_text(f["abs"])
        for m in _COMPOSE_SCREEN_RE.finditer(text):
            sym = m.group(1)
            if sym.lower().startswith("class") or not sym[0].isupper():
                continue
            if not any(sym.lower().endswith(s.lower()) for s in _COMPOSE_SCREEN_SUFFIX):
                continue
            if any(sym.lower().startswith(p.lower()) for p in _COMPOSE_SCREEN_SKIP_PREFIX):
                continue
            # Exclude trivial single-word "Content" slot (too generic, not a page)
            if sym == "Content":
                continue
            # Exclude ultra-generic names that are not pages even though they match suffix
            if sym in ("CheckmarkContent", "CustomMenuContent", "DeveloperContent"):
                continue
            # exclude widget-like names (compound widgets loosely matching View)
            if sym.lower().endswith("view") and sym.lower() not in ("mainview", "rootview", "screenview"):
                continue
            screens.append({"symbol": sym, "file": f["rel"],
                            "source_ref": f"{f['rel']}:{_line_of(text, m.start())}"})
    return screens


def scan_compose(files: List[Dict[str, Any]], pages: List[Dict[str, Any]],
                 screens: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Compose UI nodes -> components: fields(order/label/icon), icons, spacers, dividers."""
    page_by_symbol = {p["symbol"]: p for p in pages}
    nodes: List[Dict[str, Any]] = []
    for scr in screens:
        f_rel = scr["file"]
        f = next((x for x in files if x["rel"] == f_rel), None)
        if not f:
            continue
        text = read_text(f["abs"])
        sym = scr["symbol"]
        page = page_by_symbol.get(sym)
        pid = page["page_id"] if page else ""
        # function body: from fun start to matching brace
        fpos = text.find("fun " + sym)
        if fpos < 0:
            continue
        paren = text.find("(", fpos)
        brace = text.find("{", paren) if paren >= 0 else -1
        if brace < 0:
            continue
        body_end = _brace_match(text, brace, "{", "}")
        if body_end < 0:
            body_end = len(text)
        body = text[brace:body_end]
        base_line = _line_of(text, 0) + text.count("\n", 0, brace)

        # walk ordered UI calls (all-of: known + unknown canvas)
        calls = {}
        for m in _COMPOSE_FIELD_RE.finditer(body):
            calls[m.start()] = ("field", m.group(1))
        for m in _COMPOSE_SPACER_RE.finditer(body):
            calls[m.start()] = ("spacing", m.group(1))
        for m in _COMPOSE_DIVIDER_RE.finditer(body):
            calls[m.start()] = ("divider", m.group(1))
        for m in _COMPOSE_ICON_RE.finditer(body):
            calls[m.start()] = ("icon", m.group(1))
        for m in _COMPOSE_CARD_RE.finditer(body):
            calls[m.start()] = ("card", m.group(1))
        for m in _COMPOSE_FUN_OTHER_RE.finditer(body):
            calls.setdefault(m.start(), ("other", m.group(1)))
        # canvas: capture every remaining uppercase UI call (self-built components too)
        for m in _COMPOSE_ANY_CALL_RE.finditer(body):
            cname = m.group(1)
            if any(cname.startswith(p) for p in _UI_SKIP_PREFIXES):
                continue
            if cname in ("Color", "Modifier", "Intent", "Title", "Text", "Icon", "Brush"):
                continue
            if m.start() not in calls:
                calls[m.start()] = ("canvas_" + _ui_call_kind(cname), cname)
        call_items = sorted(calls.items())
        seen_pos = set()
        order = 0
        pending_icon = ""
        for pos, (kind, name) in call_items:
            key = (pos, kind)
            if key in seen_pos:
                continue
            seen_pos.add(key)
            order += 1
            chunk = body[pos:pos + 900]

            def _icon_of(text_chunk: str) -> str:
                im = _ICON_RES_RE.search(text_chunk)
                if im:
                    return im.group(1) or im.group(2)
                im2 = _ICON_IMPORT_RE.search(text_chunk)
                return im2.group(1) if im2 else ""

            if kind == "icon":
                icon = _icon_of(chunk) or _icon_of(body[max(0, pos - 200):pos])
                if icon:
                    pending_icon = icon
                nodes.append({
                    "layout": f_rel, "component_id": component_id(f"{f_rel}#{sym}#icon#{icon}"),
                    "type": f"compose:{name}", "resource_id": "",
                    "attributes": {"icon": icon}, "text": "",
                    "page_id": pid, "source_ref": f"{f_rel}:{base_line + _line_of(body, pos) - 1}",
                    "order": order, "_chunk": chunk, "kind": "icon",
                })
                continue
            if kind == "spacing":
                mm = _SPACER_DP_RE.search(body[pos:pos + 200])
                nodes.append({
                    "layout": f_rel, "component_id": component_id(f"{f_rel}#{sym}#spacer#{pos}"),
                    "type": f"compose:{name}", "resource_id": "",
                    "attributes": {"spacing": (mm.group(1) + "dp") if mm else "default"}, "text": "",
                    "page_id": pid, "source_ref": f"{f_rel}:{base_line + _line_of(body, pos) - 1}",
                    "order": order, "_chunk": chunk, "kind": "spacing",
                })
                continue
            if kind == "divider":
                nodes.append({
                    "layout": f_rel, "component_id": component_id(f"{f_rel}#{sym}#divider#{pos}"),
                    "type": f"compose:{name}", "resource_id": "",
                    "attributes": {"divider": "1dp"}, "text": "",
                    "page_id": pid, "source_ref": f"{f_rel}:{base_line + _line_of(body, pos) - 1}",
                    "order": order, "_chunk": chunk, "kind": "divider",
                })
                continue
            if kind == "card":
                mm = _ELEVATION_DP_RE.search(body[pos:pos + 200])
                nodes.append({
                    "layout": f_rel, "component_id": component_id(f"{f_rel}#{sym}#card#{pos}"),
                    "type": f"compose:{name}", "resource_id": "",
                    "attributes": {"elevation": (mm.group(1) + "dp") if mm else "", "cardElevation": (mm.group(1) + "dp") if mm else ""},
                    "text": "",
                    "page_id": pid, "source_ref": f"{f_rel}:{base_line + _line_of(body, pos) - 1}",
                    "order": order, "_chunk": chunk, "kind": "card",
                })
                continue
            if kind == "field" or kind.startswith("canvas_"):
                if name in _UI_CANVAS_SKIP:
                    continue
                lm = _LABEL_QRE.search(chunk)
                label = ""
                if lm:
                    label = lm.group(1) or lm.group(2) or lm.group(3) or ""
                if not label:
                    # trailing-lambda label: Text(stringResource(R.string.x)) in the call block
                    tm = re.search(r'Text\(\s*(?:stringResource\s*\(R\.string\.([A-Za-z0-9_]+)\)|"([^"]{1,60})")', chunk)
                    if tm:
                        label = tm.group(1) or tm.group(2) or ""
                    else:
                        rm = re.search(r"stringResource\s*\(R\.string\.([A-Za-z0-9_]+)\)", chunk)
                        if rm:
                            label = rm.group(1)
                # icon may be a param of the SAME call (gap5: field↔icon 1:1)
                icon = _icon_of(chunk) or pending_icon
                pending_icon = ""
                nodes.append({
                    "layout": f_rel, "component_id": component_id(f"{f_rel}#{sym}#field#{pos}"),
                    "type": f"compose:{name}", "resource_id": "", "attributes": {
                        "label": label, "icon": icon,
                        "inputType": "text" if "password" in chunk.lower() else "textPersonName",
                    }, "text": label, "page_id": pid,
                    "source_ref": f"{f_rel}:{base_line + _line_of(body, pos) - 1}",
                    "order": order, "kind": ("field" if kind == "field" else "canvas_field"),
                    "canvas_kind": _ui_call_kind(name) if kind.startswith("canvas_") else "",
                })
                continue
            nodes.append({
                "layout": f_rel, "component_id": component_id(f"{f_rel}#{sym}#other#{pos}"),
                "type": f"compose:{name}" if name else "compose:UI", "resource_id": "", "attributes": {}, "text": "",
                "page_id": pid, "source_ref": f"{f_rel}:{base_line + _line_of(body, pos) - 1}",
                "order": order, "_chunk": chunk, "kind": "other",
            })
    return nodes


# ---------------------------------------------------------------------------
# Preference XML (res/xml/preferences_*.xml): option tree + sub-options + linked fragment
# ---------------------------------------------------------------------------

PREF_CATEGORY_TAGS = ("PreferenceCategory", "PreferenceScreen", "PreferenceGroup")
PREF_ENDPOINT_TAGS = (
    "Preference", "SwitchPreferenceCompat", "SwitchPreference", "ListPreference", "Dropdown",
    "SeekBarPreference", "TimePreference", "DatePreference", "EditTextPreference",
    "CheckBoxPreference", "RadioPreference", "IconPreference", "PasswordPreference",
)


def _pref_attr(attrs: Dict[str, str], key: str) -> str:
    return attrs.get(key, attrs.get("android:" + key, ""))


def scan_preference_xml(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Parse res/xml/preferences*.xml into option-tree rows (leveled) + sub-options."""
    rows = []
    for f in files:
        if f["category"] not in ("resource", "values"):
            continue
        rel = f["rel"]
        if not Path(rel).name.startswith("preferences"):
            continue
        text = read_text(f["abs"])
        if "<PreferenceScreen" not in text and "<PreferenceCategory" not in text:
            continue
        # stack-based tree walk (open tags push, close tags pop)
        stack: List[Dict[str, Any]] = []
        pref_open_re = re.compile(r"<(PreferenceScreen|PreferenceCategory|PreferenceGroup)([^>]*?)(/?)>")
        pref_all_re = re.compile(r"<(/?)(PreferenceScreen|PreferenceCategory|PreferenceGroup|Preference|SwitchPreferenceCompat|SwitchPreference|ListPreference|Dropdown|SeekBarPreference|TimePreference|DatePreference|EditTextPreference|CheckBoxPreference|RadioPreference|IconPreference|PasswordPreference)([^>]*?)(/?)>")
        for m in pref_all_re.finditer(text):
            closing, tag, attrs_txt, selfclose = m.groups()
            if closing:
                while stack:
                    popped = stack.pop()
                    if popped["tag"] == tag:
                        break
                continue
            attrs = dict(XML_ATTR_RE.findall(attrs_txt))
            title = _pref_attr(attrs, "title")
            key = _pref_attr(attrs, "key")
            entry = _pref_attr(attrs, "entries")
            entry_val = _pref_attr(attrs, "entryValues")
            summary = _pref_attr(attrs, "summary")
            icon = _pref_attr(attrs, "icon")
            default_val = _pref_attr(attrs, "defaultValue")
            fragment = _pref_attr(attrs, "fragment")
            intent = _pref_attr(attrs, "action")
            selectable = _pref_attr(attrs, "selectable")
            dependency = _pref_attr(attrs, "dependency")
            root = stack[-1] if stack else {}
            node = {
                "file": rel, "level": len(stack), "tag": tag,
                "title": title, "key": key, "summary": summary, "icon": icon,
                "entries": entry, "entryValues": entry_val, "defaultValue": default_val,
                "fragment": fragment, "intentAction": intent,
                "selectable": selectable, "dependency": dependency,
                "parent": root.get("key", "") if root else "",
                "source_ref": f"{rel}:{_line_of(text, m.start())}",
            }
            rows.append(node)
            if tag in ("PreferenceScreen", "PreferenceCategory", "PreferenceGroup") and not selfclose:
                stack.append(node)
    return rows


def scan_string_arrays(files: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    """values/arrays.xml + strings.xml: string-array / array -> items."""
    arrays: Dict[str, List[str]] = {}
    for f in files:
        if f["category"] not in ("values",):
            continue
        text = read_text(f["abs"])
        for m in TAG_RE.finditer(text):
            tag, attrs_txt, selfclose = m.groups()
            if tag not in ("string-array", "array"):
                continue
            attrs = dict(XML_ATTR_RE.findall(attrs_txt))
            name = attrs.get("name", "")
            if not name:
                continue
            end = text.find("</" + tag + ">", m.end())
            block = text[m.end():end] if end > 0 else ""
            items = re.findall(r"<item[^>]*>([^<]*)</item>", block)
            arrays[name] = [it.strip() for it in items]
    return arrays


# ---------------------------------------------------------------------------
# navigation relations: 跳转 (click→destination) + 返回 (back)
# ---------------------------------------------------------------------------

_NATIVE_NAV_RE = re.compile(r"findNavController\(\)?\.navigate\s*\(\s*([^)]{1,150})\)")
_INTENT_START_RE = re.compile(r"(?:startActivity(?:ForResult)?|\.launch|launcher\.launch)\s*\(\s*Intent\([^,]{0,100}?,\s*([A-Za-z0-9_.$]+::class)")
_BACK_RE = re.compile(r"\b(onBackPressedDispatcher\.onBackPressed|onBackPressed\(\)|popBackStack(?:Immediate)?\(|finish\(\)|backStack\.removeLastOrNull|PlatformBackHandler|BackHandler\(|onSupportNavigateUp\(\))\b")
_MENU_ITEM_WHEN_RE = re.compile(r"R\.id\.([A-Za-z0-9_]+)\s*->\s*\{(.*?)\}\s*(?:true|false|continue|break)", re.S)
_MENU_CLICK_RE = re.compile(r"R\.id\.([A-Za-z0-9_]+)\s*(?:\}|\s*\)\s*(?:\{|\->))")
_SET_CLICK_RE = re.compile(r"\.setOnPreferenceClickListener\s*\{")
# Nav3/回调跳转（缺口4）：onXxxClick = { backStack.add(Dest) } / navigate / switch route
_NAV3_CB_RE = re.compile(
    r"(?:on[A-Z]\w*(?:Click|Tap|Select)|onClick|onNavigate)\s*=\s*\{[^}]{0,120}?"
    r"(?:backStack\.(?:add|push|record)\s*\(\s*([A-Za-z0-9_]+)|navigate\s*\(\s*\"?([A-Za-z0-9_.]+)\"?)\)")
_NAV3_ROUTE_RE = re.compile(r"\b(currentRoute|route)\s*(?:==|===)\s*\"?([A-Za-z0-9_.-]+)\"?")
_NAV3_ENTRY_RE = re.compile(r"entry\s*<\s*([A-Za-z0-9_]+)\s*>\s*\{")


def _stem_owner(rel: str, pages: List[Dict[str, Any]]) -> str:
    stem = Path(rel).stem
    page_by_symbol = {p["symbol"]: p for p in pages}
    if stem in page_by_symbol:
        return page_by_symbol[stem]["page_id"]
    return pages[0]["page_id"] if pages else ""


_WHEN_HEAD_RE = re.compile(r"\bwhen\s*\(\s*(\w+(?:\.\w+)*)\s*\)\s*\{")
_WHEN_OPT_RE = re.compile(r"([A-Za-z0-9_.@]+)\s*->\s*([A-Za-z0-9_()]*)\s*(?:[,}\n])")
_WHEN_MENU_OPT_RE = re.compile(r"R\.id\.([A-Za-z0-9_]+)\s*->\s*\{")
_MENU_BRANCH_RE = re.compile(r"([A-Za-z0-9_.@]+)\s*->\s*\{")


def scan_when_branches(files: List[Dict[str, Any]]) -> Tuple[Dict[str, List[str]], Dict[str, str], List[Dict[str, Any]]]:
    """when(destination) branches -> option map {arg: [options]}
    + menu when-branches -> rows {menu_id, target_block} (子选项/跳转来源).
    Brace-aware: walks from 'when(' and matches the whole when block."""
    out: Dict[str, List[str]] = {}
    sources: Dict[str, str] = {}
    menu_rows: List[Dict[str, Any]] = []
    for f in files:
        if f["category"] not in ("source",):
            continue
        text = read_text(f["abs"])
        for m in _WHEN_HEAD_RE.finditer(text):
            argname = m.group(1).split(".")[-1]
            brace_start = m.end() - 1  # '{'
            brace_end = _brace_match(text, brace_start, "{", "}")
            if brace_end < 0:
                continue
            body = text[brace_start:brace_end]
            branches = _WHEN_OPT_RE.findall(body)
            if len(branches) >= 2:
                out[argname] = [b[0] for b in branches]
                source = f"{f['rel']}:{_line_of(text, m.start())}"
                if argname not in sources:
                    sources[argname] = source
                elif sources[argname].split(":", 1)[0] != f["rel"]:
                    sources[argname] = ""
            for mm in _WHEN_MENU_OPT_RE.finditer(body):
                b_start = mm.end() - 1
                b_end = _brace_match(body, b_start, "{", "}")
                if b_end < 0:
                    continue
                menu_rows.append({
                    "menu_id": mm.group(1), "block": body[b_start:b_end][:600],
                    "file": f["rel"], "line": _line_of(text, m.start()) + body[:mm.start()].count("\n"),
                })
    return out, sources, menu_rows


def scan_nav_relations(files: List[Dict[str, Any]],
                       pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """click-destination (navController/startActivity/preference-click/menu-item)
    + back relations (pop/finish/backstack/BackHandler)."""
    rels: List[Dict[str, Any]] = []
    seen = set()

    def add(rel: str, page_id: str, trigger: str, action: str, to_page: str,
            rtype: str, source_ref: str) -> None:
        key = (source_ref, trigger, to_page, rtype)
        if key in seen:
            return
        seen.add(key)
        rels.append({
            "page_id": page_id, "trigger": trigger, "action": action,
            "to_page_id": to_page, "relation_type": rtype, "source_ref": source_ref,
            "file": rel,
        })

    def back_type(op: str) -> str:
        op_l = op.lower()
        if "finish" in op_l or "onbackpressed" in op_l:
            return "BACK"
        if "popbackstack" in op_l or "removelastornull" in op_l:
            return "POP"
        return "BACK_HANDLER"

    # nav XML: id -> target symbol (for navigate(R.id.x) resolution)
    id_to_symbol: Dict[str, str] = {}
    for f in files:
        if f["category"] != "navigation":
            continue
        text = read_text(f["abs"])
        for m in TAG_RE.finditer(text):
            tag, attrs_txt, _ = m.groups()
            if tag not in ("fragment", "activity"):
                continue
            attrs = dict(XML_ATTR_RE.findall(attrs_txt))
            key = (attrs.get("android:id") or attrs.get("app:id") or "").replace("@+id/", "").replace("@id/", "")
            sym = _strip_symbol(attrs.get("android:name", ""))
            if key and sym:
                id_to_symbol[key] = sym

    for f in files:
        rel, cat = f["rel"], f["category"]
        if cat != "source":
            continue
        text = read_text(f["abs"])
        page = _stem_owner(rel, pages)

        for m in _NATIVE_NAV_RE.finditer(text):
            arg = m.group(1).strip()
            line = _line_of(text, m.start())
            did = re.search(r"R\.id\.([A-Za-z0-9_]+)", arg)
            route = re.search(r"(?:Route\.|Routes\.)?([A-Za-z0-9_]+)", arg)
            target = ""
            if did and did.group(1) in id_to_symbol:
                target = id_to_symbol[did.group(1)]
            elif route:
                target = route.group(1)
            add(rel, page, "navController.navigate", arg[:80], target, "NAVIGATE", f"{rel}:{line}")

        for m in _INTENT_START_RE.finditer(text):
            cls = m.group(1).replace("::class", "").rsplit(".", 1)[-1]
            line = _line_of(text, m.start())
            add(rel, page, "startActivity", cls[:60], cls, "INTENT", f"{rel}:{line}")

        for m in _SET_CLICK_RE.finditer(text):
            line = _line_of(text, m.start())
            snippet = text[m.end():m.end() + 400]
            cls = re.search(r"([A-Za-z0-9_.]+::class)", snippet)
            if cls:
                c = cls.group(1).replace("::class", "").rsplit(".", 1)[-1]
                add(rel, page, "onPreferenceClick", c[:60], c, "NAVIGATE", f"{rel}:{line}")
            else:
                launch = re.search(r"launcher\.launch|newThemePickerDialog|FilterSelection", snippet)
                if launch:
                    add(rel, page, "onPreferenceClick", launch.group(0)[:50], "",
                        "OPEN", f"{rel}:{line}")

        for m in _MENU_CLICK_RE.finditer(text):
            mid = m.group(1)
            line = _line_of(text, m.start())
            block = text[m.end():m.end() + 400]
            im = re.search(r"([A-Za-z0-9_.]+::class)", block)
            if im:
                cls = im.group(1).replace("::class", "").rsplit(".", 1)[-1]
                add(rel, page, f"menu_item[{mid}]", "startActivity:" + cls[:50], cls,
                    "MENU_ITEM", f"{rel}:{line}")
                continue
            if "launch(Intent" in block or "launch(" in block:
                add(rel, page, f"menu_item[{mid}]", "launcher", "", "MENU_LAUNCHER", f"{rel}:{line}")

        for m in _MENU_ITEM_WHEN_RE.finditer(text):
            mid = m.group(1)
            line = _line_of(text, m.start())
            block = m.group(2)[:600]
            im = re.search(r"([A-Za-z0-9_.]+::class)", block)
            if im:
                cls = im.group(1).replace("::class", "").rsplit(".", 1)[-1]
                add(rel, page, f"menu_item[{mid}]", "launch:" + cls[:50], cls,
                    "MENU_ITEM", f"{rel}:{line}")
                continue
            nav = re.search(r"(?:navigate|backStack\.add|findNavController\(\)\.navigate)\s*\(", block)
            if nav:
                add(rel, page, f"menu_item[{mid}]", nav.group(0)[:60], "", "MENU_NAV", f"{rel}:{line}")
            elif "PopupMenu" in block or "showMenu" in block or "MenuState" in block:
                add(rel, page, f"menu_item[{mid}]", "openMenu", "", "MENU_OPEN", f"{rel}:{line}")
            if "launch(Intent" in block or "launch(" in block:
                add(rel, page, f"menu_item[{mid}]", "launcher", "", "MENU_LAUNCHER", f"{rel}:{line}")

        # Nav3/回调跳转（缺口4）
        for m in _NAV3_CB_RE.finditer(text):
            dest = m.group(1) or m.group(2) or ""
            line = _line_of(text, m.start())
            add(rel, page, "onClick(backStack.add/navigate)", dest[:60], dest,
                "NAV3_CB", f"{rel}:{line}")
        for m in _NAV3_ROUTE_RE.finditer(text):
            route = m.group(2) or ""
            line = _line_of(text, m.start())
            add(rel, page, "route switch", route[:60], route,
                "NAV3_ROUTE", f"{rel}:{line}")
        for m in _NAV3_ENTRY_RE.finditer(text):
            dest = m.group(1)
            line = _line_of(text, m.start())
            add(rel, page, "entry< >", dest[:60], dest,
                "NAV3_ENTRY", f"{rel}:{line}")

        for m in _BACK_RE.finditer(text):
            op = m.group(1)
            line = _line_of(text, m.start())
            add(rel, page, op[:40], "", "", back_type(op), f"{rel}:{line}")

    return rels


_CATALOG_HEADER_RE = re.compile(r"^\[(\w+)\]")
_CATALOG_LINE_RE = re.compile(r"^([\w.-]+)\s*=\s*(.+)$")
_CATALOG_MODULE_RE = re.compile(r"module\s*=\s*\"([^\"]+)\"")
_CATALOG_VERSION_REF_RE = re.compile(r"version\.ref\s*=\s*\"([^\"]+)\"")
_CATALOG_GROUP_RE = re.compile(r"group\s*=\s*\"([^\"]+)\"")
_CATALOG_NAME_RE = re.compile(r"name\s*=\s*\"([^\"]+)\"")
_ALIAS_ACCESS_RE = re.compile(r"libs\.([A-Za-z0-9_.]+)")


def scan_version_catalog(files: List[Dict[str, Any]]) -> Dict[str, Dict[str, str]]:
    """libs.versions.toml -> {alias(parts): {group, artifact, version}}."""
    catalog: Dict[str, Dict[str, str]] = {}
    for f in files:
        if Path(f["rel"]).name != "libs.versions.toml":
            continue
        text = read_text(f["abs"])
        section = ""
        versions: Dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            mh = _CATALOG_HEADER_RE.match(line)
            if mh:
                section = mh.group(1)
                continue
            if section == "versions":
                mv = _CATALOG_LINE_RE.match(line)
                if mv:
                    versions[mv.group(1)] = mv.group(2).strip().strip('"').split("//")[0].strip()
            elif section == "libraries":
                mv = _CATALOG_LINE_RE.match(line)
                if not mv:
                    continue
                alias = mv.group(1)
                value = mv.group(2).strip().strip('"')
                group, artifact, version = "", "", ""
                if "{" in value:
                    mm = _CATALOG_MODULE_RE.search(value)
                    mg = _CATALOG_GROUP_RE.search(value)
                    mn = _CATALOG_NAME_RE.search(value)
                    vr = _CATALOG_VERSION_REF_RE.search(value)
                    if mm:
                        parts = mm.group(1).split(":")
                        group, artifact = parts[0], parts[1]
                    elif mg and mn:
                        group, artifact = mg.group(1), mn.group(1)
                    if vr:
                        version = versions.get(vr.group(1), "")
                else:
                    parts = value.split(":")
                    if len(parts) >= 2:
                        group, artifact = parts[0], parts[1]
                        version = parts[2] if len(parts) > 2 else versions.get(alias, "")
                catalog[alias] = {"group": group, "artifact": artifact, "version": version}
    return catalog


_GRADLE_SCOPE_RE = re.compile(
    r"^\s*(implementation|api|kapt|kaptTest|kaptAndroidTest|compileOnly|runtimeOnly|"
    r"annotationProcessor|testImplementation|testApi|debugImplementation|debugRuntimeOnly|"
    r"releaseImplementation|androidTestImplementation|classpath|dependencies)"
)
_GRADLE_RAW_RE = re.compile(r"[\"']([^\"']+)[\"']")
_KTS_CONFIG_RE = re.compile(r"^\s*(compileSdk|minSdk|targetSdk|versionCode|versionName|applicationId)\s*[=:]?\s*[\"']?([^\"'\s]+)")


def scan_gradle_deps(files: List[Dict[str, Any]],
                     catalog: Dict[str, Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Dependencies (explicit + version-catalog aliases, resolved) + SDK config."""
    catalog = catalog or {}
    rules = []
    seen = set()

    def add_dep(rel: str, line_no: int, scope: str, group: str, artifact: str,
                version: str, alias: str = "") -> None:
        key = (rel, group, artifact, version)
        if (group, artifact) in seen:
            return
        seen.add(key)
        ver = version or "catalog@%s" % alias if alias else version
        rules.append({
            "source_ref": f"{rel}:{line_no}", "page_id": "",
            "condition": f"dependency {group}:{artifact}:{version or '(见catalog)'}",
            "outcome_hint": f"第三方依赖({scope})",
            "example_rule": f"Gradle 依赖 {group}:{artifact}@{version or '?'} [{scope}] @{rel}:{line_no}"
                            + (f" (alias libs.{alias})" if alias else ""),
            "resolution": "ALIAS" if alias else "DIRECT",
            "scope": scope, "group": group, "artifact": artifact, "version": version,
        })

    for f in files:
        if f["category"] != "build" or not f["rel"].endswith((".gradle", ".gradle.kts")):
            continue
        text = read_text(f["abs"])
        for i, line in enumerate(text.splitlines(), start=1):
            sm = _GRADLE_SCOPE_RE.match(line)
            if not sm:
                cm = _KTS_CONFIG_RE.match(line.strip())
                if cm:
                    key = (f["rel"], line.strip())
                    if key in seen:
                        continue
                    seen.add(key)
                    rules.append({
                        "source_ref": f"{f['rel']}:{i}", "page_id": "",
                        "condition": line.strip(), "outcome_hint": "构建/SDK 配置",
                        "example_rule": f"构建配置 {line.strip()} @{f['rel']}",
                        "resolution": "CONFIG", "scope": "", "group": "", "artifact": "", "version": "",
                    })
                continue
            scope = sm.group(1)
            rest = line[sm.end():]
            # alias form: implementation(libs.androidx.room.ktx)
            am = _ALIAS_ACCESS_RE.search(rest)
            if am:
                parts = am.group(1).split(".")
                alias = "-".join(p for p in parts if p)
                cb = catalog.get(alias) or catalog.get("-".join(parts))
                if cb:
                    add_dep(f["rel"], i, scope, cb["group"], cb["artifact"], cb["version"], alias)
                else:
                    rules.append({
                        "source_ref": f"{f['rel']}:{i}", "page_id": "",
                        "condition": f"dependency alias libs.{'.'.join(parts)} (catalog中未定义)",
                        "outcome_hint": f"第三方依赖({scope})|别名未在libs.versions.toml解析",
                        "example_rule": f"Gradle alias libs.{'.'.join(parts)} [{scope}] @{f['rel']}:{i}",
                        "resolution": "ALIAS_UNRESOLVED", "scope": scope,
                        "group": "", "artifact": "", "version": "",
                    })
                continue
            # raw form: implementation("g:a:v") / 'g:a:v'
            rm = _GRADLE_RAW_RE.search(rest)
            if rm:
                dep = rm.group(1).strip()
                if dep.endswith("$") or dep.startswith("$") or dep.startswith(".") or dep.endswith(")"):
                    continue
                parts = dep.split(":")
                if len(parts) >= 2:
                    add_dep(f["rel"], i, scope, parts[0], parts[1],
                            parts[2].split("@")[0] if len(parts) > 2 else "")
    # catalog entries never referenced in build files -> still candidates
    for alias, cb in catalog.items():
        if not cb["group"]:
            continue
        key = ("catalog", cb["group"], cb["artifact"])
        if key[1:] in seen:
            continue
        seen.add(key)
        rules.append({
            "source_ref": "gradle/libs.versions.toml", "page_id": "",
            "condition": f"dependency {cb['group']}:{cb['artifact']}:{cb['version']}",
            "outcome_hint": "第三方依赖(catalog 定义，未见使用)",
            "example_rule": f"版本目录库 {cb['group']}:{cb['artifact']}@{cb['version']} (alias {alias})",
            "resolution": "CATALOG", "scope": "", "group": cb["group"],
            "artifact": cb["artifact"], "version": cb["version"],
        })
    return rules


def discover_data_layer(files: List[Dict[str, Any]]) -> bool:
    for f in files:
        if f["category"] != "source":
            continue
        text = read_text(f["abs"])
        if "@Database" in text or "@Dao" in text or "@Entity" in text or "@Query" in text:
            return True
    return False


_COMPOSE_FUN_OTHER_RE = re.compile(r"\b(Scaffold|TopAppBar|BottomAppBar|NavigationBar|LazyColumn|Row|Column|Box|Image|AsyncImage|FloatingActionButton|Button|OutlinedButton|TextButton|Switch|Checkbox|RadioButton|Slider|TabRow|Chip|FilterChip|AlertDialog|ModalBottomSheet|DropdownMenu)\s*\(")

# --- call-canvas: capture EVERY UI call (recognized or self-built) ---
_UI_SKIP_PREFIXES = (
    "remember", "Launched", "Disposable", "SideEffect", "String", "Int", "Float", "Boolean",
    "List", "Map", "Set", "Array", "Mutable", "State", "Dp", "Color", "Brush", "Offset", "Size",
    "Rect", "Shape", "mutable", "Immutable", "Comparator", "key", "Enum", "Radio", "Alpha",
)
_COMPOSE_ANY_CALL_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]{2,})\s*\(")
_UI_CONTAINER_CALLS = {
    "Row", "Column", "Box", "Scaffold", "LazyRow", "LazyColumn", "LazyVerticalGrid",
    "ListStack", "Stack", "FlowRow", "FlowColumn", "HorizontalPager", "VerticalPager",
    "Surface", "TopAppBar", "BottomAppBar", "NavigationBar", "NavigationBarItem", "TabRow",
    "ModalBottomSheet", "AlertDialog", "DropdownMenu", "Popup", "GlasensePopup",
    "GlasenseModalTopBar", "TopBarSpacer", "CompositionLocalProvider", "BoxWithConstraints",
    "Pager", "Crossfade", "AnimatedVisibility", "CustomAnimatedVisibility",
}
_UI_CANVAS_SKIP = {
    "Color", "Modifier", "Intent", "CircleShape", "RoundedCornerShape", "RectangleShape",
    "DpSize", "FontWeight", "TextAlign", "Stroke", "Offset", "Size", "ColorScheme",
    "Brush", "Image", "Context", "MediaType", "Rect", "arrayListOf", "mutableStateOf",
    "AlertDialog", "Canvas",
}


_UI_CANVAS_SKIP = {
    "Color", "Modifier", "Intent", "CircleShape", "RoundedCornerShape", "RectangleShape",
    "DpSize", "FontWeight", "TextAlign", "Stroke", "Offset", "Size", "ColorScheme",
    "Brush", "Image", "Context", "MediaType", "Rect", "arrayListOf", "mutableStateOf",
    "AlertDialog", "Canvas", "PaddingValues", "DpRect", "Shape", "RectF", "ColorValue",
    "AlphaMask", "Action", "PopupState", "DimIndication", "MutableInteractionSource",
    "KeyboardOptions", "KeyboardType", "ImeAction", "StrokeWidth", "Inset", "Clip",
    "Type", "Enum", "Value", "Pos", "Name", "Flow", "Wind", "Text",
}


def _ui_call_kind(name: str) -> str:
    if name in _UI_CONTAINER_CALLS or name in _UI_CANVAS_SKIP:
        return "container"
    if any(name.endswith(sfx) for sfx in ("Row", "Field", "Input", "Toggle", "Switch", "Picker",
                                          "Item", "Button", "Selector", "Option", "Section",
                                          "Sheet", "Dialog", "Tile", "Dots", "List")):
        return "field"
    return "ui_call"

# ---------------------------------------------------------------------------
# risk probes: WebView / reflection / background / media / native / SDK ... (高危清单)
# ---------------------------------------------------------------------------

RISK_PROBES = [
    {
        "probe_id": "RISK-WEBVIEW", "category": "WebView/JS Bridge",
        "severity": "高", "classes": (),
        "patterns": (r"addJavascriptInterface", r"loadUrl\(|evaluateJavascript", r"WebViewClient",
                      r"loadDataWithBaseURL", r"JavaScriptEnabled", r"WebChromeClient"),
        "hint": "鸿蒙需 Web 组件 + JS bridge 重写，API 不兼容",
    },
    {
        "probe_id": "RISK-REFLECTION", "category": "反射/动态代理",
        "severity": "高", "classes": (),
        "patterns": (r"Class\.forName", r"java\.lang\.reflect", r"Method\.invoke",
                      r"getDeclaredField", r"Proxy\.newProxyInstance", r"getDeclaredMethod"),
        "hint": "静态分析不透，需运行时验证",
    },
    {
        "probe_id": "RISK-BACKGROUND", "category": "后台任务/服务",
        "severity": "高", "classes": (),
        "patterns": (r"\bWorkManager\b", r"JobScheduler", r"\bAlarmManager\b", r"startForeground",
                      r"setExact\(|setRepeating", r"\bBroadcastReceiver\b", r"background\.\w+",
                      r"extends Service\b|: Service\b|Service\(\)\s*\{|startService\(|schedule\(Worker"),
        "hint": "鸿蒙需 WorkScheduler/长时任务，API 不同",
    },
    {
        "probe_id": "RISK-CUSTOMPAINT", "category": "自定义绘制/动画",
        "severity": "高", "classes": (),
        "patterns": (r"onDraw\(|onMeasure\(|onLayout\(", r"Canvas\.", r"ValueAnimator", r"ObjectAnimator",
                      r"Animator\.inflation", r"SurfaceView", r"TextureView", r"openGL"),
        "hint": "Canvas 绘制与 ArkUI 画布 API 不同，需逐点迁移",
    },
    {
        "probe_id": "RISK-MEDIA", "category": "音视频/相机",
        "severity": "高",
        "classes": ("MediaPlayer", "ExoPlayer", "AudioRecord", "CameraX", "MediaRecorder", "VideoView", "MediaCodec"),
        "patterns": (r"MediaPlayer", r"ExoPlayer", r"AudioRecord", r"CameraX", r"MediaRecorder",
                      r"VideoView", r"MediaCodec", r"MediaProjection"),
        "hint": "鸿蒙需 AVSession/Camera API 重写",
    },
    {
        "probe_id": "RISK-NATIVE", "category": "NDK/JNI",
        "severity": "高", "classes": (),
        "patterns": (r"System\.loadLibrary", r"external fun", r"native fun", r"JNIEXPORT",
                      r'extern "C"', r"cinterop"),
        "hint": "需 tocpp/native 重构或提供鸿蒙 .so",
    },
    {
        "probe_id": "RISK-ROOM", "category": "Room 高级用法",
        "severity": "中", "classes": (),
        "patterns": (r"@ForeignKey", r"@Transaction", r"Migration\(|addMigrations", r"@Embedded",
                      r"@ColumnInfo", r"@Entity\(.*indices", r"@RewriteQueriesToDropUnusedColumns",
                      r"@Database\(.*version"),
        "hint": "多表关系/迁移链要完整还原",
    },
    {
        "probe_id": "RISK-NETAPI", "category": "网络 API 定义",
        "severity": "中", "classes": (),
        "patterns": (r"Retrofit", r"OkHttp", r"Ktor", r"HttpURLConnection", r"@GET", r"@POST",
                      r"@PUT", r"@DELETE", r"@PATCH", r"RequestBody", r"@Streaming"),
        "hint": "端点/拦截器行为需逐条迁移",
    },
    {
        "probe_id": "RISK-SDK", "category": "第三方 SDK 调用点",
        "severity": "中", "classes": (),
        "patterns": (r"Firebase", r"Admob", r"AdMob", r"AppMetrica", r"Flurry", r"YandexStats",
                      r"Crashlytics", r"Analytics", r"Billing", r"Payment"),
        "hint": "需要鸿蒙侧同等 SDK 或降级方案",
    },
    {
        "probe_id": "RISK-SERIALIZE", "category": "序列化方案",
        "severity": "中", "classes": (),
        "patterns": (r"kotlinx\.serialization", r"Gson", r"Moshi", r"Parcelable", r"Serializable",
                      r"Jackson", r"ProtoBuf"),
        "hint": "数据模型迁移需确认序列化兼容",
    },
    {
        "probe_id": "RISK-AUTH", "category": "账号/认证",
        "severity": "中", "classes": (),
        "patterns": (r"AccountManager", r"Authenticator", r"OAuth", r"signIn", r"login\w*\s*\(",
                      r"auth\.token"),
        "hint": "OAuth/令牌逻辑通常与平台绑定",
    },
    {
        "probe_id": "RISK-SHORTCUT", "category": "快捷方式/小组件",
        "severity": "中", "classes": ("AppWidgetProvider", "Glance"),
        "patterns": (r"AppWidgetProvider", r"Glance\.\w*appwidget", r"ShortcutManager", r"RemoteViews",
                      r"ACTION_APPWIDGET", r"PinShortcut"),
        "hint": "小组件需列在 entry 并提供 Harmony 侧实现",
    },
    {
        "probe_id": "RISK-BIOMETRIC", "category": "生物识别/加密",
        "severity": "中", "classes": (),
        "patterns": (r"BiometricPrompt", r"Fingerprint", r"KeyStore", r"Cipher\.", r"generateKey"),
        "hint": "鸿蒙需认证库/合规审批",
    },
    {
        "probe_id": "RISK-EXPERIMENTAL", "category": "Compose 实验性 API",
        "severity": "中", "classes": (),
        "patterns": (r"@OptIn\(Experimental", r"ExperimentalMaterial3Api", r"ExperimentalFoundationApi",
                      r"ExperimentalAnimationApi", r"ExperimentalLayoutApi"),
        "hint": "实验 API 在鸿蒙侧可能无直接对应",
    },
]


def scan_risk_probes(files: List[Dict[str, Any]],
                     pages: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """检出 WebView/反射/后台/媒体/NDK 等风险信号 (存在性扫描，非抽取)。

    每匹配记一行：probe_id / severity / file:line / 信号上下文 / Harmony 提示。
    （不参与覆盖台账——风险扫描只做备忘，GAP 判定仍由候选覆盖决定。）"""
    probes: List[Dict[str, Any]] = []
    page_by_symbol = {p["symbol"]: p for p in pages} if pages else {}
    for f in files:
        cat = f["category"]
        if cat in ("test", "metadata", "binary", "build", "empty"):
            continue
        text = read_text(f["abs"])
        if not text:
            continue
        rel = f["rel"]
        page_id = ""
        stem = Path(rel).stem
        if stem in page_by_symbol:
            page_id = page_by_symbol[stem]["page_id"]
        for probe in RISK_PROBES:
            pats = probe["patterns"]
            hits = []
            expr = "|".join("(?:" + pat + ")" for pat in pats)
            try:
                comp = re.compile(expr)
            except re.error:
                continue
            for m in comp.finditer(text):
                hits.append((m.group(0)[:60], _line_of(text, m.start())))
            for cls in probe.get("classes", ()):
                if cls and len(cls) > 2 and cls in text:
                    hits.append((cls, _line_of(text, text.find(cls))))
            if not hits:
                continue
            seen_sig = set()
            for sig, ln in hits[:8]:
                key = (sig, ln)
                if key in seen_sig:
                    continue
                seen_sig.add(key)
                probes.append({
                    "probe_id": probe["probe_id"], "category": probe["category"],
                    "severity": probe["severity"], "file": rel, "line": ln,
                    "signal": sig[:60], "harmony_hint": probe["hint"],
                    "page_id": page_id, "count": len(hits),
                })
    return probes

# ---------------------------------------------------------------------------
# value-level extraction: colors / gradients / shape-size / motion (动效)
# ---------------------------------------------------------------------------

_COLOR_HEX_RE = re.compile(r"Color\((?:0x)?([0-9A-Fa-f]{8})\)")
_COLOR_HEX6_RE = re.compile(r"Color\((?:0x)?([0-9A-Fa-f]{6})\)")
_COPY_ALPHA_RE = re.compile(r"copy\s*\(\s*alpha\s*=\s*([\d.]+f?)\s*\)")
_NAMED_TOKEN_RE = re.compile(r"\b([A-Z][A-Za-z0-9]+500)\b")  # Blue500 / Orange500
_ALPHA_SHORTCUT_RE = re.compile(r"\bColor\.(Black|White)\.copy\(\s*([\d.]+)f?\)")
_TOKENS_COLOR_RE = re.compile(r"val\s+([A-Za-z0-9]+500)\s*=\s*Color\((?:0x)?([0-9A-Fa-f]{8})\)")
_PALETTE_ENTRY_RE = re.compile(r"(\w+)\s*=\s*([^,\n]+)")

# shape / size / gradient / blur / shadow signals
_MODIFIER_SIZE_RE = re.compile(r"\.size\s*\(\s*([\d.]+)\s*\.dp\)\s*|\s*([\d.]+)\s*\.dp\)")
_MODIFIER_WIDTH_HEIGHT_RE = re.compile(r"\.(width|height|widthIn|heightIn|aspectRatio)\s*\(([^)]*)\)")
_MODIFIER_PADDING_RE = re.compile(r"\.padding\s*\(\s*(?:horizontal\s*=\s*)?[\d.]+\s*\.dp\)|\.padding\s*\((?:all|start|end|top|bottom)*\s*=\s*[\d.]+\s*\.dp\)")
_MODIFIER_WEIGHT_RE = re.compile(r"\.weight\s*\(\s*([\d.]+)f?\s*\)")
_MODIFIER_SHAPE_RE = re.compile(r"\.(?!size|width|height|padding|weight|background)(?:clip|background)\s*\(\s*([^,)]+)")
_CIRCLE_RE = re.compile(r"(CircleShape|RoundedCornerShape\s*\(\s*([\d.]+)\s*\.dp\s*\)|RectangleShape)")
_BLUR_RE = re.compile(r"blur\s*\(\s*([\d.]+)\s*\.dp|graphicsLayer\s*\([^)]*blur\s*=\s*([\d.]+)")
_SHADOW_RE = re.compile(r"\.shadow\s*\(\s*([\d.]+)\s*\.dp|elevation\s*=\s*([\d.]+)\s*\.dp")
_BRUSH_RE = re.compile(r"Brush\.(linearGradient|radialGradient|verticalGradient)\s*\(\s*(?:colors\s*=\s*)?listOf\((.*?)\)", re.S)
_ANIM_STATE_RE = re.compile(r"\b(?:animate[a-zA-Z]*AsState|Animatable|rememberInfiniteTransition|animatable|animateContentSize|AnimatedVisibility|AnimatedContent|Crossfade|CustomAnimatedVisibility)\s*\(")
_NESTED_SCROLL_RE = re.compile(r"\bNestedScrollConnection\b|\bmaxCollapseOffset|\bcalendarOffsetPx|\bonPreScroll|\bonPostScroll|\bnestedScroll\s*\(")
_RUNTIME_SHADER_RE = re.compile(r"\bruntimeShaderEffect\s*\(\s*\"([A-Za-z0-9_]+)\"")
_FADE_RE = re.compile(r"myFade(In|Out|InFade|OutFade)\s*\(")


def scan_color_palette(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Color constants + palette entries -> rows {name, hex, alpha, src} (真值)."""
    rows = []
    seen = set()
    for f in files:
        if f["category"] not in ("source",):
            continue
        text = read_text(f["abs"])
        rel = f["rel"]
        if "Color(0x" not in text and "500" not in text:
            continue
        # tokens: val Blue500 = Color(0xFF...)
        for m in _TOKENS_COLOR_RE.finditer(text):
            name, hexv = m.group(1), m.group(2)
            key = (name, hexv)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "name": name, "hex": "#" + hexv[2:], "alpha": "1.0",
                "kind": "TOKEN", "file": rel, "line": _line_of(text, m.start()),
            })
        # palette: XxxPalette = GlasenseColors(activeTrack = Green500, ...)
        for m in re.finditer(r"val\s+([A-Za-z]+(?:Light|Dark)?Palette)\s*=\s*\w+\s*\(", text):
            pname = m.group(1)
            open_paren = text.find("(", m.start())
            close_paren = _brace_match(text, open_paren, "(", ")")
            if close_paren < 0:
                continue
            body = text[open_paren + 1:close_paren]
            for e in _PALETTE_ENTRY_RE.finditer(body):
                field, val = e.group(1), e.group(2).strip()
                # keep original text for traceability
                key = (pname, field, val[:30])
                if key in seen:
                    continue
                seen.add(key)
                # resolve: Color(0xFF...).copy(alpha) | NamedToken | Color.Black.copy(a) | Color.White
                hexv, alpha = "", "1.0"
                shape = ""
                hm = _COLOR_HEX_RE.search(val)
                if hm:
                    hexv = "#" + hm.group(1)[2:]
                else:
                    nm = re.search(r"\bColor\.(Black|White)", val)
                    if nm:
                        hexv = "#000000" if nm.group(1) == "Black" else "#FFFFFF"
                    else:
                        tok = _NAMED_TOKEN_RE.search(val)
                        if tok:
                            shape = "token:" + tok.group(1)
                cm = _COPY_ALPHA_RE.search(val)
                if cm:
                    alpha = cm.group(1)
                rows.append({
                    "name": f"{pname}.{field}", "hex": hexv, "alpha": alpha,
                    "kind": "PALETTE" + ("|" + shape if shape else ""),
                    "file": rel, "line": _line_of(text, m.start()),
                })
        # gradient list (linearGradient / listOf of Color())
        for gm in re.finditer(r"(?:Brush\.\w*Gradient\s*\(\s*(?:colors\s*=\s*)?)?listOf(.*?)\)", text, re.S):
            gbody = gm.group(1)
            items = []
            for im in re.finditer(r"(?:Color\((?:0x)?([0-9A-Fa-f]{8})\)|\b([A-Za-z]+500))", gbody):
                if im.group(1):
                    items.append("#" + im.group(1)[2:])
                else:
                    items.append("token:" + im.group(2))
            if items:
                rows.append({
                    "name": f"gradient(listOf)", "hex": " > ".join(items),
                    "alpha": "", "kind": "GRADIENT", "file": rel,
                    "line": _line_of(text, gm.start()),
                })
    return rows


def scan_motion(files: List[Dict[str, Any]],
                pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """动效/行为: 动画状态、嵌套滚动折叠、虚化着色器、过渡."""
    motion = []
    seen = set()
    page_by_symbol = {p["symbol"]: p for p in pages}
    for f in files:
        if f["category"] not in ("source", "layout"):
            continue
        text = read_text(f["abs"])
        rel = f["rel"]
        stem = Path(rel).stem
        pid = page_by_symbol.get(stem, {}).get("page_id", "")
        for rx, label in ((_ANIM_STATE_RE, "animation"), (_NESTED_SCROLL_RE, "nested-scroll-fold"),
                          (_RUNTIME_SHADER_RE, "runtime-shader"), (_FADE_RE, "fade"),
                          (_MODIFIER_SIZE_RE, "size"), (_MODIFIER_SHAPE_RE, "shape")):
            for m in rx.finditer(text):
                sig = m.group(0)[:60]
                key = (rel, label, _line_of(text, m.start()), sig)
                if key in seen:
                    continue
                seen.add(key)
                motion.append({
                    "page_id": pid, "motion_type": label, "signal": sig,
                    "file": rel, "line": _line_of(text, m.start()),
                })
    return motion


def scan_shape_details(nodes: List[Dict[str, Any]]) -> None:
    """就地增强 compose nodes: size/shape/brush/blur/alpha 从 chunk 内抽取."""
    for n in nodes:
        chunk = n.get("_chunk", "")
        if not chunk:
            continue
        attrs: Dict[str, str] = dict(n.get("attributes", {}))
        sm = _MODIFIER_SIZE_RE.search(chunk)
        if sm:
            attrs["size_dp"] = (sm.group(1) or sm.group(2) or "") + "dp"
        wm = _MODIFIER_WIDTH_HEIGHT_RE.search(chunk)
        if wm:
            num = re.search(r"[\d.]+", wm.group(2))
            if num:
                attrs[f"{wm.group(1)}_dp"] = num.group(0) + "dp"
        pm = _MODIFIER_PADDING_RE.search(chunk)
        if pm:
            attrs["padding_dp"] = pm.group(0).split("(")[-1].rstrip(")")
        wr = _MODIFIER_WEIGHT_RE.search(chunk)
        if wr:
            attrs["weight"] = wr.group(1)
        cm = _CIRCLE_RE.search(chunk)
        if cm:
            attrs["shape"] = cm.group(0)[:40]
        bm = _BLUR_RE.search(chunk)
        if bm:
            attrs["blur_dp"] = (bm.group(1) or "") + ("dp" if bm.group(1) else "")
        hm = _SHADOW_RE.search(chunk)
        if hm:
            attrs["elevation_dp"] = (hm.group(1) or hm.group(2) or "") + "dp"
        gm = _BRUSH_RE.search(chunk)
        if gm:
            items = re.findall(r"0x[0-9A-Fa-f]{8}|[A-Za-z]+500", gm.group(2))
            attrs["gradient"] = " > ".join("#" + i[2:] if i.startswith("0x") else "token:" + i for i in items)
        n["attributes"] = attrs

# ---------------------------------------------------------------------------
# behaviors: 行为流（onClick/onXxx -> action -> data_target -> side_effect）
# P4 功能还原核心：按钮/开关/查询 的事件处理要落到数据通道，而非只有 UI。
# ---------------------------------------------------------------------------

_BEHAVIOR_EVENT_RE = re.compile(
    r"(?:onClick|onCheckedChange|onLongClick|onValueChange|onDismiss|onConfirm|onSave|onDelete|onAdd|onSelect|onToggle|onSearch|onSubmit)\s*=\s*\{([^}]{0,220})", re.S)
_BEHAVIOR_CALL_RE = re.compile(
    r"(?:\bviewModel(?:\s*\?)?\.|\b(?:dao|repository|repo|database)\s*\.|\bauthStore\s*\.|\binventory\s*\.)"
    r"([A-Za-z0-9_]+)\s*\(")
_BEHAVIOR_ALT_RE = re.compile(r"\b(?:insert|update|delete|remove|add|save|submit|toggle|switch|select|search|query|filter|load|refresh|navigate|launch|show|hide|open|close|clear|copy|share|export)\w*\s*\(")
_BEHAVIOR_LAMBDA_REF_RE = re.compile(r"\b(vm\.|viewModel\.|onItemClick|onAction|callback)\s*")


def scan_behaviors(files: List[Dict[str, Any]],
                   pages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从源码抽事件行为：page/component 的事件 handler 调用的数据方法与其目标/副作用。

    一行一条：page_id | component(事件挂载点/文本) | event | action | params | data_target | side_effect
    供 P4 contract 的 behavior_bindings 直接消费。
    """
    page_by_symbol = {p["symbol"]: p for p in pages}
    rows: List[Dict[str, Any]] = []
    seen = set()
    for f in files:
        if f["category"] != "source":
            continue
        text = read_text(f["abs"])
        rel = f["rel"]
        stem = Path(rel).stem
        page = page_by_symbol.get(stem)
        pid = page["page_id"] if page else ""
        # 找到从事件 handler 到数据方法的关系
        for m in _BEHAVIOR_EVENT_RE.finditer(text):
            evt = "click"
            raw = f"onClick" if "onClick" in m.group(0)[:40] else (m.group(0)[:40])
            handler = m.group(1)
            line = _line_of(text, m.start())
            # data call 搜索
            dm = _BEHAVIOR_CALL_RE.search(handler)
            if dm:
                action = dm.group(1)
                data_target = "viewModel/dao/repository"
            else:
                am = _BEHAVIOR_ALT_RE.search(handler)
                action = am.group(0) if am else ""
                data_target = "component/internal"
            # 副作用判断
            side = []
            if "navigate" in handler or "navController" in handler or "backStack" in handler:
                side.append("navigate")
            if "refresh" in handler or "notify" in handler or "adapter" in handler or "reload" in handler:
                side.append("refresh-list")
            if "Toast" in handler or "snackBar" in handler or "Snackbar" in handler or "showDialog" in handler:
                side.append("feedback")
            if "alertDialog" in handler or "dialog" in handler.lower() or "popup" in handler.lower():
                side.append("dialog")
            if "dismiss" in handler.lower():
                side.append("dismiss")
            key = (rel, line, action)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "page_id": pid, "page_symbol": page["symbol"] if page else "",
                "event": raw[:32], "target": _BEHAVIOR_EVENT_RE.pattern or "",
                "action": action[:60],
                "params": (dm.group(0)[:60] if dm else handler[:50]),
                "data_target": data_target,
                "side_effect": "+".join(side) if side else "none",
                "source_ref": f"{rel}:{line}",
            })
    return rows
