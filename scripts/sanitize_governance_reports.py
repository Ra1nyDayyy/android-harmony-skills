#!/usr/bin/env python3
"""Normalize local absolute paths in generated Skill governance JSON reports."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


WINDOWS_HOME = re.compile(r"(?i)^[a-z]:[\\/]users[\\/][^\\/]+[\\/]")
POSIX_HOME = re.compile(r"^/(?:home|users)/[^/]+/")


def normalize_string(value: str, repo_root: Path, skill_root: Path) -> str:
    pairs = (
        (str(skill_root.resolve()), "<skill-root>"),
        (str(repo_root.resolve()), "<repo-root>"),
    )
    normalized = value
    for prefix, label in pairs:
        if normalized.casefold().startswith(prefix.casefold()):
            suffix = normalized[len(prefix) :].lstrip("\\/").replace("\\", "/")
            return label if not suffix else f"{label}/{suffix}"
    return normalized


def normalize_payload(value: Any, repo_root: Path, skill_root: Path) -> Any:
    if isinstance(value, dict):
        return {key: normalize_payload(item, repo_root, skill_root) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_payload(item, repo_root, skill_root) for item in value]
    if isinstance(value, str):
        return normalize_string(value, repo_root, skill_root)
    return value


def atomic_write_json(path: Path, payload: Any) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def contains_private_home(value: Any) -> bool:
    if isinstance(value, dict):
        return any(contains_private_home(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_private_home(item) for item in value)
    if isinstance(value, str):
        return bool(WINDOWS_HOME.match(value) or POSIX_HOME.match(value))
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo_root", type=Path)
    parser.add_argument("--check", action="store_true", help="Check only; do not rewrite reports.")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    skills_root = repo_root / ".codeartsdoer" / "skills"
    if not (repo_root / ".git").exists() or not skills_root.is_dir():
        parser.error("repo_root must be the android-harmony-skills Git repository")

    changed = 0
    violations: list[str] = []
    for skill_root in sorted(path for path in skills_root.iterdir() if path.is_dir()):
        reports = skill_root / "reports"
        if not reports.is_dir():
            continue
        for path in sorted(reports.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            normalized = normalize_payload(payload, repo_root, skill_root)
            if contains_private_home(normalized):
                violations.append(str(path.relative_to(repo_root)))
            if normalized != payload:
                changed += 1
                if not args.check:
                    atomic_write_json(path, normalized)

    print(f"changed_reports: {changed}")
    print(f"private_path_violations: {len(violations)}")
    for path in violations:
        print(f"- {path}")
    return 1 if violations or (args.check and changed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
