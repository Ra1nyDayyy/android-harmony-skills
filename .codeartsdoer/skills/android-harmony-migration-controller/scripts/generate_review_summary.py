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
KEY_SAMPLE_LIMIT = 5

PHASE_REPORTS = {
    1: ("controller/scope.json",),
    2: (
        "phase-02-android-inventory/closure-report.json",
        "phase-02-android-inventory/page-gate-report.json",
        "phase-02-android-inventory/advanced-gate-report.json",
        "phase-02-android-inventory/evidence-index.csv",
    ),
    3: (
        "phase-03-harmony-scaffold/stage-03-gate-report.json",
        "phase-03-harmony-scaffold/build-report.json",
    ),
    4: (
        "phase-04-harmony-implementation/stage-04-gate-report.json",
        "phase-04-harmony-implementation/evidence-index.csv",
    ),
}


def severity_key(value: Any) -> tuple[int, str]:
    if not isinstance(value, dict):
        return (99, str(value))
    severity = str(value.get("severity", "")).upper()
    identity = str(value.get("id", value.get("title", "")))
    return (SEVERITY_ORDER.get(severity, 99), identity)


def object_list(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value] if isinstance(value, list) and all(isinstance(item, dict) for item in value) else []


def canonical_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def unique_sorted(values: list[Any], *, limit: int | None = None) -> list[Any]:
    indexed = {canonical_key(value): value for value in values}
    result = [indexed[key] for key in sorted(indexed)]
    return result if limit is None else result[:limit]


def anomaly_title(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("message", "title", "error", "warning", "detail"):
            if str(value.get(key, "")).strip():
                return str(value[key]).strip()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return canonical_key(value)


def machine_anomalies(gate_report: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for field, severity, prefix in (
        ("errors", "CRITICAL", "GATE-ERROR"),
        ("warnings", "MEDIUM", "GATE-WARNING"),
    ):
        values = gate_report.get(field, [])
        if not isinstance(values, list):
            values = [values] if values not in (None, "") else []
        for index, value in enumerate(values, 1):
            items.append({
                "id": f"{prefix}-{index:03d}",
                "severity": severity,
                "title": anomaly_title(value),
                "source": "machine_gate",
            })
    return items


def phase_context(run_dir: Path, phase: int, gate_path: Path, gate_report: dict[str, Any]) -> dict[str, Any]:
    coverage: dict[str, Any] = {}
    included = gate_report.get("included_features")
    if isinstance(included, list):
        coverage["included_features"] = len(included)

    evidence_links = [gate_path.relative_to(run_dir).as_posix()]
    for relative in PHASE_REPORTS[phase]:
        path = run_dir / relative
        if not path.is_file():
            continue
        evidence_links.append(relative)
        if path.suffix != ".json":
            continue
        try:
            report = load_json_object(path, f"Phase {phase} report")
        except ValueError:
            continue
        direct_coverage = report.get("coverage")
        if isinstance(direct_coverage, dict):
            for key, value in sorted(direct_coverage.items()):
                coverage.setdefault(str(key), value)
        counts = report.get("counts")
        if isinstance(counts, dict):
            coverage.setdefault("counts", {str(key): value for key, value in sorted(counts.items())})
        if phase == 2:
            for key in (
                "inventory_rows", "archived_assets", "indexed_evidence",
                "open_rechecks", "open_critical_rechecks", "pending_confirmations",
                "advanced_gate_required_observations", "advanced_gate_received_observations",
            ):
                if key in report:
                    coverage.setdefault(key, report[key])

    samples: list[dict[str, str]] = []
    for field, kind in (
        ("included_features", "feature"),
        ("harmony_build_ids", "build"),
        ("harmony_evidence_ids", "evidence"),
    ):
        values = gate_report.get(field)
        if isinstance(values, list):
            samples.extend({"kind": kind, "id": str(value)} for value in values if str(value).strip())
    return {
        "coverage": coverage,
        "evidence_links": sorted(set(evidence_links)),
        "key_samples": unique_sorted(samples, limit=KEY_SAMPLE_LIMIT),
    }


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


def build_summary(
    phase: int,
    gate_report: dict[str, Any],
    source: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    machine = machine_anomalies(gate_report)
    exceptions = sorted(machine + object_list(source.get("exceptions")), key=severity_key)
    top_risks = sorted(machine + object_list(source.get("top_risks")), key=severity_key)
    critical_count = sum(
        1 for item in exceptions
        if isinstance(item, dict) and str(item.get("severity", "")).upper() == "CRITICAL"
    )
    warning_count = len(exceptions) - critical_count
    machine_pass = gate_is_clear_pass(gate_report)
    recommended_action = "REWORK" if not machine_pass or critical_count else "REVIEW"
    coverage = dict(context.get("coverage", {}))
    source_coverage = source.get("coverage")
    if isinstance(source_coverage, dict):
        for key, value in sorted(source_coverage.items()):
            coverage.setdefault(str(key), value)
    source_links = source.get("evidence_links")
    links = list(context.get("evidence_links", []))
    if isinstance(source_links, list):
        links.extend(str(item) for item in source_links if str(item).strip())
    samples = list(context.get("key_samples", []))
    if isinstance(source.get("key_samples"), list):
        samples.extend(source["key_samples"])
    return {
        "phase": phase,
        "status": "WAITING_HUMAN_REVIEW" if machine_pass else "MACHINE_GATE_FAILED",
        "coverage": coverage,
        "critical_count": critical_count,
        "warning_count": warning_count,
        "top_risks": top_risks,
        "exceptions": exceptions,
        "key_samples": unique_sorted(samples, limit=KEY_SAMPLE_LIMIT),
        "evidence_links": sorted(set(links)),
        "recommended_action": recommended_action,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--phase", required=True, type=int, choices=(1, 2, 3, 4))
    parser.add_argument("--gate-report", required=True)
    parser.add_argument("--input", help="Optional supplemental review details; machine findings remain authoritative")
    parser.add_argument("--output")
    args = parser.parse_args()

    try:
        run_dir = Path(args.run_dir).resolve()
        gate_path = resolve_run_file(run_dir, Path(args.gate_report), "controller gate report")
        gate_report = load_json_object(gate_path, "controller gate report")
        if gate_report.get("phase") != args.phase:
            raise ValueError("Controller gate report phase differs from requested summary phase")
        source = {}
        if args.input:
            source_path = resolve_run_file(run_dir, Path(args.input), "review summary input")
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
        context = phase_context(run_dir, args.phase, gate_path, gate_report)
        atomic_json(output, build_summary(args.phase, gate_report, source, context))
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
