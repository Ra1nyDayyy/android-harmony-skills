#!/usr/bin/env python3
"""Adversarial helper checks for the strict Phase 4 evidence runtime."""

from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from _common import (  # noqa: E402
    PHASE4_CATEGORY_ORDER,
    frozen_category_contracts,
    frozen_output_verdict,
    png_dimensions,
    selector_is_present,
    sha256_file,
    validate_hap,
)
from capture_state import validate_assertion_result  # noqa: E402


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload)) + kind + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def valid_png(width: int = 4, height: int = 3) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanlines = (b"\x00" + bytes((10, 20, 30)) * width) * height
    return (
        b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", zlib.compress(scanlines)) + png_chunk(b"IEND", b"")
    )


class Phase4CommonTest(unittest.TestCase):
    def test_category_contracts_are_exact_and_hash_bound(self) -> None:
        executable = Path(sys.executable).resolve()
        contract = {
            "resolved_executable": str(executable),
            "executable_sha256": sha256_file(executable),
            "required_argv_tokens": ["action"],
            "success_output_contains": ["ONE", "TWO"],
            "error_output_contains": ["Error:"],
        }
        environment = {
            "category_contracts": {
                category: dict(contract) for category in PHASE4_CATEGORY_ORDER
            }
        }
        normalized = frozen_category_contracts(environment)
        self.assertEqual(set(normalized), set(PHASE4_CATEGORY_ORDER))
        missing = json.loads(json.dumps(environment))
        del missing["category_contracts"]["UI_TREE_CAPTURE"]
        with self.assertRaises(ValueError):
            frozen_category_contracts(missing)
        changed = json.loads(json.dumps(environment))
        changed["category_contracts"]["TOOLCHAIN"]["executable_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            frozen_category_contracts(changed)
        ok, successes, errors = frozen_output_verdict("ONE only", "", normalized["TOOLCHAIN"])
        self.assertFalse(ok)
        self.assertEqual(successes, ["ONE"])
        self.assertEqual(errors, [])
        ok, _, errors = frozen_output_verdict("ONE TWO", "Error: hidden", normalized["TOOLCHAIN"])
        self.assertFalse(ok)
        self.assertEqual(errors, ["Error:"])

    def test_png_requires_complete_crc_valid_stream(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            valid = root / "valid.png"
            valid.write_bytes(valid_png())
            self.assertEqual(png_dimensions(valid), (4, 3))
            trailing = root / "trailing.png"
            trailing.write_bytes(valid_png() + b"tampered")
            with self.assertRaises(ValueError):
                png_dimensions(trailing)
            corrupt = bytearray(valid_png())
            corrupt[-5] ^= 1
            corrupt_path = root / "corrupt.png"
            corrupt_path.write_bytes(corrupt)
            with self.assertRaises(ValueError):
                png_dimensions(corrupt_path)

    def test_hap_requires_zip_and_module_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            valid = root / "valid.hap"
            with zipfile.ZipFile(valid, "w") as archive:
                archive.writestr("module.json", "{}")
                archive.writestr("ets/modules.abc", b"fixture")
            validate_hap(valid)
            missing = root / "missing.hap"
            with zipfile.ZipFile(missing, "w") as archive:
                archive.writestr("payload.bin", b"fixture")
            with self.assertRaises(ValueError):
                validate_hap(missing)
            plain = root / "plain.hap"
            plain.write_text("not a zip", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_hap(plain)

    def test_device_selector_must_be_contiguous_and_exact(self) -> None:
        self.assertTrue(selector_is_present(["tool", "--serial", "device-1"], ["--serial", "device-1"]))
        self.assertFalse(
            selector_is_present(["tool", "--serial", "other", "device-1"], ["--serial", "device-1"])
        )

    def test_live_assertion_subject_binding_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "assertions.json"
            bindings = {
                "parity_id": "PAR-001",
                "hbuild_id": "HBUILD-001",
                "h4env_id": "H4ENV-001",
                "device_id": "HDEVICE-001",
                "device_serial": "device-1",
                "bundle_name": "com.example.fixture",
            }
            plan = [
                {
                    "assertion_id": "ASSERT-CAP",
                    "kind": "CAPABILITY_RESULT",
                    "expected": "granted",
                    "subject_ids": ["HREQ-CAMERA"],
                },
                {"assertion_id": "ASSERT-VIS", "kind": "VISUAL_STATE", "expected": "visible"},
                {"assertion_id": "ASSERT-BIZ", "kind": "BUSINESS_RESULT", "expected": "saved"},
                {"assertion_id": "ASSERT-INT", "kind": "INTERACTION", "expected": "clicked"},
            ]
            results = []
            for item in plan:
                result = {**item, "actual": item["expected"], "status": "PASS"}
                results.append(result)
            path.write_text(json.dumps({**bindings, "assertions": results}) + "\n", encoding="utf-8")
            validate_assertion_result(path, plan, bindings)
            results[0]["subject_ids"] = ["HREQ-OTHER"]
            path.write_text(json.dumps({**bindings, "assertions": results}) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_assertion_result(path, plan, bindings)


if __name__ == "__main__":
    unittest.main()
