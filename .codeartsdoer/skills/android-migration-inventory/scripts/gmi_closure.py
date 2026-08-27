# -*- coding: utf-8 -*-
"""gmi_closure -- Phase 2 统一 Gate：产出机器终态 READY_FOR_HUMAN_REVIEW（不生成 CLOSED）。

用法：
  python gmi_closure.py --workspace <Phase-2 工作区>

门槛（人工审核准入条件，全部同时满足才输出 READY_FOR_HUMAN_REVIEW）：
  - 静态发现完整率 100%：coverage UNMAPPED=0、completeness MISSING=0（带 hint 也阻断）
  - UI 状态运行验证率 >= 90%（RUNTIME_UI 任务口径）
  - 外部可观察功能验证率 >= 90%（RUNTIME_EFFECT 任务口径；SOURCE_ONLY 不抬高）
  - REQUIRED 任务 100% 运行验证（NOT_ENTERED/UNRECOGNIZED 一律阻断）
  - 证据哈希、页面身份、设备身份错误 = 0（audit-result.json passed）
  - 未验证 REVIEW 项 <= 10%
  - PAGE-NONE 不得作为字段/选项的合法页面归属

机器阶段只能产生 READY_FOR_HUMAN_REVIEW；PASS/CLOSED 由人工审核后写入。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List

UI_GATE_PCT = 90.0
EFFECT_GATE_PCT = 90.0
REVIEW_UNVERIFIED_MAX_PCT = 10.0

UNVERIFIED_REASONS = {
    "NOT_ENTERED": "页面未进入（路由失败或入口未发现）",
    "UNRECOGNIZED": "到达应用但页面身份特征未命中",
    "EXITED": "操作后掉出目标应用",
    "ERROR": "执行器异常",
    "": "任务无运行结果行（未进入 lane 队列或证据合并遗漏）",
}


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
    import csv
    if not p.exists():
        return []
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def pct(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 1) if denominator else 100.0


def main() -> int:
    ap = argparse.ArgumentParser(description="gmi phase-2 unified machine gate")
    ap.add_argument("--workspace", required=True)
    args = ap.parse_args()
    ws = Path(args.workspace)
    cands = ws / "candidates"
    cov = ws / "coverage"
    rt_ = ws / "runtime-evidence"

    errors: List[str] = []

    # 1) 静态发现完整率 100%
    ledger_rows = read_rows(cov / "coverage-ledger.csv")
    gaps = [r for r in ledger_rows if r.get("status") == "GAP"]
    if gaps:
        errors.append(f"UNMAPPED>0: {len(gaps)} gaps (static discovery must be 100%)")

    comp_rows = read_rows(cands / "phase-2-completeness.csv") if (cands / "phase-2-completeness.csv").exists() else []
    missing_rows = [r for r in comp_rows if r.get("status") == "MISSING"]
    if missing_rows:
        # MISSING 即使带 hint 也不得被当作完成（逐项点名 ≠ 验证）
        errors.append(f"completeness MISSING={len(missing_rows)} (hint does not close a gap)")

    # 2) 页面归属：PAGE-NONE 不是合法归属
    known_pages = {r.get("page_id", "") for r in comp_rows if r.get("page_id")}
    known_pages.discard("")
    for name in ("page-fields.candidates.csv", "field-options.candidates.csv"):
        rows = read_rows(cands / name)
        unbound = [r for r in rows if not r.get("page_id")
                   or r.get("page_id") in ("PAGE-NONE",) or r.get("page_id") not in known_pages]
        if unbound:
            errors.append(f"{name} has unbound/PAGE-NONE/unknown page_id rows: {len(unbound)}")

    # 3) 证据链：audit 必须通过（哈希/身份/serial/slot/队列一致性）
    audit_result_path = rt_ / "audit-result.json"
    audit_passed = False
    if audit_result_path.is_file():
        try:
            audit_passed = bool(json.loads(audit_result_path.read_text(encoding="utf-8")).get("passed"))
        except ValueError:
            audit_passed = False
    if not audit_passed:
        errors.append("dual-lane audit not passed (hash / page identity / device identity / queue errors)")

    # 4) 运行任务双口径覆盖率（task 级；SOURCE_ONLY 不进分母）
    tasks_path = ws / "static-analysis" / "runtime-tasks.json"
    if not tasks_path.is_file():
        errors.append("static-analysis/runtime-tasks.json missing (run static analysis first)")
        tasks: List[Dict[str, Any]] = []
    else:
        tasks = json.loads(tasks_path.read_text(encoding="utf-8")).get("tasks", [])
    task_by_id = {str(t.get("task_id", "")): t for t in tasks}
    runnable = [t for t in tasks if t.get("verification_mode") != "SOURCE_ONLY"]

    gate_rows = read_rows(rt_ / "runtime-gate.csv")
    if gate_rows and "task_id" not in gate_rows[0]:
        errors.append("legacy page-level runtime-gate.csv found; rerun with lane queues "
                      "(--split-queues then --queue/--slot)")
    status_by_task: Dict[str, str] = {}
    for g in gate_rows:
        tid = str(g.get("task_id", ""))
        if tid:
            status_by_task[tid] = str(g.get("status", ""))
    unknown_gate_ids = sorted(set(status_by_task) - set(task_by_id))
    if unknown_gate_ids:
        errors.append(f"runtime-gate references unknown Task-IDs: {unknown_gate_ids[:5]}")

    def verified(t: Dict[str, Any]) -> bool:
        return status_by_task.get(str(t.get("task_id", ""))) == "VERIFIED"

    ui_tasks = [t for t in runnable if t.get("verification_mode") == "RUNTIME_UI"]
    effect_tasks = [t for t in runnable if t.get("verification_mode") == "RUNTIME_EFFECT"]
    required_tasks = [t for t in runnable if t.get("review_tier") == "REQUIRED"]
    review_tasks = [t for t in runnable if t.get("review_tier") == "REVIEW"]

    ui_verified = sum(1 for t in ui_tasks if verified(t))
    effect_verified = sum(1 for t in effect_tasks if verified(t))
    required_verified = sum(1 for t in required_tasks if verified(t))
    review_unverified = [t for t in review_tasks if not verified(t)]

    ui_pct = pct(ui_verified, len(ui_tasks))
    effect_pct = pct(effect_verified, len(effect_tasks))
    required_pct_v = pct(required_verified, len(required_tasks))
    review_unverified_pct = pct(len(review_unverified), len(review_tasks))

    if ui_pct < UI_GATE_PCT:
        errors.append(f"UI verification {ui_pct}% < {UI_GATE_PCT}% ({ui_verified}/{len(ui_tasks)})")
    if effect_pct < EFFECT_GATE_PCT:
        errors.append(f"functional (external observable) verification {effect_pct}% < "
                      f"{EFFECT_GATE_PCT}% ({effect_verified}/{len(effect_tasks)})")
    if required_verified < len(required_tasks):
        bad = [str(t.get("task_id")) for t in required_tasks if not verified(t)]
        # REQUIRED 的 NOT_ENTERED / UNRECOGNIZED 必须阻断（100% 门）
        errors.append(f"REQUIRED tasks not 100% verified ({required_verified}/{len(required_tasks)}): "
                      f"{bad[:8]}")
    if review_unverified_pct > REVIEW_UNVERIFIED_MAX_PCT:
        errors.append(f"unverified REVIEW share {review_unverified_pct}% > {REVIEW_UNVERIFIED_MAX_PCT}% "
                      f"({len(review_unverified)}/{len(review_tasks)})")

    unverified_review_items = [
        {
            "task_id": str(t.get("task_id", "")),
            "page_id": str(t.get("page_id", "")),
            "reason": UNVERIFIED_REASONS.get(status_by_task.get(str(t.get("task_id", "")), ""),
                                             status_by_task.get(str(t.get("task_id", "")), "NO_RESULT_ROW")),
            "risk": "low (REVIEW tier)",
            "suggested_human_check": "spot-check on emulator against recorded anchors",
        }
        for t in review_unverified
    ]

    if not (cands / "manifest.sha256").exists():
        errors.append("candidates/manifest.sha256 missing (13 表未固化)")

    machine_status = "READY_FOR_HUMAN_REVIEW" if not errors else "BLOCKED"
    closure = {
        "generator": "gmi_closure",
        "workspace": str(ws),
        "closed_at_machine_gate": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "machine_status": machine_status,
        "gate": {
            "static_discovery_complete": not gaps and not missing_rows,
            "unmapped": len(gaps),
            "completeness_missing": len(missing_rows),
            "audit_passed": audit_passed,
            "ui_total": len(ui_tasks), "ui_verified": ui_verified, "ui_verified_pct": ui_pct,
            "effect_total": len(effect_tasks), "effect_verified": effect_verified,
            "effect_verified_pct": effect_pct,
            "required_total": len(required_tasks), "required_verified": required_verified,
            "required_verified_pct": required_pct_v,
            "review_total": len(review_tasks), "review_unverified": len(review_unverified),
            "review_unverified_pct": review_unverified_pct,
            "source_only_total": len(tasks) - len(runnable),
            "unverified_review_items": unverified_review_items,
        },
        "blocking_errors": errors,
        "artifact_hashes": {
            "candidates_dir_sha256": sha256_dir(cands) if cands.is_dir() else "",
            "coverage_ledger_sha256": sha256_file(cov / "coverage-ledger.csv") if (cov / "coverage-ledger.csv").exists() else "",
            "runtime_evidence_dir_sha256": sha256_dir(rt_) if (rt_ / "evidence-index.csv").exists() else "",
        },
    }
    out = ws / "phase-2-closure.json"
    out.write_text(json.dumps(closure, indent=2, ensure_ascii=False), encoding="utf-8")
    g = closure["gate"]
    if errors:
        print(f"CLOSURE BLOCKED ({len(errors)} error(s)):")
        for e in errors:
            print("  -", e)
        print("->", out)
        return 1
    print(f"MACHINE GATE OK -> READY_FOR_HUMAN_REVIEW: "
          f"ui={g['ui_verified_pct']}% effect={g['effect_verified_pct']}% "
          f"required={g['required_verified_pct']}% review_unverified={g['review_unverified_pct']}%")
    print("->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
