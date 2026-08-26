#!/usr/bin/env python3
"""Publish a closed Phase-4 Harmony source project to an explicit empty target directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


EXCLUDED_PARTS = {".git", ".hvigor", "build", "oh_modules", "node_modules", "__pycache__"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_source_project(source: Path, target: Path) -> int:
    count = 0
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"symbolic links are prohibited in published project: {relative}")
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            count += 1
    return count


def verify_source_bindings(source: Path, manifest: Path) -> None:
    entries: dict[str, str] = {}
    for line in manifest.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2 and len(parts[0]) == 64:
            entries[parts[1].lstrip("* ")] = parts[0]
    source_files = []
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part in EXCLUDED_PARTS for part in relative.parts) or not path.is_file():
            continue
        source_files.append(path)
        key = f"harmony-project/{relative.as_posix()}"
        if entries.get(key) != sha256_file(path):
            raise ValueError(f"Phase-4 closure manifest does not bind published source: {relative}")
    if not source_files:
        raise ValueError("Harmony source project contains no publishable files")


def publish(workspace: Path, target: Path) -> int:
    workspace = workspace.expanduser().resolve(strict=True)
    target = target.expanduser().absolute()
    if workspace.name != "phase-04-harmony-implementation":
        raise ValueError("workspace must be the canonical phase-04-harmony-implementation directory")
    source = (workspace / "harmony-project").resolve(strict=True)
    if target.resolve(strict=False) == source:
        raise ValueError("target must differ from the Phase-4 source project")
    report = workspace / "stage-04-gate-report.json"
    closed = workspace / "CLOSED"
    manifest = workspace / "stage-04-closure-manifest.sha256"
    if not report.is_file() or not closed.is_file() or not manifest.is_file():
        raise ValueError("Phase 4 is not closed; report, manifest, or CLOSED is missing")
    data = json.loads(report.read_text(encoding="utf-8"))
    if str(data.get("verdict", data.get("final_verdict", ""))).upper() != "PASS":
        raise ValueError("Phase-4 gate report is not PASS")
    expected = closed.read_text(encoding="utf-8", errors="replace").strip().split()[0]
    if expected != sha256_file(report):
        raise ValueError("CLOSED does not bind the Phase-4 gate report")
    verify_source_bindings(source, manifest)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ValueError(f"publish target must be an empty directory: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.publish-", dir=target.parent))
    try:
        count = copy_source_project(source, staging)
        marker = {
            "schema_version": 1,
            "source_workspace": str(workspace),
            "stage4_gate_sha256": sha256_file(report),
            "stage4_closure_manifest_sha256": sha256_file(manifest),
            "published_file_count": count,
        }
        (staging / ".migration-source.json").write_text(
            json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if target.exists():
            target.rmdir()
        os.replace(staging, target)
        return count
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="closed Phase-4 workspace")
    parser.add_argument("--target", required=True, help="explicit empty Harmony target directory")
    args = parser.parse_args()
    try:
        count = publish(Path(args.workspace), Path(args.target))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(f"[publish] PASS files={count} target={Path(args.target).expanduser().absolute()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
