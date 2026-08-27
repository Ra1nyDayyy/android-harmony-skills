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
from _stage4_audit import (  # noqa: E402
    EVIDENCE_SEQUENCE,
    LITE_COMPONENT_OVERLAP_MIN,
    LITE_EVIDENCE_SEQUENCE,
    _lite_component_overlap,
    evidence_sequence_for,
    validate_uitest_evidence_lite,
)


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


def lite_component(component_id: str, text: str = "", component_type: str = "Text") -> dict[str, object]:
    return {
        "component_id": component_id, "type": component_type, "text": text,
        "bounds": {"left": 0, "top": 0, "right": 10, "bottom": 10},
        "visible": True, "enabled": True, "clickable": False,
        "visibility_basis": "UNIQUE_MATCH_AND_VALID_BOUNDS",
        "locator_strategy": "ID", "locator_value": component_id, "match_count": 1,
    }


class LiteComponentOverlapTest(unittest.TestCase):
    """LITE 结构化组件树对比（内联自 compare_component_tree.compare_components）。"""

    def test_overlap_rate_formula_and_threshold_boundary(self) -> None:
        expected = [{"component_id": f"COMP-{i}", "type": "Text", "text": str(i)} for i in range(10)]
        # 8/10 匹配（2 MISSING）→ 一致率 0.8，恰达阈值。
        overlap, differences = _lite_component_overlap(
            expected, [dict(row) for row in expected[:8]],
        )
        self.assertEqual(0.8, overlap)
        self.assertEqual(0.8, LITE_COMPONENT_OVERLAP_MIN)
        self.assertEqual({"MISSING_COMPONENT"}, {item["kind"] for item in differences})
        # 6/10 匹配 → 0.6，低于阈值。
        overlap_low, _ = _lite_component_overlap(
            expected, [dict(row) for row in expected[:6]],
        )
        self.assertEqual(0.6, overlap_low)
        self.assertLess(overlap_low, LITE_COMPONENT_OVERLAP_MIN)
        # UNEXPECTED 与 type/text 不匹配同样计入差异。
        overlap_unexpected, differences_unexpected = _lite_component_overlap(
            expected[:1],
            [dict(expected[0]), {"component_id": "COMP-GHOST", "type": "Text", "text": ""}],
        )
        self.assertEqual(0.0, overlap_unexpected)
        overlap_type, differences_type = _lite_component_overlap(
            [{"component_id": "COMP-0", "type": "textview", "text": "x"}],
            [{"component_id": "COMP-0", "type": "text", "text": "x"}],
        )
        self.assertEqual(1.0, overlap_type)  # 归一化 type 等价（textview→text）
        overlap_text, _ = _lite_component_overlap(
            [{"component_id": "COMP-0", "type": "Text", "text": "A"}],
            [{"component_id": "COMP-0", "type": "Text", "text": "B"}],
        )
        self.assertEqual(0.0, overlap_text)

    def test_evidence_sequence_for_tiers(self) -> None:
        self.assertEqual(EVIDENCE_SEQUENCE, evidence_sequence_for("CORE"))
        self.assertEqual(EVIDENCE_SEQUENCE, evidence_sequence_for(""))
        self.assertEqual(
            [
                "DEVICE_CHECK", "CLEAN_INSTALL", "LAUNCH", "NAVIGATE",
                "SCREENSHOT_CAPTURE", "UITEST_SNAPSHOT_CAPTURE",
            ],
            LITE_EVIDENCE_SEQUENCE,
        )
        self.assertEqual(LITE_EVIDENCE_SEQUENCE, evidence_sequence_for("LITE"))
        with self.assertRaisesRegex(ValueError, "must be one of"):
            evidence_sequence_for("FAST")


class LiteUiTestEvidenceTest(unittest.TestCase):
    """LITE 页 validate 轻校验：80% 组件集 PASS、60% FAIL（哈希绑定保留）。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="uitest-lite-")
        self.root = Path(self.temp.name)
        self.screenshot = self.root / "ui-test-snapshot.png"
        self.screenshot.write_bytes(b"uitest-lite-png")
        self.trace = self.root / "ui-test-snapshot-operation-trace.json"
        write_json(self.trace, [
            {"subject_type": "EVENT", "subject_id": f"EVENT-{i}", "action": "CLICK",
             "component_id": f"COMP-{i}", "observable_result": "seen"}
            for i in range(5)
        ])
        self.hashes = {
            "test_hap_sha256": "1" * 64, "final_hap_sha256": "2" * 64,
            "device_identity_sha256": "3" * 64, "command_sha256": "4" * 64,
        }
        self.probe = {
            "probe_id": "PAGE-HOME::STATE-READY", "page_id": "PAGE-HOME",
            "state_id": "STATE-READY", "target_id": "pages/Home",
            "required_components": [],
            "result_directory": "ui-test-snapshot/PAGE-HOME/STATE-READY",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write_package(self, components: list[dict[str, object]]) -> None:
        result = self.root / "ui-test-snapshot.json"
        write_json(result, {"probe_id": "PAGE-HOME::STATE-READY", "components": components})
        write_json(self.root / "ui-test-snapshot-metadata.json", {
            "schema_version": "ui-test-snapshot-evidence-v1",
            "probe_id": "PAGE-HOME::STATE-READY", "page_id": "PAGE-HOME",
            "state_id": "STATE-READY", "bundle_name": "com.example.app",
            "carrier": "PAGE", "target_id": "pages/Home",
            "result_path": "ui-test-snapshot.json",
            "result_sha256": sha(result.read_bytes()),
            "operation_trace_path": "ui-test-snapshot-operation-trace.json",
            "operation_trace_sha256": sha(self.trace.read_bytes()),
            "screenshot_path": "ui-test-snapshot.png",
            "screenshot_sha256": sha(self.screenshot.read_bytes()),
            "generation_manifest_sha256": "5" * 64, "page_plan_sha256": "6" * 64,
            **self.hashes,
        })

    def _validate(self, expected_components: list[dict[str, object]]) -> dict:
        return validate_uitest_evidence_lite(
            self.root, self.probe,
            page_id="PAGE-HOME", state_id="STATE-READY", bundle_name="com.example.app",
            carrier="PAGE", target_id="pages/Home",
            generation_manifest_sha256="5" * 64, page_plan_sha256="6" * 64,
            expected_components=expected_components, **self.hashes,
        )

    def test_eighty_percent_overlap_passes_and_sixty_fails(self) -> None:
        expected = [{"component_id": f"COMP-{i}", "type": "Text", "text": str(i)} for i in range(10)]
        # 80% 一致（8 命中 + 2 缺失）→ PASS，且 match_count 严检已放宽。
        actual_80 = [lite_component(f"COMP-{i}", str(i)) for i in range(8)]
        actual_80[0]["match_count"] = 2  # LITE 不再要求 match_count==1
        self._write_package(actual_80)
        value = self._validate(expected)
        self.assertEqual(0.8, value["lite_component_overlap"])
        # 60% 一致 → FAIL（低于 0.8 阈值）。
        self._write_package([lite_component(f"COMP-{i}", str(i)) for i in range(6)])
        with self.assertRaisesRegex(ValueError, "overlap 0.600 is below 0.8"):
            self._validate(expected)

    def test_lite_keeps_hash_binding_and_identity_checks(self) -> None:
        expected = [{"component_id": "COMP-0", "type": "Text", "text": "0"}]
        self._write_package([lite_component("COMP-0", "0")])
        # 哈希篡改仍被拒（哈希绑定保留）。
        metadata = json.loads((self.root / "ui-test-snapshot-metadata.json").read_text(encoding="utf-8"))
        metadata["final_hap_sha256"] = "9" * 64
        write_json(self.root / "ui-test-snapshot-metadata.json", metadata)
        with self.assertRaisesRegex(ValueError, "final_hap_sha256"):
            self._validate(expected)
        # 空组件集合被拒（组件集合非空要求）。
        self._write_package([])
        with self.assertRaisesRegex(ValueError, "empty or malformed"):
            self._validate(expected)


if __name__ == "__main__":
    unittest.main()
