#!/usr/bin/env python3
"""Open or close Phase 4 rework with frozen routing and controller mirroring."""

from __future__ import annotations

import argparse
import json
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

from _common import (
    csv_fieldnames,
    exclusive_lock,
    load_json,
    read_csv,
    safe_relative_path,
    sha256_file,
    split_multi,
    utc_now,
    validate_actor,
    validate_id,
    write_csv,
)


SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSETS = SKILL_ROOT / "assets"
PHASE_NAME = "phase-04-harmony-implementation"
LOCAL_FIELDS = [
    "ticket_id", "severity", "problem_type", "phase", "record_id", "feature_id",
    "page_id", "state_id", "env_id", "failed_verification_id",
    "responsible_role", "responsible_agent", "completion_condition", "status",
    "opened_by", "opened_at", "confirmed_by", "confirmed_at",
    "resolution_verification_id", "closed_by", "closed_at", "notes",
]
CONTROLLER_FIELDS = [
    "rework_id", "created_at", "phase", "record_id", "feature_id", "page_id",
    "state_id", "env_id", "evidence_id", "gate_rule", "reason", "assigned_to",
    "completion_condition", "status", "resolved_at", "resolution_evidence_id",
    "reviewed_by",
]
ROUTES = {
    "INTEGRATION": ("page-owner", "feature_owner_id"),
    "SOURCE": ("page-owner", "feature_owner_id"),
    "UI": ("page-ui-agent", "ui_agent_id"),
    "VISUAL": ("page-ui-agent", "ui_agent_id"),
    "INTERACTION": ("page-ui-agent", "ui_agent_id"),
    "BUSINESS": ("page-owner", "business_data_agent_id"),
    "DATA": ("page-owner", "business_data_agent_id"),
    "STATE": ("page-owner", "business_data_agent_id"),
    "NATIVE": ("shared-capability-specialist", "native_capability_agent_id"),
    "CAPABILITY": ("shared-capability-specialist", "native_capability_agent_id"),
    "PERMISSION": ("shared-capability-specialist", "native_capability_agent_id"),
    "ASSET": ("visual-asset-agent", "visual_asset_agent_id"),
    "PROVENANCE": ("visual-asset-agent", "visual_asset_agent_id"),
    "CONVERSION": ("visual-asset-agent", "visual_asset_agent_id"),
    "BUILD": ("verification-executor", "verification_executor_id"),
    "INSTALL": ("verification-executor", "verification_executor_id"),
    "DEVICE": ("verification-executor", "verification_executor_id"),
    "ENVIRONMENT": ("verification-executor", "verification_executor_id"),
    "SCREENSHOT": ("verification-executor", "verification_executor_id"),
    "UI_TREE": ("verification-executor", "verification_executor_id"),
    "ASSERTION": ("verification-executor", "verification_executor_id"),
    "EVIDENCE": ("verification-executor", "verification_executor_id"),
}


def canonical_workspace(value: str) -> Path:
    raw = Path(value).expanduser().absolute()
    if raw.is_symlink():
        raise ValueError("Workspace must not be a symbolic link")
    workspace = raw.resolve(strict=True)
    if workspace.name != PHASE_NAME:
        raise ValueError(f"Workspace must be the canonical {PHASE_NAME} directory")
    return workspace


def indexed(rows: list[dict[str, str]], key: str, label: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        identifier = str(row.get(key, ""))
        if not identifier or identifier in result:
            raise ValueError(f"Missing or duplicate {label} {key}: {identifier!r}")
        result[identifier] = row
    return result


def parse_time(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"Invalid {label}: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed


def verify_sealed_package(
    directory: Path,
    package_id: str,
    status: str,
) -> dict[str, Any]:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError(f"Missing sealed package: {directory}")
    manifest = directory / "manifest.sha256"
    committed = directory / "COMMITTED"
    metadata_path = directory / "metadata.json"
    if not manifest.is_file() or not committed.is_file() or not metadata_path.is_file():
        raise ValueError(f"Sealed package lacks manifest/COMMITTED/metadata: {package_id}")
    entries: dict[str, str] = {}
    lines = manifest.read_text(encoding="utf-8").splitlines()
    if lines != sorted(lines, key=lambda line: line.split("  ", 1)[-1]):
        raise ValueError(f"Sealed manifest is not sorted: {package_id}")
    for number, line in enumerate(lines, start=1):
        if "  " not in line:
            raise ValueError(f"Malformed sealed manifest line {number}: {package_id}")
        digest, relative = line.split("  ", 1)
        path = safe_relative_path(directory, relative, f"sealed artifact {package_id}")
        if (
            len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or relative in entries
            or relative in {"manifest.sha256", "COMMITTED"}
            or not path.is_file()
            or sha256_file(path) != digest
        ):
            raise ValueError(f"Invalid sealed manifest entry: {package_id}:{relative}")
        entries[relative] = digest
    actual: set[str] = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in sealed package: {path}")
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
    if actual != {*entries, "manifest.sha256", "COMMITTED"}:
        raise ValueError(f"Sealed manifest does not exactly cover package: {package_id}")
    marker = committed.read_text(encoding="utf-8").strip()
    if (
        not marker.startswith(f"{package_id} {status} ")
        or f"manifest_sha256={sha256_file(manifest)}" not in marker
    ):
        raise ValueError(f"COMMITTED does not bind sealed package: {package_id}")
    for path in (directory, *directory.rglob("*")):
        if path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"Sealed package is writable: {path}")
    metadata = load_json(metadata_path)
    if not isinstance(metadata, dict) or metadata.get("status") != status:
        raise ValueError(f"Sealed package metadata status differs: {package_id}")
    return metadata


def load_page_order(
    workspace: Path,
    page_id: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    if not (workspace / "page-work-order-registry.csv").is_file():
        raise ValueError("Phase 4 rework requires the page work-order registry (page model only)")
    ledger = indexed(
        read_csv(workspace / "page-implementation-ledger.csv"), "page_id", "page ledger"
    )
    row = ledger.get(page_id)
    if not row or not row.get("work_order_id"):
        raise ValueError(f"Page has no issued implementation work order: {page_id}")
    work_order_id = validate_id(row["work_order_id"], "Page Work-Order-ID")
    registry = [
        item for item in read_csv(workspace / "page-work-order-registry.csv")
        if item.get("work_order_id") == work_order_id and item.get("status") == "ISSUED"
    ]
    relative = f"page-work-orders/{work_order_id}.json"
    if len(registry) != 1 or registry[0].get("relative_path") != relative:
        raise ValueError(f"Page work order is not uniquely registered: {work_order_id}")
    path = safe_relative_path(workspace, relative, f"page work order {work_order_id}")
    if (
        not path.is_file() or path.is_symlink() or path.stat().st_mode & 0o222
        or registry[0].get("work_order_sha256") != sha256_file(path)
    ):
        raise ValueError(f"Page work order is changed/writable: {work_order_id}")
    order = load_json(path)
    if (
        not isinstance(order, dict) or order.get("page_id") != page_id
        or order.get("status") != "ISSUED"
        or row.get("owner_id") != order.get("owner_id")
        or row.get("ui_understanding_agent_id") != order.get("ui_understanding_agent_id")
        or row.get("codearts_task_id") != order.get("codearts_task_id")
    ):
        raise ValueError(f"Page work-order identity differs: {work_order_id}")
    page_owner = str(order["owner_id"])
    ui_owner = str(order["ui_understanding_agent_id"])
    order = dict(order)
    order["ownership"] = {
        "feature_owner_id": page_owner,
        "ui_agent_id": ui_owner,
        "business_data_agent_id": page_owner,
        "native_capability_agent_id": page_owner,
    }
    return row, order


def route(
    problem_type: str,
    phase_ownership: dict[str, str],
    page_order: dict[str, Any],
) -> tuple[str, str]:
    definition = ROUTES.get(problem_type)
    if not definition:
        raise ValueError(f"Unsupported Phase 4 problem type: {problem_type}")
    role, owner_key = definition
    if owner_key in phase_ownership:
        actor = phase_ownership.get(owner_key, "")
    else:
        page_ownership = page_order.get("ownership", {})
        actor = page_ownership.get(owner_key, "") if isinstance(page_ownership, dict) else ""
    if not isinstance(actor, str) or not actor:
        raise ValueError(f"Frozen routing has no actor for {problem_type}")
    if actor == phase_ownership.get("parity_acceptance_agent_id"):
        raise ValueError("Parity acceptance agent cannot be assigned its own rework")
    return role, actor


def load_evidence(
    workspace: Path,
    evidence_id: str,
    expected_executor: str,
    *,
    allowed_statuses: tuple[str, ...] = ("SEALED",),
) -> tuple[dict[str, str], dict[str, Any]]:
    validate_id(evidence_id, "HEVD-ID")
    index = [
        row for row in read_csv(workspace / "evidence-index.csv")
        if row.get("evidence_id") == evidence_id
        and row.get("status") in allowed_statuses
    ]
    if len(index) != 1:
        raise ValueError(f"HEVD is not one active SEALED evidence record: {evidence_id}")
    path = safe_relative_path(workspace, index[0].get("relative_path", ""), f"HEVD {evidence_id}")
    metadata = verify_sealed_package(path, evidence_id, "SEALED")
    if (
        metadata.get("evidence_id") != evidence_id
        or metadata.get("captured_by") != expected_executor
        or index[0].get("metadata_sha256") != sha256_file(path / "metadata.json")
    ):
        raise ValueError(f"HEVD identity/executor differs: {evidence_id}")
    return index[0], metadata


def load_build(
    workspace: Path,
    build_id: str,
    expected_executor: str,
) -> dict[str, Any]:
    validate_id(build_id, "HBUILD-ID")
    metadata = verify_sealed_package(workspace / "builds" / build_id, build_id, "PASS")
    if metadata.get("hbuild_id") != build_id or metadata.get("executed_by") != expected_executor:
        raise ValueError(f"HBUILD identity/executor differs: {build_id}")
    return metadata


def record_belongs_to_page(
    workspace: Path,
    record_id: str,
    page_id: str,
) -> tuple[str, str, str]:
    parity = next(
        (row for row in read_csv(workspace / "parity-map.csv") if row.get("parity_id") == record_id),
        None,
    )
    if parity:
        if parity.get("page_id") != page_id:
            raise ValueError("Parity record belongs to another page")
        return parity.get("feature_id", ""), parity.get("state_id", ""), parity.get("h4env_id", "")
    visual = next(
        (row for row in read_csv(workspace / "visual-elements.csv") if row.get("visual_element_id") == record_id),
        None,
    )
    if visual:
        parity = next(
            (
                row for row in read_csv(workspace / "parity-map.csv")
                if row.get("parity_id") == visual.get("parity_id")
            ),
            None,
        )
        if not parity or parity.get("page_id") != page_id:
            raise ValueError("Visual record belongs to another page")
        return parity.get("feature_id", ""), parity.get("state_id", ""), parity.get("h4env_id", "")
    asset = next(
        (row for row in read_csv(workspace / "asset-migration.csv") if row.get("asset_id") == record_id),
        None,
    )
    if asset:
        if page_id not in split_multi(asset.get("page_ids", "")):
            raise ValueError("Asset record belongs to another page")
        feature_ids = split_multi(asset.get("feature_ids", ""))
        return (feature_ids[0] if feature_ids else ""), "", ""
    capability = next(
        (
            row for row in read_csv(workspace / "capability-implementation.csv")
            if row.get("capability_requirement_id") == record_id
        ),
        None,
    )
    if capability:
        return capability.get("feature_id", ""), "", ""
    if record_id == page_id:
        return "", "", ""
    raise ValueError(f"Rework record is not a frozen page/parity/visual/asset/capability record: {record_id}")


def expected_controller_row(local: dict[str, str], *, closed: bool) -> dict[str, str]:
    return {
        "rework_id": local["ticket_id"],
        "created_at": local["opened_at"],
        "phase": local["phase"],
        "record_id": local["record_id"],
        "feature_id": local["feature_id"],
        "page_id": local["page_id"],
        "state_id": local["state_id"],
        "env_id": local["env_id"],
        "evidence_id": local["failed_verification_id"],
        "gate_rule": local["problem_type"],
        "reason": local["notes"],
        "assigned_to": local["responsible_agent"],
        "completion_condition": local["completion_condition"],
        "status": "CLOSED" if closed else "REWORK",
        "resolved_at": local["closed_at"] if closed else "",
        "resolution_evidence_id": local["resolution_verification_id"] if closed else "",
        "reviewed_by": local["closed_by"] if closed else local["opened_by"],
    }


def verify_double_ledger(
    local_rows: list[dict[str, str]],
    controller_rows: list[dict[str, str]],
) -> None:
    local = indexed(local_rows, "ticket_id", "local rework")
    controller_phase4 = {
        key: value for key, value in indexed(
            [row for row in controller_rows if row.get("phase") == "4"],
            "rework_id",
            "controller Phase 4 rework",
        ).items()
    }
    if set(local) != set(controller_phase4):
        raise ValueError("Local/controller Phase 4 rework Ticket-ID sets differ")
    for ticket_id, row in local.items():
        if row.get("status") not in {"OPEN", "CLOSED"}:
            raise ValueError(f"Unsupported local rework status: {ticket_id}")
        expected = expected_controller_row(row, closed=row["status"] == "CLOSED")
        if controller_phase4[ticket_id] != expected:
            raise ValueError(f"Controller rework mirror differs: {ticket_id}")


def write_synced(
    local_path: Path,
    local_rows: list[dict[str, str]],
    old_local: list[dict[str, str]],
    controller_path: Path,
    controller_rows: list[dict[str, str]],
    old_controller: list[dict[str, str]],
) -> None:
    try:
        write_csv(local_path, LOCAL_FIELDS, local_rows)
        write_csv(controller_path, CONTROLLER_FIELDS, controller_rows)
    except Exception:
        try:
            write_csv(local_path, LOCAL_FIELDS, old_local)
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
    parser.add_argument("--page-id")
    parser.add_argument("--problem-type")
    parser.add_argument("--record-id")
    parser.add_argument("--state-id")
    parser.add_argument("--env-id")
    parser.add_argument("--failed-verification-id")
    parser.add_argument("--severity", choices=("CRITICAL", "HIGH", "MEDIUM", "LOW"))
    parser.add_argument("--reason")
    parser.add_argument("--completion-condition")
    parser.add_argument("--confirmed-by", required=True)
    parser.add_argument("--responsible-agent")
    parser.add_argument("--resolution-verification-id")
    args = parser.parse_args()

    try:
        workspace = canonical_workspace(args.workspace)
        if (workspace / "CLOSED").exists():
            raise ValueError("Phase 4 is CLOSED; rework history is read-only")
        manifest = load_json(workspace / "phase-manifest.json")
        input_lock_path = workspace / "stage-04-input-lock.json"
        if not isinstance(manifest, dict) or manifest.get("phase") != 4:
            raise ValueError("Phase 4 manifest is missing")
        ownership = manifest.get("ownership")
        if not isinstance(ownership, dict):
            raise ValueError("Phase 4 manifest lacks frozen ownership")
        if sha256_file(input_lock_path) != manifest.get("input_lock_sha256"):
            raise ValueError("Phase 4 input lock changed after initialization")
        reviewer = validate_actor(args.reviewer, "parity acceptance agent")
        lead = str(ownership.get("implementation_lead_id", ""))
        if reviewer != ownership.get("parity_acceptance_agent_id"):
            raise ValueError("Only the frozen parity acceptance agent may open or close rework")
        if args.confirmed_by != lead:
            raise ValueError("--confirmed-by must equal the frozen implementation lead")
        ticket_id = validate_id(args.ticket_id, "Rework Ticket-ID")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    local_path = workspace / "rework-tickets.csv"
    controller_path = workspace.parent / "controller" / "rework-log.csv"
    if local_path.is_symlink() or controller_path.is_symlink():
        parser.error("Rework ledgers must not be symbolic links")
    try:
        if csv_fieldnames(local_path) != LOCAL_FIELDS:
            raise ValueError("Phase 4 rework-tickets.csv header differs from the contract")
        if csv_fieldnames(controller_path) != CONTROLLER_FIELDS:
            raise ValueError("Controller rework-log.csv header differs from the contract")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    lock_path = workspace / ".locks" / "stage4-rework-controller-sync.lock"
    try:
        with exclusive_lock(lock_path):
            local_rows = read_csv(local_path)
            controller_rows = read_csv(controller_path)
            verify_double_ledger(local_rows, controller_rows)
            old_local = [dict(row) for row in local_rows]
            old_controller = [dict(row) for row in controller_rows]
            local_matches = [row for row in local_rows if row.get("ticket_id") == ticket_id]
            controller_matches = [
                row for row in controller_rows
                if row.get("phase") == "4" and row.get("rework_id") == ticket_id
            ]

            if args.action == "open":
                required = {
                    "page_id": args.page_id,
                    "problem_type": args.problem_type,
                    "record_id": args.record_id,
                    "failed_verification_id": args.failed_verification_id,
                    "severity": args.severity,
                    "reason": args.reason,
                    "completion_condition": args.completion_condition,
                }
                missing = [name for name, value in required.items() if not value]
                if missing:
                    raise ValueError(f"Opening Phase 4 rework requires: {', '.join(missing)}")
                if local_matches or controller_matches:
                    raise ValueError("Ticket-ID already exists; overwrite is prohibited")
                page_id = validate_id(str(args.page_id), "Page-ID")
                record_id = validate_id(str(args.record_id), "record ID")
                problem_type = str(args.problem_type).upper()
                derived_feature, derived_state, derived_env = record_belongs_to_page(
                    workspace, record_id, page_id
                )
                _page_row, page_order = load_page_order(workspace, page_id)
                responsible_role, responsible_agent = route(problem_type, ownership, page_order)
                if args.responsible_agent and args.responsible_agent != responsible_agent:
                    raise ValueError(
                        f"--responsible-agent differs from frozen routing; expected {responsible_agent}"
                    )
                feature_id = derived_feature
                state_id = str(args.state_id or derived_state)
                env_id = str(args.env_id or derived_env)
                for supplied, derived, label in (
                    (args.state_id, derived_state, "state_id"),
                    (args.env_id, derived_env, "env_id"),
                ):
                    if supplied and derived and supplied != derived:
                        raise ValueError(f"Supplied {label} differs from the frozen record")
                failed_verification_id = str(args.failed_verification_id)
                failed_index, failed_evidence = load_evidence(
                    workspace, failed_verification_id, str(ownership["verification_executor_id"])
                )
                if failed_evidence.get("page_id") != page_id:
                    raise ValueError("Failed HEVD page differs from the rework record")
                if feature_id and failed_evidence.get("feature_id") != feature_id:
                    raise ValueError("Failed HEVD belongs to another feature")
                if state_id and failed_evidence.get("state_id") != state_id:
                    raise ValueError("Failed HEVD state differs from the rework record")
                if env_id and failed_evidence.get("h4env_id") != env_id:
                    raise ValueError("Failed HEVD H4ENV differs from the rework record")
                failed_build_id = str(failed_evidence.get("hbuild_id", ""))
                if not failed_build_id:
                    raise ValueError("Failed HEVD does not bind a failed HBUILD")
                failed_build = load_build(
                    workspace, failed_build_id, str(ownership["verification_executor_id"])
                )
                if env_id and failed_build.get("h4env_id") != env_id:
                    raise ValueError("Failed HBUILD H4ENV differs from the rework record")
                opened_at = utc_now()
                local = {
                    "ticket_id": ticket_id,
                    "severity": str(args.severity),
                    "problem_type": problem_type,
                    "phase": "4",
                    "record_id": record_id,
                    "feature_id": feature_id,
                    "page_id": page_id,
                    "state_id": state_id,
                    "env_id": env_id,
                    "failed_verification_id": failed_verification_id,
                    "responsible_role": responsible_role,
                    "responsible_agent": responsible_agent,
                    "completion_condition": str(args.completion_condition),
                    "status": "OPEN",
                    "opened_by": reviewer,
                    "opened_at": opened_at,
                    "confirmed_by": lead,
                    "confirmed_at": opened_at,
                    "resolution_verification_id": "",
                    "closed_by": "",
                    "closed_at": "",
                    "notes": str(args.reason),
                }
                if any(
                    row.get("status") == "OPEN"
                    and row.get("record_id") == record_id
                    and row.get("problem_type") == problem_type
                    for row in local_rows
                ):
                    raise ValueError("An OPEN ticket already covers this record/problem type")
                local_rows.append(local)
                controller_rows.append(expected_controller_row(local, closed=False))
            else:
                if len(local_matches) != 1 or len(controller_matches) != 1:
                    raise ValueError("Ticket-ID is not uniquely mirrored")
                local = local_matches[0]
                controller = controller_matches[0]
                if local.get("status") != "OPEN" or controller.get("status") != "REWORK":
                    raise ValueError("Only an OPEN/REWORK ticket may be closed")
                page_id = local["page_id"]
                _page_row, page_order = load_page_order(workspace, page_id)
                responsible_role, responsible_agent = route(
                    local["problem_type"], ownership, page_order
                )
                if (
                    local.get("responsible_role") != responsible_role
                    or local.get("responsible_agent") != responsible_agent
                    or local.get("opened_by") != reviewer
                    or local.get("confirmed_by") != lead
                    or controller != expected_controller_row(local, closed=False)
                ):
                    raise ValueError("Stored ticket authority, routing, or mirror differs")
                resolution_verification_id = str(args.resolution_verification_id or "")
                if not resolution_verification_id:
                    raise ValueError("Closing Phase 4 rework requires --resolution-verification-id")
                if resolution_verification_id == local.get("failed_verification_id"):
                    raise ValueError("Resolution HEVD must use a new Evidence-ID")
                resolution_index, resolution_evidence = load_evidence(
                    workspace, resolution_verification_id, str(ownership["verification_executor_id"])
                )
                if resolution_evidence.get("page_id") != page_id:
                    raise ValueError("Resolution HEVD page differs from the ticket")
                if local.get("feature_id") and resolution_evidence.get("feature_id") != local.get("feature_id"):
                    raise ValueError("Resolution HEVD belongs to another feature")
                if local.get("state_id") and resolution_evidence.get("state_id") != local.get("state_id"):
                    raise ValueError("Resolution HEVD state differs from the ticket")
                if local.get("env_id") and resolution_evidence.get("h4env_id") != local.get("env_id"):
                    raise ValueError("Resolution HEVD H4ENV differs from the ticket")
                if parse_time(str(resolution_evidence.get("captured_at", "")), "HEVD captured_at") <= parse_time(
                    local["opened_at"], "ticket opened_at"
                ):
                    raise ValueError("Resolution HEVD must be newer than the ticket")
                resolution_build_id = str(resolution_evidence.get("hbuild_id", ""))
                if not resolution_build_id:
                    raise ValueError("Resolution HEVD does not bind a resolution HBUILD")
                # 关闭返工单时，失败证据可能已被 resolution 证据取代（SUPERSEDED
                # 是合法终态）；此处只需其 HBUILD 归属做新旧对比，故同时接受
                # SEALED 与 SUPERSEDED。resolution 证据仍严格要求 SEALED。
                _failed_index, failed_evidence = load_evidence(
                    workspace,
                    str(local.get("failed_verification_id", "")),
                    str(ownership["verification_executor_id"]),
                    allowed_statuses=("SEALED", "SUPERSEDED"),
                )
                if failed_evidence.get("hbuild_id") == resolution_build_id:
                    raise ValueError("Resolution HBUILD must use a new Build-ID")
                resolution_build = load_build(
                    workspace, resolution_build_id, str(ownership["verification_executor_id"])
                )
                if parse_time(str(resolution_build.get("created_at", "")), "HBUILD created_at") <= parse_time(
                    local["opened_at"], "ticket opened_at"
                ):
                    raise ValueError("Resolution HBUILD must be newer than the ticket")
                closed_at = utc_now()
                local.update(
                    {
                        "status": "CLOSED",
                        "resolution_verification_id": resolution_verification_id,
                        "closed_by": reviewer,
                        "closed_at": closed_at,
                    }
                )
                controller.update(expected_controller_row(local, closed=True))
            write_synced(
                local_path, local_rows, old_local,
                controller_path, controller_rows, old_controller,
            )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print(json.dumps({"ticket_id": ticket_id, "action": args.action, "reviewer": reviewer}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
