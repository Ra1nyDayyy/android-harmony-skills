from __future__ import annotations

import sys
import unittest


from pathlib import Path  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import init_implementation as init_impl  # noqa: E402


def decision_rows():
    return [
        {
            "decision_id": "DEC-0999",
            "decision_type": "CARRIER_DEVIATION_APPROVAL",
            "decision": "User ruling 1B: DIALOG->PAGE named deviation approved for PAGE-FILEINFODIALOG-0606A720 et al.",
            "rationale": "cold-start-testable parity with UiTest content assertions",
        }
    ]


class CarrierDeviationTest(unittest.TestCase):
    def _decl(self, **overrides):
        entry = {
            "page_id": "PAGE-FILEINFODIALOG-0606A720",
            "expected_carrier": "DIALOG",
            "provided_carrier": "PAGE",
            "authorized_decision_id": "DEC-0999",
            "rationale": "route-page landing keeps cold-start navigation testable",
        }
        entry.update(overrides)
        return [entry]

    def test_normalize_accepts_wellformed(self):
        out = init_impl.normalize_carrier_deviations(self._decl())
        self.assertEqual(out["PAGE-FILEINFODIALOG-0606A720"]["provided_carrier"], "PAGE")

    def test_normalize_rejects_noop_and_nonpage(self):
        with self.assertRaisesRegex(ValueError, "no effective change"):
            init_impl.normalize_carrier_deviations(
                self._decl(provided_carrier="DIALOG"))
        with self.assertRaisesRegex(ValueError, "outside sanctioned policy"):
            init_impl.normalize_carrier_deviations(
                self._decl(expected_carrier="PAGE", provided_carrier="DIALOG"))

    def test_authorization_checks_decision_log(self):
        dev = init_impl.normalize_carrier_deviations(self._decl())["PAGE-FILEINFODIALOG-0606A720"]
        self.assertTrue(init_impl._decision_authorizes_carrier_deviation(
            decision_rows(), "PAGE-FILEINFODIALOG-0606A720", dev))
        self.assertFalse(init_impl._decision_authorizes_carrier_deviation(
            [], "PAGE-FILEINFODIALOG-0606A720", dev))

    def test_wrong_page_rejected_by_log_matcher(self):
        dev = init_impl.normalize_carrier_deviations(self._decl())["PAGE-FILEINFODIALOG-0606A720"]
        self.assertFalse(init_impl._decision_authorizes_carrier_deviation(
            decision_rows(), "PAGE-SOMEONEELSE", dev))


if __name__ == "__main__":
    unittest.main()