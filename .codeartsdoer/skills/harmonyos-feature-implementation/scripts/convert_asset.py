#!/usr/bin/env python3
"""Execute and seal one frozen Phase 4 asset-format conversion."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from _common import (
    atomic_json,
    atomic_text,
    csv_fieldnames,
    exclusive_lock,
    load_json,
    make_tree_read_only,
    manifest_text,
    read_csv,
    run_command,
    safe_relative_path,
    sha256_file,
    utc_now,
    validate_actor,
    validate_id,
    write_csv,
)


CONTRACT_KEYS = {
    "contract_id",
    "source_extensions",
    "target_extensions",
    "resolved_executable",
    "executable_sha256",
    "argv_template",
    "required_argv_tokens",
    "success_output_contains",
    "error_output_contains",
}
CONTRACT_REGISTRY_KEYS = {"schema_version", "created_at", "locked_by", "contracts"}
ASSET_UPDATE_FIELDS = {
    "asset_id",
    "migration_mode",
    "source_sha256",
    "target_resource_path",
    "target_sha256",
    "conversion_record_id",
    "conversion_record_sha256",
    "verification_evidence_id",
    "migrated_by",
    "status",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EXTENSION_RE = re.compile(r"^\.[a-z0-9]+$")


def string_array(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise ValueError(f"{label} must be a nonempty string array")
    return list(value)


def load_contract_registry(
    path: Path,
    input_lock: dict[str, Any],
    implementation_lead_id: str,
) -> tuple[dict[str, dict[str, Any]], str]:
    if not path.is_file() or path.is_symlink():
        raise ValueError("Asset-conversion contract registry is missing or unsafe")
    registry_sha256 = sha256_file(path)
    if (
        not SHA256_RE.fullmatch(str(input_lock.get("asset_conversion_contracts_sha256", "")))
        or registry_sha256 != input_lock.get("asset_conversion_contracts_sha256")
    ):
        raise ValueError("Asset-conversion contract registry differs from the input lock")
    registry = load_json(path)
    if (
        not isinstance(registry, dict)
        or set(registry) != CONTRACT_REGISTRY_KEYS
        or registry.get("schema_version") != "1.0"
        or not isinstance(registry.get("created_at"), str)
        or not registry.get("created_at")
        or registry.get("locked_by") != implementation_lead_id
        or not isinstance(registry.get("contracts"), list)
        or not registry.get("contracts")
    ):
        raise ValueError("Asset-conversion contract registry identity is invalid")

    contracts: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(registry["contracts"]):
        if not isinstance(raw, dict) or set(raw) != CONTRACT_KEYS:
            raise ValueError(f"contracts[{index}] has an invalid field set")
        contract_id = validate_id(str(raw.get("contract_id", "")), "Conversion Contract-ID")
        if contract_id in contracts:
            raise ValueError(f"Duplicate asset-conversion Contract-ID: {contract_id}")

        source_extensions = string_array(
            raw.get("source_extensions"), f"{contract_id}.source_extensions"
        )
        target_extensions = string_array(
            raw.get("target_extensions"), f"{contract_id}.target_extensions"
        )
        if any(not EXTENSION_RE.fullmatch(item) for item in source_extensions + target_extensions):
            raise ValueError(f"{contract_id}: extensions must be lowercase .extension values")

        executable_value = str(raw.get("resolved_executable", ""))
        executable = Path(executable_value).expanduser()
        if not executable.is_absolute():
            raise ValueError(f"{contract_id}: resolved_executable must be absolute")
        try:
            resolved_executable = executable.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"{contract_id}: frozen executable is unavailable: {exc}") from exc
        executable_sha256 = str(raw.get("executable_sha256", ""))
        if (
            str(resolved_executable) != executable_value
            or not resolved_executable.is_file()
            or not os.access(resolved_executable, os.X_OK)
            or not SHA256_RE.fullmatch(executable_sha256)
            or sha256_file(resolved_executable) != executable_sha256
        ):
            raise ValueError(f"{contract_id}: executable path or SHA-256 differs")

        argv_template = string_array(raw.get("argv_template"), f"{contract_id}.argv_template")
        required_tokens = string_array(
            raw.get("required_argv_tokens"), f"{contract_id}.required_argv_tokens"
        )
        success_markers = string_array(
            raw.get("success_output_contains"), f"{contract_id}.success_output_contains"
        )
        error_markers = string_array(
            raw.get("error_output_contains"), f"{contract_id}.error_output_contains"
        )
        if argv_template[0] != executable_value:
            raise ValueError(f"{contract_id}: argv_template[0] differs from resolved_executable")
        if sum(token.count("{SOURCE}") for token in argv_template) != 1:
            raise ValueError(f"{contract_id}: argv_template must contain {{SOURCE}} exactly once")
        if sum(token.count("{TARGET}") for token in argv_template) != 1:
            raise ValueError(f"{contract_id}: argv_template must contain {{TARGET}} exactly once")
        if any("{SOURCE}" in token or "{TARGET}" in token for token in required_tokens):
            raise ValueError(
                f"{contract_id}: required_argv_tokens must be literal tokens, not placeholders"
            )

        contracts[contract_id] = {
            "contract_id": contract_id,
            "source_extensions": source_extensions,
            "target_extensions": target_extensions,
            "resolved_executable": executable_value,
            "executable_sha256": executable_sha256,
            "argv_template": argv_template,
            "required_argv_tokens": required_tokens,
            "success_output_contains": success_markers,
            "error_output_contains": error_markers,
        }
    return contracts, registry_sha256


def substitute_token(token: str, source: Path, target: Path) -> str:
    return token.replace("{SOURCE}", str(source)).replace("{TARGET}", str(target))


def command_argv(contract: dict[str, Any], source: Path, target: Path) -> list[str]:
    argv = [substitute_token(token, source, target) for token in contract["argv_template"]]
    if argv[0] != contract["resolved_executable"]:
        raise ValueError("Materialized asset conversion executable differs from the frozen contract")
    required = [substitute_token(token, source, target) for token in contract["required_argv_tokens"]]
    missing = [token for token in required if token not in argv]
    if missing:
        raise ValueError(f"Asset conversion argv lacks frozen required tokens: {missing}")
    return argv


def source_record(input_lock: dict[str, Any], workspace: Path, asset_id: str) -> dict[str, Any]:
    records = input_lock.get("phase2_asset_files")
    if not isinstance(records, list):
        raise ValueError("Phase 4 input lock lacks phase2_asset_files")
    matches = [item for item in records if isinstance(item, dict) and item.get("asset_id") == asset_id]
    if len(matches) != 1:
        raise ValueError(f"Asset input lock must contain exactly one record for {asset_id}")
    record = matches[0]
    if set(record) != {"asset_id", "source_path", "snapshot_path", "sha256", "size"}:
        raise ValueError(f"Asset input lock field set differs for {asset_id}")
    snapshot_value = str(record.get("snapshot_path", ""))
    snapshot = Path(snapshot_value).expanduser()
    if not snapshot.is_absolute():
        raise ValueError(f"Asset snapshot_path must be absolute: {asset_id}")
    try:
        resolved = snapshot.resolve(strict=True)
        resolved.relative_to((workspace / "inputs" / "phase2-assets").resolve())
    except (OSError, ValueError) as exc:
        raise ValueError(f"Asset snapshot_path is not a frozen workspace input: {asset_id}") from exc
    digest = str(record.get("sha256", ""))
    size = record.get("size")
    if (
        str(resolved) != snapshot_value
        or resolved.is_symlink()
        or not resolved.is_file()
        or not SHA256_RE.fullmatch(digest)
        or not isinstance(size, int)
        or size <= 0
        or sha256_file(resolved) != digest
        or resolved.stat().st_size != size
    ):
        raise ValueError(f"Frozen asset snapshot hash or size differs: {asset_id}")
    return {**record, "resolved_snapshot": resolved}


def asset_row(
    csv_path: Path,
    asset_id: str,
) -> tuple[list[str], list[dict[str, str]], int, dict[str, str]]:
    if not csv_path.is_file() or csv_path.is_symlink():
        raise ValueError("asset-migration.csv is missing or unsafe")
    fields = csv_fieldnames(csv_path)
    missing_fields = sorted(ASSET_UPDATE_FIELDS - set(fields))
    if missing_fields:
        raise ValueError(f"asset-migration.csv lacks required columns: {missing_fields}")
    rows = read_csv(csv_path)
    matches = [(index, row) for index, row in enumerate(rows) if row.get("asset_id") == asset_id]
    if len(matches) != 1:
        raise ValueError(f"asset-migration.csv must contain exactly one row for {asset_id}")
    index, row = matches[0]
    if row.get("migration_mode") != "FORMAT_CONVERSION" or row.get("status") != "PLANNED":
        raise ValueError(f"{asset_id}: conversion requires FORMAT_CONVERSION + PLANNED")
    if any(
        row.get(field, "")
        for field in (
            "target_sha256", "conversion_record_id", "conversion_record_sha256",
            "verification_evidence_id", "migrated_by",
        )
    ):
        raise ValueError(f"{asset_id}: planned conversion row already contains result fields")
    return fields, rows, index, row


def remove_created_tree(path: Path) -> None:
    """Remove only the concrete new package created by this invocation."""
    if not path.exists() and not path.is_symlink():
        return
    for child in sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if child.is_symlink():
            child.unlink()
        elif child.is_file():
            child.chmod(stat.S_IRUSR | stat.S_IWUSR)
        elif child.is_dir():
            child.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    shutil.rmtree(path)


def install_new_file(source: Path, target: Path) -> list[Path]:
    """Copy a new target without ever replacing an existing path."""
    created_directories: list[Path] = []
    missing: list[Path] = []
    current = target.parent
    while not current.exists():
        missing.append(current)
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise ValueError(f"Asset target parent is unsafe: {target.parent}")
    try:
        for directory in reversed(missing):
            directory.mkdir()
            created_directories.append(directory)
        if target.exists() or target.is_symlink():
            raise ValueError(f"Asset target appeared before commit: {target}")

        descriptor, temp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temp = Path(temp_name)
        try:
            with source.open("rb") as input_handle, os.fdopen(descriptor, "wb") as output_handle:
                shutil.copyfileobj(input_handle, output_handle)
                output_handle.flush()
                os.fsync(output_handle.fileno())
            os.link(temp, target)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
    except Exception:
        for directory in reversed(created_directories):
            try:
                directory.rmdir()
            except OSError:
                break
        raise
    return created_directories


def cleanup_installed_target(target: Path, created_directories: list[Path]) -> None:
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    for directory in reversed(created_directories):
        try:
            directory.rmdir()
        except OSError:
            break


def write_attempt(
    workspace: Path,
    conversion_id: str,
    asset_id: str,
    contract_id: str,
    executed_by: str,
    error: str,
    command: dict[str, Any] | None,
) -> Path | None:
    attempts = workspace / "attempts"
    if not attempts.is_dir() or attempts.is_symlink():
        return None
    path = attempts / f"ATT-{conversion_id}.json"
    if path.exists() or path.is_symlink():
        return None
    value: dict[str, Any] = {
        "schema_version": 1,
        "attempt_id": f"ATT-{conversion_id}",
        "conversion_id": conversion_id,
        "asset_id": asset_id,
        "contract_id": contract_id,
        "executed_by": executed_by,
        "attempted_at": utc_now(),
        "status": "FAIL",
        "errors": [error],
        "valid_conversion_id": None,
    }
    if command is not None:
        value["command"] = command
    atomic_json(path, value)
    return path


def execute_locked(
    workspace: Path,
    conversion_id: str,
    asset_id: str,
    contract_id: str,
    executed_by: str,
    timeout: int,
    attempt_context: dict[str, Any],
) -> Path:
    project = workspace / "harmony-project"
    final_root = workspace / "asset-conversions"
    staging_root = workspace / ".staging"
    final_dir = final_root / conversion_id
    csv_path = workspace / "asset-migration.csv"
    contract_path = workspace / "asset-conversion-contracts.json"
    input_lock_path = workspace / "stage-04-input-lock.json"
    manifest_path = workspace / "phase-manifest.json"

    if (workspace / "CLOSED").exists():
        raise ValueError("Phase 4 is CLOSED; asset conversion is prohibited")
    if not project.is_dir() or project.is_symlink():
        raise ValueError("Phase 4 HarmonyOS project is missing or unsafe")
    for directory, label in (
        (final_root, "asset-conversions"),
        (staging_root, ".staging"),
        (workspace / "attempts", "attempts"),
    ):
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f"Phase 4 {label} directory is missing or unsafe")
    for path, label in (
        (manifest_path, "phase-manifest.json"),
        (input_lock_path, "stage-04-input-lock.json"),
        (csv_path, "asset-migration.csv"),
    ):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"Phase 4 {label} is missing or unsafe")
    if final_dir.exists() or final_dir.is_symlink():
        raise ValueError(f"Conversion-ID already exists; overwrite is prohibited: {conversion_id}")
    if (workspace / "attempts" / f"ATT-{conversion_id}.json").exists():
        raise ValueError(f"Conversion-ID was already attempted and cannot be reused: {conversion_id}")

    phase_manifest = load_json(manifest_path)
    if phase_manifest.get("phase") != 4 or phase_manifest.get("status") != "IN_PROGRESS":
        raise ValueError("Not an open Phase 4 implementation workspace")
    ownership = phase_manifest.get("ownership")
    if not isinstance(ownership, dict):
        raise ValueError("Phase 4 manifest lacks frozen ownership")
    expected_actor = validate_actor(
        str(ownership.get("visual_asset_agent_id", "")), "visual asset agent"
    )
    implementation_lead = validate_actor(
        str(ownership.get("implementation_lead_id", "")), "implementation lead"
    )
    if executed_by != expected_actor:
        raise ValueError("Only phase-manifest.ownership.visual_asset_agent_id may convert assets")

    input_lock = load_json(input_lock_path)
    input_lock_sha256 = sha256_file(input_lock_path)
    if phase_manifest.get("input_lock_sha256") != input_lock_sha256:
        raise ValueError("Phase 4 input lock differs from phase-manifest.json")
    if input_lock.get("ownership") != ownership:
        raise ValueError("Phase 4 input-lock ownership differs from phase-manifest.json")

    contracts, contract_registry_sha256 = load_contract_registry(
        contract_path, input_lock, implementation_lead
    )
    contract = contracts.get(contract_id)
    if contract is None:
        raise ValueError(f"Unknown frozen asset-conversion Contract-ID: {contract_id}")

    source = source_record(input_lock, workspace, asset_id)
    source_path = source["resolved_snapshot"]
    fields, initial_rows, row_index, row = asset_row(csv_path, asset_id)
    if row.get("source_sha256") != source["sha256"]:
        raise ValueError(f"{asset_id}: asset row source SHA-256 differs from the input lock")

    target_relative = str(row.get("target_resource_path", ""))
    target_pure = PurePosixPath(target_relative)
    if not target_relative or target_pure.is_absolute() or ".." in target_pure.parts:
        raise ValueError(f"{asset_id}: target_resource_path is unsafe")
    target_path = safe_relative_path(project, target_relative, f"asset target {asset_id}", must_exist=False)
    if target_path.exists() or target_path.is_symlink():
        raise ValueError(f"{asset_id}: target already exists; stale output reuse is prohibited")
    source_extension = source_path.suffix.lower()
    target_extension = target_path.suffix.lower()
    if source_extension not in contract["source_extensions"]:
        raise ValueError(f"{asset_id}: source extension is not allowed by {contract_id}")
    if target_extension not in contract["target_extensions"]:
        raise ValueError(f"{asset_id}: target extension is not allowed by {contract_id}")

    installed_target = False
    created_directories: list[Path] = []
    final_created = False
    with tempfile.TemporaryDirectory(prefix=f".{conversion_id}-", dir=staging_root) as temp_name:
        staging = Path(temp_name)
        output_dir = staging / "output"
        output_dir.mkdir()
        staged_target = output_dir / target_path.name
        if staged_target.exists() or staged_target.is_symlink():
            raise ValueError("Staged conversion target unexpectedly exists before execution")
        argv = command_argv(contract, source_path, staged_target)
        if sha256_file(Path(contract["resolved_executable"])) != contract["executable_sha256"]:
            raise ValueError("Frozen conversion executable changed immediately before execution")

        raw = run_command(argv, staging, timeout)
        stdout = raw.pop("stdout")
        stderr = raw.pop("stderr")
        combined = stdout + "\n" + stderr
        lowered = combined.lower()
        success_matches = [
            marker for marker in contract["success_output_contains"] if marker in combined
        ]
        error_matches = [
            marker for marker in contract["error_output_contains"] if marker.lower() in lowered
        ]
        passed = (
            raw.get("exit_code") == 0
            and raw.get("timed_out") is False
            and raw.get("semantic_error") is False
            and len(success_matches) == len(contract["success_output_contains"])
            and not error_matches
        )
        attempt_context["command"] = {
            **raw,
            "category": "ASSET_FORMAT_CONVERSION",
            "cwd": str(staging.resolve()),
            "argv_template": contract["argv_template"],
            "argv": argv,
            "resolved_executable": contract["resolved_executable"],
            "executable_sha256": contract["executable_sha256"],
            "required_argv_tokens": contract["required_argv_tokens"],
            "success_output_contains": contract["success_output_contains"],
            "error_output_contains": contract["error_output_contains"],
            "success_output_matches": success_matches,
            "error_output_matches": error_matches,
            "stdout": stdout,
            "stderr": stderr,
            "command_verdict": "PASS" if passed else "FAIL",
        }
        if not passed:
            raise ValueError(
                "Asset conversion command failed frozen output/exit semantics: "
                f"exit={raw.get('exit_code')}, success={success_matches}, errors={error_matches}"
            )
        if (
            not staged_target.is_file()
            or staged_target.is_symlink()
            or staged_target.stat().st_size <= 0
        ):
            raise ValueError("Asset converter did not create a new nonempty staged target")
        unexpected = [
            path for path in staging.rglob("*")
            if path.is_symlink() or (path.is_file() and path != staged_target)
        ]
        if unexpected:
            raise ValueError("Asset converter created undeclared staging outputs")

        if (
            sha256_file(source_path) != source["sha256"]
            or source_path.stat().st_size != source["size"]
        ):
            raise ValueError("Frozen asset source changed during conversion")
        if sha256_file(contract_path) != contract_registry_sha256:
            raise ValueError("Asset-conversion contract registry changed during conversion")
        if sha256_file(Path(contract["resolved_executable"])) != contract["executable_sha256"]:
            raise ValueError("Frozen conversion executable changed during conversion")
        if sha256_file(input_lock_path) != input_lock_sha256:
            raise ValueError("Phase 4 input lock changed during conversion")
        if (workspace / "CLOSED").exists():
            raise ValueError("Phase 4 was CLOSED during asset conversion")

        logs = staging / "logs"
        logs.mkdir()
        stdout_path = logs / "stdout.log"
        stderr_path = logs / "stderr.log"
        atomic_text(stdout_path, stdout)
        atomic_text(stderr_path, stderr)
        command_record = {
            key: value
            for key, value in attempt_context["command"].items()
            if key not in {"stdout", "stderr"}
        }
        command_record.update(
            {
                "stdout_path": "logs/stdout.log",
                "stdout_sha256": sha256_file(stdout_path),
                "stderr_path": "logs/stderr.log",
                "stderr_sha256": sha256_file(stderr_path),
            }
        )
        attempt_context["command"] = command_record

        target_sha256 = sha256_file(staged_target)
        metadata = {
            "schema_version": 1,
            "conversion_id": conversion_id,
            "asset_id": asset_id,
            "contract_id": contract_id,
            "executed_by": executed_by,
            "executed_at": utc_now(),
            "status": "PASS",
            "input_lock_sha256": input_lock_sha256,
            "source": {
                "snapshot_path": str(source_path),
                "sha256": source["sha256"],
                "size": source["size"],
                "extension": source_extension,
            },
            "target": {
                "project_relative_path": target_pure.as_posix(),
                "sealed_relative_path": f"output/{target_path.name}",
                "sha256": target_sha256,
                "size": staged_target.stat().st_size,
                "extension": target_extension,
            },
            "command": command_record,
        }
        metadata_path = staging / "metadata.json"
        atomic_json(metadata_path, metadata)
        metadata_sha256 = sha256_file(metadata_path)
        expected_manifest_names = {
            "metadata.json",
            f"output/{target_path.name}",
            "logs/stdout.log",
            "logs/stderr.log",
        }
        actual_manifest_names = {
            path.relative_to(staging).as_posix()
            for path in staging.rglob("*")
            if path.is_file()
        }
        if actual_manifest_names != expected_manifest_names:
            raise ValueError("Asset conversion package contains an undeclared file")
        atomic_text(
            staging / "manifest.sha256",
            manifest_text(staging, sorted(expected_manifest_names)),
        )
        manifest_sha256 = sha256_file(staging / "manifest.sha256")
        atomic_text(
            staging / "COMMITTED",
            f"{conversion_id} PASS manifest_sha256={manifest_sha256} "
            f"committed_at={utc_now()}\n",
        )

        try:
            if final_dir.exists() or final_dir.is_symlink():
                raise ValueError(f"Conversion-ID appeared before commit: {conversion_id}")
            staging.rename(final_dir)
            final_created = True
            make_tree_read_only(final_dir)

            # Re-read under the same conversion lock immediately before mutation.
            current_fields = csv_fieldnames(csv_path)
            current_rows = read_csv(csv_path)
            if current_fields != fields or current_rows != initial_rows:
                raise ValueError("asset-migration.csv changed during conversion")
            _, _, current_index, current_row = asset_row(csv_path, asset_id)
            if current_index != row_index or current_row != row:
                raise ValueError("Asset migration row changed during conversion")
            if target_path.exists() or target_path.is_symlink():
                raise ValueError("Asset target appeared during conversion")
            created_directories = install_new_file(final_dir / "output" / target_path.name, target_path)
            installed_target = True
            if sha256_file(target_path) != target_sha256:
                raise ValueError("Installed asset target differs from sealed conversion output")

            updated = dict(current_row)
            updated.update(
                {
                    "target_sha256": target_sha256,
                    "conversion_record_id": conversion_id,
                    "conversion_record_sha256": metadata_sha256,
                    "migrated_by": executed_by,
                    "status": "CONVERSION_VERIFIED",
                }
            )
            if "conversion_command" in fields:
                updated["conversion_command"] = json.dumps(
                    argv, ensure_ascii=False, separators=(",", ":")
                )
            # Evidence remains empty until a live HEVD is captured and linked.
            updated["verification_evidence_id"] = ""
            current_rows[current_index] = updated
            write_csv(csv_path, current_fields, current_rows)
        except Exception:
            if installed_target:
                cleanup_installed_target(target_path, created_directories)
            if final_created:
                remove_created_tree(final_dir)
            raise

    return final_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--conversion-id", required=True)
    parser.add_argument("--asset-id", required=True)
    parser.add_argument("--contract-id", required=True)
    parser.add_argument("--executed-by", required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    try:
        conversion_id = validate_id(args.conversion_id, "Conversion-ID")
        asset_id = validate_id(args.asset_id, "Asset-ID")
        contract_id = validate_id(args.contract_id, "Conversion Contract-ID")
        executed_by = validate_actor(args.executed_by, "visual asset agent")
        if not workspace.is_dir() or workspace.is_symlink():
            raise ValueError(f"Phase 4 workspace is missing or unsafe: {workspace}")
        if args.timeout <= 0 or args.timeout > 3600:
            raise ValueError("--timeout must be between 1 and 3600 seconds")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    attempt_context: dict[str, Any] = {}
    lock_path = workspace / ".locks" / "asset-conversion.lock"
    try:
        if (workspace / "CLOSED").exists():
            raise ValueError("Phase 4 is CLOSED; asset conversion is prohibited")
        if not lock_path.parent.is_dir() or lock_path.parent.is_symlink():
            raise ValueError("Phase 4 .locks directory is missing or unsafe")
        with exclusive_lock(lock_path, timeout=max(15.0, float(args.timeout) + 5.0)):
            try:
                final_dir = execute_locked(
                    workspace,
                    conversion_id,
                    asset_id,
                    contract_id,
                    executed_by,
                    args.timeout,
                    attempt_context,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                attempt_path = None
                if not (workspace / "CLOSED").exists():
                    attempt_path = write_attempt(
                        workspace,
                        conversion_id,
                        asset_id,
                        contract_id,
                        executed_by,
                        str(exc),
                        attempt_context.get("command"),
                    )
                result = {
                    "conversion_id": conversion_id,
                    "status": "FAIL",
                    "error": str(exc),
                    "attempt": str(attempt_path) if attempt_path else None,
                }
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 1
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {"conversion_id": conversion_id, "status": "FAIL", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    print(
        json.dumps(
            {"conversion_id": conversion_id, "status": "PASS", "path": str(final_dir)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
