#!/usr/bin/env python3
"""Shared deterministic helpers for HarmonyOS Phase 4 implementation."""

from __future__ import annotations

SCRIPT_INTERFACE = "internal-module"
SCRIPT_INTERFACE_REASON = "Shared deterministic library imported by Phase 4 implementation CLIs."

import csv
import binascii
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence


ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,95}$")
ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
PLACEHOLDER_RE = re.compile(r"^__.+__$")
ERROR_OUTPUT_RE = re.compile(r"(?im)^\s*(?:error|failed|failure)\s*:")
MAX_COMMAND_OUTPUT = 4 * 1024 * 1024
PROJECT_EXCLUDED_PARTS = {
    ".git", ".idea", ".hvigor", "build", "dist", "coverage", "node_modules",
    "oh_modules", "__pycache__", ".pytest_cache",
}
SECRET_KEY_RE = re.compile(
    r"(?i)(password|passwd|passphrase|token|secret|private[_-]?key|storepass|keypass)"
)
PHASE4_CATEGORY_ORDER = (
    "TOOLCHAIN", "CLEAN_BUILD", "BUNDLE_CHECK", "SIGNING_CHECK",
    "DEVICE_CHECK", "CLEAN_INSTALL", "SEED_RESET", "NETWORK_PROFILE",
    "PERMISSION_PROFILE", "LAUNCH", "NAVIGATE", "BUSINESS_ASSERT",
    "SCREENSHOT_CAPTURE", "UITEST_SNAPSHOT_CAPTURE",
)
PHASE4_CATEGORY_SET = set(PHASE4_CATEGORY_ORDER)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValueError(f"{label} must match {ID_RE.pattern}: {value!r}")
    return value


def validate_actor(value: str, label: str) -> str:
    if not isinstance(value, str) or not ACTOR_RE.fullmatch(value):
        raise ValueError(f"{label} must be a nonempty actor ID: {value!r}")
    return value


def is_unresolved(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, str):
        stripped = value.strip()
        return bool(PLACEHOLDER_RE.fullmatch(stripped)) or stripped.upper() in {
            "PENDING", "PENDING_CONFIRMATION", "UNKNOWN", "TBD", "TODO",
        }
    if isinstance(value, list):
        return not value or any(is_unresolved(item) for item in value)
    if isinstance(value, dict):
        return any(is_unresolved(item) for item in value.values())
    return False


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


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
        except FileNotFoundError:
            pass


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csv_fieldnames(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            return next(reader)
    except (FileNotFoundError, StopIteration) as exc:
        raise ValueError(f"Missing or empty CSV template: {path}") from exc


def read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ValueError(f"CSV has no header: {path}")
            return list(reader)
    except FileNotFoundError as exc:
        raise ValueError(f"Missing CSV: {path}") from exc


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


@contextmanager
def exclusive_lock(lock_path: Path, timeout: float = 15.0) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(descriptor, f"pid={os.getpid()} created={utc_now()}\n".encode())
        except FileExistsError:
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
        except FileNotFoundError:
            pass


def append_csv_atomic(path: Path, fieldnames: list[str], row: dict[str, Any]) -> None:
    with exclusive_lock(path.with_suffix(path.suffix + ".lock")):
        rows = read_csv(path) if path.exists() else []
        rows.append({field: row.get(field, "") for field in fieldnames})
        write_csv(path, fieldnames, rows)


def split_multi(value: str) -> list[str]:
    if not value:
        return []
    stripped = value.strip()
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON array: {value}") from exc
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise ValueError(f"Expected a string array: {value}")
        return [item for item in parsed if item]
    return [item.strip() for item in value.split("|") if item.strip()]


def join_multi(values: Sequence[str]) -> str:
    return json.dumps(sorted(set(values)), ensure_ascii=False, separators=(",", ":"))


def safe_relative_path(root: Path, relative: str, label: str, must_exist: bool = True) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"{label} must stay inside {root}: {relative}")
    root_resolved = root.resolve()
    resolved = (root_resolved / candidate).resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"{label} escapes {root}: {relative}") from exc
    current = root_resolved
    for part in candidate.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(f"Symbolic links are prohibited: {current}")
    if must_exist and not resolved.exists():
        raise ValueError(f"Missing {label}: {resolved}")
    return resolved


def copy_tree_without_generated(source: Path, target: Path) -> None:
    for path in source.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in accepted project: {path}")
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(*sorted(PROJECT_EXCLUDED_PARTS)),
    )


def build_project_snapshot(project: Path) -> dict[str, Any]:
    if not project.is_dir():
        raise ValueError(f"Missing HarmonyOS project: {project}")
    entries: list[dict[str, Any]] = []
    for path in sorted(project.rglob("*")):
        relative = path.relative_to(project)
        if any(part in PROJECT_EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in project snapshot: {path}")
        if path.is_file():
            entries.append({"path": relative.as_posix(), "sha256": sha256_file(path), "size": path.stat().st_size})
    entries.sort(key=lambda item: item["path"])
    canonical = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return {"entry_count": len(entries), "entries": entries, "snapshot_sha256": sha256_text(canonical)}


def closure_excluded(relative: Path) -> bool:
    if relative.as_posix() in {
        "stage-04-gate-report.json", "stage-04-closure-manifest.sha256", "CLOSED",
    }:
        return True
    if any(part in {".locks", ".staging", "__pycache__", ".pytest_cache"} for part in relative.parts):
        return True
    if relative.suffix in {".tmp", ".pyc"} or relative.name.endswith(".lock"):
        return True
    if relative.parts and relative.parts[0] == "harmony-project" and any(
        part in PROJECT_EXCLUDED_PARTS for part in relative.parts[1:]
    ):
        return True
    return False


def build_closure_snapshot(workspace: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace)
        if closure_excluded(relative):
            continue
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in Phase 4 closure: {path}")
        if path.is_file():
            entries.append({"path": relative.as_posix(), "sha256": sha256_file(path), "size": path.stat().st_size})
    canonical = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return {"entry_count": len(entries), "entries": entries, "snapshot_sha256": sha256_text(canonical)}


def verify_snapshot(root: Path, snapshot: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_entries = snapshot.get("entries")
    if not isinstance(expected_entries, list):
        return ["Snapshot entries are missing"]
    actual: list[dict[str, Any]] = []
    for entry in expected_entries:
        if not isinstance(entry, dict):
            errors.append("Snapshot contains a non-object entry")
            continue
        try:
            path = safe_relative_path(root, str(entry.get("path", "")), "snapshot file")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not path.is_file():
            errors.append(f"Snapshot path is not a file: {path}")
            continue
        actual.append({"path": str(entry.get("path")), "sha256": sha256_file(path), "size": path.stat().st_size})
        if actual[-1]["sha256"] != entry.get("sha256") or actual[-1]["size"] != entry.get("size"):
            errors.append(f"Snapshot file changed: {path}")
    canonical = json.dumps(actual, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if sha256_text(canonical) != snapshot.get("snapshot_sha256"):
        errors.append("Snapshot digest differs")
    return errors


def png_dimensions(path: Path) -> tuple[int, int]:
    """Validate a complete non-interlaced PNG, including CRC and IDAT payload."""
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError(f"Missing PNG: {path}") from exc
    except OSError as exc:
        raise ValueError(f"Cannot read PNG {path}: {exc}") from exc
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Invalid PNG signature: {path}")
    offset = 8
    ihdr: bytes | None = None
    idat = bytearray()
    saw_iend = False
    chunk_index = 0
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
        actual_crc = binascii.crc32(payload, binascii.crc32(chunk_type)) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise ValueError(f"PNG chunk CRC mismatch: {path}")
        if chunk_index == 0 and chunk_type != b"IHDR":
            raise ValueError(f"PNG IHDR is not the first chunk: {path}")
        if chunk_type == b"IHDR":
            if ihdr is not None or length != 13:
                raise ValueError(f"Invalid PNG IHDR: {path}")
            ihdr = payload
        elif chunk_type == b"IDAT":
            idat.extend(payload)
        elif chunk_type == b"IEND":
            if length != 0:
                raise ValueError(f"Invalid PNG IEND: {path}")
            saw_iend = True
            offset = end
            break
        offset = end
        chunk_index += 1
    if not ihdr or not idat or not saw_iend or offset != len(data):
        raise ValueError(f"PNG is incomplete or has trailing bytes: {path}")
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    valid_depths = {
        0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8},
        4: {8, 16}, 6: {8, 16},
    }
    if (
        width <= 0 or height <= 0 or width * height > 100_000_000
        or compression != 0 or filtering != 0 or interlace != 0
        or channels is None or bit_depth not in valid_depths[color_type]
    ):
        raise ValueError(f"Unsupported or invalid PNG IHDR: {path}")
    expected_size = height * (((width * channels * bit_depth + 7) // 8) + 1)
    try:
        decoded = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ValueError(f"Invalid PNG IDAT payload: {path}: {exc}") from exc
    if len(decoded) != expected_size:
        raise ValueError(f"PNG decompressed payload length differs: {path}")
    return width, height


def validate_hap(path: Path) -> None:
    """Require a structurally valid HAP ZIP with a module configuration."""
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > 100_000:
                raise ValueError(f"HAP ZIP is empty or has too many entries: {path}")
            if sum(item.file_size for item in entries) > 2 * 1024 * 1024 * 1024:
                raise ValueError(f"HAP uncompressed size exceeds the verification limit: {path}")
            for item in entries:
                member = Path(item.filename)
                if member.is_absolute() or ".." in member.parts:
                    raise ValueError(f"HAP contains an unsafe member: {item.filename}")
            if archive.testzip() is not None:
                raise ValueError(f"HAP ZIP CRC check failed: {path}")
            if not any(Path(item.filename).name in {"module.json", "config.json"} for item in entries):
                raise ValueError(f"HAP lacks module.json or config.json: {path}")
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"Invalid HAP ZIP {path}: {exc}") from exc


def file_state(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    info = path.stat()
    return {"sha256": sha256_file(path), "size": info.st_size, "mtime_ns": info.st_mtime_ns}


def make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in a sealed package: {path}")
        if path.is_file():
            path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        elif path.is_dir():
            path.chmod(
                stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP
                | stat.S_IROTH | stat.S_IXOTH
            )
    root.chmod(
        stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP
        | stat.S_IROTH | stat.S_IXOTH
    )


def _string_array(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{label} must be a nonempty string array")
    return list(value)


def frozen_category_contracts(environment: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate and normalize the exact Phase 4 command-category contracts."""
    raw = environment.get("category_contracts")
    if not isinstance(raw, dict) or set(raw) != PHASE4_CATEGORY_SET:
        raise ValueError(
            "H4ENV category_contracts must cover exactly: "
            + ", ".join(PHASE4_CATEGORY_ORDER)
        )
    normalized: dict[str, dict[str, Any]] = {}
    for category in PHASE4_CATEGORY_ORDER:
        contract = raw[category]
        if not isinstance(contract, dict):
            raise ValueError(f"H4ENV category contract must be an object: {category}")
        executable_value = str(contract.get("resolved_executable", ""))
        executable = Path(executable_value).expanduser()
        if not executable.is_absolute():
            raise ValueError(f"{category}: resolved_executable must be absolute")
        try:
            resolved = executable.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"{category}: frozen executable is unavailable: {exc}") from exc
        if str(resolved) != executable_value or not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise ValueError(f"{category}: frozen executable is not canonical/executable")
        executable_sha = str(contract.get("executable_sha256", "")).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", executable_sha) or sha256_file(resolved) != executable_sha:
            raise ValueError(f"{category}: frozen executable hash differs")
        normalized[category] = {
            "resolved_executable": str(resolved),
            "executable_sha256": executable_sha,
            "required_argv_tokens": _string_array(
                contract.get("required_argv_tokens"), f"{category}.required_argv_tokens"
            ),
            "success_output_contains": _string_array(
                contract.get("success_output_contains"), f"{category}.success_output_contains"
            ),
            "error_output_contains": _string_array(
                contract.get("error_output_contains"), f"{category}.error_output_contains"
            ),
        }
    return normalized


def validate_frozen_command(
    category: str,
    argv: Any,
    contracts: dict[str, dict[str, Any]],
) -> tuple[list[str], dict[str, Any]]:
    if category not in contracts:
        raise ValueError(f"Unknown or unfrozen command category: {category}")
    if not isinstance(argv, list) or not argv or any(
        not isinstance(item, str) or not item for item in argv
    ):
        raise ValueError(f"{category}: argv must be a nonempty string array")
    contract = contracts[category]
    if argv[0] != contract["resolved_executable"]:
        raise ValueError(f"{category}: executable differs from the frozen category contract")
    missing = [item for item in contract["required_argv_tokens"] if item not in argv]
    if missing:
        raise ValueError(f"{category}: argv lacks frozen required tokens: {missing}")
    return list(argv), contract


def frozen_output_verdict(
    stdout: str,
    stderr: str,
    contract: dict[str, Any],
) -> tuple[bool, list[str], list[str]]:
    combined = stdout + "\n" + stderr
    lowered = combined.lower()
    successes = [item for item in contract["success_output_contains"] if item in combined]
    errors = [item for item in contract["error_output_contains"] if item.lower() in lowered]
    return len(successes) == len(contract["success_output_contains"]) and not errors, successes, errors


def manifest_text(directory: Path, relative_names: Sequence[str]) -> str:
    lines: list[str] = []
    for name in sorted(set(relative_names)):
        if "\\" in name:
            raise ValueError(f"Manifest path must use POSIX separators: {name}")
        path = safe_relative_path(directory, name, "manifest artifact")
        if not path.is_file():
            raise ValueError(f"Manifest artifact is not a file: {path}")
        lines.append(f"{sha256_file(path)}  {Path(name).as_posix()}")
    return "\n".join(lines) + "\n"


def verify_manifest(directory: Path, expected_names: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    manifest = directory / "manifest.sha256"
    if not manifest.is_file():
        return [f"Missing manifest: {manifest}"]
    names: set[str] = set()
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), start=1):
        if "  " not in line:
            errors.append(f"Malformed manifest line {manifest}:{number}")
            continue
        expected, name = line.split("  ", 1)
        if "\\" in name:
            errors.append(f"Manifest path must use POSIX separators: {name}")
            continue
        if name in names:
            errors.append(f"Duplicate manifest entry: {name}")
            continue
        names.add(name)
        try:
            path = safe_relative_path(directory, name, "manifest artifact")
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not path.is_file() or sha256_file(path) != expected:
            errors.append(f"Manifest hash mismatch: {path}")
    if expected_names is not None and names != expected_names:
        errors.append(f"Manifest file set differs; expected={sorted(expected_names)}, actual={sorted(names)}")
    return errors


def sanitize_log(value: str) -> str:
    redacted = re.sub(
        r"(?i)((?:password|passwd|passphrase|token|secret|private[_-]?key|storepass|keypass)\s*[:=]\s*)\S+",
        r"\1[REDACTED]",
        value,
    )
    return re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        redacted,
        flags=re.IGNORECASE | re.DOTALL,
    )


def assert_no_secrets(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_KEY_RE.search(str(key)) and item not in (None, "", False, "[REDACTED]"):
                raise ValueError(f"Secret-bearing field is prohibited in plan/config: {path}.{key}")
            assert_no_secrets(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_secrets(item, f"{path}[{index}]")


def executable_is_allowed(executable: str, allowed: Sequence[str]) -> bool:
    actual = Path(executable).expanduser()
    for item in allowed:
        allowed_path = Path(item).expanduser()
        if executable == item:
            return True
        if len(allowed_path.parts) == 1 and actual.name == allowed_path.name:
            return True
        if allowed_path.is_absolute() and actual.is_absolute():
            try:
                if actual.resolve() == allowed_path.resolve():
                    return True
            except OSError:
                continue
    return False


def validate_command_argv(value: Any, allowed: Sequence[str]) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ValueError("Command argv must be a nonempty string array")
    if not executable_is_allowed(value[0], allowed):
        raise ValueError(f"Executable is not frozen/allowed: {value[0]}")
    return list(value)


def selector_is_present(argv: Sequence[str], selector_tokens: Sequence[str]) -> bool:
    if not selector_tokens:
        return False
    width = len(selector_tokens)
    return any(list(argv[index:index + width]) == list(selector_tokens) for index in range(len(argv) - width + 1))


def run_command(argv: Sequence[str], cwd: Path, timeout: int = 300) -> dict[str, Any]:
    started = utc_now()
    start_clock = time.monotonic()
    recorded_argv = list(argv)
    execution_argv = recorded_argv
    if os.name == "nt" and recorded_argv and recorded_argv[0].lower().endswith(".py"):
        execution_argv = [sys.executable, *recorded_argv]
    try:
        completed = subprocess.run(
            execution_argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, shell=False, timeout=timeout, check=False,
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
    semantic_error = bool(ERROR_OUTPUT_RE.search(stdout) or ERROR_OUTPUT_RE.search(stderr))
    return {
        "argv": recorded_argv, "started_at": started, "finished_at": utc_now(),
        "duration_seconds": round(time.monotonic() - start_clock, 3),
        "exit_code": exit_code, "timed_out": timed_out, "semantic_error": semantic_error,
        "stdout": sanitize_log(stdout), "stderr": sanitize_log(stderr),
    }


def command_passed(record: dict[str, Any]) -> bool:
    return record.get("exit_code") == 0 and record.get("timed_out") is False and record.get("semantic_error") is False


def parse_resolution(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", str(value))
    if not match:
        raise ValueError(f"Resolution must look like WIDTHxHEIGHT: {value}")
    return int(match.group(1)), int(match.group(2))
