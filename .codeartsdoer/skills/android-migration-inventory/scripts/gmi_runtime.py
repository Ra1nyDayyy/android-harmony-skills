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
import json
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


def start_app(serial: str, pkg: str, act: str) -> str:
    """am start 带组件名回退：.{X} / .activity.{X} / {pkg}.{X}
    （修子包 Activity：markor 的 DocumentActivity 在 .activity 子包，
    直接拼 /.DocumentActivity 会 Error type 3）。"""
    out = ""
    for comp in (f"{pkg}/.{act}", f"{pkg}/.activity.{act}", f"{pkg}/{pkg}.{act}"):
        out = adb(serial, "shell", "am", "start", "-n", comp)
        first = out.strip().splitlines()[0][:70] if out.strip() else "(empty)"
        if "Error" not in out:
            print(f"[start_app] {comp} -> OK: {first}")
            return out
        print(f"[start_app] {comp} -> ERR: {first}")
    return out


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
    invalidate_screen_cache(serial)  # B4：pm clear 后屏幕参数缓存失效重查
    time.sleep(1.0)
    start_app(serial, pkg, act)
    time.sleep(WAIT_COLD_START)  # 冷启动固定值，不随 --wait-scale 缩放


# ---------------------------------------------------------------------------
# B4/B5：adb 精简与等待压缩（统一参数化，避免散落魔法数）
#   * 屏幕参数（wm size / wm density）进程级缓存：首次查询后缓存复用，
#     pm clear（或设备重置）后经 invalidate_screen_cache 失效重查——
#     单 lane 数千次 snapshot 只需各查一次 adb。
#   * uiautomator dump 一步法：exec-out uiautomator dump /dev/tty 直接解析
#     输出，失败自动回退原「dump /sdcard + pull」两步（老设备/老 adb 兼容）。
#   * 等待时长统一为「模块级基准值 × WAIT_SCALE」：CLI --wait-scale 默认 0.5
#     （提速档），1.0 恢复旧行为；外部 import 方（probe 等）默认 1.0 旧基准。
# ---------------------------------------------------------------------------
WAIT_SCALE = 1.0  # main() 由 --wait-scale 覆盖；库内直接调用保持旧行为
WAIT_PERM_RECHECK_BASE = 0.8   # 权限弹窗处理后重查（默认 0.5 档 -> 0.4）
WAIT_DISMISS_PERM_BASE = 1.2   # dismiss 流程权限弹窗点击后（-> 0.6）
WAIT_DISMISS_TAP_BASE = 1.6    # dismiss 确认按钮点击后（-> 0.8）
WAIT_TAP_SETTLE_BASE = 1.5     # lane 内 tap 后稳定等待（stay 之外）（-> ~0.8）
WAIT_BACK_BASE = 2.0           # BACK 键后（-> 1.0）
WAIT_BTF_BASE = 3.0            # bring_to_front 拉前台基准
WAIT_BTF_MIN = 2.0             # bring_to_front 下限（提速目标 2.0；scale=1 恢复 3.0）
WAIT_COLD_START = 6.0          # 冷启动固定值（保首启稳定，不缩放）


def wait_secs(base: float, floor: float = 0.05) -> float:
    """基准等待 × 全局 WAIT_SCALE（--wait-scale），下限 floor 防极端参数。"""
    return max(base * WAIT_SCALE, floor)


_SCREEN_INFO_CACHE: Dict[str, Dict[str, str]] = {}


def invalidate_screen_cache(serial: str = "") -> None:
    """pm clear / 设备重置后调用：该设备屏幕参数缓存失效，下次 snapshot 重查。"""
    if serial:
        _SCREEN_INFO_CACHE.pop(serial, None)
    else:
        _SCREEN_INFO_CACHE.clear()


def dump_ui_xml(serial: str, dst: Path) -> str:
    """uiautomator dump 一步法（B4）：`exec-out uiautomator dump /dev/tty`
    直接解析 stdout 中的 xml；无 xml 标记（老设备/输出异常）自动回退
    原「dump /sdcard + pull」两步。xml 写入 dst 并返回文本。"""
    out = adb(serial, "exec-out", "uiautomator", "dump", "/dev/tty")
    start = -1
    for marker in ("<?xml", "<hierarchy"):
        pos = out.find(marker)
        if pos >= 0 and (start < 0 or pos < start):
            start = pos
    if start >= 0:
        end = out.rfind("</hierarchy>")
        xml = out[start:end + len("</hierarchy>")] if end > start else out[start:]
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(xml + "\n", encoding="utf-8")
        return xml
    # 回退：原两步 dump + pull（保证兼容）
    adb(serial, "shell", "uiautomator", "dump", "/sdcard/ui.xml")
    dst.write_text("", encoding="utf-8")
    adb(serial, "pull", "/sdcard/ui.xml", str(dst))
    return dst.read_text(encoding="utf-8", errors="replace") if dst.exists() else ""


def snapshot(serial: str, tag: str, out_dir: Path, page_id: str,
             pkg: str = "") -> Dict[str, Any]:
    d = out_dir / page_id
    d.mkdir(parents=True, exist_ok=True)
    dump_ui_xml(serial, d / "ui.xml")  # B4：一步法 + 失败回退两步
    if not (d / "ui.xml").exists() or (d / "ui.xml").stat().st_size < 100:
        d.joinpath("ui.xml").write_text("<?xml version='1.0'?><hierarchy/><!--- empty --->", encoding="utf-8")
    ui_xml = (d / "ui.xml").read_text(encoding="utf-8", errors="replace") if (d / "ui.xml").exists() else ""
    # 处理权限弹窗：循环直到无(允许/不允许)对
    for _ in range(4):
        if not handle_permission_dialog(ui_xml, serial):
            break
        ui_xml = dump_ui_xml(serial, d / "ui.xml")
        time.sleep(wait_secs(WAIT_PERM_RECHECK_BASE))  # B5
    adb(serial, "shell", "screencap", "-p", "/sdcard/sc.png")
    adb(serial, "pull", "/sdcard/sc.png", str(d / "screenshot.png"))
    fg = adb(serial, "shell", "dumpsys", "activity", "activities")
    m = re.search(r"topResumedActivity=.*?u0 (\S+)", fg)
    fg_comp = m.group(1) if m else ""
    # 固定屏幕参数（分辨率/密度），记录到证据，供 P4 对齐校验；
    # B4：进程级缓存（首查后复用，pm clear 后经 invalidate_screen_cache 失效重查）
    screen_size = ""
    screen_density = ""
    if pkg:
        cached = _SCREEN_INFO_CACHE.get(serial)
        if cached and cached.get("size") and cached.get("density"):
            screen_size = cached["size"]
            screen_density = cached["density"]
        else:
            size_out = adb(serial, "shell", "wm", "size")
            dm = re.search(r"(\d+x\d+)", size_out)
            screen_size = dm.group(1) if dm else ""
            dens_out = adb(serial, "shell", "wm", "density")
            dm2 = re.search(r"(\d+)", dens_out)
            screen_density = dm2.group(1) if dm2 else ""
            if screen_size and screen_density:
                _SCREEN_INFO_CACHE[serial] = {"size": screen_size,
                                              "density": screen_density}
    in_pkg = (pkg in fg_comp) if pkg else True
    return {
        "page_id": page_id, "tag": tag,
        "ui_sha256": sha256f(d / "ui.xml") if (d / "ui.xml").exists() else "",
        "png_sha256": sha256f(d / "screenshot.png") if (d / "screenshot.png").exists() else "",
        "foreground": fg_comp, "in_pkg": in_pkg,
        "screen_resolution": screen_size, "screen_density": screen_density,
        "xml": ui_xml or "",
    }


def dismiss_startup_dialogs(serial: str, pkg: str, rounds: int = 5) -> int:
    """消解首启拦截对话框（如 Markor 存储说明弹窗「更多信息/确定」、Intro 的下一步/跳过）。
    特征：UI 上存在确认类按钮且可点文本节点很少（对话框形态）。
    权限弹窗（允许/不允许成对）仍交给 handle_permission_dialog，不在此误点。
    返回实际点击的确认按钮次数（调用方可据此决定刷新 UI）。"""
    ok_words = ("确定", "知道了", "好，开始使用", "开始使用", "跳过", "下一步", "完成",
                "OK", "Got it", "SKIP", "NEXT", "DONE",
                # 拒绝/关闭类（markor 第 N 次启动弹评分对话框 RATE!/NO THANKS，
                # 无这些词 dismiss 失效会卡死 BFS 且 RATE!/Give feedback 是
                # 浏览器/Play Store 跳转污染源）
                "NO THANKS", "NOT NOW", "LATER", "否", "以后再说", "暂不",
                "不再提示", "不，谢谢")
    tapped = 0
    for _ in range(rounds):
        adb(serial, "shell", "uiautomator", "dump", "/sdcard/ui.xml")
        xml = adb(serial, "exec-out", "cat", "/sdcard/ui.xml") or ""
        if "<node" not in xml:
            return tapped
        nodes = ui_nodes(xml)
        if any("不允许" in n["label"] for n in nodes):
            handle_permission_dialog(xml, serial)
            time.sleep(wait_secs(WAIT_DISMISS_PERM_BASE))  # B5
            continue
        # 对话框形态：节点少；主页节点远多于此阈值，避免误点主页按钮
        if len(nodes) > 12:
            return tapped
        tgt = None
        for w in ok_words:
            for n in nodes:
                if n["label"].strip() == w:
                    tgt = n
                    break
            if tgt:
                break
        if tgt is None:
            return tapped
        print(f"[dismiss] tap '{tgt['label'][:16]}' ({tgt['cx']},{tgt['cy']}) nodes={len(nodes)}")
        adb(serial, "shell", "input", "tap", str(tgt["cx"]), str(tgt["cy"]))
        tapped += 1
        time.sleep(wait_secs(WAIT_DISMISS_TAP_BASE))  # B5
    return tapped


def ensure_home_ui(serial: str, pkg: str, act: str, loops: int = 3) -> str:
    """确保当前 UI 是文件浏览器主页（BFS 锚点基地），返回最新 dump 的 xml。
    markor 首启序列：存储说明弹窗(dismiss_startup_dialogs 可消解) → WebView 帮助文档页
    （首启默认打开帮助文档，模拟器 WebView 渲染失败显示「出了点问题/请返回并重试」，
    特征=xml 含 android.webkit.WebView 且有文本节点数<8）→ 必须 BACK 一次才到真正的
    文件浏览器主页（锚点基地，溢出按钮 content-desc="选项"）。
    循环≤loops 次：dismiss → dump → 命中 WebView 特征则 BACK+sleep2 继续，否则返回 xml。"""
    xml = ""
    for _ in range(loops):
        dismiss_startup_dialogs(serial, pkg)
        adb(serial, "shell", "uiautomator", "dump", "/sdcard/ui.xml")
        xml = adb(serial, "exec-out", "cat", "/sdcard/ui.xml") or ""
        if "<node" not in xml:
            return xml
        n = len(ui_nodes(xml))
        if "WebView" in xml and n < 8:
            print(f"[ensure_home] WebView help page (text-nodes={n}) -> BACK")
            adb(serial, "shell", "input", "keyevent", "4")
            time.sleep(wait_secs(WAIT_BACK_BASE))  # B5
            continue
        return xml
    return xml


def bring_to_front(serial: str, pkg: str, act: str) -> str:
    """am start（不 force-stop）把 app 带回前台；返回当前 foreground。
    拉回后消解首启拦截弹窗并确保离开 WebView 帮助页回到主页锚点基地；
    start_app 输出 + dumpsys 复查 foreground 必须回到 pkg，未回则重试一次。"""
    start_app(serial, pkg, act)
    time.sleep(max(wait_secs(WAIT_BTF_BASE), WAIT_BTF_MIN))  # B5：提速档 2.0，scale=1 恢复 3.0
    dismiss_startup_dialogs(serial, pkg)
    ensure_home_ui(serial, pkg, act)
    fg = adb(serial, "shell", "dumpsys", "activity", "activities")
    m = re.search(r"topResumedActivity=.*?u0 (\S+)", fg)
    fg_comp = m.group(1) if m else ""
    if pkg not in fg_comp:
        print(f"[bring_to_front] fg={fg_comp} not in {pkg}, retry start_app")
        start_app(serial, pkg, act)
        time.sleep(max(wait_secs(WAIT_BTF_BASE), WAIT_BTF_MIN))  # B5
        dismiss_startup_dialogs(serial, pkg)
        ensure_home_ui(serial, pkg, act)
        fg = adb(serial, "shell", "dumpsys", "activity", "activities")
        m = re.search(r"topResumedActivity=.*?u0 (\S+)", fg)
        fg_comp = m.group(1) if m else ""
    return fg_comp


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


def page_symbol_universe(cands_dir: Path) -> List[str]:
    """候选页符号全集（runtime BFS 与 audit 重放同源）：
    completeness ∪ page-fields 的 page_symbol，lower 归一保留真实写法。
    不含 inventory page_id 反推符号（其大小写失真产生幽灵页）。"""
    seen: Dict[str, str] = {}
    for name in ("phase-2-completeness.csv", "page-fields.candidates.csv"):
        for r in read_csv(Path(cands_dir) / name):
            s = (r.get("page_symbol") or "").strip()
            if s:
                seen.setdefault(s.lower(), s)
    return list(seen.values())


def symbol_page_ids(cands_dir: Path) -> Dict[str, str]:
    """page_symbol -> page_id 映射（completeness ∪ page-fields，排除 PAGE-NONE）。
    P1：NOT_ENTERED 行回填 page_id 用。"""
    out: Dict[str, str] = {}
    for name in ("phase-2-completeness.csv", "page-fields.candidates.csv"):
        for r in read_csv(Path(cands_dir) / name):
            s = (r.get("page_symbol") or "").strip()
            pid = (r.get("page_id") or "").strip()
            if s and pid and pid != "PAGE-NONE":
                out.setdefault(s, pid)
    return out


def build_anchor_registry(symbols: List[str], strings: Dict[str, str]) -> Dict[str, set]:
    """锚点字符串 -> 拥有它的 page_symbol 集合（P2 shared 度判定）。
    某锚点同时是多个 symbol 的锚点 => shared（如'设置'属于所有 *Settings*，
    '文件'属于多个 Dialog），单独命中不构成页面身份证据。"""
    reg: Dict[str, set] = {}
    for s in symbols:
        for a in anchor_for(s, strings):
            reg.setdefault(a, set()).add(s)
    return reg


def ordered_click_anchors(sym: str, strings: Dict[str, str],
                          registry: Dict[str, set]) -> List[str]:
    """BFS 点击触发序（P2）：非 shared 优先，同组长度降序。
    保真路径：全 shared 的 sym（如 SettingsActivity 的可见入口只有'设置'）
    仍可用最长的 shared 锚点触发点击。"""
    anchors = anchor_for(sym, strings)
    uniq = [a for a in anchors if len(registry.get(a, set())) <= 1]
    shared = [a for a in anchors if len(registry.get(a, set())) > 1]
    return sorted(uniq, key=len, reverse=True) + sorted(shared, key=len, reverse=True)


# P2 shared 证据兜底上限：拥有者<=该数的 shared 锚点可作页面身份证据。
# 默认 6，按应用校准（CLI --max-shared-owners 可覆盖；markor 校准值见下）。
# 数据边界（markor 校准）：真页救命锚点 owners∈{1,2,3,6}（如'编辑器设置'=6
# 属 Settings/ActionButton/DocumentEdit/OpenEditor* 家族；'正则表达式搜索'=3）；
# 误配锚点 owners=7（Dialog 家族全量共享同一批 form string：'- 文件和文件夹
# 不会被覆盖'/'空文件'/'创建一个新的文件或文件夹'）——单一家族全量共享的
# 表单串在家族任一对话框上都命中，不构成身份证据。
SHARED_EVIDENCE_MAX_OWNERS = 6


def match_anchors(sym: str, strings: Dict[str, str], registry: Dict[str, set],
                  xml: str) -> List[str]:
    """命中判定（P2，audit 同源）：
    - 非 shared 锚点（>=3 字）命中 = 页面身份证据，优先；
    - 无非 shared 命中时，仅「owners<=SHARED_EVIDENCE_MAX_OWNERS 且 >=4 字」的
      shared 锚点可兜底（跨页面家族共用的特异词）；高共享（owners>上限，
      即单一家族全量共享）锚点不作证据，防 Dialog 家族互相误配。"""
    anchors = anchor_for(sym, strings)
    uniq_hits = [a for a in anchors
                 if len(registry.get(a, set())) <= 1
                 and len(a) >= 3 and a and a in xml]
    if uniq_hits:
        return uniq_hits
    shared = sorted((a for a in anchors if len(registry.get(a, set())) > 1),
                    key=len, reverse=True)
    return [a for a in shared
            if len(a) >= 4
            and len(registry.get(a, set())) <= SHARED_EVIDENCE_MAX_OWNERS
            and a in xml][:2]


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


# ---------------------------------------------------------------------------
# 双 Android 模拟器（双 lane）运行架构：
#   * Phase 2 只启动两个配置一致的 Android 模拟器（A/B capture slots），
#     鸿蒙模拟器只在 Phase 4 启动。
#   * 分配单位是"运行旅程"（journey）：同一页面同一数据状态下连续操作
#     必须在同一台模拟器完成，导航/保存/重启等完整链路不得拆开。
#   * 使用共享服务器账号或共享远程数据的任务只允许独占通道（lane A）。
#   * 队列生成后计算哈希并冻结，重跑保持相同分配；同一 Task-ID 不得出现
#     在两个队列。
# ---------------------------------------------------------------------------

EXCLUSIVE_TASK_TYPES = {"VERIFY_SCENARIO", "VERIFY_SIDE_EFFECT"}

LANE_EVIDENCE_FIELDS = [
    "task_id", "page_id", "tag", "foreground", "ui_sha256", "png_sha256",
    "screen_resolution", "screen_density", "capture_slot", "device_serial",
]

LANE_GATE_FIELDS = [
    "task_id", "journey_id", "page_id", "symbol", "status", "evidence",
    "capture_slot", "device_serial", "verification_mode", "review_tier",
]


def _read_json(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8"))


def _write_json_atomic(p: Path, obj: Any) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def load_runtime_tasks(ws: Path, tasks_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    p = tasks_path if tasks_path else (ws / "static-analysis" / "runtime-tasks.json")
    if not p.exists():
        raise SystemExit(f"runtime-tasks.json missing: {p}")
    data = _read_json(p)
    return list(data.get("tasks", []))


def _tasks_digest(task_ids: List[str]) -> str:
    h = hashlib.sha256()
    for tid in task_ids:
        h.update(tid.encode("utf-8"))
        h.update(b"\n")
    return h.hexdigest()


def split_queues(ws: Path, tasks_path: Optional[Path] = None) -> int:
    """把冻结的运行任务拆成 Queue A / Queue B（确定性）。

    B1 任务级均衡（不再按 exclusive journey 整页捆绑）：
    - VERIFY_SCENARIO / VERIFY_SIDE_EFFECT（shared-server 独占语义）任务本身
      连同其次序全部分配 lane A；
    - 其余任务按页分组（页内保序），页组按任务数降序轮流放入当前总数较少的
      lane（lane A 起始基数 = exclusive 数量），目标 |A-B| 最小且同页任务
      同 lane 连续；tie 时轮换放置，保证确定性。"""
    tasks = load_runtime_tasks(ws, tasks_path)
    runnable = [dict(t) for t in tasks if t.get("verification_mode") != "SOURCE_ONLY"]
    if not runnable:
        raise SystemExit("no runnable runtime tasks (all SOURCE_ONLY?)")

    pages: Dict[str, List[Dict[str, Any]]] = {}
    for t in runnable:
        jid = "JOURNEY-" + (t.get("page_id") or "GLOBAL")
        t["journey_id"] = jid
        t["exclusive"] = t.get("task_type") in EXCLUSIVE_TASK_TYPES
        pages.setdefault(jid, []).append(t)
    for rows in pages.values():
        rows.sort(key=lambda t: str(t.get("task_id", "")))  # 页内保序（冻结次序）

    def journey_key(jid: str):
        rows = pages[jid]
        required = sum(1 for t in rows if t.get("review_tier") == "REQUIRED")
        exclusive = any(t["exclusive"] for t in rows)
        return (0 if exclusive else 1, -required, jid)

    lanes: Dict[str, List[Dict[str, Any]]] = {"A": [], "B": []}
    # 1) exclusive 任务全部进 lane A（保持既有 journey_key 次序 + 页内 task_id 序）
    for jid in sorted(pages, key=journey_key):
        for t in pages[jid]:
            if t["exclusive"]:
                lanes["A"].append(t)  # 共享外部状态任务独占通道

    # 2) 非 exclusive 任务按页贪心均衡：页组任务数降序（tie 用 journey_id），
    #    每次整组放入当前总数较少的 lane（A 基数=exclusive 数量）；相等时轮换
    non_excl_groups = [[t for t in rows if not t["exclusive"]]
                       for rows in pages.values()]
    non_excl_groups = [g for g in non_excl_groups if g]
    last_lane = "B"  # tie 轮换起点：首个持平组放 A
    for g in sorted(non_excl_groups,
                    key=lambda g: (-len(g), str(g[0].get("journey_id", "")))):
        if len(lanes["A"]) < len(lanes["B"]):
            target = "A"
        elif len(lanes["B"]) < len(lanes["A"]):
            target = "B"
        else:
            target = "B" if last_lane == "A" else "A"
        lanes[target].extend(g)  # 整组连续放入（同页任务同 lane 连续）
        last_lane = target

    ids_a = [t["task_id"] for t in lanes["A"]]
    ids_b = [t["task_id"] for t in lanes["B"]]
    overlap = sorted(set(ids_a) & set(ids_b))
    if overlap:
        raise SystemExit(f"queue split produced duplicate Task-IDs: {overlap[:5]}")
    union = sorted(set(ids_a) | set(ids_b))
    total = sorted(t["task_id"] for t in runnable)
    if union != total:
        raise SystemExit("queue union != frozen runnable task set")

    ev_dir = ws / "runtime-evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    task_set = {
        "schema_version": 1,
        "task_ids": total,
        "total": len(total),
        "source_only_excluded": len(tasks) - len(runnable),
        "tasks_sha256": _tasks_digest(total),
    }
    _write_json_atomic(ev_dir / "runtime-task-set.json", task_set)

    queues = {}
    for lane in ("A", "B"):
        ids = [t["task_id"] for t in lanes[lane]]
        queues[lane] = {
            "schema_version": 1,
            "queue_id": f"runtime-queue-{lane.lower()}",
            "lane": lane,
            "tasks_sha256": _tasks_digest(ids),
            "tasks": lanes[lane],
        }
        _write_json_atomic(ev_dir / f"runtime-queue-{lane.lower()}.json", queues[lane])

    freeze_lines = [
        f"{_tasks_digest(union)}  runtime-task-set.json",
        f"{queues['A']['tasks_sha256']}  runtime-queue-a.json",
        f"{queues['B']['tasks_sha256']}  runtime-queue-b.json",
    ]
    (ev_dir / "queue-freeze.sha256").write_text("\n".join(freeze_lines) + "\n", encoding="utf-8")
    print(f"[split] lane A tasks={len(ids_a)} lane B tasks={len(ids_b)} "
          f"runnable={len(runnable)} source_only={task_set['source_only_excluded']}")
    print("[split] frozen:", ev_dir / "queue-freeze.sha256")
    return 0


def _judge_task(snap: Dict[str, Any], sym: str, strings: Dict[str, str],
                registry: Dict[str, set],
                label_by_page: Dict[str, List[str]]) -> tuple:
    """VERIFIED 条件（C1 收紧）：foreground 属目标包 且 页面身份特征命中。
    根页（MainActivity）与「无锚点定义」不再无条件 VERIFIED——foreground
    匹配但无特征命中一律 UNRECOGNIZED，杜绝假 VERIFIED（判定是收紧，
    与 audit replay 的差异会记录在 audit-replay.csv 的 discrepancy 行）。"""
    feats = match_anchors(sym, strings, registry, snap["xml"]) if sym else []
    feats += [f for f in label_by_page.get(sym, []) if f and f in snap["xml"]]
    if not snap["in_pkg"]:
        return "EXITED", feats
    if feats:
        return "VERIFIED", feats
    return "UNRECOGNIZED", feats


def run_lane(ws: Path, project: Path, pkg: str, serial: str, act: str,
             queue_path: Path, slot: str, stay: float,
             stale_streak: int = 6, fail_streak: int = 15) -> int:
    """单 lane 串行执行冻结队列：所有 ADB 强制 -s serial，checkpoint/resume。

    僵尸运行防护（lane 级局部，checkpoint 保留可 resume）：
    - 机制 1 stale guard：连续 stale_streak 个真拍快照（组首/独立执行/重试，
      不含 B2 复制路径）的 ui+png 哈希与前一真拍完全相同且涉及 >=2 个不同
      page_id（跨页才可疑；同页/B2 合法共享同快照，单页应用退化为机制 2
      兜底）-> 画面冻结，SystemExit(3)；达到阈值 50%（floor）先打 WARNING。
      VERIFIED 不清零计数（冻结也能假 VERIFIED），仅"不同哈希"真拍清零。
    - 机制 2 circuit breaker：连续 fail_streak 个任务终态 UNRECOGNIZED/EXITED/
      ERROR -> 设备疑似卡死，SystemExit(4)；VERIFIED 清零（按任务终态计一次，
      B2 复制后重试不重复计）。
    两机制只影响"是否继续跑"，不改判定/证据语义；触发时在 checkpoint 目录
    写 fuse-state.json 记录原因与时间戳，resume 启动时打印并清除后继续。"""
    queue = _read_json(queue_path)
    if queue.get("lane") != slot:
        raise SystemExit(f"queue lane={queue.get('lane')!r} does not match --slot {slot}")
    tasks = queue.get("tasks", [])
    # B7 serial 独占校验：另一 lane 已绑定同一设备 serial 时拒绝启动
    # （两 lane 同绑一台模拟器会交叉污染 ADB 写入与证据归属）。
    other = "B" if slot == "A" else "A"
    other_meta = ws / "runtime-evidence" / f"lane-{other.lower()}" / "lane-meta.json"
    if other_meta.is_file():
        try:
            other_serial = str(_read_json(other_meta).get("device_serial", ""))
        except (OSError, ValueError):
            other_serial = ""
        if other_serial and other_serial == serial:
            print(f"[lane-{slot}] REFUSING TO START: lane {other} is already bound "
                  f"to the SAME device serial {serial!r}.")
            print(f"[lane-{slot}] dual-lane execution needs TWO emulators with "
                  f"distinct serials (e.g. lane A: --serial emulator-5554, "
                  f"lane B: --serial emulator-5556).")
            print(f"[lane-{slot}] fix: start a second emulator, or point this lane "
                  f"at a different device via --serial.")
            return 2
    else:
        print(f"[lane-{slot}] note: lane-{other.lower()}/lane-meta.json absent -- "
              f"the other lane may not have started yet (proceeding).")
    lane_dir = ws / "runtime-evidence" / f"lane-{slot.lower()}"
    lane_dir.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(lane_dir / "lane-meta.json", {
        "schema_version": 1, "lane": slot, "device_serial": serial,
        "queue_file": str(queue_path), "queue_tasks": [t["task_id"] for t in tasks],
    })

    strings = load_strings(project)
    universe = set(page_symbol_universe(ws / "candidates"))
    universe |= {str(t.get("symbol") or "") for t in tasks if t.get("symbol")}
    universe.discard("")
    registry = build_anchor_registry(sorted(universe), strings)
    sym2pid = symbol_page_ids(ws / "candidates")
    pid2sym = {v: k for k, v in sym2pid.items()}
    # static-analysis page-id adapter: tasks may carry static-analysis Page-IDs;
    # resolve their symbol via static-analysis/pages.json then to the GMI page-id
    # so navigation and pid2sym lookups keep working (ids stay untouched in records).
    try:
        _static_pages = _read_json(ws / "static-analysis" / "pages.json").get("pages", [])
        for _sp in _static_pages:
            _sym = str(_sp.get("symbol") or "")
            _static_pid = str(_sp.get("page_id") or "")
            if _sym and _static_pid and _sym in sym2pid:
                pid2sym.setdefault(_static_pid, _sym)
    except (OSError, ValueError):
        pass
    label_by_page: Dict[str, List[str]] = {}
    for r in read_csv(ws / "candidates" / "page-fields.candidates.csv"):
        sym = r.get("page_symbol", "") or ""
        lbl = (r.get("field_label") or "").strip()
        if sym and lbl and len(lbl) <= 40 and "%" not in lbl[:1]:
            label_by_page.setdefault(sym, []).append(lbl)

    ensure_home_ui(serial, pkg, act)
    cur_xml = snapshot(serial, "lane-base", lane_dir, "LANE-BASE", pkg)["xml"]

    ckpt_path = lane_dir / "checkpoint.json"
    checkpoint: Dict[str, Any] = {"completed": {}}
    if ckpt_path.exists():
        try:
            loaded = _read_json(ckpt_path)
            if isinstance(loaded.get("completed"), dict):
                checkpoint = loaded
                print(f"[lane-{slot}] resume: {len(checkpoint['completed'])} tasks already done")
        except ValueError:
            print(f"[lane-{slot}] checkpoint corrupt; starting fresh")

    # 僵尸运行防护 resume 路径：上次熔断留下的 fuse-state.json 说明人工已介入
    # （重启模拟器/清除卡死覆盖层），打印其内容供核对后删除，本 lane 继续执行。
    fuse_path = lane_dir / "fuse-state.json"
    if fuse_path.exists():
        try:
            prev_fuse = _read_json(fuse_path)
            print(f"[lane-{slot}] previous fuse-state detected "
                  f"(assuming human intervention):")
            print(json.dumps(prev_fuse, ensure_ascii=False, indent=2))
        except (OSError, ValueError):
            print(f"[lane-{slot}] previous fuse-state unreadable; discarding it")
        fuse_path.unlink()
        print(f"[lane-{slot}] fuse-state cleared; resuming lane")

    def snap_task(task_id: str, tag: str) -> Dict[str, Any]:
        return snapshot(serial, tag, lane_dir, f"{task_id}/{tag}", pkg)

    def navigate(sym: str) -> str:
        """锚点级联导航到 sym 页（最多 2 轮容器刷新），返回当前 xml。"""
        nonlocal cur_xml
        if not sym or sym == "MainActivity":
            return cur_xml
        for _round in range(2):
            for a in ordered_click_anchors(sym, strings, registry):
                tgt = find_click(cur_xml, a)
                if tgt:
                    adb(serial, "shell", "input", "tap", str(tgt["cx"]), str(tgt["cy"]))
                    time.sleep(stay + wait_secs(WAIT_TAP_SETTLE_BASE))  # B5
                    cur_xml = snapshot(serial, "nav", lane_dir, "LANE-NAV", pkg)["xml"]
                    return cur_xml
            for kw in ("更多", "选项", "菜单", "menu", "more"):
                tgt = find_click(cur_xml, kw)
                if tgt:
                    adb(serial, "shell", "input", "tap", str(tgt["cx"]), str(tgt["cy"]))
                    time.sleep(stay + wait_secs(1.0))  # B5：兜底菜单点击后随 --wait-scale 缩放
                    cur_xml = snapshot(serial, "nav", lane_dir, "LANE-NAV", pkg)["xml"]
                    break
            else:
                break
        return cur_xml

    # ---- B2 同页合并快照：执行器拆分 --------------------------------------
    # 对同一 page_id 且 trigger 不可点击（VERIFY_STATE_BRANCH：主循环从不点击
    # 其 trigger）的连续任务组，只做一次 navigate+before+after 拍摄；组内每任务
    # 仍独立判定（_judge_task 各自跑）、独立 gate 行；后续任务的证据目录照常
    # 创建，before/after 文件从组首任务复制（任务 meta 记 shared_source），
    # 保证每任务哈希完整、gmi_audit 逐行重放兼容。组内任一任务 UNRECOGNIZED：
    # 仅该任务降级为独立重试一轮（单独导航+拍摄），仍失败保持 UNRECOGNIZED。
    MERGE_GROUP_LIMIT = 50

    def _task_sym(t: Dict[str, Any]) -> str:
        sym = str(t.get("symbol") or "")
        if not sym and t.get("page_id"):
            sym = pid2sym.get(t["page_id"], "")
        return sym

    def _new_gate_row(t: Dict[str, Any], sym: str, page_id: str) -> Dict[str, Any]:
        return {
            "task_id": str(t.get("task_id", "")), "journey_id": t.get("journey_id", ""),
            "page_id": page_id, "symbol": sym, "status": "NOT_ENTERED",
            "evidence": "", "capture_slot": slot, "device_serial": serial,
            "verification_mode": t.get("verification_mode", "RUNTIME_UI"),
            "review_tier": t.get("review_tier", "REQUIRED"),
        }

    def exec_task_standalone(t: Dict[str, Any]) -> tuple:
        """单任务完整执行：navigate + before/after 双拍 + 判定（组首/重试共用）。"""
        nonlocal cur_xml
        tid = str(t.get("task_id", ""))
        page_id = str(t.get("page_id", ""))
        sym = _task_sym(t)
        gate_row = _new_gate_row(t, sym, page_id)
        ev_rows: List[Dict[str, Any]] = []
        try:
            task_type = str(t.get("task_type", ""))
            cur_xml = navigate(sym)
            before = snap_task(tid, "before")
            ev_rows.append({**{k: v for k, v in before.items() if k != "xml"},
                            "task_id": tid, "page_id": page_id, "tag": "before",
                            "capture_slot": slot, "device_serial": serial})
            trigger = str(t.get("trigger", "") or "")
            clickable = {n["label"] for n in tap_targets(cur_xml)}
            if task_type in ("VERIFY_EVENT", "VERIFY_TRANSITION") and trigger \
                    and trigger not in ("AUTO_LAUNCH_OR_ROUTE", "AUTO_SATISFY_CONDITION") \
                    and trigger in clickable:
                tgt = find_click(cur_xml, trigger)
                if tgt:
                    adb(serial, "shell", "input", "tap", str(tgt["cx"]), str(tgt["cy"]))
                    time.sleep(stay + wait_secs(WAIT_TAP_SETTLE_BASE))  # B5
            elif task_type in ("VERIFY_SCENARIO", "VERIFY_SIDE_EFFECT"):
                # 副作用/场景：执行 trigger 可点文本；重启类场景进程级复核在 after 探针
                tgt = find_click(cur_xml, trigger) if trigger else None
                if tgt:
                    adb(serial, "shell", "input", "tap", str(tgt["cx"]), str(tgt["cy"]))
                    time.sleep(stay + wait_secs(WAIT_TAP_SETTLE_BASE))  # B5
            after = snap_task(tid, "after")
            ev_rows.append({**{k: v for k, v in after.items() if k != "xml"},
                            "task_id": tid, "page_id": page_id, "tag": "after",
                            "capture_slot": slot, "device_serial": serial})
            status, feats = _judge_task(after, sym, strings, registry, label_by_page)
            if status == "EXITED":
                bring_to_front(serial, pkg, act)
                cur_xml = snapshot(serial, "root", lane_dir, "LANE-NAV", pkg)["xml"]
            else:
                cur_xml = after["xml"]
            gate_row["status"] = status
            gate_row["evidence"] = f"lane-{slot.lower()}/{tid}/after/ui.xml"
            print(f"[lane-{slot}] {tid} -> {status} (feats={len(feats)})")
        except Exception as exc:  # noqa: BLE001
            gate_row["status"] = "ERROR"
            gate_row["evidence"] = f"error: {exc}"
            print(f"[lane-{slot}] {tid} -> ERROR: {exc}")
        return gate_row, ev_rows

    def _rebuild_head_after(head_tid: str) -> Optional[Dict[str, Any]]:
        """从组首磁盘证据 + checkpoint 记录重建判定输入（resume 场景同样可用）。"""
        xml_p = lane_dir / head_tid / "after" / "ui.xml"
        rows = checkpoint.get("completed", {}).get(head_tid, {}).get("ev_rows", [])
        after_row = next((r for r in rows if str(r.get("tag", "")) == "after"), None)
        if not xml_p.is_file() or after_row is None:
            return None
        fg = str(after_row.get("foreground", ""))
        return {"xml": xml_p.read_text(encoding="utf-8", errors="replace"),
                "foreground": fg, "in_pkg": (pkg in fg) if pkg else True}

    def exec_task_shared(t: Dict[str, Any], head_tid: str) -> Optional[tuple]:
        """B2 复制路径：组首 before/after 文件复制到本任务目录（哈希完整、
        audit 逐行重放兼容），再对本任务独立判定。源证据缺失返回 None（降级）。"""
        tid = str(t.get("task_id", ""))
        page_id = str(t.get("page_id", ""))
        sym = _task_sym(t)
        ev_rows: List[Dict[str, Any]] = []
        for tag in ("before", "after"):
            src = lane_dir / head_tid / tag
            dst = lane_dir / tid / tag
            if not (src / "ui.xml").is_file() or not (src / "screenshot.png").is_file():
                return None  # 源证据缺失（resume 中途删目录等）-> 降级独立执行
            dst.mkdir(parents=True, exist_ok=True)
            (dst / "ui.xml").write_bytes((src / "ui.xml").read_bytes())
            (dst / "screenshot.png").write_bytes((src / "screenshot.png").read_bytes())
            head_row = next(
                (r for r in checkpoint["completed"].get(head_tid, {}).get("ev_rows", [])
                 if str(r.get("tag", "")) == tag), None)
            if head_row is None:
                return None
            ev_rows.append({**head_row, "task_id": tid, "page_id": page_id, "tag": tag,
                            "capture_slot": slot, "device_serial": serial,
                            "ui_sha256": sha256f(dst / "ui.xml"),
                            "png_sha256": sha256f(dst / "screenshot.png")})
        head_after = _rebuild_head_after(head_tid)
        if head_after is None:
            return None
        gate_row = _new_gate_row(t, sym, page_id)
        status, feats = _judge_task(head_after, sym, strings, registry, label_by_page)
        gate_row["status"] = status
        gate_row["evidence"] = f"lane-{slot.lower()}/{tid}/after/ui.xml"
        meta = {"shared_source": f"lane-{slot.lower()}/{head_tid}"}
        print(f"[lane-{slot}] {tid} -> {status} (shared snapshot from {head_tid}, "
              f"feats={len(feats)})")
        return gate_row, ev_rows, meta

    def plan_groups() -> List[List[int]]:
        """B2 分组：连续、同 page_id、task_type=VERIFY_STATE_BRANCH 的任务合组
        （上限 MERGE_GROUP_LIMIT）；其余任务单元素组（行为不变）。"""
        groups: List[List[int]] = []
        i, n = 0, len(tasks)
        while i < n:
            t = tasks[i]
            if str(t.get("task_type", "")) == "VERIFY_STATE_BRANCH" and t.get("page_id"):
                j = i + 1
                while j < n and j - i < MERGE_GROUP_LIMIT \
                        and str(tasks[j].get("task_type", "")) == "VERIFY_STATE_BRANCH" \
                        and tasks[j].get("page_id") == t.get("page_id"):
                    j += 1
                groups.append(list(range(i, j)))
                i = j
            else:
                groups.append([i])
                i += 1
        return groups

    # ---- 僵尸运行防护 lane 级局部状态 ------------------------------------
    # 机制 1：当前"静止 streak"内真拍快照指纹 [(page_id, ui_sha256, png_sha256)]；
    # 同页快照照常入列（不去重、不断链），触发仅要求 streak 内 >=2 个不同 page_id。
    stale_snaps: List[Dict[str, str]] = []
    stale_warned = False  # 每个 streak 只打一次 WARNING（不同哈希清零时复位）
    # 机制 2：连续未成功（UNRECOGNIZED/EXITED/ERROR）任务数，VERIFIED 清零
    fail_run = 0

    for grp in plan_groups():
        head_tid = ""  # 本组当前可共享快照的组首 Task-ID（须 VERIFIED/UNRECOGNIZED）
        for idx in grp:
            t = tasks[idx]
            tid = str(t.get("task_id", ""))
            if not tid or tid in checkpoint["completed"]:
                continue
            gate_row: Optional[Dict[str, Any]] = None
            ev_rows: List[Dict[str, Any]] = []
            extra_meta: Dict[str, Any] = {}
            shared = None
            standalone_exec = False  # 真拍（组首/独立执行/重试）= True；B2 复制 = False
            if head_tid and len(grp) > 1:
                head_status = str(checkpoint["completed"].get(head_tid, {})
                                  .get("gate_row", {}).get("status", ""))
                if head_status in ("VERIFIED", "UNRECOGNIZED"):
                    shared = exec_task_shared(t, head_tid)
            if shared is not None:
                gate_row, ev_rows, extra_meta = shared
                if gate_row["status"] == "UNRECOGNIZED":
                    # 组内该任务判不出特征：仅该任务降级独立重试一轮（单独拍摄）
                    print(f"[lane-{slot}] {tid} UNRECOGNIZED under shared snapshot "
                          f"-> standalone retry")
                    gate_row, ev_rows = exec_task_standalone(t)
                    extra_meta = {}
                    standalone_exec = True  # 重试轮是真拍，计入快照指纹队列
            else:
                gate_row, ev_rows = exec_task_standalone(t)
                standalone_exec = True
                if gate_row["status"] in ("VERIFIED", "UNRECOGNIZED"):
                    head_tid = tid  # 后续同页任务可共享本任务快照
                else:
                    head_tid = ""   # EXITED/ERROR：后续任务全部独立执行
            checkpoint["completed"][tid] = {
                "gate_row": gate_row, "ev_rows": ev_rows,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            if extra_meta:
                checkpoint["completed"][tid].update(extra_meta)
            # checkpoint 粒度仍按任务：每任务完成即落盘（B2 不改变断点恢复语义）
            _write_json_atomic(ckpt_path, checkpoint)

            # ---- 僵尸运行防护（任务结果已持久化后才判定熔断）----------------
            # 机制 1 stale guard：仅真拍快照（组首/独立执行/重试）计入指纹队列；
            # B2 复制路径与组首合法共享同一快照，不重复计数。
            if standalone_exec:
                before_row = next(
                    (r for r in ev_rows if str(r.get("tag", "")) == "before"), None)
                if before_row is not None:
                    fp = {"page_id": str(before_row.get("page_id", "")),
                          "ui_sha256": str(before_row.get("ui_sha256", "")),
                          "png_sha256": str(before_row.get("png_sha256", ""))}
                    if stale_snaps and \
                            stale_snaps[-1]["ui_sha256"] == fp["ui_sha256"] and \
                            stale_snaps[-1]["png_sha256"] == fp["png_sha256"]:
                        stale_snaps.append(fp)
                    else:
                        # 不同哈希真拍快照 -> 静止 streak 清零重链
                        # （VERIFIED 不清零：冻结也能假 VERIFIED）
                        stale_snaps = [fp]
                        stale_warned = False
                    pages_in_streak = sorted({s["page_id"] for s in stale_snaps})
                    warn_at = max(2, stale_streak // 2)  # 阈值 50%（floor）
                    if len(stale_snaps) >= warn_at and len(pages_in_streak) >= 2 \
                            and not stale_warned:
                        stale_warned = True
                        recent = [(s["page_id"], s["ui_sha256"][:12], s["png_sha256"][:12])
                                  for s in stale_snaps[-3:]]
                        print(f"WARNING [lane-{slot}] stale-screen suspicion: "
                              f"{len(stale_snaps)}/{stale_streak} consecutive identical "
                              f"real-capture snapshots across pages {pages_in_streak}; "
                              f"recent fingerprints (page, ui, png)={recent}")
                    if len(stale_snaps) >= stale_streak and len(pages_in_streak) >= 2:
                        reason = (
                            f"FROZEN_DEVICE_SUSPECTED: {len(stale_snaps)} consecutive "
                            f"identical snapshots across pages {pages_in_streak}; "
                            f"the emulator screen appears frozen while ADB still "
                            f"responds; reboot the emulator or clear the stuck "
                            f"overlay, then resume this lane (checkpoint preserved)")
                        print(f"[lane-{slot}] STALE GUARD TRIPPED -> {reason}")
                        _write_json_atomic(fuse_path, {
                            "fuse": "stale_guard", "exit_code": 3, "reason": reason,
                            "streak": len(stale_snaps),
                            "stale_streak_threshold": stale_streak,
                            "pages": pages_in_streak,
                            "snapshots": stale_snaps,
                            "triggered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                          time.gmtime())})
                        raise SystemExit(3)

            # 机制 2 circuit breaker：按任务终态计一次（B2 复制后降级重试的
            # 重试轮不重复计）；VERIFIED 清零。
            final_status = str(gate_row.get("status", ""))
            if final_status == "VERIFIED":
                fail_run = 0
            elif final_status in ("UNRECOGNIZED", "EXITED", "ERROR"):
                fail_run += 1
                if fail_run >= fail_streak:
                    reason = (
                        f"DEVICE_UNRESPONSIVE_SUSPECTED: {fail_run} consecutive "
                        f"tasks ended {final_status}; device {serial} likely "
                        f"stuck/locked/ANR; recover the device then resume "
                        f"(checkpoint preserved)")
                    print(f"[lane-{slot}] CIRCUIT BREAKER TRIPPED -> {reason}")
                    _write_json_atomic(fuse_path, {
                        "fuse": "fail_breaker", "exit_code": 4, "reason": reason,
                        "consecutive_failures": fail_run,
                        "fail_streak_threshold": fail_streak,
                        "last_status": final_status, "device_serial": serial,
                        "triggered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                      time.gmtime())})
                    raise SystemExit(4)

    all_gate = [checkpoint["completed"][tid]["gate_row"]
                for tid in [t["task_id"] for t in tasks] if tid in checkpoint["completed"]]
    all_ev = [row for tid in [t["task_id"] for t in tasks]
              for row in checkpoint["completed"].get(tid, {}).get("ev_rows", [])]
    write_csv(lane_dir / "lane-runtime-gate.csv", LANE_GATE_FIELDS, all_gate)
    write_csv(lane_dir / "lane-evidence-index.csv", LANE_EVIDENCE_FIELDS, all_ev)
    done = sum(1 for g in all_gate if g.get("status") == "VERIFIED")
    print(f"[lane-{slot}] finished: total={len(all_gate)} verified={done} serial={serial}")
    return 0


def main() -> int:
    global WAIT_SCALE, SHARED_EVIDENCE_MAX_OWNERS
    ap = argparse.ArgumentParser(description="gmi runtime bridge v5 (dual Android emulator lanes)")
    ap.add_argument("--project", required=False)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--package", required=False)
    ap.add_argument("--activity", default="MainActivity")
    ap.add_argument("--serial", default="emulator-5554")
    ap.add_argument("--split-queues", action="store_true",
                    help="静态任务冻结后拆分 Queue A / Queue B（确定性、旅程不跨 lane）")
    ap.add_argument("--tasks", default=None,
                    help="runtime-tasks.json 路径覆盖（默认 <workspace>/static-analysis/runtime-tasks.json）")
    ap.add_argument("--queue", default=None,
                    help="冻结队列文件（runtime-queue-a.json 或 b），进入单 lane 执行模式")
    ap.add_argument("--slot", default=None, choices=["A", "B"],
                    help="本 worker 的 capture slot，必须与队列文件 lane 一致")
    ap.add_argument("--auto", action="store_true",
                    help="[DIAGNOSTIC ONLY] 盲 BFS 只作诊断，不能产出 VERIFIED；"
                         "正式证据必须走 --queue/--slot lane 模式")
    ap.add_argument("--visits", default=None)
    ap.add_argument("--max-hops", type=int, default=80)
    ap.add_argument("--stay", type=float, default=2.0)
    ap.add_argument("--wait-scale", type=float, default=0.5,
                    help="B5 全局等待缩放：所有等待=模块级基准值×该值，"
                         "默认 0.5（提速档），1.0 恢复旧行为；下限 0.05")
    ap.add_argument("--max-shared-owners", type=int, default=SHARED_EVIDENCE_MAX_OWNERS,
                    help="C4 shared 锚点可作页面身份证据的最大拥有者数"
                         "（按应用校准，默认 6；markor 校准值）")
    ap.add_argument("--stale-streak", type=int, default=6, metavar="S",
                    help="僵尸防护机制1（跨页快照静止检测）：连续 S 个真拍快照"
                         "（组首/独立执行/重试，不含 B2 复制）的 ui+png 哈希与"
                         "前一真拍完全相同且涉及 >=2 个不同 page_id 时判定画面"
                         "冻结并以退出码 3 停车（达到 S 的 50%% 先打 WARNING）；"
                         "下限 3，默认 6")
    ap.add_argument("--fail-streak", type=int, default=15, metavar="F",
                    help="僵尸防护机制2（连续异常熔断）：连续 F 个任务终态为 "
                         "UNRECOGNIZED/EXITED/ERROR 即判定设备疑似卡死并以退出码 "
                         "4 熔断（VERIFIED 清零计数）；下限 5，默认 15")
    ap.add_argument("--back-after", action="store_true")
    ap.add_argument("--grant-perms", action="store_true")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--fill", default=None,
                    help="表单填充：hint:值;hint2:值2（进入页面后自动填）")
    ap.add_argument("--pm-clear", action="store_true",
                    help="先 pm clear 再启动（抓首启流程页 Guide/Welcome）")
    ap.add_argument("--explore", action="store_true",
                    help="探索模式：对无文本可点节点逐级点击（图标按钮），指纹去重防死循环")
    ap.add_argument("--screen-size", default="1080x2400",
                    help="固定模拟器分辨率（默认 1080x2400，与 Harmony 侧一致）")
    ap.add_argument("--screen-density", default="440",
                    help="固定模拟器密度 dpi（默认 440，与 Harmony 侧一致）")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    # B5：全局等待缩放（默认 0.5 提速；1.0 恢复旧行为；下限 0.05 防极端参数）
    WAIT_SCALE = max(args.wait_scale, 0.05)
    # C4：shared 证据兜底上限参数化（按应用校准）
    SHARED_EVIDENCE_MAX_OWNERS = args.max_shared_owners

    # Phase 1 冻结值贯通：preflight_screen.py 写入的 scope.json 若存在，
    # 且命令行未显式指定 --screen-size/--screen-density，则用 scope 值（全程同一基准）
    _scope_fp = Path(args.workspace).parent / "controller" / "scope.json"
    try:
        if _scope_fp.exists():
            import json as _j
            _sc = _j.loads(_scope_fp.read_text(encoding="utf-8"))
            if _sc.get("screen_resolution") and args.screen_size == "1080x2400":
                args.screen_size = str(_sc["screen_resolution"])
            if _sc.get("screen_density") and args.screen_density == "440":
                args.screen_density = str(_sc["screen_density"])
            if _sc.get("android_serial"):
                args.serial = str(_sc["android_serial"])
    except (ValueError, OSError):
        pass

    ws = Path(args.workspace)
    out_dir = ws / "runtime-evidence"

    # 新正式路径 1：队列拆分（静态 100% 之后）
    if args.split_queues:
        return split_queues(ws, Path(args.tasks) if args.tasks else None)

    # 新正式路径 2：单 lane 队列执行（每设备单 ADB writer，lane 内串行）
    if args.queue:
        if not args.slot:
            ap.error("--queue requires --slot A|B (capture slot of this worker)")
        if not args.project or not args.package:
            ap.error("--queue mode requires --project and --package")
        return run_lane(ws, Path(args.project), args.package, args.serial,
                        args.activity, Path(args.queue), args.slot, args.stay,
                        max(args.stale_streak, 3), max(args.fail_streak, 5))

    if not args.project or not args.package:
        ap.error("--project and --package are required")

    pkg, serial, act = args.package, args.serial, args.activity
    cands = ws / "candidates"

    if args.grant_perms:
        n = grant_permissions(serial, Path(args.project), pkg)
        print(f"[perms] granted {n} permissions for {pkg}")

    # 固定屏幕分辨率/密度（Phase 3/4 屏幕一致性基准）
    if args.screen_size:
        adb(serial, "shell", "wm", "size", args.screen_size)
        time.sleep(0.8)
    if args.screen_density:
        adb(serial, "shell", "wm", "density", args.screen_density)
        time.sleep(1.5)
    cur_size = adb(serial, "shell", "wm", "size").strip().replace("\n", " ")
    cur_den = adb(serial, "shell", "wm", "density").strip().replace("\n", " ")
    print(f"[screen] fixed to {cur_size} / {cur_den}")

    if args.pm_clear:
        pm_clear_and_relaunch(serial, pkg, act)
    else:
        start_app(serial, pkg, act)
        time.sleep(6)

    ev: List[Dict[str, Any]] = []
    gate_rows: List[Dict[str, Any]] = []
    visited: set = set()

    if args.auto:
        print("[auto] DEPRECATED: --auto is a DIAGNOSTIC tool only and cannot produce VERIFIED. "
              "Formal Phase-2 evidence must come from --queue/--slot lane execution.")
        # 诊断产物隔离：不进入 lane 目录，不参与 closure 分母
        out_dir = ws / "runtime-evidence" / "diagnostic"
        out_dir.mkdir(parents=True, exist_ok=True)
        project = Path(args.project)
        strings = load_strings(project)

        # 候选页全集（P2 与 audit 同源）：completeness ∪ page-fields 真实写法，
        # 不再用 inventory page_id 反推符号（大小写失真幽灵页，如 Mainactivity）
        # LAUNCH 主页（MainActivity）由 PAGE-LAUNCH 覆盖，不参与 BFS pending，
        # 但保留在 registry 构建域（与 audit universe 完全一致，防 shared 度漂移）
        universe = page_symbol_universe(cands)
        pages = [p for p in universe if p.lower() != "mainactivity"]
        print(f"[auto] candidate pages={len(pages)} (strings={len(strings)})")

        # P2 泛锚点治理：锚点 -> 拥有者集合（shared 判定），点击序/命中判定共用
        registry = build_anchor_registry(universe, strings)
        # P1：NOT_ENTERED 行 page_id 回填映射
        sym2pid = symbol_page_ids(cands)

        # 特征集（与 gmi_audit 重放同源）：strings 锚点 + page-fields field_label。
        # VISITED 判定双校验 = foreground in pkg 且特征命中（P2 shared 治理后），
        # 防「点错页记 VISITED」与泛锚点误配（'文件'级 2 字泛词不再单独构成证据）
        label_by_page: Dict[str, List[str]] = {}
        for r in read_csv(cands / "page-fields.candidates.csv"):
            _sym = r.get("page_symbol", "") or ""
            _lbl = (r.get("field_label") or "").strip()
            if _sym and _lbl and len(_lbl) <= 40 and "%" not in _lbl[:1]:
                label_by_page.setdefault(_sym, []).append(_lbl)

        def feats_hit_in(sym: str, xml: str) -> List[str]:
            """命中特征（P2，audit 同源）：锚点走 match_anchors 规则
            （非 shared 优先，shared 需 >=3 字符）+ page-fields field_label 直配。"""
            hits = match_anchors(sym, strings, registry, xml)
            return hits + [f for f in label_by_page.get(sym, []) if f and f in xml]

        # 首启拦截弹窗消解 + WebView 帮助页回归主页（markor 首启默认打开帮助文档
        # 且 WebView 渲染失败，必须 BACK 一次才到文件浏览器主页锚点基地）
        ensure_home_ui(serial, pkg, act)
        home_xml = snapshot(serial, "main0", out_dir, "PAGE-LAUNCH", pkg)["xml"]
        prio = []
        for p in pages:
            for a in ordered_click_anchors(p, strings, registry):
                if find_click(home_xml, a):
                    prio.append(p)
                    break
        rest = [p for p in pages if p not in prio]
        pages = prio + rest
        print(f"[auto] priority-anchored={len(prio)}")

        home = snapshot(serial, "main", out_dir, "PAGE-LAUNCH", pkg)
        ev.append({k: v for k, v in home.items() if k != "xml"})
        # symbol 用候选页真实符号 MainActivity（与 completeness/audit 豁免口径一致）
        gate_rows.append({"page_id": "PAGE-LAUNCH", "symbol": "MainActivity", "status": "VISITED",
                          "evidence": "PAGE-LAUNCH/ui.xml"})
        visited.add("MainActivity")
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

        # 文件条目旅程（文档浏览器类 app 通用入口）：主页点 .md/.txt 等文件条目
        # -> 编辑/预览页。markor 实测：DocumentEditAndViewFragment（宿主 DocumentActivity）
        # 的入口是文件列表条目，文件名不在 strings 锚点集内，锚点机制无法发现。
        if any(p.lower() == "documenteditandviewfragment" for p in pages) and \
                "DocumentEditAndViewFragment" not in visited:
            file_like = [t for t in tap_targets(home_xml)
                         if re.search(r"\S+\.(md|txt|markdown|json|org|todo)(\s|$)", t["label"], re.I)]
            if file_like:
                t0 = file_like[0]
                print(f"[auto] tap file entry '{t0['label'][:24]}' -> document edit page")
                adb(serial, "shell", "input", "tap", str(t0["cx"]), str(t0["cy"]))
                time.sleep(args.stay + 2.0)
                pid = "PAGE-DOCUMENT-EDIT"
                snap = snapshot(serial, "document-edit", out_dir, pid, pkg)
                ev.append({k: v for k, v in snap.items() if k != "xml"})
                sym_doc = "DocumentEditAndViewFragment"
                hits = feats_hit_in(sym_doc, snap["xml"])
                if snap["in_pkg"] and hits:
                    gate_rows.append({"page_id": pid, "symbol": sym_doc, "status": "VISITED",
                                      "evidence": f"{pid}/ui.xml"})
                    visited.add(sym_doc)
                    print(f"[auto] document entry -> {sym_doc} VISITED (hits={len(hits)})")
                    status_doc = "VISITED"
                else:
                    status_doc = "UNRECOGNIZED" if snap["in_pkg"] else "EXITED"
                    gate_rows.append({"page_id": pid, "symbol": sym_doc, "status": status_doc,
                                      "evidence": f"{pid}/ui.xml"})
                # 回主页基地（编辑页 BACK 回 LAUNCH 主页）
                adb(serial, "shell", "input", "keyevent", "4")
                time.sleep(2.0)
                home2 = snapshot(serial, "root", out_dir, "PAGE-ROOT", pkg)
                if not home2["in_pkg"]:
                    bring_to_front(serial, pkg, act)
                    home2 = snapshot(serial, "root2", out_dir, "PAGE-ROOT", pkg)
                if home2["in_pkg"]:
                    cur_xml = home2["xml"]
                    home_xml = home2["xml"]

        # 级联 BFS：每步把「当前 UI 里存在的锚点文本」作为跳转机会
        pending = [p for p in pages if p not in visited]
        deferred: List[str] = []  # 点击后掉出 app 的锚点页：本轮暂时跳过，防反复点
        hops = 0
        container_taps = 0
        while pending and hops < args.max_hops:
            hops += 1
            clicked = False
            # 弹窗消解 + 主页基地保障（markor 存储说明弹窗/WebView 帮助页在 BACK/
            # 重启后会重现，遮蔽主页锚点）；返回最新 UI 作为本轮锚点匹配基准
            _home = ensure_home_ui(serial, pkg, act)
            if _home and "<node" in _home:
                cur_xml = _home
            for sym in list(pending):
                if sym in visited or sym in deferred:
                    continue
                for a in ordered_click_anchors(sym, strings, registry):
                    tgt = find_click(cur_xml, a)
                    if tgt:
                        adb(serial, "shell", "input", "tap", str(tgt["cx"]), str(tgt["cy"]))
                        # B5：锚点 tap 后稳定等待随 --wait-scale 缩放（与 navigate 版一致）
                        time.sleep(args.stay + wait_secs(WAIT_TAP_SETTLE_BASE))
                        pid = f"STEP-{hops:02d}-{re.sub(r'[^A-Za-z0-9]', '', sym)[:30]}"
                        snap = snapshot(serial, sym, out_dir, pid, pkg)
                        ev.append({k: v for k, v in snap.items() if k != "xml"})
                        # 双校验（与 audit 同源，P2 shared 治理）：fg in pkg
                        # 且该页特征命中才 VISITED
                        feats_hit = feats_hit_in(sym, snap["xml"])
                        if snap["in_pkg"] and feats_hit:
                            gate_rows.append({"page_id": pid, "symbol": sym, "status": "VISITED",
                                              "evidence": f"{pid}/ui.xml"})
                            visited.add(sym)
                        elif snap["in_pkg"]:
                            # 到达 app 但无目标页特征（锚点太泛点到了别页）：如实记
                            # UNRECOGNIZED（audit 重放同判定 -> 无 discrepancy），不算到达
                            gate_rows.append({"page_id": pid, "symbol": sym,
                                              "status": "UNRECOGNIZED",
                                              "evidence": f"{pid}/ui.xml"})
                            if sym not in deferred:
                                deferred.append(sym)
                            print(f"[auto] {hops}. {sym} -> UNRECOGNIZED (no feature hit)")
                        else:
                            # 掉出 app（点到了别处/退出）：标 EXITED，绝不当作到达；
                            # 该锚点本轮 deferred 暂时跳过（不反复点），拉回后验证前台
                            gate_rows.append({"page_id": pid, "symbol": sym, "status": "EXITED",
                                              "evidence": f"{pid}/ui.xml"})
                            if sym not in deferred:
                                deferred.append(sym)
                            fg_btf = bring_to_front(serial, pkg, act)
                            rsnap = snapshot(serial, "root", out_dir, "PAGE-ROOT", pkg)
                            ev.append({k: v for k, v in rsnap.items() if k != "xml"})
                            if not rsnap["in_pkg"]:
                                # 教训：回不去 markor 就终止本轮，如实保留已访问报告
                                print(f"[auto] cannot return to {pkg} (fg={fg_btf}); stop BFS")
                                hops = args.max_hops
                                clicked = True
                                break
                            cur_xml = rsnap["xml"]
                            break
                        cur_xml = snap["xml"]
                        adb(serial, "shell", "input", "keyevent", "4")
                        time.sleep(2.0)
                        back_snap = snapshot(serial, "back", out_dir, "PAGE-BACK", pkg)
                        ev.append({k: v for k, v in back_snap.items() if k != "xml"})
                        if not back_snap["in_pkg"]:
                            # BACK 把 app 退到桌面了：拉回来
                            fg = bring_to_front(serial, pkg, act)
                        back2 = snapshot(serial, "root2", out_dir, "PAGE-ROOT", pkg)
                        ev.append({k: v for k, v in back2.items() if k != "xml"})
                        if back2["in_pkg"]:
                            cur_xml = back2["xml"]
                        else:
                            # 回不去 markor：终止本轮如实报告，绝不在错误 UI 上继续找锚点
                            print(f"[auto] cannot return to {pkg} after BACK (fg={fg}); stop BFS")
                            hops = args.max_hops
                            break
                        print(f"[auto] {hops}. {sym} -> visited")
                        clicked = True
                        break
                if clicked:
                    break
            if not clicked:
                # 容器先行（修菜单/抽屉遮蔽）：当前 UI 无文本锚点时，先点开
                # 「更多/菜单/导航抽屉」类容器刷新 UI，再继续级联找锚点（限次防死循环）
                tgt = None
                if container_taps < 6:
                    for kw in ("更多", "选项", "菜单", "导航", "drawer", "menu", "more", "options", "navigation"):
                        tgt = find_click(cur_xml, kw)
                        if tgt:
                            break
                if tgt:
                    container_taps += 1
                    adb(serial, "shell", "input", "tap", str(tgt["cx"]), str(tgt["cy"]))
                    time.sleep(args.stay + 1.0)
                    csnap = snapshot(serial, f"container{container_taps}", out_dir, "PAGE-ROOT", pkg)
                    if not csnap["in_pkg"]:
                        fg_c = bring_to_front(serial, pkg, act)
                        csnap = snapshot(serial, "root", out_dir, "PAGE-ROOT", pkg)
                    if not csnap["in_pkg"]:
                        # 回不去 markor：终止本轮如实报告
                        print(f"[auto] cannot return to {pkg} after container tap (fg={fg_c}); stop BFS")
                        break
                    cur_xml = csnap["xml"]
                    print(f"[auto] container-tap #{container_taps} '{tgt['label'][:16]}' -> ui refreshed")
                    continue
                break

        unreach = [p for p in pending if p not in visited]
        print(f"[auto] finished. visited={len(visited)} not_entered={len(unreach)}")
        for p in unreach:
            # P1：NOT_ENTERED 行回填 page_id（completeness/page-fields 反查），
            # 找不到映射时留空（如实）
            gate_rows.append({"page_id": sym2pid.get(p, ""), "symbol": p,
                              "status": "NOT_ENTERED",
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
                # 弹窗消解（BFS 结束态常停在拦截弹窗上，explore 无目标会静默退出）
                if dismiss_startup_dialogs(serial, pkg) > 0:
                    cur_xml = snapshot(serial, "root", out_dir, "PAGE-ROOT", pkg)["xml"]
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
                    if not snapx["in_pkg"]:
                        # 掉出 app：立即拉回前台，避免后续轮次在桌面/其他 app 上乱点
                        bring_to_front(serial, pkg, act)
                    print(f"[explore] {hop}. tap {t['label'][:12] or t['cls']} -> {snapx['page_id']} "
                          f"{'VISITED' if snapx['in_pkg'] else 'EXITED'}")
                    acted = True
                    adb(serial, "shell", "input", "keyevent", "4")
                    time.sleep(1.2)
                if not acted:
                    break
                _rs = snapshot(serial, "root", out_dir, "PAGE-ROOT", pkg)
                if not _rs["in_pkg"]:
                    bring_to_front(serial, pkg, act)
                    _rs = snapshot(serial, "root2", out_dir, "PAGE-ROOT", pkg)
                cur_xml = _rs["xml"]
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

    # --auto 诊断降级：所有 gate 状态映射为 DIAGNOSTIC_*，绝不产出 VERIFIED/VISITED
    if args.auto:
        _DIAG_MAP = {"VISITED": "DIAGNOSTIC_VISITED", "NOT_ENTERED": "DIAGNOSTIC_NOT_ENTERED",
                     "UNRECOGNIZED": "DIAGNOSTIC_UNRECOGNIZED", "EXITED": "DIAGNOSTIC_EXITED"}
        for g in gate_rows:
            g["status"] = _DIAG_MAP.get(g.get("status", ""), g.get("status", ""))

    write_csv(out_dir / "evidence-index.csv",
              ["page_id", "tag", "foreground", "ui_sha256", "png_sha256", "screen_resolution", "screen_density"], ev)
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
    ur = sum(1 for g in gate_rows if g["status"] == "UNRECOGNIZED")
    print(f"[runtime] visited={v} not_entered={ne} unrecognized={ur} evidence={len(ev)} out={out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
