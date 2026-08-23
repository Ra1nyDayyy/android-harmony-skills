#!/usr/bin/env python3
"""Issue an immutable Phase 2 Android-inventory work order from a PASS gate."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def mark_phase2_in_progress(run_dir: Path, issued_at: str, work_order_id: str) -> None:
    path = run_dir / "controller" / "task-ledger.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    matches = [row for row in rows if row.get("phase") == "2"]
    if len(matches) != 1 or not fields:
        raise ValueError("Task ledger has no unique Phase 2 row")
    matches[0].update({"status": "IN_PROGRESS", "updated_at": issued_at, "notes": work_order_id})
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


def register_work_order(run_dir: Path, record: dict[str, str]) -> None:
    path = run_dir / "controller" / "work-order-registry.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    if not fields or any(row.get("work_order_id") == record["work_order_id"] for row in rows):
        raise ValueError("Work-order registry is missing or already contains this ID")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
            writer.writeheader()
            writer.writerows([*rows, record])
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--issued-by", required=True)
    args = parser.parse_args()

    run_input = Path(args.run_dir).expanduser().absolute()
    if run_input.is_symlink():
        parser.error("Migration run must not be a symbolic link")
    run_dir = run_input.resolve()
    try:
        scope_path = run_dir / "controller" / "scope.json"
        scope = load_json(scope_path)
        gate = load_json(run_dir / "controller" / "gate-report.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    scope_sha256 = sha256_file(scope_path)
    if gate.get("phase") != 1 or gate.get("verdict") != "PASS" or gate.get("scope_sha256") != scope_sha256:
        parser.error("A current Phase 1 PASS gate is required")
    recheck = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("validate_gate.py")), "--run-dir", str(run_dir), "--phase", "1"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if recheck.returncode != 0:
        parser.error("Phase 1 baseline changed after its recorded PASS; refusing to issue a work order")
    ownership = scope.get("ownership", {})
    if args.issued_by != ownership.get("migration_controller_id"):
        parser.error("--issued-by must equal the frozen migration controller")
    suffix = scope_sha256[:12].upper()
    work_order_id = f"WO-PHASE-02-{suffix}"
    output = run_dir / "controller" / "work-orders" / f"{work_order_id}.json"
    if output.exists():
        parser.error(f"Work order already exists; overwrite is prohibited: {output}")
    baseline = [env["env_id"] for env in scope.get("environments", []) if env.get("is_baseline") is True]
    issued_at = utc_now()
    work_order = {
        "work_order_id": work_order_id,
        "run_id": scope.get("run_id"),
        "phase": 2,
        "status": "ISSUED",
        "issued_at": issued_at,
        "issued_by": args.issued_by,
        "scope_relative_path": "controller/scope.json",
        "scope_sha256": scope_sha256,
        "source_revision": scope.get("android", {}).get("source_revision"),
        "apk_sha256": scope.get("android", {}).get("apk_sha256"),
        "baseline_env_id": baseline[0] if len(baseline) == 1 else None,
        "included_features": scope.get("migration_scope", {}).get("included_features", []),
        "excluded_features": scope.get("migration_scope", {}).get("excluded_features", []),
        "ownership": ownership,
        "required_skill": "android-migration-inventory",
        "runtime_ui_tool": "android-cli",
        "layout_inspector_allowed": False,
        "mp4_allowed": False,
        "inventory_row_formula": "Feature-ID x Page-ID x State-ID x ENV-ID x Evidence-ID",
        "required_return": [
            "environments.json", "coverage-ledger.csv", "catalogs/", "inventory.csv",
            "environment-attestations/", "evidence-index.csv", "acceptance-registry.csv", "evidence/",
            "rechecks.csv", "closure-report.json",
            "evidence-anchors.snapshot.csv", "closure-manifest.sha256", "CLOSED",
        ],
    }
    atomic_json(output, work_order)
    try:
        register_work_order(
            run_dir,
            {
                "work_order_id": work_order_id,
                "phase": "2",
                "relative_path": output.relative_to(run_dir).as_posix(),
                "scope_sha256": scope_sha256,
                "work_order_sha256": sha256_file(output),
                "issued_at": issued_at,
                "issued_by": args.issued_by,
                "status": "ISSUED",
            },
        )
        mark_phase2_in_progress(run_dir, issued_at, work_order_id)
    except (OSError, ValueError) as exc:
        parser.error(f"Work order was issued but task-ledger update failed: {exc}")
    print(json.dumps({"work_order_id": work_order_id, "work_order": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
