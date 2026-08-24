#!/usr/bin/env python3
"""Record one immutable human decision bound to the current machine gate report."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from _human_gate import (
    APPROVAL_DECISIONS,
    DECISIONS,
    gate_is_clear_pass,
    load_json_object,
    resolve_run_file,
    review_directory,
    sha256_file,
    validate_current_review_summary,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def exclusive_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(str(path), flags, 0o444)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--phase", required=True, type=int, choices=(1, 2, 3, 4))
    parser.add_argument("--gate-report", required=True)
    parser.add_argument("--review-id", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--decision", required=True, choices=sorted(DECISIONS))
    parser.add_argument("--reason", default="")
    parser.add_argument("--deviation", action="append", default=[])
    args = parser.parse_args()

    try:
        run_dir = Path(args.run_dir).resolve()
        gate_path = resolve_run_file(run_dir, Path(args.gate_report), "controller gate report")
        current_gate_path = (run_dir / "controller" / "gate-report.json").resolve()
        if gate_path != current_gate_path:
            raise ValueError("Human review must bind the current controller gate report")
        report = load_json_object(gate_path, "controller gate report")
        if report.get("phase") != args.phase:
            raise ValueError("Controller gate report phase differs from requested human review phase")
        if args.decision in APPROVAL_DECISIONS and not gate_is_clear_pass(report):
            raise ValueError("Human approval requires a clear PASS machine gate with no blockers")
        validate_current_review_summary(
            run_dir,
            args.phase,
            gate_path,
            require_waiting=args.decision in APPROVAL_DECISIONS,
        )
        if args.decision == "APPROVED_DEVIATION" and not args.deviation:
            raise ValueError("APPROVED_DEVIATION requires at least one --deviation")
        if not args.review_id.strip() or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for char in args.review_id):
            raise ValueError("review-id contains unsafe characters")
        if not args.reviewer.strip():
            raise ValueError("reviewer must be nonempty")

        directory = review_directory(run_dir, args.phase).resolve()
        try:
            directory.relative_to(run_dir)
        except ValueError as exc:
            raise ValueError("Human review output must stay inside the migration run") from exc
        record_path = directory / f"{args.review_id}.json"
        seal_path = record_path.with_suffix(record_path.suffix + ".sha256")
        if record_path.exists() or seal_path.exists():
            raise ValueError(f"Human review already exists: {record_path}")
        record = {
            "review_id": args.review_id,
            "phase": args.phase,
            "decision": args.decision,
            "reviewer": args.reviewer.strip(),
            "gate_report_relative_path": gate_path.relative_to(run_dir).as_posix(),
            "gate_report_sha256": sha256_file(gate_path),
            "reason": args.reason,
            "deviations": args.deviation,
            "recorded_at": utc_now(),
        }
        content = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
        exclusive_write(record_path, content)
        try:
            exclusive_write(seal_path, f"{sha256_file(record_path)}  {record_path.name}\n")
        except Exception:
            record_path.unlink(missing_ok=True)
            raise
        print(record_path)
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
