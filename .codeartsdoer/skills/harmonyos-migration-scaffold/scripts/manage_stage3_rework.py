#!/usr/bin/env python3
"""Open or close Phase 3 rework with frozen routing and controller mirroring."""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import os
from pathlib import Path

from _common import load_json, read_csv, utc_now, validate_id, write_csv


FIELDS = [
    "ticket_id", "severity", "problem_type", "source_or_mapping_id",
    "failed_verification_id", "responsible_role", "responsible_agent", "confirmed_by",
    "status", "opened_by", "opened_at", "correction_verification_id", "closed_by",
    "closed_at", "notes",
]
CONTROLLER_FIELDS = [
    "rework_id", "created_at", "phase", "record_id", "feature_id", "page_id",
    "state_id", "env_id", "evidence_id", "gate_rule", "reason", "assigned_to",
    "completion_condition", "status", "resolved_at", "resolution_evidence_id",
    "reviewed_by",
]
ROUTES = {
    "ARCHITECTURE": ("architecture-lead", "architecture_lead_id"),
    "PLACEMENT": ("architecture-lead", "architecture_lead_id"),
    "ASSET": ("architecture-lead", "architecture_lead_id"),
    "DEPENDENCY": ("architecture-lead", "architecture_lead_id"),
    "INPUT": ("architecture-lead", "architecture_lead_id"),
    "TOOLCHAIN": ("toolchain-agent", "toolchain_agent_id"),
    "BUILD": ("toolchain-agent", "toolchain_agent_id"),
    "DEVICE": ("toolchain-agent", "toolchain_agent_id"),
    "BUNDLE": ("toolchain-agent", "toolchain_agent_id"),
    "SIGNING": ("toolchain-agent", "toolchain_agent_id"),
    "INSTALL": ("toolchain-agent", "toolchain_agent_id"),
    "LAUNCH": ("toolchain-agent", "toolchain_agent_id"),
    "ARTIFACT": ("toolchain-agent", "toolchain_agent_id"),
    "SCREENSHOT": ("toolchain-agent", "toolchain_agent_id"),
    "NAVIGATION": ("navigation-agent", "navigation_agent_id"),
    "ROUTE": ("navigation-agent", "navigation_agent_id"),
    "SURFACE": ("navigation-agent", "navigation_agent_id"),
    "MAPPING": ("navigation-agent", "navigation_agent_id"),
    "SMOKE": ("navigation-agent", "navigation_agent_id"),
    "PUBLIC_UI": ("public-ui-agent", "public_ui_agent_id"),
    "RESPONSIVE": ("public-ui-agent", "public_ui_agent_id"),
    "THEME": ("public-ui-agent", "public_ui_agent_id"),
    "CAPABILITY": ("capability-contract-agent", "capability_contract_agent_id"),
    "CONTRACT": ("capability-contract-agent", "capability_contract_agent_id"),
}


@contextlib.contextmanager
def exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError(f"Another Phase 3 rework update is in progress: {path}") from exc
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle).fieldnames or [])


def route(problem_type: str, ownership: dict[str, str]) -> tuple[str, str]:
    definition = ROUTES.get(problem_type)
    if not definition:
        raise ValueError(f"Unsupported Phase 3 problem type: {problem_type}")
    role, owner_key = definition
    actor = ownership.get(owner_key, "")
    if not isinstance(actor, str) or not actor:
        raise ValueError(f"Frozen ownership has no {owner_key}")
    if actor == ownership.get("architecture_acceptance_agent_id"):
        raise ValueError("The acceptance agent cannot be assigned its own rework")
    return role, actor


def load_hver(workspace: Path, verification_id: str) -> dict:
    validate_id(verification_id, "HVER-ID")
    metadata = load_json(workspace / "verification" / verification_id / "metadata.json")
    if not isinstance(metadata, dict) or metadata.get("verification_id") != verification_id:
        raise ValueError(f"Invalid HVER metadata: {verification_id}")
    return metadata


def controller_row(
    local: dict[str, str], reason: str, completion_condition: str
) -> dict[str, str]:
    return {
        "rework_id": local["ticket_id"],
        "created_at": local["opened_at"],
        "phase": "3",
        "record_id": local["source_or_mapping_id"],
        "feature_id": "",
        "page_id": "",
        "state_id": "",
        "env_id": "",
        "evidence_id": local["failed_verification_id"],
        "gate_rule": local["problem_type"],
        "reason": reason,
        "assigned_to": local["responsible_agent"],
        "completion_condition": completion_condition,
        "status": "REWORK",
        "resolved_at": "",
        "resolution_evidence_id": "",
        "reviewed_by": local["opened_by"],
    }


def validate_mirror(local: dict[str, str], controller: dict[str, str]) -> None:
    expected = {
        "rework_id": local["ticket_id"],
        "created_at": local["opened_at"],
        "phase": "3",
        "record_id": local["source_or_mapping_id"],
        "evidence_id": local["failed_verification_id"],
        "gate_rule": local["problem_type"],
        "assigned_to": local["responsible_agent"],
    }
    differences = [
        key for key, value in expected.items() if controller.get(key, "") != value
    ]
    if differences:
        raise ValueError(f"Controller Phase 3 rework mirror differs on: {differences}")


def write_synced(
    local_path: Path,
    local_rows: list[dict[str, str]],
    old_local: list[dict[str, str]],
    controller_path: Path,
    controller_rows: list[dict[str, str]],
    old_controller: list[dict[str, str]],
) -> None:
    try:
        write_csv(local_path, FIELDS, local_rows)
        write_csv(controller_path, CONTROLLER_FIELDS, controller_rows)
    except Exception:
        try:
            write_csv(local_path, FIELDS, old_local)
            write_csv(controller_path, CONTROLLER_FIELDS, old_controller)
        except Exception:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--action", required=True, choices=("open", "close"))
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--problem-type")
    parser.add_argument("--source-or-mapping-id")
    parser.add_argument("--failed-verification-id")
    parser.add_argument("--severity", choices=("CRITICAL", "HIGH", "MEDIUM", "LOW"))
    parser.add_argument("--reason")
    parser.add_argument("--completion-condition")
    parser.add_argument("--confirmed-by")
    parser.add_argument("--responsible-agent")
    parser.add_argument("--correction-verification-id")
    args = parser.parse_args()

    workspace_input = Path(args.workspace).expanduser().absolute()
    if workspace_input.is_symlink():
        parser.error("Workspace must not be a symbolic link")
    workspace = workspace_input.resolve()
    if workspace.name != "phase-03-harmony-scaffold":
        parser.error("Workspace must be the canonical phase-03-harmony-scaffold directory")
    if (workspace / "CLOSED").exists():
        parser.error("Phase 3 is CLOSED; rework history is read-only")
    try:
        manifest = load_json(workspace / "phase-manifest.json")
    except ValueError as exc:
        parser.error(str(exc))
    ownership = manifest.get("ownership") if isinstance(manifest, dict) else None
    if not isinstance(ownership, dict):
        parser.error("Phase 3 manifest has no frozen ownership")
    acceptance = ownership.get("architecture_acceptance_agent_id")
    lead = ownership.get("architecture_lead_id")
    if args.reviewer != acceptance:
        parser.error("Only the frozen architecture acceptance agent may open or close rework")
    try:
        validate_id(args.ticket_id, "Rework Ticket-ID")
    except ValueError as exc:
        parser.error(str(exc))

    local_path = workspace / "rework-tickets.csv"
    controller_path = workspace.parent / "controller" / "rework-log.csv"
    if local_path.is_symlink() or controller_path.is_symlink():
        parser.error("Rework ledgers must not be symbolic links")
    try:
        if read_header(local_path) != FIELDS:
            raise ValueError("Phase 3 rework-tickets.csv header differs from the contract")
        if read_header(controller_path) != CONTROLLER_FIELDS:
            raise ValueError("Controller rework-log.csv header differs from the contract")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    lock_path = workspace / ".locks" / "stage3-rework-controller-sync.lock"
    try:
        with exclusive_lock(lock_path):
            local_rows = read_csv(local_path)
            controller_rows = read_csv(controller_path)
            old_local = [dict(row) for row in local_rows]
            old_controller = [dict(row) for row in controller_rows]
            local_matches = [
                row for row in local_rows if row.get("ticket_id") == args.ticket_id
            ]
            controller_matches = [
                row for row in controller_rows if row.get("rework_id") == args.ticket_id
            ]
            if args.action == "open":
                required = {
                    "problem_type": args.problem_type,
                    "source_or_mapping_id": args.source_or_mapping_id,
                    "failed_verification_id": args.failed_verification_id,
                    "severity": args.severity,
                    "reason": args.reason,
                    "completion_condition": args.completion_condition,
                    "confirmed_by": args.confirmed_by,
                }
                missing = [key for key, value in required.items() if not value]
                if missing:
                    parser.error(f"Opening Phase 3 rework requires: {', '.join(missing)}")
                if local_matches or controller_matches:
                    parser.error("Ticket-ID already exists; overwrite is prohibited")
                problem_type = str(args.problem_type).upper()
                try:
                    source_id = validate_id(
                        str(args.source_or_mapping_id), "source_or_mapping_id"
                    )
                    failed_id = validate_id(
                        str(args.failed_verification_id), "failed HVER-ID"
                    )
                    failed = load_hver(workspace, failed_id)
                    responsible_role, responsible_agent = route(problem_type, ownership)
                except ValueError as exc:
                    parser.error(str(exc))
                failed_marker = workspace / "verification" / failed_id / "COMMITTED"
                if (
                    failed.get("status") != "FAIL"
                    or failed.get("run_id") != manifest.get("run_id")
                    or failed.get("executed_by") != ownership.get("toolchain_agent_id")
                    or not failed_marker.is_file()
                    or not failed_marker.read_text(encoding="utf-8").strip().startswith(
                        f"{failed_id} FAIL "
                    )
                ):
                    parser.error(
                        "Failed HVER must be a sealed FAIL from this run by the frozen toolchain agent"
                    )
                if args.confirmed_by != lead:
                    parser.error("--confirmed-by must equal the frozen architecture lead")
                if args.responsible_agent and args.responsible_agent != responsible_agent:
                    parser.error(
                        f"--responsible-agent differs from frozen routing; expected {responsible_agent}"
                    )
                opened_at = utc_now()
                local = {
                    "ticket_id": args.ticket_id,
                    "severity": str(args.severity),
                    "problem_type": problem_type,
                    "source_or_mapping_id": source_id,
                    "failed_verification_id": failed_id,
                    "responsible_role": responsible_role,
                    "responsible_agent": responsible_agent,
                    "confirmed_by": str(lead),
                    "status": "OPEN",
                    "opened_by": str(acceptance),
                    "opened_at": opened_at,
                    "correction_verification_id": "",
                    "closed_by": "",
                    "closed_at": "",
                    "notes": str(args.reason),
                }
                local_rows.append(local)
                controller_rows.append(
                    controller_row(local, str(args.reason), str(args.completion_condition))
                )
            else:
                if len(local_matches) != 1 or len(controller_matches) != 1:
                    parser.error("Ticket-ID is not uniquely mirrored")
                local = local_matches[0]
                controller = controller_matches[0]
                if local.get("status") != "OPEN" or controller.get("status") != "REWORK":
                    parser.error("Only an OPEN/REWORK ticket may be closed")
                try:
                    responsible_role, responsible_agent = route(
                        local.get("problem_type", ""), ownership
                    )
                    validate_mirror(local, controller)
                except ValueError as exc:
                    parser.error(str(exc))
                if (
                    local.get("responsible_role") != responsible_role
                    or local.get("responsible_agent") != responsible_agent
                    or local.get("confirmed_by") != lead
                    or local.get("opened_by") != acceptance
                ):
                    parser.error("Stored ticket authority or frozen routing differs")
                correction_id = str(args.correction_verification_id or "")
                if not correction_id:
                    parser.error("Closing Phase 3 rework requires --correction-verification-id")
                if correction_id == local.get("failed_verification_id"):
                    parser.error("Correction evidence must use a new HVER-ID")
                try:
                    correction = load_hver(workspace, correction_id)
                except ValueError as exc:
                    parser.error(str(exc))
                committed = workspace / "verification" / correction_id / "COMMITTED"
                if (
                    correction.get("status") != "PASS"
                    or correction.get("executed_by") != ownership.get("toolchain_agent_id")
                    or not committed.is_file()
                    or not committed.read_text(encoding="utf-8").strip().startswith(
                        f"{correction_id} PASS "
                    )
                    or correction.get("created_at", "") < local.get("opened_at", "")
                ):
                    parser.error("Correction HVER is not a newer sealed PASS by the frozen toolchain agent")
                closed_at = utc_now()
                local.update(
                    {
                        "status": "CLOSED",
                        "correction_verification_id": correction_id,
                        "closed_by": str(acceptance),
                        "closed_at": closed_at,
                    }
                )
                controller.update(
                    {
                        "status": "CLOSED",
                        "resolved_at": closed_at,
                        "resolution_evidence_id": correction_id,
                        "reviewed_by": str(acceptance),
                    }
                )
            write_synced(
                local_path, local_rows, old_local,
                controller_path, controller_rows, old_controller,
            )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {"ticket_id": args.ticket_id, "action": args.action, "reviewer": args.reviewer}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
