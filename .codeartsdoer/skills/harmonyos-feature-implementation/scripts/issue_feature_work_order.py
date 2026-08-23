#!/usr/bin/env python3
"""Issue one immutable, evidence-bound Phase 4 feature work order."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

from _common import (
    atomic_json,
    csv_fieldnames,
    exclusive_lock,
    join_multi,
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
FEATURE_ROLE_KEYS = (
    "feature_owner_id",
    "ui_agent_id",
    "business_data_agent_id",
    "native_capability_agent_id",
)
GOVERNANCE_ROLE_KEYS = (
    "implementation_lead_id",
    "visual_asset_agent_id",
    "verification_executor_id",
    "parity_acceptance_agent_id",
)
GENERATED_PARTS = {
    ".git", ".idea", ".hvigor", "build", "dist", "coverage", "node_modules",
    "oh_modules", "__pycache__", ".pytest_cache",
}


def canonical_workspace(value: str) -> Path:
    raw = Path(value).expanduser().absolute()
    if raw.is_symlink():
        raise ValueError("Workspace must not be a symbolic link")
    workspace = raw.resolve(strict=True)
    if workspace.name != PHASE_NAME or workspace.parent == workspace:
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


def normalize_code_path(project: Path, value: str) -> str:
    if not value or "\\" in value or any(ord(character) < 32 for character in value):
        raise ValueError(f"Unsafe exclusive code path: {value!r}")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or not pure.parts
        or any(part in {"", ".", ".."} or part in GENERATED_PARTS for part in pure.parts)
    ):
        raise ValueError(f"Unsafe/generated exclusive code path: {value!r}")
    path = safe_relative_path(project, pure.as_posix(), "exclusive code path")
    if path == project or not (path.is_file() or path.is_dir()):
        raise ValueError(f"Exclusive code path must be an existing project file/directory: {value}")
    candidates = [path] if path.is_file() else [path, *path.rglob("*")]
    if any(item.is_symlink() for item in candidates):
        raise ValueError(f"Symbolic links are prohibited in exclusive code paths: {value}")
    return path.relative_to(project).as_posix()


def paths_overlap(left: str, right: str) -> bool:
    left_parts = PurePosixPath(left).parts
    right_parts = PurePosixPath(right).parts
    common = min(len(left_parts), len(right_parts))
    return left_parts[:common] == right_parts[:common]


def verify_registered_orders(
    workspace: Path,
    rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    expected_fields = csv_fieldnames(ASSETS / "feature-work-order-registry.template.csv")
    if expected_fields != [
        "work_order_id", "feature_id", "relative_path", "work_order_sha256",
        "issued_by", "issued_at", "status",
    ]:
        raise ValueError("Feature work-order registry template differs from the contract")
    orders: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for row in rows:
        work_order_id = validate_id(row.get("work_order_id", ""), "Feature Work-Order-ID")
        if work_order_id in seen_ids:
            raise ValueError(f"Duplicate feature Work-Order-ID: {work_order_id}")
        seen_ids.add(work_order_id)
        expected_relative = f"feature-work-orders/{work_order_id}.json"
        if row.get("relative_path") != expected_relative or row.get("status") not in {"ISSUED", "SUPERSEDED"}:
            raise ValueError(f"Feature work-order registry path/status differs: {work_order_id}")
        path = safe_relative_path(workspace, expected_relative, f"feature work order {work_order_id}")
        if (
            not path.is_file()
            or path.is_symlink()
            or sha256_file(path) != row.get("work_order_sha256")
            or path.stat().st_mode & 0o222
        ):
            raise ValueError(f"Registered feature work order is changed/writable: {work_order_id}")
        order = load_json(path)
        if (
            not isinstance(order, dict)
            or order.get("work_order_id") != work_order_id
            or order.get("feature_id") != row.get("feature_id")
            or order.get("issued_by") != row.get("issued_by")
            or order.get("issued_at") != row.get("issued_at")
            or order.get("status") != "ISSUED"
        ):
            raise ValueError(f"Feature work-order identity differs: {work_order_id}")
        orders.append(order)
    return orders


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--feature-id", required=True)
    parser.add_argument("--issued-by", required=True)
    parser.add_argument("--feature-owner", required=True)
    parser.add_argument("--ui-agent", required=True)
    parser.add_argument("--business-data-agent", required=True)
    parser.add_argument("--native-capability-agent", required=True)
    parser.add_argument("--exclusive-code-path", action="append", required=True)
    args = parser.parse_args()

    try:
        workspace = canonical_workspace(args.workspace)
        if (workspace / "CLOSED").exists():
            raise ValueError("Phase 4 is CLOSED; feature work orders are immutable")
        feature_id = validate_id(args.feature_id, "Feature-ID")
        issued_by = validate_actor(args.issued_by, "work-order issuer")
        manifest_path = workspace / "phase-manifest.json"
        input_lock_path = workspace / "stage-04-input-lock.json"
        manifest = load_json(manifest_path)
        input_lock = load_json(input_lock_path)
        if not isinstance(manifest, dict) or manifest.get("phase") != 4:
            raise ValueError("Phase 4 manifest is missing")
        ownership = manifest.get("ownership")
        if not isinstance(ownership, dict) or set(ownership) != set(GOVERNANCE_ROLE_KEYS):
            raise ValueError("Phase 4 manifest ownership differs from the frozen contract")
        if (
            issued_by != ownership.get("implementation_lead_id")
            or sha256_file(input_lock_path) != manifest.get("input_lock_sha256")
            or input_lock.get("ownership") != ownership
            or input_lock.get("work_order_id") != manifest.get("work_order_id")
            or input_lock.get("work_order_sha256") != manifest.get("work_order_sha256")
        ):
            raise ValueError("Only the frozen implementation lead may issue a bound feature work order")
        feature_ownership = {
            "feature_owner_id": validate_actor(args.feature_owner, "feature owner"),
            "ui_agent_id": validate_actor(args.ui_agent, "UI agent"),
            "business_data_agent_id": validate_actor(args.business_data_agent, "business/data agent"),
            "native_capability_agent_id": validate_actor(
                args.native_capability_agent, "native capability agent"
            ),
        }
        actor_values = list(feature_ownership.values())
        if len(actor_values) != len(set(actor_values)):
            raise ValueError("The four feature implementation actors must be distinct")
        overlap = sorted(set(actor_values) & set(ownership.values()))
        if overlap:
            raise ValueError(f"Feature actors must not reuse Phase 4 governance actors: {overlap}")
        project = workspace / "harmony-project"
        exclusive_paths = sorted(
            {normalize_code_path(project, value) for value in args.exclusive_code_path}
        )
        for index, left in enumerate(exclusive_paths):
            for right in exclusive_paths[index + 1:]:
                if paths_overlap(left, right):
                    raise ValueError(f"Exclusive code paths overlap each other: {left} / {right}")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    registry_path = workspace / "feature-work-order-registry.csv"
    ledger_path = workspace / "implementation-ledger.csv"
    lock_path = workspace / ".locks" / "feature-work-order-issuance.lock"
    work_order_path: Path | None = None
    try:
        with exclusive_lock(lock_path):
            registry_rows = read_csv(registry_path)
            existing_orders = verify_registered_orders(workspace, registry_rows)
            if any(
                order.get("feature_id") == feature_id
                and next(
                    row.get("status") for row in registry_rows
                    if row.get("work_order_id") == order.get("work_order_id")
                ) != "SUPERSEDED"
                for order in existing_orders
            ):
                raise ValueError(f"Feature already has an active work order: {feature_id}")
            for order in existing_orders:
                registry_row = next(
                    row for row in registry_rows
                    if row.get("work_order_id") == order.get("work_order_id")
                )
                if registry_row.get("status") == "SUPERSEDED":
                    continue
                existing_paths = order.get("exclusive_code_paths")
                if not isinstance(existing_paths, list):
                    raise ValueError(f"Existing work order lacks exclusive code paths: {order.get('work_order_id')}")
                for current in exclusive_paths:
                    for existing in existing_paths:
                        normalized_existing = normalize_code_path(project, str(existing))
                        if paths_overlap(current, normalized_existing):
                            raise ValueError(
                                f"Exclusive code path overlaps {order.get('work_order_id')}: "
                                f"{current} / {normalized_existing}"
                            )

            ledger_rows = read_csv(ledger_path)
            ledger = indexed(ledger_rows, "feature_id", "implementation ledger")
            feature_row = ledger.get(feature_id)
            if not feature_row:
                raise ValueError(f"Feature is outside the frozen implementation ledger: {feature_id}")
            if (
                feature_row.get("status") != "NOT_STARTED"
                or feature_row.get("work_order_id")
                or any(feature_row.get(field) for field in FEATURE_ROLE_KEYS)
            ):
                raise ValueError(f"Feature is not eligible for initial work-order issuance: {feature_id}")

            parity_rows = [
                row for row in read_csv(workspace / "parity-map.csv")
                if row.get("feature_id") == feature_id
            ]
            if not parity_rows or any(row.get("status") != "NOT_STARTED" for row in parity_rows):
                raise ValueError("Every feature parity row must still be NOT_STARTED at issuance")
            parity_index = indexed(parity_rows, "parity_id", "feature parity")
            source_inventory_ids = sorted({row["inventory_id"] for row in parity_rows})
            if source_inventory_ids != sorted(split_multi(feature_row.get("source_inventory_ids", ""))):
                raise ValueError("Feature parity inventory differs from implementation ledger")
            required_h4env_ids = sorted({row["h4env_id"] for row in parity_rows})
            frozen_h4env_ids = set(input_lock.get("required_h4env_ids", []))
            if not set(required_h4env_ids) <= frozen_h4env_ids:
                raise ValueError("Feature parity references an unfrozen H4ENV")
            harmony_module_ids = sorted({row["harmony_module_id"] for row in parity_rows})
            if harmony_module_ids != sorted(split_multi(feature_row.get("harmony_module_ids", ""))):
                raise ValueError("Feature parity modules differ from implementation ledger")
            target_by_source: dict[str, dict[str, str]] = {}
            for row in parity_rows:
                target = {
                    "source_row_key": row["source_row_key"],
                    "harmony_module_id": row["harmony_module_id"],
                    "target_kind": row["target_kind"],
                    "target_id": row["target_id"],
                }
                previous = target_by_source.setdefault(row["source_row_key"], target)
                if previous != target:
                    raise ValueError(f"Source row maps to conflicting Harmony targets: {row['source_row_key']}")
            targets = [target_by_source[key] for key in sorted(target_by_source)]
            asset_ids = sorted(
                {
                    asset_id
                    for row in parity_rows
                    for asset_id in split_multi(row.get("asset_ids", ""))
                }
            )
            asset_registry = indexed(read_csv(workspace / "asset-migration.csv"), "asset_id", "asset migration")
            if not set(asset_ids) <= set(asset_registry):
                raise ValueError("Feature parity references an unknown frozen asset")
            capability_rows = [
                row for row in read_csv(workspace / "capability-implementation.csv")
                if row.get("feature_id") == feature_id
            ]
            capability_requirement_ids = sorted(
                row["capability_requirement_id"] for row in capability_rows
            )
            capability_contract_ids = sorted(
                {row["capability_contract_id"] for row in capability_rows}
            )

            issued_at = utc_now()
            binding = json.dumps(
                {
                    "run_id": manifest.get("run_id"),
                    "feature_id": feature_id,
                    "input_lock_sha256": manifest.get("input_lock_sha256"),
                    "ownership": feature_ownership,
                    "parity_ids": sorted(parity_index),
                    "exclusive_code_paths": exclusive_paths,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            work_order_id = "H4WO-" + hashlib.sha256(binding.encode("utf-8")).hexdigest()[:20].upper()
            validate_id(work_order_id, "Feature Work-Order-ID")
            relative = f"feature-work-orders/{work_order_id}.json"
            work_order_path = workspace / relative
            if work_order_path.exists() or any(
                row.get("work_order_id") == work_order_id for row in registry_rows
            ):
                raise ValueError(f"Feature Work-Order-ID already exists: {work_order_id}")
            work_order: dict[str, Any] = {
                "schema_version": "1.0",
                "work_order_id": work_order_id,
                "run_id": manifest.get("run_id"),
                "phase": 4,
                "feature_id": feature_id,
                "status": "ISSUED",
                "issued_at": issued_at,
                "issued_by": issued_by,
                "phase4_manifest_sha256": sha256_file(manifest_path),
                "stage04_input_lock_sha256": sha256_file(input_lock_path),
                "ownership": feature_ownership,
                "visual_asset_agent_id": ownership["visual_asset_agent_id"],
                "source_inventory_ids": source_inventory_ids,
                "parity_ids": sorted(parity_index),
                "harmony_module_ids": harmony_module_ids,
                "targets": targets,
                "required_h4env_ids": required_h4env_ids,
                "asset_ids": asset_ids,
                "capability_requirement_ids": capability_requirement_ids,
                "capability_contract_ids": capability_contract_ids,
                "exclusive_code_paths": exclusive_paths,
                "completion_conditions": [
                    "Every seeded parity row is implemented and evidenced on every required H4ENV"
                ],
            }
            old_registry = [dict(row) for row in registry_rows]
            old_ledger = [dict(row) for row in ledger_rows]
            try:
                atomic_json(work_order_path, work_order)
                work_order_path.chmod(0o444)
                registry_rows.append(
                    {
                        "work_order_id": work_order_id,
                        "feature_id": feature_id,
                        "relative_path": relative,
                        "work_order_sha256": sha256_file(work_order_path),
                        "issued_by": issued_by,
                        "issued_at": issued_at,
                        "status": "ISSUED",
                    }
                )
                feature_row.update(
                    {
                        "work_order_id": work_order_id,
                        "feature_owner_id": feature_ownership["feature_owner_id"],
                        "ui_agent_id": feature_ownership["ui_agent_id"],
                        "business_data_agent_id": feature_ownership["business_data_agent_id"],
                        "native_capability_agent_id": feature_ownership["native_capability_agent_id"],
                        "asset_agent_id": ownership["visual_asset_agent_id"],
                        "source_inventory_ids": join_multi(source_inventory_ids),
                        "harmony_module_ids": join_multi(harmony_module_ids),
                        "status": "INPUT_LOCKED",
                        "updated_by": issued_by,
                        "updated_at": issued_at,
                        "notes": "",
                    }
                )
                write_csv(
                    registry_path,
                    csv_fieldnames(ASSETS / "feature-work-order-registry.template.csv"),
                    registry_rows,
                )
                write_csv(
                    ledger_path,
                    csv_fieldnames(ASSETS / "implementation-ledger.template.csv"),
                    ledger_rows,
                )
            except Exception:
                write_csv(
                    registry_path,
                    csv_fieldnames(ASSETS / "feature-work-order-registry.template.csv"),
                    old_registry,
                )
                write_csv(
                    ledger_path,
                    csv_fieldnames(ASSETS / "implementation-ledger.template.csv"),
                    old_ledger,
                )
                if work_order_path.exists():
                    work_order_path.chmod(0o644)
                    work_order_path.unlink()
                raise
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "work_order_id": work_order_id,
                "work_order": str(work_order_path),
                "feature_id": feature_id,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
