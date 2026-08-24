#!/usr/bin/env python3
"""Shared immutable-work-order helpers for controller Phases 5 and 6."""

from __future__ import annotations

SCRIPT_INTERFACE = "internal-module"
SCRIPT_INTERFACE_REASON = "Imported by Phase 5/6 controller work-order scripts; not a standalone CLI."

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
ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,79}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PROJECT_EXCLUDED_PARTS = {
    ".git", ".idea", ".hvigor", "build", "dist", "coverage", "node_modules",
    "oh_modules", "__pycache__", ".pytest_cache",
}
SECRET_KEY_PARTS = {
    "password", "passwd", "passphrase", "private_key", "privatekey", "secret",
    "token", "credential", "keystore_password", "key_password",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def safe_run_file(run_dir: Path, relative: str, label: str) -> Path:
    pure = PurePosixPath(str(relative))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts or str(pure) in {"", "."}:
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


def external_json_file(value: str, label: str) -> tuple[Path, dict[str, Any]]:
    path_input = Path(value).expanduser().absolute()
    if path_input.is_symlink():
        raise ValueError(f"Symbolic-link {label} is prohibited: {path_input}")
    path = path_input.resolve()
    if not path.is_file():
        raise ValueError(f"Missing {label}: {path}")
    return path, load_json(path)


def load_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if path.is_symlink():
        raise ValueError(f"Symbolic-link controller record is prohibited: {path}")
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            rows = list(reader)
    except FileNotFoundError as exc:
        raise ValueError(f"Missing controller CSV: {path}") from exc
    if not fields:
        raise ValueError(f"CSV has no header: {path}")
    return fields, rows


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


def all_frozen_actor_ids(
    scope: dict[str, Any], run_dir: Path, registry_rows: list[dict[str, str]], before_phase: int
) -> set[str]:
    actors = ownership_actor_ids(
        scope.get("ownership") if isinstance(scope.get("ownership"), dict) else {}
    )
    for row in registry_rows:
        try:
            phase = int(str(row.get("phase", "0")))
        except ValueError:
            continue
        if phase >= before_phase or row.get("status", "").upper() == "SUPERSEDED":
            continue
        path = safe_run_file(run_dir, row.get("relative_path", ""), f"Phase {phase} work order")
        order = load_json(path)
        ownership = order.get("ownership") if isinstance(order.get("ownership"), dict) else {}
        actors.update(ownership_actor_ids(ownership))
    if before_phase > 4:
        feature_registry = run_dir / "phase-04-harmony-implementation" / "feature-work-order-registry.csv"
        if feature_registry.is_file() and not feature_registry.is_symlink():
            _fields, feature_rows = load_csv(feature_registry)
            for row in feature_rows:
                if row.get("status", "").upper() == "SUPERSEDED":
                    continue
                relative = row.get("relative_path") or row.get("work_order_relative_path") or ""
                if not relative:
                    continue
                phase4_root = run_dir / "phase-04-harmony-implementation"
                pure = PurePosixPath(relative)
                if pure.is_absolute() or ".." in pure.parts or not pure.parts:
                    raise ValueError(f"Unsafe Phase 4 feature work-order path: {relative!r}")
                path = phase4_root.joinpath(*pure.parts)
                if path.is_symlink() or not path.is_file():
                    raise ValueError(f"Missing Phase 4 feature work order: {path}")
                order = load_json(path)
                ownership = order.get("ownership") if isinstance(order.get("ownership"), dict) else {}
                actors.update(ownership_actor_ids(ownership))
    return actors


def validate_roles(values: dict[str, str], prior_actors: set[str], label: str) -> None:
    invalid = [key for key, value in values.items() if not ACTOR_RE.fullmatch(value)]
    if invalid:
        raise ValueError(f"Invalid {label} actor ID(s): {invalid}")
    role_values = list(values.values())
    if len(role_values) != len(set(role_values)):
        raise ValueError(f"All {label} actor IDs must be distinct")
    overlaps = sorted(set(role_values) & prior_actors)
    if overlaps:
        raise ValueError(f"{label} actors must differ from all earlier frozen actors: {overlaps}")


def contains_secret_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in SECRET_KEY_PARTS):
                return True
            if contains_secret_key(item):
                return True
    elif isinstance(value, list):
        return any(contains_secret_key(item) for item in value)
    return False


def require_current_gate(run_dir: Path, scope_sha256: str, phase: int) -> tuple[Path, dict[str, Any]]:
    gate_path = safe_run_file(run_dir, "controller/gate-report.json", f"Gate {phase} report")
    gate = load_json(gate_path)
    if (
        gate.get("phase") != phase
        or gate.get("verdict") != "PASS"
        or gate.get("scope_sha256") != scope_sha256
        or gate.get("errors")
    ):
        raise ValueError(f"A current, complete controller Gate {phase} PASS is required")
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("validate_gate.py")),
         "--run-dir", str(run_dir), "--phase", str(phase)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ValueError(f"Gate {phase} baseline changed after its recorded PASS: {detail[:1000]}")
    return gate_path, gate


def active_work_order(
    run_dir: Path, registry_rows: list[dict[str, str]], phase: int
) -> tuple[dict[str, str], Path, dict[str, Any]]:
    matches = [
        row for row in registry_rows
        if row.get("phase") == str(phase) and row.get("status", "").upper() != "SUPERSEDED"
    ]
    if len(matches) != 1:
        raise ValueError(f"Controller must have exactly one active Phase {phase} work order")
    path = safe_run_file(run_dir, matches[0].get("relative_path", ""), f"Phase {phase} work order")
    if matches[0].get("work_order_sha256") != sha256_file(path):
        raise ValueError(f"Registered Phase {phase} work order hash differs")
    return matches[0], path, load_json(path)


def project_snapshot(project: Path) -> tuple[str, list[dict[str, Any]]]:
    if not project.is_dir() or project.is_symlink():
        raise ValueError(f"HarmonyOS project is missing or unsafe: {project}")
    entries: list[dict[str, Any]] = []
    for path in sorted(project.rglob("*")):
        relative = path.relative_to(project)
        if any(part in PROJECT_EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in HarmonyOS project: {path}")
        if path.is_file():
            entries.append({
                "path": relative.as_posix(),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            })
    canonical = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), entries


def file_record(relative_path: str, path: Path, snapshot_relative_path: str = "") -> dict[str, Any]:
    record: dict[str, Any] = {"relative_path": relative_path, "sha256": sha256_file(path)}
    if snapshot_relative_path:
        record["snapshot_relative_path"] = snapshot_relative_path
    return record


def prepare_controller_records(
    run_dir: Path, phase: int
) -> tuple[Path, list[str], list[dict[str, str]], Path, list[str], list[dict[str, str]]]:
    registry_path = run_dir / "controller" / "work-order-registry.csv"
    registry_fields, registry_rows = load_csv(registry_path)
    missing_registry = {
        "work_order_id", "phase", "relative_path", "scope_sha256", "work_order_sha256",
        "issued_at", "issued_by", "status",
    } - set(registry_fields)
    if missing_registry:
        raise ValueError(f"Work-order registry lacks columns: {sorted(missing_registry)}")
    active = [
        row for row in registry_rows
        if row.get("phase") == str(phase) and row.get("status", "").upper() != "SUPERSEDED"
    ]
    if active:
        raise ValueError(f"A Phase {phase} work order is already registered")
    ledger_path = run_dir / "controller" / "task-ledger.csv"
    ledger_fields, ledger_rows = load_csv(ledger_path)
    if len([row for row in ledger_rows if row.get("phase") == str(phase)]) != 1:
        raise ValueError(f"Task ledger must contain exactly one Phase {phase} row")
    return registry_path, registry_fields, registry_rows, ledger_path, ledger_fields, ledger_rows


def persist_work_order(
    *, run_dir: Path, phase: int, scope_sha256: str, issued_by: str, owner: str,
    work_order: dict[str, Any], snapshots: list[tuple[Path, bytes]],
    registry_path: Path, registry_fields: list[str], registry_rows: list[dict[str, str]],
    ledger_path: Path, ledger_fields: list[str], ledger_rows: list[dict[str, str]],
) -> tuple[Path, str]:
    work_order_id = str(work_order["work_order_id"])
    work_order_relative = f"controller/work-orders/{work_order_id}.json"
    work_order_path = run_dir / work_order_relative
    if work_order_path.exists() or any(path.exists() for path, _ in snapshots):
        raise ValueError(f"Work-order output already exists; overwrite is prohibited: {work_order_id}")
    for path, content in snapshots:
        atomic_bytes(path, content)
    atomic_json(work_order_path, work_order)
    digest = sha256_file(work_order_path)
    registry_rows.append({
        "work_order_id": work_order_id,
        "phase": str(phase),
        "relative_path": work_order_relative,
        "scope_sha256": scope_sha256,
        "work_order_sha256": digest,
        "issued_at": str(work_order["issued_at"]),
        "issued_by": issued_by,
        "status": "ISSUED",
    })
    task = next(row for row in ledger_rows if row.get("phase") == str(phase))
    task.update({
        "owner": owner,
        "status": "IN_PROGRESS",
        "updated_at": str(work_order["issued_at"]),
        "notes": work_order_id,
    })
    atomic_csv(registry_path, registry_fields, registry_rows)
    atomic_csv(ledger_path, ledger_fields, ledger_rows)
    return work_order_path, digest
