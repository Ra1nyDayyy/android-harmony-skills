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

        # gmi 布局双写：Phase 2 批准时为 gmi_phase3_adapter 额外固化人工验收记录；
        # run 下无 gmi phase-2 closure 时视为 legacy 流程，仅提示、不阻断
        acceptance_path: Path | None = None
        acceptance_content = ""
        if args.phase == 2 and args.decision in APPROVAL_DECISIONS:
            gmi_closure_path = run_dir / "phase-02-android-inventory" / "phase-2-closure.json"
            if gmi_closure_path.is_file():
                gmi_closure = load_json_object(gmi_closure_path, "gmi phase-2 closure")
                if gmi_closure.get("machine_status") != "READY_FOR_HUMAN_REVIEW":
                    raise ValueError(
                        "gmi phase-2 closure machine_status is "
                        f"{gmi_closure.get('machine_status')!r}; only READY_FOR_HUMAN_REVIEW may be accepted"
                    )
                acceptance_path = (
                    run_dir / "phase-02-android-inventory" / "human-review" / "phase-2-acceptance.json"
                )
                if acceptance_path.exists():
                    raise ValueError(f"gmi phase-2 acceptance already exists: {acceptance_path}")
                acceptance_recorded_at = utc_now()
                # decision/closure_sha256 为 gmi_phase3_adapter 的强制校验字段；
                # reviewer_id/accepted_at 是 adapter 读取的字段名，与 reviewer/recorded_at 同源
                acceptance_record = {
                    "decision": "ACCEPTED",
                    "closure_sha256": sha256_file(gmi_closure_path),
                    "reviewer": args.reviewer.strip(),
                    "reviewer_id": args.reviewer.strip(),
                    "review_id": args.review_id,
                    "recorded_at": acceptance_recorded_at,
                    "accepted_at": acceptance_recorded_at,
                    "source_decision": args.decision,
                    "reason": args.reason,
                }
                acceptance_content = json.dumps(acceptance_record, indent=2, ensure_ascii=False) + "\n"
            else:
                print(
                    "note: gmi phase-2 closure not found; skipping gmi acceptance dual-write",
                    file=sys.stderr,
                )

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
        if acceptance_path is not None:
            try:
                exclusive_write(acceptance_path, acceptance_content)
            except Exception:
                # 验收记录写出失败时连同主记录一起回滚，保持 run 可重试
                record_path.unlink(missing_ok=True)
                seal_path.unlink(missing_ok=True)
                raise
            print(acceptance_path)
        print(record_path)
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
