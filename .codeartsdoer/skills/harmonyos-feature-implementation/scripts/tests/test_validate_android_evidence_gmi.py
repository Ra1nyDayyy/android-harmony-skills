from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import init_implementation as init_impl  # noqa: E402


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def make_inventory_row(evidence_id: str = "EVD-ACTIVITYMAIN") -> dict[str, str]:
    return {
        "inventory_id": f"INV-{'ACTIVITYMAIN'}",
        "feature_id": "DOCUMENT",
        "page_id": "PAGE-ACTIVITYMAIN",
        "page_name": "MainActivity",
        "state_id": "STATE-DEFAULT",
        "state_name": "Default",
        "env_id": "ENV-001",
        "evidence_id": evidence_id,
        "row_status": "REVIEWED",
        "reviewed_by": "CHECKER",
    }


def make_index_row(row: dict[str, str], status: str) -> dict[str, str]:
    return {
        **{k: row[k] for k in (
            "inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id")},
        "status": status,
        "relative_path":
            f"evidence/{row['env_id']}/{row['page_id']}/{row['state_id']}/{row['evidence_id']}",
    }


class ValidateAndroidEvidenceGmiTest(unittest.TestCase):
    def _workspace(self, tmp: Path, *, gmi: bool, report_body: str = "") -> Path:
        phase2 = tmp / "phase-02-android-inventory"
        phase2.mkdir(parents=True)
        if gmi:
            gmi_dir = phase2 / "gmi"
            gmi_dir.mkdir()
            (gmi_dir / "phase-manifest.json").write_text(
                json.dumps({"phase": 2, "generator": "gmi"}), encoding="utf-8")
            if report_body:
                (phase2 / "evidence-recovery-report.md").write_text(
                    report_body, encoding="utf-8")
        return phase2

    def test_non_gmi_pending_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            phase2 = self._workspace(tmp, gmi=False)
            row = make_inventory_row()
            evidence_rows = [make_index_row(row, "PENDING_RUNTIME_VERIFY")]
            with self.assertRaisesRegex(ValueError, "non-ACCEPTED"):
                init_impl.validate_android_evidence(phase2, [row], evidence_rows)

    def test_gmi_without_recovery_report_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            phase2 = self._workspace(tmp, gmi=True)
            row = make_inventory_row()
            evidence_rows = [make_index_row(row, "PENDING_RUNTIME_VERIFY")]
            with self.assertRaisesRegex(ValueError, "non-ACCEPTED"):
                init_impl.validate_android_evidence(phase2, [row], evidence_rows)

    def test_gmi_report_listing_enables_honest_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            body = (
                "<!-- PENDING-FINAL-BEGIN\n"
                "EVD-ACTIVITYMAIN\n"
                "PENDING-FINAL-END -->\n"
            )
            phase2 = self._workspace(tmp, gmi=True, report_body=body)
            row = make_inventory_row("EVD-ACTIVITYMAIN")
            pending_row = make_index_row(row, "PENDING_RUNTIME_VERIFY")
            referenced = init_impl.validate_android_evidence(
                phase2, [row], [pending_row])
            index_row, source = referenced["EVD-ACTIVITYMAIN"]
            self.assertIsNone(source)
            self.assertEqual(index_row["status"], "PENDING_RUNTIME_VERIFY")

    def test_gmi_unlisted_pending_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            body = (
                "<!-- PENDING-FINAL-BEGIN\n"
                "EVD-SOMEOTHERPAGE\n"
                "PENDING-FINAL-END -->\n"
            )
            phase2 = self._workspace(tmp, gmi=True, report_body=body)
            row = make_inventory_row("EVD-ACTIVITYMAIN")
            evidence_rows = [make_index_row(row, "PENDING_RUNTIME_VERIFY")]
            with self.assertRaisesRegex(ValueError, "non-ACCEPTED"):
                init_impl.validate_android_evidence(phase2, [row], evidence_rows)

    def test_non_gmi_acceptance_still_requires_sealed_tree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            phase2 = self._workspace(tmp, gmi=False)
            row = make_inventory_row()
            evidence_rows = [make_index_row(row, "ACCEPTED")]
            # accepted status but no physical directory -> still rejected
            # (safe_relative_path performs the existence check first)
            with self.assertRaisesRegex(ValueError, "Missing Android evidence"):
                init_impl.validate_android_evidence(phase2, [row], evidence_rows)

    def test_lifecycle_loop_allows_listed_pending(self) -> None:
        """Active listed-PENDING row passes; unrelated unlisted one rejects."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            body = (
                "<!-- PENDING-FINAL-BEGIN\n"
                "EVD-ACTIVITYMAIN\n"
                "PENDING-FINAL-END -->\n"
            )
            phase2 = self._workspace(tmp, gmi=True, report_body=body)
            active = make_inventory_row("EVD-ACTIVITYMAIN")
            listed_pending = make_index_row(active, "PENDING_RUNTIME_VERIFY")
            superseded = dict(listed_pending)
            superseded["evidence_id"] = "EVD-SUPERSEDEDX"
            superseded["status"] = "SUPERSEDED"
            referenced = init_impl.validate_android_evidence(
                phase2, [active], [listed_pending, superseded])
            self.assertIsNone(referenced["EVD-ACTIVITYMAIN"][1])


if __name__ == "__main__":
    unittest.main()