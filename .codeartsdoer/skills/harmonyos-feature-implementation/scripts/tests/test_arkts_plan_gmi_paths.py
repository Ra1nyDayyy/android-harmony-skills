from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import arkts_page_plan as app  # noqa: E402


def base_contract(**overrides):
    contract = {
        "page_id": "PAGE-X",
        "carrier_type": "PAGE",
        "states": [{"state_id": "S1", "records": [{}]}],
        "components": [{"component_id": "C-1", "page_id": "PAGE-X", "type": "Text"}],
        "phase3_targets": [{"state_id": "S1", "env_id": "ENV-001", "harmony_module_id": "ENTRY", "target_kind": "ROUTE_PAGE", "target_id": "ROUTE-X"}],
        "android_evidence_hashes": [{
            "evidence_id": "EVD-A", "relative_path": "evidence/x",
            "screenshot_sha256": "a" * 64, "layout_sha256": "b" * 64,
            "metadata_sha256": "c" * 64, "source_geometry": [{"n": 1}],
        }],
    }
    contract.update(overrides)
    return contract


def plan_or_error(contract):
    try:
        return app.compile_arkts_page_plan(contract, "d" * 64)
    except ValueError as exc:
        return exc


class ArktsPlanGmiPathsTest(unittest.TestCase):
    def test_a_toggle_double_mapping(self):
        self.assertEqual(app.COMPONENT_TYPES.get("toggle"), "Toggle")

    def test_b_deviation_allows_dialog_on_route(self):
        contract = base_contract(
            carrier_type="DIALOG",
            components=[{"component_id": "C-1", "page_id": "PAGE-X", "type": "Text"}],
            carrier_deviation={
                "page_id": "PAGE-X", "expected_carrier": "DIALOG",
                "provided_carrier": "PAGE", "authorized_decision_id": "DEC-015",
                "rationale": "route shell carries dialog content with uitest assertions",
                "inventory_id": "INV-X",
            },
        )
        result = plan_or_error(contract)
        self.assertNotIsInstance(result, ValueError)

    def test_b_without_deviation_still_rejected(self):
        contract = base_contract(carrier_type="DIALOG")
        result = plan_or_error(contract)
        self.assertIsInstance(result, ValueError)

    def test_c2_pending_only_allows_empty_components(self):
        pending = {"evidence_id": "EVD-P", "pending_runtime_verify": True, "source_geometry": []}
        contract = base_contract(components=[], android_evidence_hashes=[pending])
        result = plan_or_error(contract)
        self.assertNotIsInstance(result, ValueError)

    def test_c2_accepted_baseline_keeps_nonempty_invariant(self):
        contract = base_contract(components=[])
        result = plan_or_error(contract)
        self.assertIsInstance(result, ValueError)


if __name__ == "__main__":
    unittest.main()
