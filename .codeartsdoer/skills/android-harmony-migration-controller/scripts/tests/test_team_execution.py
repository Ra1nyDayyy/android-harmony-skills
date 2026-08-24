from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))

from _team_execution import validate_order_receipts  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
            "receipt_id,phase,work_order_id,work_order_sha256,role_key,actor_id,platform_task_id,started_at,ended_at,terminal_task_state,relative_path,receipt_sha256,status,recorded_at\n",
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
            "--started-at", "2026-08-24T10:00:00Z",
            "--ended-at", "2026-08-24T10:05:00Z",
            "--terminal-task-state", "SUCCEEDED",
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

    def test_receipt_binds_work_order_hash_timestamps_and_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="team-execution-") as temp:
            run_dir, order, artifact = self.make_run(Path(temp))
            role, actor = next(iter(ROLES.items()))
            self.record(run_dir, order, artifact, role, actor, "CODEARTS-TASK-01")
            receipts = list((run_dir / "controller" / "team-execution-receipts").rglob("*.json"))
            self.assertEqual(1, len(receipts))
            receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
            self.assertEqual(sha256(order), receipt["work_order_sha256"])
            self.assertEqual("2026-08-24T10:00:00Z", receipt["started_at"])
            self.assertEqual("2026-08-24T10:05:00Z", receipt["ended_at"])
            self.assertEqual("SUCCEEDED", receipt["terminal_task_state"])

    def test_page_order_receipt_uses_page_owner_assignment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="team-execution-") as temp:
            run_dir = Path(temp) / "run"
            order = run_dir / "phase-04-harmony-implementation" / "page-work-orders" / "H4PWO-TEST.json"
            write_json(order, {
                "schema_version": "page-work-order-v1", "work_order_id": "H4PWO-TEST",
                "phase": 4, "page_id": "PAGE-A", "owner_id": "page-owner-a",
                "codearts_task_id": "CODEARTS-PAGE-01",
            })
            registry = run_dir / "controller" / "team-execution-registry.csv"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                "receipt_id,phase,work_order_id,work_order_sha256,role_key,actor_id,platform_task_id,started_at,ended_at,terminal_task_state,relative_path,receipt_sha256,status,recorded_at\n",
                encoding="utf-8",
            )
            artifact = run_dir / "phase-04-harmony-implementation" / "page-result.txt"
            artifact.write_text("page output\n", encoding="utf-8")
            self.record(run_dir, order, artifact, "page_owner_id", "page-owner-a", "CODEARTS-PAGE-01")
            self.assertEqual([], validate_order_receipts(run_dir, order))

    def test_page_receipt_rejects_task_other_than_the_order_binding(self) -> None:
        with tempfile.TemporaryDirectory(prefix="team-execution-") as temp:
            run_dir = Path(temp) / "run"
            order = run_dir / "phase-04-harmony-implementation" / "page-work-orders" / "H4PWO-TEST.json"
            write_json(order, {
                "schema_version": "page-work-order-v1", "work_order_id": "H4PWO-TEST",
                "phase": 4, "page_id": "PAGE-A", "owner_id": "page-owner-a",
                "codearts_task_id": "CODEARTS-PAGE-EXPECTED",
            })
            registry = run_dir / "controller" / "team-execution-registry.csv"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                "receipt_id,phase,work_order_id,work_order_sha256,role_key,actor_id,platform_task_id,started_at,ended_at,terminal_task_state,relative_path,receipt_sha256,status,recorded_at\n",
                encoding="utf-8",
            )
            artifact = run_dir / "phase-04-harmony-implementation" / "page-result.txt"
            artifact.write_text("page output\n", encoding="utf-8")
            self.record(
                run_dir, order, artifact, "page_owner_id", "page-owner-a",
                "CODEARTS-PAGE-WRONG", expect=2,
            )


if __name__ == "__main__":
    unittest.main()
