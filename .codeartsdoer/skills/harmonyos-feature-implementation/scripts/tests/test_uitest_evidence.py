#!/usr/bin/env python3
"""Fail-closed tests for formal UiTest snapshot evidence."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
SKILL = SCRIPTS.parent
CONTROLLER = SKILL.parent / "android-harmony-migration-controller" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from uitest_snapshot import validate_uitest_evidence  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class UiTestEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="uitest-evidence-")
        self.root = Path(self.temp.name)
        self.screenshot = self.root / "ui-test-snapshot.png"
        self.screenshot.write_bytes(b"uitest-png-evidence")
        self.trace = self.root / "ui-test-snapshot-operation-trace.json"
        write_json(self.trace, [{
            "subject_type": "EVENT", "subject_id": "EVENT-OPEN", "action": "CLICK",
            "component_id": "COMP-OPEN", "observable_result": "PAGE-DETAIL visible",
        }])
        self.result = self.root / "ui-test-snapshot.json"
        write_json(self.result, {
            "probe_id": "PAGE-HOME::STATE-READY",
            "components": [{
                "component_id": "COMP-OPEN", "type": "Button", "text": "Open",
                "bounds": {"left": 10, "top": 20, "right": 110, "bottom": 68},
                "visible": True, "enabled": True, "clickable": True,
                "visibility_basis": "UNIQUE_MATCH_AND_VALID_BOUNDS",
                "locator_strategy": "ID", "locator_value": "COMP-OPEN", "match_count": 1,
            }],
        })
        self.hashes = {
            "test_hap_sha256": "1" * 64, "final_hap_sha256": "2" * 64,
            "device_identity_sha256": "3" * 64, "command_sha256": "4" * 64,
        }
        self.metadata = self.root / "ui-test-snapshot-metadata.json"
        self._write_metadata()
        self.probe = {
            "probe_id": "PAGE-HOME::STATE-READY", "page_id": "PAGE-HOME",
            "state_id": "STATE-READY", "target_id": "pages/Home",
            "required_components": [{
                "component_id": "COMP-OPEN", "source_type": "Button", "arkts_type": "Button",
                "locator_strategy": "ID", "locator_value": "COMP-OPEN", "expected_text": "Open",
            }],
            "declared_actions": [{"event_id": "EVENT-OPEN", "component_id": "COMP-OPEN", "action": "CLICK"}],
            "result_directory": "ui-test-snapshot/PAGE-HOME/STATE-READY",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_metadata(self) -> None:
        write_json(self.metadata, {
            "schema_version": "ui-test-snapshot-evidence-v1",
            "probe_id": "PAGE-HOME::STATE-READY", "page_id": "PAGE-HOME",
            "state_id": "STATE-READY", "bundle_name": "com.example.app",
            "carrier": "PAGE", "target_id": "pages/Home",
            "result_path": "ui-test-snapshot.json",
            "result_sha256": sha(self.result.read_bytes()),
            "operation_trace_path": "ui-test-snapshot-operation-trace.json",
            "operation_trace_sha256": sha(self.trace.read_bytes()),
            "screenshot_path": "ui-test-snapshot.png",
            "screenshot_sha256": sha(self.screenshot.read_bytes()),
            "generation_manifest_sha256": "5" * 64, "page_plan_sha256": "6" * 64,
            **self.hashes,
        })

    def validate(self) -> dict:
        return validate_uitest_evidence(
            self.root, self.probe,
            page_id="PAGE-HOME", state_id="STATE-READY", bundle_name="com.example.app",
            carrier="PAGE", target_id="pages/Home",
            generation_manifest_sha256="5" * 64, page_plan_sha256="6" * 64,
            required_event_ids={"EVENT-OPEN"}, required_transition_ids=set(),
            **self.hashes,
        )

    def test_accepts_complete_hash_bound_snapshot(self) -> None:
        value = self.validate()
        self.assertEqual("COMP-OPEN", value["component_bindings"]["COMP-OPEN"]["component_id"])

    def test_missing_component_nonunique_locator_and_hash_tamper_fail(self) -> None:
        result = json.loads(self.result.read_text(encoding="utf-8"))
        result["components"] = []
        write_json(self.result, result)
        self._write_metadata()
        with self.assertRaisesRegex(ValueError, "required component"):
            self.validate()
        result["components"] = [{
            "component_id": "COMP-OPEN", "type": "Button", "text": "Open",
            "bounds": {"left": 0, "top": 0, "right": 1, "bottom": 1},
            "visible": True, "enabled": True, "clickable": True,
            "visibility_basis": "UNIQUE_MATCH_AND_VALID_BOUNDS",
            "locator_strategy": "ID", "locator_value": "COMP-OPEN", "match_count": 2,
        }]
        write_json(self.result, result)
        self._write_metadata()
        with self.assertRaisesRegex(ValueError, "not unique"):
            self.validate()
        metadata = json.loads(self.metadata.read_text(encoding="utf-8"))
        metadata["final_hap_sha256"] = "9" * 64
        write_json(self.metadata, metadata)
        with self.assertRaisesRegex(ValueError, "final_hap_sha256"):
            self.validate()

    def test_missing_page_state_and_operation_trace_fail(self) -> None:
        metadata = json.loads(self.metadata.read_text(encoding="utf-8"))
        metadata["state_id"] = "STATE-OTHER"
        write_json(self.metadata, metadata)
        with self.assertRaisesRegex(ValueError, "state_id"):
            self.validate()
        self._write_metadata()
        write_json(self.trace, [])
        self._write_metadata()
        with self.assertRaisesRegex(ValueError, "operation trace"):
            self.validate()

    def test_default_production_chain_contains_no_inspector_contract(self) -> None:
        paths = [
            SCRIPTS / "capture_state.py", SCRIPTS / "_stage4_audit.py",
            SCRIPTS / "validate_stage4.py", CONTROLLER / "validate_gate.py",
            SKILL / "assets" / "state-verification-plan.template.json",
            SKILL / "assets" / "phase4-environment.template.json",
        ]
        joined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        for forbidden in ("arkui_inspector", "getFilteredInspectorTree", "arkui-inspector-bridge"):
            self.assertNotIn(forbidden, joined)
        self.assertIn("UITEST_SNAPSHOT_CAPTURE", joined)


if __name__ == "__main__":
    unittest.main()
