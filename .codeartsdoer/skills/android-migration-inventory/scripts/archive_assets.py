#!/usr/bin/env python3
"""Archive real Android project assets into the deterministic Phase 2 handoff package."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from _common import (
    ASSET_INVENTORY_FIELDS,
    ASSET_TYPES,
    assert_no_symlink,
    csv_fieldnames,
    exclusive_lock,
    load_json,
    parse_json_string_array,
    read_csv,
    require_success,
    run_command,
    sha256_file,
    utc_now,
    validate_id,
    verify_asset_chain,
    verify_phase_identity,
    write_csv,
    atomic_text,
)


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TYPE_SUFFIXES = {
    "RASTER_IMAGE": {".png", ".jpg", ".jpeg", ".webp", ".gif"},
    "VECTOR_IMAGE": {".svg"},
    "XML_RESOURCE": {".xml"},
    "FONT": {".ttf", ".otf", ".woff", ".woff2"},
    "AUDIO": {".mp3", ".wav", ".ogg", ".m4a", ".aac"},
    "ANIMATION": {".json", ".xml"},
    "JSON_RESOURCE": {".json"},
}
MAPPING_FIELDS = {
    "asset_id", "source_path", "source_sha256", "asset_type",
    "feature_ids", "page_ids", "state_ids", "notes",
}


def normalize_mapping(
    value: Any,
    project_root: Path,
    included_features: set[str],
    archived_by: str,
    created_at: str,
) -> tuple[list[dict[str, str]], list[tuple[Path, str, str]]]:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("Asset mapping must be a schema_version 1 JSON object")
    raw_assets = value.get("assets")
    if not isinstance(raw_assets, list) or not all(isinstance(item, dict) for item in raw_assets):
        raise ValueError("Asset mapping assets must be an array of objects")
    rows: list[dict[str, str]] = []
    copies: list[tuple[Path, str, str]] = []
    seen_ids: set[str] = set()
    seen_sources: set[str] = set()
    for raw in raw_assets:
        unknown = set(raw) - MAPPING_FIELDS
        missing = (MAPPING_FIELDS - {"notes"}) - set(raw)
        if unknown or missing:
            raise ValueError(f"Asset mapping fields differ; missing={sorted(missing)}, unknown={sorted(unknown)}")
        asset_id = validate_id(str(raw.get("asset_id", "")), "Asset-ID")
        if asset_id == "NONE_FOUND" or asset_id in seen_ids:
            raise ValueError(f"Sentinel or duplicate Asset-ID is prohibited: {asset_id}")
        source_value = str(raw.get("source_path", ""))
        source_relative = PurePosixPath(source_value)
        if (
            any(ord(character) < 32 for character in source_value)
            or "\\" in source_value
            or source_relative.is_absolute()
            or not source_relative.parts
            or any(part in {"", ".", ".."} for part in source_relative.parts)
            or source_relative.as_posix() != source_value
            or source_value in seen_sources
        ):
            raise ValueError(f"Unsafe or duplicate source_path for {asset_id}: {source_value!r}")
        source = project_root / Path(*source_relative.parts)
        assert_no_symlink(source, project_root)
        if not source.is_file() or not stat.S_ISREG(source.stat().st_mode) or source.stat().st_size == 0:
            raise ValueError(f"Asset source must be a non-empty regular file: {source_value}")
        source_digest = str(raw.get("source_sha256", ""))
        if not SHA256_RE.fullmatch(source_digest) or sha256_file(source) != source_digest:
            raise ValueError(f"Frozen source hash differs for {asset_id}")
        asset_type = str(raw.get("asset_type", ""))
        if asset_type not in ASSET_TYPES:
            raise ValueError(f"Unsupported asset_type for {asset_id}: {asset_type!r}")
        allowed_suffixes = TYPE_SUFFIXES.get(asset_type)
        if allowed_suffixes is not None and source.suffix.lower() not in allowed_suffixes:
            raise ValueError(f"asset_type does not match the source suffix for {asset_id}")

        normalized_arrays: dict[str, list[str]] = {}
        for field, label in (
            ("feature_ids", "Feature-ID"), ("page_ids", "Page-ID"), ("state_ids", "State-ID")
        ):
            candidate = raw.get(field)
            if not isinstance(candidate, list):
                raise ValueError(f"{asset_id}.{field} must be an array")
            encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
            values = parse_json_string_array(encoded, f"{asset_id}.{field}")
            for item in values:
                validate_id(item, label)
            normalized_arrays[field] = values
        if not set(normalized_arrays["feature_ids"]) <= included_features:
            raise ValueError(f"Asset mapping is outside the frozen feature scope: {asset_id}")
        archive_path = f"asset-package/files/{asset_id}/{source_relative.name}"
        rows.append(
            {
                "asset_id": asset_id,
                "source_path": source_value,
                "archive_path": archive_path,
                "sha256": source_digest,
                "asset_type": asset_type,
                "feature_ids": json.dumps(normalized_arrays["feature_ids"], separators=(",", ":")),
                "page_ids": json.dumps(normalized_arrays["page_ids"], separators=(",", ":")),
                "state_ids": json.dumps(normalized_arrays["state_ids"], separators=(",", ":")),
                "created_by": archived_by,
                "created_at": created_at,
                "reviewed_by": "",
                "reviewed_at": "",
                "status": "ARCHIVED",
                "notes": str(raw.get("notes", "")),
            }
        )
        copies.append((source, archive_path.removeprefix("asset-package/"), source_digest))
        seen_ids.add(asset_id)
        seen_sources.add(source_value)
    rows.sort(key=lambda row: row["asset_id"])
    copies.sort(key=lambda item: item[1])
    return rows, copies


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--mapping", required=True)
    parser.add_argument("--archived-by", required=True)
    args = parser.parse_args()

    workspace_input = Path(args.workspace).expanduser().absolute()
    mapping_input = Path(args.mapping).expanduser().absolute()
    if workspace_input.is_symlink() or mapping_input.is_symlink():
        parser.error("Workspace and mapping must not be symbolic links")
    workspace = workspace_input.resolve()
    mapping_path = mapping_input.resolve()
    if (workspace / "CLOSED").exists():
        parser.error("Phase 2 is CLOSED; asset archive is read-only")
    if not mapping_path.is_file():
        parser.error(f"Asset mapping does not exist: {mapping_path}")

    try:
        phase_manifest = load_json(workspace / "phase-manifest.json")
        verify_phase_identity(workspace, phase_manifest)
        ownership = phase_manifest.get("ownership", {})
        if args.archived_by != ownership.get("code_map_agent_id"):
            raise ValueError("--archived-by must equal the frozen code-map agent")
        if csv_fieldnames(workspace / "asset-inventory.csv") != ASSET_INVENTORY_FIELDS:
            raise ValueError("asset-inventory.csv header differs from the asset contract")
        if read_csv(workspace / "asset-inventory.csv"):
            raise ValueError("Asset inventory is already populated; overwrite is prohibited")
        package = workspace / "asset-package"
        files_root = package / "files"
        assert_no_symlink(package, workspace)
        if not package.is_dir() or not files_root.is_dir():
            raise ValueError("Initialized asset-package/files directory is missing")
        if any(package.iterdir()) and (
            any(files_root.iterdir()) or {path.name for path in package.iterdir()} != {"files"}
        ):
            raise ValueError("Asset package is not empty; overwrite is prohibited")

        project_root = Path(str(phase_manifest.get("android_project_root", ""))).resolve()
        source_checks = [
            run_command(["git", "-C", str(project_root), "rev-parse", "HEAD"], timeout=30),
            run_command(["git", "-C", str(project_root), "status", "--porcelain", "--untracked-files=all"], timeout=30),
        ]
        require_success(source_checks[0], "source revision verification")
        require_success(source_checks[1], "source worktree verification")
        if source_checks[0]["stdout"].strip() != phase_manifest.get("source_revision"):
            raise ValueError("Android Git HEAD differs from the frozen source revision")
        if source_checks[1]["stdout"].strip():
            raise ValueError("Android worktree changed after Phase 2 initialization")
        rows, copies = normalize_mapping(
            load_json(mapping_path),
            project_root,
            set(phase_manifest.get("included_features", [])),
            args.archived_by,
            utc_now(),
        )

        staging_root = workspace / ".staging"
        staging_root.mkdir(exist_ok=True)
        assert_no_symlink(staging_root, workspace)
        with exclusive_lock(workspace / ".locks" / "asset-archive.lock"):
            with tempfile.TemporaryDirectory(prefix="asset-archive-", dir=staging_root) as temp_name:
                staged_package = Path(temp_name) / "asset-package"
                (staged_package / "files").mkdir(parents=True)
                manifest_lines: list[str] = []
                for source, relative_value, expected_digest in copies:
                    target = staged_package / relative_value
                    target.parent.mkdir(parents=True)
                    shutil.copyfile(source, target)
                    if sha256_file(target) != expected_digest:
                        raise ValueError(f"Archived asset hash differs after copy: {relative_value}")
                    manifest_lines.append(f"{expected_digest}  {relative_value}")
                manifest_value = "\n".join(manifest_lines) + ("\n" if manifest_lines else "")
                atomic_text(staged_package / "manifest.sha256", manifest_value)
                atomic_text(staged_package / "COMMITTED", sha256_file(staged_package / "manifest.sha256") + "\n")
                files_root.rmdir()
                package.rmdir()
                staged_package.rename(package)
                write_csv(workspace / "asset-inventory.csv", ASSET_INVENTORY_FIELDS, rows)
        verify_asset_chain(workspace, phase_manifest)
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))

    print(json.dumps({"archived_assets": len(rows), "asset_package": str(package)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
