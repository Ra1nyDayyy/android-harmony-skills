from __future__ import annotations

import hashlib
import json
import math
import os
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
from compare_screenshot import _metrics  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seal_directory(path: Path) -> None:
    for name in ("manifest.sha256", "COMMITTED"):
        target = path / name
        if target.exists():
            target.chmod(0o644)
            target.unlink()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    manifest = "".join(f"{sha256(item)}  {item.relative_to(path).as_posix()}\n" for item in files)
    (path / "manifest.sha256").write_text(manifest, encoding="utf-8")
    (path / "COMMITTED").write_text(f"HEVD-CALC SEALED manifest_sha256={sha256(path / 'manifest.sha256')}\n", encoding="utf-8")


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
        actual_density = scenario.get("actual_density", 1.0)
        actual_size = scenario.get("actual_size", 64)
        geometry = {row["component_id"]: row for row in self.contract["source_geometry"][0]["components"]}
        for component in components:
            if component["component_id"] == scenario.get("property_component"):
                component["text"] = scenario["property_text"]
            if component["component_id"] == scenario.get("structure_component"):
                component["parent_component_id"] = scenario["parent_component_id"]
                component["order"] = scenario["order"]
            row = geometry[component["component_id"]]
            left = (row["x"] + (scenario.get("shift_x", 0) if component["component_id"] == scenario.get("shift_component") else 0)) * actual_density
            snapshot_components.append({
                **component,
                "bounds": {"left": left, "top": row["y"] * actual_density, "right": left + row["width"] * actual_density, "bottom": (row["y"] + row["height"]) * actual_density},
            })
        write_json(actual / "ui-test-snapshot.json", {
            "probe_id": "PAGE-CALCULATOR::STATE-RESULT", "density": actual_density,
            "viewport": {"width": actual_size, "height": actual_size},
            "application_region": {"x": 0, "y": 0, "width": actual_size, "height": actual_size},
            "components": snapshot_components,
            "status": scenario.get("external_status", "PASS"),
        })
        trace = [{
            "subject_type": "TRANSITION", "subject_id": "TRANS-RESULT", "action": "CLICK_EQUALS",
            "observable_result": "8", "source_page_id": "PAGE-CALCULATOR",
            "source_state_id": "STATE-INPUT", "target_page_id": scenario.get("target_page_id", "PAGE-CALCULATOR"),
            "target_state_id": "STATE-RESULT", "back_behavior": "RETURN_INPUT", "carrier_type": scenario["carrier"],
        }]
        trace[0]["action"] = scenario.get("action", trace[0]["action"])
        if scenario.get("extra_navigation"):
            trace.append({**trace[0], "subject_id": "TRANS-EXTRA"})
        if scenario.get("duplicate_navigation"):
            trace.append(dict(trace[0]))
        write_json(actual / "ui-test-snapshot-operation-trace.json", trace)
        assertion_rows = [
            {"assertion_id": "ASSERT-RESULT", "kind": "ANDROID_EXPECTED_OBSERVABLE", "expected": "8", "actual": scenario["result"], "status": scenario.get("external_status", "PASS")},
            {"assertion_id": "ASSERT-HISTORY", "kind": "SIDE_EFFECT", "subject_ids": ["SIDE-HISTORY"], "expected": {"payload_sha256": "a" * 64}, "actual": {"payload_sha256": scenario.get("side_effect_sha256", "a" * 64)}, "status": scenario.get("external_status", "PASS")},
        ]
        if scenario.get("extra_side_effect"):
            assertion_rows.append({"assertion_id": "ASSERT-EXTRA", "kind": "SIDE_EFFECT", "subject_ids": ["SIDE-EXTRA"], "actual": {"payload_sha256": "c" * 64}, "status": "PASS"})
        write_json(actual / "assertions.json", {"assertions": assertion_rows})
        image = Image.new("RGB", (actual_size, actual_size), tuple(scenario["screenshot_rgb"]))
        for x, y in scenario.get("changed_pixels", []):
            image.putpixel((x, y), (0, 0, 0))
        image.save(actual / "ui-test-snapshot.png")
        write_json(actual / "ui-test-snapshot-metadata.json", {
            "schema_version": "ui-test-snapshot-evidence-v1", "probe_id": "PAGE-CALCULATOR::STATE-RESULT",
            "page_id": "PAGE-CALCULATOR", "state_id": "STATE-RESULT", "carrier": scenario["carrier"],
            "result_path": "ui-test-snapshot.json", "result_sha256": sha256(actual / "ui-test-snapshot.json"),
            "operation_trace_path": "ui-test-snapshot-operation-trace.json", "operation_trace_sha256": sha256(actual / "ui-test-snapshot-operation-trace.json"),
            "screenshot_path": "ui-test-snapshot.png", "screenshot_sha256": sha256(actual / "ui-test-snapshot.png"),
        })
        seal_directory(actual)

        contract_dir = self.root / name / "page-contracts"
        contract_dir.mkdir()
        contract_path = contract_dir / "PAGE-CALCULATOR.json"
        write_json(contract_path, self.contract)
        canonical_hash = hashlib.sha256(json.dumps(self.contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        registry = self.root / name / "page-contract-registry.csv"
        registry.write_text(
            "page_id,relative_path,contract_sha256,status\n"
            f"PAGE-CALCULATOR,page-contracts/PAGE-CALCULATOR.json,{canonical_hash},FROZEN\n",
            encoding="utf-8",
        )
        write_json(self.root / name / "stage-04-input-lock.json", {
            "page_contract_registry": {"relative_path": "page-contract-registry.csv", "sha256": sha256(registry)},
            "page_contracts": [{"page_id": "PAGE-CALCULATOR", "relative_path": "page-contracts/PAGE-CALCULATOR.json", "sha256": canonical_hash}],
        })
        return actual

    def compare(self, name: str):
        actual = self.materialize(name)
        return compare_page_state(
            self.contract, actual, self.root / name / "comparisons" / "UNIT-CALC" / "ATT-001",
            state_id="STATE-RESULT", source_env_id="ANDROID-ENV-001",
            contract_path=self.root / name / "page-contracts" / "PAGE-CALCULATOR.json",
            registry_path=self.root / name / "page-contract-registry.csv",
            input_lock_path=self.root / name / "stage-04-input-lock.json",
        )

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
            compare_page_state(
                self.contract, self.root / "match" / "actual", output,
                state_id="STATE-RESULT", source_env_id="ANDROID-ENV-001",
                contract_path=self.root / "match/page-contracts/PAGE-CALCULATOR.json",
                registry_path=self.root / "match/page-contract-registry.csv",
                input_lock_path=self.root / "match/stage-04-input-lock.json",
            )

    def test_rejects_page_replaced_by_dialog(self) -> None:
        self.assertFalse(self.result_for(self.compare("dialog"), "carrier").passed)

    def test_rejects_missing_buttons_and_shifted_geometry(self) -> None:
        results = self.compare("missing_shifted")
        self.assertFalse(self.result_for(results, "component-tree").passed)
        self.assertFalse(self.result_for(results, "geometry").passed)

    def test_rejects_component_property_mismatch(self) -> None:
        self.assertFalse(self.result_for(self.compare("wrong_property"), "component-tree").passed)

    def test_rejects_parent_and_order_mismatch(self) -> None:
        self.assertFalse(self.result_for(self.compare("wrong_parent_order"), "component-tree").passed)

    def test_rejects_wrong_result_even_when_external_status_says_match(self) -> None:
        self.assertFalse(self.result_for(self.compare("wrong_result"), "behavior").passed)

    def test_rejects_screenshot_side_effect_and_navigation_mismatches(self) -> None:
        self.assertFalse(self.result_for(self.compare("wrong_screenshot"), "screenshot").passed)
        self.assertFalse(self.result_for(self.compare("wrong_side_effect"), "side-effect").passed)
        self.assertFalse(self.result_for(self.compare("wrong_navigation"), "navigation").passed)

    def test_input_manifest_binds_assertions_and_committed_marker(self) -> None:
        actual = self.materialize("match")
        assertions = json.loads((actual / "assertions.json").read_text(encoding="utf-8"))
        assertions["assertions"][0]["actual"] = "0"
        write_json(actual / "assertions.json", assertions)
        with self.assertRaisesRegex(ValueError, "manifest|seal|COMMITTED"):
            compare_page_state(
                self.contract, actual, self.root / "tampered-output",
                state_id="STATE-RESULT", source_env_id="ANDROID-ENV-001",
                contract_path=self.root / "match/page-contracts/PAGE-CALCULATOR.json",
                registry_path=self.root / "match/page-contract-registry.csv",
                input_lock_path=self.root / "match/stage-04-input-lock.json",
            )

    def test_contract_must_match_registry_and_input_lock(self) -> None:
        actual = self.materialize("match")
        self.contract["carrier_type"] = "DIALOG"
        with self.assertRaisesRegex(ValueError, "contract|registry|input lock"):
            compare_page_state(
                self.contract, actual, self.root / "unlocked-output",
                state_id="STATE-RESULT", source_env_id="ANDROID-ENV-001",
                contract_path=self.root / "match/page-contracts/PAGE-CALCULATOR.json",
                registry_path=self.root / "match/page-contract-registry.csv",
                input_lock_path=self.root / "match/stage-04-input-lock.json",
            )

    def test_required_mask_uses_nested_real_geometry_and_stricter_threshold(self) -> None:
        self.assertFalse(self.result_for(self.compare("mask_mismatch"), "screenshot").passed)

    def test_application_region_and_density_are_normalized_before_comparison(self) -> None:
        results = self.compare("density_two")
        self.assertTrue(self.result_for(results, "geometry").passed)
        self.assertTrue(self.result_for(results, "screenshot").passed)

    def test_state_and_source_environment_select_exact_android_evidence(self) -> None:
        actual = self.materialize("match")
        with self.assertRaisesRegex(ValueError, "state|environment|evidence"):
            compare_page_state(
                self.contract, actual, self.root / "wrong-env-output",
                state_id="STATE-RESULT", source_env_id="ANDROID-ENV-OTHER",
                contract_path=self.root / "match/page-contracts/PAGE-CALCULATOR.json",
                registry_path=self.root / "match/page-contract-registry.csv",
                input_lock_path=self.root / "match/stage-04-input-lock.json",
            )

    def test_navigation_rejects_wrong_action_extra_and_duplicate_transitions(self) -> None:
        for scenario in ("wrong_action", "extra_navigation", "duplicate_navigation"):
            with self.subTest(scenario=scenario):
                self.assertFalse(self.result_for(self.compare(scenario), "navigation").passed)

    def test_side_effect_rejects_uncontracted_actual_effect(self) -> None:
        self.assertFalse(self.result_for(self.compare("extra_side_effect"), "side-effect").passed)

    def test_missing_upstream_side_effect_predicate_fails_closed(self) -> None:
        del self.contract["side_effects"][0]["operator"]
        del self.contract["side_effects"][0]["expected_payload_sha256"]
        self.assertFalse(self.result_for(self.compare("match"), "side-effect").passed)

    def test_geometry_rejects_nan_and_infinite_values(self) -> None:
        for value in (math.nan, math.inf):
            with self.subTest(value=value):
                name = "match"
                actual = self.materialize(name)
                snapshot_path = actual / "ui-test-snapshot.json"
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
                snapshot["components"][0]["bounds"]["left"] = value
                write_json(snapshot_path, snapshot)
                metadata_path = actual / "ui-test-snapshot-metadata.json"
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                metadata["result_sha256"] = sha256(snapshot_path)
                write_json(metadata_path, metadata)
                seal_directory(actual)
                with self.assertRaisesRegex(ValueError, "geometry|finite|bounds"):
                    compare_page_state(
                        self.contract, actual, self.root / f"bad-geometry-{str(value)}",
                        state_id="STATE-RESULT", source_env_id="ANDROID-ENV-001",
                        contract_path=self.root / "match/page-contracts/PAGE-CALCULATOR.json",
                        registry_path=self.root / "match/page-contract-registry.csv",
                        input_lock_path=self.root / "match/stage-04-input-lock.json",
                    )
                self.root.joinpath(name).rename(self.root / f"used-{str(value)}")

    def test_ssim_includes_tail_pixels_outside_full_eight_pixel_windows(self) -> None:
        expected = Image.new("RGB", (9, 9), (240, 240, 240))
        actual = expected.copy()
        actual.putpixel((8, 8), (0, 0, 0))
        ssim, changed = _metrics(expected, actual)
        self.assertLess(ssim, 1.0)
        self.assertGreater(changed, 0.0)

    def test_output_verifier_rejects_recursive_tamper(self) -> None:
        self.compare("match")
        output = self.root / "match/comparisons/UNIT-CALC/ATT-001"
        from compare_migration_unit import verify_comparison_output
        verify_comparison_output(output)
        result = next((output / "results").glob("*.json"))
        result.chmod(0o644)
        result.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest|hash|sealed"):
            verify_comparison_output(output)


if __name__ == "__main__":
    unittest.main()
