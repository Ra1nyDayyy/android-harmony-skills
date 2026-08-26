#!/usr/bin/env python3
"""Issue an immutable Phase 3 HarmonyOS-scaffold work order from a current Gate 2 PASS."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from _human_gate import require_current_human_approval

from _team_execution import validate_order_receipts


ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,95}$")
STAGE3_ROLE_KEYS = (
    "architecture_lead_id",
    "toolchain_agent_id",
    "navigation_agent_id",
    "public_ui_agent_id",
    "capability_contract_agent_id",
    "architecture_acceptance_agent_id",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_run_file(run_dir: Path, relative: str, label: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise ValueError(f"Unsafe {label} path: {relative!r}")
    candidate = run_dir.joinpath(*pure.parts)
    current = run_dir
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in {label} path: {relative}")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(run_dir)
    except ValueError as exc:
        raise ValueError(f"{label} path escapes the migration run: {relative}") from exc
    if not resolved.is_file():
        raise ValueError(f"Missing {label}: {resolved}")
    return resolved


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"Refusing symbolic-link output: {path}")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"Refusing symbolic-link output: {path}")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def load_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if path.is_symlink():
        raise ValueError(f"Symbolic-link controller record is prohibited: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if not fields:
        raise ValueError(f"CSV has no header: {path}")
    return fields, rows


def atomic_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    if path.is_symlink():
        raise ValueError(f"Refusing symbolic-link controller record: {path}")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def phase12_actor_ids(ownership: dict[str, Any]) -> set[str]:
    actors: set[str] = set()
    for value in ownership.values():
        if isinstance(value, str) and value:
            actors.add(value)
        elif isinstance(value, list):
            actors.update(str(item) for item in value if isinstance(item, str) and item)
    return actors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--issued-by", required=True)
    for key in STAGE3_ROLE_KEYS:
        parser.add_argument("--" + key.replace("_", "-"), required=True)
    args = parser.parse_args()

    run_input = Path(args.run_dir).expanduser().absolute()
    if run_input.is_symlink():
        parser.error("Migration run must not be a symbolic link")
    run_dir = run_input.resolve()
    if not run_dir.is_dir():
        parser.error(f"Migration run does not exist: {run_dir}")

    try:
        scope_path = safe_run_file(run_dir, "controller/scope.json", "controller scope")
        gate_path = safe_run_file(run_dir, "controller/gate-report.json", "Gate 2 report")
        scope = load_json(scope_path)
        gate = load_json(gate_path)
        scope_sha256 = sha256_file(scope_path)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if (
        gate.get("phase") != 2
        or gate.get("verdict") != "PASS"
        or gate.get("scope_sha256") != scope_sha256
        or gate.get("errors")
    ):
        parser.error("A current, complete controller Gate 2 PASS is required")

    recheck = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("validate_gate.py")), "--run-dir", str(run_dir), "--phase", "2"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if recheck.returncode != 0:
        detail = recheck.stderr.strip() or recheck.stdout.strip()
        parser.error(f"Gate 2 baseline changed after its recorded PASS: {detail[:500]}")
    try:
        human_review = require_current_human_approval(run_dir, 2, gate_path)
    except ValueError as exc:
        parser.error(f"Current human approval is required after Gate 2 recheck: {exc}")

    ownership = scope.get("ownership") if isinstance(scope.get("ownership"), dict) else {}
    controller_id = ownership.get("migration_controller_id")
    if args.issued_by != controller_id:
        parser.error("--issued-by must equal the frozen migration controller")

    stage3_ownership = {key: str(getattr(args, key)).strip() for key in STAGE3_ROLE_KEYS}
    invalid = [key for key, value in stage3_ownership.items() if not ACTOR_RE.fullmatch(value)]
    if invalid:
        parser.error(f"Invalid Phase 3 actor ID(s): {invalid}")
    stage3_values = list(stage3_ownership.values())
    if len(stage3_values) != len(set(stage3_values)):
        parser.error("All six Phase 3 actor IDs must be distinct")
    overlaps = sorted(set(stage3_values) & phase12_actor_ids(ownership))
    if overlaps:
        parser.error(f"Phase 3 actors must differ from all frozen Phase 1/2 actors: {overlaps}")

    input_relatives = {
        "phase2_closure_sha256": "phase-02-android-inventory/closure-report.json",
        "phase2_closure_manifest_sha256": "phase-02-android-inventory/closure-manifest.sha256",
        "phase2_closed_sha256": "phase-02-android-inventory/CLOSED",
        "phase2_inventory_sha256": "phase-02-android-inventory/inventory.csv",
        "phase2_asset_inventory_sha256": "phase-02-android-inventory/asset-inventory.csv",
        "phase2_asset_manifest_sha256": "phase-02-android-inventory/asset-package/manifest.sha256",
        "phase2_asset_committed_sha256": "phase-02-android-inventory/asset-package/COMMITTED",
        "phase2_anchor_snapshot_sha256": "phase-02-android-inventory/evidence-anchors.snapshot.csv",
        "controller_anchor_registry_sha256": "controller/evidence-anchor-registry.csv",
    }
    try:
        input_paths = {
            digest_key: safe_run_file(run_dir, relative, digest_key)
            for digest_key, relative in input_relatives.items()
        }
        registry_path = run_dir / "controller" / "work-order-registry.csv"
        registry_fields, registry_rows = load_csv(registry_path)
        ledger_path = run_dir / "controller" / "task-ledger.csv"
        ledger_fields, ledger_rows = load_csv(ledger_path)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    active_phase2 = [
        row for row in registry_rows
        if row.get("phase") == "2" and row.get("status", "").upper() != "SUPERSEDED"
    ]
    # gmi run：无控制器 Phase-2 工单（gmi_closure 证据链替代），跳过该前置检查；
    # 其等效门禁由 validate_gate --phase 2 的 gmi 等价校验承担。
    gmi_run = (run_dir / "phase-02-android-inventory" / "gmi" / "phase-2-closure.json").is_file() \
        or (run_dir / "phase-02-android-inventory" / "phase-2-closure.json").is_file()
    if len(active_phase2) != 1 and not gmi_run:
        parser.error("Exactly one active Phase 2 work order is required")
    if not active_phase2 and gmi_run:
        phase2_order_path = None
    else:
        try:
            phase2_order_path = safe_run_file(
                run_dir, active_phase2[0].get("relative_path", ""), "Phase 2 work order"
            )
        except ValueError as exc:
            parser.error(str(exc))
        receipt_errors = validate_order_receipts(run_dir, phase2_order_path)
        if receipt_errors:
            parser.error("Phase 2 worker dispatch is incomplete: " + "; ".join(receipt_errors[:8]))

    phase3_ledger = [row for row in ledger_rows if row.get("phase") == "3"]
    if len(phase3_ledger) != 1:
        parser.error("Task ledger must contain exactly one Phase 3 row")

    binding = "|".join(
        [scope_sha256, sha256_file(gate_path)]
        + [sha256_file(input_paths[key]) for key in sorted(input_paths)]
        + stage3_values
    )
    suffix = hashlib.sha256(binding.encode("utf-8")).hexdigest()[:12].upper()
    work_order_id = f"WO-PHASE-03-{suffix}"
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{2,79}", work_order_id):
        parser.error("Generated an invalid work-order ID")
    work_order_relative = f"controller/work-orders/{work_order_id}.json"
    gate_snapshot_relative = f"controller/work-orders/{work_order_id}.phase-02-gate-report.json"
    work_orders_dir = run_dir / "controller" / "work-orders"
    if work_orders_dir.is_symlink():
        parser.error("Controller work-orders directory must not be a symbolic link")
    try:
        work_orders_dir.mkdir(parents=True, exist_ok=True)
        if not work_orders_dir.is_dir() or work_orders_dir.resolve().parent != (run_dir / "controller").resolve():
            parser.error("Controller work-orders directory is not canonical")
    except OSError as exc:
        parser.error(f"Cannot prepare controller work-orders directory: {exc}")
    work_order_path = run_dir / work_order_relative
    gate_snapshot_path = run_dir / gate_snapshot_relative
    if work_order_path.exists() or gate_snapshot_path.exists():
        parser.error(f"Phase 3 work order already exists; overwrite is prohibited: {work_order_id}")
    if any(row.get("work_order_id") == work_order_id for row in registry_rows):
        parser.error(f"Work-order registry already contains: {work_order_id}")
    existing_phase3 = [
        row for row in registry_rows
        if row.get("phase") == "3" and row.get("status", "").upper() != "SUPERSEDED"
    ]
    if existing_phase3:
        parser.error("A Phase 3 work order is already registered; supersede it explicitly before reissuing")

    issued_at = utc_now()
    gate_sha256 = sha256_file(gate_path)
    work_order: dict[str, Any] = {
        "work_order_id": work_order_id,
        "run_id": scope.get("run_id"),
        "phase": 3,
        "status": "ISSUED",
        "issued_at": issued_at,
        "issued_by": args.issued_by,
        "scope_relative_path": "controller/scope.json",
        "scope_sha256": scope_sha256,
        "phase2_gate_snapshot_relative_path": gate_snapshot_relative,
        "phase2_gate_sha256": gate_sha256,
        "human_review_id": human_review["review_id"],
        "human_review_decision": human_review["decision"],
        "human_review_gate_sha256": human_review["gate_report_sha256"],
        "included_features": scope.get("migration_scope", {}).get("included_features", []),
        "excluded_features": scope.get("migration_scope", {}).get("excluded_features", []),
        "ownership": stage3_ownership,
        "required_skill": "harmonyos-migration-scaffold",
        "business_implementation_allowed": False,
        "required_return": [
            "stage-03-input-lock.json",
            "template-generation.json",
            "advanced-obligations.json",
            "phase-manifest.json",
            "asset-registry.csv",
            "environments/",
            "harmony-project/",
            "verification/",
            "scaffold-snapshot-manifest.json",
            "build-report.json",
            "stage-03-gate-report.json",
            "stage-03-closure-manifest.sha256",
            "CLOSED",
        ],
    }
    for digest_key, relative in input_relatives.items():
        work_order[digest_key] = sha256_file(input_paths[digest_key])
        work_order[digest_key.removesuffix("_sha256") + "_relative_path"] = relative

    missing_registry_fields = {
        "work_order_id", "phase", "relative_path", "scope_sha256", "work_order_sha256",
        "issued_at", "issued_by", "status",
    } - set(registry_fields)
    if missing_registry_fields:
        parser.error(f"Work-order registry lacks columns: {sorted(missing_registry_fields)}")

    # Snapshot first so the work order never points at the mutable controller gate file.
    try:
        atomic_bytes(gate_snapshot_path, gate_path.read_bytes())
        atomic_json(work_order_path, work_order)
        work_order_sha256 = sha256_file(work_order_path)
        registry_rows.append(
            {
                "work_order_id": work_order_id,
                "phase": "3",
                "relative_path": work_order_relative,
                "scope_sha256": scope_sha256,
                "work_order_sha256": work_order_sha256,
                "issued_at": issued_at,
                "issued_by": args.issued_by,
                "status": "ISSUED",
            }
        )
        phase3_ledger[0].update(
            {
                "owner": stage3_ownership["architecture_lead_id"],
                "status": "IN_PROGRESS",
                "updated_at": issued_at,
                "notes": work_order_id,
            }
        )
        atomic_csv(registry_path, registry_fields, registry_rows)
        atomic_csv(ledger_path, ledger_fields, ledger_rows)
    except (OSError, ValueError) as exc:
        parser.error(f"Could not persist Phase 3 work order: {exc}")

    print(
        json.dumps(
            {
                "work_order_id": work_order_id,
                "work_order": str(work_order_path),
                "work_order_sha256": work_order_sha256,
                "phase2_gate_snapshot": str(gate_snapshot_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
