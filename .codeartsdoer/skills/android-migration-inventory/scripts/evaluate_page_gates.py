#!/usr/bin/env python3
"""Compute Phase 2 page gates from static subjects and immutable runtime evidence."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from _common import atomic_json, load_json, read_csv, safe_workspace_path, sha256_file


SUBJECT_FILES = {
    "PAGE": ("pages.json", "pages", "page_id"),
    "COMPONENT": ("components.json", "components", "component_id"),
    "EVENT": ("events.json", "events", "event_id"),
    "TRANSITION": ("transitions.json", "transitions", "transition_id"),
    "STATE": ("state-candidates.json", "states", "state_id"),
}
OBSERVATION_FIELDS = {
    "observation_id", "subject_type", "subject_id", "page_id", "env_id",
    "before_evidence_id", "after_evidence_id", "locator_field", "locator_value",
    "locator_occurrence",
}
ACTIVE_EVIDENCE = {"SEALED", "ACCEPTED"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def add_error(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


def normalize_resource_id(value: Any) -> str:
    text = str(value or "").strip()
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    if ":id/" in text:
        text = text.rsplit(":id/", 1)[-1]
    return text


def nodes(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)


def matching_nodes(layout: Any, field: str, value: str) -> list[dict[str, Any]]:
    aliases = {
        "resource_id": {"resourceid", "resource-id", "resource_id", "id"},
        "text": {"text"},
        "type": {"type", "class", "classname", "class_name"},
        "content_description": {"contentdescription", "content-desc", "content_description"},
        "test_tag": {"testtag", "test_tag"},
    }
    allowed = aliases.get(field, set())
    result = []
    for node in nodes(layout):
        for key, actual in node.items():
            if str(key).lower() not in allowed:
                continue
            if field == "resource_id":
                matched = normalize_resource_id(actual) == normalize_resource_id(value)
            elif field == "type":
                matched = str(actual) == value or str(actual).rsplit(".", 1)[-1] == value.rsplit(".", 1)[-1]
            else:
                matched = str(actual) == value
            if matched:
                result.append(node)
                break
    return result


def verify_static_package(workspace: Path, errors: list[str]) -> dict[str, list[dict[str, Any]]]:
    package = workspace / "static-analysis"
    manifest = package / "manifest.sha256"
    committed = package / "COMMITTED"
    expected_names = {
        "project-index.json", "pages.json", "components.json", "events.json", "transitions.json",
        "state-candidates.json", "runtime-tasks.json", "advanced-analysis.json",
        "code-map.candidates.csv",
    }
    if not manifest.is_file() or not committed.is_file():
        add_error(errors, "Static-analysis package is not committed")
        return {kind: [] for kind in SUBJECT_FILES}
    if committed.read_text(encoding="utf-8") != sha256_file(manifest) + "\n":
        add_error(errors, "Static-analysis COMMITTED marker is invalid")
    listed: set[str] = set()
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if "  " not in line:
            add_error(errors, "Static-analysis manifest is malformed")
            continue
        digest, name = line.split("  ", 1)
        artifact = package / name
        if not SHA256_RE.fullmatch(digest) or name in listed or "/" in name or "\\" in name:
            add_error(errors, f"Unsafe or duplicate static artifact: {name}")
        elif not artifact.is_file() or sha256_file(artifact) != digest:
            add_error(errors, f"Static artifact hash mismatch: {name}")
        listed.add(name)
    if listed != expected_names:
        add_error(errors, "Static-analysis manifest has incomplete coverage")

    result: dict[str, list[dict[str, Any]]] = {}
    for kind, (filename, collection, id_field) in SUBJECT_FILES.items():
        try:
            rows = load_json(package / filename).get(collection, [])
        except (AttributeError, ValueError) as exc:
            add_error(errors, str(exc))
            rows = []
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            add_error(errors, f"{filename} has an invalid {collection} array")
            rows = []
        ids = [row.get(id_field) for row in rows]
        if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
            add_error(errors, f"{filename} has missing or duplicate {id_field}")
        result[kind] = rows
    if not result["PAGE"]:
        add_error(errors, "Static analysis contains no pages")
    return result


def evidence_record(
    workspace: Path,
    evidence_id: str,
    index: dict[str, dict[str, str]],
    active_inventory_evidence: set[str],
    cache: dict[str, tuple[dict[str, Any], Any, Path]],
    errors: list[str],
) -> tuple[dict[str, Any], Any, Path] | None:
    if evidence_id in cache:
        return cache[evidence_id]
    row = index.get(evidence_id)
    if not row or row.get("status") not in ACTIVE_EVIDENCE:
        add_error(errors, f"Observation references inactive evidence: {evidence_id}")
        return None
    if evidence_id not in active_inventory_evidence:
        add_error(errors, f"Observation evidence is not bound to an active inventory row: {evidence_id}")
        return None
    try:
        directory = safe_workspace_path(workspace, row.get("relative_path", ""))
        metadata_path = directory / "metadata.json"
        layout_path = directory / "layout.json"
        metadata = load_json(metadata_path)
        layout = load_json(layout_path)
        if sha256_file(metadata_path) != row.get("metadata_sha256"):
            raise ValueError(f"Evidence metadata hash mismatch: {evidence_id}")
        if metadata.get("evidence_id") != evidence_id or metadata.get("status") not in ACTIVE_EVIDENCE:
            raise ValueError(f"Evidence metadata identity/lifecycle mismatch: {evidence_id}")
    except (OSError, ValueError) as exc:
        add_error(errors, str(exc))
        return None
    cache[evidence_id] = (metadata, layout, directory)
    return cache[evidence_id]


def expected_environments(
    page: dict[str, Any], all_envs: set[str], feature_envs: dict[str, set[str]]
) -> set[str]:
    result: set[str] = set()
    for feature_id in page.get("candidate_feature_ids", []):
        result.update(feature_envs.get(str(feature_id), set()))
    return result or all_envs


def evaluate_page_gates(workspace: Path, *, write_report: bool = True) -> dict[str, Any]:
    workspace = workspace.expanduser().resolve()
    errors: list[str] = []
    subjects = verify_static_package(workspace, errors)
    pages = {row.get("page_id", ""): row for row in subjects["PAGE"] if row.get("page_id")}

    try:
        environments = load_json(workspace / "environments.json").get("environments", [])
        all_envs = {str(row.get("env_id")) for row in environments if isinstance(row, dict) and row.get("env_id")}
        coverage = read_csv(workspace / "coverage-ledger.csv")
        inventory = read_csv(workspace / "inventory.csv")
        index_rows = read_csv(workspace / "evidence-index.csv")
        observation_document = load_json(workspace / "runtime-observations.json")
    except (AttributeError, ValueError) as exc:
        add_error(errors, str(exc))
        environments, coverage, inventory, index_rows, all_envs = [], [], [], [], set()
        observation_document = {"schema_version": 0, "observations": []}
    if not all_envs:
        add_error(errors, "No frozen runtime environments are available")

    feature_envs: dict[str, set[str]] = {}
    for row in coverage:
        try:
            values = json.loads(row.get("applicable_env_ids", ""))
        except json.JSONDecodeError:
            values = []
        if isinstance(values, list):
            feature_envs[row.get("feature_id", "")] = {str(value) for value in values if value}

    expected: dict[tuple[str, str, str], dict[str, Any]] = {}
    for page_id, page in pages.items():
        for env_id in expected_environments(page, all_envs, feature_envs):
            expected[("PAGE", page_id, env_id)] = page
    for kind in ("COMPONENT", "EVENT", "STATE"):
        id_field = SUBJECT_FILES[kind][2]
        for row in subjects[kind]:
            page_id = str(row.get("page_id", ""))
            page = pages.get(page_id)
            if not page and kind == "COMPONENT":
                add_error(errors, f"{kind} subject has no resolved static page: {row.get(id_field)}")
                continue
            environments_for_subject = (
                expected_environments(page, all_envs, feature_envs) if page else all_envs
            )
            for env_id in environments_for_subject:
                expected[(kind, str(row.get(id_field)), env_id)] = row
    for row in subjects["TRANSITION"]:
        transition_id = str(row.get("transition_id", ""))
        page_id = str(row.get("source_page_id", ""))
        page = pages.get(page_id)
        environments_for_subject = (
            expected_environments(page, all_envs, feature_envs) if page else all_envs
        )
        for env_id in environments_for_subject:
            expected[("TRANSITION", transition_id, env_id)] = row

    if observation_document.get("schema_version") != 1:
        add_error(errors, "runtime-observations.json has an unsupported schema_version")
    document_unknown = set(observation_document) - {"schema_version", "observations"}
    if document_unknown:
        add_error(errors, f"runtime-observations.json contains forbidden verdict fields: {sorted(document_unknown)}")
    observations = observation_document.get("observations", [])
    if not isinstance(observations, list) or not all(isinstance(row, dict) for row in observations):
        add_error(errors, "runtime-observations.json must contain an observations array")
        observations = []
    observation_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    observation_ids: set[str] = set()
    for row in observations:
        unknown = set(row) - OBSERVATION_FIELDS
        if unknown:
            add_error(errors, f"Runtime observation contains forbidden fields: {sorted(unknown)}")
        observation_id = str(row.get("observation_id", ""))
        kind = str(row.get("subject_type", ""))
        subject_id = str(row.get("subject_id", ""))
        env_id = str(row.get("env_id", ""))
        key = (kind, subject_id, env_id)
        if not observation_id or observation_id in observation_ids:
            add_error(errors, f"Missing or duplicate Observation-ID: {observation_id!r}")
        observation_ids.add(observation_id)
        if kind not in SUBJECT_FILES:
            add_error(errors, f"Invalid observation subject type: {kind}")
        if key in observation_by_key:
            add_error(errors, f"Duplicate runtime observation for {key}")
        observation_by_key[key] = row
        if key not in expected:
            add_error(errors, f"Runtime observation does not match a required static subject: {key}")
    for key in expected:
        if key not in observation_by_key:
            add_error(errors, f"Missing runtime observation: {key}")

    index = {row.get("evidence_id", ""): row for row in index_rows if row.get("evidence_id")}
    active_inventory_evidence = {
        row.get("evidence_id", "") for row in inventory if row.get("row_status") != "SUPERSEDED"
    }
    cache: dict[str, tuple[dict[str, Any], Any, Path]] = {}
    locator_owners: dict[tuple[str, str, str, str, int], str] = {}
    page_errors: dict[str, list[str]] = {page_id: [] for page_id in pages}

    def page_error(page_id: str, message: str) -> None:
        add_error(errors, message)
        page_errors.setdefault(page_id, [])
        if message not in page_errors[page_id]:
            page_errors[page_id].append(message)

    for key, subject in expected.items():
        kind, subject_id, env_id = key
        observation = observation_by_key.get(key)
        static_page_id = str(subject.get("page_id") or subject.get("source_page_id") or "")
        page_id = static_page_id if static_page_id in pages else str(observation.get("page_id", "") if observation else "")
        if not observation:
            page_errors.setdefault(page_id, []).append(f"Missing {kind} observation: {subject_id}/{env_id}")
            continue
        if page_id not in pages:
            page_error(page_id, f"Observation did not resolve a known source page: {subject_id}/{env_id}")
        if observation.get("page_id") != page_id:
            page_error(page_id, f"Observation page differs from static subject: {subject_id}/{env_id}")
        after_id = str(observation.get("after_evidence_id", ""))
        after = evidence_record(workspace, after_id, index, active_inventory_evidence, cache, errors)
        if not after:
            page_errors.setdefault(page_id, []).append(f"Invalid after evidence: {subject_id}/{env_id}")
            continue
        after_meta, after_layout, after_dir = after
        if after_meta.get("env_id") != env_id:
            page_error(page_id, f"Observation environment differs from evidence: {subject_id}/{env_id}")

        if kind in {"PAGE", "COMPONENT", "STATE"} and after_meta.get("page_id") != page_id:
            page_error(page_id, f"Evidence page differs from static subject: {subject_id}/{env_id}")
        if kind == "PAGE" and not list(nodes(after_layout)):
            page_error(page_id, f"Page layout is empty: {subject_id}/{env_id}")
        elif kind == "COMPONENT":
            resource_id = str(subject.get("resource_id", ""))
            if resource_id:
                locator_field, locator_value = "resource_id", resource_id
                occurrence = 1
            else:
                locator_field = str(observation.get("locator_field", ""))
                locator_value = str(observation.get("locator_value", ""))
                try:
                    occurrence = int(observation.get("locator_occurrence", 0))
                except (TypeError, ValueError):
                    occurrence = 0
                allowed_values = {
                    "text": str(subject.get("text", "")),
                    "type": str(subject.get("type", "")),
                    "content_description": str(subject.get("attributes", {}).get("contentDescription", "")),
                    "test_tag": str(subject.get("attributes", {}).get("testTag", "")),
                }
                if locator_field not in allowed_values or not locator_value or locator_value != allowed_values[locator_field]:
                    page_error(page_id, f"Component lacks a source-derived runtime locator: {subject_id}/{env_id}")
                    continue
            matches = matching_nodes(after_layout, locator_field, locator_value)
            if occurrence < 1 or len(matches) < occurrence:
                page_error(page_id, f"Component is absent from runtime layout: {subject_id}/{env_id}")
                continue
            locator_key = (page_id, env_id, locator_field, locator_value, occurrence)
            owner = locator_owners.get(locator_key)
            if owner and owner != subject_id:
                page_error(page_id, f"Two static components reuse one runtime node: {owner}/{subject_id}/{env_id}")
            locator_owners[locator_key] = subject_id
        elif kind in {"EVENT", "TRANSITION"}:
            before_id = str(observation.get("before_evidence_id", ""))
            before = evidence_record(workspace, before_id, index, active_inventory_evidence, cache, errors)
            if not before:
                page_error(page_id, f"Action lacks valid predecessor evidence: {subject_id}/{env_id}")
                continue
            before_meta, _, before_dir = before
            if before_meta.get("page_id") != page_id or before_meta.get("env_id") != env_id:
                page_error(page_id, f"Action predecessor has the wrong page/environment: {subject_id}/{env_id}")
            if after_meta.get("predecessor_evidence_id") != before_id:
                page_error(page_id, f"Action evidence is not linked to its predecessor: {subject_id}/{env_id}")
            changed = any(
                sha256_file(before_dir / name) != sha256_file(after_dir / name)
                for name in ("layout.json", "screenshot.png")
            )
            if not changed:
                page_error(page_id, f"Action produced no observable runtime change: {subject_id}/{env_id}")
            if kind == "TRANSITION" and after_meta.get("page_id") != subject.get("target_page_id"):
                page_error(page_id, f"Transition did not reach its statically expected target: {subject_id}/{env_id}")

    page_reports = []
    for page_id, page in sorted(pages.items()):
        reasons = page_errors.get(page_id, [])
        def assigned_page(key: tuple[str, str, str], row: dict[str, Any]) -> str:
            static_page = str(row.get("page_id") or row.get("source_page_id") or "")
            observation = observation_by_key.get(key, {})
            return static_page if static_page in pages else str(observation.get("page_id", ""))

        required_count = sum(1 for key, row in expected.items() if assigned_page(key, row) == page_id)
        observed_count = sum(
            1 for key, row in expected.items()
            if assigned_page(key, row) == page_id and key in observation_by_key
        )
        page_reports.append({
            "page_id": page_id, "symbol": page.get("symbol", ""),
            "machine_verdict": "PAGE_PASS" if not reasons else "BLOCKED",
            "required_atomic_observations": required_count,
            "received_atomic_observations": observed_count,
            "errors": reasons,
        })

    report = {
        "schema_version": 1,
        "decision_source": "DETERMINISTIC_STATIC_RUNTIME_GATE",
        "machine_verdict": "PASS" if not errors and all(
            row["machine_verdict"] == "PAGE_PASS" for row in page_reports
        ) else "BLOCKED",
        "pages": page_reports,
        "required_atomic_observations": len(expected),
        "received_atomic_observations": len(observation_by_key),
        "errors": errors,
    }
    if write_report:
        atomic_json(workspace / "page-gate-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    report = evaluate_page_gates(Path(args.workspace))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["machine_verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
