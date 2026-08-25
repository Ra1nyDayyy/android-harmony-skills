#!/usr/bin/env python3
"""Record one immutable completion receipt for an actually dispatched worker task."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from _team_execution import ID_RE, expected_actors, load_json, read_registry, safe_file, sha256_file


FIELDS = [
    "receipt_id", "phase", "work_order_id", "work_order_sha256", "role_key", "actor_id",
    "platform_task_id", "started_at", "ended_at", "terminal_task_state",
    "relative_path", "receipt_sha256", "status", "recorded_at",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_instant(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp with timezone") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_csv(path: Path, rows: list[dict[str, str]]) -> None:
    from io import StringIO
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write(path, output.getvalue().encode("utf-8"))


@contextmanager
def registry_lock(run_dir: Path):
    """Serialize receipts from workers that finish at nearly the same time."""
    lock_path = run_dir / "controller" / ".team-execution.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--work-order", required=True, help="Run-relative controller or feature work-order JSON")
    parser.add_argument("--role-key", required=True)
    parser.add_argument("--actor-id", required=True)
    parser.add_argument("--platform-task-id", required=True, help="Real CodeArts worker/task execution ID")
    parser.add_argument("--started-at", required=True, help="CodeArts task start timestamp")
    parser.add_argument("--ended-at", required=True, help="CodeArts task terminal timestamp")
    parser.add_argument("--terminal-task-state", required=True, choices=("SUCCEEDED",))
    parser.add_argument("--artifact", action="append", required=True, help="Run-relative file produced or reviewed by this worker")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.is_dir():
        parser.error("Migration run does not exist")
    try:
        order_path = safe_file(run_dir, args.work_order, "work order")
        order = load_json(order_path)
        assignments = expected_actors(order)
        if (args.role_key, args.actor_id) not in assignments:
            parser.error("Role and actor do not match the immutable work order")
        if not ID_RE.fullmatch(args.platform_task_id):
            parser.error("Platform task ID is invalid")
        if order.get("schema_version") in {"page-work-order-v1", "capability-work-order-v1"}:
            if args.platform_task_id != order.get("codearts_task_id"):
                parser.error("Platform task ID differs from the page/capability order binding")
        started_at = parse_instant(args.started_at, "started-at")
        ended_at = parse_instant(args.ended_at, "ended-at")
        if ended_at < started_at:
            parser.error("ended-at must not precede started-at")
        work_order_sha256 = sha256_file(order_path)
        artifact_records = []
        for relative in args.artifact:
            artifact_path = safe_file(run_dir, relative, "worker artifact")
            artifact_records.append({"relative_path": relative.replace("\\", "/"), "sha256": sha256_file(artifact_path)})
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    work_order_id = str(order["work_order_id"])
    phase = int(order["phase"])
    registry_path = run_dir / "controller" / "team-execution-registry.csv"
    with registry_lock(run_dir):
        rows = read_registry(run_dir)
        if any(row.get("work_order_id") == work_order_id and row.get("role_key") == args.role_key and row.get("actor_id") == args.actor_id for row in rows):
            parser.error("A completion receipt already exists for this assigned worker")
        if any(row.get("platform_task_id") == args.platform_task_id for row in rows):
            parser.error("A platform task ID cannot satisfy multiple worker assignments")

        recorded_at = utc_now()
        receipt_id = "TEAM-" + hashlib.sha256(
            f"{work_order_id}|{args.role_key}|{args.actor_id}|{args.platform_task_id}".encode("utf-8")
        ).hexdigest()[:20].upper()
        relative = f"controller/team-execution-receipts/{work_order_id}/{receipt_id}.json"
        receipt_path = run_dir / relative
        if receipt_path.exists():
            parser.error("Receipt overwrite is prohibited")
        receipt = {
            "receipt_id": receipt_id,
            "phase": phase,
            "work_order_id": work_order_id,
            "work_order_sha256": work_order_sha256,
            "role_key": args.role_key,
            "actor_id": args.actor_id,
            "platform_task_id": args.platform_task_id,
            "started_at": args.started_at,
            "ended_at": args.ended_at,
            "terminal_task_state": args.terminal_task_state,
            "status": "COMPLETED",
            "recorded_at": recorded_at,
            "artifacts": artifact_records,
        }
        data = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        atomic_write(receipt_path, data)
        rows.append({
            "receipt_id": receipt_id,
            "phase": str(phase),
            "work_order_id": work_order_id,
            "work_order_sha256": work_order_sha256,
            "role_key": args.role_key,
            "actor_id": args.actor_id,
            "platform_task_id": args.platform_task_id,
            "started_at": args.started_at,
            "ended_at": args.ended_at,
            "terminal_task_state": args.terminal_task_state,
            "relative_path": relative,
            "receipt_sha256": sha256_file(receipt_path),
            "status": "COMPLETED",
            "recorded_at": recorded_at,
        })
        try:
            atomic_csv(registry_path, rows)
        except OSError as exc:
            receipt_path.unlink(missing_ok=True)
            parser.error(f"Could not update team execution registry: {exc}")
    print(json.dumps({"receipt_id": receipt_id, "receipt": str(receipt_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
