#!/usr/bin/env python3
"""Shared validation and lookup helpers for immutable human reviews."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


DECISIONS = {"APPROVED", "REWORK", "APPROVED_DEVIATION", "MANUAL_TAKEOVER"}
APPROVAL_DECISIONS = {"APPROVED", "APPROVED_DEVIATION"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return value


def gate_verdict(report: dict[str, Any]) -> str:
    verdict = report.get("verdict", report.get("machine_verdict", ""))
    return str(verdict).strip().upper()


def resolve_run_file(run_dir: Path, value: Path, label: str) -> Path:
    root = run_dir.resolve()
    path = value if value.is_absolute() else root / value
    path = path.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the migration run: {path}") from exc
    if not path.is_file():
        raise ValueError(f"Missing {label}: {path}")
    return path


def review_directory(run_dir: Path, phase: int) -> Path:
    return run_dir / "controller" / "human-reviews" / f"phase-{phase:02d}"


def _seal_path(record_path: Path) -> Path:
    return record_path.with_suffix(record_path.suffix + ".sha256")


def validate_human_review_record(
    run_dir: Path,
    record_path: Path,
    phase: int,
) -> dict[str, Any]:
    record_path = resolve_run_file(run_dir, record_path, "human review")
    seal_path = _seal_path(record_path)
    if not seal_path.is_file():
        raise ValueError(f"Human review seal is missing: {seal_path}")
    sealed_hash = seal_path.read_text(encoding="utf-8").strip().split()[0]
    if sealed_hash != sha256_file(record_path):
        raise ValueError(f"Human review seal does not match: {record_path}")

    record = load_json_object(record_path, "human review")
    required = {
        "review_id", "phase", "decision", "reviewer", "gate_report_relative_path",
        "gate_report_sha256", "recorded_at",
    }
    missing = sorted(required - set(record))
    if missing:
        raise ValueError(f"Human review is missing required fields: {', '.join(missing)}")
    if record.get("phase") != phase:
        raise ValueError(f"Human review phase differs: {record_path}")
    if record.get("decision") not in DECISIONS:
        raise ValueError(f"Human review decision is invalid: {record_path}")
    if not str(record.get("reviewer", "")).strip():
        raise ValueError(f"Human review reviewer is empty: {record_path}")
    return record


def read_current_human_review(
    run_dir: Path,
    phase: int,
    gate_report_path: Path,
) -> dict[str, Any]:
    """Return the sealed review bound to the current gate report.

    Work-order issuers should call this function and accept only the decision(s)
    authorized by their phase policy.
    """

    run_dir = run_dir.resolve()
    gate_report_path = resolve_run_file(run_dir, gate_report_path, "controller gate report")
    report = load_json_object(gate_report_path, "controller gate report")
    if report.get("phase") != phase:
        raise ValueError("Controller gate report phase differs from requested human review phase")
    current_sha256 = sha256_file(gate_report_path)
    current_relative = gate_report_path.relative_to(run_dir).as_posix()

    directory = review_directory(run_dir, phase)
    matches: list[dict[str, Any]] = []
    for record_path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        record = validate_human_review_record(run_dir, record_path, phase)
        if (
            record.get("gate_report_sha256") == current_sha256
            and record.get("gate_report_relative_path") == current_relative
        ):
            if record.get("decision") in APPROVAL_DECISIONS and gate_verdict(report) != "PASS":
                raise ValueError("Approval is invalid because the current machine gate is not PASS")
            matches.append(record)

    if not matches:
        raise ValueError("No sealed human review is bound to the current gate report")
    if len(matches) != 1:
        raise ValueError("Multiple human reviews are bound to the current gate report")
    return matches[0]
