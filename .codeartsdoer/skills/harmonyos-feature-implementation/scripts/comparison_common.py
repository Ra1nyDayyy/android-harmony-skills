#!/usr/bin/env python3
"""Shared deterministic comparison values and immutable output helpers."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ComparisonResult:
    comparison_id: str
    category: str
    passed: bool
    expected_sha256: str
    actual_sha256: str
    metrics: dict[str, float | int | str]
    differences: tuple[dict[str, object], ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def value_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path, label: str) -> Any:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Missing or unsafe {label}: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {label}: {path}: {exc}") from exc


def write_new_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(str(path), flags, 0o444)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())


def comparison_result(
    comparison_id: str,
    category: str,
    expected: Any,
    actual: Any,
    metrics: dict[str, float | int | str],
    differences: list[dict[str, object]],
) -> ComparisonResult:
    return ComparisonResult(
        comparison_id=comparison_id,
        category=category,
        passed=not differences,
        expected_sha256=value_sha256(expected),
        actual_sha256=value_sha256(actual),
        metrics=metrics,
        differences=tuple(differences),
    )
