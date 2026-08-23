#!/usr/bin/env python3
"""Anchor one sealed Phase 2 evidence package in the controller-owned registry."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


FIELDS = [
    "anchor_id", "evidence_id", "run_id", "phase", "relative_path",
    "package_manifest_sha256", "metadata_sha256", "scope_sha256",
    "environment_registry_sha256", "anchored_at", "anchored_by", "status",
]
ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,95}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def atomic_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if path.is_symlink():
        raise ValueError(f"Registry must not be a symbolic link: {path}")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="raise")
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


def verify_manifest(evidence_dir: Path, metadata: dict) -> None:
    transition = bool(metadata.get("predecessor_evidence_id"))
    expected = {"screenshot.png", "layout.json", "steps.md", "metadata.json"}
    if transition:
        expected.add("layout-diff.json")
    manifest_path = evidence_dir / "manifest.sha256"
    entries: dict[str, str] = {}
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if "  " not in line:
            raise ValueError("Evidence manifest is malformed")
        digest, name = line.split("  ", 1)
        pure = PurePosixPath(name)
        if not SHA256_RE.fullmatch(digest) or pure.is_absolute() or len(pure.parts) != 1 or name in entries:
            raise ValueError("Evidence manifest contains an unsafe or duplicate entry")
        entries[name] = digest
    if set(entries) != expected:
        raise ValueError("Evidence manifest does not contain the exact package artifacts")
    for name, digest in entries.items():
        path = evidence_dir / name
        if path.is_symlink() or not path.is_file() or sha256_file(path) != digest:
            raise ValueError(f"Evidence artifact differs from its manifest: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--anchored-by", required=True)
    args = parser.parse_args()

    run_input = Path(args.run_dir).expanduser().absolute()
    if run_input.is_symlink():
        parser.error("Migration run must not be a symbolic link")
    run_dir = run_input.resolve()
    if not ID_RE.fullmatch(args.evidence_id):
        parser.error("Evidence-ID is invalid")
    try:
        scope_path = run_dir / "controller" / "scope.json"
        scope = load_json(scope_path)
        phase_dir = run_dir / "phase-02-android-inventory"
        phase_manifest = load_json(phase_dir / "phase-manifest.json")
        if (phase_dir / "CLOSED").exists() or phase_manifest.get("status") != "IN_PROGRESS":
            raise ValueError("Phase 2 must be IN_PROGRESS when evidence is anchored")
        controller_id = scope.get("ownership", {}).get("migration_controller_id")
        if args.anchored_by != controller_id:
            raise ValueError("--anchored-by must equal the frozen migration controller")
        scope_digest = sha256_file(scope_path)
        if phase_manifest.get("scope_sha256") != scope_digest or phase_manifest.get("run_id") != scope.get("run_id"):
            raise ValueError("Phase 2 identity differs from the controller scope")
        android = scope.get("android", {})
        if (
            phase_manifest.get("android_project_root") != str(Path(str(android.get("project_root", ""))).expanduser().resolve())
            or phase_manifest.get("apk_path") != str(Path(str(android.get("apk_path", ""))).expanduser().resolve())
            or phase_manifest.get("apk_sha256") != android.get("apk_sha256")
            or phase_manifest.get("source_revision") != android.get("source_revision")
            or phase_manifest.get("ownership") != scope.get("ownership")
            or phase_manifest.get("included_features") != scope.get("migration_scope", {}).get("included_features")
        ):
            raise ValueError("Phase 2 manifest differs from the frozen controller scope")
        index_fields, index_rows = read_csv(phase_dir / "evidence-index.csv")
        matches = [row for row in index_rows if row.get("evidence_id") == args.evidence_id]
        if len(matches) != 1 or matches[0].get("status") not in {"SEALED", "SUPERSEDED"}:
            raise ValueError("Evidence-ID is not uniquely sealed in the Phase 2 index")
        index = matches[0]
        expected_relative = (
            f"evidence/{index.get('env_id', '')}/{index.get('page_id', '')}/"
            f"{index.get('state_id', '')}/{args.evidence_id}"
        )
        if index.get("relative_path") != expected_relative:
            raise ValueError("Evidence index path is not canonical")
        relative = Path(expected_relative)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Evidence path is unsafe")
        evidence_dir = (phase_dir / relative).resolve()
        evidence_dir.relative_to(phase_dir.resolve())
        if evidence_dir.is_symlink() or not evidence_dir.is_dir():
            raise ValueError("Evidence directory is missing or symbolic")
        for path in evidence_dir.iterdir():
            if path.is_symlink():
                raise ValueError("Evidence packages must not contain symbolic links")
        if (evidence_dir / "COMMITTED").read_text(encoding="utf-8").strip() != args.evidence_id:
            raise ValueError("Evidence package is not committed")
        metadata = load_json(evidence_dir / "metadata.json")
        if metadata.get("status") != "SEALED" or metadata.get("evidence_id") != args.evidence_id:
            raise ValueError("Evidence metadata is not sealed for this Evidence-ID")
        if metadata.get("scope_sha256") != scope_digest:
            raise ValueError("Evidence metadata differs from the controller scope")
        metadata_digest = sha256_file(evidence_dir / "metadata.json")
        if metadata_digest != index.get("metadata_sha256"):
            raise ValueError("Evidence metadata digest differs from the Phase 2 index")
        verify_manifest(evidence_dir, metadata)
        package_manifest_digest = sha256_file(evidence_dir / "manifest.sha256")
        registry_path = run_dir / "controller" / "evidence-anchor-registry.csv"
        fields, rows = read_csv(registry_path)
        if fields != FIELDS:
            raise ValueError("Controller evidence-anchor registry has an invalid header")
        if any(row.get("evidence_id") == args.evidence_id for row in rows):
            raise ValueError("Evidence-ID is already anchored; overwrite is prohibited")
        anchored_at = utc_now()
        row = {
            "anchor_id": f"ANCH-{args.evidence_id}",
            "evidence_id": args.evidence_id,
            "run_id": str(scope.get("run_id", "")),
            "phase": "2",
            "relative_path": expected_relative,
            "package_manifest_sha256": package_manifest_digest,
            "metadata_sha256": metadata_digest,
            "scope_sha256": scope_digest,
            "environment_registry_sha256": str(phase_manifest.get("environment_registry_sha256", "")),
            "anchored_at": anchored_at,
            "anchored_by": args.anchored_by,
            "status": "ANCHORED",
        }
        atomic_csv(registry_path, [*rows, row])
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"evidence_id": args.evidence_id, "anchor_id": row["anchor_id"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
