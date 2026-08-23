#!/usr/bin/env python3
"""Open or close a Phase 2 recheck under coverage-checker authority."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _common import exclusive_lock, load_json, read_csv, utc_now, validate_id, write_csv


FIELDS = [
    "rework_id", "opened_at", "inventory_id", "feature_id", "page_id", "state_id", "env_id",
    "evidence_id", "severity", "problem_code", "reason", "assigned_to", "completion_condition",
    "status", "resolved_at", "resolution_evidence_id", "closed_by",
]
CONTROLLER_FIELDS = [
    "rework_id", "created_at", "phase", "record_id", "feature_id", "page_id", "state_id",
    "env_id", "evidence_id", "gate_rule", "reason", "assigned_to", "completion_condition",
    "status", "resolved_at", "resolution_evidence_id", "reviewed_by",
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
RUNTIME_PROBLEM_CODES = {"STATE", "SCREENSHOT", "LAYOUT", "CLI", "STEPS"}
PROBLEM_CODES = set(ROUTE_OWNER_FIELDS) | RUNTIME_PROBLEM_CODES


def derive_assignee(problem_code: str, ownership: dict, inventory_row: dict[str, str]) -> str:
    if problem_code in RUNTIME_PROBLEM_CODES:
        assignee = inventory_row.get("responsible_agent", "")
        runtime_agents = ownership.get("runtime_state_agent_ids", [])
        if not isinstance(runtime_agents, list) or assignee not in runtime_agents:
            raise ValueError("Runtime rework has no frozen responsible runtime-state agent")
    else:
        owner_field = ROUTE_OWNER_FIELDS.get(problem_code)
        if owner_field is None:
            raise ValueError(f"Unsupported problem code: {problem_code}")
        assignee = ownership.get(owner_field, "")
    if not isinstance(assignee, str) or not assignee:
        raise ValueError(f"No frozen assignee is available for problem code {problem_code}")
    if assignee == ownership.get("coverage_checker_id"):
        raise ValueError("The coverage checker cannot be assigned its own rework")
    return assignee


def mirrored_controller_row(
    row: dict[str, str], opened_at: str, reviewer: str
) -> dict[str, str]:
    return {
        "rework_id": row["rework_id"],
        "created_at": opened_at,
        "phase": "2",
        "record_id": row["inventory_id"],
        "feature_id": row["feature_id"],
        "page_id": row["page_id"],
        "state_id": row["state_id"],
        "env_id": row["env_id"],
        "evidence_id": row["evidence_id"],
        "gate_rule": row["problem_code"],
        "reason": row["reason"],
        "assigned_to": row["assigned_to"],
        "completion_condition": row["completion_condition"],
        "status": "REWORK",
        "resolved_at": "",
        "resolution_evidence_id": "",
        "reviewed_by": reviewer,
    }


def validate_controller_mirror(local: dict[str, str], controller: dict[str, str]) -> None:
    expected = {
        "rework_id": local.get("rework_id", ""),
        "created_at": local.get("opened_at", ""),
        "phase": "2",
        "record_id": local.get("inventory_id", ""),
        "feature_id": local.get("feature_id", ""),
        "page_id": local.get("page_id", ""),
        "state_id": local.get("state_id", ""),
        "env_id": local.get("env_id", ""),
        "evidence_id": local.get("evidence_id", ""),
        "gate_rule": local.get("problem_code", ""),
        "reason": local.get("reason", ""),
        "assigned_to": local.get("assigned_to", ""),
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
    parser.add_argument("--rework-id", required=True)
    parser.add_argument("--inventory-id")
    parser.add_argument("--severity", choices=("CRITICAL", "HIGH", "MEDIUM", "LOW"))
    parser.add_argument("--problem-code")
    parser.add_argument("--reason")
    parser.add_argument("--assigned-to")
    parser.add_argument("--completion-condition")
    parser.add_argument("--resolution-evidence-id")
    args = parser.parse_args()

    workspace_input = Path(args.workspace).expanduser().absolute()
    if workspace_input.is_symlink():
        parser.error("Workspace must not be a symbolic link")
    workspace = workspace_input.resolve()
    if (workspace / "CLOSED").exists():
        parser.error("Phase 2 is CLOSED; rechecks are read-only")
    manifest = load_json(workspace / "phase-manifest.json")
    if not isinstance(manifest, dict) or manifest.get("status") != "IN_PROGRESS":
        parser.error("Rechecks require an IN_PROGRESS Phase 2 manifest")
    ownership = manifest.get("ownership", {})
    if not isinstance(ownership, dict):
        parser.error("Phase 2 manifest has no frozen ownership table")
    checker = ownership.get("coverage_checker_id")
    if not checker or args.reviewer != checker:
        parser.error("Only the frozen coverage checker may open or close rechecks")
    try:
        validate_id(args.rework_id, "Rework-ID")
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
        matches = [row for row in rows if row.get("rework_id") == args.rework_id]
        controller_matches = [row for row in controller_rows if row.get("rework_id") == args.rework_id]
        inventory = {row["inventory_id"]: row for row in read_csv(workspace / "inventory.csv")}
        if args.action == "open":
            required = {
                "inventory_id": args.inventory_id, "severity": args.severity,
                "problem_code": args.problem_code, "reason": args.reason,
                "completion_condition": args.completion_condition,
            }
            missing = [name for name, value in required.items() if not value]
            if missing:
                parser.error(f"Opening a recheck requires: {', '.join(missing)}")
            if matches or controller_matches:
                parser.error("Rework-ID already exists; overwrite is prohibited")
            if args.problem_code not in PROBLEM_CODES:
                parser.error(f"Unsupported problem code: {args.problem_code}")
            record = inventory.get(args.inventory_id or "")
            if not record:
                parser.error("Recheck Inventory-ID is not in the formal inventory")
            try:
                assigned_to = derive_assignee(str(args.problem_code), ownership, record)
            except ValueError as exc:
                parser.error(str(exc))
            if args.assigned_to and args.assigned_to != assigned_to:
                parser.error(
                    f"--assigned-to differs from the frozen route; expected {assigned_to}"
                )
            opened_at = utc_now()
            recheck_row = {
                "rework_id": args.rework_id,
                "opened_at": opened_at,
                "inventory_id": record["inventory_id"],
                "feature_id": record["feature_id"],
                "page_id": record["page_id"],
                "state_id": record["state_id"],
                "env_id": record["env_id"],
                "evidence_id": record["evidence_id"],
                "severity": args.severity,
                "problem_code": args.problem_code,
                "reason": args.reason,
                "assigned_to": assigned_to,
                "completion_condition": args.completion_condition,
                "status": "OPEN",
                "resolved_at": "",
                "resolution_evidence_id": "",
                "closed_by": "",
            }
            rows.append(recheck_row)
            controller_rows.append(mirrored_controller_row(recheck_row, opened_at, args.reviewer))
        else:
            if len(matches) != 1 or len(controller_matches) != 1:
                parser.error("Rework-ID is not uniquely open")
            row = matches[0]
            controller_row = controller_matches[0]
            if row.get("status") != "OPEN" or controller_row.get("status") != "REWORK":
                parser.error("Only an OPEN recheck may be closed")
            if controller_row.get("reviewed_by") != checker:
                parser.error("Controller rework was not opened by the frozen coverage checker")
            record = inventory.get(row.get("inventory_id", ""))
            if not record:
                parser.error("Recheck Inventory-ID is not in the formal inventory")
            try:
                assigned_to = derive_assignee(row.get("problem_code", ""), ownership, record)
                validate_controller_mirror(row, controller_row)
            except ValueError as exc:
                parser.error(str(exc))
            if row.get("assigned_to") != assigned_to:
                parser.error("Stored rework assignee differs from the frozen route")
            if args.assigned_to and args.assigned_to != assigned_to:
                parser.error(
                    f"--assigned-to differs from the frozen route; expected {assigned_to}"
                )
            if args.problem_code and args.problem_code != row.get("problem_code"):
                parser.error("--problem-code differs from the open recheck")
            if args.inventory_id and args.inventory_id != row.get("inventory_id"):
                parser.error("--inventory-id differs from the open recheck")
            if not args.resolution_evidence_id:
                parser.error("Closing requires --resolution-evidence-id")
            evidence = {
                item["evidence_id"]: item for item in read_csv(workspace / "evidence-index.csv")
            }.get(args.resolution_evidence_id)
            if not evidence or evidence.get("status") != "SEALED":
                parser.error("Resolution evidence must be active and SEALED")
            if args.resolution_evidence_id == row.get("evidence_id"):
                parser.error("Resolution evidence must be a new Evidence-ID")
            if any(evidence.get(field) != row.get(field) for field in ("feature_id", "page_id", "state_id", "env_id")):
                parser.error("Resolution evidence must match the rechecked feature/page/state/environment")
            if evidence.get("captured_at", "") <= row.get("opened_at", ""):
                parser.error("Resolution evidence must be captured after the recheck opened")
            resolved_at = utc_now()
            row.update({
                "status": "CLOSED",
                "resolved_at": resolved_at,
                "resolution_evidence_id": args.resolution_evidence_id,
                "closed_by": args.reviewer,
            })
            controller_row.update({
                "status": "CLOSED",
                "resolved_at": resolved_at,
                "resolution_evidence_id": args.resolution_evidence_id,
                "reviewed_by": args.reviewer,
            })
        try:
            write_synced(
                path, rows, old_rows,
                controller_path, controller_rows, old_controller_rows,
            )
        except (OSError, ValueError) as exc:
            parser.error(f"Could not synchronize Phase 2 and controller rework ledgers: {exc}")

    print(json.dumps({"rework_id": args.rework_id, "action": args.action, "reviewer": args.reviewer}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
