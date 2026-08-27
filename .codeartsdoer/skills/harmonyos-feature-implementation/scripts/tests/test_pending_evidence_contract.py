from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import page_acceptance_contract as pac  # noqa: E402

FULL = pac.RECORD_REQUIREMENTS["android_evidence_hashes"]
PENDING_KEYS = set(pac.PENDING_EVIDENCE_RECORD_KEYS)


def full_record() -> dict:
    return {
        "evidence_id": "EVD-A",
        "relative_path": "evidence/ENV-001/P-X/S-DEFAULT/EVD-A",
        "screenshot_sha256": "a" * 64,
        "layout_sha256": "b" * 64,
        "metadata_sha256": "c" * 64,
        "source_geometry": [{"component_id": "C1", "bounds": {"left": 0, "top": 0}}],
    }


class PendingEvidenceShapeTest(unittest.TestCase):
    def _run(self, records):
        pac._validate_record_array("PAGE-X", "android_evidence_hashes", records, FULL)

    def test_full_record_passes(self) -> None:
        self._run([full_record()])

    def test_pending_three_key_shape_passes(self) -> None:
        pending = {"evidence_id": "EVD-P", "pending_runtime_verify": True, "source_geometry": []}
        self._run([full_record(), pending])

    def test_pending_two_key_shape_rejected(self) -> None:
        legacy = {"evidence_id": "EVD-P", "pending_runtime_verify": True}
        with self.assertRaisesRegex(ValueError, "fields differ"):
            self._run([legacy])

    def test_non_bool_flag_falls_through_and_rejected(self) -> None:
        malformed = {"evidence_id": "EVD-P", "pending_runtime_verify": True, "extra": 1}
        with self.assertRaisesRegex(ValueError, "fields differ"):
            self._run([malformed])

    def test_android_evidence_field_same_disposition(self) -> None:
        pending = {"evidence_id": "EVD-P", "pending_runtime_verify": True, "source_geometry": []}
        pac._validate_record_array(
            "PAGE-X", "android_evidence", [pending], FULL)

    def test_missing_sha256_in_full_shape_rejected(self) -> None:
        broken = full_record()
        broken.pop("layout_sha256")
        with self.assertRaisesRegex(ValueError, "must be a non-empty string|fields differ"):
            self._run([broken])


if __name__ == "__main__":
    unittest.main()