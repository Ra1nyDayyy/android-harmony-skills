#!/usr/bin/env python3
"""Build and seal one immutable HarmonyOS Phase 4 source snapshot."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from _common import (
    assert_no_secrets,
    atomic_json,
    atomic_text,
    build_project_snapshot,
    file_state,
    frozen_category_contracts,
    frozen_output_verdict,
    load_json,
    make_tree_read_only,
    manifest_text,
    read_csv,
    run_command,
    safe_relative_path,
    selector_is_present,
    sha256_file,
    utc_now,
    validate_actor,
    validate_frozen_command,
    validate_hap,
    validate_id,
)


REQUIRED_SEQUENCE = ["TOOLCHAIN", "CLEAN_BUILD", "BUNDLE_CHECK", "SIGNING_CHECK"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    plan_path = Path(args.plan).expanduser().resolve()
    project = workspace / "harmony-project"
    try:
        phase_manifest = load_json(workspace / "phase-manifest.json")
        plan = load_json(plan_path)
        if phase_manifest.get("phase") != 4 or not project.is_dir():
            raise ValueError("Not an initialized Phase 4 workspace")
        if (workspace / "CLOSED").exists():
            raise ValueError("Phase 4 is CLOSED; new builds are prohibited")
        if not isinstance(plan, dict):
            raise ValueError("Build plan must be an object")
        assert_no_secrets(plan)
        if ".mp4" in json.dumps(plan, ensure_ascii=False).lower():
            raise ValueError("MP4 is prohibited in Phase 4 plans")
        hbuild_id = validate_id(str(plan.get("hbuild_id", "")), "HBUILD-ID")
        h4env_id = validate_id(str(plan.get("h4env_id", "")), "H4ENV-ID")
        executed_by = validate_actor(str(plan.get("executed_by", "")), "build executor")
        expected_executor = phase_manifest.get("ownership", {}).get(
            "verification_executor_id"
        ) or phase_manifest.get("roles", {}).get("verification_executor")
        if executed_by != expected_executor:
            raise ValueError("Only the frozen emulator verification executor may seal a build")
        final_dir = workspace / "builds" / hbuild_id
        if final_dir.exists():
            raise ValueError(f"HBUILD-ID already exists; overwrite is prohibited: {hbuild_id}")

        env_path = workspace / "environments" / h4env_id / "phase4-environment.json"
        environment = load_json(env_path)
        registry = read_csv(workspace / "environments" / "h4env-registry.csv")
        env_row = next((row for row in registry if row.get("h4env_id") == h4env_id), None)
        if (
            not env_row or env_row.get("status") != "FROZEN"
            or sha256_file(env_path) != env_row.get("environment_sha256")
        ):
            raise ValueError(f"H4ENV is missing, changed, or not FROZEN: {h4env_id}")
        contracts = frozen_category_contracts(environment)
        bundle_name = str(environment.get("base_application", {}).get("bundle_name", ""))
        serial = str(environment.get("emulator", {}).get("serial", ""))
        selector_tokens = environment.get("device_selector_tokens", [])
        if not bundle_name or not serial or not isinstance(selector_tokens, list):
            raise ValueError("H4ENV lacks frozen Bundle, serial, or selector tokens")

        artifact_values = plan.get("artifact_paths")
        if (
            not isinstance(artifact_values, list) or len(artifact_values) != 1
            or not isinstance(artifact_values[0], str) or not artifact_values[0]
            or not artifact_values[0].lower().endswith(".hap")
        ):
            raise ValueError("Build plan requires exactly one relative .hap artifact path")
        artifact_relative = artifact_values[0]
        artifact_path = safe_relative_path(
            project, artifact_relative, "build artifact", must_exist=False
        )

        commands = plan.get("commands")
        if not isinstance(commands, list) or len(commands) != len(REQUIRED_SEQUENCE):
            raise ValueError(f"Build plan requires exactly this command sequence: {REQUIRED_SEQUENCE}")
        normalized: list[dict[str, Any]] = []
        command_ids: set[str] = set()
        categories: list[str] = []
        for index, command in enumerate(commands):
            if not isinstance(command, dict):
                raise ValueError(f"commands[{index}] must be an object")
            command_id = validate_id(str(command.get("command_id", "")), "Command-ID")
            if command_id in command_ids:
                raise ValueError(f"Duplicate Command-ID: {command_id}")
            command_ids.add(command_id)
            category = str(command.get("category", ""))
            categories.append(category)
            cwd = safe_relative_path(project, str(command.get("cwd", ".")), "build command cwd")
            if not cwd.is_dir():
                raise ValueError(f"Build command cwd is not a directory: {cwd}")
            argv, contract = validate_frozen_command(category, command.get("argv"), contracts)
            if category == "CLEAN_BUILD" and "{ARTIFACT}" not in argv:
                raise ValueError("CLEAN_BUILD argv must contain the exact {ARTIFACT} placeholder")
            if category != "CLEAN_BUILD" and any("{ARTIFACT}" in token for token in argv):
                raise ValueError(f"{category} must not contain the build artifact placeholder")
            if category == "BUNDLE_CHECK":
                if serial not in argv or bundle_name not in argv or not selector_is_present(
                    argv, selector_tokens
                ):
                    raise ValueError("BUNDLE_CHECK must bind the exact selector, serial, and Bundle")
            if category == "SIGNING_CHECK" and bundle_name not in argv:
                raise ValueError("SIGNING_CHECK must bind the exact frozen Bundle")
            normalized.append(
                {
                    "command_id": command_id,
                    "category": category,
                    "cwd": cwd,
                    "plan_argv": argv,
                    "contract": contract,
                }
            )
        if categories != REQUIRED_SEQUENCE:
            raise ValueError(f"Build command order differs; expected {REQUIRED_SEQUENCE}, got {categories}")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    before = build_project_snapshot(project)
    artifact_before = file_state(artifact_path)
    artifact_after_build: dict[str, Any] | None = None
    command_records: list[dict[str, Any]] = []
    errors: list[str] = []
    with tempfile.TemporaryDirectory(prefix=f".{hbuild_id}-", dir=workspace / ".staging") as temp_name:
        staging = Path(temp_name)
        logs = staging / "logs"
        logs.mkdir()
        for command in normalized:
            contract = command["contract"]
            if sha256_file(Path(contract["resolved_executable"])) != contract["executable_sha256"]:
                errors.append(f"{command['command_id']}: frozen executable changed before execution")
                continue
            executed_argv = [
                str(artifact_path) if token == "{ARTIFACT}" else token
                for token in command["plan_argv"]
            ]
            raw = run_command(executed_argv, command["cwd"], args.timeout)
            stdout = raw.pop("stdout")
            stderr = raw.pop("stderr")
            stdout_path = logs / f"{command['command_id']}.stdout.log"
            stderr_path = logs / f"{command['command_id']}.stderr.log"
            atomic_text(stdout_path, stdout)
            atomic_text(stderr_path, stderr)
            output_ok, success_hits, error_hits = frozen_output_verdict(
                stdout, stderr, contract
            )
            passed = (
                raw.get("exit_code") == 0 and raw.get("timed_out") is False
                and raw.get("semantic_error") is False and output_ok
            )
            record = {
                **raw,
                "command_id": command["command_id"],
                "category": command["category"],
                "plan_argv": command["plan_argv"],
                "argv": executed_argv,
                "resolved_executable": contract["resolved_executable"],
                "executable_sha256": contract["executable_sha256"],
                "required_argv_tokens": contract["required_argv_tokens"],
                "success_output_contains": contract["success_output_contains"],
                "error_output_contains": contract["error_output_contains"],
                "success_output_matches": success_hits,
                "error_output_matches": error_hits,
                "stdout_path": stdout_path.relative_to(staging).as_posix(),
                "stdout_sha256": sha256_file(stdout_path),
                "stderr_path": stderr_path.relative_to(staging).as_posix(),
                "stderr_sha256": sha256_file(stderr_path),
                "command_verdict": "PASS" if passed else "FAIL",
            }
            command_records.append(record)
            if not passed:
                errors.append(
                    f"{command['command_id']} ({command['category']}) failed: "
                    f"exit={raw.get('exit_code')}, success_markers={success_hits}, "
                    f"error_markers={error_hits}"
                )
                continue
            if command["category"] == "CLEAN_BUILD":
                try:
                    state = file_state(artifact_path)
                    if state is None:
                        raise ValueError("CLEAN_BUILD did not create the declared HAP")
                    if artifact_before is not None and state == artifact_before:
                        raise ValueError("CLEAN_BUILD did not create or update the declared HAP")
                    validate_hap(artifact_path)
                    artifact_after_build = state
                    record["artifact_state_after_clean_build"] = state
                except ValueError as exc:
                    errors.append(str(exc))

        after = build_project_snapshot(project)
        if before["snapshot_sha256"] != after["snapshot_sha256"]:
            errors.append("Build commands changed controlled source files")
        atomic_json(staging / "source-snapshot.json", before)

        artifact_records: list[dict[str, Any]] = []
        try:
            final_state = file_state(artifact_path)
            if final_state is None or artifact_after_build is None or final_state != artifact_after_build:
                raise ValueError("Declared HAP is absent or changed after CLEAN_BUILD")
            validate_hap(artifact_path)
            artifact_root = staging / "artifacts"
            artifact_root.mkdir()
            target = artifact_root / artifact_path.name
            shutil.copyfile(artifact_path, target)
            validate_hap(target)
            artifact_records.append(
                {
                    "source_relative_path": Path(artifact_relative).as_posix(),
                    "sealed_relative_path": target.relative_to(staging).as_posix(),
                    "sha256": sha256_file(target),
                    "size": target.stat().st_size,
                    "produced_by_command_id": next(
                        item["command_id"] for item in command_records
                        if item["category"] == "CLEAN_BUILD"
                    ),
                }
            )
        except (ValueError, OSError, StopIteration) as exc:
            errors.append(str(exc))
        atomic_json(staging / "artifact-manifest.json", {"artifacts": artifact_records})

        if errors:
            attempt = {
                "attempt_id": "ATT-" + hbuild_id,
                "hbuild_id": hbuild_id,
                "h4env_id": h4env_id,
                "executed_by": executed_by,
                "attempted_at": utc_now(),
                "commands": command_records,
                "errors": errors,
                "valid_build_id": None,
            }
            atomic_json(workspace / "attempts" / f"ATT-{hbuild_id}.json", attempt)
            print(json.dumps(attempt, ensure_ascii=False, indent=2))
            return 1

        metadata = {
            "hbuild_id": hbuild_id,
            "h4env_id": h4env_id,
            "executed_by": executed_by,
            "created_at": utc_now(),
            "status": "PASS",
            "input_lock_sha256": sha256_file(workspace / "stage-04-input-lock.json"),
            "environment_sha256": sha256_file(env_path),
            "source_snapshot_sha256": before["snapshot_sha256"],
            "bundle_name": bundle_name,
            "device_id": environment.get("device_id"),
            "device_serial": serial,
            "primary_artifact": artifact_records[0],
            "artifact_count": len(artifact_records),
            "commands": command_records,
        }
        atomic_json(staging / "metadata.json", metadata)
        relative_names = [
            path.relative_to(staging).as_posix() for path in staging.rglob("*")
            if path.is_file()
            and path.relative_to(staging).as_posix() not in {"manifest.sha256", "COMMITTED"}
        ]
        atomic_text(staging / "manifest.sha256", manifest_text(staging, relative_names))
        manifest_sha = sha256_file(staging / "manifest.sha256")
        atomic_text(
            staging / "COMMITTED",
            f"{hbuild_id} PASS manifest_sha256={manifest_sha} committed_at={utc_now()}\n",
        )
        staging.rename(final_dir)
        make_tree_read_only(final_dir)

    print(json.dumps({"hbuild_id": hbuild_id, "path": str(final_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
