from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))

from _team_execution import validate_order_receipts  # noqa: E402


ROLES = {
    "architecture_lead_id": "arch-actor-001",
    "toolchain_agent_id": "tool-actor-001",
    "navigation_agent_id": "nav-actor-001",
    "public_ui_agent_id": "ui-actor-001",
    "capability_contract_agent_id": "cap-actor-001",
    "architecture_acceptance_agent_id": "accept-actor-001",
}


class TeamExecutionTest(unittest.TestCase):
    def make_run(self, root: Path) -> tuple[Path, Path, Path]:
        run_dir = root / "MIG-TEAM-TEST"
        controller = run_dir / "controller"
        controller.mkdir(parents=True)
        (controller / "team-execution-registry.csv").write_text(
            "receipt_id,phase,work_order_id,role_key,actor_id,platform_task_id,relative_path,receipt_sha256,status,recorded_at\n",
            encoding="utf-8",
        )
        order = controller / "work-orders" / "WO-PHASE-03-TEST.json"
        order.parent.mkdir()
        order.write_text(json.dumps({
            "work_order_id": "WO-PHASE-03-TEST",
            "phase": 3,
            "ownership": ROLES,
        }, indent=2) + "\n", encoding="utf-8")
        artifact = run_dir / "phase-03-harmony-scaffold" / "artifact.txt"
        artifact.parent.mkdir()
        artifact.write_text("role output\n", encoding="utf-8")
        return run_dir, order, artifact

    def record(self, run_dir: Path, order: Path, artifact: Path, role: str, actor: str, task: str, expect: int = 0) -> None:
        result = subprocess.run([
            sys.executable, str(SCRIPTS / "record_team_execution.py"),
            "--run-dir", str(run_dir),
            "--work-order", order.relative_to(run_dir).as_posix(),
            "--role-key", role,
            "--actor-id", actor,
            "--platform-task-id", task,
            "--artifact", artifact.relative_to(run_dir).as_posix(),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        self.assertEqual(expect, result.returncode, result.stderr)

    def test_all_distinct_worker_receipts_validate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="team-execution-") as temp:
            run_dir, order, artifact = self.make_run(Path(temp))
            for index, (role, actor) in enumerate(ROLES.items(), 1):
                self.record(run_dir, order, artifact, role, actor, f"CODEARTS-TASK-{index:02d}")
            self.assertEqual([], validate_order_receipts(run_dir, order))

    def test_missing_or_reused_platform_task_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="team-execution-") as temp:
            run_dir, order, artifact = self.make_run(Path(temp))
            roles = list(ROLES.items())
            self.record(run_dir, order, artifact, roles[0][0], roles[0][1], "CODEARTS-TASK-SAME")
            self.record(run_dir, order, artifact, roles[1][0], roles[1][1], "CODEARTS-TASK-SAME", expect=2)
            errors = validate_order_receipts(run_dir, order)
            self.assertTrue(any("Missing independently dispatched" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
