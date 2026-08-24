#!/usr/bin/env python3
"""Deterministic helpers for the HarmonyOS Phase 3 scaffold skill."""

from __future__ import annotations

SCRIPT_INTERFACE = "internal-module"
SCRIPT_INTERFACE_REASON = "Shared deterministic library imported by Phase 3 scaffold CLIs."

import csv
import binascii
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence


ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,95}$")
PLACEHOLDER_RE = re.compile(r"^__.+__$")
INLINE_SECRET_RE = re.compile(
    r"(?i)(password|passwd|passphrase|token|secret|private[_-]?key|storepass|keypass)\s*[:=]"
)
SECRET_KEY_RE = re.compile(
    r"(?i)^(password|passwd|passphrase|token|secret|secret_value|private_key|privatekey|"
    r"credential|credential_value|store_password|key_password|storepass|keypass)$"
)
MAX_COMMAND_OUTPUT = 2 * 1024 * 1024
SNAPSHOT_EXCLUDED_PARTS = {
    ".git", ".hg", ".svn", ".idea", ".hvigor", "oh_modules", "node_modules",
    "build", "out", "dist", "coverage", "__pycache__",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ValueError(f"{label} must match {ID_RE.pattern}: {value!r}")
    return value


def unresolved(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, str):
        return bool(PLACEHOLDER_RE.fullmatch(value))
    if isinstance(value, list):
        return not value or any(unresolved(item) for item in value)
    if isinstance(value, dict):
        return any(unresolved(item) for item in value.values())
    return False


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Cannot read JSON file {path}: {exc}") from exc


def atomic_json(path: Path, value: Any) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"Refusing to replace symbolic-link target: {path}")
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except FileNotFoundError as exc:
        raise ValueError(f"Missing CSV: {path}") from exc
    except OSError as exc:
        raise ValueError(f"Cannot read CSV {path}: {exc}") from exc


def csv_fieldnames(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle).fieldnames or [])
    except FileNotFoundError as exc:
        raise ValueError(f"Missing CSV: {path}") from exc


def write_csv(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"Refusing to replace symbolic-link target: {path}")
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
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
        except OSError:
            pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def png_dimensions(path: Path) -> tuple[int, int]:
    """Validate an entire non-interlaced PNG, including CRC and image data."""
    if not path.is_file() or path.stat().st_size < 45:
        raise ValueError(f"Missing or empty PNG: {path}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Cannot read PNG {path}: {exc}") from exc
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
        if chunk_type == b"IEND" and payload:
            raise ValueError(f"PNG IEND must be empty: {path}")
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
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", header
    )
    if width < 1 or height < 1 or compression != 0 or filtering != 0 or interlace != 0:
        raise ValueError(f"PNG dimensions or encoding are unsupported: {path}")
    allowed_depths = {
        0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8},
        4: {8, 16}, 6: {8, 16},
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


def closure_files(
    workspace: Path,
    *,
    exact_excludes: set[str] | frozenset[str] = frozenset(),
    directory_excludes: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Path]:
    """Return the exact safe file set used by an immutable closure manifest."""
    files: dict[str, Path] = {}
    for path in workspace.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in a closed package: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(workspace).as_posix()
        pure = PurePosixPath(relative)
        if relative in exact_excludes or any(part in directory_excludes for part in pure.parts):
            continue
        if path.name.endswith((".lock", ".tmp")):
            continue
        files[relative] = path
    return files


def closure_manifest_text(
    workspace: Path,
    *,
    exact_excludes: set[str] | frozenset[str] = frozenset(),
    directory_excludes: set[str] | frozenset[str] = frozenset(),
) -> str:
    files = closure_files(
        workspace, exact_excludes=exact_excludes, directory_excludes=directory_excludes
    )
    return "".join(f"{sha256_file(files[name])}  {name}\n" for name in sorted(files))


def verify_closure_manifest(
    workspace: Path,
    manifest_path: Path,
    *,
    exact_excludes: set[str] | frozenset[str] = frozenset(),
    directory_excludes: set[str] | frozenset[str] = frozenset(),
) -> str:
    """Verify safe entries, exact file coverage, and hashes; return manifest text."""
    try:
        manifest_value = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Cannot read closure manifest {manifest_path}: {exc}") from exc
    expected: dict[str, str] = {}
    for number, line in enumerate(manifest_value.splitlines(), start=1):
        if "  " not in line:
            raise ValueError(f"Malformed closure manifest line {number}: {manifest_path}")
        digest, relative = line.split("  ", 1)
        pure = PurePosixPath(relative)
        if (
            not re.fullmatch(r"[0-9a-f]{64}", digest)
            or pure.is_absolute()
            or ".." in pure.parts
            or "." in pure.parts
            or "\\" in relative
            or relative in expected
        ):
            raise ValueError(f"Unsafe or duplicate closure manifest entry: {relative!r}")
        expected[relative] = digest
    actual = closure_files(
        workspace, exact_excludes=exact_excludes, directory_excludes=directory_excludes
    )
    if set(expected) != set(actual):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise ValueError(
            f"Closure snapshot file set changed; missing={missing[:5]}, extra={extra[:5]}"
        )
    for relative, path in actual.items():
        if sha256_file(path) != expected[relative]:
            raise ValueError(f"Closure snapshot hash mismatch: {relative}")
    return manifest_value


def parse_resolution(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*[xX×]\s*(\d+)\s*", str(value))
    if not match:
        raise ValueError(f"Resolution must be WIDTHxHEIGHT: {value!r}")
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        raise ValueError(f"Resolution dimensions must be positive: {value!r}")
    return width, height


def source_row_key(row: dict[str, str]) -> str:
    fields = ("feature_id", "page_id", "state_id", "env_id", "evidence_id")
    values = [str(row.get(field, "")) for field in fields]
    if any(not value for value in values):
        raise ValueError(f"Phase 2 row lacks a core ID: {row.get('inventory_id', '<unknown>')}")
    return "SROW-" + sha256_text("|".join(values))[:20].upper()


def split_multi(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(";") if item.strip()]


def join_multi(values: Iterable[str]) -> str:
    return ";".join(sorted(set(value for value in values if value)))


def assert_no_secrets(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_KEY_RE.fullmatch(str(key)):
                raise ValueError(f"Secret-bearing field is prohibited: {path}.{key}")
            assert_no_secrets(item, f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            assert_no_secrets(item, f"{path}[{index}]")
        return
    if isinstance(value, str):
        if "-----BEGIN" in value.upper() or "PRIVATE KEY-----" in value.upper():
            raise ValueError(f"Private-key material is prohibited: {path}")
        if not path.lower().endswith("reference") and INLINE_SECRET_RE.search(value):
            raise ValueError(f"Inline secret value is prohibited: {path}")


def sanitize_output(value: str) -> str:
    redacted = re.sub(
        r"(?i)((?:password|passwd|passphrase|token|secret|private[_-]?key|storepass|keypass)\s*[:=]\s*)\S+",
        r"\1[REDACTED]",
        value,
    )
    redacted = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        redacted,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return redacted


def safe_relative_path(root: Path, relative: str, label: str, must_exist: bool = True) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"{label} must be a relative path inside {root}: {relative}")
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes {root}: {relative}") from exc
    if must_exist and not resolved.exists():
        raise ValueError(f"Missing {label}: {resolved}")
    return resolved


def is_excluded_snapshot_path(path: Path, project_root: Path) -> bool:
    relative = path.relative_to(project_root)
    return any(part in SNAPSHOT_EXCLUDED_PARTS for part in relative.parts)


def build_snapshot_manifest(workspace: Path, henv_id: str) -> dict[str, Any]:
    project = workspace / "harmony-project"
    if not project.is_dir():
        raise ValueError(f"Missing HarmonyOS project: {project}")
    entries: list[dict[str, Any]] = []
    for path in sorted(project.rglob("*")):
        if is_excluded_snapshot_path(path, project):
            continue
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in the reviewed project snapshot: {path}")
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(workspace).as_posix(),
                    "sha256": sha256_file(path),
                    "size": path.stat().st_size,
                }
            )
    registry_names = (
        "stage-03-input-lock.json", "module-registry.csv", "dependency-policy.json",
        "architecture-map.csv", "route-registry.csv", "surface-registry.csv",
        "public-ui-registry.csv", "capability-contracts.csv", "migration-status.csv",
        "architecture-decisions.csv", "asset-registry.csv", "phase-manifest.json",
    )
    for name in registry_names:
        path = workspace / name
        if not path.is_file():
            raise ValueError(f"Missing Phase 3 registry: {path}")
        entries.append({"path": name, "sha256": sha256_file(path), "size": path.stat().st_size})
    environment = workspace / "environments" / henv_id / "harmony-environment.json"
    if not environment.is_file():
        raise ValueError(f"Missing frozen HENV: {environment}")
    entries.append(
        {
            "path": environment.relative_to(workspace).as_posix(),
            "sha256": sha256_file(environment),
            "size": environment.stat().st_size,
        }
    )
    canonical = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return {
        "created_at": utc_now(),
        "workspace": str(workspace),
        "henv_id": henv_id,
        "entry_count": len(entries),
        "entries": entries,
        "snapshot_sha256": sha256_text(canonical),
        "excluded_generated_parts": sorted(SNAPSHOT_EXCLUDED_PARTS),
    }


def executable_is_allowed(executable: str, allowed: list[str]) -> bool:
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


def validate_command_argv(argv: Any, allowed_executables: list[str]) -> list[str]:
    if not isinstance(argv, list) or not argv or any(not isinstance(item, str) or not item for item in argv):
        raise ValueError("Command argv must be a non-empty array of non-empty strings")
    if not executable_is_allowed(argv[0], allowed_executables):
        raise ValueError(f"Executable is not frozen in HENV toolchain.allowed_executables: {argv[0]}")
    for item in argv:
        if INLINE_SECRET_RE.search(item) or "-----BEGIN" in item.upper():
            raise ValueError("Command arguments must not contain secrets or private-key material")
    return argv


def run_command(argv: Sequence[str], cwd: Path, timeout: int) -> dict[str, Any]:
    started_at = utc_now()
    started = time.monotonic()
    recorded_argv = list(argv)
    execution_argv = recorded_argv
    if os.name == "nt" and recorded_argv and recorded_argv[0].lower().endswith(".py"):
        execution_argv = [sys.executable, *recorded_argv]
    try:
        completed = subprocess.run(
            execution_argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            timeout=timeout,
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
        "cwd": str(cwd),
        "started_at": started_at,
        "finished_at": utc_now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout": sanitize_output(stdout),
        "stderr": sanitize_output(stderr),
    }


def manifest_text(directory: Path, relative_names: Iterable[str]) -> str:
    lines = []
    normalized = [PurePosixPath(str(name).replace("\\", "/")).as_posix() for name in relative_names]
    for name in sorted(normalized):
        path = directory / name
        if not path.is_file():
            raise ValueError(f"Cannot seal missing artifact: {path}")
        lines.append(f"{sha256_file(path)}  {name}")
    return "\n".join(lines) + "\n"
