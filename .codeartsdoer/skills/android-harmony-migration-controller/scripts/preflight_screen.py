# -*- coding: utf-8 -*-
"""preflight_screen -- Phase 1 环境前置：验证/设置 Android + Harmony 模拟器与分辨率一致性。

用法：
  python preflight_screen.py --serial emulator-5554 --width 1080 --height 2400 --density 440 \
        [--harmony-serial <鸿蒙模拟器 hdc serial>] --scope <controller/scope.json>

行为：
  1. Android 侧：adb devices 确认模拟器在线 -> `wm size <W>x<H>` + `wm density <dpi>` 固定
  2. Harmony 侧：hdc list targets 确认在线 -> `hdc shell wm size/density` 设置（若 hdc 支持）
  3. 一致性校验：两者分辨率/密度必须 == 目标值；不符即退非零（P1 门禁不通过）
  4. 把固定值写回 scope.json（android_resolution / harmony_resolution / screen_resolution / screen_density）
     供 P2 gmi_runtime 与 P4 H4ENV 直接引用（后续阶段不再重复设）

任一模拟器离线/分辨率不符 -> 输出诊断并 return 1（人工先解决环境,P1 不放行）。
"""
from __future__ import annotations

import argparse
import json
import re
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


def adb(serial: str, *args: str) -> str:
    return run(["adb", "-s", serial, *args])


def hdc(serial: str, *args: str) -> str:
    return run(["hdc", "-t", serial, *args] if serial else ["hdc", *args])


def current_size_density(dev: str, serial: str) -> tuple[str, str]:
    if dev == "android":
        size_out = adb(serial, "shell", "wm", "size")
        den_out = adb(serial, "shell", "wm", "density")
    else:
        size_out = hdc(serial, "shell", "wm", "size")
        den_out = hdc(serial, "shell", "wm", "density")
    m = re.search(r"(\d+x\d+)", size_out)
    m2 = re.search(r"(\d+)", den_out)
    return (m.group(1) if m else ""), (m2.group(1) if m2 else "")


def set_resolution(dev: str, serial: str, size: str, density: str) -> None:
    if dev == "android":
        adb(serial, "shell", "wm", "size", size)
        adb(serial, "shell", "wm", "density", density)
    else:
        hdc(serial, "shell", "wm", "size", size)
        hdc(serial, "shell", "wm", "density", density)


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase 1 screen preflight (Android+Harmony parity)")
    ap.add_argument("--serial", default="emulator-5554")
    ap.add_argument("--harmony-serial", default="", help="hdc serial; empty = Harmony simulator not checked")
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=2400)
    ap.add_argument("--density", type=int, default=440)
    ap.add_argument("--scope", help="controller/scope.json to annotate frozen screen values")
    args = ap.parse_args()

    size = f"{args.width}x{args.height}"
    errors: list[str] = []

    # 1) Android
    devices = adb(args.serial, "devices")
    if args.serial not in devices:
        errors.append(f"android emulator offline: {args.serial} (adb devices)")
    else:
        set_resolution("android", args.serial, size, str(args.density))
        got = current_size_density("android", args.serial)
        if got[0] != size or got[1] != str(args.density):
            errors.append(f"android screen mismatch: want {size}/{args.density}, got {got}")

    # 2) Harmony (optional）
    if args.harmony_serial:
        targets = hdc("", "list", "targets")
        if args.harmony_serial not in targets:
            errors.append(f"harmony simulator offline: {args.harmony_serial} (hdc list targets)")
        else:
            set_resolution("harmony", args.harmony_serial, size, str(args.density))
            got = current_size_density("harmony", args.harmony_serial)
            if got[0] != size or got[1] != str(args.density):
                errors.append(f"harmony screen mismatch: want {size}/{args.density}, got {got}")

    # 3) annotate scope
    if args.scope and Path(args.scope).exists():
        try:
            sc = json.loads(Path(args.scope).read_text(encoding="utf-8"))
            sc["screen_resolution"] = size
            sc["screen_density"] = str(args.density)
            sc["android_serial"] = args.serial
            sc["harmony_serial"] = args.harmony_serial or ""
            Path(args.scope).write_text(json.dumps(sc, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"[preflight] scope annotated: {size}/{args.density}")
        except ValueError as e:
            errors.append(f"scope annotation failed: {e}")

    if errors:
        for e in errors:
            print("[preflight] FAIL:", e)
        return 1
    print(f"[preflight] OK: screen {size} @ {args.density} fixed on android{' + harmony' if args.harmony_serial else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
