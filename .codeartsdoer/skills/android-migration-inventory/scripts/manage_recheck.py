#!/usr/bin/env python3
"""Open or close a Phase 2 rework ticket under coverage-checker authority.

Phase 2 rework tickets share the unified 22-column rework ticket contract with
Phase 3/4 (ticket_id/problem_type/failed_verification_id/...) and mirror into
controller/rework-log.csv with the same field mapping as the later phases.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import exclusive_lock, load_json, read_csv, utc_now, validate_id, write_csv


FIELDS = [
    "ticket_id", "severity", "problem_type", "phase", "record_id", "feature_id",
    "page_id", "state_id", "env_id", "failed_verification_id", "responsible_role",
    "responsible_agent", "completion_condition", "status", "opened_by", "opened_at",
    "confirmed_by", "confirmed_at", "resolution_verification_id", "closed_by",
    "closed_at", "notes",
]
CONTROLLER_FIELDS = [
    "rework_id", "created_at", "phase", "record_id", "feature_id", "page_id",
    "state_id", "env_id", "evidence_id", "gate_rule", "reason", "assigned_to",
    "completion_condition", "status", "resolved_at", "resolution_evidence_id",
    "reviewed_by",
]
ROUTE_OWNER_FIELDS = {
    "SCOPE": "inventory_lead_id",
    "ENV": "inventory_lead_id",
    "CONFLICT": "inventory_lead_id",
    "CODE": "code_map_agent_id",
    "ASSET": "code_map_agent_id",
    "ROUTE": "code_map_agent_id",
    "ENTRY": "code_map_agent_id",
    "RULE": "business_rule_agent_id",
    "VALIDATION": "business_rule_agent_id",
    "TRANSITION": "business_rule_agent_id",
    "API": "data_dependency_agent_id",
    "DATA": "data_dependency_agent_id",
    "SDK": "data_dependency_agent_id",
    "PERMISSION": "data_dependency_agent_id",
    "NATIVE": "data_dependency_agent_id",
    "EVID": "evidence_administrator_id",
    "ID": "evidence_administrator_id",
    "HASH": "evidence_administrator_id",
    "INDEX": "evidence_administrator_id",
    "SCHEMA": "evidence_administrator_id",
}
ROLE_NAMES = {
    "inventory_lead_id": "inventory-lead",
    "code_map_agent_id": "code-map-agent",
    "business_rule_agent_id": "business-rule-agent",
    "data_dependency_agent_id": "data-dependency-agent",
    "evidence_administrator_id": "evidence-administrator",
}
RUNTIME_PROBLEM_CODES = {"STATE", "SCREENSHOT", "LAYOUT", "CLI", "STEPS"}
PROBLEM_CODES = set(ROUTE_OWNER_FIELDS) | RUNTIME_PROBLEM_CODES
RUNTIME_ROLE = "runtime-state-agent"


def derive_assignment(
    problem_type: str, ownership: dict, inventory_row: dict[str, str]
) -> tuple[str, str]:
    """Return the frozen (responsible_role, responsible_agent) routing."""
    if problem_type in RUNTIME_PROBLEM_CODES:
        assignee = inventory_row.get("responsible_agent", "")
        runtime_agents = ownership.get("runtime_state_agent_ids", [])
        if not isinstance(runtime_agents, list) or assignee not in runtime_agents:
            raise ValueError("Runtime rework has no frozen responsible runtime-state agent")
        role = RUNTIME_ROLE
    else:
        owner_field = ROUTE_OWNER_FIELDS.get(problem_type)
        if owner_field is None:
            raise ValueError(f"Unsupported problem type: {problem_type}")
        assignee = ownership.get(owner_field, "")
        role = ROLE_NAMES[owner_field]
    if not isinstance(assignee, str) or not assignee:
        raise ValueError(f"No frozen assignee is available for problem type {problem_type}")
    if assignee == ownership.get("coverage_checker_id"):
        raise ValueError("The coverage checker cannot be assigned its own rework")
    return role, assignee


def mirrored_controller_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "rework_id": row["ticket_id"],
        "created_at": row["opened_at"],
        "phase": "2",
        "record_id": row["record_id"],
        "feature_id": row["feature_id"],
        "page_id": row["page_id"],
        "state_id": row["state_id"],
        "env_id": row["env_id"],
        "evidence_id": row["failed_verification_id"],
        "gate_rule": row["problem_type"],
        "reason": row["notes"],
        "assigned_to": row["responsible_agent"],
        "completion_condition": row["completion_condition"],
        "status": "REWORK",
        "resolved_at": "",
        "resolution_evidence_id": "",
        "reviewed_by": row["opened_by"],
    }


def validate_controller_mirror(local: dict[str, str], controller: dict[str, str]) -> None:
    expected = {
        "rework_id": local.get("ticket_id", ""),
        "created_at": local.get("opened_at", ""),
        "phase": "2",
        "record_id": local.get("record_id", ""),
        "feature_id": local.get("feature_id", ""),
        "page_id": local.get("page_id", ""),
        "state_id": local.get("state_id", ""),
        "env_id": local.get("env_id", ""),
        "evidence_id": local.get("failed_verification_id", ""),
        "gate_rule": local.get("problem_type", ""),
        "reason": local.get("notes", ""),
        "assigned_to": local.get("responsible_agent", ""),
        "completion_condition": local.get("completion_condition", ""),
    }
    differences = [field for field, value in expected.items() if controller.get(field, "") != value]
    if differences:
        raise ValueError(f"Controller rework mirror differs on: {', '.join(differences)}")


def write_synced(
    rechecks_path: Path,
    recheck_rows: list[dict[str, str]],
    old_recheck_rows: list[dict[str, str]],
    controller_path: Path,
    controller_rows: list[dict[str, str]],
    old_controller_rows: list[dict[str, str]],
) -> None:
    try:
        write_csv(rechecks_path, FIELDS, recheck_rows)
        write_csv(controller_path, CONTROLLER_FIELDS, controller_rows)
    except Exception:
        # Best-effort rollback keeps the two audit ledgers aligned on ordinary write failures.
        try:
            write_csv(rechecks_path, FIELDS, old_recheck_rows)
            write_csv(controller_path, CONTROLLER_FIELDS, old_controller_rows)
        except Exception:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--action", required=True, choices=("open", "close"))
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--ticket-id", required=True)
    parser.add_argument("--record-id")
    parser.add_argument("--severity", choices=("CRITICAL", "HIGH", "MEDIUM", "LOW"))
    parser.add_argument("--problem-type")
    parser.add_argument("--reason")
    parser.add_argument("--responsible-agent")
    parser.add_argument("--completion-condition")
    parser.add_argument("--resolution-verification-id")
    args = parser.parse_args()

    workspace_input = Path(args.workspace).expanduser().absolute()
    if workspace_input.is_symlink():
        parser.error("Workspace must not be a symbolic link")
    workspace = workspace_input.resolve()
    if (workspace / "CLOSED").exists():
        parser.error("Phase 2 is CLOSED; rework tickets are read-only")
    manifest = load_json(workspace / "phase-manifest.json")
    if not isinstance(manifest, dict) or manifest.get("status") != "IN_PROGRESS":
        parser.error("Rework tickets require an IN_PROGRESS Phase 2 manifest")
    ownership = manifest.get("ownership", {})
    if not isinstance(ownership, dict):
        parser.error("Phase 2 manifest has no frozen ownership table")
    checker = ownership.get("coverage_checker_id")
    if not checker or args.reviewer != checker:
        parser.error("Only the frozen coverage checker may open or close rework tickets")
    try:
        validate_id(args.ticket_id, "Rework Ticket-ID")
    except ValueError as exc:
        parser.error(str(exc))

    if workspace.name != "phase-02-android-inventory":
        parser.error("Workspace must be the canonical phase-02-android-inventory directory")
    path = workspace / "rechecks.csv"
    scope = load_json(workspace.parent / "controller" / "scope.json")
    if (
        not isinstance(scope, dict)
        or scope.get("run_id") != manifest.get("run_id")
        or scope.get("ownership") != ownership
    ):
        parser.error("Phase 2 ownership differs from the frozen controller scope")
    controller_path = workspace.parent / "controller" / "rework-log.csv"
    if controller_path.is_symlink():
        parser.error("Controller rework log must not be a symbolic link")
    with exclusive_lock(workspace / ".locks" / "rechecks-controller-sync.lock"):
        rows = read_csv(path)
        controller_rows = read_csv(controller_path)
        old_rows = [dict(row) for row in rows]
        old_controller_rows = [dict(row) for row in controller_rows]
        matches = [row for row in rows if row.get("ticket_id") == args.ticket_id]
        controller_matches = [
            row for row in controller_rows if row.get("rework_id") == args.ticket_id
        ]
        inventory = {row["inventory_id"]: row for row in read_csv(workspace / "inventory.csv")}
        if args.action == "open":
            required = {
                "record_id": args.record_id, "severity": args.severity,
                "problem_type": args.problem_type, "reason": args.reason,
                "completion_condition": args.completion_condition,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                parser.error(f"Opening a rework ticket requires: {', '.join(missing)}")
            if matches or controller_matches:
                parser.error("Ticket-ID already exists; overwrite is prohibited")
            problem_type = str(args.problem_type).upper()
            if problem_type not in PROBLEM_CODES:
                parser.error(f"Unsupported problem type: {problem_type}")
            record = inventory.get(args.record_id or "")
            if not record:
                parser.error("Rework Record-ID is not in the formal inventory")
            try:
                responsible_role, responsible_agent = derive_assignment(
                    problem_type, ownership, record
                )
            except ValueError as exc:
                parser.error(str(exc))
            if args.responsible_agent and args.responsible_agent != responsible_agent:
                parser.error(
                    f"--responsible-agent differs from the frozen route; expected {responsible_agent}"
                )
            opened_at = utc_now()
            ticket_row = {
                "ticket_id": args.ticket_id,
                "severity": str(args.severity),
                "problem_type": problem_type,
                "phase": "2",
                "record_id": record["inventory_id"],
                "feature_id": record["feature_id"],
                "page_id": record["page_id"],
                "state_id": record["state_id"],
                "env_id": record["env_id"],
                "failed_verification_id": record["evidence_id"],
                "responsible_role": responsible_role,
                "responsible_agent": responsible_agent,
                "completion_condition": args.completion_condition,
                "status": "OPEN",
                "opened_by": args.reviewer,
                "opened_at": opened_at,
                "confirmed_by": args.reviewer,
                "confirmed_at": opened_at,
                "resolution_verification_id": "",
                "closed_by": "",
                "closed_at": "",
                "notes": args.reason,
            }
            rows.append(ticket_row)
            controller_rows.append(mirrored_controller_row(ticket_row))
        else:
            if len(matches) != 1 or len(controller_matches) != 1:
                parser.error("Ticket-ID is not uniquely mirrored")
            row = matches[0]
            controller_row = controller_matches[0]
            if row.get("status") != "OPEN" or controller_row.get("status") != "REWORK":
                parser.error("Only an OPEN rework ticket may be closed")
            if controller_row.get("reviewed_by") != checker:
                parser.error("Controller rework was not opened by the frozen coverage checker")
            record = inventory.get(row.get("record_id", ""))
            if not record:
                parser.error("Rework Record-ID is not in the formal inventory")
            try:
                responsible_role, responsible_agent = derive_assignment(
                    row.get("problem_type", ""), ownership, record
                )
                validate_controller_mirror(row, controller_row)
            except ValueError as exc:
                parser.error(str(exc))
            if row.get("responsible_role") != responsible_role:
                parser.error("Stored rework role differs from the frozen route")
            if row.get("responsible_agent") != responsible_agent:
                parser.error("Stored rework assignee differs from the frozen route")
            if args.responsible_agent and args.responsible_agent != responsible_agent:
                parser.error(
                    f"--responsible-agent differs from the frozen route; expected {responsible_agent}"
                )
            if args.problem_type and args.problem_type != row.get("problem_type"):
                parser.error("--problem-type differs from the open rework ticket")
            if args.record_id and args.record_id != row.get("record_id"):
                parser.error("--record-id differs from the open rework ticket")
            if not args.resolution_verification_id:
                parser.error("Closing requires --resolution-verification-id")
            evidence = {
                item["evidence_id"]: item for item in read_csv(workspace / "evidence-index.csv")
            }.get(args.resolution_verification_id)
            if not evidence or evidence.get("status") != "SEALED":
                parser.error("Resolution evidence must be active and SEALED")
            if args.resolution_verification_id == row.get("failed_verification_id"):
                parser.error("Resolution evidence must be a new Evidence-ID")
            if any(evidence.get(field) != row.get(field) for field in ("feature_id", "page_id", "state_id", "env_id")):
                parser.error("Resolution evidence must match the rechecked feature/page/state/environment")
            if evidence.get("captured_at", "") <= row.get("opened_at", ""):
                parser.error("Resolution evidence must be captured after the rework ticket opened")
            closed_at = utc_now()
            row.update({
                "status": "CLOSED",
                "closed_at": closed_at,
                "resolution_verification_id": args.resolution_verification_id,
                "closed_by": args.reviewer,
            })
            controller_row.update({
                "status": "CLOSED",
                "resolved_at": closed_at,
                "resolution_evidence_id": args.resolution_verification_id,
                "reviewed_by": args.reviewer,
            })
        try:
            write_synced(
                path, rows, old_rows,
                controller_path, controller_rows, old_controller_rows,
            )
        except (OSError, ValueError) as exc:
            parser.error(f"Could not synchronize Phase 2 and controller rework ledgers: {exc}")

    print(json.dumps({"ticket_id": args.ticket_id, "action": args.action, "reviewer": args.reviewer}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
