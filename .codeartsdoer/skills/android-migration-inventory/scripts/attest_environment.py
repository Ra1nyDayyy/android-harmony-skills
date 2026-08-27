#!/usr/bin/env python3
"""Record a one-time inventory-lead attestation for one frozen environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
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


# 双 Android 模拟器架构：一个逻辑 ENV-ID 允许 A/B 两个采集槽（capture slots）。
# 两个槽必须通过启动前校准（同一逻辑环境），不扩大业务覆盖分母，
# 也不为 A/B 各建一个业务 ENV-ID。
CALIBRATION_KEYS = ("resolution", "density", "android_api", "locale", "font_scale")


def adb_output(serial: str, *args: str) -> str:
    try:
        result = subprocess.run(
            ["adb", "-s", serial, *args],
            capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
        )
        return (result.stdout or "") + (result.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        return f"__ADB_ERROR__{exc}"


def probe_device_profile(serial: str) -> dict:
    size_out = adb_output(serial, "shell", "wm", "size")
    density_out = adb_output(serial, "shell", "wm", "density")
    api_out = adb_output(serial, "shell", "getprop", "ro.build.version.sdk")
    locale_out = adb_output(serial, "shell", "getprop", "persist.sys.locale")
    if not locale_out.strip():
        # API 24+ no longer persists persist.sys.locale; the live locale lives in settings.
        locale_out = adb_output(serial, "shell", "settings", "get", "system", "system_locales")
    font_out = adb_output(serial, "shell", "settings", "get", "system", "font_scale")
    size_match = re.search(r"(\d+x\d+)", size_out)
    density_match = re.search(r"(\d+)", density_out)
    api_match = re.search(r"(\d+)", api_out)
    return {
        "device_serial": serial,
        "resolution": size_match.group(1) if size_match else "",
        "density": density_match.group(1) if density_match else "",
        "android_api": api_match.group(1) if api_match else "",
        "locale": locale_out.strip() or "",
        "font_scale": font_out.strip() or "",
    }


def installed_apk_digest(serial: str, package: str) -> str:
    pm_out = adb_output(serial, "shell", "pm", "path", package)
    match = re.search(r"package:(\S+)", pm_out)
    if not match:
        return ""
    apk_path = match.group(1)
    cat = subprocess.run(
        ["adb", "-s", serial, "exec-out", "cat", apk_path],
        capture_output=True, timeout=120,
    )
    return hashlib.sha256(cat.stdout).hexdigest()


def calibrate_capture_slots(slot_a_serial: str, slot_b_serial: str) -> tuple[dict, list[str]]:
    """轻量双设备校准：两个槽必须呈现同一逻辑环境，否则停止两个运行 worker。"""
    profile_a = probe_device_profile(slot_a_serial)
    profile_b = probe_device_profile(slot_b_serial)
    mismatches: list[str] = []
    for key in CALIBRATION_KEYS:
        value_a = profile_a.get(key, "")
        value_b = profile_b.get(key, "")
        if not value_a or not value_b:
            mismatches.append(f"slot probe failed for '{key}' (A={value_a!r}, B={value_b!r})")
        elif value_a != value_b:
            mismatches.append(f"'{key}' mismatch: slot A={value_a!r} vs slot B={value_b!r}")
    return {"A": profile_a, "B": profile_b}, mismatches


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
    parser.add_argument(
        "--slot-a-serial", default=None,
        help="capture slot A device serial (e.g. emulator-5554); required with --slot-b-serial",
    )
    parser.add_argument(
        "--slot-b-serial", default=None,
        help="capture slot B device serial (e.g. emulator-5556); required with --slot-a-serial",
    )
    parser.add_argument(
        "--package", default=None,
        help="optional application id: verify both slots run the same installed APK digest",
    )
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

    capture_slots: dict | None = None
    if args.slot_a_serial or args.slot_b_serial:
        if not (args.slot_a_serial and args.slot_b_serial):
            parser.error(
                "Dual-emulator attestation requires BOTH --slot-a-serial and --slot-b-serial"
            )
        if args.slot_a_serial == args.slot_b_serial:
            parser.error("--slot-a-serial and --slot-b-serial must be distinct ADB serials")
        capture_slots, mismatches = calibrate_capture_slots(
            args.slot_a_serial, args.slot_b_serial
        )
        if args.package:
            digest_a = installed_apk_digest(args.slot_a_serial, args.package)
            digest_b = installed_apk_digest(args.slot_b_serial, args.package)
            if not digest_a or not digest_b:
                mismatches.append(f"installed APK not found on both slots (A={digest_a!r}, B={digest_b!r})")
            elif digest_a != digest_b:
                mismatches.append(
                    f"installed APK digest mismatch: A={digest_a[:16]}… vs B={digest_b[:16]}…"
                )
            else:
                capture_slots["apk_sha256_installed"] = digest_a
        if mismatches:
            print("CAPTURE-SLOT CALIBRATION FAILED (do not start the two runtime workers):")
            for item in mismatches:
                print("  -", item)
            parser.error(
                "Slots A and B are not the same logical environment; fix both emulators and retry"
            )

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
                "capture_slots": capture_slots,
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
