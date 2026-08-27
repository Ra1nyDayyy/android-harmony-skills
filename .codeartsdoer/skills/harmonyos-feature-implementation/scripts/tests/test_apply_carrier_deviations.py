from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import page_acceptance_contract as pac  # noqa: E402


def make_contract(page_id="PAGE-X", carrier="DIALOG"):
    return {"page_id": page_id, "carrier_type": carrier}


class ApplyCarrierDeviationsTest(unittest.TestCase):
    def test_stamps_matching_page_and_skips_others(self):
        contracts = [make_contract("PAGE-X"), make_contract("PAGE-Y", "PAGE")]
        deviations = {"PAGE-X": {
            "expected_carrier": "DIALOG", "provided_carrier": "PAGE",
            "authorized_decision_id": "DEC-015", "rationale": "route shell carries dialog content"}}
        applied = pac.apply_carrier_deviations(contracts, deviations)
        self.assertEqual(applied, 1)
        self.assertEqual(contracts[0]["carrier_deviation"]["page_id"], "PAGE-X")
        self.assertNotIn("carrier_deviation", contracts[1])

    def test_empty_field_is_rejected(self):
        contract = make_contract()
        with self.assertRaisesRegex(ValueError, "lacks rationale"):
            pac.apply_carrier_deviations([contract], {"PAGE-X": {
                "expected_carrier": "DIALOG", "provided_carrier": "PAGE",
                "authorized_decision_id": "DEC-015", "rationale": ""}})

    def test_validate_accepts_stamped_block(self):
        contract = make_contract()
        pac.apply_carrier_deviations([contract], {"PAGE-X": {
            "expected_carrier": "DIALOG", "provided_carrier": "PAGE",
            "authorized_decision_id": "DEC-015", "rationale": "route shell carries dialog content"}})
        # 可选键语义：不在必填键集，也不属于数组字段；仅作为允许的 extra 存在。
        from page_acceptance_contract import CONTRACT_KEYS, OPTIONAL_CONTRACT_KEYS
        self.assertEqual(OPTIONAL_CONTRACT_KEYS, {"carrier_deviation"})
        self.assertNotIn("carrier_deviation", CONTRACT_KEYS)
        stamped = contract["carrier_deviation"]
        self.assertEqual(set(stamped), {"page_id", "expected_carrier", "provided_carrier", "authorized_decision_id", "rationale"})


if __name__ == "__main__":
    unittest.main()
