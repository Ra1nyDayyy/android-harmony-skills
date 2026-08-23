#!/usr/bin/env python3
"""Strict offline Android CLI/ADB double used by the skill tests."""

from __future__ import annotations

import binascii
import json
import os
import struct
import sys
import zlib
from pathlib import Path


def png_solid(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    def chunk(name: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + name + payload + struct.pack(">I", binascii.crc32(name + payload) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanline = b"\x00" + bytes(rgb) * width
    pixels = zlib.compress(scanline * height, level=9)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


def output_path(args: list[str]) -> Path | None:
    for index, arg in enumerate(args):
        if arg.startswith("-o="):
            return Path(arg.split("=", 1)[1])
        if arg in {"-o", "--output"} and index + 1 < len(args):
            return Path(args[index + 1])
        if arg.startswith("--output="):
            return Path(arg.split("=", 1)[1])
    return None


def device_arg(args: list[str]) -> str | None:
    return next((arg.split("=", 1)[1] for arg in args if arg.startswith("--device=")), None)


def main() -> int:
    args = sys.argv[1:]
    log = os.environ.get("FAKE_ANDROID_LOG")
    if log:
        with Path(log).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(args) + "\n")
    fail = os.environ.get("FAKE_ANDROID_FAIL")
    if fail and fail in " ".join(args):
        print(f"forced failure: {fail}", file=sys.stderr)
        return 9
    error_zero = os.environ.get("FAKE_ANDROID_ERROR_ZERO")
    if error_zero and error_zero in " ".join(args):
        print(f"Error: forced zero-exit failure: {error_zero}")
        return 0

    if args == ["devices", "-l"]:
        serials = os.environ.get("FAKE_ADB_DEVICES", "emulator-5554").split(",")
        print("List of devices attached")
        for serial in filter(None, serials):
            print(f"{serial}\tdevice product:fixture model:Pixel_Test transport_id:1")
        return 0
    if args == ["-s", "emulator-5554", "shell", "dumpsys", "activity", "activities"]:
        print("mResumedActivity: ActivityRecord{fixture com.example.fixture/.MainActivity}")
        return 0
    probe_outputs = {
        ("-s", "emulator-5554", "shell", "getprop", "ro.product.model"): "Pixel Test",
        ("-s", "emulator-5554", "shell", "getprop", "ro.build.version.sdk"): "35",
        ("-s", "emulator-5554", "shell", "wm", "size"): "Physical size: 1080x2400",
        ("-s", "emulator-5554", "shell", "wm", "density"): "Physical density: 420",
        ("-s", "emulator-5554", "shell", "dumpsys", "input"): "SurfaceOrientation: 0",
        ("-s", "emulator-5554", "shell", "dumpsys", "display"): "SurfaceOrientation: 0",
        ("-s", "emulator-5554", "shell", "getprop", "persist.sys.locale"): "zh-CN",
        ("-s", "emulator-5554", "shell", "settings", "get", "system", "system_locales"): "zh-CN",
        ("-s", "emulator-5554", "shell", "getprop", "persist.sys.timezone"): "Asia/Shanghai",
        ("-s", "emulator-5554", "shell", "settings", "get", "system", "font_scale"): "1.0",
        ("-s", "emulator-5554", "shell", "cmd", "uimode", "night"): "Night mode: no",
    }
    if tuple(args) in probe_outputs:
        print(probe_outputs[tuple(args)])
        return 0
    if args == ["--version"]:
        print("Android CLI fake-2.0")
        return 0
    if len(args) == 3 and args[:2] == ["manifest", "application-id"]:
        print("com.example.fixture")
        return 0
    if len(args) == 3 and args[:2] == ["manifest", "version-name"]:
        print("1.0.0")
        return 0
    if len(args) == 3 and args[:2] == ["manifest", "version-code"]:
        print("100")
        return 0
    if args and args[0] == "info":
        print(json.dumps({"sdk": "/fake/sdk"}))
        return 0
    if args and args[0] == "describe":
        print(json.dumps({"project": args[-1], "targets": ["fixture"]}))
        return 0
    if "--help" in args:
        print("fake help: " + " ".join(args))
        return 0
    if args and args[0] == "run":
        serial = device_arg(args)
        apk_values = [arg.split("=", 1)[1] for arg in args if arg.startswith("--apks=")]
        if serial != "emulator-5554" or len(apk_values) != 1 or not Path(apk_values[0]).is_file():
            print("Error: invalid run device or APK")
            return 0
        print("launched com.example.fixture")
        return 0
    if args and args[0] == "layout":
        if device_arg(args) != "emulator-5554":
            print("Error: Device not found")
            return 0
        target = output_path(args)
        if target is None:
            print("Error: missing layout output")
            return 0
        state = os.environ.get("FAKE_ANDROID_STATE", "default")
        target.parent.mkdir(parents=True, exist_ok=True)
        if "--diff" in args:
            value = [{"text": "Invalid verification code", "resourceId": "login_error", "center": "[20,20]"}]
        elif state == "invalid-code":
            value = [
                {"text": "Login", "resourceId": "login", "center": "[10,10]"},
                {"text": "Invalid verification code", "resourceId": "login_error", "center": "[20,20]"},
            ]
        else:
            value = [{"text": "Login", "resourceId": "login", "center": "[10,10]"}]
        target.write_text(json.dumps(value), encoding="utf-8")
        return 0
    if args[:2] == ["screen", "capture"]:
        if device_arg(args) != "emulator-5554":
            print("Error: Device not found")
            return 0
        target = output_path(args)
        if target is None:
            print("Error: missing screenshot output")
            return 0
        target.parent.mkdir(parents=True, exist_ok=True)
        state = os.environ.get("FAKE_ANDROID_STATE", "default")
        target.write_bytes(png_solid(1080, 2400, (220, 20, 60) if state == "invalid-code" else (30, 144, 255)))
        return 0
    print("unsupported fake Android CLI command: " + " ".join(args), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
