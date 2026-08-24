from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover - CI dependency contract
    raise AssertionError("Install requirements-ci.txt before comparator tests") from exc


SCRIPTS = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "comparators"
sys.path.insert(0, str(SCRIPTS))

from compare_migration_unit import compare_page_state  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DeterministicComparatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="deterministic-comparator-")
        self.root = Path(self.temp.name)
        self.contract = json.loads((FIXTURES / "calculator-contract.json").read_text(encoding="utf-8"))
        self.scenarios = json.loads((FIXTURES / "calculator-scenarios.json").read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def materialize(self, name: str) -> Path:
        scenario = self.scenarios[name]
        android = self.root / name / "inputs" / "android-evidence" / "EVD-CALC"
        actual = self.root / name / "actual"
        android.mkdir(parents=True)
        actual.mkdir(parents=True)
        Image.new("RGB", (64, 64), (240, 240, 240)).save(android / "screenshot.png")
        self.contract["android_evidence_hashes"][0]["screenshot_sha256"] = sha256(android / "screenshot.png")

        components = [dict(item) for item in self.contract["components"]]
        if scenario.get("omit_component"):
            components = [item for item in components if item["component_id"] != scenario["omit_component"]]
        snapshot_components = []
        geometry = {row["component_id"]: row for row in self.contract["source_geometry"][0]}
        for component in components:
            if component["component_id"] == scenario.get("property_component"):
                component["text"] = scenario["property_text"]
            row = geometry[component["component_id"]]
            left = row["x"] + (scenario.get("shift_x", 0) if component["component_id"] == scenario.get("shift_component") else 0)
            snapshot_components.append({
                **component,
                "bounds": {"left": left, "top": row["y"], "right": left + row["width"], "bottom": row["y"] + row["height"]},
            })
        write_json(actual / "ui-test-snapshot.json", {
            "probe_id": "PAGE-CALCULATOR::STATE-RESULT", "density": 1.0,
            "viewport": {"width": 64, "height": 64}, "components": snapshot_components,
            "status": scenario.get("external_status", "PASS"),
        })
        write_json(actual / "ui-test-snapshot-operation-trace.json", [{
            "subject_type": "TRANSITION", "subject_id": "TRANS-RESULT", "action": "CLICK_EQUALS",
            "observable_result": "8", "source_page_id": "PAGE-CALCULATOR",
            "source_state_id": "STATE-INPUT", "target_page_id": scenario.get("target_page_id", "PAGE-CALCULATOR"),
            "target_state_id": "STATE-RESULT", "back_behavior": "RETURN_INPUT", "carrier_type": scenario["carrier"],
        }])
        write_json(actual / "assertions.json", {"assertions": [
            {"assertion_id": "ASSERT-RESULT", "kind": "ANDROID_EXPECTED_OBSERVABLE", "expected": "8", "actual": scenario["result"], "status": scenario.get("external_status", "PASS")},
            {"assertion_id": "ASSERT-HISTORY", "kind": "SIDE_EFFECT", "subject_ids": ["SIDE-HISTORY"], "expected": {"payload_sha256": "a" * 64}, "actual": {"payload_sha256": scenario.get("side_effect_sha256", "a" * 64)}, "status": scenario.get("external_status", "PASS")},
        ]})
        Image.new("RGB", (64, 64), tuple(scenario["screenshot_rgb"])).save(actual / "ui-test-snapshot.png")
        write_json(actual / "ui-test-snapshot-metadata.json", {
            "schema_version": "ui-test-snapshot-evidence-v1", "probe_id": "PAGE-CALCULATOR::STATE-RESULT",
            "page_id": "PAGE-CALCULATOR", "state_id": "STATE-RESULT", "carrier": scenario["carrier"],
            "result_path": "ui-test-snapshot.json", "result_sha256": sha256(actual / "ui-test-snapshot.json"),
            "operation_trace_path": "ui-test-snapshot-operation-trace.json", "operation_trace_sha256": sha256(actual / "ui-test-snapshot-operation-trace.json"),
            "screenshot_path": "ui-test-snapshot.png", "screenshot_sha256": sha256(actual / "ui-test-snapshot.png"),
        })
        return actual

    def compare(self, name: str):
        actual = self.materialize(name)
        return compare_page_state(self.contract, actual, self.root / name / "comparisons" / "UNIT-CALC" / "ATT-001")

    @staticmethod
    def result_for(results, category: str):
        return next(result for result in results if result.category == category)

    def test_matching_page_passes_every_required_category_and_is_sealed(self) -> None:
        results = self.compare("match")
        self.assertTrue(all(result.passed for result in results))
        output = self.root / "match" / "comparisons" / "UNIT-CALC" / "ATT-001"
        self.assertTrue((output / "manifest.sha256").is_file())
        self.assertEqual(sha256(output / "manifest.sha256"), (output / "COMMITTED").read_text(encoding="utf-8").strip())
        result_files = sorted((output / "results").glob("*.json"))
        self.assertEqual(7, len(result_files))
        manifest = (output / "manifest.sha256").read_text(encoding="utf-8")
        self.assertTrue(all(f"results/{path.name}" in manifest for path in result_files))
        with self.assertRaisesRegex(ValueError, "exists|overwrite"):
            compare_page_state(self.contract, self.root / "match" / "actual", output)

    def test_rejects_page_replaced_by_dialog(self) -> None:
        self.assertFalse(self.result_for(self.compare("dialog"), "carrier").passed)

    def test_rejects_missing_buttons_and_shifted_geometry(self) -> None:
        results = self.compare("missing_shifted")
        self.assertFalse(self.result_for(results, "component-tree").passed)
        self.assertFalse(self.result_for(results, "geometry").passed)

    def test_rejects_component_property_mismatch(self) -> None:
        self.assertFalse(self.result_for(self.compare("wrong_property"), "component-tree").passed)

    def test_rejects_wrong_result_even_when_external_status_says_match(self) -> None:
        self.assertFalse(self.result_for(self.compare("wrong_result"), "behavior").passed)

    def test_rejects_screenshot_side_effect_and_navigation_mismatches(self) -> None:
        self.assertFalse(self.result_for(self.compare("wrong_screenshot"), "screenshot").passed)
        self.assertFalse(self.result_for(self.compare("wrong_side_effect"), "side-effect").passed)
        self.assertFalse(self.result_for(self.compare("wrong_navigation"), "navigation").passed)


if __name__ == "__main__":
    unittest.main()
