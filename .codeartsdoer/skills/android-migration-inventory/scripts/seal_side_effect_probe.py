#!/usr/bin/env python3
"""Seal a hash-bound before/after side-effect or scenario probe for Phase 2."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from _common import (
    atomic_json, atomic_text, exclusive_lock, load_json, manifest_lines, read_csv,
    safe_workspace_path, sha256_file, validate_id, write_csv,
)


INDEX_FIELDS = [
    "probe_evidence_id", "candidate_id", "page_id", "env_id",
    "relative_path", "metadata_sha256", "status",
]
CLI_ERROR_RE = re.compile(r"(?im)^\s*(?:error|failed|failure)\s*:")


def changed_paths(before: Any, after: Any, prefix: str = "$") -> list[str]:
    if type(before) is not type(after):
        return [prefix]
    if isinstance(before, dict):
        result: list[str] = []
        for key in sorted(set(before) | set(after)):
            child = f"{prefix}.{key}"
            if key not in before or key not in after:
                result.append(child)
            else:
                result.extend(changed_paths(before[key], after[key], child))
        return result
    if isinstance(before, list):
        result = []
        for index in range(max(len(before), len(after))):
            child = f"{prefix}[{index}]"
            if index >= len(before) or index >= len(after):
                result.append(child)
            else:
                result.extend(changed_paths(before[index], after[index], child))
        return result
    return [] if before == after else [prefix]


def verify_adapter_record(path: Path) -> dict[str, Any]:
    record = load_json(path)
    if record.get("schema_version") != 1:
        raise ValueError("Adapter record has an unsupported schema_version")
    adapter = Path(str(record.get("adapter_path", ""))).expanduser().resolve()
    digest = str(record.get("adapter_sha256", ""))
    if not adapter.is_file() or sha256_file(adapter) != digest:
        raise ValueError("Probe adapter is missing or differs from its recorded SHA-256")
    commands = record.get("commands")
    if not isinstance(commands, list) or not commands:
        raise ValueError("Adapter record has no commands")
    for command in commands:
        argv = command.get("argv") if isinstance(command, dict) else None
        return_code = command.get("exit_code", command.get("returncode")) if isinstance(command, dict) else None
        if (
            not isinstance(argv, list) or not argv or str(Path(str(argv[0])).resolve()) != str(adapter)
            or return_code != 0 or command.get("timed_out") is True
            or CLI_ERROR_RE.search(str(command.get("stdout", "")))
            or CLI_ERROR_RE.search(str(command.get("stderr", "")))
        ):
            raise ValueError("Probe adapter command is not successful and hash-bound")
    return record


def set_read_only(directory: Path) -> None:
    for path in directory.iterdir():
        if path.is_file():
            os.chmod(path, 0o444)
    os.chmod(directory, 0o555)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--probe-evidence-id", required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--env-id", required=True)
    parser.add_argument("--before", required=True)
    parser.add_argument("--after", required=True)
    parser.add_argument("--adapter-record", required=True)
    parser.add_argument("--comparator", required=True, choices=("CHANGED", "UNCHANGED", "EQUALS_EXPECTED"))
    parser.add_argument("--expected")
    parser.add_argument("--produced-by", required=True)
    parser.add_argument("--sealed-by", required=True)
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    if (workspace / "CLOSED").exists():
        parser.error("Phase 2 is CLOSED; probe evidence is read-only")
    for value, label in (
        (args.probe_evidence_id, "Probe-Evidence-ID"), (args.candidate_id, "Candidate-ID"),
        (args.page_id, "Page-ID"), (args.env_id, "ENV-ID"),
    ):
        validate_id(value, label)
    phase = load_json(workspace / "phase-manifest.json")
    ownership = phase.get("ownership", {})
    if args.produced_by != ownership.get("data_dependency_agent_id"):
        parser.error("--produced-by must equal the frozen data-dependency agent")
    if args.sealed_by != ownership.get("evidence_administrator_id") or args.sealed_by == args.produced_by:
        parser.error("--sealed-by must be the distinct frozen evidence administrator")

    advanced = load_json(workspace / "static-analysis" / "advanced-analysis.json")
    candidates = {
        row.get("candidate_id"): ("SIDE_EFFECT", row)
        for row in advanced.get("side_effects", []) if isinstance(row, dict)
    }
    candidates.update({
        row.get("scenario_id"): ("SCENARIO", row)
        for row in advanced.get("scenarios", []) if isinstance(row, dict)
    })
    match = candidates.get(args.candidate_id)
    if not match:
        parser.error("--candidate-id is not in the committed advanced static analysis")
    subject_type, candidate = match
    static_page = candidate.get("page_id")
    if static_page not in {args.page_id, "PENDING_SOURCE_BINDING"}:
        parser.error("--page-id differs from the static side-effect binding")
    environments = load_json(workspace / "environments.json").get("environments", [])
    if args.env_id not in {row.get("env_id") for row in environments if isinstance(row, dict)}:
        parser.error("Unknown ENV-ID")

    before_path = Path(args.before).expanduser().resolve()
    after_path = Path(args.after).expanduser().resolve()
    expected_path = Path(args.expected).expanduser().resolve() if args.expected else None
    for path in filter(None, (before_path, after_path, expected_path)):
        if not path.is_file():
            parser.error(f"Probe JSON is missing: {path}")
    if (args.comparator == "EQUALS_EXPECTED") != bool(expected_path):
        parser.error("EQUALS_EXPECTED requires --expected, and other comparators prohibit it")
    try:
        before_value = load_json(before_path)
        after_value = load_json(after_path)
        expected_value = load_json(expected_path) if expected_path else None
        adapter_record = verify_adapter_record(Path(args.adapter_record).expanduser().resolve())
    except ValueError as exc:
        parser.error(str(exc))

    differences = changed_paths(before_value, after_value)
    machine_result = {
        "CHANGED": bool(differences),
        "UNCHANGED": not differences,
        "EQUALS_EXPECTED": after_value == expected_value,
    }[args.comparator]
    relative = f"probe-evidence/{args.env_id}/{args.candidate_id}/{args.probe_evidence_id}"
    final_dir = safe_workspace_path(workspace, relative)
    if final_dir.exists():
        parser.error("Probe-Evidence-ID already exists")
    staging_root = workspace / ".staging"
    with tempfile.TemporaryDirectory(prefix="probe-", dir=staging_root) as temp_name:
        staging = Path(temp_name)
        shutil.copyfile(before_path, staging / "before.json")
        shutil.copyfile(after_path, staging / "after.json")
        if expected_path:
            shutil.copyfile(expected_path, staging / "expected.json")
        atomic_json(staging / "adapter-record.json", adapter_record)
        atomic_json(staging / "diff.json", {"changed_paths": differences})
        metadata = {
            "schema_version": 1, "probe_evidence_id": args.probe_evidence_id,
            "candidate_id": args.candidate_id, "subject_type": subject_type,
            "effect_type": candidate.get("effect_type", ""),
            "scenario_type": candidate.get("scenario_type", ""),
            "page_id": args.page_id, "env_id": args.env_id, "comparator": args.comparator,
            "machine_result": "PASS" if machine_result else "FAIL",
            "produced_by": args.produced_by, "sealed_by": args.sealed_by,
            "before_sha256": sha256_file(staging / "before.json"),
            "after_sha256": sha256_file(staging / "after.json"),
            "expected_sha256": sha256_file(staging / "expected.json") if expected_path else "",
            "adapter_record_sha256": sha256_file(staging / "adapter-record.json"),
            "diff_sha256": sha256_file(staging / "diff.json"), "status": "SEALED",
        }
        atomic_json(staging / "metadata.json", metadata)
        names = ["before.json", "after.json", "adapter-record.json", "diff.json", "metadata.json"]
        if expected_path:
            names.append("expected.json")
        atomic_text(staging / "manifest.sha256", manifest_lines(staging, sorted(names)))
        atomic_text(staging / "COMMITTED", sha256_file(staging / "manifest.sha256") + "\n")
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(final_dir)
        set_read_only(final_dir)

    index_path = workspace / "probe-evidence-index.csv"
    with exclusive_lock(workspace / ".locks" / "probe-evidence-index.lock"):
        rows = read_csv(index_path)
        if any(row.get("probe_evidence_id") == args.probe_evidence_id for row in rows):
            raise ValueError("Duplicate Probe-Evidence-ID")
        rows.append({
            "probe_evidence_id": args.probe_evidence_id, "candidate_id": args.candidate_id,
            "page_id": args.page_id, "env_id": args.env_id, "relative_path": relative,
            "metadata_sha256": sha256_file(final_dir / "metadata.json"), "status": "SEALED",
        })
        write_csv(index_path, INDEX_FIELDS, rows)
    print(json.dumps({
        "probe_evidence_id": args.probe_evidence_id,
        "machine_result": "PASS" if machine_result else "FAIL",
        "relative_path": relative,
    }, ensure_ascii=False, indent=2))
    return 0 if machine_result else 1


if __name__ == "__main__":
    raise SystemExit(main())
