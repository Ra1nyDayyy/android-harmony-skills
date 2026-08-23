#!/usr/bin/env python3
"""Strict Phase 4 CLI double used only by the runtime end-to-end test."""

from __future__ import annotations

import argparse
import json
import struct
import zipfile
import zlib
from pathlib import Path


SERIAL = "fixture-001"
BUNDLE = "com.example.fixture"
TARGET = "ROUTE-LOGIN"


def chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload)) + kind + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def screenshot(path: Path, width: int = 8, height: int = 12) -> None:
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = (b"\x00" + bytes((20, 40, 60)) * width) * height
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="action", required=True)
    commands.add_parser("toolchain")
    build = commands.add_parser("clean-build")
    build.add_argument("--artifact", required=True)
    bundle = commands.add_parser("bundle-check")
    bundle.add_argument("--serial", required=True)
    bundle.add_argument("--bundle", required=True)
    signing = commands.add_parser("signing-check")
    signing.add_argument("--bundle", required=True)
    device = commands.add_parser("device-check")
    device.add_argument("--serial", required=True)
    install = commands.add_parser("clean-install")
    install.add_argument("--serial", required=True)
    install.add_argument("--artifact", required=True)
    seed = commands.add_parser("seed-reset")
    seed.add_argument("--serial", required=True)
    seed.add_argument("--bundle", required=True)
    network = commands.add_parser("network-profile")
    network.add_argument("--serial", required=True)
    network.add_argument("--profile", required=True)
    permission = commands.add_parser("permission-profile")
    permission.add_argument("--serial", required=True)
    permission.add_argument("--bundle", required=True)
    permission.add_argument("--profile", required=True)
    launch = commands.add_parser("launch")
    launch.add_argument("--serial", required=True)
    launch.add_argument("--bundle", required=True)
    for action in ("navigate", "business-assert", "screenshot", "ui-tree"):
        item = commands.add_parser(action)
        item.add_argument("--serial", required=True)
        item.add_argument("--bundle", required=True)
        item.add_argument("--target", required=True)
        if action != "navigate":
            item.add_argument("--output", required=True)
    return root


def require_identity(args: argparse.Namespace) -> None:
    if hasattr(args, "serial") and args.serial != SERIAL:
        raise ValueError("wrong serial")
    if hasattr(args, "bundle") and args.bundle != BUNDLE:
        raise ValueError("wrong Bundle")
    if hasattr(args, "target") and args.target != TARGET:
        raise ValueError("wrong target")


def main() -> int:
    args = parser().parse_args()
    require_identity(args)
    if args.action == "toolchain":
        print("TOOLCHAIN_OK")
    elif args.action == "clean-build":
        path = Path(args.artifact)
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("module.json", json.dumps({"module": {"name": "entry"}}))
            archive.writestr("ets/modules.abc", b"fixture")
        print("CLEAN_BUILD_OK")
    elif args.action == "bundle-check":
        print("BUNDLE_CHECK_OK")
    elif args.action == "signing-check":
        print("SIGNING_CHECK_OK")
    elif args.action == "device-check":
        print("DEVICE_CHECK_OK")
    elif args.action == "clean-install":
        if not zipfile.is_zipfile(args.artifact):
            raise ValueError("artifact is not a HAP ZIP")
        print("CLEAN_INSTALL_OK")
    elif args.action == "seed-reset":
        print("SEED_RESET_OK")
    elif args.action == "network-profile":
        print("NETWORK_PROFILE_OK")
    elif args.action == "permission-profile":
        print("PERMISSION_PROFILE_OK")
    elif args.action == "launch":
        print("LAUNCH_OK")
    elif args.action == "navigate":
        print("NAVIGATE_OK")
    elif args.action == "business-assert":
        value = {
            "parity_id": "PAR-LOGIN",
            "hbuild_id": "HBUILD-001",
            "h4env_id": "H4ENV-001",
            "device_id": "HDEVICE-001",
            "device_serial": SERIAL,
            "bundle_name": BUNDLE,
            "assertions": [
                {
                    "assertion_id": "ASSERT-VISUAL", "kind": "VISUAL_STATE",
                    "expected": "visible", "actual": "visible", "status": "PASS",
                },
                {
                    "assertion_id": "ASSERT-BUSINESS", "kind": "BUSINESS_RESULT",
                    "expected": "saved", "actual": "saved", "status": "PASS",
                },
                {
                    "assertion_id": "ASSERT-INTERACTION", "kind": "INTERACTION",
                    "expected": "clicked", "actual": "clicked", "status": "PASS",
                },
                {
                    "assertion_id": "ASSERT-CAP", "kind": "CAPABILITY_RESULT",
                    "expected": "granted", "actual": "granted", "status": "PASS",
                    "subject_ids": ["HREQ-CAMERA"],
                },
            ],
        }
        Path(args.output).write_text(json.dumps(value) + "\n", encoding="utf-8")
        print("BUSINESS_ASSERT_OK")
    elif args.action == "screenshot":
        screenshot(Path(args.output))
        print("SCREENSHOT_OK")
    elif args.action == "ui-tree":
        value = {
            "bundle_name": BUNDLE,
            "window": {"id": "main"},
            "root": {"id": "root"},
            "nodes": [{"id": "login", "text": "Login"}],
            "bounds": {"x": 0, "y": 0, "width": 8, "height": 12},
            "device": {"device_id": "HDEVICE-001", "serial": SERIAL},
        }
        Path(args.output).write_text(json.dumps(value) + "\n", encoding="utf-8")
        print("UI_TREE_OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Error: {exc}")
        raise SystemExit(9)
