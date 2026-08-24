#!/usr/bin/env python3
"""Tests for conservation-checked ArkTS page plans and UiTest probes."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))

from arkts_page_plan import compile_arkts_page_plan, validate_arkts_page_plan  # noqa: E402
from prepare_uitest_probe import prepare_uitest_probe  # noqa: E402


FORBIDDEN = ("Inspector", "getFilteredInspectorTree", "getFilteredInspectorTreeById")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class UiTestPageProbeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="uitest-probe-")
        self.workspace = Path(self.temp.name) / "phase-04-harmony-implementation"
        (self.workspace / "harmony-project" / "entry" / "src" / "main" / "ets" / "pages").mkdir(parents=True)
        self.contracts = [
            page_contract("PAGE-CALCULATOR", ("STATE-EMPTY", "STATE-RESULT")),
            page_contract("PAGE-HISTORY", ("STATE-HISTORY",)),
        ]
        self._publish()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _publish(self) -> None:
        rows = []
        for contract in self.contracts:
            page_id = str(contract["page_id"])
            path = self.workspace / "page-contracts" / f"{page_id}.json"
            write_json(path, contract)
            rows.append({
                "page_id": page_id,
                "page_name": str(contract["page_name"]),
                "relative_path": f"page-contracts/{page_id}.json",
                "contract_sha256": sha256(path),
                "state_count": str(len(contract["states"])),
                "feature_ids": "FEATURE-CALC",
                "required_h4env_ids": "H4ENV-001",
                "status": "LOCKED",
            })
        with (self.workspace / "page-contract-registry.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def test_page_plan_conserves_all_frozen_sections_and_maps_components(self) -> None:
        contract = self.contracts[0]
        plan = compile_arkts_page_plan(contract, "a" * 64)
        validate_arkts_page_plan(plan, contract, "a" * 64)
        self.assertEqual("PAGE", plan["carrier"]["arkts_carrier"])
        self.assertEqual("Column", plan["components"][0]["arkts_type"])
        self.assertEqual("COMP-PAGE-CALCULATOR-ROOT", plan["components"][0]["arkts_test_tag"])
        self.assertEqual(contract["source_geometry"], plan["source_geometry"])
        self.assertEqual(contract["visible_text"], plan["visible_text"])
        self.assertEqual(contract["assets"], plan["assets"])
        self.assertEqual(contract["states"], plan["states"])
        self.assertEqual(contract["interaction_bindings"], plan["events_actions"])
        self.assertEqual(contract["transitions"], plan["transitions"])
        self.assertEqual(contract["side_effects"], plan["side_effects"])
        self.assertEqual(contract["system_capabilities"], plan["capability_dependencies"])
        self.assertEqual(contract["code_map"], plan["source_refs"]["code_map"])

    def test_plan_rejects_unknown_type_deleted_fact_and_carrier_change(self) -> None:
        contract = page_contract("PAGE-CALCULATOR", ("STATE-EMPTY",))
        contract["components"][0]["type"] = "MagicUnknownWidget"
        with self.assertRaisesRegex(ValueError, "unmapped Android component type"):
            compile_arkts_page_plan(contract, "a" * 64)
        contract = page_contract("PAGE-CALCULATOR", ("STATE-EMPTY",))
        plan = compile_arkts_page_plan(contract, "a" * 64)
        del plan["components"][0]
        with self.assertRaisesRegex(ValueError, "components.*conservation"):
            validate_arkts_page_plan(plan, contract, "a" * 64)
        plan = compile_arkts_page_plan(contract, "a" * 64)
        plan["carrier"]["arkts_carrier"] = "DIALOG"
        with self.assertRaisesRegex(ValueError, "carrier"):
            validate_arkts_page_plan(plan, contract, "a" * 64)

    def test_plan_rejects_non_unique_locator_and_missing_state_target(self) -> None:
        contract = page_contract("PAGE-CALCULATOR", ("STATE-EMPTY",))
        contract["components"].append(dict(contract["components"][0]))
        with self.assertRaisesRegex(ValueError, "non-unique locator"):
            compile_arkts_page_plan(contract, "a" * 64)
        contract = page_contract("PAGE-CALCULATOR", ("STATE-EMPTY", "STATE-RESULT"))
        contract["phase3_targets"] = contract["phase3_targets"][:1]
        with self.assertRaisesRegex(ValueError, "STATE-RESULT.*target"):
            compile_arkts_page_plan(contract, "a" * 64)

    def test_generates_one_uitest_probe_per_page_state_only_under_ohos_test(self) -> None:
        result = prepare_uitest_probe(self.workspace)
        test_root = self.workspace / "harmony-project" / "entry" / "src" / "ohosTest" / "ets" / "test"
        self.assertEqual(["PAGE-CALCULATOR", "PAGE-HISTORY"], result["page_ids"])
        self.assertEqual(3, result["probe_count"])
        self.assertTrue((test_root / "UiTestSnapshot.test.ets").is_file())
        self.assertTrue((test_root / "UiTestPageProbeRegistry.ets").is_file())
        self.assertTrue((test_root / "UiTestRunBinding.ets").is_file())
        self.assertFalse(any((self.workspace / "harmony-project" / "entry" / "src" / "main").rglob("*UiTest*")))
        generated = "\n".join(path.read_text(encoding="utf-8") for path in test_root.glob("*.ets"))
        self.assertIn("@ohos.UiTest", generated)
        self.assertIn("getBounds", generated)
        self.assertIn("isVisible", generated)
        self.assertIn("isEnabled", generated)
        self.assertIn("isClickable", generated)
        self.assertIn("ui-test-snapshot.json", generated)
        self.assertIn("ui-test-snapshot.png", generated)
        for forbidden in FORBIDDEN:
            self.assertNotIn(forbidden, generated)

    def test_probe_fails_closed_for_missing_or_non_unique_required_component(self) -> None:
        prepare_uitest_probe(self.workspace)
        capture = (
            self.workspace / "harmony-project" / "entry" / "src" / "ohosTest" / "ets" / "test" / "UiTestSnapshot.test.ets"
        ).read_text(encoding="utf-8")
        self.assertIn("Required component missing", capture)
        self.assertIn("Required component locator is not unique", capture)
        self.assertIn("UiTest run binding is not frozen", capture)

    def test_generation_manifest_binds_plans_probes_and_runtime_hash_fields(self) -> None:
        result = prepare_uitest_probe(self.workspace)
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual("ui-test-snapshot-generation-v1", manifest["schema_version"])
        self.assertEqual(
            ["PAGE-CALCULATOR::STATE-EMPTY", "PAGE-CALCULATOR::STATE-RESULT", "PAGE-HISTORY::STATE-HISTORY"],
            [probe["probe_id"] for probe in manifest["probes"]],
        )
        self.assertEqual(
            ["test_hap_sha256", "final_hap_sha256", "device_identity_sha256", "command_sha256"],
            manifest["required_runtime_hash_fields"],
        )
        for file_record in manifest["generated_files"]:
            path = self.workspace / "harmony-project" / Path(file_record["relative_path"])
            self.assertEqual(file_record["sha256"], sha256(path))
        for plan_record in manifest["page_plans"]:
            path = self.workspace / Path(plan_record["relative_path"])
            self.assertEqual(plan_record["sha256"], sha256(path))

    def test_phase4_initializer_generates_and_hash_locks_uitest_snapshot_inputs(self) -> None:
        source = (SCRIPTS / "init_implementation.py").read_text(encoding="utf-8")
        self.assertIn("from prepare_uitest_probe import prepare_uitest_probe", source)
        self.assertIn("prepare_uitest_probe(temp_dir)", source)
        self.assertIn('"ui_test_snapshot_generation"', source)
        self.assertNotIn("arkui_inspector_bridge", source)
        self.assertNotIn("ArkUIInspectorBridge", source)

    def test_generation_rejects_missing_page_state_or_required_component(self) -> None:
        (self.workspace / "page-contracts" / "PAGE-HISTORY.json").unlink()
        with self.assertRaisesRegex(ValueError, "PAGE-HISTORY.*missing"):
            prepare_uitest_probe(self.workspace)
        self._publish()
        self.contracts[0]["states"] = []
        self._publish()
        with self.assertRaisesRegex(ValueError, "PAGE-CALCULATOR.*state"):
            prepare_uitest_probe(self.workspace)
        self.contracts[0] = page_contract("PAGE-CALCULATOR", ("STATE-EMPTY",))
        self.contracts[0]["components"] = []
        self._publish()
        with self.assertRaisesRegex(ValueError, "PAGE-CALCULATOR.*component"):
            prepare_uitest_probe(self.workspace)


def page_contract(page_id: str, state_ids: tuple[str, ...]) -> dict[str, object]:
    page_name = page_id.removeprefix("PAGE-").title()
    root_id = f"COMP-{page_id}-ROOT"
    button_id = f"COMP-{page_id}-ACTION"
    return {
        "schema_version": "page-acceptance-contract-v1",
        "page_id": page_id,
        "page_name": page_name,
        "carrier_type": "PAGE",
        "feature_ids": ["FEATURE-CALC"],
        "states": [
            {"state_id": state_id, "state_name": state_id, "records": [{
                "entry_condition": "Launch frozen entry",
                "action_summary": f"Reach {state_id}",
                "expected_observable": state_id,
            }]}
            for state_id in state_ids
        ],
        "components": [
            {"component_id": root_id, "page_id": page_id, "type": "LinearLayout", "text": ""},
            {"component_id": button_id, "page_id": page_id, "type": "Button", "text": "Open"},
        ],
        "source_geometry": [
            {"component_id": root_id, "x": 0, "y": 0, "width": 360, "height": 720},
            {"component_id": button_id, "x": 10, "y": 20, "width": 100, "height": 48},
        ],
        "assets": [{"asset_id": "ASSET-ICON", "page_ids": page_id}],
        "visible_text": ["Open"],
        "interaction_bindings": [{"event_id": "EVENT-OPEN", "page_id": page_id, "component_id": button_id, "action": "CLICK"}],
        "entry_conditions": [{"state_id": state_id, "entry_condition": "Launch frozen entry", "action_summary": f"Reach {state_id}"} for state_id in state_ids],
        "transitions": [{"transition_id": "TRANS-STAY", "source_page_id": page_id, "target_page_id": page_id}],
        "code_map": [{"code_ref": "Calculator.kt:1", "page_id": page_id}],
        "business_rules": [{"business_rule_id": "BR-CALC"}],
        "data_dependencies": [{"data_dependency_id": "DATA-CALC"}],
        "side_effects": [{"side_effect_id": "SIDE-CLIPBOARD", "page_id": page_id}],
        "system_capabilities": [{"system_capability_id": "CAP-CLIPBOARD"}],
        "android_evidence_hashes": [{"evidence_id": "EVID-1", "screenshot_sha256": "b" * 64}],
        "phase3_targets": [{
            "state_id": state_id, "env_id": "ENV-001", "harmony_module_id": "MODULE-CALC",
            "target_kind": "ROUTE_PAGE", "target_id": f"pages/{page_name}",
        } for state_id in state_ids],
        "required_h4env_ids": ["H4ENV-001"],
        "comparison_policy": {"geometry_tolerance": "max(2dp, 0.5%)"},
    }


if __name__ == "__main__":
    unittest.main()
