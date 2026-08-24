#!/usr/bin/env python3
"""Validation helpers for independently dispatched worker receipts."""

from __future__ import annotations

SCRIPT_INTERFACE = "internal-module"
SCRIPT_INTERFACE_REASON = "Imported by controller receipt recording, phase issuance, and delivery audit scripts."

import csv
import hashlib
import json
import re
from pathlib import Path
from typing import Any


ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,159}")
CONTROLLER_PHASE_ROLES = {
    2: (
        "inventory_lead_id",
        "code_map_agent_id",
        "runtime_state_agent_ids",
        "business_rule_agent_id",
        "data_dependency_agent_id",
        "evidence_administrator_id",
        "coverage_checker_id",
    ),
    3: (
        "architecture_lead_id",
        "toolchain_agent_id",
        "navigation_agent_id",
        "public_ui_agent_id",
        "capability_contract_agent_id",
        "architecture_acceptance_agent_id",
    ),
    4: (
        "implementation_lead_id",
        "visual_asset_agent_id",
        "verification_executor_id",
        "parity_acceptance_agent_id",
    ),
}
FEATURE_ROLES = (
    "feature_owner_id",
    "ui_agent_id",
    "business_data_agent_id",
    "native_capability_agent_id",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def safe_file(run_dir: Path, relative: str, label: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"{label} must be a safe run-relative path")
    path = run_dir / candidate
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} is not a regular file: {relative}")
    try:
        path.resolve().relative_to(run_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes the migration run: {relative}") from exc
    return path


def expected_actors(order: dict[str, Any]) -> list[tuple[str, str]]:
    phase = order.get("phase")
    schema_version = order.get("schema_version")
    if schema_version == "page-work-order-v1":
        owner = order.get("owner_id")
        if phase != 4 or not isinstance(owner, str) or not ID_RE.fullmatch(owner):
            raise ValueError("Page work order has an invalid owner assignment")
        return [("page_owner_id", owner)]
    if schema_version == "capability-work-order-v1":
        owner = order.get("owner_id")
        if phase != 4 or not isinstance(owner, str) or not ID_RE.fullmatch(owner):
            raise ValueError("Capability work order has an invalid owner assignment")
        return [("capability_owner_id", owner)]
    ownership = order.get("ownership")
    if not isinstance(phase, int) or not isinstance(ownership, dict):
        raise ValueError("Work order lacks integer phase or ownership")
    keys = FEATURE_ROLES if str(order.get("work_order_id", "")).startswith("H4WO-") else CONTROLLER_PHASE_ROLES.get(phase)
    if not keys:
        raise ValueError(f"Worker receipts are unsupported for phase {phase}")
    result: list[tuple[str, str]] = []
    for key in keys:
        value = ownership.get(key)
        actors = value if isinstance(value, list) else [value]
        if not actors or any(not isinstance(actor, str) or not ID_RE.fullmatch(actor) for actor in actors):
            raise ValueError(f"Work order has invalid actor assignment for {key}")
        result.extend((key, actor) for actor in actors)
    if len({actor for _, actor in result}) != len(result):
        raise ValueError("One work order reuses an actor across independently assigned roles")
    return result


def read_registry(run_dir: Path) -> list[dict[str, str]]:
    path = run_dir / "controller" / "team-execution-registry.csv"
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def validate_order_receipts(run_dir: Path, order_path: Path) -> list[str]:
    errors: list[str] = []
    try:
        order = load_json(order_path)
        work_order_id = str(order.get("work_order_id", ""))
        phase = int(order.get("phase"))
        expected = expected_actors(order)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return [f"Cannot validate team execution work order: {exc}"]

    rows = [row for row in read_registry(run_dir) if row.get("work_order_id") == work_order_id]
    expected_pairs = set(expected)
    actual_pairs: set[tuple[str, str]] = set()
    task_ids: set[str] = set()
    for row in rows:
        pair = (row.get("role_key", ""), row.get("actor_id", ""))
        if pair in actual_pairs:
            errors.append(f"Duplicate worker receipt for {work_order_id} {pair[0]} {pair[1]}")
            continue
        actual_pairs.add(pair)
        if row.get("phase") != str(phase) or row.get("status") != "COMPLETED":
            errors.append(f"Worker receipt is not a completed Phase {phase} receipt: {pair}")
        task_id = row.get("platform_task_id", "")
        if not ID_RE.fullmatch(task_id) or task_id in task_ids:
            errors.append(f"Worker receipt has invalid or reused platform task ID: {task_id!r}")
        task_ids.add(task_id)
        if order.get("schema_version") in {"page-work-order-v1", "capability-work-order-v1"}:
            if task_id != order.get("codearts_task_id"):
                errors.append(f"Worker receipt task differs from work-order task binding: {task_id!r}")
        relative = row.get("relative_path", "")
        try:
            receipt_path = safe_file(run_dir, relative, "worker receipt")
            if sha256_file(receipt_path) != row.get("receipt_sha256"):
                errors.append(f"Worker receipt hash changed: {relative}")
                continue
            receipt = load_json(receipt_path)
            current_work_order_sha256 = sha256_file(order_path)
            if (
                receipt.get("work_order_id") != work_order_id
                or receipt.get("work_order_sha256") != current_work_order_sha256
                or row.get("work_order_sha256") != current_work_order_sha256
                or receipt.get("phase") != phase
                or receipt.get("role_key") != pair[0]
                or receipt.get("actor_id") != pair[1]
                or receipt.get("platform_task_id") != task_id
                or receipt.get("started_at") != row.get("started_at")
                or receipt.get("ended_at") != row.get("ended_at")
                or receipt.get("terminal_task_state") != "SUCCEEDED"
                or row.get("terminal_task_state") != "SUCCEEDED"
                or receipt.get("status") != "COMPLETED"
            ):
                errors.append(f"Worker receipt identity differs from registry: {relative}")
            artifacts = receipt.get("artifacts")
            if not isinstance(artifacts, list) or not artifacts:
                errors.append(f"Worker receipt has no artifact bindings: {relative}")
            else:
                for artifact in artifacts:
                    if not isinstance(artifact, dict):
                        errors.append(f"Worker receipt has invalid artifact binding: {relative}")
                        continue
                    artifact_path = safe_file(run_dir, str(artifact.get("relative_path", "")), "worker artifact")
                    if sha256_file(artifact_path) != artifact.get("sha256"):
                        errors.append(f"Worker artifact hash changed: {artifact.get('relative_path')}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))

    missing = sorted(expected_pairs - actual_pairs)
    extra = sorted(actual_pairs - expected_pairs)
    if missing:
        errors.append(f"Missing independently dispatched worker receipts for {work_order_id}: {missing}")
    if extra:
        errors.append(f"Unexpected worker receipts for {work_order_id}: {extra}")
    return errors
