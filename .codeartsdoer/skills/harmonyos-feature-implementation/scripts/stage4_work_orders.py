#!/usr/bin/env python3
"""Issue immutable, non-overlapping page and capability orders for Phase 4."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from _common import (
    atomic_json,
    csv_fieldnames,
    exclusive_lock,
    load_json,
    read_csv,
    sha256_file,
    utc_now,
    validate_actor,
    validate_id,
    write_csv,
)
from arkts_page_plan import compile_arkts_page_plan, validate_arkts_page_plan


SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSETS = SKILL_ROOT / "assets"
PAGE_REGISTRY_FIELDS = csv_fieldnames(ASSETS / "page-work-order-registry.template.csv")
CAPABILITY_REGISTRY_FIELDS = csv_fieldnames(ASSETS / "capability-work-order-registry.template.csv")
PAGE_LEDGER_FIELDS = csv_fieldnames(ASSETS / "page-implementation-ledger.template.csv")
PARITY_CHECKS = ("BEHAVIOR", "COMPONENT_TREE", "GEOMETRY", "NAVIGATION", "SCREENSHOT", "SIDE_EFFECT")
GENERATED_PARTS = {".git", ".idea", ".hvigor", "build", "dist", "node_modules", "oh_modules", "__pycache__"}


def _workspace(value: Path) -> Path:
    workspace = Path(value).resolve(strict=True)
    if workspace.is_symlink() or not (workspace / "harmony-project").is_dir():
        raise ValueError("Phase 4 workspace must contain harmony-project and must not be a symbolic link")
    return workspace


def _real_task_id(value: str) -> str:
    try:
        task_id = validate_id(value, "CodeArts task ID")
    except ValueError as exc:
        raise ValueError(f"A real CodeArts task ID is required: {value!r}") from exc
    if value.startswith("__") or any(word in value.upper() for word in ("FILL", "TODO", "TBD", "PLACEHOLDER", "UNKNOWN")):
        raise ValueError(f"A real CodeArts task ID is required: {value!r}")
    return task_id


def _code_paths(values: tuple[str, ...]) -> list[str]:
    if not values:
        raise ValueError("At least one exclusive code path is required")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value or "\\" in value or any(ord(char) < 32 for char in value):
            raise ValueError(f"Unsafe exclusive code path: {value!r}")
        pure = PurePosixPath(value)
        if pure.is_absolute() or any(part in {"", ".", ".."} or part in GENERATED_PARTS for part in pure.parts):
            raise ValueError(f"Unsafe exclusive code path: {value!r}")
        normalized = pure.as_posix()
        if normalized in result:
            raise ValueError(f"Duplicate exclusive code path: {normalized}")
        result.append(normalized)
    result.sort()
    for index, left in enumerate(result):
        for right in result[index + 1:]:
            if _paths_overlap(left, right):
                raise ValueError(f"Overlapping exclusive code paths in one order: {left} / {right}")
    return result


def _paths_overlap(left: str, right: str) -> bool:
    left_parts = tuple(part.casefold() for part in PurePosixPath(left).parts)
    right_parts = tuple(part.casefold() for part in PurePosixPath(right).parts)
    length = min(len(left_parts), len(right_parts))
    return left_parts[:length] == right_parts[:length]


def _page_contracts(workspace: Path) -> dict[str, tuple[dict[str, Any], str, str]]:
    rows = read_csv(workspace / "page-contract-registry.csv")
    contracts: dict[str, tuple[dict[str, Any], str, str]] = {}
    for row in rows:
        page_id = validate_id(row.get("page_id", ""), "Page-ID")
        if page_id in contracts:
            raise ValueError(f"Duplicate page contract registry row: {page_id}")
        relative = row.get("relative_path", "")
        expected = f"page-contracts/{page_id}.json"
        if relative != expected:
            raise ValueError(f"Page contract path differs for {page_id}")
        path = workspace / PurePosixPath(relative)
        if not path.is_file() or path.is_symlink() or sha256_file(path) != row.get("contract_sha256"):
            raise ValueError(f"Page contract hash differs for {page_id}")
        contract = load_json(path)
        if not isinstance(contract, dict) or contract.get("page_id") != page_id:
            raise ValueError(f"Page contract identity differs for {page_id}")
        states = contract.get("states")
        if not isinstance(states, list) or not states:
            raise ValueError(f"Page contract has no states: {page_id}")
        contracts[page_id] = (contract, relative, row["contract_sha256"])
    if not contracts:
        raise ValueError("Page contract registry is empty")
    return contracts


def _ensure_records(workspace: Path, contracts: dict[str, tuple[dict[str, Any], str, str]]) -> None:
    for target, fields in (
        (workspace / "page-work-order-registry.csv", PAGE_REGISTRY_FIELDS),
        (workspace / "capability-work-order-registry.csv", CAPABILITY_REGISTRY_FIELDS),
    ):
        if not target.exists():
            write_csv(target, fields, [])
    ledger = workspace / "page-implementation-ledger.csv"
    if not ledger.exists():
        rows = []
        for page_id, (contract, _, digest) in sorted(contracts.items()):
            rows.append({
                "page_id": page_id,
                "work_order_id": "",
                "owner_id": "",
                "ui_understanding_agent_id": "",
                "codearts_task_id": "",
                "contract_sha256": digest,
                "state_ids": json.dumps(sorted(str(row["state_id"]) for row in contract["states"]), separators=(",", ":")),
                "exclusive_code_paths": "[]",
                "status": "NOT_STARTED",
                "updated_at": "",
            })
        write_csv(ledger, PAGE_LEDGER_FIELDS, rows)


def _registered_orders(
    workspace: Path,
    contracts: dict[str, tuple[dict[str, Any], str, str]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for registry_name, fields, kind, key in (
        ("page-work-order-registry.csv", PAGE_REGISTRY_FIELDS, "page", "page_id"),
        ("capability-work-order-registry.csv", CAPABILITY_REGISTRY_FIELDS, "capability", "capability_id"),
    ):
        rows = read_csv(workspace / registry_name)
        for row in rows:
            if set(row) != set(fields) or row.get("status") != "ISSUED":
                raise ValueError(f"Malformed {kind} work-order registry row")
            relative = row.get("relative_path", "")
            work_order_id = str(row.get("work_order_id", ""))
            expected_relative = f"{kind}-work-orders/{work_order_id}.json"
            pure_relative = PurePosixPath(relative)
            if (
                relative != expected_relative
                or pure_relative.is_absolute()
                or ".." in pure_relative.parts
            ):
                raise ValueError(f"Registered {kind} work-order path is not canonical: {relative}")
            path = (workspace / Path(*pure_relative.parts)).resolve()
            try:
                path.relative_to(workspace.resolve())
            except ValueError as exc:
                raise ValueError(f"Registered {kind} work-order path escapes workspace") from exc
            if not path.is_file() or path.is_symlink() or sha256_file(path) != row.get("work_order_sha256"):
                raise ValueError(f"Registered {kind} work order changed: {row.get('work_order_id')}")
            order = load_json(path)
            if (
                not isinstance(order, dict)
                or order.get("work_order_id") != row.get("work_order_id")
                or order.get(key) != row.get(key)
                or order.get("owner_id") != row.get("owner_id")
                or order.get("codearts_task_id") != row.get("codearts_task_id")
            ):
                raise ValueError(f"Registered {kind} work-order identity differs")
            if kind == "page":
                contract_record = contracts.get(str(order.get("page_id", "")))
                if not contract_record:
                    raise ValueError("Registered page work-order schema references an unknown page")
                contract, contract_relative, contract_digest = contract_record
                expected_keys = {
                    "schema_version", "work_order_id", "phase", "page_id", "owner_id",
                    "ui_understanding_agent_id", "ui_understanding_role_mode", "codearts_task_id",
                    "status", "issued_at", "page_contract_path",
                    "page_contract_sha256", "state_ids", "feature_ids", "phase3_targets",
                    "required_h4env_ids", "capability_dependencies", "required_parity_checks",
                    "comparison_policy", "exclusive_code_paths", "arkts_page_plan_path",
                    "arkts_page_plan_sha256", "ui_understanding_contract", "completion_command",
                }
                plan_relative = f"arkts-page-plans/{order['page_id']}/arkts-page-plan.json"
                plan_path = workspace / Path(*PurePosixPath(plan_relative).parts)
                if not plan_path.is_file() or plan_path.is_symlink():
                    raise ValueError(f"Registered page work-order plan is missing: {work_order_id}")
                plan = load_json(plan_path)
                validate_arkts_page_plan(plan, contract, contract_digest)
                expected_dependencies = sorted({
                    str(item["system_capability_id"])
                    for item in contract.get("system_capabilities", [])
                    if isinstance(item, dict) and item.get("system_capability_id")
                })
                if (
                    set(order) != expected_keys
                    or order.get("schema_version") != "page-work-order-v1"
                    or order.get("phase") != 4
                    or order.get("status") != "ISSUED"
                    or not order.get("ui_understanding_agent_id")
                    or order.get("ui_understanding_role_mode") not in {"PAGE_OWNER_COMBINED", "SEPARATE"}
                    or (order.get("ui_understanding_role_mode") == "PAGE_OWNER_COMBINED")
                    != (order.get("ui_understanding_agent_id") == order.get("owner_id"))
                    or order.get("page_contract_path") != contract_relative
                    or order.get("page_contract_sha256") != contract_digest
                    or order.get("arkts_page_plan_path") != plan_relative
                    or order.get("arkts_page_plan_sha256") != sha256_file(plan_path)
                    or order.get("ui_understanding_contract") != "PHASE2_PAGE_CONTRACT_ONLY_NO_FREE_INFERENCE"
                    or order.get("state_ids") != sorted(str(item["state_id"]) for item in contract["states"])
                    or order.get("feature_ids") != sorted(str(value) for value in contract.get("feature_ids", []))
                    or order.get("phase3_targets") != contract.get("phase3_targets", [])
                    or order.get("required_h4env_ids") != sorted(str(value) for value in contract.get("required_h4env_ids", []))
                    or order.get("capability_dependencies") != expected_dependencies
                    or order.get("required_parity_checks") != list(PARITY_CHECKS)
                    or order.get("comparison_policy") != contract.get("comparison_policy")
                ):
                    raise ValueError(f"Registered page work-order schema differs: {work_order_id}")
            else:
                for field in ("interface_files", "implementation_files", "test_files"):
                    if not isinstance(order.get(field), list) or not order[field]:
                        raise ValueError(f"Registered capability work-order schema lacks {field}")
            records.append(order)
    return records


def _assert_available(orders: list[dict[str, Any]], *, unit_key: str, unit_id: str, owner: str, task: str, paths: list[str]) -> None:
    if any(order.get(unit_key) == unit_id for order in orders):
        raise ValueError(f"{unit_key.replace('_', ' ')} already has an order: {unit_id}")
    for order in orders:
        if order.get("codearts_task_id") == task:
            raise ValueError(f"CodeArts task already bound: {task}")
        if unit_key == "page_id" and order.get("owner_id") == owner:
            raise ValueError(f"Page owner already bound to another page or capability: {owner}")
        if unit_key == "capability_id" and order.get("page_id") and order.get("owner_id") == owner:
            raise ValueError(f"Capability owner already bound as a page owner: {owner}")
        existing_paths = order.get("exclusive_code_paths")
        if not isinstance(existing_paths, list):
            raise ValueError(f"Registered order lacks exclusive code paths: {order.get('work_order_id')}")
        for current in paths:
            for existing in existing_paths:
                if _paths_overlap(current, str(existing)):
                    raise ValueError(f"exclusive code path overlaps {order.get('work_order_id')}: {current} / {existing}")


def _order_id(prefix: str, binding: dict[str, Any]) -> str:
    canonical = json.dumps(binding, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return prefix + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20].upper()


def _write_order(workspace: Path, directory: str, order: dict[str, Any], registry_name: str, fields: list[str], row: dict[str, str]) -> Path:
    path = workspace / directory / f"{order['work_order_id']}.json"
    if path.exists():
        raise ValueError(f"Work order already exists: {order['work_order_id']}")
    registry_path = workspace / registry_name
    prior = read_csv(registry_path)
    try:
        atomic_json(path, order)
        path.chmod(0o444)
        row["work_order_sha256"] = sha256_file(path)
        write_csv(registry_path, fields, [*prior, row])
    except Exception:
        if path.exists():
            path.chmod(0o644)
            path.unlink()
        raise
    return path


def issue_page_order(
    workspace: Path,
    page_id: str,
    owner_id: str,
    codearts_task_id: str,
    code_paths: tuple[str, ...],
    ui_understanding_agent_id: str | None = None,
) -> Path:
    workspace = _workspace(workspace)
    page_id = validate_id(page_id, "Page-ID")
    owner_id = validate_actor(owner_id, "page owner")
    ui_understanding_agent_id = validate_actor(
        ui_understanding_agent_id or owner_id,
        "UI understanding agent",
    )
    codearts_task_id = _real_task_id(codearts_task_id)
    paths = _code_paths(code_paths)
    with exclusive_lock(workspace / ".locks" / "stage4-order-issuance.lock"):
        contracts = _page_contracts(workspace)
        _ensure_records(workspace, contracts)
        if page_id not in contracts:
            raise ValueError(f"Page is outside the frozen contract registry: {page_id}")
        orders = _registered_orders(workspace, contracts)
        _assert_available(orders, unit_key="page_id", unit_id=page_id, owner=owner_id, task=codearts_task_id, paths=paths)
        if any(
            order.get("page_id") != page_id
            and order.get("ui_understanding_agent_id") == ui_understanding_agent_id
            for order in orders
        ):
            raise ValueError(f"UI understanding agent is already bound to another page: {ui_understanding_agent_id}")
        contract, relative, digest = contracts[page_id]
        plan_relative = f"arkts-page-plans/{page_id}/arkts-page-plan.json"
        plan_path = workspace / Path(*PurePosixPath(plan_relative).parts)
        if plan_path.exists():
            if not plan_path.is_file() or plan_path.is_symlink():
                raise ValueError(f"ArkTS page plan target is not a regular file: {page_id}")
            plan = load_json(plan_path)
            validate_arkts_page_plan(plan, contract, digest)
        else:
            plan = compile_arkts_page_plan(contract, digest)
            validate_arkts_page_plan(plan, contract, digest)
            atomic_json(plan_path, plan)
            plan_path.chmod(0o444)
        plan_digest = sha256_file(plan_path)
        state_ids = sorted(str(row["state_id"]) for row in contract["states"])
        capabilities = sorted({
            str(row["system_capability_id"])
            for row in contract.get("system_capabilities", [])
            if isinstance(row, dict) and row.get("system_capability_id")
        })
        issued_at = utc_now()
        binding = {
            "page_id": page_id,
            "owner_id": owner_id,
            "ui_understanding_agent_id": ui_understanding_agent_id,
            "codearts_task_id": codearts_task_id,
            "contract_sha256": digest,
            "arkts_page_plan_sha256": plan_digest,
            "exclusive_code_paths": paths,
        }
        work_order_id = _order_id("H4PWO-", binding)
        order = {
            "schema_version": "page-work-order-v1",
            "work_order_id": work_order_id,
            "phase": 4,
            "page_id": page_id,
            "owner_id": owner_id,
            "ui_understanding_agent_id": ui_understanding_agent_id,
            "ui_understanding_role_mode": "PAGE_OWNER_COMBINED" if ui_understanding_agent_id == owner_id else "SEPARATE",
            "codearts_task_id": codearts_task_id,
            "status": "ISSUED",
            "issued_at": issued_at,
            "page_contract_path": relative,
            "page_contract_sha256": digest,
            "arkts_page_plan_path": plan_relative,
            "arkts_page_plan_sha256": plan_digest,
            "ui_understanding_contract": "PHASE2_PAGE_CONTRACT_ONLY_NO_FREE_INFERENCE",
            "state_ids": state_ids,
            "feature_ids": sorted(str(value) for value in contract.get("feature_ids", [])),
            "phase3_targets": contract.get("phase3_targets", []),
            "required_h4env_ids": sorted(str(value) for value in contract.get("required_h4env_ids", [])),
            "capability_dependencies": capabilities,
            "required_parity_checks": list(PARITY_CHECKS),
            "comparison_policy": contract.get("comparison_policy"),
            "exclusive_code_paths": paths,
            "completion_command": "python scripts/validate_stage4.py --workspace . --reviewer <independent-reviewer>",
        }
        ledger_rows = read_csv(workspace / "page-implementation-ledger.csv")
        matches = [row for row in ledger_rows if row.get("page_id") == page_id]
        if len(matches) != 1 or matches[0].get("status") != "NOT_STARTED":
            raise ValueError(f"Page implementation ledger is not eligible: {page_id}")
        matches[0].update({"work_order_id": work_order_id, "owner_id": owner_id, "ui_understanding_agent_id": ui_understanding_agent_id, "codearts_task_id": codearts_task_id, "exclusive_code_paths": json.dumps(paths, separators=(",", ":")), "status": "INPUT_LOCKED", "updated_at": issued_at})
        registry_path = workspace / "page-work-order-registry.csv"
        old_registry = read_csv(registry_path)
        path = _write_order(
            workspace, "page-work-orders", order, "page-work-order-registry.csv", PAGE_REGISTRY_FIELDS,
            {"work_order_id": work_order_id, "page_id": page_id, "owner_id": owner_id, "ui_understanding_agent_id": ui_understanding_agent_id, "codearts_task_id": codearts_task_id, "relative_path": f"page-work-orders/{work_order_id}.json", "work_order_sha256": "", "issued_at": issued_at, "status": "ISSUED"},
        )
        try:
            write_csv(workspace / "page-implementation-ledger.csv", PAGE_LEDGER_FIELDS, ledger_rows)
        except Exception:
            write_csv(registry_path, PAGE_REGISTRY_FIELDS, old_registry)
            if path.exists():
                path.chmod(0o644)
                path.unlink()
            raise
        return path


def issue_capability_order(workspace: Path, capability_id: str, owner_id: str, codearts_task_id: str, consumer_page_ids: tuple[str, ...], code_paths: tuple[str, ...]) -> Path:
    workspace = _workspace(workspace)
    capability_id = validate_id(capability_id, "Capability-ID")
    owner_id = validate_actor(owner_id, "capability owner")
    codearts_task_id = _real_task_id(codearts_task_id)
    paths = _code_paths(code_paths)
    with exclusive_lock(workspace / ".locks" / "stage4-order-issuance.lock"):
        contracts = _page_contracts(workspace)
        _ensure_records(workspace, contracts)
        actual_consumers = sorted(
            page_id for page_id, (contract, _, _) in contracts.items()
            if capability_id in {
                str(row.get("system_capability_id"))
                for row in contract.get("system_capabilities", []) if isinstance(row, dict)
            }
        )
        requested_consumers = sorted({validate_id(value, "consumer Page-ID") for value in consumer_page_ids})
        if not actual_consumers or requested_consumers != actual_consumers:
            raise ValueError(f"Capability consumer pages differ for {capability_id}: expected {actual_consumers}, got {requested_consumers}")
        orders = _registered_orders(workspace, contracts)
        _assert_available(orders, unit_key="capability_id", unit_id=capability_id, owner=owner_id, task=codearts_task_id, paths=paths)
        behavior_contracts = []
        side_effect_contracts = []
        contract_bindings = []
        for page_id in actual_consumers:
            contract, relative, digest = contracts[page_id]
            page_capability_ids = {
                str(row.get("system_capability_id"))
                for row in contract.get("system_capabilities", []) if isinstance(row, dict)
            }
            behavior_contracts.append({
                "page_id": page_id,
                "business_rules": contract.get("business_rules", []),
                "data_dependencies": contract.get("data_dependencies", []),
                "system_capability": next(row for row in contract["system_capabilities"] if row.get("system_capability_id") == capability_id),
            })
            for side_effect in contract.get("side_effects", []):
                if not isinstance(side_effect, dict):
                    continue
                declared_capability = next(
                    (
                        str(side_effect[field])
                        for field in ("system_capability_id", "capability_id", "capability_requirement_id")
                        if side_effect.get(field)
                    ),
                    "",
                )
                if declared_capability == capability_id or (
                    not declared_capability and page_capability_ids == {capability_id}
                ):
                    side_effect_contracts.append(side_effect)
            contract_bindings.append({"page_id": page_id, "relative_path": relative, "sha256": digest})
        issued_at = utc_now()
        binding = {"capability_id": capability_id, "owner_id": owner_id, "codearts_task_id": codearts_task_id, "consumer_page_ids": actual_consumers, "page_contracts": contract_bindings, "exclusive_code_paths": paths}
        work_order_id = _order_id("H4CWO-", binding)
        lower_paths = [(path, path.lower()) for path in paths]
        interface_files = [path for path, lower in lower_paths if "interface" in lower or "contract" in lower]
        implementation_files = [path for path, lower in lower_paths if "test" not in lower and "interface" not in lower and "contract" not in lower]
        test_files = [path for path, lower in lower_paths if "test" in lower]
        if not interface_files or not implementation_files or not test_files:
            raise ValueError("Capability order requires nonempty interface, implementation, and test code paths")
        order = {
            "schema_version": "capability-work-order-v1",
            "work_order_id": work_order_id,
            "phase": 4,
            "capability_id": capability_id,
            "owner_id": owner_id,
            "codearts_task_id": codearts_task_id,
            "status": "ISSUED",
            "issued_at": issued_at,
            "consumer_page_ids": actual_consumers,
            "page_contracts": contract_bindings,
            "behavior_contracts": behavior_contracts,
            "side_effect_contracts": sorted(side_effect_contracts, key=lambda row: str(row.get("side_effect_id", ""))),
            "interface_files": interface_files,
            "implementation_files": implementation_files,
            "test_files": test_files,
            "exclusive_code_paths": paths,
            "completion_command": "python scripts/validate_stage4.py --workspace . --reviewer <independent-reviewer>",
        }
        return _write_order(
            workspace, "capability-work-orders", order, "capability-work-order-registry.csv", CAPABILITY_REGISTRY_FIELDS,
            {"work_order_id": work_order_id, "capability_id": capability_id, "owner_id": owner_id, "codearts_task_id": codearts_task_id, "relative_path": f"capability-work-orders/{work_order_id}.json", "work_order_sha256": "", "issued_at": issued_at, "status": "ISSUED"},
        )


def validate_order_coverage(workspace: Path) -> dict[str, int]:
    """Fail closed unless every frozen page and shared capability has one order."""
    workspace = _workspace(workspace)
    contracts = _page_contracts(workspace)
    _ensure_records(workspace, contracts)
    orders = _registered_orders(workspace, contracts)
    page_orders = {str(order["page_id"]): order for order in orders if order.get("page_id")}
    capability_orders = {
        str(order["capability_id"]): order for order in orders if order.get("capability_id")
    }
    expected_pages = set(contracts)
    missing_pages = sorted(expected_pages - set(page_orders))
    extra_pages = sorted(set(page_orders) - expected_pages)
    if missing_pages or extra_pages:
        raise ValueError(f"missing page orders={missing_pages}; extra page orders={extra_pages}")
    expected_capabilities = {
        str(row["system_capability_id"])
        for contract, _, _ in contracts.values()
        for row in contract.get("system_capabilities", [])
        if isinstance(row, dict) and row.get("system_capability_id")
    }
    missing_capabilities = sorted(expected_capabilities - set(capability_orders))
    extra_capabilities = sorted(set(capability_orders) - expected_capabilities)
    if missing_capabilities or extra_capabilities:
        raise ValueError(
            f"missing capability orders={missing_capabilities}; extra capability orders={extra_capabilities}"
        )
    if len({order["owner_id"] for order in page_orders.values()}) != len(page_orders):
        raise ValueError("Page order owners are not distinct")
    if len({order["codearts_task_id"] for order in orders}) != len(orders):
        raise ValueError("CodeArts task IDs are not distinct")
    return {"pages": len(page_orders), "capabilities": len(capability_orders)}
