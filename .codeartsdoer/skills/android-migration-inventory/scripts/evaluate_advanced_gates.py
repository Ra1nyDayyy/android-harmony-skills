#!/usr/bin/env python3
"""Deterministically gate dynamic surfaces, non-UI effects, and exceptional scenarios."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from _common import atomic_json, load_json, read_csv, safe_workspace_path, sha256_file
from evaluate_page_gates import (
    ACTIVE_EVIDENCE, add_error, evidence_record, expected_environments, nodes,
    verify_static_package,
)
from seal_side_effect_probe import changed_paths, verify_adapter_record


COLLECTIONS = {
    "DYNAMIC_RISK": ("dynamic_risks", "risk_id"),
    "SIDE_EFFECT": ("side_effects", "candidate_id"),
    "SCENARIO": ("scenarios", "scenario_id"),
}
OBSERVATION_FIELDS = {
    "observation_id", "subject_type", "subject_id", "page_id", "env_id",
    "evidence_id", "probe_evidence_id",
}


def verify_probe(workspace: Path, probe_id: str, expected: tuple[str, str, str],
                 index: dict[str, dict[str, str]], errors: list[str]) -> None:
    row = index.get(probe_id)
    if not row or row.get("status") not in ACTIVE_EVIDENCE:
        add_error(errors, f"Advanced observation references inactive probe evidence: {probe_id}")
        return
    candidate_id, page_id, env_id = expected
    if (row.get("candidate_id"), row.get("page_id"), row.get("env_id")) != expected:
        add_error(errors, f"Probe evidence identity mismatch: {probe_id}")
        return
    try:
        directory = safe_workspace_path(workspace, row.get("relative_path", ""))
        manifest = directory / "manifest.sha256"
        if (directory / "COMMITTED").read_text(encoding="utf-8") != sha256_file(manifest) + "\n":
            raise ValueError(f"Probe COMMITTED marker is invalid: {probe_id}")
        listed: dict[str, str] = {}
        for line in manifest.read_text(encoding="utf-8").splitlines():
            digest, name = line.split("  ", 1)
            if name in listed or "/" in name or "\\" in name:
                raise ValueError(f"Unsafe probe manifest entry: {probe_id}/{name}")
            if not (directory / name).is_file() or sha256_file(directory / name) != digest:
                raise ValueError(f"Probe artifact hash mismatch: {probe_id}/{name}")
            listed[name] = digest
        required = {"before.json", "after.json", "adapter-record.json", "diff.json", "metadata.json"}
        if not required.issubset(listed) or set(listed) - required - {"expected.json"}:
            raise ValueError(f"Probe manifest coverage is invalid: {probe_id}")
        metadata = load_json(directory / "metadata.json")
        if sha256_file(directory / "metadata.json") != row.get("metadata_sha256"):
            raise ValueError(f"Probe metadata hash mismatch: {probe_id}")
        if (metadata.get("probe_evidence_id"), metadata.get("candidate_id"),
                metadata.get("page_id"), metadata.get("env_id")) != (
                probe_id, candidate_id, page_id, env_id):
            raise ValueError(f"Probe metadata identity mismatch: {probe_id}")
        if metadata.get("status") not in ACTIVE_EVIDENCE or metadata.get("machine_result") != "PASS":
            raise ValueError(f"Probe did not produce a machine PASS: {probe_id}")
        before, after = load_json(directory / "before.json"), load_json(directory / "after.json")
        differences = changed_paths(before, after)
        if load_json(directory / "diff.json").get("changed_paths") != differences:
            raise ValueError(f"Probe diff cannot be reproduced: {probe_id}")
        comparator = metadata.get("comparator")
        actual = {
            "CHANGED": bool(differences), "UNCHANGED": not differences,
            "EQUALS_EXPECTED": (
                (directory / "expected.json").is_file()
                and after == load_json(directory / "expected.json")
            ),
        }.get(comparator, False)
        if not actual:
            raise ValueError(f"Probe comparator cannot be reproduced: {probe_id}")
        for name, field in (("before.json", "before_sha256"), ("after.json", "after_sha256"),
                            ("adapter-record.json", "adapter_record_sha256"),
                            ("diff.json", "diff_sha256")):
            if sha256_file(directory / name) != metadata.get(field):
                raise ValueError(f"Probe metadata digest mismatch: {probe_id}/{name}")
        verify_adapter_record(directory / "adapter-record.json")
    except (OSError, ValueError) as exc:
        add_error(errors, str(exc))


def evaluate_advanced_gates(workspace: Path, *, write_report: bool = True) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    errors: list[str] = []
    static = verify_static_package(workspace, errors)
    pages = {row.get("page_id"): row for row in static["PAGE"] if row.get("page_id")}
    advanced = load_json(workspace / "static-analysis" / "advanced-analysis.json")
    env_rows = load_json(workspace / "environments.json").get("environments", [])
    all_envs = {str(row.get("env_id")) for row in env_rows if isinstance(row, dict) and row.get("env_id")}
    feature_envs: dict[str, set[str]] = {}
    for row in read_csv(workspace / "coverage-ledger.csv"):
        try:
            values = json.loads(row.get("applicable_env_ids", ""))
        except json.JSONDecodeError:
            values = []
        feature_envs[row.get("feature_id", "")] = {str(value) for value in values if value}

    expected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for kind, (collection, id_field) in COLLECTIONS.items():
        rows = advanced.get(collection, [])
        if not isinstance(rows, list):
            add_error(errors, f"advanced-analysis.json has an invalid {collection} array")
            continue
        for row in rows:
            if not isinstance(row, dict) or not row.get(id_field):
                add_error(errors, f"Advanced candidate is missing {id_field}")
                continue
            candidate_page = pages.get(row.get("page_id"))
            pseudo_page = {"candidate_feature_ids": row.get("candidate_feature_ids", [])}
            envs = expected_environments(candidate_page or pseudo_page, all_envs, feature_envs)
            for env_id in envs:
                expected[(kind, str(row[id_field]), env_id)] = row

    document = load_json(workspace / "advanced-observations.json")
    if document.get("schema_version") != 1 or set(document) - {"schema_version", "observations"}:
        add_error(errors, "advanced-observations.json has an invalid schema or forbidden verdict fields")
    observations = document.get("observations", [])
    if not isinstance(observations, list):
        observations = []
        add_error(errors, "advanced-observations.json must contain an observations array")
    observed: dict[tuple[str, str, str], dict[str, Any]] = {}
    ids: set[str] = set()
    for row in observations:
        if not isinstance(row, dict) or set(row) - OBSERVATION_FIELDS:
            add_error(errors, "Advanced observation contains forbidden fields")
            continue
        key = (str(row.get("subject_type", "")), str(row.get("subject_id", "")),
               str(row.get("env_id", "")))
        observation_id = str(row.get("observation_id", ""))
        if not observation_id or observation_id in ids or key in observed:
            add_error(errors, f"Duplicate or missing advanced observation identity: {key}")
        ids.add(observation_id)
        observed[key] = row
        if key not in expected:
            add_error(errors, f"Advanced observation is not required by static analysis: {key}")
    for key in expected:
        if key not in observed:
            add_error(errors, f"Missing advanced observation: {key}")

    evidence_index = {row.get("evidence_id"): row for row in read_csv(workspace / "evidence-index.csv")}
    inventory_evidence = {row.get("evidence_id") for row in read_csv(workspace / "inventory.csv")
                          if row.get("row_status") != "SUPERSEDED"}
    probe_index = {row.get("probe_evidence_id"): row for row in
                   read_csv(workspace / "probe-evidence-index.csv")}
    cache: dict[str, tuple[dict[str, Any], Any, Path]] = {}
    for key, subject in expected.items():
        observation = observed.get(key)
        if not observation:
            continue
        kind, subject_id, env_id = key
        static_page = str(subject.get("page_id", ""))
        page_id = static_page if static_page in pages else str(observation.get("page_id", ""))
        if page_id not in pages or observation.get("page_id") != page_id:
            add_error(errors, f"Advanced observation does not resolve a known page: {key}")
        record = evidence_record(workspace, str(observation.get("evidence_id", "")),
                                 evidence_index, inventory_evidence, cache, errors)
        if record:
            metadata, layout, _ = record
            if metadata.get("env_id") != env_id or metadata.get("page_id") != page_id:
                add_error(errors, f"Advanced runtime evidence page/environment mismatch: {key}")
            if not list(nodes(layout)):
                add_error(errors, f"Advanced runtime layout is empty: {key}")
        probe_id = str(observation.get("probe_evidence_id", ""))
        if kind in {"SIDE_EFFECT", "SCENARIO"}:
            if not probe_id:
                add_error(errors, f"Advanced candidate lacks machine probe evidence: {key}")
            else:
                verify_probe(workspace, probe_id, (subject_id, page_id, env_id), probe_index, errors)
        elif probe_id:
            add_error(errors, f"Dynamic-risk observation must not smuggle a manual probe verdict: {key}")

    counts = {kind: sum(1 for key in expected if key[0] == kind) for kind in COLLECTIONS}
    report = {
        "schema_version": 1,
        "decision_source": "DETERMINISTIC_ADVANCED_RUNTIME_AND_PROBE_GATE",
        "machine_verdict": "PASS" if not errors else "BLOCKED",
        "required_observations": len(expected), "received_observations": len(observed),
        "required_by_category": counts, "errors": errors,
    }
    if write_report:
        atomic_json(workspace / "advanced-gate-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    report = evaluate_advanced_gates(Path(args.workspace))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["machine_verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
