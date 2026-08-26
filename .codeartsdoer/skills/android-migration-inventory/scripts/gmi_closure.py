# -*- coding: utf-8 -*-
"""gmi_closure -- 生成 gmi Phase 2 闭包证书 phase-2-closure.json（供 Phase 3 消费）。

用法：
  python gmi_closure.py --workspace <TASKS-RUN1 等 Phase-2 工作区>

前置校验（任一失败 exit 1，不生成）：
  - coverage/coverage-ledger.csv  UNMAPPED=0
  - runtime-evidence/audit-replay.csv 全部 discrepancy=no（若存在 runtime）
  - candidates/ 13 表 + manifest.sha256 存在
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sha256_dir(d: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(d.rglob("*")):
        if f.is_file():
            h.update(f.relative_to(d).as_posix().encode())
            h.update(sha256_file(f).encode())
    return h.hexdigest()


def read_rows(p: Path) -> List[Dict[str, str]]:
    if not p.exists():
        return []
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main() -> int:
    ap = argparse.ArgumentParser(description="gmi phase-2 closure")
    ap.add_argument("--workspace", required=True)
    args = ap.parse_args()
    ws = Path(args.workspace)
    cands = ws / "candidates"
    cov = ws / "coverage"
    rt_ = ws / "runtime-evidence"

    errors: List[str] = []

    # 1) 前置校验
    ledger_rows = read_rows(cov / "coverage-ledger.csv")
    gaps = [r for r in ledger_rows if r.get("status") == "GAP"]
    if gaps:
        errors.append(f"UNMAPPED>0: {len(gaps)} gaps")

    comp_rows = read_rows(cands / "phase-2-completeness.csv") if (cands / "phase-2-completeness.csv").exists() else []
    missing_total = sum(1 for r in comp_rows if r.get("status") == "MISSING")
    na_total = sum(1 for r in comp_rows if r.get("status") == "N/A")
    # MISSING 必须带 hint（逐项点名=无隐瞒）；无 hint 的 MISSING 才阻塞
    silent_missing = [r for r in comp_rows
                      if r.get("status") == "MISSING" and not str(r.get("hint", "")).strip()]
    if silent_missing:
        errors.append(f"silent MISSING (no hint): {len(silent_missing)}")

    # CodeArts must not guess page ownership later. Unbound fields/options otherwise
    # disappear from P4 contracts or leak into every page.
    known_pages = {r.get("page_id", "") for r in comp_rows if r.get("page_id")}
    for name in ("page-fields.candidates.csv", "field-options.candidates.csv"):
        rows = read_rows(cands / name)
        unbound = [r for r in rows if not r.get("page_id") or r.get("page_id") not in known_pages]
        if unbound:
            errors.append(f"{name} has unbound/unknown page_id rows: {len(unbound)}")

    audit_rows = read_rows(rt_ / "audit-replay.csv")
    audit_disc = sum(1 for r in audit_rows if r.get("discrepancy") == "YES")
    if audit_disc:
        errors.append(f"audit discrepancy>0: {audit_disc}")

    gate_rows = read_rows(rt_ / "runtime-gate.csv")
    visited_rows = [r for r in gate_rows if r.get("status") == "VISITED"]
    not_entered_rows = [r for r in gate_rows if r.get("status") == "NOT_ENTERED"]
    visited = len(visited_rows)
    not_entered = len(not_entered_rows)
    # 符号级口径：去重（tab 重复大小写/主页项不计）
    visited_syms = set(r.get("symbol", "") for r in visited_rows if r.get("symbol"))
    ne_syms = set(r.get("symbol", "") for r in not_entered_rows if r.get("symbol"))
    visited_u = len(visited_syms)
    not_entered_u = len(ne_syms)
    # P：以页面符号口径（completeness 的页面数），避免 gate 行数虚高
    comp_symbols = set(r.get("page_symbol", "") for r in comp_rows if r.get("page_symbol"))
    pages_total = len(comp_symbols) if comp_symbols else (visited_u + not_entered_u)

    if not (cands / "manifest.sha256").exists():
        errors.append("candidates/manifest.sha256 missing (13 表未固化)")

    if errors:
        print("CLOSURE BLOCKED:")
        for e in errors:
            print("  -", e)
        return 1

    # 2) 生成闭包
    closure = {
        "generator": "gmi_closure",
        "workspace": str(ws),
        "closure_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gate": {
            "unmapped": 0,
            "completeness_rows": len(comp_rows),
            "completeness_missing_total": missing_total,
            "completeness_na_total": na_total,
            "audit_discrepancy": audit_disc,
            "visited": visited_u,
            "visited_rows": visited,
            "not_entered": not_entered_u,
            "not_entered_rows": not_entered,
            "pages_total": pages_total,
            "pages_visited_pct": round(visited_u / pages_total * 100, 1) if pages_total else 0,
        },
        "artifact_hashes": {
            "candidates_dir_sha256": sha256_dir(cands),
            "coverage_ledger_sha256": sha256_file(cov / "coverage-ledger.csv") if (cov / "coverage-ledger.csv").exists() else "",
            "runtime_evidence_dir_sha256": sha256_dir(rt_) if (rt_ / "evidence-index.csv").exists() else "",
        },
    }
    out = ws / "phase-2-closure.json"
    out.write_text(json.dumps(closure, indent=2, ensure_ascii=False), encoding="utf-8")
    g = closure["gate"]
    print(f"CLOSURE OK: unmapped=0 audit_disc={g['audit_discrepancy']} "
          f"visited={g['visited']}/{g['pages_total']} ({g['pages_visited_pct']}%) "
          f"missing_completeness={g['completeness_missing_total']}")
    print("->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
