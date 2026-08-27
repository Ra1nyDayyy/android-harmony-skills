#!/usr/bin/env python3
"""Fail-closed audit for a Phase 1-4 migration delivery."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from _human_gate import require_current_human_approval
from _team_execution import load_json, safe_file, validate_order_receipts


CANONICAL = {
    2: (
        "phase-02-android-inventory/closure-report.json",
        "phase-02-android-inventory/page-gate-report.json",
        "phase-02-android-inventory/advanced-gate-report.json",
        "phase-02-android-inventory/closure-manifest.sha256",
        "phase-02-android-inventory/CLOSED",
    ),
    3: (
        "phase-03-harmony-scaffold/stage-03-gate-report.json",
        "phase-03-harmony-scaffold/stage-03-closure-manifest.sha256",
        "phase-03-harmony-scaffold/CLOSED",
        "phase-03-harmony-scaffold/route-registry.csv",
        "phase-03-harmony-scaffold/surface-registry.csv",
    ),
    4: (
        "phase-04-harmony-implementation/phase-manifest.json",
        "phase-04-harmony-implementation/page-implementation-ledger.csv",
        "phase-04-harmony-implementation/parity-map.csv",
        "phase-04-harmony-implementation/visual-elements.csv",
        "phase-04-harmony-implementation/acceptance-ledger.csv",
        "phase-04-harmony-implementation/evidence-index.csv",
        "phase-04-harmony-implementation/stage-04-gate-report.json",
        "phase-04-harmony-implementation/stage-04-closure-manifest.sha256",
        "phase-04-harmony-implementation/CLOSED",
    ),
}
NON_FINAL_VALUES = {"PASS_WITH_GAPS", "PARTIAL", "PENDING", "NOT_STARTED", "INPUT_LOCKED", "REWORK", "FAIL"}
SOURCE_MARKER = re.compile(
    r"(?:\bPLACEHOLDER\b|\bPENDING\b|\bTODO\b|\bFIXME\b|\bSTUB_ONLY\b|"
    r"\bMOCK_ONLY\b|\bFAKE_DATA\b|\bNO[_-]?OP\b|占位|后续阶段|后续注入)",
    re.IGNORECASE,
)
FORBIDDEN_REPORT_NAMES = {
    "phase2-final-report.json",
    "phase2-acceptance-report.json",
    "phase3-gate-report.json",
    "phase4-gate-report.json",
}


def walk_values(value: Any, path: str = "$") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            found.extend(walk_values(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(walk_values(child, f"{path}[{index}]"))
    elif isinstance(value, str) and value.strip().upper() in NON_FINAL_VALUES:
        found.append((path, value))
    return found


def _gmi_run(run_dir: Path) -> bool:
    """gmi 模式判别（与 validate_gate._gmi_run 一致）：
    phase-02-android-inventory/phase-manifest.json 存在 generator==gmi，
    或 gmi/phase-2-closure.json 存在。gmi run 的 Phase 2 由 gmi 自闭合，
    没有 controller Phase 2 工单与 controller 签发的 Phase 1/2 gate 快照。"""
    p2 = run_dir / "phase-02-android-inventory"
    if (p2 / "gmi" / "phase-2-closure.json").is_file():
        return True
    try:
        manifest = json.loads((p2 / "phase-manifest.json").read_text(encoding="utf-8"))
        return str(manifest.get("generator", "")).startswith("gmi")
    except (ValueError, OSError):
        return False


def active_orders(run_dir: Path, phase: int) -> list[Path]:
    registry = run_dir / "controller" / "work-order-registry.csv"
    if not registry.is_file():
        return []
    with registry.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    paths: list[Path] = []
    for row in rows:
        if row.get("phase") == str(phase) and row.get("status", "").upper() != "SUPERSEDED":
            try:
                paths.append(safe_file(run_dir, row.get("relative_path", ""), f"Phase {phase} work order"))
            except ValueError:
                continue
    return paths


def feature_orders(run_dir: Path) -> list[Path]:
    result: list[Path] = []
    phase_dir = run_dir / "phase-04-harmony-implementation"
    registry_names = (
        ("page-work-order-registry.csv", "page work order"),
        ("capability-work-order-registry.csv", "capability work order"),
    ) if (phase_dir / "page-work-order-registry.csv").is_file() else (
        ("feature-work-order-registry.csv", "feature work order"),
    )
    for registry_name, label in registry_names:
        registry = phase_dir / registry_name
        if not registry.is_file():
            continue
        with registry.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for row in rows:
            if row.get("status", "").upper() not in {"SUPERSEDED", "CANCELLED"}:
                try:
                    result.append(safe_file(
                        run_dir, f"phase-04-harmony-implementation/{row.get('relative_path', '')}", label
                    ))
                except ValueError:
                    pass
    return result


def human_approval_errors(
    run_dir: Path,
    gate_reports: dict[int, Path | tuple[Path, str]],
) -> list[str]:
    errors: list[str] = []
    for phase, value in sorted(gate_reports.items()):
        if isinstance(value, tuple):
            gate_path, bound_relative = value
        else:
            gate_path, bound_relative = value, None
        try:
            require_current_human_approval(
                run_dir,
                phase,
                gate_path,
                bound_gate_report_relative_path=bound_relative,
            )
        except ValueError as exc:
            errors.append(f"Phase {phase} human approval is not current and valid: {exc}")
    return errors


def delivery_gate_reports(
    run_dir: Path,
    through_phase: int,
) -> tuple[dict[int, Path | tuple[Path, str]], list[str]]:
    reports: dict[int, Path | tuple[Path, str]] = {}
    errors: list[str] = []
    downstream = {
        1: (2, "controller_gate1_snapshot_relative_path"),
        2: (3, "phase2_gate_snapshot_relative_path"),
        3: (4, "controller_gate3_snapshot_relative_path"),
    }
    for phase in range(1, through_phase):
        next_phase, field = downstream[phase]
        # gmi run：Phase 2 由 gmi 自闭合，controller 不签发 Phase 1/2 工单，
        # 因此没有下游工单内嵌的 gate 快照可审计（Phase 3/4 仍严格审计）。
        if _gmi_run(run_dir) and phase in (1, 2):
            continue
        orders = active_orders(run_dir, next_phase)
        if len(orders) != 1:
            errors.append(f"Cannot locate the unique Phase {phase} gate snapshot for human approval audit")
            continue
        try:
            order = load_json(orders[0])
            relative = str(order.get(field, ""))
            path = safe_file(run_dir, relative, f"Phase {phase} gate snapshot")
            reports[phase] = (path, "controller/gate-report.json")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"Cannot inspect Phase {phase} gate snapshot: {exc}")
    reports[through_phase] = run_dir / "controller" / "gate-report.json"
    return reports, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--through-phase", type=int, choices=(1, 2, 3, 4), default=4)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    errors: list[str] = []
    checks: list[dict[str, Any]] = []
    if not run_dir.is_dir():
        parser.error("Migration run does not exist")

    validator = Path(__file__).with_name("validate_gate.py")
    for phase in range(1, args.through_phase + 1):
        result = subprocess.run(
            [sys.executable, str(validator), "--run-dir", str(run_dir), "--phase", str(phase)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError:
            report = {}
        passed = result.returncode == 0 and report.get("verdict") == "PASS" and not report.get("errors")
        checks.append({"phase": phase, "validator_pass": passed})
        if not passed:
            detail = report.get("errors") or result.stderr.strip() or result.stdout.strip()
            errors.append(f"Controller Gate {phase} did not independently revalidate: {detail}")

    gate_reports, gate_report_errors = delivery_gate_reports(run_dir, args.through_phase)
    errors.extend(gate_report_errors)
    errors.extend(human_approval_errors(run_dir, gate_reports))

    ledger = run_dir / "controller" / "task-ledger.csv"
    try:
        with ledger.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        for phase in range(1, args.through_phase + 1):
            phase_rows = [row for row in rows if row.get("phase") == str(phase)]
            if len(phase_rows) != 1 or phase_rows[0].get("status") != "PASS":
                errors.append(f"Controller task ledger does not contain exactly one Phase {phase} PASS")
    except OSError as exc:
        errors.append(f"Cannot read controller task ledger: {exc}")

    gmi_mode = _gmi_run(run_dir)
    for phase in range(2, args.through_phase + 1):
        for relative in CANONICAL[phase]:
            if not (run_dir / relative).is_file():
                errors.append(f"Missing canonical Phase {phase} artifact: {relative}")
        # gmi run：Phase 2 无 controller 工单（gmi 唯一路径契约）；
        # Phase 3/4 的工单与回执仍严格校验。
        if gmi_mode and phase == 2:
            continue
        orders = active_orders(run_dir, phase)
        if len(orders) != 1:
            errors.append(f"Phase {phase} must have exactly one active controller work order")
        else:
            errors.extend(validate_order_receipts(run_dir, orders[0]))

    for root_name in (
        "controller", "phase-02-android-inventory", "phase-03-harmony-scaffold",
        "phase-04-harmony-implementation",
    ):
        root = run_dir / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*.json"):
            if path.name in FORBIDDEN_REPORT_NAMES:
                errors.append(f"Forbidden substitute gate report exists: {path.relative_to(run_dir).as_posix()}")
            try:
                if path.stat().st_size <= 5 * 1024 * 1024 and "PASS_WITH_GAPS" in path.read_text(encoding="utf-8"):
                    errors.append(f"Forbidden PASS_WITH_GAPS appears in {path.relative_to(run_dir).as_posix()}")
            except (OSError, UnicodeDecodeError):
                continue

    if args.through_phase >= 2:
        try:
            closure = load_json(run_dir / "phase-02-android-inventory" / "closure-report.json")
            if (
                closure.get("final_verdict") != "PASS"
                or closure.get("evidence_chain_closed") is not True
                or closure.get("page_gate_verdict") != "PASS"
                or closure.get("advanced_gate_verdict") != "PASS"
            ):
                errors.append("Phase 2 closure is not a complete deterministic PASS")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"Cannot inspect Phase 2 closure: {exc}")

    for phase, relative in ((3, "phase-03-harmony-scaffold/stage-03-gate-report.json"), (4, "phase-04-harmony-implementation/stage-04-gate-report.json")):
        if args.through_phase < phase:
            continue
        try:
            report = load_json(run_dir / relative)
            if report.get("verdict") != "PASS" or (phase == 4 and report.get("final_verdict") != "PASS"):
                errors.append(f"Canonical Phase {phase} report is not an exact PASS")
            for value_path, value in walk_values(report):
                errors.append(f"Canonical Phase {phase} report contains unfinished value {value!r} at {value_path}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"Cannot inspect canonical Phase {phase} report: {exc}")

    if args.through_phase >= 4:
        feature_paths = feature_orders(run_dir)
        if not feature_paths:
            errors.append("Phase 4 has no active page/capability work orders")
        for order_path in feature_paths:
            errors.extend(validate_order_receipts(run_dir, order_path))
        source_root = run_dir / "phase-04-harmony-implementation" / "harmony-project"
        for path in source_root.rglob("*") if source_root.is_dir() else []:
            lowered = {part.lower() for part in path.parts}
            if not path.is_file() or path.suffix.lower() not in {".ets", ".ts", ".js"}:
                continue
            if lowered & {"ohostest", "test", "tests", "mock", "mocks", "build"}:
                continue
            try:
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if SOURCE_MARKER.search(line):
                        errors.append(f"Production source contains unfinished marker: {path.relative_to(run_dir).as_posix()}:{number}")
                        if len(errors) >= 200:
                            break
            except UnicodeDecodeError:
                errors.append(f"Production source is not UTF-8 text: {path.relative_to(run_dir).as_posix()}")
            if len(errors) >= 200:
                break

    report = {
        "run_id": run_dir.name,
        "through_phase": args.through_phase,
        "verdict": "PASS" if not errors else "FAIL",
        "checks": checks,
        "errors": errors,
    }
    # ASCII escaping keeps diagnostics printable on Windows consoles whose active
    # code page cannot represent a damaged legacy path from an older delivery.
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
