#!/usr/bin/env python3
"""Issue an immutable Phase 4 feature-implementation work order from a current Gate 3 PASS."""

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


ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,95}$")
STAGE4_ROLE_KEYS = (
    "implementation_lead_id",
    "visual_asset_agent_id",
    "verification_executor_id",
    "parity_acceptance_agent_id",
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
    current = run_dir
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in {label} path: {relative}")
    resolved = current.resolve()
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


def ownership_actor_ids(ownership: dict[str, Any]) -> set[str]:
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
    for key in STAGE4_ROLE_KEYS:
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
        gate_path = safe_run_file(run_dir, "controller/gate-report.json", "Gate 3 report")
        scope = load_json(scope_path)
        gate = load_json(gate_path)
        scope_sha256 = sha256_file(scope_path)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if (
        gate.get("phase") != 3
        or gate.get("verdict") != "PASS"
        or gate.get("scope_sha256") != scope_sha256
        or gate.get("errors")
    ):
        parser.error("A current, complete controller Gate 3 PASS is required")

    recheck = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("validate_gate.py")), "--run-dir", str(run_dir), "--phase", "3"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=180,
        check=False,
    )
    if recheck.returncode != 0:
        detail = recheck.stderr.strip() or recheck.stdout.strip()
        parser.error(f"Gate 3 baseline changed after its recorded PASS: {detail[:800]}")

    controller_ownership = scope.get("ownership") if isinstance(scope.get("ownership"), dict) else {}
    controller_id = controller_ownership.get("migration_controller_id")
    if args.issued_by != controller_id:
        parser.error("--issued-by must equal the frozen migration controller")

    stage4_ownership = {key: str(getattr(args, key)).strip() for key in STAGE4_ROLE_KEYS}
    invalid = [key for key, value in stage4_ownership.items() if not ACTOR_RE.fullmatch(value)]
    if invalid:
        parser.error(f"Invalid Phase 4 actor ID(s): {invalid}")
    role_values = list(stage4_ownership.values())
    if len(role_values) != len(set(role_values)):
        parser.error("All four Phase 4 actor IDs must be distinct")

    try:
        registry_path = run_dir / "controller" / "work-order-registry.csv"
        registry_fields, registry_rows = load_csv(registry_path)
        active_phase3 = [
            row for row in registry_rows
            if row.get("phase") == "3" and row.get("status", "").upper() != "SUPERSEDED"
        ]
        if len(active_phase3) != 1:
            raise ValueError("Controller must have exactly one active Phase 3 work order")
        phase3_order_path = safe_run_file(
            run_dir, active_phase3[0].get("relative_path", ""), "Phase 3 work order"
        )
        phase3_order = load_json(phase3_order_path)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    prior_actors = ownership_actor_ids(controller_ownership)
    phase3_ownership = phase3_order.get("ownership") if isinstance(phase3_order.get("ownership"), dict) else {}
    prior_actors.update(ownership_actor_ids(phase3_ownership))
    overlaps = sorted(set(role_values) & prior_actors)
    if overlaps:
        parser.error(f"Phase 4 actors must differ from all frozen Phase 1–3 actors: {overlaps}")

    input_relatives = {
        "phase2_closure_sha256": "phase-02-android-inventory/closure-report.json",
        "phase2_closure_manifest_sha256": "phase-02-android-inventory/closure-manifest.sha256",
        "phase2_closed_sha256": "phase-02-android-inventory/CLOSED",
        "phase2_inventory_sha256": "phase-02-android-inventory/inventory.csv",
        "phase2_evidence_index_sha256": "phase-02-android-inventory/evidence-index.csv",
        "phase2_asset_inventory_sha256": "phase-02-android-inventory/asset-inventory.csv",
        "phase2_asset_manifest_sha256": "phase-02-android-inventory/asset-package/manifest.sha256",
        "phase2_asset_committed_sha256": "phase-02-android-inventory/asset-package/COMMITTED",
        "phase2_static_pages_sha256": "phase-02-android-inventory/static-analysis/pages.json",
        "phase2_static_components_sha256": "phase-02-android-inventory/static-analysis/components.json",
        "phase2_static_events_sha256": "phase-02-android-inventory/static-analysis/events.json",
        "phase2_static_transitions_sha256": "phase-02-android-inventory/static-analysis/transitions.json",
        "phase2_runtime_observations_sha256": "phase-02-android-inventory/runtime-observations.json",
        "phase2_page_gate_sha256": "phase-02-android-inventory/page-gate-report.json",
        "phase2_advanced_analysis_sha256": "phase-02-android-inventory/static-analysis/advanced-analysis.json",
        "phase2_advanced_observations_sha256": "phase-02-android-inventory/advanced-observations.json",
        "phase2_advanced_gate_sha256": "phase-02-android-inventory/advanced-gate-report.json",
        "phase2_probe_index_sha256": "phase-02-android-inventory/probe-evidence-index.csv",
        "phase3_input_lock_sha256": "phase-03-harmony-scaffold/stage-03-input-lock.json",
        "phase3_gate_report_sha256": "phase-03-harmony-scaffold/stage-03-gate-report.json",
        "phase3_closure_manifest_sha256": "phase-03-harmony-scaffold/stage-03-closure-manifest.sha256",
        "phase3_closed_sha256": "phase-03-harmony-scaffold/CLOSED",
        "phase3_scaffold_snapshot_sha256": "phase-03-harmony-scaffold/scaffold-snapshot-manifest.json",
        "phase3_architecture_map_sha256": "phase-03-harmony-scaffold/architecture-map.csv",
        "phase3_module_registry_sha256": "phase-03-harmony-scaffold/module-registry.csv",
        "phase3_route_registry_sha256": "phase-03-harmony-scaffold/route-registry.csv",
        "phase3_surface_registry_sha256": "phase-03-harmony-scaffold/surface-registry.csv",
        "phase3_public_ui_registry_sha256": "phase-03-harmony-scaffold/public-ui-registry.csv",
        "phase3_capability_contracts_sha256": "phase-03-harmony-scaffold/capability-contracts.csv",
        "phase3_asset_registry_sha256": "phase-03-harmony-scaffold/asset-registry.csv",
        "phase3_advanced_obligations_sha256": "phase-03-harmony-scaffold/advanced-obligations.json",
        "phase3_henv_registry_sha256": "phase-03-harmony-scaffold/environments/henv-registry.csv",
    }
    try:
        input_paths = {
            digest_key: safe_run_file(run_dir, relative, digest_key)
            for digest_key, relative in input_relatives.items()
        }
        henv_rows = load_csv(input_paths["phase3_henv_registry_sha256"])[1]
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    henv_records: list[dict[str, str]] = []
    seen_henv_ids: set[str] = set()
    try:
        for row in henv_rows:
            henv_id = str(row.get("henv_id", ""))
            if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{2,79}", henv_id) or henv_id in seen_henv_ids:
                raise ValueError(f"Unsafe or duplicate HENV-ID: {henv_id!r}")
            seen_henv_ids.add(henv_id)
            if row.get("status") != "FROZEN":
                raise ValueError(f"Phase 4 may consume only frozen HENV rows: {henv_id}")
            relative = f"phase-03-harmony-scaffold/environments/{henv_id}/harmony-environment.json"
            path = safe_run_file(run_dir, relative, f"HENV {henv_id}")
            digest = sha256_file(path)
            if row.get("environment_sha256") != digest:
                raise ValueError(f"HENV registry hash differs for {henv_id}")
            henv_records.append({"henv_id": henv_id, "relative_path": relative, "sha256": digest})
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if not henv_records:
        parser.error("Phase 3 has no frozen HENV available to Phase 4")

    ledger_path = run_dir / "controller" / "task-ledger.csv"
    try:
        ledger_fields, ledger_rows = load_csv(ledger_path)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    phase4_ledger = [row for row in ledger_rows if row.get("phase") == "4"]
    if len(phase4_ledger) != 1:
        parser.error("Task ledger must contain exactly one Phase 4 row")
    active_phase4 = [
        row for row in registry_rows
        if row.get("phase") == "4" and row.get("status", "").upper() != "SUPERSEDED"
    ]
    if active_phase4:
        parser.error("A Phase 4 work order is already registered; supersede it explicitly before reissuing")

    gate_sha256 = sha256_file(gate_path)
    binding = "|".join(
        [scope_sha256, gate_sha256, sha256_file(phase3_order_path)]
        + [sha256_file(input_paths[key]) for key in sorted(input_paths)]
        + [record["sha256"] for record in sorted(henv_records, key=lambda item: item["henv_id"])]
        + role_values
    )
    suffix = hashlib.sha256(binding.encode("utf-8")).hexdigest()[:12].upper()
    work_order_id = f"WO-PHASE-04-{suffix}"
    work_order_relative = f"controller/work-orders/{work_order_id}.json"
    gate_snapshot_relative = f"controller/work-orders/{work_order_id}.phase-03-gate-report.json"
    work_orders_dir = run_dir / "controller" / "work-orders"
    if work_orders_dir.is_symlink():
        parser.error("Controller work-orders directory must not be a symbolic link")
    try:
        work_orders_dir.mkdir(parents=True, exist_ok=True)
        if not work_orders_dir.is_dir() or work_orders_dir.resolve().parent != (run_dir / "controller").resolve():
            raise ValueError("Controller work-orders directory is not canonical")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    work_order_path = run_dir / work_order_relative
    gate_snapshot_path = run_dir / gate_snapshot_relative
    if work_order_path.exists() or gate_snapshot_path.exists():
        parser.error(f"Phase 4 work order already exists; overwrite is prohibited: {work_order_id}")

    issued_at = utc_now()
    work_order: dict[str, Any] = {
        "work_order_id": work_order_id,
        "run_id": scope.get("run_id"),
        "phase": 4,
        "status": "ISSUED",
        "issued_at": issued_at,
        "issued_by": args.issued_by,
        "scope_relative_path": "controller/scope.json",
        "scope_sha256": scope_sha256,
        "controller_gate3_snapshot_relative_path": gate_snapshot_relative,
        "controller_gate3_sha256": gate_sha256,
        "upstream_phase3_work_order_id": phase3_order.get("work_order_id"),
        "upstream_phase3_work_order_relative_path": active_phase3[0].get("relative_path"),
        "upstream_phase3_work_order_sha256": sha256_file(phase3_order_path),
        "included_features": scope.get("migration_scope", {}).get("included_features", []),
        "excluded_features": scope.get("migration_scope", {}).get("excluded_features", []),
        "ownership": stage4_ownership,
        "phase3_henvs": sorted(henv_records, key=lambda item: item["henv_id"]),
        "required_skill": "harmonyos-feature-implementation",
        "business_implementation_allowed": True,
        "mp4_allowed": False,
        "required_return": [
            "stage-04-input-lock.json", "phase-manifest.json", "feature-work-orders/",
            "implementation-ledger.csv", "parity-map.csv", "visual-elements.csv",
            "migration-unit-contracts.json",
            "asset-migration.csv", "capability-implementation.csv", "nativeization-decisions.csv",
            "asset-policy.json", "asset-conversion-contracts.json", "asset-conversions/",
            "environments/", "harmony-project/", "builds/", "evidence/", "evidence-index.csv",
            "attempt-ledger.csv",
            "reviews/", "acceptance-ledger.csv", "rework-tickets.csv", "stage-04-gate-report.json",
            "stage-04-closure-manifest.sha256", "CLOSED",
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

    try:
        atomic_bytes(gate_snapshot_path, gate_path.read_bytes())
        atomic_json(work_order_path, work_order)
        work_order_sha256 = sha256_file(work_order_path)
        registry_rows.append(
            {
                "work_order_id": work_order_id,
                "phase": "4",
                "relative_path": work_order_relative,
                "scope_sha256": scope_sha256,
                "work_order_sha256": work_order_sha256,
                "issued_at": issued_at,
                "issued_by": args.issued_by,
                "status": "ISSUED",
            }
        )
        phase4_ledger[0].update(
            {
                "owner": stage4_ownership["implementation_lead_id"],
                "status": "IN_PROGRESS",
                "updated_at": issued_at,
                "notes": work_order_id,
            }
        )
        atomic_csv(registry_path, registry_fields, registry_rows)
        atomic_csv(ledger_path, ledger_fields, ledger_rows)
    except (OSError, ValueError) as exc:
        parser.error(f"Could not persist Phase 4 work order: {exc}")

    print(json.dumps({
        "work_order_id": work_order_id,
        "work_order": str(work_order_path),
        "work_order_sha256": work_order_sha256,
        "controller_gate3_snapshot": str(gate_snapshot_path),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
