#!/usr/bin/env python3
"""Capture and seal one Android UI state with Android CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from _common import (
    assert_no_symlink,
    assert_valid_json,
    assert_valid_png,
    atomic_json,
    atomic_text,
    environment_probe_commands,
    exclusive_lock,
    load_json,
    manifest_lines,
    read_csv,
    require_success,
    parse_resolution,
    resolve_executable,
    run_command,
    sha256_file,
    utc_now,
    validate_id,
    verify_environment_probe,
    verify_environment_attestation,
    verify_phase_identity,
    write_csv,
)


INDEX_FIELDS = [
    "evidence_id", "inventory_id", "feature_id", "page_id", "state_id", "env_id",
    "captured_at", "relative_path", "metadata_sha256", "status", "supersedes_evidence_id",
]


def evidence_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"EVD-{stamp}-{uuid.uuid4().hex[:6].upper()}"


def find_environment(workspace: Path, env_id: str) -> dict:
    registry = load_json(workspace / "environments.json")
    for env in registry.get("environments", []):
        if env.get("env_id") == env_id:
            if env.get("status") != "FROZEN":
                raise ValueError(f"Environment is not frozen: {env_id}")
            return env
    raise ValueError(f"Unknown ENV-ID: {env_id}")


def find_previous(workspace: Path, previous_id: str) -> tuple[Path, dict]:
    for row in read_csv(workspace / "evidence-index.csv"):
        if row.get("evidence_id") == previous_id:
            path = workspace / row["relative_path"]
            assert_no_symlink(path, workspace)
            if row.get("status") not in {"SEALED", "ACCEPTED"}:
                raise ValueError(f"Previous evidence is not active: {previous_id}")
            if not (path / "COMMITTED").is_file() or (path / "COMMITTED").read_text(encoding="utf-8").strip() != previous_id:
                raise ValueError(f"Previous evidence is not committed: {previous_id}")
            metadata = load_json(path / "metadata.json")
            if metadata.get("status") != "SEALED":
                raise ValueError(f"Previous evidence metadata is not sealed: {previous_id}")
            return path, metadata
    raise ValueError(f"Previous evidence is not indexed: {previous_id}")


def verify_frozen_workspace(workspace: Path, phase_manifest: dict) -> dict:
    environment_path = workspace / "environments.json"
    if sha256_file(environment_path) != phase_manifest.get("environment_registry_sha256"):
        raise ValueError("Frozen environment registry changed; issue a new ENV-ID/workspace")
    return verify_phase_identity(workspace, phase_manifest)


def verify_source_revision(phase_manifest: dict) -> list[dict]:
    project_root = phase_manifest.get("android_project_root", "")
    records = []
    for label, argv in (
        ("git revision", ["git", "-C", project_root, "rev-parse", "HEAD"]),
        ("git worktree", ["git", "-C", project_root, "status", "--porcelain", "--untracked-files=all"]),
    ):
        record = run_command(argv, timeout=30)
        records.append({"label": label, **record})
        require_success(record, label)
    if records[0]["stdout"].strip() != phase_manifest.get("source_revision"):
        raise ValueError("Android source revision changed after Phase 2 initialization")
    if records[1]["stdout"].strip():
        raise ValueError("Android source worktree is no longer clean")
    return records


def verify_device(adb_bin: str, device_serial: str, timeout: int) -> dict:
    record = run_command([adb_bin, "devices", "-l"], timeout=timeout)
    require_success(record, "adb devices")
    matching = []
    for line in record["stdout"].splitlines()[1:]:
        fields = line.split()
        if fields and fields[0] == device_serial:
            matching.append(fields)
    if len(matching) != 1 or len(matching[0]) < 2 or matching[0][1] != "device":
        raise ValueError(f"Frozen device is not uniquely available: {device_serial}")
    return record


def verify_foreground_application(
    adb_bin: str, device_serial: str, application_id: str, timeout: int
) -> dict:
    record = run_command(
        [adb_bin, "-s", device_serial, "shell", "dumpsys", "activity", "activities"],
        timeout=timeout,
    )
    require_success(record, "foreground application check")
    if application_id not in record["stdout"]:
        raise ValueError(f"Frozen application is not the resumed foreground app: {application_id}")
    return record


def verify_actual_environment(adb_bin: str, device_serial: str, env_spec: dict, timeout: int) -> list[dict]:
    records = []
    for label, argv in environment_probe_commands(adb_bin, device_serial):
        record = run_command(argv, timeout=timeout)
        require_success(record, f"environment {label}")
        records.append({"label": label, **record})
    verify_environment_probe(records, env_spec)
    return records


def commit_index(workspace: Path, row: dict, supersedes_id: str | None) -> None:
    index_path = workspace / "evidence-index.csv"
    with exclusive_lock(workspace / ".locks" / "evidence-index.lock"):
        rows = read_csv(index_path)
        if any(item.get("evidence_id") == row["evidence_id"] for item in rows):
            raise ValueError(f"Duplicate Evidence-ID in index: {row['evidence_id']}")
        if supersedes_id:
            matches = [item for item in rows if item.get("evidence_id") == supersedes_id]
            if len(matches) != 1:
                raise ValueError(f"Superseded Evidence-ID is not uniquely indexed: {supersedes_id}")
            old = matches[0]
            if old.get("status") != "SEALED":
                raise ValueError(f"Only SEALED evidence may be superseded: {supersedes_id}")
            for field in ("feature_id", "page_id", "state_id", "env_id"):
                if old.get(field) != row.get(field):
                    raise ValueError(f"Superseded evidence differs on {field}: {supersedes_id}")
            old["status"] = "SUPERSEDED"
        write_csv(index_path, INDEX_FIELDS, [*rows, row])


def set_evidence_read_only(directory: Path, read_only: bool) -> None:
    file_mode = 0o444 if read_only else 0o644
    directory_mode = 0o555 if read_only else 0o755
    if not read_only:
        os.chmod(directory, directory_mode)
    for path in directory.iterdir():
        if path.is_file():
            os.chmod(path, file_mode)
    if read_only:
        os.chmod(directory, directory_mode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--inventory-id", required=True)
    parser.add_argument("--feature-id", required=True)
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--state-id", required=True)
    parser.add_argument("--env-id", required=True)
    parser.add_argument("--steps", required=True)
    parser.add_argument("--issued-by", required=True)
    parser.add_argument("--captured-by", required=True)
    parser.add_argument("--previous-evidence")
    parser.add_argument("--supersedes-evidence")
    parser.add_argument("--include-diff", action="store_true")
    parser.add_argument("--launch", action="store_true", help="Run the frozen APK before the capture")
    parser.add_argument("--activity", help="Optional activity passed to android run")
    parser.add_argument("--android-bin", default="android")
    parser.add_argument("--adb-bin", default="adb")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    workspace_input = Path(args.workspace).expanduser().absolute()
    if workspace_input.is_symlink():
        parser.error("Workspace must not be a symbolic link")
    workspace = workspace_input.resolve()
    if not workspace.is_dir():
        parser.error(f"Workspace does not exist: {workspace}")
    if (workspace / "phase-manifest.json").is_file() is False:
        parser.error("Not an initialized Android inventory workspace")
    if (workspace / "CLOSED").exists():
        parser.error("Phase 2 is CLOSED; capture is read-only")

    ids = {
        "inventory_id": validate_id(args.inventory_id, "Inventory-ID"),
        "feature_id": validate_id(args.feature_id, "Feature-ID"),
        "page_id": validate_id(args.page_id, "Page-ID"),
        "state_id": validate_id(args.state_id, "State-ID"),
        "env_id": validate_id(args.env_id, "ENV-ID"),
    }
    if bool(args.previous_evidence) != bool(args.include_diff):
        parser.error("A transition requires both --previous-evidence and --include-diff")
    if args.launch and args.previous_evidence:
        parser.error("--launch cannot be combined with transition capture")
    if args.issued_by == args.captured_by:
        parser.error("Evidence issuer and runtime collector must be different roles")
    if args.previous_evidence:
        validate_id(args.previous_evidence, "previous Evidence-ID")
    if args.supersedes_evidence:
        validate_id(args.supersedes_evidence, "superseded Evidence-ID")

    steps = Path(args.steps).expanduser().resolve()
    if not steps.is_file() or steps.stat().st_size == 0:
        parser.error("Steps file must exist and be non-empty")
    phase_manifest = load_json(workspace / "phase-manifest.json")
    try:
        scope_snapshot = verify_frozen_workspace(workspace, phase_manifest)
    except ValueError as exc:
        parser.error(str(exc))
    ownership = phase_manifest.get("ownership", {})
    if args.issued_by != ownership.get("evidence_administrator_id"):
        parser.error("--issued-by must equal the frozen evidence administrator")
    if args.captured_by not in ownership.get("runtime_state_agent_ids", []):
        parser.error("--captured-by must be one of the frozen runtime-state agents")
    env_spec = find_environment(workspace, ids["env_id"])
    device_serial = env_spec.get("device_serial")
    if not device_serial:
        parser.error("Frozen environment has no device serial")
    try:
        _, environment_attestation_sha256 = verify_environment_attestation(
            workspace, env_spec, phase_manifest
        )
    except ValueError as exc:
        parser.error(str(exc))
    try:
        android_bin = resolve_executable(args.android_bin)
        adb_bin = resolve_executable(args.adb_bin)
    except ValueError as exc:
        parser.error(str(exc))

    previous_metadata = None
    if args.previous_evidence:
        previous_path, previous_metadata = find_previous(workspace, args.previous_evidence)
        if previous_metadata.get("env_id") != ids["env_id"]:
            parser.error("Previous evidence belongs to a different environment")
        if previous_metadata.get("device_serial") != device_serial:
            parser.error("Previous evidence belongs to a different device")
        if previous_metadata.get("feature_id") != ids["feature_id"]:
            parser.error("Previous evidence belongs to a different feature")
    else:
        previous_path = None
    if args.supersedes_evidence:
        _, superseded_metadata = find_previous(workspace, args.supersedes_evidence)
        for field in ("feature_id", "page_id", "state_id", "env_id"):
            if superseded_metadata.get(field) != ids[field]:
                parser.error(f"Superseded evidence differs on {field}")

    evd_id = evidence_id()
    attempt_id = "ATT-" + evd_id.removeprefix("EVD-")
    final_dir = workspace / "evidence" / ids["env_id"] / ids["page_id"] / ids["state_id"] / evd_id
    assert_no_symlink(final_dir, workspace)
    if final_dir.exists():
        parser.error(f"Evidence path already exists: {final_dir}")
    staging = workspace / ".staging" / evd_id
    if staging.exists():
        parser.error(f"Staging path already exists: {staging}")

    command_records = []
    device_verification = None
    application_verification = None
    environment_verification = []
    source_verification = []
    process_env = os.environ.copy()
    process_env["ANDROID_SERIAL"] = str(device_serial)
    issued_at = utc_now()
    lock_digest = hashlib.sha256(str(device_serial).encode("utf-8")).hexdigest()
    lock_path = Path(tempfile.gettempdir()) / "android-migration-device-locks" / f"{lock_digest}.lock"
    final_created = False

    try:
        with exclusive_lock(lock_path):
            device_verification = verify_device(adb_bin, str(device_serial), args.timeout)
            environment_verification = verify_actual_environment(
                adb_bin, str(device_serial), env_spec, args.timeout
            )
            source_verification = verify_source_revision(phase_manifest)
            staging.mkdir(parents=True)
            shutil.copyfile(steps, staging / "steps.md")

            if args.launch:
                launch_cmd = [
                    android_bin, "run", f"--device={device_serial}",
                    f"--apks={phase_manifest['apk_path']}",
                ]
                if args.activity:
                    launch_cmd.append(f"--activity={args.activity}")
                record = run_command(launch_cmd, timeout=args.timeout, env=process_env)
                command_records.append(record)
                require_success(record, "android run")

            application_verification = verify_foreground_application(
                adb_bin, str(device_serial), str(env_spec.get("application_id", "")), args.timeout
            )

            if args.include_diff:
                diff_cmd = [
                    android_bin, "layout", f"--device={device_serial}", "--diff", "--pretty",
                    f"-o={staging / 'layout-diff.json'}",
                ]
                record = run_command(diff_cmd, timeout=args.timeout, env=process_env)
                command_records.append(record)
                require_success(record, "android layout --diff")

            layout_cmd = [
                android_bin, "layout", f"--device={device_serial}", "--pretty",
                f"-o={staging / 'layout.json'}",
            ]
            record = run_command(layout_cmd, timeout=args.timeout, env=process_env)
            command_records.append(record)
            require_success(record, "android layout")

            screenshot_cmd = [
                android_bin, "screen", "capture", f"--device={device_serial}",
                "-o", str(staging / "screenshot.png"),
            ]
            record = run_command(screenshot_cmd, timeout=args.timeout, env=process_env)
            command_records.append(record)
            require_success(record, "android screen capture")

            assert_valid_json(staging / "layout.json")
            if args.include_diff:
                assert_valid_json(staging / "layout-diff.json")
            screenshot_size = assert_valid_png(staging / "screenshot.png")
            if screenshot_size != parse_resolution(str(env_spec.get("resolution", ""))):
                raise ValueError("Screenshot pixel dimensions differ from the frozen environment")
            if previous_path is not None:
                same_layout = sha256_file(staging / "layout.json") == sha256_file(previous_path / "layout.json")
                same_screen = sha256_file(staging / "screenshot.png") == sha256_file(previous_path / "screenshot.png")
                if same_layout and same_screen:
                    raise ValueError("Transition capture is not observably different from predecessor")

            version_record = run_command([android_bin, "--version"], timeout=args.timeout, env=process_env)
            command_records.append(version_record)
            require_success(version_record, "android --version")
            cli_version = version_record["stdout"].strip() or version_record["stderr"].strip()

            artifact_names = ["screenshot.png", "layout.json", "steps.md"]
            if args.include_diff:
                artifact_names.append("layout-diff.json")
            artifacts = []
            mime = {
                "screenshot.png": "image/png",
                "layout.json": "application/json",
                "layout-diff.json": "application/json",
                "steps.md": "text/markdown",
            }
            for name in artifact_names:
                path = staging / name
                artifacts.append(
                    {
                        "relative_path": name,
                        "mime_type": mime[name],
                        "sha256": sha256_file(path),
                        "size_bytes": path.stat().st_size,
                    }
                )

            captured_at = utc_now()
            metadata = {
                "evidence_id": evd_id,
                **ids,
                "issued_by": args.issued_by,
                "issued_at": issued_at,
                "captured_by": args.captured_by,
                "captured_at": captured_at,
                "capture_tool": "android-cli",
                "android_cli_version": cli_version,
                "device_serial": device_serial,
                "application_id": env_spec.get("application_id"),
                "app_version": env_spec.get("app_version"),
                "app_build": env_spec.get("app_build"),
                "source_revision": env_spec.get("source_revision"),
                "apk_sha256": env_spec.get("apk_sha256"),
                "environment_registry_sha256": phase_manifest.get("environment_registry_sha256"),
                "environment_attestation_sha256": environment_attestation_sha256,
                "scope_sha256": phase_manifest.get("scope_sha256"),
                "predecessor_evidence_id": args.previous_evidence or "",
                "supersedes_evidence_id": args.supersedes_evidence or "",
                "commands": command_records,
                "device_verification": device_verification,
                "environment_verification": environment_verification,
                "application_verification": application_verification,
                "source_verification": source_verification,
                "artifacts": artifacts,
                "status": "SEALED",
            }
            atomic_json(staging / "metadata.json", metadata)
            manifest_names = artifact_names + ["metadata.json"]
            atomic_text(staging / "manifest.sha256", manifest_lines(staging, manifest_names))

            final_dir.parent.mkdir(parents=True, exist_ok=True)
            staging.rename(final_dir)
            final_created = True
            atomic_text(final_dir / "COMMITTED", f"{evd_id}\n")
            set_evidence_read_only(final_dir, True)
            relative = final_dir.relative_to(workspace).as_posix()
            commit_index(
                workspace,
                {
                    "evidence_id": evd_id,
                    **ids,
                    "captured_at": captured_at,
                    "relative_path": relative,
                    "metadata_sha256": sha256_file(final_dir / "metadata.json"),
                    "status": "SEALED",
                    "supersedes_evidence_id": args.supersedes_evidence or "",
                },
                args.supersedes_evidence,
            )
    except Exception as exc:
        attempt = {
            "attempt_id": attempt_id,
            **ids,
            "attempted_at": utc_now(),
            "commands": command_records,
            "error": str(exc),
            "valid_evidence_id": None,
        }
        atomic_json(workspace / "attempts" / f"{attempt_id}.json", attempt)
        if staging.exists():
            shutil.rmtree(staging)
        if final_created and final_dir.exists():
            set_evidence_read_only(final_dir, False)
            shutil.rmtree(final_dir)
        parser.error(f"Capture failed; no valid Evidence-ID was issued: {exc}")

    print(json.dumps({"evidence_id": evd_id, "path": str(final_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
