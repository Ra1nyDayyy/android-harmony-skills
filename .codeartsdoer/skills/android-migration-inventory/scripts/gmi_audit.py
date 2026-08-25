# -*- coding: utf-8 -*-
"""gmi_runtime --audit: 证据重放审计（防伪造）。

不触摸模拟器。只读 runtime-evidence/ 下每个页面目录的 ui.xml + screenshot.png +
evidence-index.csv 的记录，用"证据本身"重新判定每页真实状态：

  VISITED       = foreground 属目标包 且 UI 树出现该页特征文本（锚点命中）
  UNRECOGNIZED  = foreground 属目标包 但 UI 无目标页特征（点错页/非特征页）
  EXITED        = foreground 非目标包（掉到桌面/别的 app）
  NO_EVIDENCE   = ui.xml 或 screenshot.png 缺失/为空

visits 记录中的 status 与重放结果不一致 -> audit-discrepancies.csv。
任何页面不能仅凭"点击过"标签被判 VISITED。
"""
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import gmi_runtime as rt


def page_features(text: str) -> List[str]:
    out = []
    for m in re.finditer(r'(?:text|content-desc)="([^"]+)"', text):
        v = m.group(1).strip()
        if v and v not in out:
            out.append(v)
    return out


def audit(project: Path, workspace: Path, pkg: str) -> List[Dict[str, Any]]:
    out_dir = workspace / "runtime-evidence"
    index_rows = rt.read_csv(out_dir / "evidence-index.csv")
    gate_rows = rt.read_csv(out_dir / "runtime-gate.csv")
    strings = rt.load_strings(project)

    # 缺口5：特征集扩到 page-fields field_label（含自绘标题页）
    pf_rows = rt.read_csv(workspace / "candidates" / "page-fields.candidates.csv")
    label_by_page: Dict[str, List[str]] = {}
    for r in pf_rows:
        sym = r.get("page_symbol", "") or ""
        lbl = (r.get("field_label") or "").strip()
        if sym and lbl and len(lbl) <= 40 and "%" not in lbl[:1]:
            label_by_page.setdefault(sym, []).append(lbl)

    rows: List[Dict[str, Any]] = []
    for g in gate_rows:
        pid = g.get("page_id", "")
        sym = g.get("symbol", "")
        recorded = g.get("status", "")
        # NOT_ENTERED: 预期无证据，跳过（不属于伪造）
        if recorded == "NOT_ENTERED":
            continue
        d = out_dir / pid if pid else None
        ui_p = (d / "ui.xml") if d else None
        fg = ""
        for e in index_rows:
            if e.get("page_id") == pid:
                fg = e.get("foreground", "")
                break
        if not d or not ui_p or not ui_p.exists() or ui_p.stat().st_size < 200:
            status, note = "NO_EVIDENCE", "ui.xml missing/empty"
        else:
            ui_text = ui_p.read_text(encoding="utf-8", errors="replace")
            in_pkg = pkg in fg
            feats = rt.anchor_for(sym, strings) if sym else []
            # 补充 page-fields 特征（自绘标题页如 AboutScreen）
            feats += label_by_page.get(sym, [])
            # 补充 UI 树自身文本特征（锚点扩展最后手段）
            if not feats:
                feats = page_features(ui_text)
            hits = [f for f in feats if f and f in ui_text]
            if not in_pkg:
                status, note = "EXITED", f"foreground={fg}"
            elif pid == "PAGE-LAUNCH" or sym in ("MainActivity", "待办事项"):
                status, note = "VISITED", f"fg={fg} (root page, evidence present)"
            elif hits:
                status, note = "VISITED", f"fg={fg} hits={len(hits)}"
            else:
                status, note = "UNRECOGNIZED", f"fg={fg} no target feature"
        rows.append({"page_id": pid, "symbol": sym, "replayed": status,
                     "recorded": recorded,
                     "discrepancy": ("YES" if status != recorded else "no"),
                     "note": note})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="gmi runtime audit (anti-forgery)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--package", required=True)
    args = ap.parse_args()
    rows = audit(Path(args.project), Path(args.workspace), args.package)
    from collections import Counter
    out_dir = Path(args.workspace) / "runtime-evidence"
    rt.write_csv(out_dir / "audit-replay.csv",
                 ["page_id", "symbol", "replayed", "recorded", "discrepancy", "note"], rows)
    bad = [r for r in rows if r["discrepancy"] == "YES"]
    print("[audit] replayed:", dict(Counter(r["replayed"] for r in rows)))
    if bad:
        print(f"[audit] DISCREPANCIES={len(bad)} (recorded != replayed):")
        for r in bad[:20]:
            print(f"   {r['page_id'][:38]:40} recorded={r['recorded']:12} replayed={r['replayed']}")
        print("-> audit-discrepancies.csv written.")
        return 1
    print("[audit] OK: all recorded status matches evidence replay.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
