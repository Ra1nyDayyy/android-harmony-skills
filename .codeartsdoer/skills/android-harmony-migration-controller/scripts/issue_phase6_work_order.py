#!/usr/bin/env python3
"""Issue an immutable Phase 6 delivery-acceptance work order from Gate 5."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from _phase56_common import (
    ID_RE, SHA256_RE, active_work_order, all_frozen_actor_ids, contains_secret_key,
    external_json_file, file_record, load_json, persist_work_order, prepare_controller_records,
    require_current_gate, safe_run_file, sha256_file, utc_now, validate_roles,
)


ROLE_KEYS = (
    "delivery_lead_id",
    "candidate_custody_agent_id",
    "candidate_validation_agent_id",
    "material_consistency_agent_id",
    "delivery_acceptance_agent_id",
)


def validate_environment_configs(
    values: list[str], allowed_h5env_ids: set[str]
) -> list[tuple[Path, dict[str, Any]]]:
    if not values:
        raise ValueError("At least one --environment-config is required")
    records: list[tuple[Path, dict[str, Any]]] = []
    seen: set[str] = set()
    for value in values:
        path, environment = external_json_file(value, "Phase 6 environment config")
        if contains_secret_key(environment):
            raise ValueError(f"H6ENV must not contain secrets: {path}")
        h6env_id = str(environment.get("h6env_id", ""))
        base_h5env_id = str(environment.get("base_h5env_id", ""))
        if not ID_RE.fullmatch(h6env_id) or h6env_id in seen:
            raise ValueError(f"Unsafe or duplicate H6ENV-ID: {h6env_id!r}")
        if base_h5env_id not in allowed_h5env_ids:
            raise ValueError(f"{h6env_id} inherits unknown required H5ENV: {base_h5env_id!r}")
        seen.add(h6env_id)
        records.append((path, environment))
    return sorted(records, key=lambda item: str(item[1]["h6env_id"]))


def collect_candidate_artifacts(
    run_dir: Path, report: dict[str, Any], release_candidate_id: str
) -> list[dict[str, Any]]:
    values = report.get("candidate_artifacts")
    if not isinstance(values, list) or not values:
        raise ValueError("Gate 5 report has no candidate_artifacts")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    prefix = "phase-05-harmony-regression/"
    for index, item in enumerate(values):
        if not isinstance(item, dict):
            raise ValueError(f"Gate 5 candidate artifact {index} is not an object")
        relative = str(item.get("relative_path", ""))
        phase_relative = relative[len(prefix):] if relative.startswith(prefix) else relative
        pure = PurePosixPath(phase_relative)
        if (
            pure.is_absolute() or not pure.parts or ".." in pure.parts
            or pure.parts[:2] != ("release-candidates", release_candidate_id)
            or phase_relative in seen
        ):
            raise ValueError(f"Unsafe, noncanonical, or duplicate candidate artifact: {relative!r}")
        run_relative = prefix + phase_relative
        path = safe_run_file(run_dir, run_relative, f"Gate 5 candidate artifact {index}")
        digest = str(item.get("sha256", ""))
        size = item.get("size")
        if not SHA256_RE.fullmatch(digest) or digest != sha256_file(path) or size != path.stat().st_size:
            raise ValueError(f"Gate 5 candidate artifact bytes differ: {run_relative}")
        records.append({"relative_path": phase_relative, "sha256": digest, "size": size})
        seen.add(phase_relative)
    return sorted(records, key=lambda item: str(item["relative_path"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--issued-by", required=True)
    parser.add_argument("--environment-config", action="append", default=[])
    for key in ROLE_KEYS:
        parser.add_argument("--" + key.replace("_", "-"), required=True)
    args = parser.parse_args()

    run_input = Path(args.run_dir).expanduser().absolute()
    if run_input.is_symlink():
        parser.error("Migration run must not be a symbolic link")
    run_dir = run_input.resolve()
    if not run_dir.is_dir():
        parser.error(f"Migration run does not exist: {run_dir}")

    try:
        scope_path = safe_run_file(run_dir, "controller/scope.json", "controller scope")
        scope = load_json(scope_path)
        scope_sha256 = sha256_file(scope_path)
        ownership = scope.get("ownership") if isinstance(scope.get("ownership"), dict) else {}
        if args.issued_by != ownership.get("migration_controller_id"):
            raise ValueError("--issued-by must equal the frozen migration controller")
        gate_path, gate = require_current_gate(run_dir, scope_sha256, 5)
        (
            registry_path, registry_fields, registry_rows,
            ledger_path, ledger_fields, ledger_rows,
        ) = prepare_controller_records(run_dir, 6)
        phase5_registry, phase5_order_path, phase5_order = active_work_order(
            run_dir, registry_rows, 5
        )
        stage6_ownership = {key: str(getattr(args, key)).strip() for key in ROLE_KEYS}
        prior_actor_ids = all_frozen_actor_ids(scope, run_dir, registry_rows, 6)
        validate_roles(stage6_ownership, prior_actor_ids, "Phase 6")

        phase5_report_path = safe_run_file(
            run_dir, "phase-05-harmony-regression/stage-05-gate-report.json", "Phase 5 report"
        )
        phase5_report = load_json(phase5_report_path)
        release_candidate_id = str(
            gate.get("release_candidate_id") or phase5_report.get("release_candidate_id") or ""
        )
        if not ID_RE.fullmatch(release_candidate_id):
            raise ValueError("Gate 5 lacks a safe Release-Candidate-ID")
        if (
            phase5_report.get("release_candidate_id") != release_candidate_id
            or phase5_report.get("verdict") != "PASS"
            or phase5_report.get("final_verdict") != "PASS"
        ):
            raise ValueError("Phase 5 report identity or verdict differs from controller Gate 5")
        candidate_artifacts = collect_candidate_artifacts(
            run_dir, phase5_report, release_candidate_id
        )
        phase5_candidate_registry_path = safe_run_file(
            run_dir, "phase-05-harmony-regression/release-candidate-registry.csv",
            "Phase 5 candidate registry",
        )
        candidate_registry_sha256 = sha256_file(phase5_candidate_registry_path)
        phase5_input_path = safe_run_file(
            run_dir, "phase-05-harmony-regression/stage-05-input-lock.json", "Phase 5 input lock"
        )
        phase5_closure_path = safe_run_file(
            run_dir, "phase-05-harmony-regression/stage-05-closure-manifest.sha256", "Phase 5 closure"
        )
        phase5_closed_path = safe_run_file(
            run_dir, "phase-05-harmony-regression/CLOSED", "Phase 5 CLOSED"
        )
        h5env_ids_value = phase5_order.get("required_h5env_ids")
        if (
            not isinstance(h5env_ids_value, list) or not h5env_ids_value
            or any(not isinstance(item, str) or not ID_RE.fullmatch(item) for item in h5env_ids_value)
            or h5env_ids_value != sorted(set(h5env_ids_value))
        ):
            raise ValueError("Phase 5 work order has no valid required_h5env_ids")
        environments = validate_environment_configs(args.environment_config, set(h5env_ids_value))

        input_sources = {
            "phase5_gate_report": ("phase-05-harmony-regression/stage-05-gate-report.json", phase5_report_path),
            "phase5_work_order": (str(phase5_registry["relative_path"]), phase5_order_path),
            "phase5_input_lock": ("phase-05-harmony-regression/stage-05-input-lock.json", phase5_input_path),
            "phase5_closure_manifest": ("phase-05-harmony-regression/stage-05-closure-manifest.sha256", phase5_closure_path),
            "phase5_closed": ("phase-05-harmony-regression/CLOSED", phase5_closed_path),
            "phase5_release_candidate_registry": (
                "phase-05-harmony-regression/release-candidate-registry.csv",
                phase5_candidate_registry_path,
            ),
        }
        binding_parts = [
            scope_sha256, sha256_file(gate_path), sha256_file(phase5_order_path),
            candidate_registry_sha256,
            *(sha256_file(path) for _, path in input_sources.values()),
            *(str(item["sha256"]) for item in candidate_artifacts),
            *(sha256_file(path) for path, _ in environments),
            *(stage6_ownership[key] for key in ROLE_KEYS),
        ]
        suffix = hashlib.sha256("|".join(binding_parts).encode("utf-8")).hexdigest()[:12].upper()
        work_order_id = f"WO-PHASE-06-{suffix}"
        base_snapshot = f"controller/work-orders/{work_order_id}.inputs"
        snapshots: list[tuple[Path, bytes]] = []
        input_records: dict[str, Any] = {}
        for key, (relative, path) in input_sources.items():
            snapshot_relative = f"{base_snapshot}/{key}{path.suffix or '.bin'}"
            snapshots.append((run_dir / snapshot_relative, path.read_bytes()))
            input_records[key] = file_record(relative, path, snapshot_relative)

        h6envs: list[dict[str, Any]] = []
        for path, environment in environments:
            h6env_id = str(environment["h6env_id"])
            snapshot_relative = f"{base_snapshot}/environments/{h6env_id}.json"
            snapshots.append((run_dir / snapshot_relative, path.read_bytes()))
            h6envs.append({
                "h6env_id": h6env_id,
                "base_h5env_id": str(environment["base_h5env_id"]),
                "relative_path": str(path),
                "snapshot_relative_path": snapshot_relative,
                "sha256": sha256_file(path),
                "required": True,
            })

        issued_at = utc_now()
        work_order = {
            "schema_version": "1.0",
            "work_order_id": work_order_id,
            "run_id": scope.get("run_id"),
            "phase": 6,
            "status": "ISSUED",
            "issued_at": issued_at,
            "issued_by": args.issued_by,
            "required_skill": "harmonyos-delivery-acceptance",
            "ownership": stage6_ownership,
            "forbidden_prior_actor_ids": sorted(prior_actor_ids),
            "inputs": input_records,
            "candidate": {
                "release_candidate_id": release_candidate_id,
                "artifacts": candidate_artifacts,
                "bundle_id": phase5_report.get("bundle_id"),
                "version_name": phase5_report.get("version_name"),
                "version_code": phase5_report.get("version_code"),
                "target_api": phase5_report.get("target_api"),
                "device_types": phase5_report.get("device_types"),
                "build_mode": phase5_report.get("build_mode"),
                "signing_identity": phase5_report.get("signing_identity"),
                "source_snapshot_sha256": phase5_report.get("source_snapshot_sha256"),
                "candidate_registry_relative_path": "phase-05-harmony-regression/release-candidate-registry.csv",
                "candidate_registry_sha256": candidate_registry_sha256,
            },
            "required_h6env_ids": [str(item[1]["h6env_id"]) for item in environments],
            "h6envs": h6envs,
            "permissions": {
                "rebuild": False,
                "resign": False,
                "upload": False,
                "send": False,
                "distribute": False,
                "store": False,
                "remote_signing": False,
                "publish": False,
            },
            "required_return": [
                "stage-06-input-lock.json", "phase-manifest.json",
                "candidate-custody-registry.csv", "delivery-smoke-index.csv",
                "material-snapshot-registry.csv", "rework-tickets.csv", "delivery-manifest.json",
                "inputs/", "environments/", "candidate-custody/", "smoke-evidence/", "materials/",
                "stage-06-gate-report.json", "stage-06-closure-manifest.sha256", "CLOSED",
            ],
        }
        work_order_path, digest = persist_work_order(
            run_dir=run_dir, phase=6, scope_sha256=scope_sha256,
            issued_by=args.issued_by, owner=stage6_ownership["delivery_lead_id"],
            work_order=work_order, snapshots=snapshots,
            registry_path=registry_path, registry_fields=registry_fields, registry_rows=registry_rows,
            ledger_path=ledger_path, ledger_fields=ledger_fields, ledger_rows=ledger_rows,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    print(json.dumps({
        "work_order_id": work_order_id,
        "work_order": str(work_order_path),
        "work_order_sha256": digest,
        "release_candidate_id": release_candidate_id,
        "required_h6env_ids": work_order["required_h6env_ids"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
