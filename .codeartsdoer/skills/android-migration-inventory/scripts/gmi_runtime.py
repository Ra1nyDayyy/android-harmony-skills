# -*- coding: utf-8 -*-
"""gmi_runtime -- 运行时复核桥 v4：启动 + 级联自动路由 + 权限处理 + 证据差分。

用法：
  python gmi_runtime.py --project <root> --workspace <out> --package <pkg>
        [--activity MainActivity] [--serial emulator-5554]
        [--auto]                  # 级联自动路由（主页→深层页 BFS）
        [--max-hops 80] [--stay 2.0] [--back-after]
        [--visits "文本:秒;文本2:2"]     # 或手工序列
        [--grant-perms]           # 自动 pm grant manifest 权限 + 重启
        [--compare]               # 截图差分 + UI 文本 Jaccard
        [--verbose]

产出 runtime-evidence/<page_id>/ui.xml + screenshot.png
     evidence-index.csv (哈希) + runtime-gate.csv (VISITED/NOT_ENTERED)
     compare.csv (差分) + route-hints.csv (未达页路由建议)
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def run(cmd: List[str], timeout: int = 40) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa
        return f"__ERR__{e}"


def adb(serial: str, *args: str) -> str:
    return run(["adb", "-s", serial, *args])


def sha256f(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_csv(p: Path) -> List[Dict[str, str]]:
    if not p.exists():
        return []
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(p: Path, fields: List[str], rows: List[Dict[str, Any]]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def ui_nodes(xml: str) -> List[Dict[str, Any]]:
    out = []
    for m in re.finditer(r"<node[^>]*?>", xml):
        node = m.group(0)
        text = re.search(r'text="([^"]*)"', node)
        desc = re.search(r'content-desc="([^"]*)"', node)
        bounds = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
        cls = re.search(r'class="([^"]+)"', node)
        tval = (text.group(1) if text else "") or (desc.group(1) if desc else "")
        if not tval or not bounds:
            continue
        x1, y1, x2, y2 = map(int, bounds.groups())
        out.append({"label": tval.strip(), "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
                    "cls": cls.group(1).split(".")[-1] if cls else ""})
    return out


def find_click(xml: str, want: str) -> Optional[Dict[str, Any]]:
    for n in ui_nodes(xml):
        if want.lower() in n["label"].lower():
            return n
    return None


# --- 权限弹窗识别（system dialog: 允许/仅限此应用时可用/不允许）---
PERM_BUTTONS = ("仅在使用该应用时允许", "使用应用时允许", "仅限该应用", "允许", "Allow",
                "仅限此应用", "使用期间允许")
PERM_DENY = "不允许"


def handle_permission_dialog(xml: str, serial: str) -> bool:
    """若当前 UI 是权限弹窗，点击允许类按钮，返回是否处理了。"""
    all_labels = [n["label"].strip() for n in ui_nodes(xml)]
    is_dialog = any(("允许" in l or "Allow" in l or "权限" in l or "permission" in l.lower()) for l in all_labels) and \
                any(("不允许" in l or "Don" in l or "Deny" in l) for l in all_labels)
    if not is_dialog:
        return False
    for btn in PERM_BUTTONS:
        tgt = find_click(xml, btn)
        if tgt:
            adb(serial, "shell", "input", "tap", str(tgt["cx"]), str(tgt["cy"]))
            time.sleep(1.5)
            return True
    return False


# --- 表单填充（缺口1）：find EditText/TextInput -> tap -> input text -> enter ---
def input_nodes(xml: str) -> List[Dict[str, Any]]:
    """可输入节点：class 含 EditText/TextInput 或 hint 非空且可聚焦。"""
    out = []
    for m in re.finditer(r"<node[^>]*?>", xml):
        node = m.group(0)
        cls = re.search(r'class="([^"]+)"', node)
        hint = re.search(r'hint="([^"]*)"', node)
        text = re.search(r'text="([^"]*)"', node)
        c = (cls.group(1) if cls else "")
        if c.split(".")[-1] not in ("EditText", "TextInput"):
            if not (hint and hint.group(1)) and not (text and text.group(1)):
                continue
            if "EditText" not in c and "TextInput" not in c:
                continue
        bounds = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
        if not bounds:
            continue
        x1, y1, x2, y2 = map(int, bounds.groups())
        out.append({"cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
                    "hint": hint.group(1) if hint else "",
                    "text": text.group(1) if text else ""})
    return out


def fill_field(serial: str, xml: str, want_hint: str, value: str) -> bool:
    """在当前 UI 找到 hint 匹配的输入框 -> tap -> input text -> enter。"""
    for n in input_nodes(xml):
        if want_hint and want_hint.lower() not in (n["hint"] or "").lower() and \
           want_hint.lower() not in (n["text"] or "").lower():
            continue
        adb(serial, "shell", "input", "tap", str(n["cx"]), str(n["cy"]))
        time.sleep(1.2)
        esc = value.replace(" ", "%s")
        adb(serial, "shell", "input", "text", esc)
        time.sleep(0.8)
        adb(serial, "shell", "input", "keyevent", "66")  # Enter
        time.sleep(1.2)
        return True
    return False


# --- 无文本可点节点（缺口3）：clickable 且无 label 的节点候补 ---
def tap_targets(xml: str) -> List[Dict[str, Any]]:
    """所有 clickable 节点（含无文本的图标按钮）。"""
    out = []
    for m in re.finditer(r"<node[^>]*?>", xml):
        node = m.group(0)
        cls = re.search(r'class="([^"]+)"', node)
        click = re.search(r'clickable="(\w+)"', node)
        bounds = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', node)
        if not click or click.group(1) != "true" or not bounds:
            continue
        text = re.search(r'text="([^"]*)"', node)
        desc = re.search(r'content-desc="([^"]*)"', node)
        x1, y1, x2, y2 = map(int, bounds.groups())
        lab = (text.group(1) if text else "") or (desc.group(1) if desc else "")
        out.append({"cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
                    "label": lab.strip(),
                    "cls": cls.group(1).split(".")[-1] if cls else ""})
    return out


def pm_clear_and_relaunch(serial: str, pkg: str, act: str) -> None:
    """缺口2：清数据并重启，抓首启流程页。"""
    adb(serial, "shell", "pm", "clear", pkg)
    time.sleep(1.0)
    adb(serial, "shell", "am", "start", "-n", f"{pkg}/.{act}")
    time.sleep(6.0)


def snapshot(serial: str, tag: str, out_dir: Path, page_id: str,
             pkg: str = "") -> Dict[str, Any]:
    d = out_dir / page_id
    d.mkdir(parents=True, exist_ok=True)
    adb(serial, "shell", "uiautomator", "dump", "/sdcard/ui.xml")
    (d / "ui.xml").write_text("", encoding="utf-8")
    adb(serial, "pull", "/sdcard/ui.xml", str(d / "ui.xml"))
    if not (d / "ui.xml").exists() or (d / "ui.xml").stat().st_size < 100:
        d.joinpath("ui.xml").write_text("<?xml version='1.0'?><hierarchy/><!--- empty --->", encoding="utf-8")
    ui_xml = (d / "ui.xml").read_text(encoding="utf-8", errors="replace") if (d / "ui.xml").exists() else ""
    # 处理权限弹窗：循环直到无(允许/不允许)对
    for _ in range(4):
        if not handle_permission_dialog(ui_xml, serial):
            break
        adb(serial, "shell", "uiautomator", "dump", "/sdcard/ui.xml")
        adb(serial, "pull", "/sdcard/ui.xml", str(d / "ui.xml"))
        ui_xml = (d / "ui.xml").read_text(encoding="utf-8", errors="replace")
        time.sleep(0.8)
    adb(serial, "shell", "screencap", "-p", "/sdcard/sc.png")
    adb(serial, "pull", "/sdcard/sc.png", str(d / "screenshot.png"))
    fg = adb(serial, "shell", "dumpsys", "activity", "activities")
    m = re.search(r"topResumedActivity=.*?u0 (\S+)", fg)
    fg_comp = m.group(1) if m else ""
    in_pkg = (pkg in fg_comp) if pkg else True
    return {
        "page_id": page_id, "tag": tag,
        "ui_sha256": sha256f(d / "ui.xml") if (d / "ui.xml").exists() else "",
        "png_sha256": sha256f(d / "screenshot.png") if (d / "screenshot.png").exists() else "",
        "foreground": fg_comp, "in_pkg": in_pkg,
        "xml": ui_xml or "",
    }


def bring_to_front(serial: str, pkg: str, act: str) -> str:
    """am start（不 force-stop）把 app 带回前台；返回当前 foreground。"""
    adb(serial, "shell", "am", "start", "-n", f"{pkg}/.{act}")
    time.sleep(3.0)
    fg = adb(serial, "shell", "dumpsys", "activity", "activities")
    m = re.search(r"topResumedActivity=.*?u0 (\S+)", fg)
    return m.group(1) if m else ""


def load_strings(project: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    targets = []
    res_dir = project / "app" / "src" / "main" / "res"
    if not res_dir.exists():
        res_dir = project / "res"
    if res_dir.exists():
        for sub in ("values", "values-zh", "values-zh-rCN"):
            targets.append(res_dir / sub / "strings.xml")
    for xml in targets:
        if not xml.exists():
            continue
        t = xml.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'<string name="([^"]+)"[^>]*>([^<]+)</string>', t):
            out[m.group(1)] = m.group(2).strip()
    return out


def anchor_for(page_symbol: str, strings: Dict[str, str]) -> List[str]:
    words = re.findall(r"[A-Z][a-z0-9]+", page_symbol)
    words = [w for w in words if len(w) >= 3 and w.lower() not in (
        "screen", "page", "dialog", "view", "activity", "route", "sheet", "fragment")]
    if not words:
        words = re.findall(r"[A-Za-z0-9]{3,}", page_symbol)
    anchors: List[str] = []
    for key, val in strings.items():
        kl = key.lower().lstrip("_")
        for w in words:
            if w.lower() in kl or kl in w.lower():
                if len(val) <= 40 and "%" not in val[:1]:
                    anchors.append(val)
                break
    return list(dict.fromkeys(anchors))


def ui_text_set(xml: str) -> set:
    return {n["label"] for n in ui_nodes(xml)}


def jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if (a | b) else 0.0


def pixel_diff(a_png: Path, b_png: Path) -> Optional[float]:
    try:
        from PIL import Image
        import numpy as np
        ia = Image.open(a_png).convert("L").resize((64, 64))
        ib = Image.open(b_png).convert("L").resize((64, 64))
        a = np.asarray(ia, dtype=np.float32)
        b = np.asarray(ib, dtype=np.float32)
        return float(np.abs(a - b).mean())
    except Exception:
        return None


def grant_permissions(serial: str, project: Path, pkg: str) -> int:
    """从 AndroidManifest 抓 uses-permission 并 pm grant；返回成功条数。"""
    perms = set()
    for mf in (project / "app" / "src" / "main" / "AndroidManifest.xml",):
        if not mf.exists():
            continue
        t = mf.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'android:name="([^"]+permission[^"]*)"', t):
            perms.add(m.group(1))
    granted = 0
    for perm in sorted(perms):
        r = adb(serial, "shell", "pm", "grant", pkg, perm)
        if "Exception" not in r and "Error" not in r and r.strip():
            granted += 1
    return granted


def sprintf_pid(sym: str, pid: str, hops: int) -> str:
    return f"STEP-{hops:02d}-{sym[:36]}"


def main() -> int:
    ap = argparse.ArgumentParser(description="gmi runtime bridge v4")
    ap.add_argument("--project", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--package", required=True)
    ap.add_argument("--activity", default="MainActivity")
    ap.add_argument("--serial", default="emulator-5554")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--visits", default=None)
    ap.add_argument("--max-hops", type=int, default=80)
    ap.add_argument("--stay", type=float, default=2.0)
    ap.add_argument("--back-after", action="store_true")
    ap.add_argument("--grant-perms", action="store_true")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--fill", default=None,
                    help="表单填充：hint:值;hint2:值2（进入页面后自动填）")
    ap.add_argument("--pm-clear", action="store_true",
                    help="先 pm clear 再启动（抓首启流程页 Guide/Welcome）")
    ap.add_argument("--explore", action="store_true",
                    help="探索模式：对无文本可点节点逐级点击（图标按钮），指纹去重防死循环")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    ws = Path(args.workspace)
    out_dir = ws / "runtime-evidence"
    pkg, serial, act = args.package, args.serial, args.activity
    cands = ws / "candidates"

    if args.grant_perms:
        n = grant_permissions(serial, Path(args.project), pkg)
        print(f"[perms] granted {n} permissions for {pkg}")

    if args.pm_clear:
        pm_clear_and_relaunch(serial, pkg, act)
    else:
        adb(serial, "shell", "am", "start", "-n", f"{pkg}/.{act}")
        time.sleep(6)

    ev: List[Dict[str, Any]] = []
    gate_rows: List[Dict[str, Any]] = []
    visited: set = set()

    if args.auto:
        project = Path(args.project)
        strings = load_strings(project)

        seen_pages: Dict[str, str] = {}
        for r in read_csv(cands / "phase-2-completeness.csv"):
            pym = r.get("page_symbol", "")
            if pym and pym not in ("", "MainActivity"):
                seen_pages[pym] = pym
        for r in read_csv(cands / "inventory.candidates.csv"):
            pid = r.get("page_id", "")
            if not pid:
                continue
            m = re.match(r"PAGE-([A-Za-z0-9]+(?:Screen|Page|Activity|Dialog|View|Sheet)?)", pid)
            sym = "".join(w.capitalize() for w in re.findall(r"[A-Za-z]+", m.group(1) if m else "")[:3])
            if not sym:
                sym = m.group(1) if m else pid
            if sym and sym not in seen_pages and sym != "MainActivity":
                seen_pages[sym] = sym
        for r in read_csv(cands / "page-fields.candidates.csv"):
            pym = r.get("page_symbol", "")
            if pym and pym not in ("", "MainActivity"):
                seen_pages.setdefault(pym, pym)
        pages = list(seen_pages.keys())
        print(f"[auto] candidate pages={len(pages)} (strings={len(strings)})")

        home_xml = snapshot(serial, "main0", out_dir, "PAGE-LAUNCH", pkg)["xml"]
        prio = []
        for p in pages:
            for a in anchor_for(p, strings):
                if find_click(home_xml, a):
                    prio.append(p)
                    break
        rest = [p for p in pages if p not in prio]
        pages = prio + rest
        print(f"[auto] priority-anchored={len(prio)}")

        home = snapshot(serial, "main", out_dir, "PAGE-LAUNCH", pkg)
        ev.append({k: v for k, v in home.items() if k != "xml"})
        gate_rows.append({"page_id": "PAGE-LAUNCH", "symbol": act, "status": "VISITED",
                          "evidence": "PAGE-LAUNCH/ui.xml"})
        visited.add(act)
        cur_xml = home["xml"]

        # 主页可点旅程（抓证据兜底；tab 间切换不需 BACK，back 会退出 app）
        for lab in ("待办事项", "日历视图", "进展"):
            tgt = find_click(home_xml, lab)
            if tgt:
                adb(serial, "shell", "input", "tap", str(tgt["cx"]), str(tgt["cy"]))
                time.sleep(args.stay + 1.5)
                pid = f"TAB-{re.sub(r'[^A-Za-z0-9]', '', lab)[:20]}"
                snap = snapshot(serial, lab, out_dir, pid, pkg)
                if snap["in_pkg"]:
                    ev.append({k: v for k, v in snap.items() if k != "xml"})
                    gate_rows.append({"page_id": pid, "symbol": lab, "status": "VISITED",
                                      "evidence": f"{pid}/ui.xml"})
                    visited.add(lab)
                else:
                    ev.append({k: v for k, v in snap.items() if k != "xml"})
                    gate_rows.append({"page_id": pid, "symbol": lab, "status": "EXITED",
                                      "evidence": f"{pid}/ui.xml"})
                    bring_to_front(serial, pkg, act)
                cur_xml = snapshot(serial, "root", out_dir, "PAGE-ROOT", pkg)["xml"]

        # 级联 BFS：每步把「当前 UI 里存在的锚点文本」作为跳转机会
        pending = [p for p in pages if p not in visited]
        hops = 0
        while pending and hops < args.max_hops:
            hops += 1
            clicked = False
            for sym in list(pending):
                if sym in visited:
                    continue
                for a in anchor_for(sym, strings):
                    tgt = find_click(cur_xml, a)
                    if tgt:
                        adb(serial, "shell", "input", "tap", str(tgt["cx"]), str(tgt["cy"]))
                        time.sleep(args.stay + 1.5)
                        pid = f"STEP-{hops:02d}-{re.sub(r'[^A-Za-z0-9]', '', sym)[:30]}"
                        snap = snapshot(serial, sym, out_dir, pid, pkg)
                        if snap["in_pkg"]:
                            gate_rows.append({"page_id": pid, "symbol": sym, "status": "VISITED",
                                              "evidence": f"{pid}/ui.xml"})
                            visited.add(sym)
                        else:
                            # 掉出 app（点到了别处/退出）：标 EXITED，绝不当作到达
                            gate_rows.append({"page_id": pid, "symbol": sym, "status": "EXITED",
                                              "evidence": f"{pid}/ui.xml"})
                            bring_to_front(serial, pkg, act)
                            cur_xml = snapshot(serial, "root", out_dir, "PAGE-ROOT", pkg)["xml"]
                            break
                        ev.append({k: v for k, v in snap.items() if k != "xml"})
                        cur_xml = snap["xml"]
                        adb(serial, "shell", "input", "keyevent", "4")
                        time.sleep(2.0)
                        back_snap = snapshot(serial, "back", out_dir, "PAGE-BACK", pkg)
                        ev.append({k: v for k, v in back_snap.items() if k != "xml"})
                        if not back_snap["in_pkg"]:
                            # BACK 把 app 退到桌面了：拉回来
                            fg = bring_to_front(serial, pkg, act)
                        back2 = snapshot(serial, "root2", out_dir, "PAGE-ROOT", pkg)
                        if back2["in_pkg"]:
                            cur_xml = back2["xml"]
                        else:
                            cur_xml = back_snap["xml"]
                        print(f"[auto] {hops}. {sym} -> visited")
                        clicked = True
                        break
                if clicked:
                    break
            if not clicked:
                break

        unreach = [p for p in pending if p not in visited]
        print(f"[auto] finished. visited={len(visited)} not_entered={len(unreach)}")
        for p in unreach:
            gate_rows.append({"page_id": "", "symbol": p, "status": "NOT_ENTERED",
                              "evidence": "(route hints below)"})
        # 路由提示单
        hint_rows = []
        for p in unreach:
            anchors = anchor_for(p, strings)
            hint_rows.append({"symbol": p,
                              "anchors": " / ".join(anchors[:4]) if anchors else "(no anchor; try from screenshots)",
                              "hint": "需人工：从主页逐层点入（锚点文字见下）"})
        write_csv(out_dir / "route-hints.csv", ["symbol", "anchors", "hint"], hint_rows)

        # ---- 表单填充（缺口1）：对当前页填 --fill 指定字段（造数据以便打开详情）----
        if args.fill:
            for item in args.fill.split(";"):
                parts = item.strip().split(":")
                if len(parts) >= 2:
                    hint, val = parts[0].strip(), ":".join(parts[1:]).strip()
                    if fill_field(serial, cur_xml, hint, val):
                        print(f"[fill] '{hint}' <- '{val[:20]}'")
                    else:
                        print(f"[fill] MISS hint='{hint}'")
            time.sleep(1.5)
            cur_xml = snapshot(serial, "afterfill", out_dir, "PAGE-AFTERFILL", pkg)["xml"]

        # ---- 探索模式（缺口3）：对无文本可点节点逐级点击，指纹去重 ----
        if args.explore:
            seen_fp = {hashlib.sha256(cur_xml.encode()).hexdigest()}
            hop = 0
            while hop < args.max_hops:
                hop += 1
                targets = [t for t in tap_targets(cur_xml) if t["cx"] > 0]
                if not targets:
                    break
                acted = False
                for t in targets:
                    adb(serial, "shell", "input", "tap", str(t["cx"]), str(t["cy"]))
                    time.sleep(args.stay + 1.2)
                    snapx = snapshot(serial, f"explore{hop}", out_dir, f"EXPLORE-{hop:02d}-{t['cls']}", pkg)
                    fp = snapx["xml"]
                    fp_hash = hashlib.sha256(fp.encode()).hexdigest()
                    if fp_hash in seen_fp:
                        adb(serial, "shell", "input", "keyevent", "4")
                        time.sleep(1.2)
                        continue
                    seen_fp.add(fp_hash)
                    ev.append({k: v for k, v in snapx.items() if k != "xml"})
                    gate_rows.append({"page_id": snapx["page_id"], "symbol": t["label"] or t["cls"],
                                      "status": "VISITED" if snapx["in_pkg"] else "EXITED",
                                      "evidence": f"{snapx['page_id']}/ui.xml"})
                    print(f"[explore] {hop}. tap {t['label'][:12] or t['cls']} -> {snapx['page_id']} "
                          f"{'VISITED' if snapx['in_pkg'] else 'EXITED'}")
                    acted = True
                    adb(serial, "shell", "input", "keyevent", "4")
                    time.sleep(1.2)
                if not acted:
                    break
                cur_xml = snapshot(serial, "root", out_dir, "PAGE-ROOT", pkg)["xml"]
                print(f"[explore] round {hop} done, targets={len(targets)}")
    else:
        visits = []
        if args.visits:
            for item in args.visits.split(";"):
                parts = item.strip().split(":")
                if len(parts) >= 2:
                    visits.append((parts[0].strip(), float(parts[1]) if parts[1].strip().replace(".", "").isdigit() else 2.0))
        cap = snapshot(serial, "main", out_dir, "PAGE-LAUNCH", pkg)
        ev.append({k: v for k, v in cap.items() if k != "xml"})
        gate_rows.append({"page_id": "PAGE-LAUNCH", "symbol": act, "status": "VISITED",
                          "evidence": "PAGE-LAUNCH/ui.xml"})
        cur_xml = cap["xml"]
        for i, (label, stay) in enumerate(visits, start=1):
            tgt = find_click(cur_xml, label)
            if not tgt:
                print(f"[visit] MISS '{label}'")
                continue
            adb(serial, "shell", "input", "tap", str(tgt["cx"]), str(tgt["cy"]))
            time.sleep(stay + 2.0)
            pid = f"STEP-{i:02d}-{re.sub(r'[^A-Za-z0-9]', '-', label)[:24]}"
            snap = snapshot(serial, label, out_dir, pid)
            ev.append({k: v for k, v in snap.items() if k != "xml"})
            gate_rows.append({"page_id": pid, "symbol": label, "status": "VISITED",
                              "evidence": f"{pid}/ui.xml"})
            cur_xml = snap["xml"]

    write_csv(out_dir / "evidence-index.csv",
              ["page_id", "tag", "foreground", "ui_sha256", "png_sha256"], ev)
    write_csv(out_dir / "runtime-gate.csv",
              ["page_id", "symbol", "status", "evidence"], gate_rows)

    if args.compare:
        comp = []
        prev_ui: Dict[str, set] = {}
        for e in ev:
            pid = e["page_id"]
            d = out_dir / pid
            ui_p = d / "ui.xml"
            png_p = d / "screenshot.png"
            tset = set()
            if ui_p.exists():
                tset = ui_text_set(ui_p.read_text(encoding="utf-8", errors="replace"))
            for opid, oset in prev_ui.items():
                j = jaccard(tset, oset)
                pd = pixel_diff(png_p, out_dir / opid / "screenshot.png") if png_p.exists() else None
                if j < 0.97 or (pd is not None and pd > 12):
                    comp.append({"page_id": pid, "vs": opid,
                                 "text_jaccard": f"{j:.2f}",
                                 "pixel_diff": f"{pd:.1f}" if pd is not None else ""})
            prev_ui[pid] = tset
        write_csv(out_dir / "compare.csv",
                  ["page_id", "vs", "text_jaccard", "pixel_diff"], comp)
        print(f"[compare] distinct-diff rows={len(comp)}")

    v = sum(1 for g in gate_rows if g["status"] == "VISITED")
    ne = sum(1 for g in gate_rows if g["status"] == "NOT_ENTERED")
    print(f"[runtime] visited={v} not_entered={ne} evidence={len(ev)} out={out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
