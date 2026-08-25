#!/usr/bin/env python3
"""Adversarial helper checks for the strict Phase 4 evidence runtime."""

from __future__ import annotations

import json
import hashlib
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
from capture_state import (  # noqa: E402
    ATTEMPT_LEDGER_FIELDS, reserve_execution, validate_assertion_result,
)
from _common import write_csv  # noqa: E402


def inspector_envelope() -> dict:
    raw_tree = {
        "$ID": "root", "$type": "Column",
        "$rect": {"x": 0, "y": 0, "width": 320, "height": 640},
        "$children": [{"$ID": "login", "$type": "Text", "id": "login", "content": "Login"}],
    }
    digest = hashlib.sha256(json.dumps(
        raw_tree, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    return {
        "schema_version": 1, "source": "ARKUI_UI_CONTEXT",
        "api": "getFilteredInspectorTree", "capture_mode": "OHOS_TEST_BRIDGE",
        "bridge_contract": "arkui-inspector-bridge-v1", "raw_tree": raw_tree,
        "raw_tree_sha256": digest,
    }


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
        del missing["category_contracts"]["UITEST_SNAPSHOT_CAPTURE"]
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

    def test_external_pass_cannot_override_mismatched_actual(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "assertions.json"
            bindings = {
                "parity_id": "PAR-001", "hbuild_id": "HBUILD-001",
                "h4env_id": "H4ENV-001", "device_id": "HDEVICE-001",
                "device_serial": "device-1", "bundle_name": "com.example.fixture",
            }
            plan = [
                {"assertion_id": "ASSERT-VIS", "kind": "VISUAL_STATE", "expected": "visible"},
                {"assertion_id": "ASSERT-BIZ", "kind": "BUSINESS_RESULT", "expected": "saved"},
                {"assertion_id": "ASSERT-INT", "kind": "INTERACTION", "expected": "clicked"},
            ]
            results = [{**item, "actual": item["expected"], "status": "PASS"} for item in plan]
            results[1]["actual"] = "not-saved"
            path.write_text(json.dumps({**bindings, "assertions": results}) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Generated assertion differs"):
                validate_assertion_result(path, plan, bindings)

    @unittest.skip("legacy tree fixture removed; UiTest carrier rejection is covered by test_uitest_evidence")
    def test_runtime_carrier_substitution_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "ui-tree.json"
            value = {
                "bundle_name": "com.example.fixture", "carrier": "DIALOG",
                "target_id": "ROUTE-LOGIN", "window": {"id": "main"},
                "inspector": inspector_envelope(), "operation_trace": [],
                "device": {"device_id": "HDEVICE-001", "serial": "device-1"},
            }
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            contract = {
                "expected_carrier": "PAGE", "target_id": "ROUTE-LOGIN",
                "required_component_ids": [], "component_locators": {},
                "required_event_ids": [], "required_transition_ids": [],
            }
            with self.assertRaisesRegex(ValueError, "Runtime carrier differs"):
                validate_ui_tree(
                    path, "com.example.fixture", "HDEVICE-001", "device-1", contract
                )

    @unittest.skip("legacy tree fixture removed; UiTest trace rejection is covered by test_uitest_evidence")
    def test_event_ids_without_raw_operation_trace_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "ui-tree.json"
            value = {
                "bundle_name": "com.example.fixture", "carrier": "PAGE",
                "target_id": "ROUTE-LOGIN", "window": {"id": "main"},
                "inspector": inspector_envelope(),
                "observed_event_ids": ["EVENT-SUBMIT"], "operation_trace": [],
                "device": {"device_id": "HDEVICE-001", "serial": "device-1"},
            }
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            contract = {
                "expected_carrier": "PAGE", "target_id": "ROUTE-LOGIN",
                "required_component_ids": [], "component_locators": {},
                "required_event_ids": ["EVENT-SUBMIT"], "required_transition_ids": [],
            }
            with self.assertRaisesRegex(ValueError, "operation trace differs"):
                validate_ui_tree(path, "com.example.fixture", "HDEVICE-001", "device-1", contract)

    @unittest.skip("legacy tree fixture removed; production no longer accepts tree evidence")
    def test_plain_script_declared_nodes_cannot_impersonate_inspector(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "ui-tree.json"
            value = {
                "bundle_name": "com.example.fixture", "carrier": "PAGE",
                "target_id": "ROUTE-LOGIN", "window": {"id": "main"},
                "root": {"id": "root"}, "nodes": [{"id": "login"}],
                "operation_trace": [],
                "bounds": {"x": 0, "y": 0, "width": 320, "height": 640},
                "device": {"device_id": "HDEVICE-001", "serial": "device-1"},
            }
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            contract = {
                "expected_carrier": "PAGE", "target_id": "ROUTE-LOGIN",
                "required_component_ids": [], "component_locators": {},
                "required_event_ids": [], "required_transition_ids": [],
            }
            with self.assertRaisesRegex(ValueError, "Inspector envelope"):
                validate_ui_tree(path, "com.example.fixture", "HDEVICE-001", "device-1", contract)

    @unittest.skip("legacy tree fixture removed; UiTest hash rejection is covered by test_uitest_evidence")
    def test_inspector_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "ui-tree.json"
            inspector = inspector_envelope()
            inspector["raw_tree"]["content"] = "tampered"
            value = {
                "bundle_name": "com.example.fixture", "carrier": "PAGE",
                "target_id": "ROUTE-LOGIN", "window": {"id": "main"},
                "inspector": inspector, "operation_trace": [],
                "device": {"device_id": "HDEVICE-001", "serial": "device-1"},
            }
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            contract = {
                "expected_carrier": "PAGE", "target_id": "ROUTE-LOGIN",
                "required_component_ids": [], "component_locators": {},
                "required_event_ids": [], "required_transition_ids": [],
            }
            with self.assertRaisesRegex(ValueError, "raw_tree hash differs"):
                validate_ui_tree(path, "com.example.fixture", "HDEVICE-001", "device-1", contract)

    @unittest.skip("legacy tree fixture removed; UiTest locator binding is covered by test_uitest_evidence")
    def test_component_binding_is_derived_from_inspector_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            path = Path(temp_name) / "ui-tree.json"
            value = {
                "bundle_name": "com.example.fixture", "carrier": "PAGE",
                "target_id": "ROUTE-LOGIN", "window": {"id": "main"},
                "inspector": inspector_envelope(), "operation_trace": [],
                "device": {"device_id": "HDEVICE-001", "serial": "device-1"},
                "nodes": [{"source_component_id": "CMP-LOGIN"}],
            }
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")
            contract = {
                "expected_carrier": "PAGE", "target_id": "ROUTE-LOGIN",
                "required_component_ids": ["CMP-LOGIN"],
                "component_locators": {"CMP-LOGIN": {"resource_id": "login"}},
                "required_event_ids": [], "required_transition_ids": [],
            }
            result = validate_ui_tree(
                path, "com.example.fixture", "HDEVICE-001", "device-1", contract
            )
            self.assertEqual(result["component_bindings"]["CMP-LOGIN"]["basis"], "RESOURCE_ID")
            self.assertNotIn("source_component_id", result["nodes"][0])
            sealed = json.loads(path.read_text(encoding="utf-8"))
            sealed["nodes"][0]["type"] = "Forged"
            with self.assertRaisesRegex(ValueError, "normalized nodes differ"):
                validate_normalized(sealed)
            sealed = json.loads(path.read_text(encoding="utf-8"))
            sealed["inspector"]["normalized_nodes_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "normalized node hash differs"):
                validate_normalized(sealed)

    def test_controller_attempt_chain_prevents_budget_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            run_dir = Path(temp_name)
            workspace = run_dir / "phase-04-harmony-implementation"
            controller = run_dir / "controller"
            workspace.mkdir()
            controller.mkdir()
            write_csv(workspace / "attempt-ledger.csv", ATTEMPT_LEDGER_FIELDS, [])
            write_csv(controller / "phase4-attempt-ledger.csv", ATTEMPT_LEDGER_FIELDS, [])
            for number in range(1, 4):
                reserve_execution(
                    workspace, "PAR-001", f"HEVD-{number:03d}", "executor-001", 2
                )
            with self.assertRaisesRegex(ValueError, "budget exhausted"):
                reserve_execution(workspace, "PAR-001", "HEVD-004", "executor-001", 2)


if __name__ == "__main__":
    unittest.main()
