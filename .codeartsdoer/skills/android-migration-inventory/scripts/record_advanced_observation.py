#!/usr/bin/env python3
"""Bind an advanced static candidate to runtime and probe evidence; no verdict is accepted."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from _common import atomic_json, exclusive_lock, load_json, read_csv, validate_id


COLLECTIONS = {
    "DYNAMIC_RISK": ("dynamic_risks", "risk_id"),
    "SIDE_EFFECT": ("side_effects", "candidate_id"),
    "SCENARIO": ("scenarios", "scenario_id"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--subject-type", required=True, choices=tuple(COLLECTIONS))
    parser.add_argument("--subject-id", required=True)
    parser.add_argument("--page-id", required=True)
    parser.add_argument("--env-id", required=True)
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--probe-evidence-id")
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    if (workspace / "CLOSED").exists():
        parser.error("Phase 2 is CLOSED; advanced observations are read-only")
    for value, label in ((args.subject_id, "Subject-ID"), (args.page_id, "Page-ID"),
                         (args.env_id, "ENV-ID"), (args.evidence_id, "Evidence-ID")):
        validate_id(value, label)
    requires_probe = args.subject_type in {"SIDE_EFFECT", "SCENARIO"}
    if requires_probe != bool(args.probe_evidence_id):
        parser.error("SIDE_EFFECT and SCENARIO require --probe-evidence-id; DYNAMIC_RISK prohibits it")
    if args.probe_evidence_id:
        validate_id(args.probe_evidence_id, "Probe-Evidence-ID")

    advanced = load_json(workspace / "static-analysis" / "advanced-analysis.json")
    collection, id_field = COLLECTIONS[args.subject_type]
    matches = [row for row in advanced.get(collection, [])
               if isinstance(row, dict) and row.get(id_field) == args.subject_id]
    if len(matches) != 1:
        parser.error("Subject-ID is not unique in the committed advanced analysis")
    static_page = matches[0].get("page_id")
    known_pages = {row.get("page_id") for row in
                   load_json(workspace / "static-analysis" / "pages.json").get("pages", [])
                   if isinstance(row, dict)}
    if static_page == "PENDING_SOURCE_BINDING":
        if args.page_id not in known_pages:
            parser.error("--page-id must resolve the candidate to a known page")
    elif static_page != args.page_id:
        parser.error("--page-id differs from the static candidate binding")

    environments = load_json(workspace / "environments.json").get("environments", [])
    if args.env_id not in {row.get("env_id") for row in environments if isinstance(row, dict)}:
        parser.error("Unknown ENV-ID")
    evidence = {row.get("evidence_id"): row for row in read_csv(workspace / "evidence-index.csv")}
    if evidence.get(args.evidence_id, {}).get("status") not in {"SEALED", "ACCEPTED"}:
        parser.error("Runtime evidence is not active and sealed")
    if args.probe_evidence_id:
        probes = {row.get("probe_evidence_id"): row for row in
                  read_csv(workspace / "probe-evidence-index.csv")}
        probe = probes.get(args.probe_evidence_id, {})
        if probe.get("status") not in {"SEALED", "ACCEPTED"}:
            parser.error("Probe evidence is not active and sealed")
        if (probe.get("candidate_id"), probe.get("page_id"), probe.get("env_id")) != (
                args.subject_id, args.page_id, args.env_id):
            parser.error("Probe evidence identity differs from the advanced observation")

    digest = hashlib.sha256(
        f"{args.subject_type}|{args.subject_id}|{args.env_id}".encode("utf-8")
    ).hexdigest()[:12].upper()
    row = {
        "observation_id": f"AOBS-{digest}", "subject_type": args.subject_type,
        "subject_id": args.subject_id, "page_id": args.page_id, "env_id": args.env_id,
        "evidence_id": args.evidence_id, "probe_evidence_id": args.probe_evidence_id or "",
    }
    path = workspace / "advanced-observations.json"
    with exclusive_lock(workspace / ".locks" / "advanced-observations.lock"):
        document = load_json(path)
        rows = document.get("observations", [])
        if not isinstance(rows, list):
            parser.error("advanced-observations.json has no observations array")
        key = (args.subject_type, args.subject_id, args.env_id)
        if any((item.get("subject_type"), item.get("subject_id"), item.get("env_id")) == key
               for item in rows if isinstance(item, dict)):
            parser.error("This advanced candidate/environment already has an observation")
        atomic_json(path, {"schema_version": 1, "observations": [*rows, row]})
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
