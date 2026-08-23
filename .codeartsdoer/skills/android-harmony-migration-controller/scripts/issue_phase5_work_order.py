#!/usr/bin/env python3
"""Issue an immutable Phase 5 whole-application regression work order from Gate 4."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from _phase56_common import (
    ID_RE, SHA256_RE, active_work_order, all_frozen_actor_ids, contains_secret_key,
    external_json_file, file_record, load_json, persist_work_order, prepare_controller_records,
    project_snapshot, require_current_gate, safe_run_file, sha256_file, utc_now, validate_roles,
)


ROLE_KEYS = (
    "regression_lead_id",
    "candidate_build_agent_id",
    "journey_executor_id",
    "quality_agent_id",
    "system_acceptance_agent_id",
)
PROFILE_KEYS = (
    "profile_id", "bundle_id", "version_name", "version_code", "target_api",
    "device_types", "build_mode", "signing_mode", "signing_identity",
    "primary_artifact_path", "candidate_artifact_paths",
)
ALLOWED_SIGNING_MODES = {"LOCAL_TEST", "LOCAL_PRODUCTION", "REMOTE"}


def require_text(value: Any, label: str) -> str:
    if not isinstance(value, (str, int)) or not str(value).strip():
        raise ValueError(f"{label} must be nonempty")
    return str(value).strip()


def validate_release_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if contains_secret_key(profile):
        raise ValueError("Release profile must not contain passwords, tokens, private keys, or secrets")
    missing = [key for key in PROFILE_KEYS if key not in profile]
    if missing:
        raise ValueError(f"Release profile lacks fields: {missing}")
    profile_id = require_text(profile.get("profile_id"), "release profile ID")
    if not ID_RE.fullmatch(profile_id):
        raise ValueError(f"Unsafe release profile ID: {profile_id!r}")
    signing_mode = require_text(profile.get("signing_mode"), "signing_mode").upper()
    if signing_mode not in ALLOWED_SIGNING_MODES:
        raise ValueError(f"Unsupported signing_mode: {signing_mode}")
    device_types = profile.get("device_types")
    if (
        not isinstance(device_types, list) or not device_types
        or any(not isinstance(item, str) or not item.strip() for item in device_types)
        or device_types != sorted(set(device_types))
    ):
        raise ValueError("release_profile.device_types must be a sorted unique nonempty string list")
    candidates = profile.get("candidate_artifact_paths")
    if (
        not isinstance(candidates, list) or not candidates
        or any(not isinstance(item, str) or not item.strip() for item in candidates)
        or candidates != sorted(set(candidates))
    ):
        raise ValueError("candidate_artifact_paths must be a sorted unique nonempty string list")
    primary = require_text(profile.get("primary_artifact_path"), "primary_artifact_path")
    if primary not in candidates:
        raise ValueError("primary_artifact_path must appear in candidate_artifact_paths")
    public = {
        key: profile[key] for key in PROFILE_KEYS
    }
    public["profile_id"] = profile_id
    public["signing_mode"] = signing_mode
    for key in (
        "bundle_id", "version_name", "version_code", "target_api", "build_mode",
        "signing_identity", "primary_artifact_path",
    ):
        public[key] = require_text(public[key], f"release_profile.{key}")
    return public


def validate_environment_configs(
    values: list[str], allowed_h4env_ids: set[str]
) -> list[tuple[Path, dict[str, Any]]]:
    if not values:
        raise ValueError("At least one --environment-config is required")
    records: list[tuple[Path, dict[str, Any]]] = []
    seen: set[str] = set()
    for value in values:
        path, environment = external_json_file(value, "Phase 5 environment config")
        if contains_secret_key(environment):
            raise ValueError(f"H5ENV must not contain secrets: {path}")
        h5env_id = str(environment.get("h5env_id", ""))
        base_h4env_id = str(environment.get("base_h4env_id", ""))
        if not ID_RE.fullmatch(h5env_id) or h5env_id in seen:
            raise ValueError(f"Unsafe or duplicate H5ENV-ID: {h5env_id!r}")
        if base_h4env_id not in allowed_h4env_ids:
            raise ValueError(f"{h5env_id} inherits unknown final H4ENV: {base_h4env_id!r}")
        if environment.get("required") is not True:
            raise ValueError(f"{h5env_id} must declare required=true")
        seen.add(h5env_id)
        records.append((path, environment))
    return sorted(records, key=lambda item: str(item[1]["h5env_id"]))


def collect_phase4_builds(phase_dir: Path, report: dict[str, Any]) -> list[dict[str, Any]]:
    build_ids = report.get("build_ids")
    if (
        not isinstance(build_ids, list) or not build_ids
        or any(not isinstance(item, str) or not ID_RE.fullmatch(item) for item in build_ids)
        or build_ids != sorted(set(build_ids))
    ):
        raise ValueError("Phase 4 report lacks a sorted unique final HBUILD set")
    records: list[dict[str, Any]] = []
    seen_envs: set[str] = set()
    for build_id in build_ids:
        directory = phase_dir / "builds" / build_id
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"Missing or unsafe final HBUILD: {build_id}")
        metadata_path = safe_run_file(phase_dir, f"builds/{build_id}/metadata.json", build_id)
        manifest_path = safe_run_file(phase_dir, f"builds/{build_id}/artifact-manifest.json", build_id)
        metadata = load_json(metadata_path)
        manifest = load_json(manifest_path)
        h4env_id = str(metadata.get("h4env_id", ""))
        if not ID_RE.fullmatch(h4env_id) or h4env_id in seen_envs:
            raise ValueError(f"Final HBUILD has an unsafe or duplicate H4ENV-ID: {build_id}")
        if metadata.get("hbuild_id") != build_id or metadata.get("status") != "PASS":
            raise ValueError(f"Final HBUILD identity/status differs: {build_id}")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            raise ValueError(f"Final HBUILD has no artifacts: {build_id}")
        frozen_artifacts: list[dict[str, Any]] = []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise ValueError(f"Invalid HBUILD artifact record: {build_id}")
            local = str(artifact.get("sealed_relative_path", ""))
            path = safe_run_file(phase_dir, f"builds/{build_id}/{local}", f"{build_id} artifact")
            digest = str(artifact.get("sha256", ""))
            size = artifact.get("size")
            if not SHA256_RE.fullmatch(digest) or digest != sha256_file(path) or size != path.stat().st_size:
                raise ValueError(f"Final HBUILD artifact bytes differ: {build_id}/{local}")
            frozen_artifacts.append({
                "relative_path": f"phase-04-harmony-implementation/builds/{build_id}/{local}",
                "sha256": digest,
                "size": size,
            })
        records.append({
            "h4env_id": h4env_id,
            "hbuild_id": build_id,
            "source_snapshot_sha256": metadata.get("source_snapshot_sha256"),
            "build_record_relative_path": f"phase-04-harmony-implementation/builds/{build_id}/metadata.json",
            "build_record_sha256": sha256_file(metadata_path),
            "artifacts": frozen_artifacts,
        })
        seen_envs.add(h4env_id)
    return sorted(records, key=lambda item: str(item["h4env_id"]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--issued-by", required=True)
    parser.add_argument("--release-profile", required=True)
    parser.add_argument("--signing-authorization")
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
        gate_path, _gate = require_current_gate(run_dir, scope_sha256, 4)
        (
            registry_path, registry_fields, registry_rows,
            ledger_path, ledger_fields, ledger_rows,
        ) = prepare_controller_records(run_dir, 5)
        phase4_registry, phase4_order_path, phase4_order = active_work_order(
            run_dir, registry_rows, 4
        )
        stage5_ownership = {key: str(getattr(args, key)).strip() for key in ROLE_KEYS}
        prior_actor_ids = all_frozen_actor_ids(scope, run_dir, registry_rows, 5)
        validate_roles(stage5_ownership, prior_actor_ids, "Phase 5")
        profile_path, raw_profile = external_json_file(args.release_profile, "release profile")
        profile_public = validate_release_profile(raw_profile)
        signing_mode = str(profile_public["signing_mode"])
        authorization_path: Path | None = None
        authorization: dict[str, Any] | None = None
        if args.signing_authorization:
            authorization_path, authorization = external_json_file(
                args.signing_authorization, "signing authorization"
            )
            if contains_secret_key(authorization):
                raise ValueError("Signing authorization must not contain secrets")
            if authorization.get("approved") is not True:
                raise ValueError("Signing authorization must declare approved=true")
            if str(authorization.get("signing_mode", "")).upper() != signing_mode:
                raise ValueError("Signing authorization mode differs from the release profile")
        if signing_mode in {"LOCAL_PRODUCTION", "REMOTE"} and not authorization:
            raise ValueError(f"{signing_mode} requires --signing-authorization")
        if signing_mode == "LOCAL_TEST" and authorization:
            raise ValueError("LOCAL_TEST must not carry a signing authorization")

        phase4_dir = run_dir / "phase-04-harmony-implementation"
        phase4_input_path = safe_run_file(
            run_dir, "phase-04-harmony-implementation/stage-04-input-lock.json", "Phase 4 input lock"
        )
        phase4_manifest_path = safe_run_file(
            run_dir, "phase-04-harmony-implementation/phase-manifest.json", "Phase 4 manifest"
        )
        phase4_report_path = safe_run_file(
            run_dir, "phase-04-harmony-implementation/stage-04-gate-report.json", "Phase 4 report"
        )
        phase4_closure_path = safe_run_file(
            run_dir, "phase-04-harmony-implementation/stage-04-closure-manifest.sha256", "Phase 4 closure"
        )
        phase4_closed_path = safe_run_file(
            run_dir, "phase-04-harmony-implementation/CLOSED", "Phase 4 CLOSED"
        )
        phase4_report = load_json(phase4_report_path)
        source_snapshot_sha256, source_entries = project_snapshot(phase4_dir / "harmony-project")
        if phase4_report.get("source_snapshot_sha256") != source_snapshot_sha256:
            raise ValueError("Current Phase 4 project differs from its final report snapshot")
        final_builds = collect_phase4_builds(phase4_dir, phase4_report)
        if any(item.get("source_snapshot_sha256") != source_snapshot_sha256 for item in final_builds):
            raise ValueError("A final Phase 4 build uses another source snapshot")
        h4env_ids = {str(item["h4env_id"]) for item in final_builds}
        environments = validate_environment_configs(args.environment_config, h4env_ids)

        input_sources = {
            "scope": ("controller/scope.json", scope_path),
            "gate4_report": ("controller/gate-report.json", gate_path),
            "phase4_work_order": (str(phase4_registry["relative_path"]), phase4_order_path),
            "phase4_input_lock": ("phase-04-harmony-implementation/stage-04-input-lock.json", phase4_input_path),
            "phase4_manifest": ("phase-04-harmony-implementation/phase-manifest.json", phase4_manifest_path),
            "phase4_report": ("phase-04-harmony-implementation/stage-04-gate-report.json", phase4_report_path),
            "phase4_closure_manifest": ("phase-04-harmony-implementation/stage-04-closure-manifest.sha256", phase4_closure_path),
            "phase4_closed": ("phase-04-harmony-implementation/CLOSED", phase4_closed_path),
        }
        binding_parts = [
            scope_sha256, sha256_file(gate_path), sha256_file(phase4_order_path),
            sha256_file(profile_path),
            *(sha256_file(path) for _, path in input_sources.values()),
            *(sha256_file(path) for path, _ in environments),
            *(stage5_ownership[key] for key in ROLE_KEYS),
        ]
        if authorization_path:
            binding_parts.append(sha256_file(authorization_path))
        suffix = hashlib.sha256("|".join(binding_parts).encode("utf-8")).hexdigest()[:12].upper()
        work_order_id = f"WO-PHASE-05-{suffix}"
        base_snapshot = f"controller/work-orders/{work_order_id}.inputs"
        snapshots: list[tuple[Path, bytes]] = []
        input_records: dict[str, Any] = {}
        for key, (relative, path) in input_sources.items():
            snapshot_relative = f"{base_snapshot}/{key}{path.suffix or '.bin'}"
            snapshots.append((run_dir / snapshot_relative, path.read_bytes()))
            input_records[key] = file_record(relative, path, snapshot_relative)
        input_records["phase4_project"] = {
            "relative_path": "phase-04-harmony-implementation/harmony-project",
            "snapshot_sha256": source_snapshot_sha256,
            "entry_count": len(source_entries),
        }
        input_records["phase4_final_builds"] = final_builds

        profile_snapshot_relative = f"{base_snapshot}/release-profile.json"
        snapshots.append((run_dir / profile_snapshot_relative, profile_path.read_bytes()))
        release_profile = {
            **profile_public,
            "relative_path": str(profile_path),
            "snapshot_relative_path": profile_snapshot_relative,
            "sha256": sha256_file(profile_path),
        }
        if authorization_path:
            authorization_snapshot_relative = f"{base_snapshot}/signing-authorization.json"
            snapshots.append((run_dir / authorization_snapshot_relative, authorization_path.read_bytes()))
            signing_authorization = {
                "required": True,
                "present": True,
                "relative_path": str(authorization_path),
                "snapshot_relative_path": authorization_snapshot_relative,
                "sha256": sha256_file(authorization_path),
            }
        else:
            signing_authorization = {
                "required": False, "present": False, "relative_path": "",
                "snapshot_relative_path": "", "sha256": "",
            }

        h5envs: list[dict[str, Any]] = []
        for path, environment in environments:
            h5env_id = str(environment["h5env_id"])
            snapshot_relative = f"{base_snapshot}/environments/{h5env_id}.json"
            snapshots.append((run_dir / snapshot_relative, path.read_bytes()))
            h5envs.append({
                "h5env_id": h5env_id,
                "base_h4env_id": str(environment["base_h4env_id"]),
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
            "phase": 5,
            "status": "ISSUED",
            "issued_at": issued_at,
            "issued_by": args.issued_by,
            "required_skill": "harmonyos-system-regression",
            "ownership": stage5_ownership,
            "forbidden_prior_actor_ids": sorted(prior_actor_ids),
            "inputs": input_records,
            "release_profile": release_profile,
            "signing_authorization": signing_authorization,
            "required_h5env_ids": [str(item[1]["h5env_id"]) for item in environments],
            "h5envs": h5envs,
            "permissions": {
                "source_modification_allowed": False,
                "new_feature_allowed": False,
                "mp4_allowed": False,
                "external_publish_allowed": False,
            },
            "required_return": [
                "stage-05-input-lock.json", "phase-manifest.json", "release-candidate-registry.csv",
                "flow-edge-registry.csv", "lifecycle-invariants.csv", "no-cross-flow.csv",
                "scenario-registry.csv", "scenario-acceptance.csv", "evidence-index.csv",
                "rework-tickets.csv", "inputs/", "environments/", "harmony-project/",
                "environments/h5env-registry.csv",
                "release-candidates/", "scenarios/", "evidence/", "reviews/",
                "stage-05-gate-report.json", "stage-05-closure-manifest.sha256", "CLOSED",
            ],
        }
        work_order_path, digest = persist_work_order(
            run_dir=run_dir, phase=5, scope_sha256=scope_sha256,
            issued_by=args.issued_by, owner=stage5_ownership["regression_lead_id"],
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
        "required_h5env_ids": work_order["required_h5env_ids"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
