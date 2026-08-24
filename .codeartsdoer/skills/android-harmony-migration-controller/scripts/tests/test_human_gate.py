from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))

from _human_gate import read_current_human_review  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class HumanGateTest(unittest.TestCase):
    def make_run(self, root: Path, verdict: str = "PASS", phase: int = 2) -> tuple[Path, Path]:
        run_dir = root / "MIG-HUMAN-GATE"
        gate_report = run_dir / "controller" / "gate-report.json"
        write_json(gate_report, {
            "run_id": run_dir.name,
            "phase": phase,
            "verdict": verdict,
            "errors": [] if verdict == "PASS" else ["coverage incomplete"],
            "warnings": [],
        })
        self.generate_summary(run_dir, gate_report, phase)
        return run_dir, gate_report

    def run_script(self, name: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def generate_summary(self, run_dir: Path, gate_report: Path, phase: int = 2) -> None:
        source = run_dir / f"review-input-{phase}.json"
        write_json(source, {"coverage": {}, "exceptions": [], "top_risks": []})
        result = self.run_script(
            "generate_review_summary.py",
            "--run-dir", str(run_dir), "--phase", str(phase),
            "--gate-report", str(gate_report), "--input", str(source),
        )
        self.assertEqual(0, result.returncode, result.stderr)

    def test_recorder_requires_current_review_summary(self) -> None:
        with tempfile.TemporaryDirectory(prefix="human-gate-summary-required-") as temp:
            run_dir, gate_report = self.make_run(Path(temp))
            summary = run_dir / "controller" / "review-summaries" / "phase-02" / "review-summary.json"
            summary.unlink()
            summary.with_suffix(".json.gate.sha256").unlink()
            result = self.run_script(
                "record_human_review.py",
                "--run-dir", str(run_dir), "--phase", "2",
                "--gate-report", str(gate_report), "--review-id", "HREV-NO-SUMMARY",
                "--reviewer", "human-reviewer", "--decision", "APPROVED",
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("review summary", result.stderr.lower())

    def test_recorder_rejects_review_summary_bound_to_old_gate_hash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="human-gate-summary-stale-") as temp:
            run_dir, gate_report = self.make_run(Path(temp))
            self.generate_summary(run_dir, gate_report)
            gate = json.loads(gate_report.read_text(encoding="utf-8"))
            gate["checked_at"] = "2099-01-01T00:00:00Z"
            write_json(gate_report, gate)
            result = self.run_script(
                "record_human_review.py",
                "--run-dir", str(run_dir), "--phase", "2",
                "--gate-report", str(gate_report), "--review-id", "HREV-STALE-SUMMARY",
                "--reviewer", "human-reviewer", "--decision", "APPROVED",
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("review summary", result.stderr.lower())

    def test_human_review_directory_cannot_escape_run_through_link(self) -> None:
        with tempfile.TemporaryDirectory(prefix="human-review-link-") as temp:
            root = Path(temp)
            run_dir, gate_report = self.make_run(root)
            self.generate_summary(run_dir, gate_report)
            outside = root / "outside-human-reviews"
            outside.mkdir()
            link = run_dir / "controller" / "human-reviews"
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                if sys.platform != "win32":
                    self.skipTest(f"Directory symlinks unavailable: {exc}")
                junction = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                )
                if junction.returncode != 0:
                    self.skipTest(f"Directory links unavailable: {junction.stderr or junction.stdout}")
            result = self.run_script(
                "record_human_review.py",
                "--run-dir", str(run_dir), "--phase", "2",
                "--gate-report", str(gate_report), "--review-id", "HREV-ESCAPE",
                "--reviewer", "human-reviewer", "--decision", "APPROVED",
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("inside the migration run", result.stderr.lower())
            self.assertFalse((outside / "phase-02" / "HREV-ESCAPE.json").exists())

    def test_review_summary_is_exception_first_and_never_claims_pass(self) -> None:
        with tempfile.TemporaryDirectory(prefix="human-gate-summary-") as temp:
            run_dir, gate_report = self.make_run(Path(temp))
            source = run_dir / "review-input.json"
            write_json(source, {
                "coverage": {"required": 10, "verified": 8},
                "top_risks": [
                    {"severity": "LOW", "title": "minor spacing"},
                    {"severity": "CRITICAL", "title": "missing payment page"},
                ],
                "exceptions": [
                    {"id": "EX-LOW", "severity": "LOW", "title": "spacing"},
                    {"id": "EX-CRIT", "severity": "CRITICAL", "title": "page missing"},
                    {"id": "EX-WARN", "severity": "MEDIUM", "title": "state untested"},
                ],
                "key_samples": [{"page_id": "PAGE-001"}],
                "evidence_links": ["phase-02/evidence/EV-001"],
            })

            result = self.run_script(
                "generate_review_summary.py",
                "--run-dir", str(run_dir),
                "--phase", "2",
                "--gate-report", str(gate_report),
                "--input", str(source),
            )
            self.assertEqual(0, result.returncode, result.stderr)

            summary = json.loads((run_dir / "controller" / "review-summaries" / "phase-02" / "review-summary.json").read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "phase", "status", "coverage", "critical_count", "warning_count",
                    "top_risks", "exceptions", "key_samples", "evidence_links",
                    "recommended_action",
                },
                set(summary),
            )
            self.assertNotEqual("PASS", summary["status"])
            self.assertEqual("EX-CRIT", summary["exceptions"][0]["id"])
            self.assertEqual("missing payment page", summary["top_risks"][0]["title"])
            self.assertEqual(1, summary["critical_count"])
            self.assertEqual(2, summary["warning_count"])
            self.assertEqual("REWORK", summary["recommended_action"])

    def test_review_summary_can_be_generated_without_optional_input(self) -> None:
        with tempfile.TemporaryDirectory(prefix="human-gate-no-input-") as temp:
            run_dir, gate_report = self.make_run(Path(temp), verdict="FAIL", phase=1)
            result = self.run_script(
                "generate_review_summary.py",
                "--run-dir", str(run_dir), "--phase", "1",
                "--gate-report", str(gate_report),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            summary_path = (
                run_dir / "controller" / "review-summaries" / "phase-01" / "review-summary.json"
            )
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual("MACHINE_GATE_FAILED", summary["status"])
            self.assertEqual("CRITICAL", summary["exceptions"][0]["severity"])
            self.assertEqual("coverage incomplete", summary["exceptions"][0]["title"])
            self.assertTrue(summary_path.with_suffix(".json.gate.sha256").is_file())

    def test_review_input_cannot_hide_or_downgrade_machine_anomalies(self) -> None:
        with tempfile.TemporaryDirectory(prefix="human-gate-machine-anomaly-") as temp:
            run_dir, gate_report = self.make_run(Path(temp), verdict="PASS")
            gate = json.loads(gate_report.read_text(encoding="utf-8"))
            gate["errors"] = ["missing required page"]
            gate["warnings"] = ["one state has weak evidence"]
            write_json(gate_report, gate)
            source = run_dir / "review-input.json"
            write_json(source, {
                "exceptions": [],
                "top_risks": [{"id": "GATE-ERROR-001", "severity": "LOW", "title": "harmless"}],
            })
            result = self.run_script(
                "generate_review_summary.py",
                "--run-dir", str(run_dir), "--phase", "2",
                "--gate-report", str(gate_report), "--input", str(source),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            summary = json.loads(
                (run_dir / "controller" / "review-summaries" / "phase-02" / "review-summary.json")
                .read_text(encoding="utf-8")
            )
            machine_exceptions = {item["id"]: item for item in summary["exceptions"] if item.get("source") == "machine_gate"}
            machine_risks = {item["id"]: item for item in summary["top_risks"] if item.get("source") == "machine_gate"}
            self.assertEqual("CRITICAL", machine_exceptions["GATE-ERROR-001"]["severity"])
            self.assertEqual("missing required page", machine_exceptions["GATE-ERROR-001"]["title"])
            self.assertEqual("MEDIUM", machine_exceptions["GATE-WARNING-001"]["severity"])
            self.assertEqual(set(machine_exceptions), set(machine_risks))

    def test_review_summary_key_samples_are_deterministic_and_capped(self) -> None:
        with tempfile.TemporaryDirectory(prefix="human-gate-sample-cap-") as temp:
            run_dir, gate_report = self.make_run(Path(temp))
            source = run_dir / "review-input.json"
            write_json(source, {
                "key_samples": [{"page_id": f"PAGE-{number:03d}"} for number in range(12, 0, -1)],
            })
            result = self.run_script(
                "generate_review_summary.py",
                "--run-dir", str(run_dir), "--phase", "2",
                "--gate-report", str(gate_report), "--input", str(source),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            summary = json.loads(
                (run_dir / "controller" / "review-summaries" / "phase-02" / "review-summary.json")
                .read_text(encoding="utf-8")
            )
            self.assertEqual(5, len(summary["key_samples"]))
            self.assertEqual(
                [f"PAGE-{number:03d}" for number in range(1, 6)],
                [item["page_id"] for item in summary["key_samples"]],
            )

    def test_review_summary_does_not_wait_for_approval_when_pass_has_blockers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="human-gate-summary-blocked-") as temp:
            run_dir, gate_report = self.make_run(Path(temp))
            gate = json.loads(gate_report.read_text(encoding="utf-8"))
            gate["errors"] = ["coverage incomplete"]
            write_json(gate_report, gate)
            self.generate_summary(run_dir, gate_report)
            summary = json.loads(
                (run_dir / "controller" / "review-summaries" / "phase-02" / "review-summary.json").read_text(encoding="utf-8")
            )
            self.assertEqual("MACHINE_GATE_FAILED", summary["status"])
            self.assertEqual("REWORK", summary["recommended_action"])

    def test_machine_fail_cannot_be_approved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="human-gate-fail-") as temp:
            run_dir, gate_report = self.make_run(Path(temp), verdict="FAIL")
            result = self.run_script(
                "record_human_review.py",
                "--run-dir", str(run_dir),
                "--phase", "2",
                "--gate-report", str(gate_report),
                "--review-id", "HREV-FAIL-001",
                "--reviewer", "human-reviewer",
                "--decision", "APPROVED",
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("machine gate", result.stderr.lower())
            self.assertFalse((run_dir / "controller" / "human-reviews" / "phase-02" / "HREV-FAIL-001.json").exists())

    def test_recorder_rejects_noncurrent_gate_report_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="human-gate-old-report-") as temp:
            run_dir, gate_report = self.make_run(Path(temp))
            old_report = run_dir / "controller" / "old-gate-report.json"
            old_report.write_bytes(gate_report.read_bytes())
            result = self.run_script(
                "record_human_review.py",
                "--run-dir", str(run_dir), "--phase", "2",
                "--gate-report", str(old_report), "--review-id", "HREV-OLD",
                "--reviewer", "human-reviewer", "--decision", "APPROVED",
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("current controller gate report", result.stderr.lower())

    def test_machine_pass_with_blockers_cannot_be_approved(self) -> None:
        for blocker in (
            {"errors": ["coverage incomplete"]},
            {"blocking": True},
            {"critical_count": 1},
        ):
            with self.subTest(blocker=blocker), tempfile.TemporaryDirectory(prefix="human-gate-blocked-") as temp:
                run_dir, gate_report = self.make_run(Path(temp), verdict="PASS")
                gate = json.loads(gate_report.read_text(encoding="utf-8"))
                gate.update(blocker)
                write_json(gate_report, gate)
                result = self.run_script(
                    "record_human_review.py",
                    "--run-dir", str(run_dir), "--phase", "2",
                    "--gate-report", str(gate_report), "--review-id", "HREV-BLOCKED",
                    "--reviewer", "human-reviewer", "--decision", "APPROVED",
                )
                self.assertEqual(2, result.returncode)
                self.assertIn("machine gate", result.stderr.lower())

    def test_human_review_is_immutable_and_duplicate_id_cannot_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="human-gate-immutable-") as temp:
            run_dir, gate_report = self.make_run(Path(temp))
            args = (
                "--run-dir", str(run_dir), "--phase", "2",
                "--gate-report", str(gate_report), "--review-id", "HREV-001",
                "--reviewer", "human-reviewer", "--decision", "APPROVED",
            )
            first = self.run_script("record_human_review.py", *args)
            self.assertEqual(0, first.returncode, first.stderr)
            record_path = run_dir / "controller" / "human-reviews" / "phase-02" / "HREV-001.json"
            original = record_path.read_bytes()

            second = self.run_script("record_human_review.py", *args)
            self.assertEqual(2, second.returncode)
            self.assertIn("already exists", second.stderr.lower())
            self.assertEqual(original, record_path.read_bytes())

    def test_shared_reader_rejects_stale_and_forged_reviews(self) -> None:
        with tempfile.TemporaryDirectory(prefix="human-gate-stale-") as temp:
            run_dir, gate_report = self.make_run(Path(temp))
            result = self.run_script(
                "record_human_review.py",
                "--run-dir", str(run_dir), "--phase", "2",
                "--gate-report", str(gate_report), "--review-id", "HREV-001",
                "--reviewer", "human-reviewer", "--decision", "APPROVED",
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual("APPROVED", read_current_human_review(run_dir, 2, gate_report)["decision"])

            gate = json.loads(gate_report.read_text(encoding="utf-8"))
            gate["checked_at"] = "2099-01-01T00:00:00Z"
            write_json(gate_report, gate)
            with self.assertRaisesRegex(ValueError, "current gate report"):
                read_current_human_review(run_dir, 2, gate_report)

            forged = run_dir / "controller" / "human-reviews" / "phase-02" / "HREV-FORGED.json"
            write_json(forged, {
                "review_id": "HREV-FORGED",
                "phase": 2,
                "decision": "APPROVED",
                "reviewer": "attacker",
                "gate_report_sha256": hashlib.sha256(gate_report.read_bytes()).hexdigest(),
            })
            with self.assertRaisesRegex(ValueError, "seal"):
                read_current_human_review(run_dir, 2, gate_report)

    def test_reader_rejects_approved_deviation_without_deviations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="human-gate-deviation-") as temp:
            run_dir, gate_report = self.make_run(Path(temp))
            self.generate_summary(run_dir, gate_report)
            result = self.run_script(
                "record_human_review.py",
                "--run-dir", str(run_dir), "--phase", "2",
                "--gate-report", str(gate_report), "--review-id", "HREV-DEVIATION",
                "--reviewer", "human-reviewer", "--decision", "APPROVED_DEVIATION",
                "--deviation", "accepted spacing difference",
            )
            self.assertEqual(0, result.returncode, result.stderr)
            record_path = run_dir / "controller" / "human-reviews" / "phase-02" / "HREV-DEVIATION.json"
            original = json.loads(record_path.read_text(encoding="utf-8"))
            record_path.chmod(0o644)
            seal_path = record_path.with_suffix(".json.sha256")
            seal_path.chmod(0o644)
            for malformed in ([], [""], ["   "], [123], "accepted spacing difference", {"text": "difference"}):
                with self.subTest(malformed=malformed):
                    record = dict(original)
                    record["deviations"] = malformed
                    write_json(record_path, record)
                    seal_path.write_text(f"{hashlib.sha256(record_path.read_bytes()).hexdigest()}  {record_path.name}\n", encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "deviation"):
                        read_current_human_review(run_dir, 2, gate_report)

    def test_review_summary_rejects_symlink_output_escape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="human-gate-symlink-") as temp:
            root = Path(temp)
            run_dir, gate_report = self.make_run(root)
            source = run_dir / "review-input.json"
            write_json(source, {"coverage": {}, "exceptions": []})
            outside = root / "outside"
            outside.mkdir()
            link = run_dir / "controller" / "review-summaries"
            summary_dir = link / "phase-02"
            (summary_dir / "review-summary.json").unlink()
            (summary_dir / "review-summary.json.gate.sha256").unlink()
            summary_dir.rmdir()
            link.rmdir()
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                if sys.platform != "win32":
                    self.skipTest(f"Directory symlinks unavailable: {exc}")
                junction = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                )
                if junction.returncode != 0:
                    self.skipTest(f"Directory links unavailable: {junction.stderr or junction.stdout}")
            result = self.run_script(
                "generate_review_summary.py",
                "--run-dir", str(run_dir), "--phase", "2",
                "--gate-report", str(gate_report), "--input", str(source),
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("inside the migration run", result.stderr.lower())
            self.assertFalse((outside / "phase-02" / "review-summary.json").exists())


if __name__ == "__main__":
    unittest.main()
