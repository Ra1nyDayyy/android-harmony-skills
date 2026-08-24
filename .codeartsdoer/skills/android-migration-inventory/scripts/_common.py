#!/usr/bin/env python3
"""Shared deterministic helpers for the Android inventory skill."""

from __future__ import annotations

SCRIPT_INTERFACE = "internal-module"
SCRIPT_INTERFACE_REASON = "Shared deterministic library imported by Phase 2 inventory CLIs."

import csv
import binascii
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Sequence


ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,95}$")
MAX_COMMAND_OUTPUT = 2 * 1024 * 1024
CLI_ERROR_RE = re.compile(r"(?im)^\s*(?:error|failed|failure)\s*:")
RESOLUTION_RE = re.compile(r"(?i)(?:physical|override)?\s*size\s*:\s*(\d+)\s*x\s*(\d+)")
DENSITY_RE = re.compile(r"(?i)(?:physical|override)?\s*density\s*:\s*(\d+)")
FROZEN_ENVIRONMENT_KEYS = (
    "env_id", "is_baseline", "account_id", "account_role", "seed_data_id", "seed_reset_ref",
    "network_profile", "network_conditions_ref", "network_toggle_available", "permissions_profile",
    "device_serial", "emulator_model", "resolution", "density_dpi", "android_api_level",
    "orientation", "locale", "theme", "font_scale", "timezone", "application_id", "app_version",
    "app_build", "build_variant", "source_revision", "apk_sha256",
)
ASSET_INVENTORY_FIELDS = [
    "asset_id", "source_path", "archive_path", "sha256", "asset_type",
    "feature_ids", "page_ids", "state_ids", "created_by", "created_at",
    "reviewed_by", "reviewed_at", "status", "notes",
]
ASSET_TYPES = {
    "RASTER_IMAGE", "VECTOR_IMAGE", "XML_RESOURCE", "FONT", "AUDIO",
    "ANIMATION", "JSON_RESOURCE", "RAW_RESOURCE",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValueError(f"{label} must match {ID_RE.pattern}: {value!r}")
    return value


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"Refusing to replace symbolic-link target: {path}")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def resolve_executable(value: str) -> str:
    candidate = Path(value).expanduser()
    if candidate.parent != Path(".") or candidate.is_absolute():
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            raise ValueError(f"Executable is not available: {candidate}")
        return str(candidate.resolve())
    resolved = shutil.which(value)
    if not resolved:
        raise ValueError(f"Executable is not on PATH: {value}")
    return resolved


def run_command(argv: Sequence[str], timeout: int = 60, env: dict[str, str] | None = None) -> dict[str, Any]:
    started = utc_now()
    start_clock = time.monotonic()
    recorded_argv = list(argv)
    execution_argv = recorded_argv
    if os.name == "nt" and recorded_argv and recorded_argv[0].lower().endswith(".py"):
        execution_argv = [sys.executable, *recorded_argv]
    try:
        completed = subprocess.run(
            execution_argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            timeout=timeout,
            env=env,
            check=False,
        )
        stdout = completed.stdout[:MAX_COMMAND_OUTPUT].decode("utf-8", errors="replace")
        stderr = completed.stderr[:MAX_COMMAND_OUTPUT].decode("utf-8", errors="replace")
        exit_code: int | None = completed.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or b"")[:MAX_COMMAND_OUTPUT].decode("utf-8", errors="replace")
        stderr = (exc.stderr or b"")[:MAX_COMMAND_OUTPUT].decode("utf-8", errors="replace")
        exit_code = None
        timed_out = True
    return {
        "argv": recorded_argv,
        "started_at": started,
        "finished_at": utc_now(),
        "duration_seconds": round(time.monotonic() - start_clock, 3),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
    }


def require_success(record: dict[str, Any], label: str) -> None:
    combined = f"{record.get('stdout', '')}\n{record.get('stderr', '')}"
    if record.get("exit_code") != 0 or record.get("timed_out") is True or CLI_ERROR_RE.search(combined):
        detail = record.get("stderr") or record.get("stdout") or "no command output"
        raise RuntimeError(f"{label} failed: {detail[:1000]}")


def environment_probe_commands(adb_bin: str, device_serial: str) -> list[tuple[str, list[str]]]:
    prefix = [adb_bin, "-s", device_serial, "shell"]
    return [
        ("model", [*prefix, "getprop", "ro.product.model"]),
        ("api_level", [*prefix, "getprop", "ro.build.version.sdk"]),
        ("resolution", [*prefix, "wm", "size"]),
        ("density", [*prefix, "wm", "density"]),
        ("orientation", [*prefix, "dumpsys", "display"]),
        ("locale", [*prefix, "settings", "get", "system", "system_locales"]),
        ("timezone", [*prefix, "getprop", "persist.sys.timezone"]),
        ("font_scale", [*prefix, "settings", "get", "system", "font_scale"]),
        ("theme", [*prefix, "cmd", "uimode", "night"]),
    ]


def parse_resolution(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*x\s*(\d+)\s*", str(value), re.IGNORECASE)
    if not match or int(match.group(1)) < 1 or int(match.group(2)) < 1:
        raise ValueError(f"Frozen resolution is invalid: {value!r}")
    return int(match.group(1)), int(match.group(2))


def verify_environment_probe(records: Any, env_spec: dict[str, Any]) -> None:
    """Verify recorded ADB probes against measurable frozen ENV fields."""
    serial = str(env_spec.get("device_serial", ""))
    expected_commands = environment_probe_commands("<adb>", serial)
    if not isinstance(records, list) or len(records) != len(expected_commands):
        raise ValueError("Environment verification command set is incomplete")
    by_label: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("label"), str):
            raise ValueError("Environment verification contains a malformed command")
        label = record["label"]
        if label in by_label:
            raise ValueError(f"Duplicate environment verification command: {label}")
        require_success(record, f"environment {label}")
        by_label[label] = record
    for label, expected in expected_commands:
        record = by_label.get(label)
        argv = record.get("argv") if record else None
        if not isinstance(argv, list) or len(argv) < 2 or argv[1:] != expected[1:]:
            raise ValueError(f"Environment verification command differs: {label}")

    def output(label: str) -> str:
        return str(by_label[label].get("stdout", "")).strip()

    normalize = lambda value: re.sub(r"[^a-z0-9]", "", str(value).lower())
    if normalize(output("model")) != normalize(env_spec.get("emulator_model", "")):
        raise ValueError("Actual device model differs from frozen environment")
    try:
        if int(output("api_level")) != int(env_spec.get("android_api_level")):
            raise ValueError("Actual Android API level differs from frozen environment")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("Actual"):
            raise
        raise ValueError("Android API verification output is invalid") from exc
    resolution_matches = RESOLUTION_RE.findall(output("resolution"))
    if not resolution_matches or tuple(map(int, resolution_matches[-1])) != parse_resolution(str(env_spec.get("resolution", ""))):
        raise ValueError("Actual screen resolution differs from frozen environment")
    density_matches = DENSITY_RE.findall(output("density"))
    if not density_matches or int(density_matches[-1]) != int(env_spec.get("density_dpi")):
        raise ValueError("Actual screen density differs from frozen environment")
    orientation_match = re.search(r"(?i)SurfaceOrientation\s*:\s*([0-3])", output("orientation"))
    if not orientation_match:
        orientation_match = re.search(r"(?i)mCurrentOrientation\s*=\s*([0-3])", output("orientation"))
    expected_orientation = str(env_spec.get("orientation", "")).lower()
    actual_orientation = "portrait" if orientation_match and orientation_match.group(1) in {"0", "2"} else "landscape"
    if not orientation_match or actual_orientation != expected_orientation:
        raise ValueError("Actual screen orientation differs from frozen environment")
    actual_locale = output("locale").replace("_", "-").lower()
    if actual_locale != str(env_spec.get("locale", "")).replace("_", "-").lower():
        raise ValueError("Actual locale differs from frozen environment")
    if output("timezone") != str(env_spec.get("timezone", "")):
        raise ValueError("Actual timezone differs from frozen environment")
    try:
        if abs(float(output("font_scale")) - float(env_spec.get("font_scale"))) > 0.001:
            raise ValueError("Actual font scale differs from frozen environment")
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith("Actual"):
            raise
        raise ValueError("Font-scale verification output is invalid") from exc
    theme_output = output("theme").lower()
    actual_theme = "dark" if re.search(r"(?:night mode\s*:\s*)?(?:yes|true|2)\b", theme_output) else "light"
    if actual_theme != str(env_spec.get("theme", "")).lower():
        raise ValueError("Actual theme differs from frozen environment")


def verify_environment_attestation(
    workspace: Path, env_spec: dict[str, Any], phase_manifest: dict[str, Any]
) -> tuple[dict[str, Any], str]:
    env_id = str(env_spec.get("env_id", ""))
    path = workspace / "environment-attestations" / f"{env_id}.json"
    assert_no_symlink(path, workspace)
    record = load_json(path)
    ownership = phase_manifest.get("ownership") if isinstance(phase_manifest.get("ownership"), dict) else {}
    expected_frozen = {key: env_spec.get(key) for key in FROZEN_ENVIRONMENT_KEYS}
    if (
        not isinstance(record, dict)
        or record.get("schema_version") != 1
        or record.get("attestation_type") != "phase-02-environment-readiness"
        or record.get("status") != "ATTESTED"
        or record.get("run_id") != phase_manifest.get("run_id")
        or record.get("env_id") != env_id
        or record.get("inventory_lead_id") != ownership.get("inventory_lead_id")
        or any(record.get(field) is not True for field in (
            "account_ready", "seed_ready", "network_ready", "permissions_ready"
        ))
        or record.get("scope_sha256") != phase_manifest.get("scope_sha256")
        or record.get("environment_registry_sha256") != phase_manifest.get("environment_registry_sha256")
        or record.get("environment_sha256") != canonical_json_sha256(env_spec)
        or record.get("frozen_environment") != expected_frozen
    ):
        raise ValueError(f"Environment readiness attestation differs from frozen ENV-ID: {env_id}")
    timestamp = str(record.get("attested_at", ""))
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Environment readiness attestation timestamp is invalid: {env_id}") from exc
    return record, sha256_file(path)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"Refusing to replace symbolic-link target: {path}")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except FileNotFoundError as exc:
        raise ValueError(f"Missing CSV: {path}") from exc


def csv_fieldnames(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle).fieldnames or [])
    except FileNotFoundError as exc:
        raise ValueError(f"Missing CSV: {path}") from exc


def parse_json_string_array(value: str, label: str, *, allow_empty: bool = False) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be a JSON string array") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) and item for item in parsed):
        raise ValueError(f"{label} must be a JSON string array")
    if not allow_empty and not parsed:
        raise ValueError(f"{label} must not be empty")
    if parsed != sorted(set(parsed)):
        raise ValueError(f"{label} must be sorted and contain no duplicates")
    return parsed


def verify_asset_chain(
    workspace: Path,
    phase_manifest: dict[str, Any],
    inventory_rows: list[dict[str, str]] | None = None,
    *,
    require_reviewed: bool = False,
) -> list[dict[str, str]]:
    """Verify the immutable Phase 2 asset package and its inventory/reference chain."""
    asset_inventory_path = workspace / "asset-inventory.csv"
    package = workspace / "asset-package"
    files_root = package / "files"
    manifest_path = package / "manifest.sha256"
    committed_path = package / "COMMITTED"
    for path in (asset_inventory_path, package, files_root, manifest_path, committed_path):
        assert_no_symlink(path, workspace)
    if csv_fieldnames(asset_inventory_path) != ASSET_INVENTORY_FIELDS:
        raise ValueError("asset-inventory.csv header differs from the frozen asset contract")
    asset_rows = read_csv(asset_inventory_path)
    if not package.is_dir() or not files_root.is_dir() or not manifest_path.is_file() or not committed_path.is_file():
        raise ValueError("Asset package must contain files/, manifest.sha256, and COMMITTED")
    if committed_path.read_text(encoding="utf-8") != sha256_file(manifest_path) + "\n":
        raise ValueError("Asset package COMMITTED does not bind manifest.sha256")

    manifest_entries: dict[str, str] = {}
    manifest_lines_value = manifest_path.read_text(encoding="utf-8").splitlines()
    if manifest_lines_value != sorted(manifest_lines_value, key=lambda line: line.split("  ", 1)[-1]):
        raise ValueError("Asset package manifest entries are not sorted")
    for number, line in enumerate(manifest_lines_value, start=1):
        if "  " not in line:
            raise ValueError(f"Malformed asset manifest line {number}")
        digest, relative_value = line.split("  ", 1)
        pure = PurePosixPath(relative_value)
        if (
            not SHA256_RE.fullmatch(digest)
            or pure.is_absolute()
            or len(pure.parts) != 3
            or pure.parts[0] != "files"
            or any(part in {"", ".", ".."} for part in pure.parts)
            or relative_value in manifest_entries
        ):
            raise ValueError(f"Unsafe or duplicate asset manifest entry at line {number}")
        artifact = safe_workspace_path(package, relative_value)
        if not artifact.is_file() or sha256_file(artifact) != digest:
            raise ValueError(f"Asset manifest hash mismatch: {relative_value}")
        manifest_entries[relative_value] = digest
    actual_files = {
        path.relative_to(package).as_posix()
        for path in files_root.rglob("*")
        if path.is_file()
    }
    if actual_files != set(manifest_entries):
        raise ValueError("Asset package manifest does not exactly cover archived files")
    for path in package.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in the asset package: {path}")
        if path.is_file() and path.relative_to(package).as_posix() not in {
            *manifest_entries, "manifest.sha256", "COMMITTED",
        }:
            raise ValueError(f"Unexpected file in asset package: {path}")

    ownership = phase_manifest.get("ownership") if isinstance(phase_manifest.get("ownership"), dict) else {}
    creator = ownership.get("code_map_agent_id")
    reviewer = ownership.get("coverage_checker_id")
    included = set(phase_manifest.get("included_features", []))
    project_root = Path(str(phase_manifest.get("android_project_root", ""))).resolve()
    assets_by_id: dict[str, dict[str, str]] = {}
    for row in asset_rows:
        asset_id = validate_id(row.get("asset_id", ""), "Asset-ID")
        if asset_id == "NONE_FOUND" or asset_id in assets_by_id:
            raise ValueError(f"Missing, sentinel, or duplicate Asset-ID: {asset_id!r}")
        source_relative = PurePosixPath(row.get("source_path", ""))
        archive_relative = PurePosixPath(row.get("archive_path", ""))
        if (
            any(ord(character) < 32 for character in row.get("source_path", ""))
            or "\\" in row.get("source_path", "")
            or source_relative.is_absolute()
            or not source_relative.parts
            or any(part in {"", ".", ".."} for part in source_relative.parts)
            or archive_relative.as_posix() != f"asset-package/files/{asset_id}/{source_relative.name}"
        ):
            raise ValueError(f"Unsafe or non-canonical asset path: {asset_id}")
        source = project_root / Path(*source_relative.parts)
        assert_no_symlink(source, project_root)
        archive = safe_workspace_path(workspace, archive_relative.as_posix())
        digest = row.get("sha256", "")
        if (
            not source.is_file()
            or not archive.is_file()
            or not SHA256_RE.fullmatch(digest)
            or sha256_file(source) != digest
            or sha256_file(archive) != digest
            or manifest_entries.get(archive.relative_to(package).as_posix()) != digest
        ):
            raise ValueError(f"Asset source/archive/hash chain differs: {asset_id}")
        if row.get("asset_type") not in ASSET_TYPES:
            raise ValueError(f"Unsupported asset_type for {asset_id}: {row.get('asset_type')!r}")
        feature_ids = parse_json_string_array(row.get("feature_ids", ""), f"{asset_id}.feature_ids")
        page_ids = parse_json_string_array(row.get("page_ids", ""), f"{asset_id}.page_ids")
        state_ids = parse_json_string_array(row.get("state_ids", ""), f"{asset_id}.state_ids")
        for value, label in ((*[(item, "Feature-ID") for item in feature_ids],
                              *[(item, "Page-ID") for item in page_ids],
                              *[(item, "State-ID") for item in state_ids])):
            validate_id(value, label)
        if not set(feature_ids) <= included:
            raise ValueError(f"Asset is outside frozen feature scope: {asset_id}")
        if row.get("created_by") != creator:
            raise ValueError(f"Asset was not archived by the frozen code-map agent: {asset_id}")
        try:
            datetime.fromisoformat(row.get("created_at", "").replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Asset created_at is invalid: {asset_id}") from exc
        if require_reviewed:
            if row.get("status") != "REVIEWED" or row.get("reviewed_by") != reviewer:
                raise ValueError(f"Asset was not reviewed by the frozen coverage checker: {asset_id}")
            try:
                datetime.fromisoformat(row.get("reviewed_at", "").replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"Asset reviewed_at is invalid: {asset_id}") from exc
        elif row.get("status") == "ARCHIVED":
            if row.get("reviewed_by") or row.get("reviewed_at"):
                raise ValueError(f"Unreviewed asset has reviewer fields: {asset_id}")
        elif row.get("status") == "REVIEWED":
            if row.get("reviewed_by") != reviewer or not row.get("reviewed_at"):
                raise ValueError(f"Asset has invalid reviewer fields: {asset_id}")
        else:
            raise ValueError(f"Invalid asset lifecycle status: {asset_id}")
        assets_by_id[asset_id] = row
    if len(asset_rows) != len(manifest_entries):
        raise ValueError("Asset inventory and package are not one-to-one")

    if inventory_rows is not None:
        referenced: set[str] = set()
        for inventory_row in inventory_rows:
            if inventory_row.get("row_status") == "SUPERSEDED":
                continue
            inventory_id = inventory_row.get("inventory_id", "<unknown>")
            refs = parse_json_string_array(inventory_row.get("asset_ids", ""), f"{inventory_id}.asset_ids")
            if "NONE_FOUND" in refs:
                if refs != ["NONE_FOUND"]:
                    raise ValueError(f"{inventory_id}: NONE_FOUND cannot be mixed with Asset-IDs")
                continue
            for asset_id in refs:
                asset = assets_by_id.get(asset_id)
                if asset is None:
                    raise ValueError(f"{inventory_id}: unknown Asset-ID {asset_id}")
                if inventory_row.get("feature_id") not in parse_json_string_array(asset["feature_ids"], f"{asset_id}.feature_ids"):
                    raise ValueError(f"{inventory_id}: asset Feature-ID coverage differs for {asset_id}")
                if inventory_row.get("page_id") not in parse_json_string_array(asset["page_ids"], f"{asset_id}.page_ids"):
                    raise ValueError(f"{inventory_id}: asset Page-ID coverage differs for {asset_id}")
                if inventory_row.get("state_id") not in parse_json_string_array(asset["state_ids"], f"{asset_id}.state_ids"):
                    raise ValueError(f"{inventory_id}: asset State-ID coverage differs for {asset_id}")
                referenced.add(asset_id)
        if referenced != set(assets_by_id):
            raise ValueError("Every archived asset must be referenced by at least one active inventory row")
    return asset_rows


@contextmanager
def exclusive_lock(lock_path: Path, timeout: float = 10.0) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(descriptor, f"pid={os.getpid()} created={utc_now()}\n".encode())
        except FileExistsError:
            try:
                lock_age = time.time() - lock_path.stat().st_mtime
                content = lock_path.read_text(encoding="utf-8")
                match = re.search(r"pid=(\d+)", content)
                owner_alive = True
                if match:
                    owner_pid = int(match.group(1))
                    if os.name == "nt":
                        import ctypes

                        process_query_limited_information = 0x1000
                        handle = ctypes.windll.kernel32.OpenProcess(
                            process_query_limited_information, False, owner_pid
                        )
                        if handle:
                            ctypes.windll.kernel32.CloseHandle(handle)
                            owner_alive = True
                        else:
                            owner_alive = ctypes.windll.kernel32.GetLastError() == 5
                    else:
                        try:
                            os.kill(owner_pid, 0)
                        except ProcessLookupError:
                            owner_alive = False
                        except PermissionError:
                            owner_alive = True
                if not owner_alive or (match is None and lock_age > 300):
                    lock_path.unlink()
                    continue
            except (FileNotFoundError, OSError, ValueError):
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Timed out waiting for lock: {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            lock_path.unlink()
        except OSError:
            pass


def append_csv_locked(path: Path, fieldnames: list[str], row: dict[str, Any]) -> None:
    lock_path = path.with_suffix(path.suffix + ".lock")
    with exclusive_lock(lock_path):
        rows = read_csv(path) if path.exists() and path.stat().st_size else []
        write_csv(path, fieldnames, [*rows, row])


def assert_valid_png(path: Path) -> tuple[int, int]:
    """Validate a complete, non-interlaced PNG and return its pixel dimensions."""
    if not path.is_file() or path.stat().st_size < 45:
        raise ValueError(f"Missing or empty PNG: {path}")
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Invalid PNG signature: {path}")
    offset = 8
    chunks: list[tuple[bytes, bytes]] = []
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError(f"Truncated PNG chunk: {path}")
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError(f"Truncated PNG payload: {path}")
        payload = data[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length:end])[0]
        actual_crc = binascii.crc32(chunk_type + payload) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise ValueError(f"PNG CRC mismatch: {path}")
        chunks.append((chunk_type, payload))
        offset = end
        if chunk_type == b"IEND":
            break
    if offset != len(data) or not chunks or chunks[0][0] != b"IHDR" or chunks[-1][0] != b"IEND":
        raise ValueError(f"PNG chunk order or trailing data is invalid: {path}")
    if len([kind for kind, _ in chunks if kind == b"IHDR"]) != 1:
        raise ValueError(f"PNG must contain exactly one IHDR: {path}")
    header = chunks[0][1]
    if len(header) != 13:
        raise ValueError(f"PNG IHDR length is invalid: {path}")
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(">IIBBBBB", header)
    if width < 1 or height < 1 or compression != 0 or filtering != 0 or interlace != 0:
        raise ValueError(f"PNG dimensions or encoding are unsupported: {path}")
    allowed_depths = {
        0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16},
    }
    if color_type not in allowed_depths or bit_depth not in allowed_depths[color_type]:
        raise ValueError(f"PNG color type/bit depth is invalid: {path}")
    idat = b"".join(payload for kind, payload in chunks if kind == b"IDAT")
    if not idat:
        raise ValueError(f"PNG has no IDAT data: {path}")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    row_bytes = (width * channels * bit_depth + 7) // 8
    expected_size = height * (row_bytes + 1)
    try:
        decompressor = zlib.decompressobj()
        pixels = decompressor.decompress(idat, expected_size + 1)
        pixels += decompressor.flush()
    except zlib.error as exc:
        raise ValueError(f"PNG image data is corrupt: {path}: {exc}") from exc
    if not decompressor.eof or decompressor.unused_data or len(pixels) != expected_size:
        raise ValueError(f"PNG image data length is invalid: {path}")
    return width, height


def verify_phase_identity(workspace: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Bind mutable Phase 2 files directly to the controller-owned frozen scope."""
    scope_snapshot_path = workspace / "controller-scope.snapshot.json"
    controller_scope_path = workspace.parent / "controller" / "scope.json"
    run_manifest_path = workspace.parent / "run-manifest.json"
    environment_path = workspace / "environments.json"
    scope_digest = str(manifest.get("scope_sha256", ""))
    if not scope_snapshot_path.is_file() or not controller_scope_path.is_file():
        raise ValueError("Controller scope or Phase 2 scope snapshot is missing")
    if sha256_file(scope_snapshot_path) != scope_digest or sha256_file(controller_scope_path) != scope_digest:
        raise ValueError("Phase 2 is not bound to the current controller-owned scope")
    if not run_manifest_path.is_file() or sha256_file(run_manifest_path) != manifest.get("run_manifest_sha256"):
        raise ValueError("Phase 2 is not bound to the immutable run manifest")
    if not environment_path.is_file() or sha256_file(environment_path) != manifest.get("environment_registry_sha256"):
        raise ValueError("Frozen environment registry changed")
    scope = load_json(controller_scope_path)
    snapshot = load_json(scope_snapshot_path)
    if snapshot != scope:
        raise ValueError("Controller scope snapshot differs from the controller-owned scope")
    android = scope.get("android") if isinstance(scope.get("android"), dict) else {}
    migration_scope = scope.get("migration_scope") if isinstance(scope.get("migration_scope"), dict) else {}
    expected = {
        "run_id": scope.get("run_id"),
        "android_project_root": str(Path(str(android.get("project_root", ""))).expanduser().resolve()),
        "apk_path": str(Path(str(android.get("apk_path", ""))).expanduser().resolve()),
        "apk_sha256": android.get("apk_sha256"),
        "source_revision": android.get("source_revision"),
        "ownership": scope.get("ownership"),
        "included_features": migration_scope.get("included_features"),
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"Phase manifest {key} differs from the controller-owned scope")
    apk_path = Path(expected["apk_path"])
    if not apk_path.is_file() or sha256_file(apk_path) != expected["apk_sha256"]:
        raise ValueError("Frozen APK changed after Phase 2 initialization")
    project_root = Path(expected["android_project_root"])
    if not project_root.is_dir():
        raise ValueError("Frozen Android project root no longer exists")
    registry = load_json(environment_path)
    scope_environments = scope.get("environments") if isinstance(scope.get("environments"), list) else []
    frozen_environments = registry.get("environments") if isinstance(registry, dict) else []
    scope_by_id = {item.get("env_id"): item for item in scope_environments if isinstance(item, dict)}
    frozen_by_id = {item.get("env_id"): item for item in frozen_environments if isinstance(item, dict)}
    if not scope_by_id or set(scope_by_id) != set(frozen_by_id):
        raise ValueError("Environment registry does not exactly match the controller scope")
    baseline_ids = [item.get("env_id") for item in scope_environments if isinstance(item, dict) and item.get("is_baseline") is True]
    if len(baseline_ids) != 1 or registry.get("baseline_env_id") != baseline_ids[0]:
        raise ValueError("Environment registry baseline differs from the controller scope")
    app_identity = {
        "application_id": android.get("application_id"), "app_version": android.get("app_version"),
        "app_build": android.get("app_build"), "build_variant": android.get("build_variant"),
        "source_revision": android.get("source_revision"), "apk_sha256": android.get("apk_sha256"),
    }
    for env_id, scoped in scope_by_id.items():
        frozen = frozen_by_id[env_id]
        if any(frozen.get(key) != value for key, value in scoped.items()):
            raise ValueError(f"Frozen environment {env_id} differs from the controller scope")
        if any(frozen.get(key) != value for key, value in app_identity.items()) or frozen.get("status") != "FROZEN":
            raise ValueError(f"Frozen environment {env_id} has a different app/source identity")
    return scope


def assert_valid_json(path: Path) -> None:
    value = load_json(path)
    if value in (None, [], {}):
        raise ValueError(f"JSON artifact is empty: {path}")


def assert_no_symlink(path: Path, root: Path) -> None:
    root_abs = root.absolute()
    path_abs = path.absolute()
    try:
        relative = path_abs.relative_to(root_abs)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {path}") from exc
    if any(part in {"..", "."} for part in relative.parts):
        raise ValueError(f"Path escapes workspace: {path}")
    try:
        root_resolved = root_abs.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"Workspace does not exist: {root}") from exc
    candidate_resolved = path_abs.resolve(strict=False)
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"Resolved path escapes workspace: {path}") from exc
    current = root_abs
    if current.is_symlink():
        raise ValueError(f"Workspace must not be a symlink: {root}")
    for part in relative.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in evidence paths: {current}")


def safe_workspace_path(root: Path, relative_value: str) -> Path:
    """Resolve a strictly relative, traversal-free path below a workspace."""
    relative = Path(relative_value)
    if relative.is_absolute() or not relative.parts or any(part in {"..", "."} for part in relative.parts):
        raise ValueError(f"Unsafe workspace-relative path: {relative_value!r}")
    target = root / relative
    assert_no_symlink(target, root)
    return target


def manifest_lines(directory: Path, names: list[str]) -> str:
    lines = []
    for name in names:
        artifact = directory / name
        lines.append(f"{sha256_file(artifact)}  {name}")
    return "\n".join(lines) + "\n"
