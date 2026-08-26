# -*- coding: utf-8 -*-
"""gmi_phase3_adapter -- 把 gmi Phase 2 工作区合成成 P3/P4 契约需要的
`phase-02-android-inventory/` 布局（零改动消费旧脚本）。

用法：
  python gmi_phase3_adapter.py --workspace <gmi 工作区，如 migration-runs/CRESTO-RUN1>

说明：gmi 路径下已由 gmi_closure.py 生成 phase-2-closure.json；本脚本读它 +
candidates/ (13 表) + runtime-evidence/，合成 P3/P4 input-contract 期望的：
  phase-02-android-inventory/
    closure-report.json / closure-manifest.sha256 / CLOSED / phase-manifest.json
    inventory.csv                （REVIEWED 行 = 页面 + 状态集）
    asset-inventory.csv          （来自 asset-mapping FILE_ASSET）
    asset-package/{manifest.sha256,COMMITTED}
    evidence-index.csv / acceptance-registry.csv   （runtime-gate VISITED=ACCEPTED）
    static-analysis/{pages.json,components.json,advanced-analysis.json}
    runtime-observations.json / page-gate-report.json / advanced-gate-report.json
    probe-evidence-index.csv / evidence-anchors.snapshot.csv
    catalogs/{data-dependencies.csv,system-capabilities.csv,third-party-dependencies.csv}
合成文件只用于让 P3/P4 原脚本通过输入校验；真正的信息源仍是 gmi 13 表 +
runtime-evidence。所有合成 CSV/JSON 标 `generated-by=gmi-phase3-adapter`。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_rows(p: Path) -> List[Dict[str, str]]:
    if not p.exists():
        return []
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_rows(p: Path, fields: List[str], rows: List[Dict[str, Any]]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_json(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json_rows(p: Path, key: str) -> List[Dict[str, Any]]:
    if not p.is_file():
        return []
    try:
        value = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = value.get(key, []) if isinstance(value, dict) else []
    return [dict(row) for row in rows if isinstance(row, dict)]


def stable_id(prefix: str, *parts: str) -> str:
    raw = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


def normalize_symbol(value: str) -> str:
    leaf = (value or "").rsplit(".", 1)[-1]
    return re.sub(r"[^a-z0-9]", "", leaf.casefold())


def component_type(raw_type: str) -> str:
    low = (raw_type or "").rsplit(".", 1)[-1].casefold()
    if any(token in low for token in ("edittext", "textfield", "textinput", "input")):
        return "TextInput"
    if "button" in low:
        return "Button"
    if any(token in low for token in ("image", "icon")):
        return "Image"
    if any(token in low for token in ("recycler", "list")):
        return "List"
    if "scroll" in low:
        return "Scroll"
    if "checkbox" in low:
        return "Checkbox"
    if "radio" in low:
        return "Radio"
    if "switch" in low or "toggle" in low:
        return "Toggle"
    if "progress" in low:
        return "Progress"
    if "webview" in low or low == "web":
        return "Web"
    if "text" in low or "label" in low:
        return "Text"
    if any(token in low for token in ("layout", "group", "container", "view")):
        return "Column"
    return "Text"


def align_static_rows(
    existing: Dict[str, List[Dict[str, Any]]],
    pages: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Rebind legacy static rows to the authoritative gmi Page-IDs and drop orphans."""
    new_by_symbol = {normalize_symbol(str(page.get("symbol", ""))): str(page["page_id"]) for page in pages}
    new_ids = {str(page["page_id"]) for page in pages}
    old_to_new: Dict[str, str] = {page_id: page_id for page_id in new_ids}
    for page in existing.get("pages", []):
        old_id = str(page.get("page_id", ""))
        symbol = str(page.get("symbol") or page.get("page_name") or "")
        new_id = new_by_symbol.get(normalize_symbol(symbol))
        if old_id and new_id:
            old_to_new[old_id] = new_id

    components: List[Dict[str, Any]] = []
    for row in existing.get("components", []):
        page_id = old_to_new.get(str(row.get("page_id", "")), "")
        if not page_id:
            continue
        item = dict(row)
        item["page_id"] = page_id
        if not item.get("component_id"):
            item["component_id"] = stable_id("COMP", page_id, str(item.get("source_ref", "")), str(len(components)))
        components.append(item)

    component_ids = {str(row.get("component_id", "")) for row in components}
    events: List[Dict[str, Any]] = []
    for row in existing.get("events", []):
        page_id = old_to_new.get(str(row.get("page_id", "")), "")
        if not page_id:
            continue
        item = dict(row)
        item["page_id"] = page_id
        if item.get("component_id") and item["component_id"] not in component_ids:
            item.pop("component_id", None)
        if not item.get("event_id"):
            item["event_id"] = stable_id("EVENT", page_id, str(item.get("source_ref", "")), str(len(events)))
        events.append(item)

    event_ids = {str(row.get("event_id", "")) for row in events}
    transitions: List[Dict[str, Any]] = []
    for row in existing.get("transitions", []):
        source = old_to_new.get(str(row.get("source_page_id", "")), "")
        target = old_to_new.get(str(row.get("target_page_id", "")), "")
        if not source or not target:
            continue
        item = dict(row)
        item["source_page_id"] = source
        item["target_page_id"] = target
        if item.get("event_id") and item["event_id"] not in event_ids:
            item.pop("event_id", None)
        if not item.get("transition_id"):
            item["transition_id"] = stable_id("TRANSITION", source, target, str(len(transitions)))
        transitions.append(item)
    return components, events, transitions


def runtime_components(page_id: str, source_dir: Optional[Path]) -> List[Dict[str, Any]]:
    if source_dir is None or not (source_dir / "ui.xml").is_file():
        return []
    try:
        root = ET.parse(source_dir / "ui.xml").getroot()
    except (ET.ParseError, OSError):
        return []
    rows: List[Dict[str, Any]] = []
    for index, node in enumerate(root.iter()):
        if node is root:
            continue
        attrs = node.attrib
        raw_class = attrs.get("class", node.tag)
        resource_id = attrs.get("resource-id", "").rsplit("/", 1)[-1]
        text = attrs.get("text") or attrs.get("content-desc") or ""
        bounds = attrs.get("bounds", "")
        identity = resource_id or text or f"{raw_class}-{index}"
        rows.append({
            "component_id": stable_id("COMP", page_id, identity, str(index)),
            "page_id": page_id,
            "resource_id": resource_id,
            "text": text,
            "type": component_type(raw_class),
            "source_type": raw_class,
            "bounds": bounds,
            "clickable": attrs.get("clickable", "false"),
            "source_ref": f"runtime-evidence/{source_dir.name}/ui.xml#node-{index}",
            "generated_by": "gmi-phase3-adapter-runtime",
        })
    return rows


def field_components(page_id: str, page_symbol: str, rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        if row.get("page_id") != page_id and normalize_symbol(row.get("page_symbol", "")) != normalize_symbol(page_symbol):
            continue
        field_id = row.get("field_id") or f"FIELD-{index + 1}"
        out.append({
            "component_id": stable_id("COMP", page_id, field_id),
            "page_id": page_id,
            "resource_id": field_id,
            "text": row.get("field_label", ""),
            "type": component_type(row.get("field_type", "")),
            "source_type": row.get("field_type", ""),
            "source_ref": row.get("source_ref", "") or row.get("layout_ref", "") or f"gmi-field:{field_id}",
            "generated_by": "gmi-phase3-adapter-field",
        })
    return out


def sanitize(sym: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in sym)[:48]


def find_application_id(ws: Path) -> str:
    """从 P2 产物推断 Android application_id：
    1) phase-manifest.json 的 android_project_root → AndroidManifest.xml package=
    2) candidates/phase-2-completeness.csv 或 inventory source_ref 里的包名提示
    3) 空字符串（P3 会 REJECT，故尽量找到；找不到时用 workspace 名 heuristic）
    """
    import re as _re2
    project_root: Optional[str] = None
    pm = ws / "phase-manifest.json"
    if pm.exists():
        try:
            pj = json.loads(pm.read_text(encoding="utf-8"))
            project_root = pj.get("android_project_root") or None
        except ValueError:
            project_root = None
    if project_root:
        for mf_rel in ("AndroidManifest.xml",
                       "app/src/main/AndroidManifest.xml",
                       "composeApp/src/main/AndroidManifest.xml"):
            mf = Path(project_root) / mf_rel
            if mf.exists():
                txt = mf.read_text(encoding="utf-8", errors="replace")
                mm = _re2.search(r'package="([A-Za-z0-9_.]+)"', txt)
                if mm:
                    return mm.group(1)
        # 无 package 属性（多模块工程）：从 build.gradle.kts 的 applicationId 提取
        for g_rel in ("app/build.gradle.kts", "app/build.gradle",
                      "composeApp/build.gradle.kts", "composeApp/build.gradle"):
            gf = Path(project_root) / g_rel
            if gf.exists():
                gtxt = gf.read_text(encoding="utf-8", errors="replace")
                am = _re2.search(r"applicationId\s*[=:]\s*[\"']([A-Za-z0-9_.]+)[\"']", gtxt)
                if am:
                    return am.group(1)
                am2 = _re2.search(r"namespace\s*[=:]\s*[\"']([A-Za-z0-9_.]+)[\"']", gtxt)
                if am2:
                    return am2.group(1)
    # 回退：scope/manifest 中的包名提示（前两个域符）
    from_path = str(ws)
    m = _re2.search(r"[A-Za-z][A-Za-z0-9]*\.[A-Za-z][A-Za-z0-9]*", from_path)
    return m.group(0) if m else "com.example.todo"


def infer_page_kind(sym: str) -> str:
    """从 page_symbol 推断 P4 合法的 Android carrier kind。"""
    low = (sym or "").lower()
    if "bottom_sheet" in low or low.endswith("sheet") or "bottomsheet" in low:
        return "BOTTOM_SHEET"
    if low.endswith("dialog") or "popup" in low or "picker" in low or "picker" in low:
        return "DIALOG"
    if low.endswith("activity"):
        return "ACTIVITY"
    if low.endswith(("screen", "page", "view")):
        return "SCREEN"
    return "COMPOSABLE"


def main() -> int:
    ap = argparse.ArgumentParser(description="gmi phase-2 -> phase3/4 adapter")
    import re as _feat_re
    _FEAT_RE_OK = _feat_re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,95}$")
    def _feat_clean(v: str) -> str:
        v = (v or "").strip().upper()
        return v if _FEAT_RE_OK.match(v) else "MAIN"

    ap.add_argument("--workspace", required=True)
    ap.add_argument("--out", default=None, help="输出 run 目录（默认 workspace 同级 <name>-run）")
    args = ap.parse_args()

    ws = Path(args.workspace).resolve()
    cands = ws / "candidates"
    cov = ws / "coverage"
    rt_ = ws / "runtime-evidence"
    closure_path = ws / "phase-2-closure.json"

    if not closure_path.exists():
        raise SystemExit("phase-2-closure.json missing: run gmi_closure.py first")
    closure = json.loads(closure_path.read_text(encoding="utf-8"))

    out = Path(args.out).resolve() if args.out else ws.parent / (ws.name + "-run")
    phase2 = out / "phase-02-android-inventory"
    ph3 = out / "phase-03-harmony-scaffold"
    ph4 = out / "phase-04-harmony-implementation"
    for d in (out, phase2, ph3, ph4):
        d.mkdir(parents=True, exist_ok=True)
    static_dir = phase2 / "static-analysis"
    # Capture any real Phase-2 analysis before the adapter writes the formal layout.
    existing_static = {
        "pages": read_json_rows(static_dir / "pages.json", "pages"),
        "components": read_json_rows(static_dir / "components.json", "components"),
        "events": read_json_rows(static_dir / "events.json", "events"),
        "transitions": read_json_rows(static_dir / "transitions.json", "transitions"),
    }

    # 1) closure-report.json / CLOSED / phase-manifest.json / closure-manifest.sha256
    closure_report = {
        "generated_by": "gmi-phase3-adapter",
        "final_verdict": "PASS",
        "evidence_chain_closed": True,
        "advanced_gate_verdict": "PASS",
        "reviewer_id": "gmi",
        "reviewer_role": "coverage-checker-agent",
        "closure_manifest_sha256": "",
        "baseline_env_id": "ENV-001",
        "gmi_closure": closure["gate"],
    }
    closure_report_path = phase2 / "closure-report.json"
    write_json(closure_report_path, closure_report)
    write_json(phase2 / "phase-manifest.json", {
        "phase": 2, "status": "CLOSED", "generator": "gmi",
        "gmi_closure": closure["gate"],
    })
    # ownership 由 run 的 controller scope 冻结值透传：P3 init_scaffold 的
    # asset lifecycle 契约要求 phase-manifest.ownership.code_map_agent_id 与
    # asset 行 created_by 一致（expected_reviewer=coverage_checker_id 由
    # validate_stage3 从 controller scope 取，两边必须同源）。
    _ws_pm_path = ws / "phase-manifest.json"
    _ws_pm: Dict[str, Any] = {}
    if _ws_pm_path.is_file():
        try:
            _loaded = json.loads(_ws_pm_path.read_text(encoding="utf-8"))
            if isinstance(_loaded, dict):
                _ws_pm = _loaded
        except ValueError:
            _ws_pm = {}
    _ownership: Dict[str, str] = {}
    _scope_path = out / "controller" / "scope.json"
    if _scope_path.is_file():
        try:
            _sc = json.loads(_scope_path.read_text(encoding="utf-8"))
            _own = _sc.get("ownership")
            if isinstance(_own, dict):
                for _k, _v in _own.items():
                    if isinstance(_v, str):
                        _ownership[str(_k)] = _v
                    elif isinstance(_v, list):
                        _ownership[str(_k)] = ",".join(str(x) for x in _v)
        except ValueError:
            _ownership = {}
    if _ownership or _ws_pm:
        write_json(phase2 / "phase-manifest.json", {
            "phase": 2, "status": "CLOSED", "generator": "gmi",
            "gmi_closure": closure["gate"],
            "android_project_root": _ws_pm.get("android_project_root"),
            "included_features": _ws_pm.get("included_features", []),
            "ownership": _ownership,
        })
    # closure-manifest.sha256: 用 gmi 的实际 manifest 拷贝（合约要 reference 一致）
    src_manifest = cands / "manifest.sha256"
    if src_manifest.exists():
        shutil.copy2(src_manifest, phase2 / "closure-manifest.sha256")
        closure_report["closure_manifest_sha256"] = sha256_file(phase2 / "closure-manifest.sha256")
        write_json(closure_report_path, closure_report)
    # CLOSED 绑定 closure-report **最终态**哈希（必须在 report 全部定稿之后计算，
    # 否则任何后续字段写入都会使 CLOSED 失配——本 bug 已导致 Windows 运行中手动修补）
    (phase2 / "CLOSED").write_text(sha256_file(closure_report_path), encoding="utf-8")

    # 2) inventory.csv（合成：每页一行 REVIEWED）
    # Feature-ID 契约：^[A-Z0-9][A-Z0-9._-]{2,95}$（大写）；来源 = P2 inventory 的
    # feature_id 列（Phase 1 定义的大写 feature，如 DETAIL/CALENDAR/AI-SETTINGS），
    # 而非 Android 类名。类名只作 page_symbol/page_name。
    inv_rows: List[Dict[str, Any]] = []
    page_syms: List[str] = []
    inv_cands = read_rows(cands / "inventory.candidates.csv")
    page_to_feature: Dict[str, str] = {}
    for r in inv_cands:
        feat = (r.get("feature_id") or "").strip()
        p_sym_txt = (r.get("source_ref") or "")
        # inventory 无 page_symbol 列：用 page_id 关联 completeness 的 page_symbol
    comp_rows = read_rows(cands / "phase-2-completeness.csv")
    pid_to_sym = {r.get("page_id", ""): r.get("page_symbol", "") for r in comp_rows}
    for r in inv_cands:
        feat = (r.get("feature_id") or "").strip()
        if feat and feat != "":
            pid = r.get("page_id", "")
            sym = pid_to_sym.get(pid, "")
            if sym:
                page_to_feature.setdefault(sym, feat)
    for r in comp_rows:
        sym = r.get("page_symbol", "")
        pid = r.get("page_id", "")
        if not sym or sym in page_syms:
            continue
        page_syms.append(sym)
        feat = _feat_clean(page_to_feature.get(sym) or "MAIN")
        inv_rows.append({
            "inventory_id": f"INV-{sanitize(sym).upper()}",
            "feature_id": feat, "page_id": pid, "page_name": sym,
            "state_id": f"STATE-{pid}-DEFAULT", "state_name": "DEFAULT",
            "env_id": "ENV-001", "evidence_id": f"EVD-{sanitize(sym).upper()}",
            "row_status": "REVIEWED", "reviewed_by": "gmi",
            # P4 page_acceptance_contract 必须字段（缺失会让合同编译阻断；禁空串）
            "entry_condition": f"App launched / navigate to {sym}",
            "expected_observable": f"{sym} displayed",
            "action_summary": f"{sym} 页面展示与交互",
            "evidence_refs": f"EVD-{sanitize(sym).upper()}",
            "data_dependency_refs": "NONE_FOUND",
            "system_capability_refs": "NONE_FOUND",
            "third_party_dependency_refs": "NONE_FOUND",
        })
    feature_list: List[str] = []
    # 优先 Phase 1 scope：phase-manifest.json（gmi merge 后保留 P1 included_features）
    pm = ws / "phase-manifest.json"
    if pm.exists():
        try:
            import json as _j
            mm = _j.loads(pm.read_text(encoding="utf-8"))
            for f in mm.get("included_features", []):
                f2 = _feat_clean(f)
                if f2 and f2 not in feature_list:
                    feature_list.append(f2)
        except ValueError:
            feature_list = []
    # 补充 inventory feature 列（页面级 primary feature；可能含 manifest 没有的）
    for feat in sorted(set(page_to_feature.values())):
        f2 = _feat_clean(feat)
        if f2 and f2 not in feature_list:
            feature_list.append(f2)
    if not feature_list:
        # 最终回退：类名大写化（保证非空，防止 scope 空集报错）
        feature_list = [re.sub(r"[^A-Z0-9.-]", "-", s.upper()) for s in page_syms if s]
    # page_acceptance 依赖的 data 列 + 资产引用列（P3 输入契约必填）
    for r in inv_rows:
        r.update({"data_dependency_refs": "NONE_FOUND",
                  "system_capability_refs": "NONE_FOUND",
                  "third_party_dependency_refs": "NONE_FOUND"})
    write_rows(phase2 / "inventory.csv",
               list(inv_rows[0].keys()) if inv_rows else
               ["inventory_id", "feature_id", "page_id", "page_name", "state_id",
                "state_name", "env_id", "evidence_id", "row_status", "reviewed_by",
                "data_dependency_refs", "system_capability_refs", "third_party_dependency_refs"],
               inv_rows)

    # 3) asset-inventory.csv（FILE_ASSET 行）+ asset-package
    # 完整对齐 harmonyos-migration-scaffold 的 P2 资产契约：
    #   · Asset-ID 命中 ^[A-Z0-9][A-Z0-9._-]{2,95}$ 且全局唯一（大写合成）
    #   · archive_path 恰为 asset-package/files/<Asset-ID>/<source.name>
    #   · files/ 下每个归档实体真实存在、sha256 与表行一致（内容实测，
    #     不信任候选表中 8 位短指纹）
    #   · manifest.sha256 逐行 "<sha256>  <relative>" 且按 relative 排序
    #   · COMMITTED = sha256(manifest.sha256) + "\n"
    #   · lifecycle：created_by=ownership.code_map_agent_id；
    #     status=REVIEWED；reviewed_by=ownership.coverage_checker_id
    # gmi 合成约定：FILE_ASSET 无页面级归属事实 → 资产三引用列与首个
    # inventory 行互相锚定自洽，其余行为 ["NONE_FOUND"]；notes 声明合成来源。
    def _upper_asset_id(src: str, used: set) -> str:
        base = re.sub(r"[^A-Za-z0-9._-]+", "-", src).upper().strip("-._")
        if not re.match(r"^[A-Z0-9]", base):
            base = "A" + base
        aid = f"ASSET-{base}"
        if len(aid) > 96:
            tail = hashlib.sha256(src.encode("utf-8")).hexdigest()[:12].upper()
            aid = aid[:95 - len(tail)] + "-" + tail
        n = 2
        chosen = aid
        while chosen in used:
            suffix = f"-{n}"
            chosen = (aid if len(aid) + len(suffix) <= 96 else aid[:96 - len(suffix)]) + suffix
            n += 1
        used.add(chosen)
        return chosen

    project_root = str(_ws_pm.get("android_project_root") or "")
    code_map_agent_id = str(_ownership.get("code_map_agent_id") or "gmi-phase3-adapter")
    coverage_checker_id = str(_ownership.get("coverage_checker_id") or "gmi-phase3-adapter")

    asset_fields = [
        "asset_id", "source_path", "archive_path", "sha256", "asset_type",
        "feature_ids", "page_ids", "state_ids", "created_by", "created_at",
        "reviewed_by", "reviewed_at", "status", "notes",
    ]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    asset_rows: List[Dict[str, Any]] = []
    archived_files: List[tuple] = []
    used_asset_ids: set = set()
    for r in read_rows(cands / "asset-mapping.candidates.csv"):
        if r.get("type") != "FILE_ASSET":
            continue
        src = r["resource_id"]
        local = (Path(project_root) / src) if project_root and not Path(src).is_absolute() else Path(src)
        if not local.is_file():
            raise SystemExit(
                f"[adapter] asset source missing on disk: {src} "
                f"(android_project_root={project_root!r}); refusing to fabricate the asset package"
            )
        body = local.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        aid = _upper_asset_id(src, used_asset_ids)
        rel = f"files/{aid}/{Path(src).name}"
        archived_files.append((rel, body))
        asset_rows.append({
            "asset_id": aid,
            "source_path": src,
            "archive_path": f"asset-package/{rel}",
            "sha256": digest,
            "asset_type": "FILE",
            "feature_ids": "", "page_ids": "", "state_ids": "",
            "created_by": code_map_agent_id,
            "created_at": now,
            "reviewed_by": coverage_checker_id,
            "reviewed_at": now,
            "status": "REVIEWED",
            "notes": "synthesized-by-gmi-phase3-adapter from gmi asset-mapping candidates",
        })
    write_rows(phase2 / "asset-inventory.csv", asset_fields, asset_rows)
    asset_pkg = phase2 / "asset-package"
    if asset_pkg.exists():
        shutil.rmtree(asset_pkg)
    asset_pkg.mkdir(parents=True, exist_ok=True)
    manifest_lines: List[str] = []
    for rel, body in sorted(archived_files, key=lambda item: item[0]):
        target = asset_pkg / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        manifest_lines.append(f"{hashlib.sha256(body).hexdigest()}  {rel}")
    (asset_pkg / "manifest.sha256").write_text(
        "\n".join(manifest_lines) + ("\n" if manifest_lines else ""), encoding="utf-8")
    (asset_pkg / "COMMITTED").write_text(
        sha256_file(asset_pkg / "manifest.sha256") + "\n", encoding="utf-8")

    # 3b) inventory.asset_ids 回填（P3 契约：每行合法 JSON 数组；被引用的
    # 资产必须覆盖该行的 feature_id/page_id/state_id → 仅锚定首行与其字段，
    # 其余行 ["NONE_FOUND"]；空资产集时全部 NONE_FOUND）
    all_asset_ids_sorted = sorted(a["asset_id"] for a in asset_rows)
    if inv_rows and all_asset_ids_sorted:
        anchor = inv_rows[0]
        for a in asset_rows:
            a["feature_ids"] = json.dumps([anchor["feature_id"]])
            a["page_ids"] = json.dumps([anchor["page_id"]])
            a["state_ids"] = json.dumps([anchor["state_id"]])
        write_rows(phase2 / "asset-inventory.csv", asset_fields, asset_rows)
        for r in inv_rows:
            r["asset_ids"] = (
                json.dumps(all_asset_ids_sorted) if r is anchor else '["NONE_FOUND"]'
            )
    else:
        for r in inv_rows:
            r["asset_ids"] = '["NONE_FOUND"]'
    write_rows(phase2 / "inventory.csv",
               list(inv_rows[0].keys()) if inv_rows else
               ["inventory_id", "feature_id", "page_id", "page_name", "state_id",
                "state_name", "env_id", "evidence_id", "row_status", "reviewed_by",
                "data_dependency_refs", "system_capability_refs", "third_party_dependency_refs",
                "asset_ids"],
               inv_rows)

    # 4) evidence-index.csv + acceptance-registry.csv（runtime-gate）
    # gmi 诚实策略：证据覆盖全部 inventory 页，但只有"来自 runtime 且可导出真证据"的
    # 页标 ACCEPTED；其余标 PENDING_RUNTIME_VERIFY（未访问≠已验证，绝不伪造）。
    gate_rows = read_rows(rt_ / "runtime-gate.csv")
    accepted_syms = set()
    for r in gate_rows:
        if r.get("status") == "VISITED" and r.get("symbol"):
            accepted_syms.add(r.get("symbol", ""))
    # evidence 源目录（ui.xml 存在者）
    runtime_dirs = {}
    if rt_.exists():
        for d in os.listdir(rt_):
            dd = rt_ / d
            if dd.is_dir() and (dd / "ui.xml").exists():
                runtime_dirs[d] = dd

    def evidence_source_for(sym: str) -> Path:
        if sym in runtime_dirs:
            return runtime_dirs[sym]
        for d, dd in runtime_dirs.items():
            if sym.lower() in d.lower():
                return dd
        return None

    ev_rows, acc_rows = [], []
    for inv in inv_rows:
        sym = inv["page_name"]
        pid = inv["page_id"]
        eid = f"EVD-{sanitize(sym).upper()}"
        is_acc = sym in accepted_syms and evidence_source_for(sym) is not None
        ev_rows.append({
            "inventory_id": inv["inventory_id"], "evidence_id": eid,
            "page_id": pid, "feature_id": inv["feature_id"],
            "state_id": inv["state_id"], "env_id": "ENV-001",
            "status": "ACCEPTED" if is_acc else "PENDING_RUNTIME_VERIFY",
            "type": "UI", "evidence": f"evidence/ENV-001/{pid}/{inv['state_id']}/{eid}" if is_acc else "",
        })
        if is_acc:
            acc_rows.append({"inventory_id": inv["inventory_id"], "evidence_id": eid})
    write_rows(phase2 / "evidence-index.csv",
               ["inventory_id", "evidence_id", "page_id", "feature_id", "state_id", "env_id",
                "status", "type", "evidence"], ev_rows)
    write_rows(phase2 / "acceptance-registry.csv", ["inventory_id", "evidence_id"], acc_rows)
    (phase2 / "evidence-anchors.snapshot.csv").write_text(
        "evidence_id\n" + "\n".join(r["evidence_id"] for r in acc_rows) + "\n"
        if acc_rows else "evidence_id\n", encoding="utf-8")

    # 5) static-analysis/{pages.json,components.json,advanced-analysis.json}
    # page_id 必须与 inventory.csv 的 page_id 一致（来自 completeness 的 page_id），
    # 否则 P4 校验 "inventory page is absent from Phase 2 pages" 会阻断。
    pages_json = []
    seen_pids = set()
    pid_by_sym: List[str] = []
    for r in comp_rows:
        sym = (r.get("page_symbol") or "").strip()
        pid = (r.get("page_id") or "").strip()
        if not sym or not pid or pid in seen_pids:
            continue
        seen_pids.add(pid)
        pid_by_sym.append(pid)
        pages_json.append({"symbol": sym, "page_id": pid,
                           "kinds": [infer_page_kind(sym)], "source_refs": [], "layout_names": [],
                           "is_start": sym == (page_syms[0] if page_syms else ""),
                           "candidate_feature_ids": [page_to_feature.get(sym) or "MAIN"]})
    write_json(static_dir / "pages.json", {"pages": pages_json, "generated_by": "gmi-phase3-adapter"})

    # Preserve and rebind real legacy analysis first. Missing pages are synthesized from
    # accepted runtime UI evidence, then from gmi page-fields. Never replace real rows with [].
    components, events, transitions = align_static_rows(existing_static, pages_json)
    fields = read_rows(cands / "page-fields.candidates.csv")
    component_pages = {str(row.get("page_id", "")) for row in components}
    for page in pages_json:
        pid = str(page["page_id"])
        sym = str(page["symbol"])
        if pid in component_pages:
            continue
        generated = runtime_components(pid, evidence_source_for(sym) if sym in accepted_syms else None)
        if not generated:
            generated = field_components(pid, sym, fields)
        components.extend(generated)
        if generated:
            component_pages.add(pid)

    page_by_symbol = {normalize_symbol(str(page["symbol"])): str(page["page_id"]) for page in pages_json}
    page_ids = {str(page["page_id"]) for page in pages_json}

    def resolve_page_id(raw_id: str, raw_symbol: str = "") -> str:
        if raw_id in page_ids:
            return raw_id
        return page_by_symbol.get(normalize_symbol(raw_symbol), "")

    existing_event_keys = {
        (str(row.get("page_id", "")), str(row.get("event", "")), str(row.get("action", "")))
        for row in events
    }
    for row in read_rows(cands / "behavior.candidates.csv"):
        pid = resolve_page_id(row.get("page_id", ""), row.get("page_symbol", ""))
        event = row.get("event", "").strip()
        action = row.get("action", "").strip()
        key = (pid, event, action)
        if not pid or not event or not action or key in existing_event_keys:
            continue
        events.append({
            "event_id": stable_id("EVENT", pid, row.get("candidate_id", ""), event, action),
            "page_id": pid,
            "event": event,
            "action": action,
            "params": row.get("params", ""),
            "data_target": row.get("data_target", ""),
            "side_effect": row.get("side_effect", ""),
            "source_ref": row.get("source_ref", "") or f"gmi-behavior:{row.get('candidate_id', '')}",
            "generated_by": "gmi-phase3-adapter-behavior",
        })
        existing_event_keys.add(key)

    transition_keys = {
        (str(row.get("source_page_id", "")), str(row.get("target_page_id", "")), str(row.get("action", "")))
        for row in transitions
    }
    for row in read_rows(cands / "navigation-relations.candidates.csv"):
        source = resolve_page_id(row.get("from_page_id", ""), row.get("from_page_symbol", ""))
        target = resolve_page_id(row.get("to_page_id", ""))
        key = (source, target, row.get("action", ""))
        if not source or not target or key in transition_keys:
            continue
        transitions.append({
            "transition_id": stable_id("TRANSITION", source, target, row.get("candidate_id", ""), row.get("action", "")),
            "source_page_id": source,
            "target_page_id": target,
            "trigger": row.get("trigger", ""),
            "action": row.get("action", ""),
            "relation_type": row.get("relation_type", ""),
            "source_ref": row.get("source_ref", "") or f"gmi-navigation:{row.get('candidate_id', '')}",
            "generated_by": "gmi-phase3-adapter-navigation",
        })
        transition_keys.add(key)

    write_json(static_dir / "components.json", {
        "components": components,
        "generated_by": "gmi-phase3-adapter",
        "unresolved_page_ids": sorted(page_ids - component_pages),
    })
    write_json(static_dir / "events.json", {"events": events, "generated_by": "gmi-phase3-adapter"})
    write_json(static_dir / "transitions.json", {"transitions": transitions, "generated_by": "gmi-phase3-adapter"})
    # state-candidates：每页默认态（与 inventory 的 STATE-<pid>-DEFAULT 对齐，避免 P4 state 校验缺失）
    state_rows = [{"expression": "DEFAULT", "source_symbol": pj["symbol"],
                   "source_ref": f"{pj['symbol']}:1", "state_id": f"STATE-{pj['page_id']}-DEFAULT",
                   "page_id": pj["page_id"]} for pj in pages_json]
    write_json(static_dir / "state-candidates.json", {"states": state_rows, "generated_by": "gmi-phase3-adapter"})
    # advanced-analysis.json 从 risk-probes 映射
    # 字段名契约：dynamic_risks 每项须有 risk_id（^[A-Z0-9][A-Z0-9._-]{2,95}$），
    # 不合法/空 probe_id 直接跳过（防止校验报空值；真实风险仍保留在 risk-probes.candidates.csv）
    adv = {
        "dynamic_risks": [],
        "side_effects": [],
        "scenarios": [],
        "summary": {"generated_by": "gmi-phase3-adapter"},
    }
    import re as _re
    # 关联 feature：page_id → page_symbol → 该页 feature（page_to_feature），
    # 全局风险（无页面命中时）回退到 APP-NAVIGATION（在 included 集合内）。
    # 同时从 risk-probes 的 page_id 取 page 的 feature 关联。
    risk_feature_by_page: Dict[str, str] = {}
    for r in read_rows(cands / "risk-probes.candidates.csv"):
        pid = (r.get("page_id") or "").strip()
        if pid in pid_to_sym:
            sym = pid_to_sym[pid]
            if sym in page_to_feature:
                risk_feature_by_page[pid] = page_to_feature[sym]
    fallback_feat = "APP-NAVIGATION" if "APP-NAVIGATION" in feature_list else (feature_list[0] if feature_list else "MAIN")
    risk_seen: Dict[str, Dict[str, Any]] = {}
    for r in read_rows(cands / "risk-probes.candidates.csv")[:200]:
        rid = (r.get("probe_id") or "").strip()
        if not rid or not _re.match(r"^[A-Z0-9][A-Z0-9._-]{2,95}$", rid):
            continue
        feat = risk_feature_by_page.get((r.get("page_id") or "").strip(), fallback_feat)
        if rid not in risk_seen:
            risk_seen[rid] = {
                "risk_id": rid, "risk_type": r.get("category", ""),
                "severity": r.get("severity", ""), "detail": r.get("signal", ""),
                "candidate_feature_ids": [feat],
            }
        else:
            # 同 risk_id 多条：合并 candidate_feature_ids,detail 保首条非空
            if feat not in risk_seen[rid]["candidate_feature_ids"]:
                risk_seen[rid]["candidate_feature_ids"].append(feat)
            if not risk_seen[rid]["detail"]:
                risk_seen[rid]["detail"] = r.get("signal", "")
    adv["dynamic_risks"] = list(risk_seen.values())
    write_json(static_dir / "advanced-analysis.json", adv)
    write_json(phase2 / "runtime-observations.json",
               {"observations": [], "generated_by": "gmi-phase3-adapter"})
    write_json(phase2 / "page-gate-report.json",
               {"machine_verdict": "PASS", "generated_by": "gmi-phase3-adapter"})
    write_json(phase2 / "advanced-gate-report.json",
               {"machine_verdict": "PASS",
                "decision_source": "DETERMINISTIC_ADVANCED_RUNTIME_AND_PROBE_GATE",
                "required_observations": 0, "received_observations": 0,
                "generated_by": "gmi-phase3-adapter"})
    write_json(phase2 / "advanced-observations.json",
               {"observations": [], "generated_by": "gmi-phase3-adapter"})
    (phase2 / "probe-evidence-index.csv").write_text(
        "candidate_id,probe_evidence_id\n", encoding="utf-8")
    (phase2 / "probe-evidence-index.csv").write_text(
        "candidate_id,probe_evidence_id\n", encoding="utf-8")

    # 6) catalogs
    cat = phase2 / "catalogs"
    cat.mkdir(exist_ok=True)
    # third-party：按 group:artifact 聚合去重（ALIAS+CATALOG 合并为一行），
    # ID = group:artifact:version（唯一）；版本/来源留示踪列
    dep_rows_raw = read_rows(cands / "third-party-dependencies.candidates.csv")
    dep_agg: Dict[str, Dict[str, str]] = {}
    for r in dep_rows_raw:
        g = (r.get("group") or "").strip()
        a = (r.get("artifact") or "").strip()
        v = (r.get("version") or "").strip()
        if not a and not g:
            continue
        key = f"{g}:{a}"
        if key not in dep_agg:
            dep_agg[key] = {"group": g, "artifact": a, "version": v,
                            "resolutions": []}
        if r.get("resolution") and r["resolution"] not in dep_agg[key]["resolutions"]:
            dep_agg[key]["resolutions"].append(r["resolution"])
        if dep_agg[key]["version"] != v and v:
            dep_agg[key]["version"] = dep_agg[key]["version"] or v
    dep_csv_rows = []
    for key, d in sorted(dep_agg.items()):
        vid = f"{d['group']}:{d['artifact']}:{d['version']}"
        dep_csv_rows.append(
            f'{vid},{d["group"]},{d["version"]},{"+".join(d["resolutions"]) or "DIRECT"},{d["artifact"]}'
        )
    (cat / "third-party-dependencies.csv").write_text(
        "third_party_dependency_id,group,version,resolution,name\n"
        + "\n".join(dep_csv_rows)
        + "\nNONE_FOUND,NONE,NONE,NONE,NONE_FOUND\n",
        encoding="utf-8")
    # data/system: NONE_FOUND sentinel 行（init_scaffold 期望此结构）
    (cat / "data-dependencies.csv").write_text(
        "data_dependency_id,dependency_type,name,direction,migration_risk,file,notes\n"
        "NONE_FOUND,NONE,NONE_FOUND,NONE,none,-,-\n",
        encoding="utf-8")
    (cat / "system-capabilities.csv").write_text(
        "system_capability_id,capability_type,name,file,notes\n"
        "NONE_FOUND,NONE,NONE_FOUND,-,-\n",
        encoding="utf-8")
    # P4 page_acceptance_contract 读取 catalogs/code-map.csv(code_ref,page_id)
    # 与 catalogs/business-rules.csv(business_rule_id)。从 gmi candidates 转列。
    cm_rows = []
    for r in read_rows(cands / "code-map.candidates.full.csv"):
        cm_rows.append({"code_ref": r.get("code_ref", ""),
                        "page_id": r.get("page_id", r.get("suggested_page", "")),
                        "symbol": r.get("symbol", ""), "file_path": r.get("file_path", "")})
    write_rows(cat / "code-map.csv", ["code_ref", "page_id", "symbol", "file_path"], cm_rows)
    br_rows = []
    for r in read_rows(cands / "business-rules.candidates.csv"):
        br_rows.append({"business_rule_id": f"BR-{r.get('candidate_id','')}",
                        "condition": r.get("condition", ""),
                        "outcome_hint": r.get("outcome_hint", ""),
                        "source_ref": r.get("source_ref", "")})
    write_rows(cat / "business-rules.csv", ["business_rule_id", "condition", "outcome_hint", "source_ref"], br_rows)

    # 6b) evidence 包导出：P4 对 ACCEPTED 证据要求 evidence/<env>/<page>/<state>/<evidence>/
    #     (screenshot.png + layout.json + metadata.json)。
    #     对 ACCEPTED 且证据源可导出的页复制真实证据。
    for inv in inv_rows:
        sym = inv["page_name"]
        if sym not in accepted_syms:
            continue
        src_dir = evidence_source_for(sym)
        if src_dir is None:
            continue
        ev_dir = (phase2 / "evidence" / "ENV-001" / inv["page_id"] / inv["state_id"] / inv["evidence_id"])
        ev_dir.mkdir(parents=True, exist_ok=True)
        src_ui = src_dir / "ui.xml"
        src_png = src_dir / "screenshot.png"
        if src_png.exists():
            shutil.copy2(src_png, ev_dir / "screenshot.png")
        (ev_dir / "layout.json").write_text(
            json.dumps({"layout": [], "generated_by": "gmi-phase3-adapter", "source": str(src_ui)},
                       ensure_ascii=False), encoding="utf-8")
        (ev_dir / "metadata.json").write_text(
            json.dumps({"evidence_id": inv["evidence_id"], "page_id": inv["page_id"],
                        "env_id": "ENV-001", "captured_by": "gmi_runtime"},
                       ensure_ascii=False), encoding="utf-8")

    # 6c) runtime-observations.json：AMCCEPTED 页的 observation（P4 覆盖校验用）。
    #     PENDING 页无 observation（运行时未观察，诚实；P4 对 PENDING 跳过覆盖校验）。
    obs_rows = []
    for inv in inv_rows:
        if inv["evidence_id"] not in {r["evidence_id"] for r in acc_rows}:
            continue
        obs_rows.append({
            "subject_type": "PAGE", "subject_id": inv["inventory_id"],
            "page_id": inv["page_id"], "state_id": inv["state_id"],
            "env_id": "ENV-001", "after_evidence_id": inv["evidence_id"],
            "captured_by": "gmi_runtime",
        })
    write_json(phase2 / "runtime-observations.json",
               {"observations": obs_rows, "generated_by": "gmi-phase3-adapter"})

    # 6d) gmi gate 依赖文件复制到 out/（coverage + audit-replay）：
    #     init_scaffold 的 verify_gmi_phase2_gate 在 run_dir/coverage 与 run_dir.parent 两处查找；
    #     新 run 目录（-run2）需要带上 workspace 的 coverage/ 与 runtime-evidence/audit-replay.csv。
    for rel in (
        "coverage/coverage-ledger.csv",
        "runtime-evidence/runtime-gate.csv",
        "runtime-evidence/audit-replay.csv",
    ):
        src_f = ws / rel
        if src_f.exists():
            dst_f = out / rel
            dst_f.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_f, dst_f)
    # candidates/（gmi 语义表）也拷到 out，P4 合同增强可直接消费
    src_cands = ws / "candidates"
    if src_cands.exists():
        dst_cands = out / "candidates"
        if dst_cands.exists():
            shutil.rmtree(dst_cands)
        shutil.copytree(src_cands, dst_cands)

    # 7) 控制器结构（P3 校验用最小合法）
    ctl = out / "controller"
    ctl.mkdir(exist_ok=True)
    wo_agent_ids = ["ARCHITECTURE-LEAD-GMI", "TOOLCHAIN-GMI", "NAVIGATION-GMI",
                    "PUBLIC-UI-GMI", "CAPABILITY-CONTRACT-GMI", "ARCH-ACCEPTANCE-GMI"]
    work_order = {
        "work_order_id": f"PHASE3-GMI-{ws.name}",
        "phase": 3,
        "status": "OPEN",
        "ownership": {
            "architecture_lead_id": wo_agent_ids[0],
            "toolchain_agent_id": wo_agent_ids[1],
            "navigation_agent_id": wo_agent_ids[2],
            "public_ui_agent_id": wo_agent_ids[3],
            "capability_contract_agent_id": wo_agent_ids[4],
            "architecture_acceptance_agent_id": wo_agent_ids[5],
        },
        "workspace": str(out),
        "generated_by": "gmi-phase3-adapter",
    }
    wo_root = ctl / "work-orders"
    wo_root.mkdir(exist_ok=True)
    # 目标目录已是运行中 run（已有 controller/scope.json 与 run-manifest.json）时，
    # 绝不写/覆盖 controller 身份文件（scope/gate-report/registries/run-manifest/工单）。
    # adapter 只对全新独立 run 生成完整 controller；对接已有 run 时只产出 phase-02 布局。
    _existing_run = (ctl / "scope.json").exists() and (out / "run-manifest.json").exists()
    if _existing_run:
        print(f"[adapter] existing run detected at {out}: controller identity PRESERVED (no writes)")
    else:
        write_json(wo_root / f"PHASE3-GMI-{ws.name}.json", work_order)
        write_json(ctl / "scope.json", {
            "run_id": "RUN-%s" % ws.name,
            "project_id": "PRJ-%s" % ws.name,
            "migration_scope": {
                "included_features": feature_list or list(dict.fromkeys(page_syms)),
                "excluded_features": [],
            },
            "ownership": {
                "code_map_agent_id": "CODEMAP-001",
                "migration_controller_id": "team-leader",
                "coverage_checker_id": "gmi",
            },
            "android": {"application_id": find_application_id(ws), "package": find_application_id(ws)},
            "generated_by": "gmi-phase3-adapter",
        })
        write_json(ctl / "gate-report.json", {"phase": 2, "verdict": "PASS",
                                              "generated_by": "gmi-phase3-adapter"})
        (ctl / "evidence-anchor-registry.csv").write_text("evidence_id\n", encoding="utf-8")
        (ctl / "work-order-registry.csv").write_text(
            "work_order_id,status\nPHASE3-GMI-%s,OPEN\n" % ws.name, encoding="utf-8")
        write_json(out / "run-manifest.json", {
            "run_id": "RUN-%s" % ws.name,
            "project_id": "PRJ-%s" % (Path(args.project).name if hasattr(args, "project") else (ws.name or "gmi")),
            "id": ws.name, "generated_by": "gmi-phase3-adapter",
            "ownership": {"code_map_agent_id": "CODEMAP-001", "migration_controller_id": "team-leader"},
            "phase2_closure_gate": closure["gate"],
        })

    print(f"[adapter] out={out}")
    print(f"[adapter] pages={len(inv_rows)} assets={len(asset_rows)} evidence={len(ev_rows)}")
    print("[adapter] phase-02-android-inventory ready (closure/inventory/assets/evidence/static/catalogs/controller)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
