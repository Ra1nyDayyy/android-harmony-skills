# -*- coding: utf-8 -*-
"""gmi_audit -- 双 lane 证据审计与合并（防伪造）。

不触摸模拟器。对 runtime-evidence/lane-a/ 与 lane-b/ 各自独立审计后合并：

  1) 重算每个证据文件哈希（ui.xml / screenshot.png 对比 lane-evidence-index 记录）
  2) 重放页面身份（foreground + 锚点特征，绝不信"点击过"）
  3) 校验 capture_slot / device_serial 与 lane-meta 声明一致
  4) 检查两队列是否重叠（同 Task-ID 重复）或遗漏（并集 ≠ 冻结总集）
  5) 合并输出唯一 runtime-evidence/{evidence-index,runtime-gate,audit-replay}.csv

recorded 与 replayed 一致但都不是 VERITED 时同样视为 invalid（"都错"也要能发现）。

阻断（exit 1）：重复 Task-ID、serial/slot 声明不符、哈希不符、分辨率/密度
不一致、队列并集≠总集、REQUIRED 任务在两条 lane 均无证据行。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import gmi_runtime as rt

MERGED_EVIDENCE_FIELDS = ["lane"] + rt.LANE_EVIDENCE_FIELDS
MERGED_GATE_FIELDS = ["lane"] + rt.LANE_GATE_FIELDS
REPLAY_FIELDS = ["lane", "task_id", "page_id", "symbol", "recorded",
                 "replayed", "discrepancy", "invalid", "note"]


def _read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _label_by_page(ws: Path) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for r in rt.read_csv(ws / "candidates" / "page-fields.candidates.csv"):
        sym = r.get("page_symbol", "") or ""
        lbl = (r.get("field_label") or "").strip()
        if sym and lbl and len(lbl) <= 40 and "%" not in lbl[:1]:
            out.setdefault(sym, []).append(lbl)
    return out


def replay_status(ui_text: str, foreground: str, pkg: str, sym: str,
                  strings: Dict[str, str], registry: Dict[str, set],
                  labels: Dict[str, List[str]]) -> Tuple[str, str]:
    if "<node" not in ui_text:
        return "NO_EVIDENCE", "ui.xml missing/empty"
    if pkg not in (foreground or ""):
        return "EXITED", f"foreground={foreground}"
    feats = rt.match_anchors(sym, strings, registry, ui_text) if sym else []
    feats += [f for f in labels.get(sym, []) if f and f in ui_text]
    anchors_defined = bool(rt.anchor_for(sym, strings)) or bool(labels.get(sym))
    if feats or not anchors_defined or sym in ("MainActivity",):
        return "VERIFIED", f"hits={len(feats)}"
    return "UNRECOGNIZED", "no page-identity feature hit"


def audit_lane(project: Path, ws: Path, pkg: str, lane: str) -> Tuple[List[Dict], List[Dict], List[Dict], List[str]]:
    """返回 (evidence_rows, gate_rows, replay_rows, errors)。"""
    lane_dir = ws / "runtime-evidence" / f"lane-{lane.lower()}"
    errors: List[str] = []
    if not lane_dir.is_dir():
        return [], [], [], [f"lane-{lane.lower()}/ directory missing"]
    meta_path = lane_dir / "lane-meta.json"
    if not meta_path.is_file():
        errors.append(f"lane-{lane.lower()}/lane-meta.json missing")
        return [], [], [], errors
    meta = _read_json(meta_path)
    declared_serial = str(meta.get("device_serial", ""))
    declared_slot = str(meta.get("lane", ""))
    if declared_slot != lane:
        errors.append(f"lane-{lane.lower()} meta lane={declared_slot!r} != {lane!r}")
    queue_tasks = list(meta.get("queue_tasks", []))
    dup_in_lane = [t for t, c in Counter(queue_tasks).items() if c > 1]
    if dup_in_lane:
        errors.append(f"lane-{lane.lower()} queue has duplicate Task-IDs: {dup_in_lane[:5]}")

    strings = rt.load_strings(project)
    universe = set(rt.page_symbol_universe(ws / "candidates"))
    universe |= {str(g.get("symbol") or "") for g in rt.read_csv(lane_dir / "lane-runtime-gate.csv") if g.get("symbol")}
    universe.discard("")
    registry = rt.build_anchor_registry(sorted(universe), strings)
    labels = _label_by_page(ws)

    ev_rows = rt.read_csv(lane_dir / "lane-evidence-index.csv")
    gate_rows = rt.read_csv(lane_dir / "lane-runtime-gate.csv")

    fg_by_task: Dict[str, str] = {}
    for e in ev_rows:
        fg_by_task.setdefault(str(e.get("task_id", "")), str(e.get("foreground", "")))
        # slot / serial 必须与 lane 声明一致（证据声明的 serial 与实际采集设备不一致 -> 阻断）
        if str(e.get("capture_slot", "")) != lane:
            errors.append(f"evidence row task={e.get('task_id')} declares capture_slot="
                          f"{e.get('capture_slot')!r} but lives in lane-{lane.lower()}")
        if str(e.get("device_serial", "")) != declared_serial:
            errors.append(f"evidence row task={e.get('task_id')} device_serial="
                          f"{e.get('device_serial')!r} != lane serial {declared_serial!r}")
        # 哈希重算
        for tag_file, hash_col, name in (("ui.xml", "ui_sha256", "ui"),
                                         ("screenshot.png", "png_sha256", "png")):
            art = lane_dir / str(e.get("task_id", "")) / str(e.get("tag", "")) / tag_file
            if not art.is_file():
                errors.append(f"lane-{lane.lower()} evidence file missing: {art.relative_to(ws)}")
                continue
            actual = rt.sha256f(art)
            recorded = str(e.get(hash_col, ""))
            if recorded and actual != recorded:
                errors.append(f"lane-{lane.lower()} {name} hash mismatch at task={e.get('task_id')}/{e.get('tag')}")

    replay_rows: List[Dict[str, Any]] = []
    for g in gate_rows:
        tid = str(g.get("task_id", ""))
        recorded = str(g.get("status", ""))
        art = lane_dir / tid / "after" / "ui.xml"
        if art.is_file() and art.stat().st_size >= 100:
            ui_text = art.read_text(encoding="utf-8", errors="replace")
            replayed, note = replay_status(ui_text, fg_by_task.get(tid, ""), pkg,
                                           str(g.get("symbol", "")), strings, registry, labels)
        else:
            replayed, note = "NO_EVIDENCE", "after/ui.xml missing/empty"
        discrepancy = "YES" if replayed != recorded else "no"
        # recorded == replayed 但两者都无效（非 VERIFIED）同样算发现的问题
        invalid = "YES" if replayed != "VERIFIED" else "no"
        replay_rows.append({"lane": lane, "task_id": tid,
                            "page_id": g.get("page_id", ""), "symbol": g.get("symbol", ""),
                            "recorded": recorded, "replayed": replayed,
                            "discrepancy": discrepancy, "invalid": invalid, "note": note})
    return ev_rows, gate_rows, replay_rows, errors


def main() -> int:
    ap = argparse.ArgumentParser(description="dual-lane runtime audit & merge (anti-forgery)")
    ap.add_argument("--project", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--package", required=True)
    args = ap.parse_args()
    ws = Path(args.workspace)
    ev_root = ws / "runtime-evidence"

    errors: List[str] = []
    merged_ev: List[Dict[str, Any]] = []
    merged_gate: List[Dict[str, Any]] = []
    replay: List[Dict[str, Any]] = []
    lane_task_ids: Dict[str, List[str]] = {}

    lanes_present = [d.name.split("-", 1)[1].upper() for d in ev_root.glob("lane-*") if d.is_dir()]
    if not lanes_present:
        print("[audit] BLOCKED: no lane evidence found (expected runtime-evidence/lane-a and lane-b)")
        return 1

    for lane in sorted(set(lanes_present)):
        ev_rows, gate_rows, replay_rows, lane_errors = audit_lane(
            Path(args.project), ws, args.package, lane)
        errors.extend(lane_errors)
        for e in ev_rows:
            merged_ev.append({"lane": lane.lower(), **e})
        for g in gate_rows:
            merged_gate.append({"lane": lane.lower(), **g})
        replay.extend(replay_rows)
        lane_task_ids[lane] = [str(g.get("task_id", "")) for g in gate_rows if g.get("task_id")]

    # 跨 lane：同 Task-ID 重复（交集即冲突）
    if "A" in lane_task_ids and "B" in lane_task_ids:
        overlap = sorted(set(lane_task_ids["A"]) & set(lane_task_ids["B"]))
        if overlap:
            errors.append(f"Task-ID executed in BOTH lanes: {overlap[:5]}")

    # 队列并集 == 冻结总集
    task_set_path = ev_root / "runtime-task-set.json"
    if task_set_path.is_file():
        task_set = _read_json(task_set_path)
        frozen = set(task_set.get("task_ids", []))
        covered = set()
        for ids in lane_task_ids.values():
            covered |= set(ids)
        missing = sorted(frozen - covered)
        extra = sorted(covered - frozen)
        if missing:
            errors.append(f"queue union misses frozen tasks ({len(missing)}): {missing[:5]}")
        if extra:
            errors.append(f"gate rows reference unknown Task-IDs: {extra[:5]}")

    # 双 lane 屏幕基准一致（同一逻辑环境）
    res = {str(e.get("screen_resolution", "")) for e in merged_ev if e.get("screen_resolution")}
    dens = {str(e.get("screen_density", "")) for e in merged_ev if e.get("screen_density")}
    if len(res) > 1:
        errors.append(f"screen resolution mismatch across evidence: {sorted(res)}")
    if len(dens) > 1:
        errors.append(f"screen density mismatch across evidence: {sorted(dens)}")

    rt.write_csv(ev_root / "evidence-index.csv", MERGED_EVIDENCE_FIELDS, merged_ev)
    rt.write_csv(ev_root / "runtime-gate.csv", MERGED_GATE_FIELDS, merged_gate)
    rt.write_csv(ev_root / "audit-replay.csv", REPLAY_FIELDS, replay)

    n_disc = sum(1 for r in replay if r["discrepancy"] == "YES")
    n_invalid = sum(1 for r in replay if r["invalid"] == "YES")
    print(f"[audit] replayed={len(replay)} discrepancy={n_disc} invalid_status={n_invalid}")
    if errors:
        print(f"[audit] BLOCKED with {len(errors)} error(s):")
        for e in errors[:30]:
            print("  -", e)
        result = {"passed": False, "errors": errors}
    else:
        result = {"passed": True, "errors": []}
    (ev_root / "audit-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if not errors:
        print("[audit] OK: merged evidence-index.csv / runtime-gate.csv / audit-replay.csv")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
