from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))

import audit_delivery  # noqa: E402
import issue_phase2_work_order  # noqa: E402
import issue_phase3_work_order  # noqa: E402
import issue_phase4_work_order  # noqa: E402
from _human_gate import require_current_human_approval  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class HumanGateWiringTest(unittest.TestCase):
    def make_run(self, root: Path, phase: int) -> tuple[Path, Path]:
        run_dir = root / f"MIG-HUMAN-WIRE-{phase}"
        scope_path = run_dir / "controller" / "scope.json"
        scope = {
            "run_id": run_dir.name,
            "ownership": {
                "migration_controller_id": "controller-001",
                "inventory_lead_id": "inventory-lead-001",
                "code_map_agent_id": "code-map-001",
                "runtime_state_agent_id": "runtime-001",
                "business_rule_agent_id": "business-001",
                "data_dependency_agent_id": "data-001",
                "evidence_administrator_id": "evidence-001",
                "coverage_checker_id": "coverage-001",
            },
            "environments": [{"env_id": "ENV-001", "is_baseline": True}],
            "android": {"apk_sha256": "a" * 64},
        }
        write_json(scope_path, scope)
        gate_path = run_dir / "controller" / "gate-report.json"
        write_json(gate_path, {
            "run_id": run_dir.name,
            "phase": phase,
            "verdict": "PASS",
            "scope_sha256": hashlib.sha256(scope_path.read_bytes()).hexdigest(),
            "errors": [],
        })
        summary_input = run_dir / "review-input.json"
        write_json(summary_input, {"coverage": {}, "exceptions": [], "top_risks": []})
        generated = subprocess.run(
            [
                sys.executable, str(SCRIPTS / "generate_review_summary.py"),
                "--run-dir", str(run_dir), "--phase", str(phase),
                "--gate-report", str(gate_path), "--input", str(summary_input),
            ],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        self.assertEqual(0, generated.returncode, generated.stderr)
        return run_dir, gate_path

    def record(self, run_dir: Path, gate_path: Path, phase: int, review_id: str, decision: str) -> None:
        args = [
            sys.executable, str(SCRIPTS / "record_human_review.py"),
            "--run-dir", str(run_dir), "--phase", str(phase),
            "--gate-report", str(gate_path), "--review-id", review_id,
            "--reviewer", "human-reviewer", "--decision", decision,
        ]
        if decision == "APPROVED_DEVIATION":
            args.extend(["--deviation", "UI spacing accepted"])
        result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(0, result.returncode, result.stderr)

    def run_issuer(self, module: object, run_dir: Path, phase: int, mutate_gate: bool = False) -> str:
        argv = ["issuer", "--run-dir", str(run_dir), "--issued-by", "controller-001"]
        if phase == 2:
            pass
        elif phase == 3:
            for key, value in {
                "architecture-lead-id": "arch-001", "toolchain-agent-id": "tool-001",
                "navigation-agent-id": "nav-001", "public-ui-agent-id": "public-001",
                "capability-contract-agent-id": "cap-001", "architecture-acceptance-agent-id": "accept-001",
            }.items():
                argv.extend([f"--{key}", value])
        else:
            for key, value in {
                "implementation-lead-id": "impl-001", "visual-asset-agent-id": "asset-001",
                "verification-executor-id": "verify-001", "parity-acceptance-agent-id": "accept4-001",
            }.items():
                argv.extend([f"--{key}", value])

        def recheck(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if mutate_gate:
                gate_path = run_dir / "controller" / "gate-report.json"
                gate = json.loads(gate_path.read_text(encoding="utf-8"))
                gate["checked_at"] = "2099-01-01T00:00:00Z"
                write_json(gate_path, gate)
            return subprocess.CompletedProcess([], 0, stdout="{}", stderr="")

        stderr = io.StringIO()
        with mock.patch.object(sys, "argv", argv), mock.patch.object(module.subprocess, "run", side_effect=recheck), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                module.main()
        return stderr.getvalue()

    def test_each_issuer_rejects_machine_pass_without_human_approval(self) -> None:
        cases = (
            (issue_phase2_work_order, 1),
            (issue_phase3_work_order, 2),
            (issue_phase4_work_order, 3),
        )
        for module, gate_phase in cases:
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory(prefix="issuer-human-missing-") as temp:
                run_dir, _ = self.make_run(Path(temp), gate_phase)
                error = self.run_issuer(module, run_dir, gate_phase + 1)
                self.assertIn("human approval", error.lower())

    def test_issuer_reads_gate_after_recheck_and_rejects_stale_approval(self) -> None:
        with tempfile.TemporaryDirectory(prefix="issuer-human-stale-") as temp:
            run_dir, gate_path = self.make_run(Path(temp), 1)
            self.record(run_dir, gate_path, 1, "HREV-STALE", "APPROVED")
            error = self.run_issuer(issue_phase2_work_order, run_dir, 2, mutate_gate=True)
            self.assertIn("current gate report", error.lower())

    def test_only_approval_decisions_authorize_continuation(self) -> None:
        for decision, allowed in (
            ("APPROVED", True),
            ("APPROVED_DEVIATION", True),
            ("REWORK", False),
            ("MANUAL_TAKEOVER", False),
        ):
            with self.subTest(decision=decision), tempfile.TemporaryDirectory(prefix="human-decision-") as temp:
                run_dir, gate_path = self.make_run(Path(temp), 2)
                self.record(run_dir, gate_path, 2, f"HREV-{decision}", decision)
                if allowed:
                    self.assertEqual(decision, require_current_human_approval(run_dir, 2, gate_path)["decision"])
                else:
                    with self.assertRaisesRegex(ValueError, "does not authorize"):
                        require_current_human_approval(run_dir, 2, gate_path)

    def test_delivery_human_gate_check_rejects_missing_and_stale_approval(self) -> None:
        with tempfile.TemporaryDirectory(prefix="delivery-human-") as temp:
            run_dir, gate_path = self.make_run(Path(temp), 4)
            self.assertTrue(audit_delivery.human_approval_errors(run_dir, {4: gate_path}))
            self.record(run_dir, gate_path, 4, "HREV-DELIVERY", "APPROVED")
            self.assertEqual([], audit_delivery.human_approval_errors(run_dir, {4: gate_path}))

            gate = json.loads(gate_path.read_text(encoding="utf-8"))
            gate["checked_at"] = "2099-01-01T00:00:00Z"
            write_json(gate_path, gate)
            errors = audit_delivery.human_approval_errors(run_dir, {4: gate_path})
            self.assertTrue(any("current gate report" in error.lower() for error in errors))


if __name__ == "__main__":
    unittest.main()
