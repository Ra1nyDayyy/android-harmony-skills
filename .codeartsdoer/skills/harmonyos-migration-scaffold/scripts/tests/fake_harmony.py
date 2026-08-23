#!/usr/bin/env python3
"""Strict, stateful HarmonyOS CLI double used only by Phase 3 tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import zipfile
import zlib
from pathlib import Path


SERIAL = "fixture-001"
BUNDLE = "com.example.fixture"


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def write_png(path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanline = b"\x00" + bytes((33, 150, 243)) * width
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", zlib.compress(scanline * height, 9))
        + png_chunk(b"IEND", b"")
    )


def state_path() -> Path:
    return Path.cwd() / "build" / ".fake-harmony-state.json"


def load_state() -> dict[str, object]:
    path = state_path()
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(value: dict[str, object]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def require_identity(serial: str | None = None, bundle: str | None = None) -> None:
    if serial is not None and serial != SERIAL:
        raise ValueError(f"unexpected serial: {serial}")
    if bundle is not None and bundle != BUNDLE:
        raise ValueError(f"unexpected bundle: {bundle}")


def maybe_error_zero(action: str) -> bool:
    if os.environ.get("FAKE_HARMONY_ERROR_ZERO") == action:
        print(f"Error: forced zero-exit failure for {action}")
        return True
    return False


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="action", required=True)
    sub.add_parser("toolchain")
    device = sub.add_parser("device")
    device.add_argument("--serial", required=True)
    bundle = sub.add_parser("bundle")
    bundle.add_argument("--serial", required=True)
    bundle.add_argument("--bundle", required=True)
    signing = sub.add_parser("signing")
    signing.add_argument("--bundle", required=True)
    build = sub.add_parser("build")
    build.add_argument("--artifact", required=True)
    install = sub.add_parser("install")
    install.add_argument("--serial", required=True)
    install.add_argument("--bundle")
    install.add_argument("--artifact", required=True)
    launch = sub.add_parser("launch")
    launch.add_argument("--serial", required=True)
    launch.add_argument("--bundle", required=True)
    launch.add_argument("--ability", required=True)
    seed = sub.add_parser("seed-reset")
    seed.add_argument("--serial", required=True)
    seed.add_argument("--bundle", required=True)
    network = sub.add_parser("network-profile")
    network.add_argument("--serial", required=True)
    network.add_argument("--profile", required=True)
    permission = sub.add_parser("permission-profile")
    permission.add_argument("--serial", required=True)
    permission.add_argument("--bundle", required=True)
    permission.add_argument("--profile", required=True)
    navigate = sub.add_parser("navigate")
    navigate.add_argument("--serial", required=True)
    navigate.add_argument("--bundle", required=True)
    navigate.add_argument("--target", required=True)
    assertion = sub.add_parser("business-assert")
    assertion.add_argument("--serial", required=True)
    assertion.add_argument("--bundle", required=True)
    assertion.add_argument("--target", required=True)
    assertion.add_argument("--parity", required=True)
    assertion.add_argument("--build", required=True)
    assertion.add_argument("--env", required=True)
    assertion.add_argument("--output", required=True)
    tree = sub.add_parser("ui-tree")
    tree.add_argument("--serial", required=True)
    tree.add_argument("--bundle", required=True)
    tree.add_argument("--target", required=True)
    tree.add_argument("--output", required=True)
    smoke = sub.add_parser("smoke")
    smoke.add_argument("--serial", required=True)
    smoke.add_argument("--bundle", required=True)
    smoke.add_argument("--kind", choices=("ROUTE_PAGE", "VISUAL_SURFACE"), required=True)
    smoke.add_argument("--target", required=True)
    smoke.add_argument("--page", required=True)
    smoke.add_argument("--shell", required=True)
    smoke.add_argument("--result", required=True)
    screenshot = sub.add_parser("screenshot")
    screenshot.add_argument("--serial", required=True)
    screenshot.add_argument("--bundle")
    screenshot.add_argument("--target", required=True)
    screenshot.add_argument("--output", required=True)
    screenshot.add_argument("--width", type=int, required=True)
    screenshot.add_argument("--height", type=int, required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    if maybe_error_zero(args.action):
        return 0
    try:
        if args.action == "toolchain":
            print("TOOLCHAIN_OK version=fixture-1")
            return 0
        if args.action == "device":
            require_identity(serial=args.serial)
            print(f"DEVICE_OK serial={args.serial} model=Fixture api=test resolution=320x640")
            return 0
        if args.action == "bundle":
            require_identity(args.serial, args.bundle)
            print(f"BUNDLE_OK serial={args.serial} bundle={args.bundle} conflict=none")
            return 0
        if args.action == "signing":
            require_identity(bundle=args.bundle)
            print(f"SIGNING_OK bundle={args.bundle} fingerprint={'A' * 64}")
            return 0
        if args.action == "build":
            artifact = Path(args.artifact)
            artifact.parent.mkdir(parents=True, exist_ok=True)
            state = load_state()
            build_count = int(state.get("build_count", 0)) + 1
            with zipfile.ZipFile(artifact, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("module.json", json.dumps({"module": {"name": "entry"}, "bundleName": BUNDLE}))
                archive.writestr("ets/modules.abc", f"fixture-bytecode-{build_count}".encode("utf-8"))
                archive.writestr("resources.index", b"fixture-resources")
            state.update({"artifact": str(artifact), "built": True, "build_count": build_count})
            save_state(state)
            print(f"BUILD_OK artifact={artifact}")
            return 0
        if args.action == "install":
            require_identity(serial=args.serial, bundle=args.bundle)
            artifact = Path(args.artifact)
            if not artifact.is_file() or not zipfile.is_zipfile(artifact):
                raise ValueError("install requires the newly built HAP")
            state = load_state()
            if state.get("built") is not True:
                raise ValueError("install did not follow a clean build")
            state["installed"] = True
            save_state(state)
            print(f"INSTALL_OK serial={args.serial} artifact={artifact}")
            return 0
        if args.action == "launch":
            require_identity(args.serial, args.bundle)
            state = load_state()
            if state.get("installed") is not True:
                raise ValueError("launch requires install")
            state.update({"launched": True, "ability": args.ability})
            save_state(state)
            print(f"LAUNCH_OK serial={args.serial} bundle={args.bundle} ability={args.ability}")
            return 0
        if args.action == "seed-reset":
            require_identity(args.serial, args.bundle)
            print(f"SEED_RESET_OK serial={args.serial} bundle={args.bundle}")
            return 0
        if args.action == "network-profile":
            require_identity(serial=args.serial)
            print(f"NETWORK_PROFILE_OK serial={args.serial} profile={args.profile}")
            return 0
        if args.action == "permission-profile":
            require_identity(args.serial, args.bundle)
            print(f"PERMISSION_PROFILE_OK serial={args.serial} profile={args.profile}")
            return 0
        if args.action == "navigate":
            require_identity(args.serial, args.bundle)
            state = load_state()
            if state.get("launched") is not True:
                raise ValueError("navigate requires launch")
            state.update({"smoke_target": args.target, "smoke_kind": "ROUTE_PAGE"})
            save_state(state)
            print(f"NAVIGATE_OK serial={args.serial} target={args.target}")
            return 0
        if args.action == "business-assert":
            require_identity(args.serial, args.bundle)
            state = load_state()
            if state.get("smoke_target") != args.target:
                raise ValueError("business assertion target has no preceding navigation")
            result = {
                "parity_id": args.parity,
                "hbuild_id": args.build,
                "h4env_id": args.env,
                "device_id": "HDEVICE-001",
                "device_serial": args.serial,
                "bundle_name": args.bundle,
                "assertions": [
                    {
                        "assertion_id": "ASSERT-VISUAL", "kind": "VISUAL_STATE",
                        "expected": "visible", "actual": "visible", "status": "PASS",
                    },
                    {
                        "assertion_id": "ASSERT-BUSINESS", "kind": "BUSINESS_RESULT",
                        "expected": "login-ready", "actual": "login-ready", "status": "PASS",
                        "subject_ids": [
                            "BR-AUTH-NONE", "DATA-AUTH-NONE", "SYS-AUTH-NONE", "SDK-AUTH-NONE"
                        ],
                    },
                    {
                        "assertion_id": "ASSERT-INTERACTION", "kind": "INTERACTION",
                        "expected": "tap-ready", "actual": "tap-ready", "status": "PASS",
                    },
                    {
                        "assertion_id": "ASSERT-ANDROID-OBSERVABLE",
                        "kind": "ANDROID_EXPECTED_OBSERVABLE",
                        "expected": "Login form is visible", "actual": "Login form is visible",
                        "subject_ids": ["INV-AUTH-LOGIN-DEFAULT"], "status": "PASS",
                    },
                ],
            }
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
            print(f"BUSINESS_ASSERT_OK serial={args.serial} target={args.target}")
            return 0
        if args.action == "ui-tree":
            require_identity(args.serial, args.bundle)
            state = load_state()
            if state.get("smoke_target") != args.target:
                raise ValueError("UI tree target has no preceding navigation")
            raw_tree = {
                "$ID": "root", "$type": "Column",
                "$rect": {"x": 0, "y": 0, "width": 320, "height": 640},
                "$children": [
                    {"$ID": "login", "$type": "Text", "id": "login", "content": "Login",
                     "$rect": {"x": 16, "y": 20, "width": 120, "height": 40}}
                ],
            }
            raw_digest = hashlib.sha256(json.dumps(
                raw_tree, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest()
            result = {
                "bundle_name": args.bundle,
                "carrier": "PAGE",
                "target_id": args.target,
                "window": {"id": "main"},
                "inspector": {
                    "schema_version": 1, "source": "ARKUI_UI_CONTEXT",
                    "api": "getFilteredInspectorTree", "capture_mode": "OHOS_TEST_BRIDGE",
                    "bridge_contract": "arkui-inspector-bridge-v1", "raw_tree": raw_tree,
                    "raw_tree_sha256": raw_digest,
                },
                "operation_trace": [],
                "device": {"device_id": "HDEVICE-001", "serial": args.serial},
            }
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
            print(f"UI_TREE_OK serial={args.serial} target={args.target}")
            return 0
        if args.action == "smoke":
            require_identity(args.serial, args.bundle)
            state = load_state()
            if state.get("launched") is not True:
                raise ValueError("smoke requires launch")
            result_path = Path(args.result)
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result = {
                "target_kind": args.kind,
                "target_id": args.target,
                "page_id": args.page,
                "page_shell_id": args.shell,
                "device_id": "HDEVICE-001",
                "device_serial": args.serial,
                "bundle_name": args.bundle,
                "status": "PASS",
            }
            if args.kind == "ROUTE_PAGE":
                result["route_id"] = args.target
            else:
                result["surface_shell_id"] = args.target
            result_path.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
            state.update({"smoke_target": args.target, "smoke_kind": args.kind})
            save_state(state)
            print(f"SMOKE_OK serial={args.serial} target={args.target}")
            return 0
        if args.action == "screenshot":
            require_identity(serial=args.serial, bundle=args.bundle)
            state = load_state()
            if state.get("smoke_target") != args.target:
                raise ValueError("screenshot target has no preceding smoke")
            write_png(Path(args.output), args.width, args.height)
            print(f"SCREENSHOT_OK serial={args.serial} target={args.target} output={args.output}")
            return 0
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 9
    print(f"Error: unsupported action {args.action}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
