#!/usr/bin/env python3
"""Generate the compact, exception-first review summary for one phase."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from _human_gate import gate_is_clear_pass, load_json_object, resolve_run_file, sha256_file


SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def severity_key(value: Any) -> tuple[int, str]:
    if not isinstance(value, dict):
        return (99, str(value))
    severity = str(value.get("severity", "")).upper()
    identity = str(value.get("id", value.get("title", "")))
    return (SEVERITY_ORDER.get(severity, 99), identity)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def build_summary(phase: int, gate_report: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    exceptions = sorted(list(source.get("exceptions", [])), key=severity_key)
    top_risks = sorted(list(source.get("top_risks", [])), key=severity_key)
    critical_count = sum(
        1 for item in exceptions
        if isinstance(item, dict) and str(item.get("severity", "")).upper() == "CRITICAL"
    )
    warning_count = len(exceptions) - critical_count
    machine_pass = gate_is_clear_pass(gate_report)
    recommended_action = "REWORK" if not machine_pass or critical_count else "REVIEW"
    return {
        "phase": phase,
        "status": "WAITING_HUMAN_REVIEW" if machine_pass else "MACHINE_GATE_FAILED",
        "coverage": source.get("coverage", {}),
        "critical_count": critical_count,
        "warning_count": warning_count,
        "top_risks": top_risks,
        "exceptions": exceptions,
        "key_samples": list(source.get("key_samples", [])),
        "evidence_links": list(source.get("evidence_links", [])),
        "recommended_action": recommended_action,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--phase", required=True, type=int, choices=(1, 2, 3, 4))
    parser.add_argument("--gate-report", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    try:
        run_dir = Path(args.run_dir).resolve()
        gate_path = resolve_run_file(run_dir, Path(args.gate_report), "controller gate report")
        source_path = resolve_run_file(run_dir, Path(args.input), "review summary input")
        gate_report = load_json_object(gate_path, "controller gate report")
        if gate_report.get("phase") != args.phase:
            raise ValueError("Controller gate report phase differs from requested summary phase")
        source = load_json_object(source_path, "review summary input")
        output = (
            Path(args.output)
            if args.output
            else run_dir / "controller" / "review-summaries" / f"phase-{args.phase:02d}" / "review-summary.json"
        )
        if not output.is_absolute():
            output = run_dir / output
        output = output.resolve()
        try:
            output.relative_to(run_dir)
        except ValueError as exc:
            raise ValueError("Review summary output must stay inside the migration run") from exc
        atomic_json(output, build_summary(args.phase, gate_report, source))
        sidecar = output.with_suffix(output.suffix + ".gate.sha256").resolve()
        try:
            sidecar.relative_to(run_dir)
        except ValueError as exc:
            raise ValueError("Review summary output must stay inside the migration run") from exc
        atomic_text(sidecar, f"{sha256_file(gate_path)}\n")
        print(output)
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
