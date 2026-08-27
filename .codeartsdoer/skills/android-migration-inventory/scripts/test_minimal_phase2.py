#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 2 双模拟器方案最小测试集（方案第十一节，9 项）。

运行：python3 scripts/test_minimal_phase2.py
不依赖真实模拟器：通过 PATH 注入 fake adb 驱动 lane 执行器，
通过构造工作区驱动 audit / closure / adapter 门禁。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import gmi_runtime as rt  # noqa: E402
import attest_environment  # noqa: E402

PKG = "com.example.app"
FAKE_XML = ('<?xml version="1.0"?><hierarchy>'
            '<node text="首页" bounds="[0,0][540,100]" class="android.widget.TextView"/>'
            '<node text="保存" bounds="[0,100][540,200]" class="android.widget.Button" '
            'clickable="true"/></hierarchy>')

FAKE_ADB = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
serial = ""
if args and args[0] == "-s":
    serial = args[1]; args = args[2:]
def out(s):
    sys.stdout.write(s)
cfg = {}
cfgp = os.environ.get("FAKE_ADB_CONFIG")
if cfgp and os.path.exists(cfgp):
    cfg = json.load(open(cfgp))
dev = cfg.get(serial, {})
xml = os.environ.get("FAKE_UI_XML")
if xml and os.path.exists(xml):
    xml_body = open(xml, encoding="utf-8").read()
else:
    xml_body = """ + repr(FAKE_XML) + """
log = os.environ.get("FAKE_ADB_LOG")
cmd = args
if cmd[:2] == ["shell", "uiautomator"] or cmd[:3] == ["exec-out", "uiautomator"]:
    dcnt = os.environ.get("FAKE_DUMP_COUNT")
    if dcnt:
        with open(dcnt, "a", encoding="utf-8") as f:
            f.write("dump\\n")
if cmd[:2] == ["shell", "uiautomator"]:
    out("UI hierchary dumped to: /sdcard/ui.xml\\n")
elif cmd[:3] == ["exec-out", "uiautomator", "dump"]:
    # B4 一步法：exec-out uiautomator dump /dev/tty 直接在输出里带 xml
    out("UI hierchary dumped to: /dev/tty\\n" + xml_body)
elif cmd[:2] == ["exec-out", "cat"]:
    out(xml_body)
elif cmd and cmd[0] == "pull":
    dst = cmd[2]
    if cmd[1].endswith(".xml"):
        open(dst, "w", encoding="utf-8").write(xml_body)
    else:
        open(dst, "wb").write(b"\\x89PNG\\r\\n\\x1a\\nFAKE")
elif cmd[:2] == ["shell", "dumpsys"]:
    out("topResumedActivity=... u0 com.example.app/.MainActivity")
elif cmd[:3] == ["shell", "wm", "size"]:
    out(dev.get("size", "Physical size: 1080x2400"))
elif cmd[:3] == ["shell", "wm", "density"]:
    out(dev.get("density", "Physical density: 440"))
elif cmd[:2] == ["shell", "getprop"]:
    out(dev.get("getprop:" + cmd[2] if len(cmd) > 2 else "", ""))
elif cmd[:2] == ["shell", "settings"]:
    out(dev.get("font_scale", "1.0"))
elif cmd[:2] == ["shell", "input"]:
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write("input:" + " ".join(cmd[2:]) + "\\n")
elif cmd[:2] == ["shell", "am"]:
    out("Starting: Intent { cmp=com.example.app/.MainActivity }")
elif cmd[:2] == ["shell", "pm"]:
    out("Success")
else:
    out("")
"""


def run_py(script: str, *argv: str, env_extra: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *[str(a) for a in argv]],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(cwd) if cwd else None,
    )


def write_csv(p: Path, fields: list[str], rows: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def read_csv(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def make_tasks(n_ui: int = 0, n_effect: int = 0, n_review: int = 0,
               n_required_extra: int = 0, page: str = "PAGE-X") -> list[dict]:
    tasks = []

    def add(i, mode, tier, ttype, trigger=""):
        tasks.append({
            "task_id": f"RTASK-{i:04d}", "task_type": ttype, "subject_id": f"SUB-{i}",
            "page_id": page, "symbol": "MainActivity",
            "candidate_feature_ids": ["MAIN"], "trigger": trigger or "AUTO_LAUNCH_OR_ROUTE",
            "expected": "confirm", "status": "OPEN", "source_refs": ["app/src/main/F.kt:1"],
            "verification_mode": mode, "review_tier": tier,
        })

    for i in range(n_ui):
        add(i, "RUNTIME_UI", "REVIEW", "VERIFY_STATE_BRANCH")
    for i in range(n_ui, n_ui + n_effect):
        add(i, "RUNTIME_EFFECT", "REVIEW", "VERIFY_SCENARIO")
    for i in range(n_ui + n_effect, n_ui + n_effect + n_review + n_required_extra):
        add(i, "RUNTIME_UI", "REQUIRED", "VERIFY_PAGE_DEFAULT_STATE")
    return tasks


class TmpWs(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="p2test-"))
        self.ws = self.tmp / "ws"
        self.ws.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 通用工作区 ----
    def build_static_ws(self, tasks: list[dict]):
        d = self.ws / "static-analysis"
        d.mkdir(parents=True, exist_ok=True)
        (d / "runtime-tasks.json").write_text(
            json.dumps({"schema_version": 1, "tasks": tasks}, ensure_ascii=False), encoding="utf-8")

    def build_gate_files(self, comp_rows=None, review_gate=True):
        cands = self.ws / "candidates"
        cands.mkdir(parents=True, exist_ok=True)
        (cands / "manifest.sha256").write_text("placeholder\n", encoding="utf-8")
        comp = comp_rows if comp_rows is not None else [
            {"page_symbol": "MainActivity", "page_id": "PAGE-X", "status": "RECORDED"}]
        write_csv(cands / "phase-2-completeness.csv",
                  ["page_symbol", "page_id", "status", "hint"], comp)
        write_csv(cands / "page-fields.candidates.csv",
                  ["page_symbol", "field_label", "page_id"], [])
        write_csv(cands / "field-options.candidates.csv",
                  ["page_symbol", "option_label", "page_id"], [])
        cov = self.ws / "coverage"
        cov.mkdir(parents=True, exist_ok=True)
        write_csv(cov / "coverage-ledger.csv", ["file", "status"], [{"file": "a.kt", "status": "OK"}])
        ev = self.ws / "runtime-evidence"
        ev.mkdir(parents=True, exist_ok=True)
        (ev / "audit-result.json").write_text(json.dumps({"passed": review_gate, "errors": []}),
                                              encoding="utf-8")


class TestQueueSplit(TmpWs):
    """1) Queue A 与 Queue B 无交集，并集等于全部运行任务。"""

    def test_disjoint_and_complete(self):
        tasks = make_tasks(n_ui=6, n_effect=4, n_review=4)
        tasks.append({**tasks[0], "task_id": "RTASK-SRC", "verification_mode": "SOURCE_ONLY"})
        self.build_static_ws(tasks)
        r = run_py("gmi_runtime.py", "--workspace", self.ws, "--split-queues")
        self.assertEqual(r.returncode, 0, r.stderr)
        ev = self.ws / "runtime-evidence"
        qa = json.loads((ev / "runtime-queue-a.json").read_text(encoding="utf-8"))
        qb = json.loads((ev / "runtime-queue-b.json").read_text(encoding="utf-8"))
        ids_a = {t["task_id"] for t in qa["tasks"]}
        ids_b = {t["task_id"] for t in qb["tasks"]}
        self.assertFalse(ids_a & ids_b, "queues must be disjoint")
        runnable = {t["task_id"] for t in tasks if t["verification_mode"] != "SOURCE_ONLY"}
        self.assertEqual(ids_a | ids_b, runnable, "union must equal the runnable set")
        self.assertNotIn("RTASK-SRC", ids_a | ids_b, "SOURCE_ONLY never enters a queue")

    """2) 保存/重启旅程不拆：exclusive 任务独占 lane A；同页非 exclusive 任务
    同 lane 连续（B1 任务级均衡语义：exclusive 不再整页捆绑同页任务）。"""

    def test_journey_stays_in_one_lane(self):
        tasks = [
            {"task_id": "T-OPEN", "task_type": "VERIFY_PAGE_DEFAULT_STATE", "page_id": "PAGE-DOC",
             "symbol": "MainActivity", "trigger": "AUTO_LAUNCH_OR_ROUTE", "source_refs": ["a:1"],
             "verification_mode": "RUNTIME_UI", "review_tier": "REQUIRED"},
            {"task_id": "T-SAVE", "task_type": "VERIFY_EVENT", "page_id": "PAGE-DOC",
             "symbol": "MainActivity", "trigger": "保存", "source_refs": ["a:2"],
             "verification_mode": "RUNTIME_UI", "review_tier": "REQUIRED"},
            {"task_id": "T-RESTART", "task_type": "VERIFY_SCENARIO", "page_id": "PAGE-DOC",
             "symbol": "MainActivity", "trigger": "重启", "source_refs": ["a:3"],
             "verification_mode": "RUNTIME_EFFECT", "review_tier": "REQUIRED"},
            {"task_id": "T-OTHER", "task_type": "VERIFY_PAGE_DEFAULT_STATE", "page_id": "PAGE-Y",
             "symbol": "MainActivity", "trigger": "AUTO_LAUNCH_OR_ROUTE", "source_refs": ["b:1"],
             "verification_mode": "RUNTIME_UI", "review_tier": "REVIEW"},
        ]
        self.build_static_ws(tasks)
        r = run_py("gmi_runtime.py", "--workspace", self.ws, "--split-queues")
        self.assertEqual(r.returncode, 0, r.stderr)
        ev = self.ws / "runtime-evidence"
        qa = json.loads((ev / "runtime-queue-a.json").read_text(encoding="utf-8"))
        qb = json.loads((ev / "runtime-queue-b.json").read_text(encoding="utf-8"))
        lanes = {}
        pos = {}
        for lane, q in (("A", qa), ("B", qb)):
            for i, t in enumerate(q["tasks"]):
                lanes[t["task_id"]] = lane
                pos[t["task_id"]] = i
        # shared-server 独占语义不变：VERIFY_SCENARIO 任务必须在 lane A
        self.assertEqual(lanes["T-RESTART"], "A",
                         "exclusive (VERIFY_SCENARIO) task must own lane A")
        # 同页非 exclusive 任务必须同 lane 且连续（页组整块放置，不拆页）
        self.assertEqual(lanes["T-OPEN"], lanes["T-SAVE"],
                         "same-page non-exclusive tasks must stay in one lane")
        self.assertEqual(abs(pos["T-OPEN"] - pos["T-SAVE"]), 1,
                         "same-page tasks must be adjacent in the lane queue")

    def test_b1_task_level_balance_60_tasks(self):
        """B1 任务级均衡：60 任务 = 10 exclusive（3 页）+ 50 普通任务（10 页）。

        断言：exclusive 全在 lane A；两 lane 任务数差 <= 2（页组贪心均衡的
        确定性目标）；同页任务保持同 lane 且连续（页组整块放置，不拆页）。"""
        tasks = []
        # 10 个 exclusive 任务分布 3 页（4/3/3）
        ex_pages = {"PAGE-EX-1": 4, "PAGE-EX-2": 3, "PAGE-EX-3": 3}
        n = 0
        for page_id, count in ex_pages.items():
            for _ in range(count):
                tasks.append({
                    "task_id": f"T-EX-{n:02d}", "task_type": "VERIFY_SCENARIO",
                    "page_id": page_id, "symbol": "MainActivity",
                    "trigger": "重启", "source_refs": ["e:1"],
                    "verification_mode": "RUNTIME_EFFECT", "review_tier": "REQUIRED"})
                n += 1
        # 其余 50 个非 exclusive 任务分布 10 页（每页 5 个）
        for p in range(10):
            for k in range(5):
                tasks.append({
                    "task_id": f"T-N-{p:02d}-{k}", "task_type": "VERIFY_STATE_BRANCH",
                    "page_id": f"PAGE-N-{p:02d}", "symbol": "MainActivity",
                    "trigger": "AUTO_LAUNCH_OR_ROUTE", "source_refs": [f"n:{p}"],
                    "verification_mode": "RUNTIME_UI", "review_tier": "REVIEW"})
        self.assertEqual(len(tasks), 60)
        self.build_static_ws(tasks)
        r = run_py("gmi_runtime.py", "--workspace", self.ws, "--split-queues")
        self.assertEqual(r.returncode, 0, r.stderr)
        ev = self.ws / "runtime-evidence"
        qa = json.loads((ev / "runtime-queue-a.json").read_text(encoding="utf-8"))
        qb = json.loads((ev / "runtime-queue-b.json").read_text(encoding="utf-8"))
        self.assertEqual(len(qa["tasks"]) + len(qb["tasks"]), 60)

        lanes, pos = {}, {}
        for lane, q in (("A", qa), ("B", qb)):
            for i, t in enumerate(q["tasks"]):
                lanes[t["task_id"]] = lane
                pos[t["task_id"]] = i

        ex_ids = [t["task_id"] for t in tasks if t["task_type"] == "VERIFY_SCENARIO"]
        self.assertEqual(len(ex_ids), 10)
        self.assertTrue(all(lanes[tid] == "A" for tid in ex_ids),
                        "all exclusive tasks must own lane A")

        self.assertLessEqual(abs(len(qa["tasks"]) - len(qb["tasks"])), 2,
                             f"lane balance violated: A={len(qa['tasks'])} B={len(qb['tasks'])}")

        by_page = {}
        for t in tasks:
            by_page.setdefault(t["page_id"], []).append(t["task_id"])
        for page_id, ids in by_page.items():
            page_lanes = {lanes[tid] for tid in ids}
            self.assertEqual(len(page_lanes), 1,
                             f"page {page_id} split across lanes {page_lanes}")
            positions = sorted(pos[tid] for tid in ids)
            self.assertEqual(positions[-1] - positions[0] + 1, len(ids),
                             f"page {page_id} tasks are not contiguous in its lane")


class TestCalibration(TmpWs):
    """3) 两台设备配置不一致时立即阻断。"""

    def test_calibration_blocks_on_mismatch(self):
        profiles = {
            "emu-a": {"wm size": "Physical size: 1080x2400",
                      "wm density": "Physical density: 440",
                      "api": "34", "locale": "zh-CN", "font": "1.0"},
            "emu-b": {"wm size": "Physical size: 1080x2400",
                      "wm density": "Physical density: 320",  # 不一致
                      "api": "34", "locale": "zh-CN", "font": "1.0"},
        }

        def fake_adb(serial, *args):
            p = profiles[serial]
            if args[:2] == ("shell", "wm") and "size" in args:
                return p["wm size"]
            if args[:2] == ("shell", "wm"):
                return p["wm density"]
            if args[:2] == ("shell", "getprop") and len(args) > 2 and "sdk" in args[2]:
                return p["api"]
            if args[:2] == ("shell", "getprop"):
                return p["locale"]
            if args[:2] == ("shell", "settings"):
                return p["font"]
            return ""

        with mock.patch.object(attest_environment, "adb_output", side_effect=fake_adb):
            slots, mismatches = attest_environment.calibrate_capture_slots("emu-a", "emu-b")
        self.assertTrue(mismatches, "density mismatch must be detected")
        self.assertTrue(any("density" in m for m in mismatches))
        self.assertEqual(slots["A"]["density"], "440")
        self.assertEqual(slots["B"]["density"], "320")

    def test_calibration_passes_when_identical(self):
        def fake_adb(serial, *args):
            if args[:2] == ("shell", "wm") and "size" in args:
                return "Physical size: 1080x2400"
            if args[:2] == ("shell", "wm"):
                return "Physical density: 440"
            if args[:2] == ("shell", "getprop") and len(args) > 2 and "sdk" in args[2]:
                return "34"
            if args[:2] == ("shell", "getprop"):
                return "zh-CN"
            if args[:2] == ("shell", "settings"):
                return "1.0"
            return ""

        with mock.patch.object(attest_environment, "adb_output", side_effect=fake_adb):
            slots, mismatches = attest_environment.calibrate_capture_slots("emu-a", "emu-b")
        self.assertEqual(mismatches, [])


class FakeAdbEnv(TmpWs):
    def setUp(self):
        super().setUp()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.adb_path = self.bin / "adb"
        self.adb_path.write_text(FAKE_ADB, encoding="utf-8")
        self.adb_path.chmod(self.adb_path.stat().st_mode | stat.S_IEXEC)
        self.tap_log = self.tmp / "tap.log"
        self.ui_xml_path = self.tmp / "ui.xml"
        self.ui_xml_path.write_text(FAKE_XML, encoding="utf-8")
        self.project = self.tmp / "project"
        (self.project / "app" / "src").mkdir(parents=True)
        # C1 收紧后根页不再无条件 VERIFIED：给 MainActivity 补页面身份特征
        # （page-fields field_label 直配，fake UI 的「首页」即锚点特征）
        cands = self.ws / "candidates"
        cands.mkdir(parents=True, exist_ok=True)
        write_csv(cands / "page-fields.candidates.csv",
                  ["page_symbol", "field_label", "page_id"],
                  [{"page_symbol": "MainActivity", "field_label": "首页",
                    "page_id": "PAGE-X"}])

    def env(self):
        return {
            "PATH": f"{self.bin}:{os.environ.get('PATH', '')}",
            "FAKE_ADB_LOG": str(self.tap_log),
            "FAKE_UI_XML": str(self.ui_xml_path),
        }


class TestLaneRun(FakeAdbEnv):
    """4) 一个 lane 中断后可以从 checkpoint 恢复。"""

    def make_queue(self):
        tasks = [
            {"task_id": "T1", "task_type": "VERIFY_PAGE_DEFAULT_STATE", "journey_id": "J1",
             "page_id": "PAGE-X", "symbol": "MainActivity", "trigger": "AUTO_LAUNCH_OR_ROUTE",
             "source_refs": ["a:1"], "verification_mode": "RUNTIME_UI", "review_tier": "REQUIRED"},
            {"task_id": "T2", "task_type": "VERIFY_EVENT", "journey_id": "J1",
             "page_id": "PAGE-X", "symbol": "MainActivity", "trigger": "保存",
             "source_refs": ["a:2"], "verification_mode": "RUNTIME_UI", "review_tier": "REQUIRED"},
        ]
        queue = {"schema_version": 1, "queue_id": "runtime-queue-a", "lane": "A",
                 "tasks_sha256": "0" * 64, "tasks": tasks}
        qpath = self.ws / "runtime-evidence" / "runtime-queue-a.json"
        qpath.parent.mkdir(parents=True, exist_ok=True)
        qpath.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")
        return qpath

    def run_lane(self, qpath):
        return run_py("gmi_runtime.py", "--workspace", self.ws, "--queue", qpath,
                      "--slot", "A", "--serial", "emu-a", "--project", self.project,
                      "--package", PKG, "--stay", "0.1", env_extra=self.env())

    def test_checkpoint_resume(self):
        qpath = self.make_queue()
        r = self.run_lane(qpath)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        lane = self.ws / "runtime-evidence" / "lane-a"
        gate = read_csv(lane / "lane-runtime-gate.csv")
        self.assertEqual(len(gate), 2)
        self.assertEqual({g["status"] for g in gate}, {"VERIFIED"})
        taps_after_first = self.tap_log.read_text().count("input:tap") if self.tap_log.exists() else 0
        self.assertGreaterEqual(taps_after_first, 1, "T2 trigger tap expected")

        # 模拟中断：T2 结果丢失 -> 删除 checkpoint 中 T2 -> 重跑只补 T2，不重跑 T1
        ckpt = json.loads((lane / "checkpoint.json").read_text(encoding="utf-8"))
        self.assertIn("T1", ckpt["completed"])
        self.assertIn("T2", ckpt["completed"])
        t1_rows_before = len(read_csv(lane / "lane-evidence-index.csv"))
        ckpt["completed"].pop("T2")
        (lane / "checkpoint.json").write_text(json.dumps(ckpt, ensure_ascii=False), encoding="utf-8")
        shutil.rmtree(lane / "T1")  # 若误重跑 T1 会因证据缺失重建目录
        if self.tap_log.exists():
            self.tap_log.unlink()

        r2 = self.run_lane(qpath)
        self.assertEqual(r2.returncode, 0, r2.stderr + r2.stdout)
        gate2 = read_csv(lane / "lane-runtime-gate.csv")
        self.assertEqual(len(gate2), 2, "resumed gate must cover both tasks")
        self.assertFalse((lane / "T1").exists(), "completed T1 must NOT re-execute")
        self.assertTrue((lane / "T2" / "after" / "ui.xml").exists(), "T2 must be re-captured")
        taps_after_resume = self.tap_log.read_text().count("input:tap") if self.tap_log.exists() else 0
        self.assertEqual(taps_after_resume, 1, "only T2's tap should re-run")

    def test_b7_same_serial_across_lanes_refuses_to_start(self):
        """B7：另一 lane 已绑定同一设备 serial 时，本 lane 拒绝启动（非 0 退出）。"""
        qpath = self.make_queue()
        other_meta = self.ws / "runtime-evidence" / "lane-b" / "lane-meta.json"
        other_meta.parent.mkdir(parents=True, exist_ok=True)
        other_meta.write_text(json.dumps({
            "schema_version": 1, "lane": "B", "device_serial": "emulator-5554",
            "queue_file": "runtime-evidence/runtime-queue-b.json", "queue_tasks": [],
        }, ensure_ascii=False), encoding="utf-8")
        r = run_py("gmi_runtime.py", "--workspace", self.ws, "--queue", qpath,
                   "--slot", "A", "--serial", "emulator-5554",
                   "--project", self.project, "--package", PKG, "--stay", "0.1",
                   env_extra=self.env())
        self.assertNotEqual(r.returncode, 0,
                            "lane A must refuse to start on lane B's serial")
        self.assertIn("REFUSING TO START", r.stdout)
        # 拒绝启动不得留下本 lane 的执行痕迹（未写 lane-meta / checkpoint）
        self.assertFalse((self.ws / "runtime-evidence" / "lane-a" / "lane-meta.json").exists(),
                         "refused lane must not write its own lane-meta.json")


class TestJudgeTask(TmpWs):
    """C1 收紧：_judge_task 不再对 MainActivity / 无锚点页无条件 VERIFIED。"""

    def snap(self, xml: str, foreground: str = PKG + "/.MainActivity") -> dict:
        return {"xml": xml, "foreground": foreground, "in_pkg": PKG in foreground}

    def test_main_activity_without_feature_hit_is_unrecognized(self):
        snap = self.snap(FAKE_XML)  # foreground 属目标包，但无任何页面身份特征命中
        status, feats = rt._judge_task(snap, "MainActivity", {}, {}, {})
        self.assertEqual(status, "UNRECOGNIZED",
                         "no page-identity feature hit must NOT verify, even for MainActivity")
        self.assertEqual(feats, [])

    def test_feature_hit_still_verifies(self):
        label_by_page = {"MainActivity": ["首页"]}  # page-fields field_label 直配锚点
        status, feats = rt._judge_task(self.snap(FAKE_XML), "MainActivity", {}, {},
                                       label_by_page)
        self.assertEqual(status, "VERIFIED")
        self.assertIn("首页", feats)

    def test_exited_foreground_still_wins(self):
        snap = self.snap(FAKE_XML, foreground="com.other.app/.MainActivity")
        status, _ = rt._judge_task(snap, "MainActivity", {}, {},
                                   {"MainActivity": ["首页"]})
        self.assertEqual(status, "EXITED")


def build_lane(ws: Path, lane: str, serial: str, tasks: list[dict],
               task_set: list[str] | None = None, extra_serial: str | None = None) -> None:
    lane_dir = ws / "runtime-evidence" / f"lane-{lane.lower()}"
    lane_dir.mkdir(parents=True, exist_ok=True)
    queue_ids = [t["task_id"] for t in tasks]
    (lane_dir / "lane-meta.json").write_text(json.dumps({
        "schema_version": 1, "lane": lane, "device_serial": serial,
        "queue_file": "runtime-evidence/runtime-queue-%s.json" % lane.lower(),
        "queue_tasks": queue_ids,
    }, ensure_ascii=False), encoding="utf-8")
    ev_rows, gate_rows = [], []
    for t in tasks:
        for tag in ("before", "after"):
            d = lane_dir / t["task_id"] / tag
            d.mkdir(parents=True, exist_ok=True)
            (d / "ui.xml").write_text(FAKE_XML, encoding="utf-8")
            (d / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\nFAKE")
            ev_rows.append({
                "task_id": t["task_id"], "page_id": t.get("page_id", "PAGE-X"), "tag": tag,
                "foreground": PKG + "/.MainActivity",
                "ui_sha256": hashlib.sha256((d / "ui.xml").read_bytes()).hexdigest(),
                "png_sha256": hashlib.sha256((d / "screenshot.png").read_bytes()).hexdigest(),
                "screen_resolution": "1080x2400", "screen_density": "440",
                "capture_slot": lane,
                "device_serial": extra_serial or serial,
            })
        gate_rows.append({
            "task_id": t["task_id"], "journey_id": t.get("journey_id", "J"),
            "page_id": t.get("page_id", "PAGE-X"), "symbol": t.get("symbol", "MainActivity"),
            "status": "VERIFIED", "evidence": f"lane-{lane.lower()}/{t['task_id']}/after/ui.xml",
            "capture_slot": lane, "device_serial": extra_serial or serial,
            "verification_mode": t.get("verification_mode", "RUNTIME_UI"),
            "review_tier": t.get("review_tier", "REQUIRED"),
        })
    write_csv(lane_dir / "lane-evidence-index.csv", rt.LANE_EVIDENCE_FIELDS, ev_rows)
    write_csv(lane_dir / "lane-runtime-gate.csv", rt.LANE_GATE_FIELDS, gate_rows)
    if task_set is not None:
        (ws / "runtime-evidence" / "runtime-task-set.json").write_text(
            json.dumps({"schema_version": 1, "task_ids": task_set, "total": len(task_set),
                        "tasks_sha256": "0" * 64}, ensure_ascii=False), encoding="utf-8")


def make_project(tmp: Path) -> Path:
    project = tmp / "proj"
    (project / "app" / "src" / "main" / "res" / "values").mkdir(parents=True, exist_ok=True)
    (project / "app" / "src" / "main" / "res" / "values" / "strings.xml").write_text(
        "<resources><string name=\"app_name\">示例</string></resources>", encoding="utf-8")
    return project


class TestAudit(TmpWs):
    """5) 重复 Task-ID 或错误 serial 会被审计发现。"""

    def audit(self):
        self.project = make_project(self.tmp)
        return run_py("gmi_audit.py", "--project", self.project, "--workspace", self.ws,
                      "--package", PKG)

    def test_duplicate_task_id_across_lanes(self):
        task = {"task_id": "T1", "symbol": "MainActivity"}
        build_lane(self.ws, "A", "emu-a", [task], task_set=["T1"])
        build_lane(self.ws, "B", "emu-b", [dict(task)], task_set=["T1"])
        r = self.audit()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("BOTH lanes", r.stdout)

    def test_wrong_serial_detected(self):
        task = {"task_id": "T1", "symbol": "MainActivity"}
        build_lane(self.ws, "A", "emu-a", [task], task_set=["T1"], extra_serial="emu-z")
        build_lane(self.ws, "B", "emu-b", [{"task_id": "T2", "symbol": "MainActivity"}],
                  task_set=["T1", "T2"])
        r = self.audit()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("device_serial", r.stdout)

    def test_clean_lanes_pass_and_merge(self):
        build_lane(self.ws, "A", "emu-a", [{"task_id": "T1", "symbol": "MainActivity"}],
                  task_set=["T1", "T2"])
        build_lane(self.ws, "B", "emu-b", [{"task_id": "T2", "symbol": "MainActivity"}],
                   task_set=["T1", "T2"])
        r = self.audit()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        ev = self.ws / "runtime-evidence"
        self.assertTrue((ev / "evidence-index.csv").exists())
        self.assertTrue((ev / "runtime-gate.csv").exists())
        self.assertTrue((ev / "audit-replay.csv").exists())
        self.assertEqual(len(read_csv(ev / "runtime-gate.csv")), 2)


class TestClosureThresholds(TmpWs):
    """6) UI 覆盖率 89% 阻断，90% 进入人工审核；7) REQUIRED 未完成阻断。"""

    def run_closure(self, verified_ids: set[str], tasks: list[dict]):
        self.build_static_ws(tasks)
        self.build_gate_files()
        gate = [{"task_id": t["task_id"], "status": "VERIFIED" if t["task_id"] in verified_ids else "NOT_ENTERED"}
                for t in tasks]
        write_csv(self.ws / "runtime-evidence" / "runtime-gate.csv",
                  ["task_id", "status"], gate)
        r = run_py("gmi_closure.py", "--workspace", self.ws)
        closure = json.loads((self.ws / "phase-2-closure.json").read_text(encoding="utf-8"))
        return r, closure

    def test_ui_89_blocks_and_90_passes(self):
        tasks = make_tasks(n_ui=100)  # 100 个 RUNTIME_UI / REVIEW
        ids = {t["task_id"] for t in tasks}
        r89, c89 = self.run_closure(set(sorted(ids)[:89]), tasks)
        self.assertEqual(r89.returncode, 1)
        self.assertEqual(c89["machine_status"], "BLOCKED")
        self.assertEqual(c89["gate"]["ui_verified_pct"], 89.0)

        r90, c90 = self.run_closure(set(sorted(ids)[:90]), tasks)
        self.assertEqual(r90.returncode, 0, r90.stdout)
        self.assertEqual(c90["machine_status"], "READY_FOR_HUMAN_REVIEW")
        self.assertEqual(c90["gate"]["ui_verified_pct"], 90.0)
        self.assertEqual(c90["gate"]["review_unverified_pct"], 10.0)

    def test_required_unfinished_blocks(self):
        tasks = make_tasks(n_review=10)  # 10 个 REQUIRED
        ids = sorted(t["task_id"] for t in tasks)
        r, c = self.run_closure(set(ids[:9]), tasks)  # 9/10 -> 90% 但 REQUIRED 必须全过
        self.assertEqual(r.returncode, 1)
        self.assertEqual(c["machine_status"], "BLOCKED")
        self.assertTrue(any("REQUIRED" in e for e in c["blocking_errors"]))

    def test_source_only_never_inflates(self):
        tasks = make_tasks(n_effect=10) + [
            {"task_id": "RTASK-SRC", "task_type": "RESOLVE_NOTE", "page_id": "PAGE-X",
             "symbol": "MainActivity", "trigger": "AUTO_RESCAN", "source_refs": ["a:1"],
             "verification_mode": "SOURCE_ONLY", "review_tier": "REVIEW"}]
        ids = {t["task_id"] for t in tasks if t["verification_mode"] == "RUNTIME_EFFECT"}
        self.assertEqual(len(ids), 10)
        r, c = self.run_closure(ids, tasks)  # 10/10 EFFECT -> 100%
        self.assertEqual(c["gate"]["effect_total"], 10)
        self.assertEqual(c["gate"]["effect_verified_pct"], 100.0)
        self.assertEqual(c["gate"]["source_only_total"], 1)


class TestPhase3Adapter(TmpWs):
    """8) Phase 3 adapter 在没有人工接受记录时拒绝运行。"""

    def build_ws(self):
        self.build_static_ws(make_tasks(n_review=2))
        self.build_gate_files()
        # adapter 的 application_id 推断三级来源均需 fixture 提供：补
        # phase-manifest.json -> AndroidManifest package（新严版不再兜底编造包名）
        proj = self.tmp / "proj"
        mf = proj / "app" / "src" / "main" / "AndroidManifest.xml"
        mf.parent.mkdir(parents=True, exist_ok=True)
        mf.write_text('<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                      f'package="{PKG}"/>', encoding="utf-8")
        (self.ws / "phase-manifest.json").write_text(
            json.dumps({"android_project_root": str(proj)}), encoding="utf-8")
        closure = {
            "generator": "gmi_closure", "machine_status": "READY_FOR_HUMAN_REVIEW",
            "gate": {"ui_verified_pct": 100.0, "required_verified_pct": 100.0},
        }
        (self.ws / "phase-2-closure.json").write_text(
            json.dumps(closure, ensure_ascii=False), encoding="utf-8")
        gate = [{"task_id": t["task_id"], "journey_id": "J", "page_id": "PAGE-X",
                 "symbol": "MainActivity", "status": "VERIFIED",
                 "evidence": "lane-a/x/after/ui.xml", "capture_slot": "A",
                 "device_serial": "emu-a", "verification_mode": "RUNTIME_UI",
                 "review_tier": "REQUIRED"} for t in make_tasks(n_review=2)]
        write_csv(self.ws / "runtime-evidence" / "runtime-gate.csv", rt.LANE_GATE_FIELDS, gate)

    def test_refuses_without_acceptance(self):
        self.build_ws()
        r = run_py("gmi_phase3_adapter.py", "--workspace", self.ws,
                   "--out", self.tmp / "run1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("acceptance", (r.stderr + r.stdout).lower())

    def test_refuses_when_closure_changed(self):
        self.build_ws()
        sha = hashlib.sha256((self.ws / "phase-2-closure.json").read_bytes()).hexdigest()
        acc_dir = self.ws / "human-review"
        acc_dir.mkdir(parents=True, exist_ok=True)
        (acc_dir / "phase-2-acceptance.json").write_text(json.dumps({
            "decision": "ACCEPTED", "reviewer_id": "human-1",
            "closure_sha256": "0" * 64,  # 与实际不符 -> 哈希已变化
        }, ensure_ascii=False), encoding="utf-8")
        r = run_py("gmi_phase3_adapter.py", "--workspace", self.ws,
                   "--out", self.tmp / "run2")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("changed", (r.stderr + r.stdout).lower())

    def test_runs_with_valid_acceptance(self):
        self.build_ws()
        sha = hashlib.sha256((self.ws / "phase-2-closure.json").read_bytes()).hexdigest()
        acc_dir = self.ws / "human-review"
        acc_dir.mkdir(parents=True, exist_ok=True)
        (acc_dir / "phase-2-acceptance.json").write_text(json.dumps({
            "decision": "ACCEPTED", "reviewer_id": "human-1",
            "accepted_at": "2026-08-27T00:00:00Z", "closure_sha256": sha,
        }, ensure_ascii=False), encoding="utf-8")
        out = self.tmp / "run3"
        r = run_py("gmi_phase3_adapter.py", "--workspace", self.ws, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        report = json.loads((out / "phase-02-android-inventory" / "closure-report.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(report["final_verdict_source"], "human-review-acceptance")
        self.assertEqual(report["reviewer_id"], "human-1")


class TestMutationQualification(TmpWs):
    """9) UI / 事件导航 / 持久化三类变异资格测试（检测机制有效性）。"""

    def test_ui_mutation_detected_by_hash(self):
        # UI 内容变化 -> 证据哈希重算不一致 -> audit 阻断
        task = {"task_id": "T1", "symbol": "MainActivity"}
        build_lane(self.ws, "A", "emu-a", [task], task_set=["T1"])
        project = make_project(self.tmp)
        # 变异：篡改 after/ui.xml（index 里的哈希不再匹配）
        target = self.ws / "runtime-evidence" / "lane-a" / "T1" / "after" / "ui.xml"
        target.write_text(FAKE_XML.replace("首页", "首页 mutated"), encoding="utf-8")
        r = run_py("gmi_audit.py", "--project", project, "--workspace", self.ws, "--package", PKG)
        self.assertEqual(r.returncode, 1)
        self.assertIn("hash mismatch", r.stdout)

    def test_event_and_persistence_mutation_change_frozen_digest(self):
        import analyze_static_pages as asp
        # 事件/导航变异：trigger(event) 参与 stable_id -> Task-ID 变 -> 冻结集合哈希变
        event_id_a = asp.stable_id("EVENT", "PAGE-X", "Btn", "save", "a.kt:1")
        event_id_b = asp.stable_id("EVENT", "PAGE-X", "Btn", "delete", "a.kt:1")
        task_a = asp.stable_id("RTASK", event_id_a)
        task_b = asp.stable_id("RTASK", event_id_b)
        self.assertNotEqual(task_a, task_b,
                            "event-handler mutation must produce a new Task-ID (frozen set hash changes)")

        # 拆分器冻结哈希随任务集合变化（任务变化 -> tasks_sha256 变化 -> 旧冻结失效）
        ws_static = self.ws / "static-analysis"
        ws_static.mkdir(parents=True, exist_ok=True)
        t_a = [{"task_id": "E1", "task_type": "VERIFY_EVENT", "page_id": "PAGE-X",
                "symbol": "MainActivity", "trigger": "保存", "source_refs": ["a:1"],
                "verification_mode": "RUNTIME_UI", "review_tier": "REQUIRED"},
               {"task_id": "P1", "task_type": "VERIFY_SIDE_EFFECT", "page_id": "PAGE-X",
                "symbol": "MainActivity", "trigger": "DB_WRITE", "source_refs": ["a:2"],
                "verification_mode": "RUNTIME_EFFECT", "review_tier": "REQUIRED"}]
        t_b = [dict(t_a[0], task_id="E1B"), dict(t_a[1], task_id="P1B")]
        (ws_static / "runtime-tasks.json").write_text(
            json.dumps({"tasks": t_a}, ensure_ascii=False), encoding="utf-8")
        r1 = run_py("gmi_runtime.py", "--workspace", self.ws, "--split-queues")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        freeze1 = (self.ws / "runtime-evidence" / "runtime-task-set.json").read_text(encoding="utf-8")
        digest1 = json.loads(freeze1)["tasks_sha256"]
        (ws_static / "runtime-tasks.json").write_text(
            json.dumps({"tasks": t_b}, ensure_ascii=False), encoding="utf-8")
        r2 = run_py("gmi_runtime.py", "--workspace", self.ws, "--split-queues")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        digest2 = json.loads(
            (self.ws / "runtime-evidence" / "runtime-task-set.json").read_text(encoding="utf-8")
        )["tasks_sha256"]
        self.assertNotEqual(digest1, digest2,
                            "changed task set must invalidate the frozen queue digest")

        # 持久化变异：before/after 探针快照哈希必须变化（比较器核心）
        snap1 = hashlib.sha256(json.dumps({"db": {"todos": 1}}, sort_keys=True).encode()).hexdigest()
        snap2 = hashlib.sha256(json.dumps({"db": {"todos": 0}}, sort_keys=True).encode()).hexdigest()
        self.assertNotEqual(snap1, snap2, "persistence mutation must change the probe snapshot hash")


class TestZombieGuard(FakeAdbEnv):
    """僵尸运行防护：跨页快照静止检测（机制1）+ 连续异常熔断（机制2）。

    fake adb 的 dump/截图天然对全部页恒定返回同一 xml/png 内容
    （FAKE_UI_XML 指向固定文件、png 写固定 bytes），正好模拟
    "adb 通道活着、画面冻结"的盲区；无需按页返回不同 xml 的扩展。
    """

    def make_queue(self, tasks: list[dict], lane: str = "A") -> Path:
        queue = {"schema_version": 1, "queue_id": f"runtime-queue-{lane.lower()}",
                 "lane": lane, "tasks_sha256": "0" * 64, "tasks": tasks}
        qpath = self.ws / "runtime-evidence" / f"runtime-queue-{lane.lower()}.json"
        qpath.parent.mkdir(parents=True, exist_ok=True)
        qpath.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")
        return qpath

    @staticmethod
    def task(tid: str, page_id: str, ttype: str = "VERIFY_PAGE_DEFAULT_STATE",
             symbol: str = "MainActivity") -> dict:
        return {"task_id": tid, "task_type": ttype, "journey_id": "J1",
                "page_id": page_id, "symbol": symbol,
                "trigger": "AUTO_LAUNCH_OR_ROUTE", "source_refs": ["a:1"],
                "verification_mode": "RUNTIME_UI", "review_tier": "REQUIRED"}

    def run_lane(self, qpath: Path, *extra: str) -> subprocess.CompletedProcess:
        return run_py("gmi_runtime.py", "--workspace", self.ws, "--queue", qpath,
                      "--slot", "A", "--serial", "emu-a", "--project", self.project,
                      "--package", PKG, "--stay", "0.1", *extra, env_extra=self.env())

    def test_stale_guard_freezes_on_identical_cross_page_snapshots(self):
        """机制1：fake adb 对 >=2 页恒定返回同一内容 -> 跨页快照静止，
        第 6 个真拍触发退出码 3 + FROZEN_DEVICE_SUSPECTED + fuse-state.json；
        checkpoint 已完成任务保留。

        任务全为 symbol=MainActivity 且 xml 含「首页」特征 -> 均 VERIFIED，
        同时验证「VERIFIED 不清零静止计数（冻结也能假 VERIFIED）」。"""
        tasks = [self.task(f"S{i}", "PAGE-X" if i % 2 == 0 else "PAGE-Y")
                 for i in range(8)]
        qpath = self.make_queue(tasks)
        r = self.run_lane(qpath)
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("FROZEN_DEVICE_SUSPECTED", r.stdout)
        self.assertIn("WARNING", r.stdout)  # 达到阈值 50% 先打醒目警告
        lane = self.ws / "runtime-evidence" / "lane-a"
        ckpt = json.loads((lane / "checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(len(ckpt["completed"]), 6,
                         "fuse must trip right after the 6th real capture; "
                         "completed tasks must be preserved")
        fuse = json.loads((lane / "fuse-state.json").read_text(encoding="utf-8"))
        self.assertEqual(fuse["fuse"], "stale_guard")
        self.assertEqual(fuse["exit_code"], 3)
        self.assertIn("PAGE-X", fuse["pages"])
        self.assertIn("PAGE-Y", fuse["pages"])

    def test_fail_breaker_trips_on_consecutive_failures(self):
        """机制2：symbol 无特征命中 -> 全部 UNRECOGNIZED，达到 --fail-streak 5
        熔断退出码 4 + DEVICE_UNRESPONSIVE_SUSPECTED。

        任务同页 PAGE-Z（单页）：机制1 因不跨页不触发，隔离验证机制2。"""
        tasks = [self.task(f"F{i}", "PAGE-Z", symbol="SettingsActivity")
                 for i in range(10)]
        qpath = self.make_queue(tasks)
        r = self.run_lane(qpath, "--fail-streak", "5")
        self.assertEqual(r.returncode, 4, r.stdout + r.stderr)
        self.assertIn("DEVICE_UNRESPONSIVE_SUSPECTED", r.stdout)
        self.assertIn("UNRECOGNIZED", r.stdout)
        lane = self.ws / "runtime-evidence" / "lane-a"
        fuse = json.loads((lane / "fuse-state.json").read_text(encoding="utf-8"))
        self.assertEqual(fuse["fuse"], "fail_breaker")
        self.assertEqual(fuse["consecutive_failures"], 5)
        self.assertIn("emu-a", fuse["device_serial"])

    def test_stale_guard_ignores_same_page_merges(self):
        """机制1 不误伤合法场景：同页连续任务（含 B2 共享快照合并组）即使
        真拍哈希全部相同也不触发（单页不跨页；B2 复制路径本就不计数）。

        8 个独立任务（8 真拍）+ 4 个连续 VERIFY_STATE_BRANCH（B2：组首 1 真拍
        + 3 复制）= 9 个同哈希真拍快照，--stale-streak 3 时 streak 已远超阈值
        但仅 1 个 page_id -> 不触发，lane 正常完成。"""
        tasks = [self.task(f"M{i}", "PAGE-X") for i in range(8)]
        tasks += [self.task(f"B{i}", "PAGE-X", ttype="VERIFY_STATE_BRANCH")
                  for i in range(4)]
        qpath = self.make_queue(tasks)
        r = self.run_lane(qpath, "--stale-streak", "3")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("FROZEN_DEVICE_SUSPECTED", r.stdout)
        lane = self.ws / "runtime-evidence" / "lane-a"
        gate = read_csv(lane / "lane-runtime-gate.csv")
        self.assertEqual(len(gate), 12, "all 12 tasks must complete normally")
        self.assertEqual({g["status"] for g in gate}, {"VERIFIED"})
        self.assertFalse((lane / "fuse-state.json").exists(),
                         "same-page snapshots must never trip the stale guard")
        # B2 合并确实发生（3 个任务走复制路径），排除「无合并所以不触发」假绿
        ckpt = json.loads((lane / "checkpoint.json").read_text(encoding="utf-8"))
        shared = [tid for tid, rec in ckpt["completed"].items()
                  if rec.get("shared_source")]
        self.assertEqual(len(shared), 3, "B2 merge group must share head snapshot")

    def test_resume_clears_fuse_state(self):
        """resume 路径：预置 fuse-state.json -> 启动打印其内容并删除后正常完成。"""
        tasks = [self.task("R1", "PAGE-X"), self.task("R2", "PAGE-X")]
        qpath = self.make_queue(tasks)
        lane = self.ws / "runtime-evidence" / "lane-a"
        lane.mkdir(parents=True, exist_ok=True)
        reason = "FROZEN_DEVICE_SUSPECTED: test fixture fuse"
        (lane / "fuse-state.json").write_text(json.dumps({
            "fuse": "stale_guard", "exit_code": 3, "reason": reason,
            "triggered_at": "2026-08-28T00:00:00Z"}, ensure_ascii=False),
            encoding="utf-8")
        r = self.run_lane(qpath)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(reason, r.stdout, "resume must print previous fuse state")
        self.assertIn("fuse-state cleared", r.stdout)
        self.assertFalse((lane / "fuse-state.json").exists(),
                         "resume must delete stale fuse-state.json")
        gate = read_csv(lane / "lane-runtime-gate.csv")
        self.assertEqual(len(gate), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)