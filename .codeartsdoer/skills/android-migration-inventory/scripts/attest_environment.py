#!/usr/bin/env python3
"""Record a one-time inventory-lead attestation for one frozen environment."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from _common import (
    assert_no_symlink,
    atomic_json,
    exclusive_lock,
    load_json,
    sha256_file,
    utc_now,
    validate_id,
    verify_phase_identity,
)


FROZEN_ENVIRONMENT_KEYS = (
    "env_id",
    "is_baseline",
    "account_id",
    "account_role",
    "seed_data_id",
    "seed_reset_ref",
    "network_profile",
    "network_conditions_ref",
    "network_toggle_available",
    "permissions_profile",
    "device_serial",
    "emulator_model",
    "resolution",
    "density_dpi",
    "android_api_level",
    "orientation",
    "locale",
    "theme",
    "font_scale",
    "timezone",
    "application_id",
    "app_version",
    "app_build",
    "build_variant",
    "source_revision",
    "apk_sha256",
)


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_frozen_environment(workspace: Path, env_id: str) -> tuple[dict, str]:
    registry_path = workspace / "environments.json"
    registry = load_json(registry_path)
    if not isinstance(registry, dict) or not isinstance(registry.get("environments"), list):
        raise ValueError("Frozen environment registry is malformed")
    matches = [
        item for item in registry["environments"]
        if isinstance(item, dict) and item.get("env_id") == env_id
    ]
    if len(matches) != 1:
        raise ValueError(f"ENV-ID is not uniquely present in the frozen registry: {env_id}")
    environment = matches[0]
    if environment.get("status") != "FROZEN":
        raise ValueError(f"Environment is not frozen: {env_id}")
    required_values = (
        "account_id",
        "account_role",
        "seed_data_id",
        "seed_reset_ref",
        "network_profile",
        "network_conditions_ref",
        "permissions_profile",
    )
    missing = [key for key in required_values if environment.get(key) in (None, "")]
    if missing:
        raise ValueError(f"Frozen environment lacks readiness values: {', '.join(missing)}")
    if not isinstance(environment.get("network_toggle_available"), bool):
        raise ValueError("Frozen environment network_toggle_available must be boolean")
    return environment, sha256_file(registry_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--env-id", required=True)
    parser.add_argument("--inventory-lead-id", required=True)
    parser.add_argument("--account-ready", action="store_true")
    parser.add_argument("--seed-ready", action="store_true")
    parser.add_argument("--network-ready", action="store_true")
    parser.add_argument("--permissions-ready", action="store_true")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    try:
        env_id = validate_id(args.env_id, "ENV-ID")
    except ValueError as exc:
        parser.error(str(exc))
    checks = {
        "account_ready": args.account_ready,
        "seed_ready": args.seed_ready,
        "network_ready": args.network_ready,
        "permissions_ready": args.permissions_ready,
    }
    if not all(checks.values()):
        parser.error(
            "Attestation requires --account-ready, --seed-ready, --network-ready, "
            "and --permissions-ready"
        )
    notes = args.notes.strip()
    if len(notes) > 4000 or "\x00" in notes:
        parser.error("--notes must be at most 4000 characters and contain no NUL bytes")

    workspace_input = Path(args.workspace).expanduser().absolute()
    if workspace_input.is_symlink():
        parser.error("Workspace must not be a symbolic link")
    workspace = workspace_input.resolve()
    if not workspace.is_dir() or workspace.name != "phase-02-android-inventory":
        parser.error("Workspace must be the canonical phase-02-android-inventory directory")

    protected_paths = (
        workspace / "phase-manifest.json",
        workspace / "environments.json",
        workspace / "controller-scope.snapshot.json",
        workspace.parent / "controller" / "scope.json",
        workspace.parent / "run-manifest.json",
    )
    if any(path.is_symlink() for path in protected_paths):
        parser.error("Phase identity files must not be symbolic links")
    closed_marker = workspace / "CLOSED"
    if closed_marker.is_symlink():
        parser.error("CLOSED marker must not be a symbolic link")
    if closed_marker.exists():
        parser.error("Phase 2 is CLOSED; environment attestations are read-only")

    try:
        manifest = load_json(workspace / "phase-manifest.json")
        if not isinstance(manifest, dict):
            raise ValueError("Phase manifest must be a JSON object")
        if manifest.get("status") == "CLOSED":
            raise ValueError("Phase 2 is CLOSED; environment attestations are read-only")
        scope = verify_phase_identity(workspace, manifest)
        ownership = scope.get("ownership") if isinstance(scope.get("ownership"), dict) else {}
        expected_lead = ownership.get("inventory_lead_id")
        if not expected_lead or args.inventory_lead_id != expected_lead:
            raise ValueError("Only the controller-frozen inventory_lead_id may attest an environment")
        environment, registry_digest = load_frozen_environment(workspace, env_id)
    except ValueError as exc:
        parser.error(str(exc))

    attestations_dir = workspace / "environment-attestations"
    target = attestations_dir / f"{env_id}.json"
    lock_path = workspace / ".locks" / f"environment-attestation-{env_id}.lock"
    try:
        assert_no_symlink(lock_path, workspace)
        with exclusive_lock(lock_path):
            current_manifest = load_json(workspace / "phase-manifest.json")
            if not isinstance(current_manifest, dict) or current_manifest.get("status") == "CLOSED":
                raise ValueError("Phase 2 is CLOSED; environment attestations are read-only")
            if closed_marker.exists() or closed_marker.is_symlink():
                raise ValueError("Phase 2 is CLOSED; environment attestations are read-only")
            verify_phase_identity(workspace, current_manifest)
            if attestations_dir.is_symlink() or target.is_symlink():
                raise ValueError("Environment attestation paths must not be symbolic links")
            attestations_dir.mkdir(mode=0o700, exist_ok=True)
            assert_no_symlink(attestations_dir, workspace)
            assert_no_symlink(target, workspace)
            if target.exists() or target.is_symlink():
                raise ValueError(f"Environment attestation already exists; overwrite is prohibited: {target}")

            frozen_values = {key: environment.get(key) for key in FROZEN_ENVIRONMENT_KEYS}
            record = {
                "schema_version": 1,
                "attestation_type": "phase-02-environment-readiness",
                "status": "ATTESTED",
                "run_id": current_manifest.get("run_id"),
                "env_id": env_id,
                "inventory_lead_id": args.inventory_lead_id,
                "attested_at": utc_now(),
                **checks,
                "notes": notes,
                "scope_sha256": current_manifest.get("scope_sha256"),
                "environment_registry_sha256": registry_digest,
                "environment_sha256": canonical_digest(environment),
                "frozen_environment": frozen_values,
            }
            atomic_json(target, record)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))

    print(json.dumps({"env_id": env_id, "attestation": str(target)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
