# -*- coding: utf-8 -*-
"""preflight_env -- Phase 1 环境前置：双模拟器屏幕参数 + SDK/工具链，一并冻结到 scope.json。

用法：
  python preflight_env.py --serial emulator-5554 --width 1080 --height 2400 --density 440 \
        [--harmony-serial <鸿蒙模拟器 hdc serial>] --scope <controller/scope.json>

行为（三段，全部在 P1 scope 冻结前完成）：
  A. Screen   : Android wm size/density 固定 WxH/dpi；Harmony 同参数（hdc）；不一致 -> FAIL(1)
  B. SDK 探测 : android(home/adb/java) + harmony(hdc/DevEco/node/ohpm/hvigor) 定位+版本；
               每一项 found-not-found 全量记录；缺失项只记「缺失」不中断（P3/P4 gateway 再硬卡）
  C. 写回     : scope.json 增加 screen_resolution/screen_density/serial + sdk_android/sdk_harmony 块，
               供 P2 gmi_runtime、P3 init_scaffold、P4 H4ENV 直接引用，全程同一环境基准。

任一 screen 不符 -> return 1（环境没就绪，P1 不放行）。
SDK 缺失 -> return 0 但输出 [WARN] 清单（先在 P1 暴露，允许先记录后补齐）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:  # noqa
        return f"__ERR__{e}"


def which_or_path(prog: str, extra: list[str]) -> tuple[str, bool]:
    p = shutil.which(prog)
    if p:
        return str(p), True
    for e in extra:
        if Path(e).exists():
            return e, True
    return "", False


def detect_tool_version(label: str, prog: str, cmd: list[str], extra_paths: list[str]) -> tuple[str, str, str]:
    path, ok = which_or_path(prog, extra_paths)
    if not ok:
        return prog, "not-found", ""
    effective = list(cmd) if cmd else [path, "--version"]
    if effective and not shutil.which(effective[0]) and Path(path).is_file():
        effective[0] = path
    out = run(effective)
    ver = ""
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", out)
    if m:
        ver = m.group(1)
    return path, "found", ver


def find_deveco() -> tuple[str, str, str]:
    cands = [
        "/Applications/DevEco-Studio.app",
        "/Applications/DevEco Studio.app",
        r"C:\Program Files\Huawei\DevEcoStudio\bin\devecostudio64.exe",
        r"C:\Program Files\Huawei\DevEcoStudio\bin\deveco-studio64.exe",
        r"D:\DevEcoStudio\bin\devecostudio64.exe",
        r"C:\Program Files\Huawei\DevEcoStudio",
        r"D:\DevEco Studio",
        r"E:\DevEco Studio",
    ]
    for c in cands:
        if Path(c).exists():
            return c, "found", ""
    return "DevEcoStudio", "not-found", ""


def deveco_tool_roots(deveco_root: str) -> list[str]:
    """从找到的 DevEco 根路径推导工具链候选路径（hdc/ohpm/hvigorw 所在）。"""
    roots = [deveco_root]
    # DevEco 安装根下常见 SDK/工具子目录
    for child in ("sdk", "Contents", "tools", "sdk/default/openharmony/toolchains"):
        candidate = Path(deveco_root) / child
        if candidate.is_dir():
            roots.append(str(candidate))
    # Windows 上 hdc 典型位置
    roots.extend([
        r"D:\DevEco Studio\sdk\default\openharmony\toolchains",
        r"C:\Program Files\Huawei\DevEcoStudio\sdk\default\openharmony\toolchains",
    ])
    return roots


def adb(serial: str, *args: str) -> str:
    return run(["adb", "-s", serial, *args])


def hdc(serial: str, *args: str) -> str:
    return run(["hdc", "-t", serial, *args] if serial else ["hdc", *args])


def _wm_value(output: str, label: str, value_pattern: str) -> str:
    """Prefer Android's active Override value, then fall back to Physical/plain output."""
    override = re.search(rf"Override\s+{label}:\s*({value_pattern})", output, re.IGNORECASE)
    if override:
        return override.group(1)
    physical = re.search(rf"Physical\s+{label}:\s*({value_pattern})", output, re.IGNORECASE)
    if physical:
        return physical.group(1)
    plain = re.search(rf"({value_pattern})", output)
    return plain.group(1) if plain else ""


def current_size_density(dev: str, serial: str) -> tuple[str, str]:
    if dev == "android":
        size_out = adb(serial, "shell", "wm", "size")
        den_out = adb(serial, "shell", "wm", "density")
    else:
        size_out = hdc(serial, "shell", "wm", "size")
        den_out = hdc(serial, "shell", "wm", "density")
    return _wm_value(size_out, "size", r"\d+x\d+"), _wm_value(den_out, "density", r"\d+")


def set_resolution(dev: str, serial: str, size: str, density: str) -> bool:
    if dev == "android":
        adb(serial, "shell", "wm", "size", size)
        adb(serial, "shell", "wm", "density", density)
        return True
    out = hdc(serial, "shell", "wm", "size", size) + hdc(serial, "shell", "wm", "density", density)
    lowered = out.lower()
    fail_markers = ("inaccessible", "not found", "no such", "command not found", "__err__")
    if any(marker in lowered for marker in fail_markers):
        return False
    return True


def _harmony_size_density(serial: str, config_hint: str = "") -> tuple[str, str]:
    """Harmony 模拟器无 wm 命令（Mac/部分镜像）时：
    优先 hidumper 渲染分辨率（真实证据），密度回退模拟器 config.ini（hw.lcd.density）。

    注意：`param get const.product.density` 在多数模拟器镜像上不存在，
    其失败输出常含数字（如 'fail! errNum is:106!'），故必须按失败标记判定，
    不得把错误码误采信为密度值（否则 config.ini 兜底永不触发）。"""
    size_out = hdc(serial, "shell", "hidumper", "-s", "10", "-a", "screen")
    m = re.search(r"render resolution=(\d+x\d+)", size_out)
    if not m:
        m = re.search(r"physical resolution=(\d+x\d+)", size_out)
    if not m:
        m = re.search(r"(\d+x\d+)", size_out)
    size = m.group(1) if m else ""
    den = ""
    hid = hdc(serial, "shell", "param", "get", "const.product.density")
    lowered = hid.lower()
    param_get_failed = any(
        marker in lowered
        for marker in ("fail", "errnum", "error", "inaccessible", "not found", "no such")
    )
    m2 = re.search(r"(\d+)", hid)
    # 只有真实成功响应（无失败标记的纯数字/数值输出）才可采信；
    # 失败输出（如 errNum 码）按“无值”处理，交由 config.ini 兜底。
    if not param_get_failed and m2:
        den = m2.group(1)
    # 从模拟器部署 config.ini 独立兜底 size/density；必须显式传入才使用。
    if config_hint and Path(config_hint).is_file() and (not size or not den):
        txt = Path(config_hint).read_text(encoding="utf-8", errors="replace")
        mw = re.search(r"hw\.lcd\.single\.width=(\d+)", txt)
        mh = re.search(r"hw\.lcd\.single\.height=(\d+)", txt)
        md = re.search(r"hw\.lcd\.density=(\d+)", txt)
        if not size and mw and mh:
            size = f"{mw.group(1)}x{mh.group(1)}"
        if not den and md:
            den = md.group(1)
    return size, den


def _is_ascii_path(path: Path) -> bool:
    try:
        os.fspath(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 1 env preflight (screen parity + SDK/toolchain)")
    ap.add_argument("--serial", default="emulator-5554")
    ap.add_argument("--harmony-serial", default="", help="hdc serial; empty = Harmony simulator not checked")
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=2400)
    ap.add_argument("--density", type=int, default=440)
    ap.add_argument("--scope", help="controller/scope.json to annotate frozen env")
    ap.add_argument("--harmony-config", default="", help="Harmony emulator config.ini for wm-less density fallback")
    args = ap.parse_args()

    size = f"{args.width}x{args.height}"
    errors: list[str] = []
    warns: list[str] = []
    sdk_android: dict = {}
    sdk_harmony: dict = {}

    run_hint = Path(args.scope).expanduser().absolute() if args.scope else Path.cwd().absolute()
    if not _is_ascii_path(run_hint):
        print(
            f"[ERROR] non-ASCII migration path is unsupported by hvigor: {run_hint} "
            "(move the run to an ASCII-only path before Phase 1)"
        )
        return 1

    # ---- A. Screen parity ----
    devices = run(["adb", "devices"])
    if args.serial not in devices:
        errors.append(f"android emulator offline: {args.serial} (adb devices)")
    else:
        set_resolution("android", args.serial, size, str(args.density))
        got = current_size_density("android", args.serial)
        if got[0] != size or got[1] != str(args.density):
            errors.append(f"android screen mismatch: want {size}/{args.density}, got {got}")

    if args.harmony_serial:
        targets = hdc("", "list", "targets")
        if args.harmony_serial not in targets:
            errors.append(f"harmony simulator offline: {args.harmony_serial} (hdc list targets)")
        else:
            ok = set_resolution("harmony", args.harmony_serial, size, str(args.density))
            harm_size, harm_den = _harmony_size_density(args.harmony_serial, args.harmony_config)
            if not ok:
                warns.append("harmony wm unavailable: 分辨率已在模拟器配置层设置，preflight 改为只读探测")
            if harm_size != size or harm_den != str(args.density):
                errors.append(f"harmony screen mismatch: want {size}/{args.density}, got {harm_size}/{harm_den}")
    else:
        warns.append("harmony-serial not provided -> Harmony simulator not verified (P4 parity may DEFERRED)")

    # ---- B. SDK / toolchain probe ----
    home = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""
    sdk_android["home"] = home or "not-set"
    for prog, cmd, extra in [
        ("adb", ["adb", "version"], []),
        ("emulator", ["emulator", "-version"], []),
    ]:
        p, st, v = detect_tool_version(prog, prog, cmd, extra)
        sdk_android[prog] = {"path": p, "status": st, "version": v}
    jp, jst, jv = detect_tool_version("java", "java", ["java", "-version"], [])
    sdk_android["java"] = {"path": jp, "status": jst, "version": jv}

    dp, dst, dv = find_deveco()
    sdk_harmony["deveco"] = {"path": dp, "status": dst, "version": dv}
    # 用已找到的 DevEco 根（或常见位置）推导工具链候选路径，hdc/ohpm/hvigorw 均可命中
    deveco_roots = deveco_tool_roots(dp if dst == "found" else "")
    harmony_tool_paths = {
        "hdc": [str(Path(root) / "hdc") for root in deveco_roots],
        "ohpm": [str(Path(root) / "ohpm" / "bin" / "ohpm") for root in deveco_roots],
        "hvigorw": [str(Path(root) / "hvigor" / "bin" / "hvigorw") for root in deveco_roots],
    }
    # 显式路径候选追加到 common（供 detect_tool_version 的 extra_paths 探测）
    hp, hst, hv = detect_tool_version("hdc", "hdc", ["hdc", "version"], harmony_tool_paths["hdc"])
    sdk_harmony["hdc"] = {"path": hp, "status": hst, "version": hv}
    for prog, cmd in [("node", ["node", "--version"]),
                      ("ohpm", ["ohpm", "--version"]),
                      ("hvigorw", ["hvigorw", "-v"])]:
        p, st, v = detect_tool_version(prog, prog, cmd, harmony_tool_paths.get(prog, []))
        sdk_harmony[prog] = {"path": p, "status": st, "version": v}

    low = {k: d for k, d in {**sdk_android, **sdk_harmony}.items() if isinstance(d, dict) and d.get("status") == "not-found"}

    # ---- C. annotate scope ----
    if args.scope:
        sp = Path(args.scope)
        if sp.exists():
            try:
                sc = json.loads(sp.read_text(encoding="utf-8"))
                sc["screen_resolution"] = size
                sc["screen_density"] = str(args.density)
                sc["android_serial"] = args.serial
                sc["harmony_serial"] = args.harmony_serial or ""
                sc["sdk_toolchain"] = {
                    "android": sdk_android,
                    "harmony": sdk_harmony,
                }
                sp.write_text(json.dumps(sc, indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"[preflight] scope annotated: screen {size}/{args.density} + sdk_toolchain")
            except ValueError as e:
                errors.append(f"scope annotation failed: {e}")
        else:
            """scope 不存在也写一个（controller 初始化早于 scope 的场景）"""
            sc = {
                "screen_resolution": size,
                "screen_density": str(args.density),
                "android_serial": args.serial,
                "harmony_serial": args.harmony_serial or "",
                "sdk_toolchain": {"android": sdk_android, "harmony": sdk_harmony},
            }
            Path(args.scope).parent.mkdir(parents=True, exist_ok=True)
            Path(args.scope).write_text(json.dumps(sc, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"[preflight] scope created: screen {size}/{args.density} + sdk_toolchain")

    if low:
        for k in sorted(low):
            print(f"[WARN] missing {k}")
            warns.append(f"missing {k}")

    for e in errors:
        print("[preflight] FAIL:", e)
    for w in warns:
        print("[preflight] note:", w)

    if errors:
        return 1
    print(f"[preflight] OK: screen {size} @ {args.density} fixed; SDK: android {len(sdk_android)} items, harmony {len(sdk_harmony)} items")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
