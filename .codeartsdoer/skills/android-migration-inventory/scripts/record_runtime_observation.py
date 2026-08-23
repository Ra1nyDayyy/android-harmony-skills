#!/usr/bin/env python3
"""Bind a static Phase 2 subject to runtime evidence without accepting a verdict."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from _common import atomic_json, exclusive_lock, load_json, read_csv, validate_id
from evaluate_page_gates import SUBJECT_FILES


def stable_observation_id(subject_type: str, subject_id: str, env_id: str) -> str:
    digest = hashlib.sha256(f"{subject_type}|{subject_id}|{env_id}".encode("utf-8")).hexdigest()[:12].upper()
    return f"OBS-{digest}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--subject-type", required=True, choices=tuple(SUBJECT_FILES))
    parser.add_argument("--subject-id", required=True)
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--env-id", required=True)
    parser.add_argument("--after-evidence", required=True)
    parser.add_argument("--before-evidence")
    parser.add_argument(
        "--locator-field", choices=("text", "type", "content_description", "test_tag")
    )
    parser.add_argument("--locator-value")
    parser.add_argument("--locator-occurrence", type=int, default=0)
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    if (workspace / "CLOSED").exists():
        parser.error("Phase 2 is CLOSED; runtime observations are read-only")
    for value, label in (
        (args.subject_id, "subject ID"), (args.page_id, "Page-ID"),
        (args.env_id, "ENV-ID"), (args.after_evidence, "after Evidence-ID"),
    ):
        validate_id(value, label)
    if args.before_evidence:
        validate_id(args.before_evidence, "before Evidence-ID")
    transition_like = args.subject_type in {"EVENT", "TRANSITION"}
    if transition_like != bool(args.before_evidence):
        parser.error("EVENT and TRANSITION observations require --before-evidence; other types prohibit it")
    if bool(args.locator_field) != bool(args.locator_value):
        parser.error("--locator-field and --locator-value must be supplied together")
    if args.locator_field and args.locator_occurrence < 1:
        parser.error("A runtime locator requires --locator-occurrence >= 1")
    if not args.locator_field and args.locator_occurrence:
        parser.error("--locator-occurrence requires a runtime locator")

    filename, collection, id_field = SUBJECT_FILES[args.subject_type]
    document = load_json(workspace / "static-analysis" / filename)
    matches = [
        row for row in document.get(collection, [])
        if isinstance(row, dict) and row.get(id_field) == args.subject_id
    ]
    if len(matches) != 1:
        parser.error("Subject-ID is not unique in the committed static package")
    subject = matches[0]
    expected_page = subject.get("source_page_id") if args.subject_type == "TRANSITION" else subject.get("page_id")
    if expected_page == "PENDING_SOURCE_BINDING":
        known_pages = {
            row.get("page_id") for row in load_json(workspace / "static-analysis" / "pages.json").get("pages", [])
            if isinstance(row, dict)
        }
        if args.page_id not in known_pages:
            parser.error("--page-id must resolve the subject to a known static page")
    elif expected_page != args.page_id:
        parser.error("--page-id differs from the subject's static source page")

    index = {row.get("evidence_id"): row for row in read_csv(workspace / "evidence-index.csv")}
    for evidence_id in filter(None, (args.before_evidence, args.after_evidence)):
        if index.get(evidence_id, {}).get("status") not in {"SEALED", "ACCEPTED"}:
            parser.error(f"Evidence is not active and sealed: {evidence_id}")

    observation_id = stable_observation_id(args.subject_type, args.subject_id, args.env_id)
    row = {
        "observation_id": observation_id,
        "subject_type": args.subject_type,
        "subject_id": args.subject_id,
        "page_id": args.page_id,
        "env_id": args.env_id,
        "before_evidence_id": args.before_evidence or "",
        "after_evidence_id": args.after_evidence,
        "locator_field": args.locator_field or "",
        "locator_value": args.locator_value or "",
        "locator_occurrence": args.locator_occurrence,
    }
    path = workspace / "runtime-observations.json"
    with exclusive_lock(workspace / ".locks" / "runtime-observations.lock"):
        value = load_json(path)
        observations = value.get("observations", [])
        if not isinstance(observations, list):
            parser.error("runtime-observations.json has no observations array")
        key = (args.subject_type, args.subject_id, args.env_id)
        if any(
            (item.get("subject_type"), item.get("subject_id"), item.get("env_id")) == key
            for item in observations if isinstance(item, dict)
        ):
            parser.error("This subject/environment already has a runtime observation; recapture evidence instead")
        atomic_json(path, {"schema_version": 1, "observations": [*observations, row]})
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
