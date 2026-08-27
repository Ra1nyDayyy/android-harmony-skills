#!/usr/bin/env python3
"""Validate Phase 1 through Phase 4 gates for a migration controller run."""

from __future__ import annotations

import argparse
import binascii
import csv
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


FEATURE_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "harmonyos-feature-implementation" / "scripts"
)
if str(FEATURE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(FEATURE_SCRIPTS))
from uitest_snapshot import validate_uitest_evidence  # noqa: E402
from _stage4_audit import (  # noqa: E402
    LITE_EVIDENCE_SEQUENCE,
    LITE_COMPONENT_OVERLAP_MIN,
    validate_uitest_evidence_lite,
)
from stage4_work_orders import (  # noqa: E402
    _page_contracts,
    _registered_orders,
    validate_order_coverage,
)


ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{2,79}$")
ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,95}$")
PLACEHOLDER_RE = re.compile(r"^__.+__$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UNRESOLVED_WORDS = {"PENDING_CONFIRMATION", "UNKNOWN", "UNRESOLVED", "TBD", "TODO"}
CLOSURE_EXACT_EXCLUDES = {"closure-report.json", "closure-manifest.sha256", "CLOSED"}
CLOSURE_DIR_EXCLUDES = {".locks", ".staging"}
STAGE3_CLOSURE_EXACT_EXCLUDES = {
    "stage-03-gate-report.json", "stage-03-closure-manifest.sha256", "CLOSED",
}
STAGE3_ROLE_KEYS = (
    "architecture_lead_id", "toolchain_agent_id", "navigation_agent_id",
    "public_ui_agent_id", "capability_contract_agent_id", "architecture_acceptance_agent_id",
)
STAGE3_SNAPSHOT_REGISTRIES = {
    "stage-03-input-lock.json", "module-registry.csv", "dependency-policy.json",
    "architecture-map.csv", "route-registry.csv", "surface-registry.csv",
    "public-ui-registry.csv", "capability-contracts.csv", "asset-registry.csv", "migration-status.csv",
    "architecture-decisions.csv", "phase-manifest.json",
}
STAGE3_SNAPSHOT_EXCLUDED_PARTS = {
    ".git", ".hg", ".svn", ".idea", ".hvigor", "oh_modules", "node_modules",
    "build", "out", "dist", "coverage", "__pycache__",
}
STAGE3_REWORK_ROUTES = {
    "ARCHITECTURE": ("architecture-lead", "architecture_lead_id"),
    "PLACEMENT": ("architecture-lead", "architecture_lead_id"),
    "DEPENDENCY": ("architecture-lead", "architecture_lead_id"),
    "INPUT": ("architecture-lead", "architecture_lead_id"),
    "TOOLCHAIN": ("toolchain-agent", "toolchain_agent_id"),
    "BUILD": ("toolchain-agent", "toolchain_agent_id"),
    "DEVICE": ("toolchain-agent", "toolchain_agent_id"),
    "BUNDLE": ("toolchain-agent", "toolchain_agent_id"),
    "SIGNING": ("toolchain-agent", "toolchain_agent_id"),
    "INSTALL": ("toolchain-agent", "toolchain_agent_id"),
    "LAUNCH": ("toolchain-agent", "toolchain_agent_id"),
    "ARTIFACT": ("toolchain-agent", "toolchain_agent_id"),
    "SCREENSHOT": ("toolchain-agent", "toolchain_agent_id"),
    "NAVIGATION": ("navigation-agent", "navigation_agent_id"),
    "ROUTE": ("navigation-agent", "navigation_agent_id"),
    "SURFACE": ("navigation-agent", "navigation_agent_id"),
    "MAPPING": ("navigation-agent", "navigation_agent_id"),
    "SMOKE": ("navigation-agent", "navigation_agent_id"),
    "PUBLIC_UI": ("public-ui-agent", "public_ui_agent_id"),
    "RESPONSIVE": ("public-ui-agent", "public_ui_agent_id"),
    "THEME": ("public-ui-agent", "public_ui_agent_id"),
    "CAPABILITY": ("capability-contract-agent", "capability_contract_agent_id"),
    "CONTRACT": ("capability-contract-agent", "capability_contract_agent_id"),
}
STAGE4_CLOSURE_EXACT_EXCLUDES = {
    "stage-04-gate-report.json", "stage-04-closure-manifest.sha256", "CLOSED",
}
STAGE4_ROLE_KEYS = (
    "implementation_lead_id", "visual_asset_agent_id",
    "verification_executor_id", "parity_acceptance_agent_id",
)
STAGE4_PROJECT_EXCLUDED_PARTS = {
    ".git", ".idea", ".hvigor", "build", "dist", "coverage", "node_modules",
    "oh_modules", "__pycache__", ".pytest_cache",
}
STAGE4_INPUT_RELATIVES = {
    "phase2_closure_sha256": "phase-02-android-inventory/closure-report.json",
    "phase2_closure_manifest_sha256": "phase-02-android-inventory/closure-manifest.sha256",
    "phase2_closed_sha256": "phase-02-android-inventory/CLOSED",
    "phase2_inventory_sha256": "phase-02-android-inventory/inventory.csv",
    "phase2_evidence_index_sha256": "phase-02-android-inventory/evidence-index.csv",
    "phase2_asset_inventory_sha256": "phase-02-android-inventory/asset-inventory.csv",
    "phase2_asset_manifest_sha256": "phase-02-android-inventory/asset-package/manifest.sha256",
    "phase2_asset_committed_sha256": "phase-02-android-inventory/asset-package/COMMITTED",
    "phase2_static_pages_sha256": "phase-02-android-inventory/static-analysis/pages.json",
    "phase2_static_components_sha256": "phase-02-android-inventory/static-analysis/components.json",
    "phase2_static_events_sha256": "phase-02-android-inventory/static-analysis/events.json",
    "phase2_static_transitions_sha256": "phase-02-android-inventory/static-analysis/transitions.json",
    "phase2_runtime_observations_sha256": "phase-02-android-inventory/runtime-observations.json",
    "phase2_page_gate_sha256": "phase-02-android-inventory/page-gate-report.json",
    "phase2_advanced_analysis_sha256": "phase-02-android-inventory/static-analysis/advanced-analysis.json",
    "phase2_advanced_observations_sha256": "phase-02-android-inventory/advanced-observations.json",
    "phase2_advanced_gate_sha256": "phase-02-android-inventory/advanced-gate-report.json",
    "phase2_probe_index_sha256": "phase-02-android-inventory/probe-evidence-index.csv",
    "phase3_input_lock_sha256": "phase-03-harmony-scaffold/stage-03-input-lock.json",
    "phase3_gate_report_sha256": "phase-03-harmony-scaffold/stage-03-gate-report.json",
    "phase3_closure_manifest_sha256": "phase-03-harmony-scaffold/stage-03-closure-manifest.sha256",
    "phase3_closed_sha256": "phase-03-harmony-scaffold/CLOSED",
    "phase3_scaffold_snapshot_sha256": "phase-03-harmony-scaffold/scaffold-snapshot-manifest.json",
    "phase3_architecture_map_sha256": "phase-03-harmony-scaffold/architecture-map.csv",
    "phase3_module_registry_sha256": "phase-03-harmony-scaffold/module-registry.csv",
    "phase3_route_registry_sha256": "phase-03-harmony-scaffold/route-registry.csv",
    "phase3_surface_registry_sha256": "phase-03-harmony-scaffold/surface-registry.csv",
    "phase3_public_ui_registry_sha256": "phase-03-harmony-scaffold/public-ui-registry.csv",
    "phase3_capability_contracts_sha256": "phase-03-harmony-scaffold/capability-contracts.csv",
    "phase3_asset_registry_sha256": "phase-03-harmony-scaffold/asset-registry.csv",
    "phase3_advanced_obligations_sha256": "phase-03-harmony-scaffold/advanced-obligations.json",
    "phase3_henv_registry_sha256": "phase-03-harmony-scaffold/environments/henv-registry.csv",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unresolved(value: Any) -> bool:
    if value is None or value == "":
        return True
    if isinstance(value, str):
        stripped = value.strip()
        return bool(PLACEHOLDER_RE.match(stripped)) or stripped.upper() in UNRESOLVED_WORDS
    if isinstance(value, list):
        return not value or any(unresolved(item) for item in value)
    if isinstance(value, dict):
        return any(unresolved(item) for item in value.values())
    return False


def need(mapping: dict[str, Any], key: str, label: str, errors: list[str]) -> Any:
    value = mapping.get(key)
    if unresolved(value):
        errors.append(f"Missing or unresolved {label}")
    return value


def run_checked(argv: list[str], label: str, errors: list[str]) -> str:
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        errors.append(f"{label} could not run: {exc}")
        return ""
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        errors.append(f"{label} failed: {detail[:500]}")
        return ""
    return completed.stdout.strip()


def validate_git_baseline(project_root: Path, revision: str, errors: list[str]) -> None:
    actual = run_checked(["git", "-C", str(project_root), "rev-parse", "HEAD"], "git revision check", errors)
    if actual and revision != actual:
        errors.append(f"android.source_revision must equal the exact Git HEAD: {actual}")
    dirty = run_checked(
        ["git", "-C", str(project_root), "status", "--porcelain", "--untracked-files=all"],
        "git worktree check",
        errors,
    )
    if dirty:
        errors.append("Android project has uncommitted or untracked files; freeze a clean source revision")


def validate_apk(apk_path: Path, declared_hash: str, errors: list[str]) -> str | None:
    if not apk_path.is_file():
        errors.append(f"Installable APK does not exist: {apk_path}")
        return None
    if not zipfile.is_zipfile(apk_path):
        errors.append(f"APK is not a valid ZIP/APK container: {apk_path}")
        return None
    try:
        with zipfile.ZipFile(apk_path) as archive:
            names = set(archive.namelist())
            bad_member = archive.testzip()
    except (OSError, zipfile.BadZipFile) as exc:
        errors.append(f"APK cannot be read: {exc}")
        return None
    if bad_member:
        errors.append(f"APK contains a corrupt member: {bad_member}")
    if "AndroidManifest.xml" not in names:
        errors.append("APK has no AndroidManifest.xml")
    if not any(name == "resources.arsc" or re.fullmatch(r"classes\d*\.dex", name) for name in names):
        errors.append("APK has neither resources.arsc nor a classes*.dex payload")
    actual_hash = sha256_file(apk_path)
    if not SHA256_RE.fullmatch(str(declared_hash)):
        errors.append("android.apk_sha256 must be a lowercase 64-character SHA-256")
    elif declared_hash != actual_hash:
        errors.append("android.apk_sha256 does not match the APK file")
    return actual_hash


def resolve_executable(value: str) -> str | None:
    candidate = Path(value).expanduser()
    if candidate.parent != Path(".") or candidate.is_absolute():
        return str(candidate.resolve()) if candidate.is_file() and os.access(candidate, os.X_OK) else None
    return shutil.which(value)


def validate_apk_identity(
    analyzer_value: str, apk_path: Path, android: dict[str, Any], errors: list[str]
) -> None:
    analyzer = resolve_executable(analyzer_value)
    if not analyzer:
        errors.append(f"APK analyzer is unavailable: {analyzer_value}")
        return
    checks = (
        ("application-id", str(android.get("application_id", ""))),
        ("version-name", str(android.get("app_version", ""))),
        ("version-code", str(android.get("app_build", ""))),
    )
    for command, expected in checks:
        command_prefix = [sys.executable, analyzer] if os.name == "nt" and analyzer.lower().endswith(".py") else [analyzer]
        actual = run_checked(
            [*command_prefix, "manifest", command, str(apk_path)],
            f"apkanalyzer manifest {command}",
            errors,
        )
        if actual and actual != expected:
            errors.append(f"APK {command} differs from controller scope: expected {expected!r}, got {actual!r}")


def validate_phase1(
    run_dir: Path, scope: dict[str, Any]
) -> tuple[list[str], list[str], str | None, dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    facts: dict[str, Any] = {}

    try:
        run_manifest = load_json(run_dir / "run-manifest.json")
    except ValueError as exc:
        errors.append(str(exc))
        run_manifest = {}
    if scope.get("run_id") != run_manifest.get("run_id"):
        errors.append("scope.run_id does not match run-manifest.json")
    if scope.get("project_id") != run_manifest.get("project_id"):
        errors.append("scope.project_id does not match run-manifest.json")

    android = scope.get("android") if isinstance(scope.get("android"), dict) else {}
    for key in (
        "project_root", "source_revision", "source_revision_kind", "apk_path", "apk_sha256",
        "application_id", "app_version", "app_build", "build_variant",
    ):
        need(android, key, f"android.{key}", errors)

    project_root = Path(str(android.get("project_root", ""))).expanduser().resolve()
    apk_path = Path(str(android.get("apk_path", ""))).expanduser().resolve()
    manifest_project = str(run_manifest.get("project_root", ""))
    if not unresolved(android.get("project_root")) and not project_root.is_dir():
        errors.append(f"Android project root does not exist: {project_root}")
    if manifest_project and str(project_root) != str(Path(manifest_project).expanduser().resolve()):
        errors.append("android.project_root does not match immutable run-manifest.json")
    settings_files = [project_root / "settings.gradle", project_root / "settings.gradle.kts"]
    gradle_files = list(project_root.rglob("build.gradle")) + list(project_root.rglob("build.gradle.kts")) if project_root.is_dir() else []
    source_manifests = list(project_root.rglob("src/main/AndroidManifest.xml")) if project_root.is_dir() else []
    if not any(path.is_file() for path in settings_files) or not gradle_files or not source_manifests:
        errors.append("Android project must contain settings.gradle(.kts), a build.gradle(.kts), and src/main/AndroidManifest.xml")
    elif not any("android" in path.read_text(encoding="utf-8", errors="replace").lower() for path in gradle_files):
        errors.append("No Android Gradle plugin declaration was found")
    if android.get("source_revision_kind") != "git-commit":
        errors.append("android.source_revision_kind must be git-commit")
    elif project_root.is_dir() and not unresolved(android.get("source_revision")):
        validate_git_baseline(project_root, str(android["source_revision"]), errors)
    if not unresolved(android.get("apk_path")):
        facts["apk_sha256"] = validate_apk(apk_path, str(android.get("apk_sha256", "")), errors)
    facts["source_revision"] = android.get("source_revision")

    target = scope.get("target") if isinstance(scope.get("target"), dict) else {}
    if need(target, "platform", "target.platform", errors) != "HarmonyOS NEXT":
        errors.append("target.platform must be HarmonyOS NEXT")
    need(target, "sdk_or_api_target", "target.sdk_or_api_target", errors)
    need(target, "device_classes", "target.device_classes", errors)

    migration_scope = scope.get("migration_scope") if isinstance(scope.get("migration_scope"), dict) else {}
    included = need(migration_scope, "included_features", "migration_scope.included_features", errors)
    excluded = migration_scope.get("excluded_features")
    if not isinstance(included, list) or any(not isinstance(item, str) or not ID_RE.fullmatch(item) for item in included):
        errors.append("migration_scope.included_features must contain valid Feature-IDs")
        included = []
    if len(set(included)) != len(included):
        errors.append("migration_scope.included_features contains duplicates")
    if "excluded_features" not in migration_scope:
        errors.append("migration_scope.excluded_features must be explicit, even when empty")
        excluded = []
    elif not isinstance(excluded, list) or any(not isinstance(item, str) or not ID_RE.fullmatch(item) for item in excluded):
        errors.append("migration_scope.excluded_features must contain valid Feature-IDs")
        excluded = []
    if set(included) & set(excluded):
        errors.append("A Feature-ID cannot be both included and excluded")
    need(migration_scope, "parity_dimensions", "migration_scope.parity_dimensions", errors)
    facts["included_features"] = included

    ownership = scope.get("ownership") if isinstance(scope.get("ownership"), dict) else {}
    actor_values: list[str] = []
    for key in (
        "migration_controller_id", "inventory_lead_id", "code_map_agent_id", "business_rule_agent_id",
        "data_dependency_agent_id", "evidence_administrator_id", "coverage_checker_id",
    ):
        value = need(ownership, key, f"ownership.{key}", errors)
        if isinstance(value, str) and not unresolved(value):
            if not ACTOR_RE.fullmatch(value):
                errors.append(f"Invalid actor ID: ownership.{key}")
            actor_values.append(value)
    runtime_agents = ownership.get("runtime_state_agent_ids")
    if not isinstance(runtime_agents, list) or not runtime_agents:
        errors.append("ownership.runtime_state_agent_ids must be a non-empty list")
    else:
        for value in runtime_agents:
            if not isinstance(value, str) or not ACTOR_RE.fullmatch(value):
                errors.append("ownership.runtime_state_agent_ids contains an invalid actor ID")
            else:
                actor_values.append(value)
    if len(actor_values) != len(set(actor_values)):
        errors.append("Every frozen controller and Phase 2 actor ID must be distinct")

    pending = scope.get("pending_confirmations")
    if not isinstance(pending, list):
        errors.append("pending_confirmations must be an explicit list")
    elif pending:
        errors.append("Phase 1 cannot PASS with pending confirmations")

    policy = scope.get("tool_policy") if isinstance(scope.get("tool_policy"), dict) else {}
    if policy.get("runtime_ui_tool") != "android-cli":
        errors.append("tool_policy.runtime_ui_tool must be android-cli")
    if policy.get("layout_inspector_allowed") is not False:
        errors.append("tool_policy.layout_inspector_allowed must be false")
    analyzer_value = need(policy, "apk_analyzer_bin", "tool_policy.apk_analyzer_bin", errors)
    if apk_path.is_file() and isinstance(analyzer_value, str) and not unresolved(analyzer_value):
        validate_apk_identity(analyzer_value, apk_path, android, errors)

    environments = scope.get("environments")
    if not isinstance(environments, list) or not environments:
        errors.append("At least one environment is required")
        return errors, warnings, None, facts

    baseline_ids: list[str] = []
    env_ids: set[str] = set()
    required_env = (
        "env_id", "account_id", "account_role", "seed_data_id", "seed_reset_ref",
        "network_profile", "network_conditions_ref", "network_toggle_available", "emulator_model",
        "device_serial", "resolution", "density_dpi", "android_api_level", "orientation", "locale",
        "theme", "font_scale", "timezone", "permissions_profile",
    )
    for index, env in enumerate(environments):
        if not isinstance(env, dict):
            errors.append(f"environments[{index}] must be an object")
            continue
        for key in required_env:
            need(env, key, f"environments[{index}].{key}", errors)
        env_id = str(env.get("env_id", ""))
        if env_id and not ID_RE.fullmatch(env_id):
            errors.append(f"Invalid ENV-ID: {env_id}")
        if env_id in env_ids:
            errors.append(f"Duplicate ENV-ID: {env_id}")
        env_ids.add(env_id)
        if env.get("is_baseline") is True:
            baseline_ids.append(env_id)
        if not isinstance(env.get("network_toggle_available"), bool):
            errors.append(f"{env_id or index}: network_toggle_available must be boolean")
        if not isinstance(env.get("density_dpi"), int):
            errors.append(f"{env_id or index}: density_dpi must be an integer")
        if not isinstance(env.get("android_api_level"), int):
            errors.append(f"{env_id or index}: android_api_level must be an integer")
        if not isinstance(env.get("font_scale"), (int, float)):
            errors.append(f"{env_id or index}: font_scale must be numeric")

    if len(baseline_ids) != 1:
        errors.append(f"Exactly one baseline environment is required; found {len(baseline_ids)}")
    baseline_env_id = baseline_ids[0] if len(baseline_ids) == 1 else None
    for name in (
        "task-ledger.csv", "decision-log.csv", "rework-log.csv", "work-order-registry.csv",
        "evidence-anchor-registry.csv",
        "phase4-attempt-ledger.csv",
    ):
        if not (run_dir / "controller" / name).is_file():
            errors.append(f"Missing controller record: controller/{name}")
    try:
        ledger_rows = read_csv_rows(run_dir / "controller" / "task-ledger.csv")
        phase1_rows = [row for row in ledger_rows if row.get("phase") == "1"]
        phase2_rows = [row for row in ledger_rows if row.get("phase") == "2"]
        if len(phase1_rows) != 1 or len(phase2_rows) != 1:
            errors.append("Task ledger must contain exactly one Phase 1 and one Phase 2 row")
        else:
            expected_controller = ownership.get("migration_controller_id")
            expected_lead = ownership.get("inventory_lead_id")
            if phase1_rows[0].get("owner") not in {expected_controller, "migration-controller"}:
                errors.append("Phase 1 task owner differs from frozen controller")
            if phase2_rows[0].get("owner") not in {expected_lead, "android-inventory-lead"}:
                errors.append("Phase 2 task owner differs from frozen inventory lead")
            if phase1_rows[0].get("owner") != expected_controller or phase2_rows[0].get("owner") != expected_lead:
                warnings.append("Task owners are template defaults and will be normalized by --write")
    except (OSError, ValueError) as exc:
        errors.append(f"Invalid task ledger: {exc}")
    facts["scope_sha256"] = sha256_file(run_dir / "controller" / "scope.json")
    facts["run_manifest_sha256"] = sha256_file(run_dir / "run-manifest.json") if (run_dir / "run-manifest.json").is_file() else None
    return errors, warnings, baseline_env_id, facts


def closure_paths(workspace: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for path in workspace.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in Phase 2 package: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(workspace).as_posix()
        pure = PurePosixPath(relative)
        if relative in CLOSURE_EXACT_EXCLUDES or any(part in CLOSURE_DIR_EXCLUDES for part in pure.parts):
            continue
        if path.name.endswith((".lock", ".tmp")):
            continue
        paths[relative] = path
    return paths


def verify_closure_snapshot(phase_dir: Path, closure: dict[str, Any], errors: list[str]) -> None:
    manifest_path = phase_dir / "closure-manifest.sha256"
    closed_path = phase_dir / "CLOSED"
    report_path = phase_dir / "closure-report.json"
    if not manifest_path.is_file() or not closed_path.is_file():
        errors.append("Phase 2 PASS requires closure-manifest.sha256 and CLOSED")
        return
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest_digest = hashlib.sha256(manifest_text.encode("utf-8")).hexdigest()
    if closure.get("closure_manifest_sha256") != manifest_digest:
        errors.append("Closure manifest digest differs from closure report")
    if closed_path.read_text(encoding="utf-8").strip() != sha256_file(report_path):
        errors.append("CLOSED marker does not bind the current closure report")

    expected: dict[str, str] = {}
    for number, line in enumerate(manifest_text.splitlines(), start=1):
        if "  " not in line:
            errors.append(f"Malformed closure manifest line {number}")
            continue
        digest, relative = line.split("  ", 1)
        pure = PurePosixPath(relative)
        if not SHA256_RE.fullmatch(digest) or pure.is_absolute() or ".." in pure.parts or relative in expected:
            errors.append(f"Unsafe or duplicate closure manifest entry: {relative!r}")
            continue
        expected[relative] = digest
    try:
        actual = closure_paths(phase_dir)
    except ValueError as exc:
        errors.append(str(exc))
        return
    if set(expected) != set(actual):
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        errors.append(f"Closure snapshot file set changed; missing={missing[:5]}, extra={extra[:5]}")
    for relative in sorted(set(expected) & set(actual)):
        if sha256_file(actual[relative]) != expected[relative]:
            errors.append(f"Closure snapshot hash mismatch: {relative}")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except FileNotFoundError:
        return []


def parse_json_id_list(value: str, label: str, errors: list[str]) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        errors.append(f"{label} must be a JSON string array")
        return []
    if (
        not isinstance(parsed, list)
        or not parsed
        or any(not isinstance(item, str) or not ID_RE.fullmatch(item) for item in parsed)
        or len(parsed) != len(set(parsed))
    ):
        errors.append(f"{label} must contain unique safe IDs")
        return []
    return parsed


def validate_phase2_assets(
    phase_dir: Path,
    scope: dict[str, Any],
    inventory_rows: list[dict[str, str]],
    errors: list[str],
) -> dict[str, dict[str, Any]]:
    """Recompute the reviewed Android asset archive and its inventory links."""
    asset_inventory_path = phase_dir / "asset-inventory.csv"
    asset_package = phase_dir / "asset-package"
    manifest_path = asset_package / "manifest.sha256"
    committed_path = asset_package / "COMMITTED"
    asset_rows = read_csv_rows(asset_inventory_path)
    expected_reviewer = scope.get("ownership", {}).get("coverage_checker_id")
    expected_creator = scope.get("ownership", {}).get("code_map_agent_id")
    android_root = Path(str(scope.get("android", {}).get("project_root", ""))).expanduser().resolve()
    assets: dict[str, dict[str, Any]] = {}

    for index, row in enumerate(asset_rows, start=2):
        asset_id = row.get("asset_id", "")
        if not ID_RE.fullmatch(asset_id) or asset_id in assets:
            errors.append(f"asset-inventory.csv:{index}: unsafe or duplicate Asset-ID: {asset_id!r}")
            continue
        feature_ids = parse_json_id_list(row.get("feature_ids", ""), f"{asset_id}.feature_ids", errors)
        page_ids = parse_json_id_list(row.get("page_ids", ""), f"{asset_id}.page_ids", errors)
        state_ids = parse_json_id_list(row.get("state_ids", ""), f"{asset_id}.state_ids", errors)
        source_relative = row.get("source_path", "")
        archive_relative = row.get("archive_path", "")
        source_name = PurePosixPath(source_relative).name
        expected_archive = f"asset-package/files/{asset_id}/{source_name}" if source_name else ""
        archive_path = safe_relative_path(phase_dir, archive_relative, f"{asset_id} archive", errors)
        source_path = safe_relative_path(android_root, source_relative, f"{asset_id} source", errors)
        digest = row.get("sha256", "")
        if archive_relative != expected_archive:
            errors.append(f"{asset_id}: archive_path is not canonical")
        if not SHA256_RE.fullmatch(digest):
            errors.append(f"{asset_id}: invalid asset SHA-256")
        else:
            if archive_path and (not archive_path.is_file() or sha256_file(archive_path) != digest):
                errors.append(f"{asset_id}: archived asset bytes differ from asset-inventory.csv")
            if source_path and (not source_path.is_file() or sha256_file(source_path) != digest):
                errors.append(f"{asset_id}: source asset bytes differ from the frozen archive")
        if (
            not row.get("asset_type")
            or row.get("created_by") != expected_creator
            or row.get("reviewed_by") != expected_reviewer
            or not row.get("created_at")
            or not row.get("reviewed_at")
            or row.get("status") != "REVIEWED"
        ):
            errors.append(f"{asset_id}: asset lifecycle or frozen ownership is invalid")
        assets[asset_id] = {
            "row": row,
            "feature_ids": set(feature_ids),
            "page_ids": set(page_ids),
            "state_ids": set(state_ids),
        }

    manifest_entries = verify_exact_manifest(
        asset_package,
        "manifest.sha256",
        {"manifest.sha256", "COMMITTED"},
        "Phase 2 asset-package manifest",
        errors,
    )
    expected_entries = {}
    for value in assets.values():
        archive = str(value["row"].get("archive_path", ""))
        prefix = "asset-package/"
        package_relative = archive[len(prefix):] if archive.startswith(prefix) else archive
        expected_entries[package_relative] = str(value["row"].get("sha256", ""))
    if manifest_entries != expected_entries:
        errors.append("Phase 2 asset-package manifest does not exactly match asset-inventory.csv")
    if manifest_path.is_file() and committed_path.is_file():
        expected_marker = (sha256_file(manifest_path) + "\n").encode("ascii")
        try:
            if committed_path.read_bytes() != expected_marker:
                errors.append("Phase 2 asset-package COMMITTED marker is invalid")
        except OSError as exc:
            errors.append(f"Cannot read Phase 2 asset-package COMMITTED marker: {exc}")
    else:
        errors.append("Phase 2 asset package is not sealed")

    referenced: set[str] = set()
    active_rows = [row for row in inventory_rows if row.get("row_status") != "SUPERSEDED"]
    for row in active_rows:
        inventory_id = row.get("inventory_id", "")
        asset_ids = parse_json_id_list(row.get("asset_ids", ""), f"{inventory_id}.asset_ids", errors)
        if asset_ids == ["NONE_FOUND"]:
            continue
        if "NONE_FOUND" in asset_ids:
            errors.append(f"{inventory_id}: NONE_FOUND cannot be mixed with real Asset-IDs")
            continue
        for asset_id in asset_ids:
            referenced.add(asset_id)
            asset = assets.get(asset_id)
            if not asset:
                errors.append(f"{inventory_id}: references an unknown Asset-ID: {asset_id}")
                continue
            if (
                row.get("feature_id") not in asset["feature_ids"]
                or row.get("page_id") not in asset["page_ids"]
                or row.get("state_id") not in asset["state_ids"]
            ):
                errors.append(f"{asset_id}: asset scope does not cover inventory row {inventory_id}")
    orphaned = sorted(set(assets) - referenced)
    if orphaned:
        errors.append(f"Phase 2 asset inventory contains unreferenced assets: {orphaned[:5]}")
    return assets


def safe_relative_path(root: Path, relative: str, label: str, errors: list[str]) -> Path | None:
    """Resolve an existing run-local path without following a symbolic-link component."""
    pure = PurePosixPath(str(relative))
    if pure.is_absolute() or ".." in pure.parts or not pure.parts or str(pure) in {"", "."}:
        errors.append(f"Unsafe {label} path: {relative!r}")
        return None
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            errors.append(f"Symbolic links are prohibited in {label} path: {relative}")
            return None
    try:
        resolved_root = root.resolve()
        resolved = current.resolve()
        resolved.relative_to(resolved_root)
    except (OSError, ValueError):
        errors.append(f"{label} path escapes its root: {relative}")
        return None
    if not resolved.exists():
        errors.append(f"Missing {label}: {resolved}")
        return None
    return resolved


def actor_ids(ownership: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for value in ownership.values():
        if isinstance(value, str) and value:
            values.add(value)
        elif isinstance(value, list):
            values.update(str(item) for item in value if isinstance(item, str) and item)
    return values


def parse_sha256_manifest(path: Path, label: str, errors: list[str]) -> dict[str, str]:
    expected: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"Cannot read {label}: {exc}")
        return expected
    for number, line in enumerate(lines, start=1):
        if "  " not in line:
            errors.append(f"Malformed {label} line {number}")
            continue
        digest, relative = line.split("  ", 1)
        pure = PurePosixPath(relative)
        if (
            not SHA256_RE.fullmatch(digest)
            or pure.is_absolute()
            or ".." in pure.parts
            or not pure.parts
            or "\\" in relative
            or relative in expected
        ):
            errors.append(f"Unsafe or duplicate {label} entry: {relative!r}")
            continue
        expected[relative] = digest
    return expected


def verify_exact_manifest(
    directory: Path,
    manifest_name: str,
    excluded: set[str],
    label: str,
    errors: list[str],
) -> dict[str, str]:
    manifest_path = directory / manifest_name
    if not manifest_path.is_file() or manifest_path.is_symlink():
        errors.append(f"Missing or unsafe {label}: {manifest_path}")
        return {}
    expected = parse_sha256_manifest(manifest_path, label, errors)
    actual: dict[str, Path] = {}
    for path in directory.rglob("*"):
        if path.is_symlink():
            errors.append(f"Symbolic links are prohibited in {label} package: {path}")
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix()
        if relative in excluded:
            continue
        actual[relative] = path
    if set(expected) != set(actual):
        errors.append(
            f"{label} file set changed; missing={sorted(set(expected) - set(actual))[:5]}, "
            f"extra={sorted(set(actual) - set(expected))[:5]}"
        )
    for relative in sorted(set(expected) & set(actual)):
        if sha256_file(actual[relative]) != expected[relative]:
            errors.append(f"{label} hash mismatch: {relative}")
    return expected


def validate_complete_png(path: Path) -> tuple[int, int]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size < 45:
        raise ValueError(f"Missing, unsafe, or empty PNG: {path}")
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"Invalid PNG signature: {path}")
    offset = 8
    chunks: list[tuple[bytes, bytes]] = []
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError(f"Truncated PNG chunk: {path}")
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError(f"Truncated PNG payload: {path}")
        payload = data[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length:end])[0]
        if (binascii.crc32(chunk_type + payload) & 0xFFFFFFFF) != expected_crc:
            raise ValueError(f"PNG CRC mismatch: {path}")
        chunks.append((chunk_type, payload))
        offset = end
        if chunk_type == b"IEND":
            break
    if offset != len(data) or not chunks or chunks[0][0] != b"IHDR" or chunks[-1][0] != b"IEND":
        raise ValueError(f"PNG chunk order or trailing data is invalid: {path}")
    if len([kind for kind, _ in chunks if kind == b"IHDR"]) != 1 or len(chunks[0][1]) != 13:
        raise ValueError(f"PNG must contain one valid IHDR: {path}")
    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", chunks[0][1]
    )
    allowed_depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
    if (
        width < 1 or height < 1 or compression != 0 or filtering != 0 or interlace != 0
        or color_type not in allowed_depths or bit_depth not in allowed_depths[color_type]
    ):
        raise ValueError(f"PNG dimensions or encoding are unsupported: {path}")
    idat = b"".join(payload for kind, payload in chunks if kind == b"IDAT")
    if not idat:
        raise ValueError(f"PNG has no IDAT data: {path}")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    expected_size = height * (((width * channels * bit_depth + 7) // 8) + 1)
    try:
        decompressor = zlib.decompressobj()
        pixels = decompressor.decompress(idat, expected_size + 1)
        pixels += decompressor.flush()
    except zlib.error as exc:
        raise ValueError(f"PNG image data is corrupt: {path}: {exc}") from exc
    if not decompressor.eof or decompressor.unused_data or len(pixels) != expected_size:
        raise ValueError(f"PNG image data length is invalid: {path}")
    return width, height


def phase4_closure_excluded(relative: PurePosixPath) -> bool:
    value = relative.as_posix()
    if value in STAGE4_CLOSURE_EXACT_EXCLUDES:
        return True
    if any(part in {".locks", ".staging", "__pycache__", ".pytest_cache"} for part in relative.parts):
        return True
    if relative.suffix in {".tmp", ".pyc"} or relative.name.endswith(".lock"):
        return True
    return bool(
        relative.parts
        and relative.parts[0] == "harmony-project"
        and any(part in STAGE4_PROJECT_EXCLUDED_PARTS for part in relative.parts[1:])
    )


def verify_phase4_closure(workspace: Path, errors: list[str]) -> dict[str, str]:
    manifest = workspace / "stage-04-closure-manifest.sha256"
    if not manifest.is_file() or manifest.is_symlink():
        errors.append("Phase 4 closure manifest is missing or unsafe")
        return {}
    expected = parse_sha256_manifest(manifest, "Phase 4 closure manifest", errors)
    actual: dict[str, Path] = {}
    for path in workspace.rglob("*"):
        relative = PurePosixPath(path.relative_to(workspace).as_posix())
        if phase4_closure_excluded(relative):
            continue
        if path.is_symlink():
            errors.append(f"Symbolic links are prohibited in Phase 4 closure: {path}")
            continue
        if path.is_file():
            actual[relative.as_posix()] = path
    if set(expected) != set(actual):
        errors.append(
            "Phase 4 closure file set changed; "
            f"missing={sorted(set(expected) - set(actual))[:5]}, "
            f"extra={sorted(set(actual) - set(expected))[:5]}"
        )
    for relative in sorted(set(expected) & set(actual)):
        if sha256_file(actual[relative]) != expected[relative]:
            errors.append(f"Phase 4 closure hash mismatch: {relative}")
    return expected


def phase4_project_snapshot(project: Path, errors: list[str]) -> tuple[str | None, list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    if not project.is_dir() or project.is_symlink():
        errors.append(f"Phase 4 HarmonyOS project is missing or unsafe: {project}")
        return None, entries
    for path in sorted(project.rglob("*")):
        relative = path.relative_to(project)
        if any(part in STAGE4_PROJECT_EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            errors.append(f"Symbolic links are prohibited in the Phase 4 project: {path}")
            continue
        if path.is_file():
            entries.append(
                {"path": relative.as_posix(), "sha256": sha256_file(path), "size": path.stat().st_size}
            )
    entries.sort(key=lambda item: item["path"])
    canonical = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), entries


def phase4_source_row_key(row: dict[str, str]) -> str:
    values = [str(row.get(field, "")) for field in (
        "feature_id", "page_id", "state_id", "env_id", "evidence_id",
    )]
    if any(not value for value in values):
        raise ValueError(f"Inventory row lacks a complete source identity: {row.get('inventory_id', '')}")
    material = "|".join(values)
    return "SROW-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20].upper()


def phase4_json_string_list(value: str, label: str, errors: list[str], *, allow_empty: bool = True) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        errors.append(f"{label} is not a JSON string array")
        return []
    if (
        not isinstance(parsed, list)
        or (not allow_empty and not parsed)
        or any(not isinstance(item, str) or not item for item in parsed)
        or parsed != sorted(set(parsed))
    ):
        errors.append(f"{label} must be a sorted unique JSON string array")
        return []
    return parsed


def phase4_geometry(value: str, label: str, errors: list[str]) -> dict[str, float] | None:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        errors.append(f"{label} is not valid JSON geometry")
        return None
    if not isinstance(parsed, dict) or any(
        not isinstance(parsed.get(field), (int, float)) for field in ("x", "y", "width", "height")
    ):
        errors.append(f"{label} must contain numeric x/y/width/height")
        return None
    if parsed["x"] < 0 or parsed["y"] < 0 or parsed["width"] <= 0 or parsed["height"] <= 0:
        errors.append(f"{label} has invalid bounds")
        return None
    return {field: float(parsed[field]) for field in ("x", "y", "width", "height")}


def normalized_geometry_matches(
    android: dict[str, float], harmony: dict[str, float],
    android_size: tuple[float, float], harmony_size: tuple[float, float], tolerance: float,
) -> bool:
    """Compare rectangles after scaling Android coordinates into the Harmony viewport."""
    aw, ah = android_size
    hw, hh = harmony_size
    if min(aw, ah, hw, hh) <= 0:
        return False
    expected = {
        "x": android["x"] / aw * hw, "width": android["width"] / aw * hw,
        "y": android["y"] / ah * hh, "height": android["height"] / ah * hh,
    }
    return all(abs(expected[field] - harmony[field]) <= tolerance for field in expected)


def comparable_visual_spec(value: dict[str, Any]) -> dict[str, Any]:
    """Discard provenance-only fields; keep visual semantics for deterministic comparison."""
    ignored = {"source", "source_ref", "platform", "implementation", "notes"}
    return {key: item for key, item in value.items() if key not in ignored}


PHASE4_ATTEMPT_FIELDS = [
    "execution_id", "parity_id", "evidence_id", "started_at", "executed_by",
    "previous_chain_sha256", "chain_sha256",
]


def reviewed_visual_ids_are_acceptable(
    reviewed: Any, expected: set[str], tier: str,
) -> bool:
    """P4 分层验证：CORE 要求 reviewed 恰为全部视觉元素；LITE 允许非空抽样子集。

    LITE 子集须满足 >=1 且 ⊆ 全集（全集本身也合法）；CORE 子集被拒。
    """
    if not isinstance(reviewed, list):
        return False
    reviewed_set = {str(item) for item in reviewed}
    if tier == "LITE":
        return bool(reviewed_set) and reviewed_set <= expected
    return reviewed_set == expected


def validate_phase4_attempt_chain(rows: list[dict[str, str]], errors: list[str]) -> None:
    previous = "0" * 64
    identities: set[str] = set()
    evidence_ids: set[str] = set()
    for row in rows:
        material = {field: row.get(field, "") for field in PHASE4_ATTEMPT_FIELDS[:-1]}
        expected = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if (
            set(row) != set(PHASE4_ATTEMPT_FIELDS)
            or not row.get("execution_id") or row.get("execution_id") in identities
            or not row.get("evidence_id") or row.get("evidence_id") in evidence_ids
            or row.get("previous_chain_sha256") != previous
            or row.get("chain_sha256") != expected
        ):
            errors.append("Phase 4 attempt ledger hash chain or identity differs")
            return
        identities.add(row["execution_id"])
        evidence_ids.add(row["evidence_id"])
        previous = expected


def directory_snapshot_facts(directory: Path) -> tuple[str, int, int]:
    entries: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in directory snapshot: {path}")
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(directory).as_posix(),
                    "sha256": sha256_file(path),
                    "size": path.stat().st_size,
                }
            )
    canonical = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest(), sum(item["size"] for item in entries), len(entries)


def verify_sealed_package(
    directory: Path,
    package_id: str,
    lifecycle: str,
    label: str,
    errors: list[str],
) -> dict[str, str]:
    expected = verify_exact_manifest(
        directory, "manifest.sha256", {"manifest.sha256", "COMMITTED"}, label, errors
    )
    marker = directory / "COMMITTED"
    manifest = directory / "manifest.sha256"
    if not marker.is_file() or marker.is_symlink() or not manifest.is_file():
        errors.append(f"{label} is not COMMITTED")
    else:
        try:
            value = marker.read_text(encoding="utf-8").strip()
            manifest_digest = sha256_file(manifest)
            if not value.startswith(f"{package_id} {lifecycle} manifest_sha256={manifest_digest}"):
                errors.append(f"{label} COMMITTED marker is invalid")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"Cannot read {label} COMMITTED marker: {exc}")
    sealed_paths = (directory, *directory.rglob("*")) if directory.is_dir() else ()
    for path in sealed_paths:
        if path.stat().st_mode & 0o222:
            display = "." if path == directory else path.relative_to(directory)
            errors.append(f"{label} contains a writable sealed path: {display}")
            break
    return expected


def index_unique_rows(
    rows: list[dict[str, str]], key: str, label: str, errors: list[str]
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        identifier = row.get(key, "")
        if not ID_RE.fullmatch(identifier) or identifier in result:
            errors.append(f"{label} has an unsafe or duplicate {key}: {identifier!r}")
            continue
        result[identifier] = row
    return result


def validate_phase4_commands(
    package_dir: Path,
    commands: Any,
    environment: dict[str, Any],
    expected_categories: list[str],
    label: str,
    errors: list[str],
) -> None:
    if not isinstance(commands, list) or [
        item.get("category") if isinstance(item, dict) else None for item in commands
    ] != expected_categories:
        errors.append(f"{label} command category sequence differs")
        return
    contracts = environment.get("category_contracts") if isinstance(environment.get("category_contracts"), dict) else {}
    command_ids: set[str] = set()
    for command in commands:
        category = str(command.get("category", ""))
        command_id = str(command.get("command_id", ""))
        contract = contracts.get(category) if isinstance(contracts.get(category), dict) else {}
        stdout = safe_relative_path(
            package_dir, str(command.get("stdout_path", "")), f"{label} stdout", errors
        )
        stderr = safe_relative_path(
            package_dir, str(command.get("stderr_path", "")), f"{label} stderr", errors
        )
        argv = command.get("argv")
        plan_argv = command.get("plan_argv")
        if (
            not ID_RE.fullmatch(command_id)
            or command_id in command_ids
            or not contract
            or command.get("resolved_executable") != contract.get("resolved_executable")
            or command.get("executable_sha256") != contract.get("executable_sha256")
            or command.get("required_argv_tokens") != contract.get("required_argv_tokens")
            or command.get("success_output_contains") != contract.get("success_output_contains")
            or command.get("error_output_contains") != contract.get("error_output_contains")
            or command.get("success_output_matches") != contract.get("success_output_contains")
            or command.get("error_output_matches") != []
            or command.get("exit_code") != 0
            or command.get("timed_out") is not False
            or command.get("semantic_error") is not False
            or command.get("command_verdict") != "PASS"
            or not isinstance(plan_argv, list)
            or not isinstance(argv, list)
            or not argv
            or len(argv) != len(plan_argv)
            or plan_argv[0] != contract.get("resolved_executable")
            or argv[0] != contract.get("resolved_executable")
            or any(token not in plan_argv for token in contract.get("required_argv_tokens", []))
            or any(
                not isinstance(planned, str)
                or not isinstance(actual, str)
                or not actual
                or ("{" not in planned and actual != planned)
                or (planned.startswith("{") and planned.endswith("}") and actual == planned)
                for planned, actual in zip(plan_argv, argv)
            )
            or not stdout
            or not stderr
            or not stdout.is_file()
            or not stderr.is_file()
            or command.get("stdout_sha256") != sha256_file(stdout)
            or command.get("stderr_sha256") != sha256_file(stderr)
        ):
            errors.append(f"{label} command record differs from frozen contract: {category}")
        command_ids.add(command_id)
        selector = environment.get("device_selector_tokens")
        serial = str(environment.get("emulator", {}).get("serial", ""))
        bundle = str(environment.get("base_application", {}).get("bundle_name", ""))
        serial_categories = {
            "BUNDLE_CHECK", "DEVICE_CHECK", "CLEAN_INSTALL", "SEED_RESET", "NETWORK_PROFILE",
            "PERMISSION_PROFILE", "LAUNCH", "NAVIGATE", "BUSINESS_ASSERT",
            "SCREENSHOT_CAPTURE", "UITEST_SNAPSHOT_CAPTURE",
        }
        bundle_categories = {
            "BUNDLE_CHECK", "SIGNING_CHECK", "CLEAN_INSTALL", "SEED_RESET",
            "PERMISSION_PROFILE", "LAUNCH", "NAVIGATE", "BUSINESS_ASSERT",
            "SCREENSHOT_CAPTURE", "UITEST_SNAPSHOT_CAPTURE",
        }
        selector_present = False
        if isinstance(plan_argv, list) and isinstance(selector, list) and selector:
            selector_present = any(
                plan_argv[index:index + len(selector)] == selector
                for index in range(0, len(plan_argv) - len(selector) + 1)
            )
        if category in serial_categories and (
            not isinstance(plan_argv, list) or serial not in plan_argv or not selector_present
        ):
            errors.append(f"{label} command lacks exact frozen emulator selection: {category}")
        if category in bundle_categories and (
            not isinstance(plan_argv, list) or bundle not in plan_argv
        ):
            errors.append(f"{label} command lacks exact frozen Bundle: {category}")
        if stdout and stderr and stdout.is_file() and stderr.is_file():
            combined = stdout.read_text(encoding="utf-8", errors="replace") + "\n" + stderr.read_text(
                encoding="utf-8", errors="replace"
            )
            successes = [item for item in contract.get("success_output_contains", []) if item in combined]
            failures = [
                item for item in contract.get("error_output_contains", []) if item.lower() in combined.lower()
            ]
            if (
                successes != command.get("success_output_matches")
                or failures != command.get("error_output_matches")
                or failures
            ):
                errors.append(f"{label} command output verdict differs: {category}")


def validate_phase2(
    run_dir: Path,
    scope: dict[str, Any],
    baseline_env_id: str | None,
    phase1_facts: dict[str, Any],
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    phase_dir = run_dir / "phase-02-android-inventory"
    required = (
        "phase-manifest.json", "environments.json", "inventory.csv", "inventory.json",
        "inventory-manifest.sha256", "evidence-index.csv", "acceptance-registry.csv",
        "asset-inventory.csv", "asset-package/manifest.sha256", "asset-package/COMMITTED",
        "evidence-anchors.snapshot.csv", "evidence", "catalogs", "rechecks.csv",
        "static-analysis/COMMITTED", "runtime-observations.json", "page-gate-report.json",
        "advanced-observations.json", "probe-evidence-index.csv", "advanced-gate-report.json",
        "closure-report.json", "closure-manifest.sha256", "CLOSED",
    )
    for name in required:
        if not (phase_dir / name).exists():
            errors.append(f"Missing Phase 2 artifact: {phase_dir / name}")

    try:
        closure = load_json(phase_dir / "closure-report.json")
        phase_manifest = load_json(phase_dir / "phase-manifest.json")
        page_gate = load_json(phase_dir / "page-gate-report.json")
        advanced_gate = load_json(phase_dir / "advanced-gate-report.json")
    except ValueError as exc:
        errors.append(str(exc))
        return errors, warnings

    if closure.get("run_id") != scope.get("run_id") or phase_manifest.get("run_id") != scope.get("run_id"):
        errors.append("Phase 2 run identity does not match controller scope")
    if closure.get("final_verdict") != "PASS":
        errors.append("Phase 2 closure report does not say PASS")
    if closure.get("evidence_chain_closed") is not True:
        errors.append("Phase 2 evidence chain is not closed")
    if closure.get("decision_source") != "DETERMINISTIC_PAGE_ADVANCED_AND_EVIDENCE_GATES":
        errors.append("Phase 2 PASS was not issued by deterministic gates")
    if closure.get("page_gate_verdict") != "PASS" or page_gate.get("machine_verdict") != "PASS":
        errors.append("Phase 2 deterministic page gate does not say PASS")
    if page_gate.get("decision_source") != "DETERMINISTIC_STATIC_RUNTIME_GATE":
        errors.append("Phase 2 page gate has an invalid decision source")
    if (
        closure.get("advanced_gate_verdict") != "PASS"
        or advanced_gate.get("machine_verdict") != "PASS"
        or advanced_gate.get("decision_source") != "DETERMINISTIC_ADVANCED_RUNTIME_AND_PROBE_GATE"
    ):
        errors.append("Phase 2 advanced deterministic gate does not say PASS")
    if (
        closure.get("advanced_gate_required_observations")
        != advanced_gate.get("required_observations")
        or closure.get("advanced_gate_received_observations")
        != advanced_gate.get("received_observations")
        or advanced_gate.get("required_observations") != advanced_gate.get("received_observations")
    ):
        errors.append("Phase 2 advanced observations are incomplete")
    page_rows = page_gate.get("pages", [])
    if not isinstance(page_rows, list) or not page_rows or any(
        not isinstance(row, dict) or row.get("machine_verdict") != "PAGE_PASS" for row in page_rows
    ):
        errors.append("Phase 2 contains a page that did not receive PAGE_PASS")
    if (
        page_gate.get("required_atomic_observations", 0) <= 0
        or page_gate.get("required_atomic_observations") != page_gate.get("received_atomic_observations")
    ):
        errors.append("Phase 2 atomic page observations are incomplete")
    if closure.get("reviewer_role") != "coverage-checker-agent":
        errors.append("Phase 2 final reviewer must be coverage-checker-agent")
    expected_reviewer = scope.get("ownership", {}).get("coverage_checker_id")
    if closure.get("reviewer_id") != expected_reviewer:
        errors.append("Phase 2 reviewer ID does not match frozen ownership")
    if closure.get("baseline_env_id") != baseline_env_id:
        errors.append("Phase 2 baseline ENV-ID does not match controller scope")
    if closure.get("scope_sha256") != phase1_facts.get("scope_sha256"):
        errors.append("Phase 2 closure is bound to a different controller scope")
    if closure.get("open_rechecks", 0) != 0 or closure.get("open_critical_rechecks", 0) != 0:
        errors.append("Phase 2 has open rechecks")
    if closure.get("pending_confirmations", 0) != 0:
        errors.append("Phase 2 has pending confirmations")
    expected_features = set(scope.get("migration_scope", {}).get("included_features", []))
    if set(closure.get("covered_feature_ids", [])) != expected_features:
        errors.append("Phase 2 does not cover the complete included feature scope")
    if phase_manifest.get("status") != "CLOSED":
        errors.append("Phase 2 manifest is not CLOSED")
    if phase_manifest.get("scope_sha256") != phase1_facts.get("scope_sha256"):
        errors.append("Phase 2 manifest scope digest differs from controller gate")
    android = scope.get("android", {})
    if (
        phase_manifest.get("android_project_root") != str(Path(android.get("project_root", "")).expanduser().resolve())
        or phase_manifest.get("apk_path") != str(Path(android.get("apk_path", "")).expanduser().resolve())
        or phase_manifest.get("apk_sha256") != android.get("apk_sha256")
        or phase_manifest.get("source_revision") != android.get("source_revision")
        or phase_manifest.get("ownership") != scope.get("ownership")
        or phase_manifest.get("included_features") != scope.get("migration_scope", {}).get("included_features")
    ):
        errors.append("Phase 2 manifest identity differs from the frozen controller scope")

    ledger_rows = read_csv_rows(run_dir / "controller" / "task-ledger.csv")
    phase1_tasks = [row for row in ledger_rows if row.get("phase") == "1"]
    phase2_tasks = [row for row in ledger_rows if row.get("phase") == "2"]
    if (
        len(phase1_tasks) != 1 or phase1_tasks[0].get("status") != "PASS"
        or phase1_tasks[0].get("owner") != scope.get("ownership", {}).get("migration_controller_id")
    ):
        errors.append("Controller task ledger does not have a frozen Phase 1 PASS")
    if (
        len(phase2_tasks) != 1 or phase2_tasks[0].get("status") not in {"IN_PROGRESS", "PASS"}
        or phase2_tasks[0].get("owner") != scope.get("ownership", {}).get("inventory_lead_id")
    ):
        errors.append("Controller task ledger does not have the assigned Phase 2 task")

    open_controller_rework = [
        row for row in read_csv_rows(run_dir / "controller" / "rework-log.csv")
        if row.get("phase") in {"1", "2"} and row.get("status", "").upper() not in {"CLOSED", "SUPERSEDED"}
    ]
    if open_controller_rework:
        errors.append(f"Controller has open Phase 1/2 rework: {len(open_controller_rework)}")

    work_order_id = phase_manifest.get("work_order_id", "")
    registry = [
        row for row in read_csv_rows(run_dir / "controller" / "work-order-registry.csv")
        if row.get("work_order_id") == work_order_id and row.get("phase") == "2"
    ]
    if len(registry) != 1:
        errors.append("Phase 2 work order is not uniquely registered")
    else:
        registry_row = registry[0]
        work_order_path = run_dir / registry_row.get("relative_path", "")
        if (
            not work_order_path.is_file()
            or registry_row.get("status") != "ISSUED"
            or registry_row.get("scope_sha256") != phase1_facts.get("scope_sha256")
            or registry_row.get("issued_by") != scope.get("ownership", {}).get("migration_controller_id")
            or sha256_file(work_order_path) != registry_row.get("work_order_sha256")
            or phase_manifest.get("work_order_sha256") != registry_row.get("work_order_sha256")
        ):
            errors.append("Registered Phase 2 work order is missing, changed, or unauthorized")
    inventory_rows = read_csv_rows(phase_dir / "inventory.csv")
    index_rows = read_csv_rows(phase_dir / "evidence-index.csv")
    acceptance_rows = read_csv_rows(phase_dir / "acceptance-registry.csv")
    anchor_snapshot_rows = read_csv_rows(phase_dir / "evidence-anchors.snapshot.csv")
    controller_anchor_rows = [
        row for row in read_csv_rows(run_dir / "controller" / "evidence-anchor-registry.csv")
        if row.get("run_id") == scope.get("run_id") and row.get("phase") == "2"
    ]
    if not inventory_rows:
        errors.append("Phase 2 inventory is empty")
    if not index_rows:
        errors.append("Phase 2 evidence index is empty")
    if anchor_snapshot_rows != sorted(controller_anchor_rows, key=lambda row: row.get("evidence_id", "")):
        errors.append("Phase 2 evidence-anchor snapshot differs from the controller-owned registry")
    anchor_snapshot_path = phase_dir / "evidence-anchors.snapshot.csv"
    if not anchor_snapshot_path.is_file():
        errors.append("Phase 2 evidence-anchor snapshot is missing")
    elif closure.get("evidence_anchor_snapshot_sha256") != sha256_file(anchor_snapshot_path):
        errors.append("Phase 2 closure references a different evidence-anchor snapshot")
    anchors_by_id = {row.get("evidence_id", ""): row for row in controller_anchor_rows}
    if len(anchors_by_id) != len(controller_anchor_rows) or set(anchors_by_id) != {
        row.get("evidence_id", "") for row in index_rows
    }:
        errors.append("Controller evidence anchors do not exactly cover the Phase 2 evidence index")
    for index in index_rows:
        evidence_id = index.get("evidence_id", "")
        anchor = anchors_by_id.get(evidence_id, {})
        expected_relative = (
            f"evidence/{index.get('env_id', '')}/{index.get('page_id', '')}/"
            f"{index.get('state_id', '')}/{evidence_id}"
        )
        evidence_dir = phase_dir / expected_relative
        try:
            manifest_digest = sha256_file(evidence_dir / "manifest.sha256")
            metadata_digest = sha256_file(evidence_dir / "metadata.json")
        except OSError:
            manifest_digest = metadata_digest = ""
        if (
            index.get("relative_path") != expected_relative
            or anchor.get("anchor_id") != f"ANCH-{evidence_id}"
            or anchor.get("relative_path") != expected_relative
            or anchor.get("package_manifest_sha256") != manifest_digest
            or anchor.get("metadata_sha256") != metadata_digest
            or anchor.get("metadata_sha256") != index.get("metadata_sha256")
            or anchor.get("scope_sha256") != phase1_facts.get("scope_sha256")
            or anchor.get("environment_registry_sha256") != phase_manifest.get("environment_registry_sha256")
            or anchor.get("anchored_by") != scope.get("ownership", {}).get("migration_controller_id")
            or anchor.get("status") != "ANCHORED"
        ):
            errors.append(f"Controller evidence anchor differs for {evidence_id}")
    active_inventory = [row for row in inventory_rows if row.get("row_status") != "SUPERSEDED"]
    if any(row.get("row_status") != "REVIEWED" or row.get("reviewed_by") != expected_reviewer for row in active_inventory):
        errors.append("Phase 2 inventory lifecycle is not REVIEWED by the frozen checker")
    if any(row.get("status") not in {"ACCEPTED", "SUPERSEDED"} for row in index_rows):
        errors.append("Phase 2 evidence lifecycle contains an unaccepted status")
    accepted_pairs = {(row.get("inventory_id"), row.get("evidence_id")) for row in acceptance_rows if row.get("decision") == "ACCEPTED" and row.get("reviewed_by") == expected_reviewer}
    inventory_pairs = {(row.get("inventory_id"), row.get("evidence_id")) for row in active_inventory}
    if accepted_pairs != inventory_pairs:
        errors.append("Acceptance registry does not exactly match active reviewed inventory")
    validate_phase2_assets(phase_dir, scope, inventory_rows, errors)
    verify_closure_snapshot(phase_dir, closure, errors)
    return errors, warnings


def validate_phase3(
    run_dir: Path, scope: dict[str, Any], phase1_facts: dict[str, Any]
) -> tuple[list[str], list[str], str | None, str | None, str | None, str | None]:
    """Independently recheck the controller-issued and fully sealed Phase 3 result."""
    errors: list[str] = []
    warnings: list[str] = []
    phase_dir = run_dir / "phase-03-harmony-scaffold"
    required = (
        "stage-03-input-lock.json", "phase-manifest.json", "inputs/phase-02-gate-report.json",
        "inputs/phase-02-advanced-analysis.json", "inputs/phase-02-advanced-observations.json",
        "inputs/phase-02-advanced-gate-report.json", "inputs/phase-02-probe-evidence-index.csv",
        "inputs/arkui-stage-template.manifest.sha256", "template-generation.json",
        "advanced-obligations.json",
        "environments", "module-registry.csv", "dependency-policy.json", "architecture-map.csv",
        "route-registry.csv", "surface-registry.csv", "public-ui-registry.csv",
        "capability-contracts.csv", "asset-registry.csv", "migration-status.csv", "architecture-decisions.csv",
        "rework-tickets.csv", "harmony-project", "verification", "scaffold-snapshot-manifest.json",
        "build-report.json", "stage-03-gate-report.json", "stage-03-closure-manifest.sha256", "CLOSED",
    )
    for name in required:
        candidate = phase_dir / name
        if not candidate.exists() or candidate.is_symlink():
            errors.append(f"Missing or unsafe Phase 3 artifact: {candidate}")

    try:
        input_lock = load_json(phase_dir / "stage-03-input-lock.json")
        phase_manifest = load_json(phase_dir / "phase-manifest.json")
        stage_report = load_json(phase_dir / "stage-03-gate-report.json")
        build_report = load_json(phase_dir / "build-report.json")
    except ValueError as exc:
        errors.append(str(exc))
        return errors, warnings, None, None, None, None

    # The closure snapshot covers every Phase 3 file except the final report, its manifest, and CLOSED.
    verify_exact_manifest(
        phase_dir,
        "stage-03-closure-manifest.sha256",
        STAGE3_CLOSURE_EXACT_EXCLUDES,
        "Phase 3 closure manifest",
        errors,
    )
    stage_report_path = phase_dir / "stage-03-gate-report.json"
    closed_path = phase_dir / "CLOSED"
    if stage_report_path.is_file() and closed_path.is_file():
        try:
            if closed_path.read_text(encoding="utf-8").strip() != sha256_file(stage_report_path):
                errors.append("Phase 3 CLOSED marker does not bind the current stage gate report")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"Cannot read Phase 3 CLOSED marker: {exc}")

    if phase_manifest.get("phase") != 3:
        errors.append("Phase 3 manifest does not identify phase 3")
    if phase_manifest.get("run_id") != scope.get("run_id") or input_lock.get("run_id") != scope.get("run_id"):
        errors.append("Phase 3 run identity differs from controller scope")

    # Resolve the immutable, controller-registered Phase 3 work order.
    work_order_id = str(phase_manifest.get("work_order_id") or input_lock.get("work_order_id") or "")
    work_order: dict[str, Any] = {}
    work_order_sha256: str | None = None
    phase3_ownership: dict[str, Any] = {}
    if not ID_RE.fullmatch(work_order_id):
        errors.append("Phase 3 lacks a safe registered Work-Order-ID")
    work_order_registry = read_csv_rows(run_dir / "controller" / "work-order-registry.csv")
    registry_matches = [
        row for row in work_order_registry
        if row.get("work_order_id") == work_order_id and row.get("phase") == "3"
    ]
    active_phase3_orders = [
        row for row in work_order_registry
        if row.get("phase") == "3" and row.get("status", "").upper() != "SUPERSEDED"
    ]
    if len(active_phase3_orders) != 1 or (
        active_phase3_orders and active_phase3_orders[0].get("work_order_id") != work_order_id
    ):
        errors.append("Controller must have exactly one active Phase 3 work order")
    if len(registry_matches) != 1:
        errors.append("Phase 3 work order is not uniquely registered")
    else:
        registry_row = registry_matches[0]
        work_order_path = safe_relative_path(
            run_dir, registry_row.get("relative_path", ""), "Phase 3 work order", errors
        )
        if work_order_path and work_order_path.is_file():
            try:
                work_order = load_json(work_order_path)
                work_order_sha256 = sha256_file(work_order_path)
            except ValueError as exc:
                errors.append(str(exc))
        if (
            registry_row.get("status") != "ISSUED"
            or registry_row.get("scope_sha256") != phase1_facts.get("scope_sha256")
            or registry_row.get("issued_by") != scope.get("ownership", {}).get("migration_controller_id")
            or work_order_sha256 != registry_row.get("work_order_sha256")
        ):
            errors.append("Registered Phase 3 work order is changed, unauthorized, or bound to another scope")

    if work_order:
        if (
            work_order.get("work_order_id") != work_order_id
            or work_order.get("phase") != 3
            or work_order.get("status") != "ISSUED"
            or work_order.get("run_id") != scope.get("run_id")
            or work_order.get("scope_sha256") != phase1_facts.get("scope_sha256")
            or work_order.get("issued_by") != scope.get("ownership", {}).get("migration_controller_id")
            or work_order.get("required_skill") != "harmonyos-migration-scaffold"
            or work_order.get("included_features") != scope.get("migration_scope", {}).get("included_features")
            or work_order.get("excluded_features") != scope.get("migration_scope", {}).get("excluded_features")
        ):
            errors.append("Phase 3 work-order identity or authority is invalid")
        phase3_ownership = work_order.get("ownership") if isinstance(work_order.get("ownership"), dict) else {}
        role_values: list[str] = []
        for key in STAGE3_ROLE_KEYS:
            value = phase3_ownership.get(key)
            if not isinstance(value, str) or not ACTOR_RE.fullmatch(value):
                errors.append(f"Phase 3 work order has invalid ownership.{key}")
            else:
                role_values.append(value)
        if len(role_values) != len(STAGE3_ROLE_KEYS) or len(role_values) != len(set(role_values)):
            errors.append("All six frozen Phase 3 actor IDs must be present and distinct")
        overlap = sorted(set(role_values) & actor_ids(scope.get("ownership", {})))
        if overlap:
            errors.append(f"Phase 3 actors overlap frozen Phase 1/2 actors: {overlap}")

        if input_lock.get("work_order_id") != work_order_id or phase_manifest.get("work_order_id") != work_order_id:
            errors.append("Phase 3 input lock/manifest does not cite the registered work order")
        if (
            input_lock.get("work_order_sha256") != work_order_sha256
            or phase_manifest.get("work_order_sha256") != work_order_sha256
        ):
            errors.append("Phase 3 input lock/manifest is bound to another work-order digest")
        if input_lock.get("ownership") != phase3_ownership or phase_manifest.get("ownership") != phase3_ownership:
            errors.append("Phase 3 frozen ownership differs from the controller work order")
        if (
            input_lock.get("included_feature_ids")
            != sorted(scope.get("migration_scope", {}).get("included_features", []))
            or input_lock.get("excluded_feature_ids")
            != sorted(scope.get("migration_scope", {}).get("excluded_features", []))
        ):
            errors.append("Phase 3 input lock feature scope differs from controller scope")

        scope_lock = input_lock.get("controller_scope")
        scope_snapshot = phase_dir / "inputs" / "controller-scope.json"
        if (
            work_order.get("scope_relative_path") != "controller/scope.json"
            or not isinstance(scope_lock, dict)
            or scope_lock.get("sha256") != phase1_facts.get("scope_sha256")
            or not scope_snapshot.is_file()
            or sha256_file(scope_snapshot) != phase1_facts.get("scope_sha256")
            or scope_lock.get("snapshot_path") != str(scope_snapshot)
        ):
            errors.append("Phase 3 scope snapshot is missing, noncanonical, or changed")

        work_order_lock = input_lock.get("phase3_work_order")
        work_order_snapshot = phase_dir / "inputs" / "phase-03-work-order.json"
        if (
            not isinstance(work_order_lock, dict)
            or work_order_lock.get("sha256") != work_order_sha256
            or not work_order_snapshot.is_file()
            or sha256_file(work_order_snapshot) != work_order_sha256
            or work_order_lock.get("snapshot_path") != str(work_order_snapshot)
        ):
            errors.append("Phase 3 input lock does not contain the registered work-order snapshot")

        input_relatives = {
            "phase2_closure_sha256": "phase-02-android-inventory/closure-report.json",
            "phase2_closure_manifest_sha256": "phase-02-android-inventory/closure-manifest.sha256",
            "phase2_closed_sha256": "phase-02-android-inventory/CLOSED",
            "phase2_inventory_sha256": "phase-02-android-inventory/inventory.csv",
            "phase2_asset_inventory_sha256": "phase-02-android-inventory/asset-inventory.csv",
            "phase2_asset_manifest_sha256": "phase-02-android-inventory/asset-package/manifest.sha256",
            "phase2_asset_committed_sha256": "phase-02-android-inventory/asset-package/COMMITTED",
            "phase2_anchor_snapshot_sha256": "phase-02-android-inventory/evidence-anchors.snapshot.csv",
            "controller_anchor_registry_sha256": "controller/evidence-anchor-registry.csv",
        }
        lock_names = {
            "phase2_closure_sha256": "phase2_closure",
            "phase2_closure_manifest_sha256": "phase2_closure_manifest",
            "phase2_closed_sha256": "phase2_closed",
            "phase2_inventory_sha256": "phase2_inventory",
            "phase2_asset_inventory_sha256": "phase2_asset_inventory",
            "phase2_asset_manifest_sha256": "phase2_asset_package_manifest",
            "phase2_asset_committed_sha256": "phase2_asset_package_committed",
            "phase2_anchor_snapshot_sha256": "phase2_anchor_snapshot",
            "controller_anchor_registry_sha256": "controller_anchor_registry",
        }
        snapshot_relatives = {
            "phase2_closure_sha256": "inputs/phase-02-closure-report.json",
            "phase2_closure_manifest_sha256": "inputs/phase-02-closure-manifest.sha256",
            "phase2_closed_sha256": "inputs/phase-02-CLOSED",
            "phase2_inventory_sha256": "inputs/phase-02-inventory.csv",
            "phase2_asset_inventory_sha256": "inputs/phase-02-asset-inventory.csv",
            "phase2_asset_manifest_sha256": "inputs/phase-02-asset-package-manifest.sha256",
            "phase2_asset_committed_sha256": "inputs/phase-02-asset-package-COMMITTED",
            "phase2_anchor_snapshot_sha256": "inputs/phase-02-evidence-anchors.snapshot.csv",
            "controller_anchor_registry_sha256": "inputs/controller-evidence-anchor-registry.csv",
        }
        for digest_key, relative in input_relatives.items():
            path = safe_relative_path(run_dir, relative, digest_key, errors)
            actual_digest = sha256_file(path) if path and path.is_file() else None
            expected_digest = work_order.get(digest_key)
            if not SHA256_RE.fullmatch(str(expected_digest)) or actual_digest != expected_digest:
                errors.append(f"Phase 3 work order input changed: {digest_key}")
            relative_key = digest_key.removesuffix("_sha256") + "_relative_path"
            if work_order.get(relative_key) != relative:
                errors.append(f"Phase 3 work order has a noncanonical input path: {relative_key}")
            lock_value = input_lock.get(lock_names[digest_key])
            lock_digest = lock_value.get("sha256") if isinstance(lock_value, dict) else input_lock.get(digest_key)
            if lock_digest != expected_digest:
                errors.append(f"Phase 3 input lock does not bind {digest_key}")
            snapshot_path = phase_dir / snapshot_relatives[digest_key]
            if (
                not snapshot_path.is_file()
                or sha256_file(snapshot_path) != expected_digest
                or not isinstance(lock_value, dict)
                or (path is not None and lock_value.get("path") != str(path))
                or lock_value.get("snapshot_path") != str(snapshot_path)
            ):
                errors.append(f"Phase 3 input snapshot does not bind {digest_key}")

        phase2_asset_rows = read_csv_rows(run_dir / "phase-02-android-inventory" / "asset-inventory.csv")
        phase2_assets = {row.get("asset_id", ""): row for row in phase2_asset_rows}
        locked_asset_files = input_lock.get("phase2_asset_files")
        if not isinstance(locked_asset_files, list):
            errors.append("Phase 3 input lock lacks phase2_asset_files")
            locked_asset_files = []
        locked_by_id: dict[str, dict[str, Any]] = {}
        for record in locked_asset_files:
            if not isinstance(record, dict):
                errors.append("Phase 3 phase2_asset_files contains a non-object record")
                continue
            asset_id = str(record.get("asset_id", ""))
            if not ID_RE.fullmatch(asset_id) or asset_id in locked_by_id:
                errors.append(f"Phase 3 input lock has an unsafe or duplicate Asset-ID: {asset_id!r}")
                continue
            locked_by_id[asset_id] = record
            source = phase2_assets.get(asset_id)
            if not source:
                errors.append(f"Phase 3 input lock contains an unknown Asset-ID: {asset_id}")
                continue
            archive_relative = str(source.get("archive_path", ""))
            canonical = safe_relative_path(
                run_dir / "phase-02-android-inventory",
                archive_relative,
                f"Phase 2 asset {asset_id}",
                errors,
            )
            if (
                record.get("archive_path") != archive_relative
                or record.get("sha256") != source.get("sha256")
                or canonical is None
                or record.get("path") != str(canonical)
                or (canonical.is_file() and sha256_file(canonical) != source.get("sha256"))
            ):
                errors.append(f"Phase 3 input lock does not bind Phase 2 asset {asset_id}")
        if set(locked_by_id) != set(phase2_assets):
            errors.append("Phase 3 input lock does not exactly cover Phase 2 archived assets")

        gate_snapshot = phase_dir / "inputs" / "phase-02-gate-report.json"
        controller_gate_snapshot = safe_relative_path(
            run_dir,
            str(work_order.get("phase2_gate_snapshot_relative_path", "")),
            "controller-owned Phase 2 gate snapshot",
            errors,
        )
        gate_digest = sha256_file(gate_snapshot) if gate_snapshot.is_file() else None
        if (
            not SHA256_RE.fullmatch(str(work_order.get("phase2_gate_sha256")))
            or gate_digest != work_order.get("phase2_gate_sha256")
            or not controller_gate_snapshot
            or sha256_file(controller_gate_snapshot) != work_order.get("phase2_gate_sha256")
        ):
            errors.append("Frozen Phase 2 gate snapshot differs from the Phase 3 work order")
        lock_gate = input_lock.get("phase2_gate")
        lock_gate_digest = lock_gate.get("sha256") if isinstance(lock_gate, dict) else input_lock.get("phase2_gate_sha256")
        if lock_gate_digest != work_order.get("phase2_gate_sha256"):
            errors.append("Phase 3 input lock does not bind the Phase 2 gate snapshot")
        if (
            not isinstance(lock_gate, dict)
            or lock_gate.get("path") != str(gate_snapshot)
            or not controller_gate_snapshot
            or lock_gate.get("source_path") != str(controller_gate_snapshot)
        ):
            errors.append("Phase 3 Gate 2 input path is not the immutable local snapshot")
        try:
            frozen_gate = load_json(gate_snapshot)
            if (
                frozen_gate.get("phase") != 2
                or frozen_gate.get("verdict") != "PASS"
                or frozen_gate.get("scope_sha256") != phase1_facts.get("scope_sha256")
                or frozen_gate.get("errors")
            ):
                errors.append("Frozen controller Gate 2 snapshot is not a complete PASS")
        except ValueError as exc:
            errors.append(str(exc))

    # Recompute the Phase 2 advanced handoff and deterministic ArkUI template provenance.
    try:
        advanced_lock = input_lock.get("phase2_advanced", {})
        advanced_paths = {
            "analysis": (
                run_dir / "phase-02-android-inventory" / "static-analysis" / "advanced-analysis.json",
                phase_dir / "inputs" / "phase-02-advanced-analysis.json",
            ),
            "observations": (
                run_dir / "phase-02-android-inventory" / "advanced-observations.json",
                phase_dir / "inputs" / "phase-02-advanced-observations.json",
            ),
            "gate": (
                run_dir / "phase-02-android-inventory" / "advanced-gate-report.json",
                phase_dir / "inputs" / "phase-02-advanced-gate-report.json",
            ),
            "probe_index": (
                run_dir / "phase-02-android-inventory" / "probe-evidence-index.csv",
                phase_dir / "inputs" / "phase-02-probe-evidence-index.csv",
            ),
        }
        for key, (source, snapshot) in advanced_paths.items():
            record = advanced_lock.get(key, {})
            if (
                not source.is_file() or not snapshot.is_file()
                or record.get("path") != str(source.resolve())
                or record.get("snapshot_path") != str(snapshot.resolve())
                or sha256_file(source) != record.get("sha256")
                or sha256_file(snapshot) != record.get("sha256")
            ):
                errors.append(f"Phase 3 advanced input binding differs: {key}")
        advanced_analysis = load_json(advanced_paths["analysis"][1])
        advanced_gate = load_json(advanced_paths["gate"][1])
        discovered = {
            "DYNAMIC_RISK": {str(row.get("risk_id")) for row in advanced_analysis.get("dynamic_risks", [])},
            "SIDE_EFFECT": {str(row.get("candidate_id")) for row in advanced_analysis.get("side_effects", [])},
            "SCENARIO": {str(row.get("scenario_id")) for row in advanced_analysis.get("scenarios", [])},
        }
        if (
            advanced_gate.get("machine_verdict") != "PASS"
            or advanced_gate.get("required_observations") != advanced_gate.get("received_observations")
            or sorted(discovered["DYNAMIC_RISK"]) != advanced_lock.get("dynamic_risk_ids")
            or sorted(discovered["SIDE_EFFECT"]) != advanced_lock.get("side_effect_ids")
            or sorted(discovered["SCENARIO"]) != advanced_lock.get("scenario_ids")
        ):
            errors.append("Phase 3 does not preserve the complete Phase 2 advanced PASS denominator")
        obligations = load_json(phase_dir / "advanced-obligations.json")
        obligation_rows = obligations.get("obligations", [])
        obligation_ids = [str(row.get("subject_id")) for row in obligation_rows if isinstance(row, dict)]
        expected_ids = set().union(*discovered.values())
        if (
            len(obligation_ids) != len(set(obligation_ids))
            or set(obligation_ids) != expected_ids
            or sorted(expected_ids) != input_lock.get("advanced_obligation_ids")
        ):
            errors.append("Phase 3 advanced obligations are incomplete or duplicated")
        template_lock = input_lock.get("arkui_template", {})
        template_manifest = phase_dir / "inputs" / "arkui-stage-template.manifest.sha256"
        generation_path = phase_dir / "template-generation.json"
        generation = load_json(generation_path)
        if (
            template_lock.get("template_id") != "ARKUI-STAGE-TEMPLATE-V1"
            or not template_manifest.is_file()
            or sha256_file(template_manifest) != template_lock.get("manifest_sha256")
            or generation.get("template_manifest_sha256") != template_lock.get("manifest_sha256")
            or generation.get("generated_file_count") != template_lock.get("file_count")
            or phase_manifest.get("template_generation_sha256") != sha256_file(generation_path)
        ):
            errors.append("Phase 3 ArkUI template provenance is invalid")
        for relative in generation.get("required_files", []):
            required_file = safe_relative_path(
                phase_dir / "harmony-project", str(relative), "required ArkUI template file", errors
            )
            if required_file is None or not required_file.is_file():
                errors.append(f"Phase 3 generated project lacks template file: {relative}")
    except (OSError, TypeError, ValueError) as exc:
        errors.append(f"Cannot validate Phase 3 advanced/template handoff: {exc}")

    expected_architecture_lead = phase3_ownership.get("architecture_lead_id")
    expected_toolchain = phase3_ownership.get("toolchain_agent_id")
    expected_navigation = phase3_ownership.get("navigation_agent_id")
    expected_public_ui = phase3_ownership.get("public_ui_agent_id")
    expected_capability = phase3_ownership.get("capability_contract_agent_id")
    expected_acceptance = phase3_ownership.get("architecture_acceptance_agent_id")

    henv_id = str(stage_report.get("henv_id") or "")
    verification_id = str(stage_report.get("verification_id") or "")
    if not ID_RE.fullmatch(henv_id):
        errors.append("Phase 3 gate report lacks a safe HENV-ID")
    if not ID_RE.fullmatch(verification_id):
        errors.append("Phase 3 gate report lacks a safe HVER-ID")
    if stage_report.get("phase") != 3 or stage_report.get("verdict") != "PASS":
        errors.append("Phase 3 gate report does not say PASS")
    if not ID_RE.fullmatch(str(stage_report.get("gate_id", ""))) or stage_report.get("run_id") != scope.get("run_id"):
        errors.append("Phase 3 gate report has an unsafe Gate-ID or wrong run identity")
    if (
        stage_report.get("reviewer_role") != "architecture-acceptance-agent"
        or stage_report.get("reviewer_id") != expected_acceptance
    ):
        errors.append("Phase 3 report was not issued by the frozen architecture acceptance agent")
    if stage_report.get("errors"):
        errors.append("Phase 3 gate report contains errors")
    counts = stage_report.get("counts") if isinstance(stage_report.get("counts"), dict) else {}
    if counts.get("open_rework", counts.get("open_blocking_rework", 0)) != 0:
        errors.append("Phase 3 gate report has open rework")
    if counts.get("inventory_rows") != counts.get("architecture_rows"):
        errors.append("Phase 3 architecture mapping is not one-to-one with frozen inventory")
    if not isinstance(counts.get("screenshots"), int) or counts.get("screenshots", 0) <= 0:
        errors.append("Phase 3 has no sealed emulator screenshot evidence")
    attestations = stage_report.get("attestations") if isinstance(stage_report.get("attestations"), dict) else {}
    required_attestations = {
        "real_file_review", "placeholder_boundaries", "contract_only",
        "dependency_review", "runtime_smoke", "screenshot_review",
    }
    if any(attestations.get(name) is not True for name in required_attestations):
        errors.append("Phase 3 acceptance report lacks one or more mandatory attestations")
    input_lock_path = phase_dir / "stage-03-input-lock.json"
    if not input_lock_path.is_file() or stage_report.get("input_lock_sha256") != sha256_file(input_lock_path):
        errors.append("Phase 3 gate report references a different input lock")

    # Ledger ownership is frozen by the work order; Gate 3 remains with the architecture lead.
    phase3_tasks = [row for row in read_csv_rows(run_dir / "controller" / "task-ledger.csv") if row.get("phase") == "3"]
    if (
        len(phase3_tasks) != 1
        or phase3_tasks[0].get("owner") != expected_architecture_lead
        or phase3_tasks[0].get("status") not in {"IN_PROGRESS", "PASS"}
    ):
        errors.append("Controller task ledger does not have the frozen Phase 3 owner and active task")

    controller_phase3_rework = [
        row for row in read_csv_rows(run_dir / "controller" / "rework-log.csv")
        if row.get("phase") == "3"
    ]
    open_controller_rework = [
        row for row in controller_phase3_rework
        if row.get("status", "").upper() != "CLOSED"
    ]
    if open_controller_rework:
        errors.append(f"Controller has open Phase 3 rework: {len(open_controller_rework)}")

    environment_path = phase_dir / "environments" / henv_id / "harmony-environment.json"
    verification_dir = phase_dir / "verification" / verification_id
    try:
        environment = load_json(environment_path)
        verification = load_json(verification_dir / "metadata.json")
        verification_snapshot = load_json(verification_dir / "scaffold-snapshot-manifest.json")
        current_snapshot = load_json(phase_dir / "scaffold-snapshot-manifest.json")
        artifact_manifest = load_json(verification_dir / "artifact-manifest.json")
        screenshot_rows = read_csv_rows(verification_dir / "screenshot-index.csv")
    except ValueError as exc:
        errors.append(str(exc))
        environment, verification, verification_snapshot, current_snapshot = {}, {}, {}, {}
        artifact_manifest, screenshot_rows = {}, []

    henv_rows = [
        row for row in read_csv_rows(phase_dir / "environments" / "henv-registry.csv")
        if row.get("henv_id") == henv_id
    ]
    if (
        len(henv_rows) != 1
        or henv_rows[0].get("status") != "FROZEN"
        or henv_rows[0].get("frozen_by") != expected_architecture_lead
        or not environment_path.is_file()
        or (environment_path.is_file() and henv_rows[0].get("environment_sha256") != sha256_file(environment_path))
    ):
        errors.append("Selected HENV is not uniquely frozen and hash-bound by the architecture lead")
    if (
        environment.get("henv_id") != henv_id
        or environment.get("created_by") != expected_architecture_lead
        or environment.get("frozen_by", expected_architecture_lead) != expected_architecture_lead
    ):
        errors.append("Selected HENV ownership differs from the Phase 3 work order")
    devices = environment.get("devices") if isinstance(environment.get("devices"), list) else []
    device_ids: set[str] = set()
    required_devices: set[str] = set()
    screenshot_devices: set[str] = set()
    for device in devices:
        if not isinstance(device, dict):
            errors.append("HENV contains a non-object device entry")
            continue
        device_id = str(device.get("device_id", ""))
        if not ID_RE.fullmatch(device_id) or device_id in device_ids:
            errors.append(f"Unsafe or duplicate HDEVICE-ID: {device_id!r}")
            continue
        device_ids.add(device_id)
        if device.get("required") is True:
            required_devices.add(device_id)
        if device.get("screenshot_required") is True:
            screenshot_devices.add(device_id)
    if not required_devices or not screenshot_devices or not screenshot_devices.issubset(required_devices):
        errors.append("HENV must contain required devices and screenshot-required devices")

    if verification_dir.is_dir():
        verify_exact_manifest(
            verification_dir, "manifest.sha256", {"manifest.sha256", "COMMITTED"},
            "HVER manifest", errors,
        )
    committed_path = verification_dir / "COMMITTED"
    if committed_path.is_file():
        try:
            committed = committed_path.read_text(encoding="utf-8").strip()
            if not committed.startswith(f"{verification_id} PASS "):
                errors.append("HVER COMMITTED marker does not bind the selected passing verification")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"Cannot read HVER COMMITTED marker: {exc}")
    else:
        errors.append("Selected HVER package is not COMMITTED")
    if (
        verification.get("verification_id") != verification_id
        or verification.get("henv_id") != henv_id
        or verification.get("status") != "PASS"
        or verification.get("executed_by") != expected_toolchain
    ):
        errors.append("Selected HVER identity, status, or executor differs from the frozen work order")
    if build_report.get("status") != "PASS" or build_report.get("verification_id") != verification_id:
        errors.append("Phase 3 build report is not PASS for the selected HVER-ID")
    if build_report.get("henv_id") != henv_id:
        errors.append("Phase 3 build report references another HENV-ID")
    if (
        build_report.get("clean_build_passed") is not True
        or set(build_report.get("install_passed_devices", []) if isinstance(build_report.get("install_passed_devices"), list) else []) != required_devices
        or set(build_report.get("launch_passed_devices", []) if isinstance(build_report.get("launch_passed_devices"), list) else []) != required_devices
        or set(build_report.get("screenshot_required_devices", []) if isinstance(build_report.get("screenshot_required_devices"), list) else []) != screenshot_devices
        or set(verification.get("required_devices", []) if isinstance(verification.get("required_devices"), list) else []) != required_devices
        or set(verification.get("screenshot_required_devices", []) if isinstance(verification.get("screenshot_required_devices"), list) else []) != screenshot_devices
    ):
        errors.append("Build/HVER device coverage differs from the frozen HENV")

    required_command_categories = {
        "TOOLCHAIN", "DEVICE", "BUNDLE_CHECK", "SIGNING_CHECK", "CLEAN_BUILD",
        "INSTALL", "LAUNCH", "ROUTE_SMOKE", "SCREENSHOT_CAPTURE",
    }
    command_categories: set[str] = set()
    command_ids: set[str] = set()
    commands = verification.get("commands") if isinstance(verification.get("commands"), list) else []
    for index, command in enumerate(commands):
        if not isinstance(command, dict):
            errors.append(f"HVER command record {index} is not an object")
            continue
        command_id = str(command.get("command_id", ""))
        category = str(command.get("category", ""))
        if not ID_RE.fullmatch(command_id) or command_id in command_ids:
            errors.append(f"Unsafe or duplicate HVER Command-ID: {command_id!r}")
        command_ids.add(command_id)
        if category not in required_command_categories:
            errors.append(f"Unknown HVER command category: {category!r}")
        command_categories.add(category)
        device_id = str(command.get("device_id", ""))
        if device_id and not ID_RE.fullmatch(device_id):
            errors.append(f"Unsafe HVER HDEVICE-ID: {device_id!r}")
        if command.get("exit_code") != 0 or command.get("timed_out") is not False:
            errors.append(f"HVER command did not complete successfully: {command_id}")
        for stream in ("stdout", "stderr"):
            relative = str(command.get(f"{stream}_path", ""))
            log_path = safe_relative_path(verification_dir, relative, f"HVER {stream} log", errors)
            digest = str(command.get(f"{stream}_sha256", ""))
            if (
                not SHA256_RE.fullmatch(digest)
                or not log_path
                or not log_path.is_file()
                or sha256_file(log_path) != digest
            ):
                errors.append(f"HVER {stream} log hash differs for {command_id}")
    if command_categories != required_command_categories:
        errors.append(
            f"HVER command category coverage differs; "
            f"missing={sorted(required_command_categories - command_categories)}, "
            f"extra={sorted(command_categories - required_command_categories)}"
        )

    # Recompute the reviewed scaffold snapshot from its entry list and current files.
    snapshot_entries = current_snapshot.get("entries") if isinstance(current_snapshot.get("entries"), list) else []
    snapshot_paths: dict[str, Path] = {}
    canonical_entries: list[dict[str, Any]] = []
    for index, entry in enumerate(snapshot_entries):
        if not isinstance(entry, dict):
            errors.append(f"Scaffold snapshot entry {index} is not an object")
            continue
        relative = str(entry.get("path", ""))
        path = safe_relative_path(phase_dir, relative, "scaffold snapshot entry", errors)
        if relative in snapshot_paths:
            errors.append(f"Duplicate scaffold snapshot path: {relative}")
            continue
        if path and path.is_file():
            snapshot_paths[relative] = path
            if sha256_file(path) != entry.get("sha256") or path.stat().st_size != entry.get("size"):
                errors.append(f"Current scaffold file differs from snapshot: {relative}")
        canonical_entries.append(entry)
    canonical = json.dumps(canonical_entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    snapshot_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if current_snapshot.get("snapshot_sha256") != snapshot_digest:
        errors.append("Scaffold snapshot manifest digest is invalid")
    if current_snapshot.get("henv_id") != henv_id or current_snapshot.get("entry_count") != len(snapshot_entries):
        errors.append("Scaffold snapshot identity or entry count is invalid")
    if current_snapshot != verification_snapshot:
        errors.append("Current scaffold snapshot manifest differs from the sealed HVER snapshot")
    if (
        verification.get("source_snapshot_sha256") != snapshot_digest
        or build_report.get("source_snapshot_sha256") != snapshot_digest
        or stage_report.get("source_snapshot_sha256") != snapshot_digest
    ):
        errors.append("HVER, build report, or Gate 3 references another scaffold snapshot")

    excluded_value = current_snapshot.get("excluded_generated_parts")
    excluded_parts = set(excluded_value if isinstance(excluded_value, list) else [])
    if excluded_parts != STAGE3_SNAPSHOT_EXCLUDED_PARTS:
        errors.append("Scaffold snapshot uses an unauthorized generated-path exclusion set")
    expected_snapshot_paths: set[str] = set()
    project = phase_dir / "harmony-project"
    if project.is_dir():
        for path in project.rglob("*"):
            if path.is_symlink():
                errors.append(f"Symbolic links are prohibited in HarmonyOS project: {path}")
                continue
            relative_project = path.relative_to(project)
            if any(part in STAGE3_SNAPSHOT_EXCLUDED_PARTS for part in relative_project.parts):
                continue
            if path.is_file():
                expected_snapshot_paths.add(path.relative_to(phase_dir).as_posix())
    expected_snapshot_paths.update(STAGE3_SNAPSHOT_REGISTRIES)
    expected_snapshot_paths.add(f"environments/{henv_id}/harmony-environment.json")
    if set(snapshot_paths) != expected_snapshot_paths:
        errors.append(
            f"Current scaffold snapshot file set differs; "
            f"missing={sorted(expected_snapshot_paths - set(snapshot_paths))[:5]}, "
            f"extra={sorted(set(snapshot_paths) - expected_snapshot_paths)[:5]}"
        )

    # Enforce the six frozen Phase 3 assignments against the actual registries.
    role_csv_checks = (
        ("module-registry.csv", "created_by", expected_toolchain, "module creator"),
        ("route-registry.csv", "created_by", expected_navigation, "route creator"),
        ("surface-registry.csv", "created_by", expected_navigation, "surface creator"),
        ("public-ui-registry.csv", "created_by", expected_public_ui, "public UI creator"),
        ("capability-contracts.csv", "created_by", expected_capability, "capability contract creator"),
        ("architecture-decisions.csv", "decided_by", expected_architecture_lead, "architecture decision owner"),
    )
    for name, field, expected_actor, label in role_csv_checks:
        rows = read_csv_rows(phase_dir / name)
        wrong = [row for row in rows if row.get(field) != expected_actor]
        if wrong:
            errors.append(f"{name} contains {len(wrong)} row(s) with the wrong frozen {label}")

    # Phase 3 must carry every frozen Android asset into one safe HarmonyOS placement plan.
    phase2_asset_rows = read_csv_rows(run_dir / "phase-02-android-inventory" / "asset-inventory.csv")
    phase2_assets = {row.get("asset_id", ""): row for row in phase2_asset_rows}
    if len(phase2_assets) != len(phase2_asset_rows):
        errors.append("Phase 2 asset inventory contains duplicate Asset-IDs")
    stage3_asset_rows = read_csv_rows(phase_dir / "asset-registry.csv")
    stage3_assets: dict[str, dict[str, str]] = {}
    module_rows = read_csv_rows(phase_dir / "module-registry.csv")
    modules = {row.get("harmony_module_id", ""): row for row in module_rows}
    seen_symbols: set[tuple[str, str]] = set()
    allowed_plans = {
        ("DIRECT_COPY", "COPY_UNCHANGED"),
        ("FORMAT_CONVERSION", "CONVERT_FORMAT"),
        ("RECREATE_FROM_PUBLIC_UI", "RECREATE_LATER"),
    }
    for row in stage3_asset_rows:
        asset_id = row.get("asset_id", "")
        if not ID_RE.fullmatch(asset_id) or asset_id in stage3_assets:
            errors.append(f"Phase 3 asset registry has an unsafe or duplicate Asset-ID: {asset_id!r}")
            continue
        stage3_assets[asset_id] = row
        source = phase2_assets.get(asset_id)
        if not source:
            errors.append(f"Phase 3 asset registry contains an unknown Asset-ID: {asset_id}")
            continue
        for field, phase2_field in (
            ("phase2_archive_path", "archive_path"),
            ("asset_sha256", "sha256"),
            ("asset_type", "asset_type"),
        ):
            if row.get(field) != source.get(phase2_field):
                errors.append(f"{asset_id}: frozen {field} differs from Phase 2")
        for field in ("feature_ids", "page_ids", "state_ids"):
            source_ids = parse_json_id_list(source.get(field, ""), f"Phase 2 {asset_id}.{field}", errors)
            target_ids = parse_json_id_list(row.get(field, ""), f"Phase 3 {asset_id}.{field}", errors)
            if target_ids != source_ids:
                errors.append(f"{asset_id}: frozen {field} differs from Phase 2")
        module_id = row.get("target_module_id", "")
        module = modules.get(module_id)
        target_relative = row.get("target_path", "")
        target_pure = PurePosixPath(target_relative)
        module_relative = str(module.get("module_path", "")) if module else ""
        module_pure = PurePosixPath(module_relative)
        if not module or module.get("status") != "READY":
            errors.append(f"{asset_id}: target module is missing or not READY: {module_id}")
        if (
            target_pure.is_absolute()
            or not target_pure.parts
            or ".." in target_pure.parts
            or module_pure.is_absolute()
            or not module_pure.parts
            or ".." in module_pure.parts
            or target_pure.parts[:len(module_pure.parts)] != module_pure.parts
            or len(target_pure.parts) <= len(module_pure.parts)
        ):
            errors.append(f"{asset_id}: target_path is not safely inside its target module")
        else:
            current = phase_dir / "harmony-project"
            for part in target_pure.parts:
                current = current / part
                if current.is_symlink():
                    errors.append(f"{asset_id}: target_path crosses a symbolic link")
                    break
        target_symbol = row.get("target_symbol", "")
        symbol_key = (module_id, target_symbol)
        if not target_symbol or symbol_key in seen_symbols:
            errors.append(f"{asset_id}: target_symbol is empty or duplicated inside the module")
        seen_symbols.add(symbol_key)
        if (
            (row.get("planned_mode"), row.get("decision")) not in allowed_plans
            or row.get("created_by") != expected_architecture_lead
            or row.get("status") != "READY"
        ):
            errors.append(f"{asset_id}: asset plan, owner, or lifecycle is invalid")
    if set(stage3_assets) != set(phase2_assets):
        errors.append("Phase 3 asset registry does not exactly cover Phase 2 assets")

    architecture_rows = read_csv_rows(phase_dir / "architecture-map.csv")
    wrong_mappers = [
        row for row in architecture_rows
        if row.get("mapped_by") != (
            expected_architecture_lead
            if row.get("mapping_type") == "EXCLUDED_BY_SCOPE"
            else expected_navigation
        )
    ]
    if wrong_mappers:
        errors.append(
            f"architecture-map.csv contains {len(wrong_mappers)} row(s) with the wrong frozen mapper"
        )
    migration_rows = read_csv_rows(phase_dir / "migration-status.csv")
    wrong_status_owners = [
        row for row in migration_rows
        if row.get("updated_by") != (
            expected_architecture_lead
            if row.get("status") == "EXCLUDED_BY_SCOPE"
            else expected_capability
            if row.get("source_kind") == "CAPABILITY_REQUIREMENT"
            else expected_navigation
        )
    ]
    if wrong_status_owners:
        errors.append(
            f"migration-status.csv contains {len(wrong_status_owners)} row(s) with the wrong frozen owner"
        )
    if (
        phase_manifest.get("architecture_lead") != expected_architecture_lead
        or input_lock.get("locked_by") != expected_architecture_lead
    ):
        errors.append("Phase 3 manifest/input lock was not owned by the frozen architecture lead")

    # Recheck every screenshot package and its PNG/hash identity.
    screenshot_ids: set[str] = set()
    for row in screenshot_rows:
        screenshot_id = row.get("screenshot_id", "")
        if not ID_RE.fullmatch(screenshot_id) or screenshot_id in screenshot_ids:
            errors.append(f"Unsafe or duplicate Screenshot-ID: {screenshot_id!r}")
            continue
        screenshot_ids.add(screenshot_id)
        expected_relative = f"screenshots/{screenshot_id}"
        if row.get("relative_path") != expected_relative:
            errors.append(f"{screenshot_id}: screenshot package path is not canonical")
            continue
        screenshot_dir = safe_relative_path(verification_dir, expected_relative, "screenshot package", errors)
        if not screenshot_dir or not screenshot_dir.is_dir():
            continue
        verify_exact_manifest(
            screenshot_dir, "manifest.sha256", {"manifest.sha256", "COMMITTED"},
            f"screenshot {screenshot_id} manifest", errors,
        )
        screenshot_committed = screenshot_dir / "COMMITTED"
        if screenshot_committed.is_file():
            try:
                if not screenshot_committed.read_text(encoding="utf-8").strip().startswith(
                    f"{screenshot_id} SEALED "
                ):
                    errors.append(f"{screenshot_id}: COMMITTED marker is invalid")
            except (OSError, UnicodeDecodeError) as exc:
                errors.append(f"{screenshot_id}: cannot read COMMITTED marker: {exc}")
        else:
            errors.append(f"{screenshot_id}: screenshot package is not COMMITTED")
        screenshot_png = screenshot_dir / "screenshot.png"
        try:
            metadata = load_json(screenshot_dir / "metadata.json")
            width, height = validate_complete_png(screenshot_png)
            png_digest = sha256_file(screenshot_png)
            if (
                row.get("verification_id") != verification_id
                or row.get("henv_id") != henv_id
                or row.get("captured_by") != expected_toolchain
                or row.get("status") != "SEALED"
                or row.get("device_id") not in screenshot_devices
                or row.get("png_sha256") != png_digest
                or metadata.get("png_sha256") != png_digest
                or metadata.get("screenshot_id") != screenshot_id
                or metadata.get("verification_id") != verification_id
                or metadata.get("henv_id") != henv_id
                or metadata.get("captured_by") != expected_toolchain
                or str(width) != row.get("width")
                or str(height) != row.get("height")
            ):
                errors.append(f"{screenshot_id}: screenshot identity or PNG hash differs")
        except (ValueError, OSError) as exc:
            errors.append(f"{screenshot_id}: {exc}")
    verification_screenshot_value = verification.get("screenshot_ids")
    verification_screenshot_ids = set(
        verification_screenshot_value if isinstance(verification_screenshot_value, list) else []
    )
    if (
        not screenshot_ids
        or verification_screenshot_ids != screenshot_ids
        or build_report.get("screenshot_count") != len(screenshot_ids)
        or counts.get("screenshots") != len(screenshot_ids)
    ):
        errors.append("HVER, build report, Gate 3, and screenshot index counts/IDs differ")

    # Built artifacts are checked against both the sealed HVER and current project bytes.
    artifacts = artifact_manifest.get("artifacts") if isinstance(artifact_manifest.get("artifacts"), list) else []
    artifact_hashes: list[str] = []
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            errors.append(f"Artifact manifest entry {index} is not an object")
            continue
        path = safe_relative_path(project, str(artifact.get("path", "")), "HarmonyOS build artifact", errors)
        digest = str(artifact.get("sha256", ""))
        if not SHA256_RE.fullmatch(digest):
            errors.append(f"Artifact {index} has an invalid SHA-256")
            continue
        artifact_hashes.append(digest)
        if path and path.is_file() and (
            sha256_file(path) != digest or path.stat().st_size != artifact.get("size")
        ):
            errors.append(f"Current build artifact differs from sealed HVER: {artifact.get('path')}")
    if not artifact_hashes:
        errors.append("Selected HVER has no built artifact")
    if build_report.get("artifacts") != artifacts or build_report.get("artifact_count") != len(artifacts):
        errors.append("Build report differs from sealed artifact manifest")
    if stage_report.get("artifact_hashes") != artifact_hashes:
        errors.append("Gate 3 artifact hashes differ from sealed HVER artifacts")

    local_phase3_rework = read_csv_rows(phase_dir / "rework-tickets.csv")
    local_open_rework = [
        row for row in local_phase3_rework if row.get("status", "").upper() != "CLOSED"
    ]
    if local_open_rework:
        errors.append(f"Phase 3 has open local rework tickets: {len(local_open_rework)}")
    local_ids = [row.get("ticket_id", "") for row in local_phase3_rework]
    controller_ids = [row.get("rework_id", "") for row in controller_phase3_rework]
    if len(local_ids) != len(set(local_ids)) or len(controller_ids) != len(set(controller_ids)):
        errors.append("Phase 3 rework ledger or controller mirror contains duplicate Ticket-ID values")
    if set(local_ids) != set(controller_ids):
        errors.append("Phase 3 rework ledger and controller mirror contain different Ticket-ID sets")
    for local in local_phase3_rework:
        ticket_id = str(local.get("ticket_id", ""))
        problem_type = str(local.get("problem_type", "")).upper()
        route = STAGE3_REWORK_ROUTES.get(problem_type)
        if not ID_RE.fullmatch(ticket_id) or route is None:
            errors.append(f"Phase 3 rework ticket identity or type is invalid: {ticket_id!r}")
        else:
            expected_role, actor_key = route
            if (
                local.get("responsible_role") != expected_role
                or local.get("responsible_agent") != phase3_ownership.get(actor_key)
            ):
                errors.append(f"Phase 3 rework ticket differs from frozen routing: {ticket_id}")
        if (
            local.get("severity") not in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
            or local.get("opened_by") != expected_acceptance
            or local.get("confirmed_by") != expected_architecture_lead
            or local.get("status", "").upper() not in {"OPEN", "CLOSED"}
        ):
            errors.append(f"Phase 3 rework ticket authority or lifecycle is invalid: {ticket_id}")
        matches = [row for row in controller_phase3_rework if row.get("rework_id") == ticket_id]
        if len(matches) != 1:
            errors.append(f"Phase 3 rework ticket is not uniquely mirrored: {ticket_id}")
            continue
        mirrored = matches[0]
        expected_fields = {
            "created_at": local.get("opened_at", ""),
            "record_id": local.get("record_id", ""),
            "evidence_id": local.get("failed_verification_id", ""),
            "gate_rule": problem_type,
            "reason": local.get("notes", ""),
            "assigned_to": local.get("responsible_agent", ""),
        }
        if any(mirrored.get(field, "") != value for field, value in expected_fields.items()):
            errors.append(f"Controller rework mirror content differs: {ticket_id}")
        if not mirrored.get("completion_condition", ""):
            errors.append(f"Controller rework mirror lacks completion condition: {ticket_id}")
        if local.get("status", "").upper() == "CLOSED":
            if (
                local.get("closed_by") != expected_acceptance
                or mirrored.get("status") != "CLOSED"
                or mirrored.get("resolved_at") != local.get("closed_at")
                or mirrored.get("resolution_evidence_id")
                != local.get("resolution_verification_id")
                or mirrored.get("reviewed_by") != expected_acceptance
            ):
                errors.append(f"Closed Phase 3 rework mirror differs: {ticket_id}")
        elif mirrored.get("status") != "REWORK":
            errors.append(f"Open Phase 3 rework mirror status differs: {ticket_id}")

    return (
        errors,
        warnings,
        henv_id if ID_RE.fullmatch(henv_id) else None,
        verification_id if ID_RE.fullmatch(verification_id) else None,
        str(expected_architecture_lead) if expected_architecture_lead else None,
        work_order_id if ID_RE.fullmatch(work_order_id) else None,
    )


def validate_phase4(
    run_dir: Path, scope: dict[str, Any], phase1_facts: dict[str, Any]
) -> tuple[list[str], list[str], list[str], list[str], str | None, str | None]:
    """Independently recheck the controller-issued and fully sealed Phase 4 result."""
    errors: list[str] = []
    warnings: list[str] = []
    phase_dir = run_dir / "phase-04-harmony-implementation"
    required = (
        "stage-04-input-lock.json", "phase-manifest.json", "initial-project-snapshot.json",
        "migration-unit-contracts.json",
        "implementation-ledger.csv",
        "feature-work-order-registry.csv", "feature-work-orders",
        "parity-map.csv", "visual-elements.csv", "asset-migration.csv",
        "asset-policy.json", "asset-conversion-contracts.json", "asset-conversions",
        "capability-implementation.csv", "nativeization-decisions.csv", "evidence-index.csv",
        "acceptance-ledger.csv", "rework-tickets.csv", "environments/h4env-registry.csv",
        "harmony-project", "builds", "evidence", "reviews", "stage-04-gate-report.json",
        "stage-04-closure-manifest.sha256", "CLOSED",
        "ui-test-snapshot-generation-manifest.json",
    )
    for relative in required:
        candidate = phase_dir / relative
        if not candidate.exists() or candidate.is_symlink():
            errors.append(f"Missing or unsafe Phase 4 artifact: {candidate}")
    try:
        phase_manifest = load_json(phase_dir / "phase-manifest.json")
        input_lock = load_json(phase_dir / "stage-04-input-lock.json")
        stage_report = load_json(phase_dir / "stage-04-gate-report.json")
    except ValueError as exc:
        errors.append(str(exc))
        return errors, warnings, [], [], None, None

    verify_phase4_closure(phase_dir, errors)
    stage_report_path = phase_dir / "stage-04-gate-report.json"
    closed_path = phase_dir / "CLOSED"
    if stage_report_path.is_file() and closed_path.is_file():
        try:
            if closed_path.read_bytes() != (sha256_file(stage_report_path) + "\n").encode("ascii"):
                errors.append("Phase 4 CLOSED marker does not bind the current gate report")
        except OSError as exc:
            errors.append(f"Cannot read Phase 4 CLOSED marker: {exc}")
    if any(path.is_file() and path.suffix.lower() == ".mp4" for path in phase_dir.rglob("*")):
        errors.append("MP4 is prohibited in Phase 4")
    expected_input_lock_keys = {
        "schema_version", "stage", "run_id", "created_at", "locked_by", "work_order_id",
        "work_order_sha256", "ownership", "controller_gate3_snapshot_sha256",
        "phase3_work_order_id", "phase3_work_order_sha256", "inputs", "android_evidence",
        "phase2_asset_files", "h4envs", "asset_conversion_contracts_sha256",
        "migration_unit_contracts_sha256",
        "page_contract_registry", "page_contracts",
        "phase2_inventory_ids", "phase2_asset_ids", "required_h4env_ids",
        "phase3_source_snapshot_sha256",
        "ui_test_snapshot_generation",
    }
    expected_phase_manifest_keys = {
        "schema_version", "run_id", "project_id", "phase", "status", "initialized_at",
        "work_order_id", "work_order_sha256", "work_order_relative_path", "ownership",
        "roles", "input_lock_sha256", "initial_project_snapshot_sha256",
        "asset_conversion_contracts_sha256", "migration_unit_contracts_sha256",
        "page_contract_registry_sha256",
        "formal_evidence_device_type", "mp4_allowed",
        "source_first_assets_required",
    }
    if (
        set(phase_manifest) != expected_phase_manifest_keys
        or phase_manifest.get("schema_version") != "1.0"
        or phase_manifest.get("phase") != 4
        or phase_manifest.get("status") != "IN_PROGRESS"
        or not phase_manifest.get("initialized_at")
        or phase_manifest.get("formal_evidence_device_type") != "emulator"
        or phase_manifest.get("mp4_allowed") is not False
        or phase_manifest.get("source_first_assets_required") is not True
        or set(input_lock) != expected_input_lock_keys
        or input_lock.get("schema_version") != "1.0"
        or input_lock.get("stage") != 4
        or not input_lock.get("created_at")
        or phase_manifest.get("run_id") != scope.get("run_id")
        or input_lock.get("run_id") != scope.get("run_id")
    ):
        errors.append("Phase 4 manifest/input-lock identity differs from controller scope")
    generation_manifest = phase_dir / "ui-test-snapshot-generation-manifest.json"
    generation_lock = input_lock.get("ui_test_snapshot_generation")
    if (
        not isinstance(generation_lock, dict)
        or set(generation_lock) != {
            "relative_path", "sha256", "generation_id", "page_ids", "probe_count",
            "contract", "production_packaging",
        }
        or generation_lock.get("relative_path") != "ui-test-snapshot-generation-manifest.json"
        or generation_lock.get("contract") != "ui-test-snapshot-generation-v1"
        or generation_lock.get("production_packaging") != "FORBIDDEN"
        or not generation_manifest.is_file()
        or generation_lock.get("sha256") != sha256_file(generation_manifest)
        or generation_manifest.stat().st_mode & 0o222
    ):
        errors.append("Phase 4 UiTest generation manifest is missing, mutable, or hash-mismatched")
    for frozen_name in (
        "stage-04-input-lock.json", "phase-manifest.json", "initial-project-snapshot.json",
        "asset-conversion-contracts.json",
    ):
        frozen_path = phase_dir / frozen_name
        if frozen_path.is_file() and frozen_path.stat().st_mode & 0o222:
            errors.append(f"Frozen Phase 4 governance record is writable: {frozen_name}")

    # Resolve the unique immutable Phase 4 order and the upstream Phase 3 order it cites.
    registry_rows = read_csv_rows(run_dir / "controller" / "work-order-registry.csv")
    work_order_id = str(phase_manifest.get("work_order_id") or input_lock.get("work_order_id") or "")
    active_phase4 = [
        row for row in registry_rows
        if row.get("phase") == "4" and row.get("status", "").upper() != "SUPERSEDED"
    ]
    matches = [row for row in registry_rows if row.get("phase") == "4" and row.get("work_order_id") == work_order_id]
    work_order: dict[str, Any] = {}
    work_order_path: Path | None = None
    work_order_sha256: str | None = None
    if not ID_RE.fullmatch(work_order_id):
        errors.append("Phase 4 lacks a safe registered Work-Order-ID")
    if len(active_phase4) != 1 or (active_phase4 and active_phase4[0].get("work_order_id") != work_order_id):
        errors.append("Controller must have exactly one active Phase 4 work order")
    if len(matches) != 1:
        errors.append("Phase 4 work order is not uniquely registered")
    else:
        row = matches[0]
        work_order_path = safe_relative_path(run_dir, row.get("relative_path", ""), "Phase 4 work order", errors)
        if work_order_path and work_order_path.is_file():
            try:
                work_order = load_json(work_order_path)
                work_order_sha256 = sha256_file(work_order_path)
            except ValueError as exc:
                errors.append(str(exc))
        if (
            row.get("relative_path") != f"controller/work-orders/{work_order_id}.json"
            or
            row.get("status") != "ISSUED"
            or row.get("scope_sha256") != phase1_facts.get("scope_sha256")
            or row.get("issued_by") != scope.get("ownership", {}).get("migration_controller_id")
            or row.get("work_order_sha256") != work_order_sha256
        ):
            errors.append("Registered Phase 4 work order is changed, unauthorized, or bound to another scope")

    phase4_ownership = work_order.get("ownership") if isinstance(work_order.get("ownership"), dict) else {}
    role_values: list[str] = []
    for key in STAGE4_ROLE_KEYS:
        value = phase4_ownership.get(key)
        if not isinstance(value, str) or not ACTOR_RE.fullmatch(value):
            errors.append(f"Phase 4 work order has invalid ownership.{key}")
        else:
            role_values.append(value)
    if len(role_values) != len(STAGE4_ROLE_KEYS) or len(role_values) != len(set(role_values)):
        errors.append("All four frozen Phase 4 actor IDs must be present and distinct")

    upstream_order_path: Path | None = None
    upstream_order: dict[str, Any] = {}
    upstream_relative = str(work_order.get("upstream_phase3_work_order_relative_path", ""))
    if upstream_relative:
        upstream_order_path = safe_relative_path(run_dir, upstream_relative, "upstream Phase 3 work order", errors)
        if upstream_order_path and upstream_order_path.is_file():
            try:
                upstream_order = load_json(upstream_order_path)
            except ValueError as exc:
                errors.append(str(exc))
    else:
        errors.append("Phase 4 work order lacks the upstream Phase 3 work-order path")
    phase3_ownership = upstream_order.get("ownership") if isinstance(upstream_order.get("ownership"), dict) else {}
    prior_actor_ids = actor_ids(scope.get("ownership", {})) | actor_ids(phase3_ownership)
    overlap = sorted(set(role_values) & prior_actor_ids)
    if overlap:
        errors.append(f"Phase 4 actors overlap frozen Phase 1–3 actors: {overlap}")
    if (
        work_order.get("work_order_id") != work_order_id
        or work_order.get("phase") != 4
        or work_order.get("status") != "ISSUED"
        or work_order.get("run_id") != scope.get("run_id")
        or work_order.get("scope_sha256") != phase1_facts.get("scope_sha256")
        or work_order.get("issued_by") != scope.get("ownership", {}).get("migration_controller_id")
        or work_order.get("required_skill") != "harmonyos-feature-implementation"
        or work_order.get("included_features") != scope.get("migration_scope", {}).get("included_features")
        or work_order.get("excluded_features") != scope.get("migration_scope", {}).get("excluded_features")
        or work_order.get("mp4_allowed") is not False
    ):
        errors.append("Phase 4 work-order identity, scope, or authority is invalid")
    if (
        not upstream_order_path
        or sha256_file(upstream_order_path) != work_order.get("upstream_phase3_work_order_sha256")
        or upstream_order.get("work_order_id") != work_order.get("upstream_phase3_work_order_id")
        or upstream_order.get("phase") != 3
    ):
        errors.append("Phase 4 work order is not bound to the registered Phase 3 work order")
    if (
        phase_manifest.get("work_order_id") != work_order_id
        or input_lock.get("work_order_id") != work_order_id
        or phase_manifest.get("work_order_sha256") != work_order_sha256
        or input_lock.get("work_order_sha256") != work_order_sha256
        or phase_manifest.get("ownership") != phase4_ownership
        or input_lock.get("ownership") != phase4_ownership
        or input_lock.get("locked_by") != phase4_ownership.get("implementation_lead_id")
        or input_lock.get("controller_gate3_snapshot_sha256") != work_order.get("controller_gate3_sha256")
        or input_lock.get("phase3_work_order_id") != work_order.get("upstream_phase3_work_order_id")
        or input_lock.get("phase3_work_order_sha256") != work_order.get("upstream_phase3_work_order_sha256")
        or phase_manifest.get("project_id") != scope.get("project_id")
        or phase_manifest.get("work_order_relative_path") != f"controller/work-orders/{work_order_id}.json"
        or phase_manifest.get("roles") != {
            "implementation_lead": phase4_ownership.get("implementation_lead_id"),
            "asset_agent": phase4_ownership.get("visual_asset_agent_id"),
            "verification_executor": phase4_ownership.get("verification_executor_id"),
            "parity_checker": phase4_ownership.get("parity_acceptance_agent_id"),
        }
    ):
        errors.append("Phase 4 manifest/input lock differs from the controller work order")

    gate_snapshot = safe_relative_path(
        run_dir,
        str(work_order.get("controller_gate3_snapshot_relative_path", "")),
        "controller-owned Gate 3 snapshot",
        errors,
    )
    if (
        not gate_snapshot
        or work_order.get("controller_gate3_snapshot_relative_path")
        != f"controller/work-orders/{work_order_id}.phase-03-gate-report.json"
        or sha256_file(gate_snapshot) != work_order.get("controller_gate3_sha256")
    ):
        errors.append("Controller-owned Gate 3 snapshot differs from the Phase 4 work order")
    else:
        try:
            frozen_gate = load_json(gate_snapshot)
            if (
                frozen_gate.get("phase") != 3
                or frozen_gate.get("verdict") != "PASS"
                or frozen_gate.get("scope_sha256") != phase1_facts.get("scope_sha256")
                or frozen_gate.get("errors")
            ):
                errors.append("Frozen controller Gate 3 snapshot is not a complete PASS")
        except ValueError as exc:
            errors.append(str(exc))

    # Every small upstream input is copied into Phase 4 and bound to its canonical source.
    raw_inputs = input_lock.get("inputs")
    source_records: dict[Path, dict[str, Any]] = {}
    snapshot_paths: set[Path] = set()
    input_labels: set[str] = set()
    if not isinstance(raw_inputs, list):
        errors.append("Phase 4 input lock inputs must be an array")
        raw_inputs = []
    for index, record in enumerate(raw_inputs):
        if not isinstance(record, dict):
            errors.append(f"Phase 4 input record {index} is not an object")
            continue
        try:
            if set(record) != {"label", "source_path", "snapshot_path", "sha256", "size"} or not record.get("label"):
                raise ValueError("input record keys or label differ from the contract")
            label_value = str(record.get("label"))
            if label_value in input_labels:
                raise ValueError("duplicate input label")
            source_value = Path(str(record.get("source_path", ""))).expanduser()
            snapshot_value = Path(str(record.get("snapshot_path", ""))).expanduser()
            if not source_value.is_absolute() or not snapshot_value.is_absolute():
                raise ValueError("source_path and snapshot_path must be absolute")
            source = source_value.resolve()
            snapshot = snapshot_value.resolve()
            source.relative_to(run_dir)
            snapshot.relative_to((phase_dir / "inputs" / "upstream").resolve())
            if source in source_records or snapshot in snapshot_paths:
                raise ValueError("duplicate source or snapshot path")
            if source_value.is_symlink() or snapshot_value.is_symlink() or not source.is_file() or not snapshot.is_file():
                raise ValueError("source or snapshot is missing/symbolic")
            digest = str(record.get("sha256", ""))
            size = record.get("size")
            if (
                not SHA256_RE.fullmatch(digest)
                or sha256_file(source) != digest
                or sha256_file(snapshot) != digest
                or source.stat().st_size != size
                or snapshot.stat().st_size != size
            ):
                raise ValueError("source/snapshot hash or size differs")
            source_records[source] = record
            snapshot_paths.add(snapshot)
            input_labels.add(label_value)
        except (OSError, ValueError) as exc:
            errors.append(f"Invalid Phase 4 input record {index}: {exc}")

    expected_sources: dict[Path, str] = {}
    scope_path = (run_dir / "controller" / "scope.json").resolve()
    if scope_path.is_file():
        expected_sources[scope_path] = str(phase1_facts.get("scope_sha256"))
    if work_order_path:
        expected_sources[work_order_path.resolve()] = str(work_order_sha256)
    if gate_snapshot:
        expected_sources[gate_snapshot.resolve()] = str(work_order.get("controller_gate3_sha256"))
    if upstream_order_path:
        expected_sources[upstream_order_path.resolve()] = str(work_order.get("upstream_phase3_work_order_sha256"))
    for digest_key, relative in STAGE4_INPUT_RELATIVES.items():
        source = safe_relative_path(run_dir, relative, digest_key, errors)
        digest = str(work_order.get(digest_key, ""))
        relative_key = digest_key.removesuffix("_sha256") + "_relative_path"
        if work_order.get(relative_key) != relative:
            errors.append(f"Phase 4 work order has a noncanonical input path: {relative_key}")
        if not SHA256_RE.fullmatch(digest) or not source or sha256_file(source) != digest:
            errors.append(f"Phase 4 work order input changed: {digest_key}")
        elif source:
            expected_sources[source.resolve()] = digest

    henv_records = work_order.get("phase3_henvs")
    henv_by_id: dict[str, dict[str, str]] = {}
    if not isinstance(henv_records, list):
        errors.append("Phase 4 work order lacks phase3_henvs")
        henv_records = []
    for record in henv_records:
        if not isinstance(record, dict):
            errors.append("Phase 4 phase3_henvs contains a non-object record")
            continue
        henv_id = str(record.get("henv_id", ""))
        relative = str(record.get("relative_path", ""))
        digest = str(record.get("sha256", ""))
        if not ID_RE.fullmatch(henv_id) or henv_id in henv_by_id:
            errors.append(f"Unsafe or duplicate Phase 3 HENV-ID in Phase 4 work order: {henv_id!r}")
            continue
        source = safe_relative_path(run_dir, relative, f"Phase 3 HENV {henv_id}", errors)
        if not source or not SHA256_RE.fullmatch(digest) or sha256_file(source) != digest:
            errors.append(f"Frozen Phase 3 HENV changed: {henv_id}")
            continue
        henv_by_id[henv_id] = {"relative_path": relative, "sha256": digest}
        expected_sources[source.resolve()] = digest
    if set(source_records) != set(expected_sources):
        errors.append(
            "Phase 4 small-input snapshots differ from the work order; "
            f"missing={sorted(str(path) for path in set(expected_sources) - set(source_records))[:5]}, "
            f"extra={sorted(str(path) for path in set(source_records) - set(expected_sources))[:5]}"
        )
    for source, digest in expected_sources.items():
        record = source_records.get(source)
        if record and record.get("sha256") != digest:
            errors.append(f"Phase 4 input snapshot binds another digest: {source}")

    input_lock_path = phase_dir / "stage-04-input-lock.json"
    input_lock_sha256 = sha256_file(input_lock_path) if input_lock_path.is_file() else None
    if phase_manifest.get("input_lock_sha256") != input_lock_sha256:
        errors.append("Phase 4 manifest references another input lock")

    # Recheck frozen Phase 4 environment identities and executable contracts.
    env_rows = read_csv_rows(phase_dir / "environments" / "h4env-registry.csv")
    env_index = index_unique_rows(env_rows, "h4env_id", "H4ENV registry", errors)
    required_h4env_value = input_lock.get("required_h4env_ids")
    required_h4env_ids = set(required_h4env_value if isinstance(required_h4env_value, list) else [])
    if not required_h4env_ids or set(env_index) != required_h4env_ids:
        errors.append("Phase 4 H4ENV registry differs from the frozen required H4ENV set")
    required_categories = {
        "TOOLCHAIN", "CLEAN_BUILD", "BUNDLE_CHECK", "SIGNING_CHECK", "DEVICE_CHECK",
        "CLEAN_INSTALL", "SEED_RESET", "NETWORK_PROFILE", "PERMISSION_PROFILE", "LAUNCH",
        "NAVIGATE", "BUSINESS_ASSERT", "SCREENSHOT_CAPTURE", "UITEST_SNAPSHOT_CAPTURE",
    }
    environments: dict[str, dict[str, Any]] = {}
    scope_environments = {
        str(item.get("env_id", "")): item
        for item in scope.get("environments", []) if isinstance(item, dict)
    }
    for h4env_id, row in env_index.items():
        env_path = phase_dir / "environments" / h4env_id / "phase4-environment.json"
        try:
            environment = load_json(env_path)
            environments[h4env_id] = environment
            base_henv_id = str(environment.get("base_henv_id", ""))
            base = henv_by_id.get(base_henv_id)
            source_environment = scope_environments.get(str(environment.get("source_android_env_id", "")), {})
            base_environment = (
                load_json(run_dir / str(base.get("relative_path", ""))) if base else {}
            )
            business_profile_fields = (
                "account_id", "account_role", "seed_data_id", "seed_reset_ref",
                "network_profile", "network_conditions_ref", "network_toggle_available",
                "locale", "theme", "font_scale", "timezone", "permissions_profile", "orientation",
            )
            expected_business_profile = {
                field: source_environment.get(field) for field in business_profile_fields
            }
            emulator = environment.get("emulator") if isinstance(environment.get("emulator"), dict) else {}
            application = (
                environment.get("base_application")
                if isinstance(environment.get("base_application"), dict) else {}
            )
            serial = str(emulator.get("serial", ""))
            bundle = str(application.get("bundle_name", ""))
            selector = environment.get("device_selector_tokens")
            comparison = environment.get("comparison") if isinstance(environment.get("comparison"), dict) else {}
            resolution_match = re.fullmatch(r"(\d+)x(\d+)", str(emulator.get("resolution", "")))
            expected_environment_keys = {
                "h4env_id", "source_android_env_id", "base_henv_id", "device_id",
                "device_serial", "bundle_name", "created_by", "required", "frozen_at",
                "device_selector_tokens", "category_contracts", "comparison", "business_profile",
                "base_henv_sha256", "base_application", "base_toolchain", "emulator",
            }
            if (
                set(environment) != expected_environment_keys
                or row.get("status") != "FROZEN"
                or row.get("required") != "true"
                or row.get("frozen_by") != phase4_ownership.get("implementation_lead_id")
                or row.get("environment_sha256") != sha256_file(env_path)
                or environment.get("h4env_id") != h4env_id
                or row.get("source_android_env_id") != environment.get("source_android_env_id")
                or row.get("base_henv_id") != base_henv_id
                or row.get("device_id") != environment.get("device_id")
                or environment.get("source_android_env_id") not in scope_environments
                or environment.get("created_by") != phase4_ownership.get("implementation_lead_id")
                or environment.get("required") is not True
                or not environment.get("frozen_at")
                or not base
                or environment.get("base_henv_sha256") != base.get("sha256")
                or str(environment.get("emulator", {}).get("device_type", "")).lower() != "emulator"
                or environment.get("device_id") != emulator.get("device_id")
                or environment.get("device_serial") != serial
                or environment.get("bundle_name") != bundle
                or not serial or not bundle
                or not isinstance(selector, list) or not selector or serial not in selector
                or environment.get("business_profile") != expected_business_profile
                or not resolution_match
                or comparison.get("screenshot_width") != int(resolution_match.group(1))
                or comparison.get("screenshot_height") != int(resolution_match.group(2))
                or not isinstance(comparison.get("content_bounds"), list)
                or len(comparison.get("content_bounds", [])) != 4
                or not isinstance(comparison.get("geometry_tolerance_px"), int)
                or comparison.get("geometry_tolerance_px", -1) < 0
                or env_path.stat().st_mode & 0o222
            ):
                errors.append(f"{h4env_id}: frozen environment identity or ownership is invalid")
            contracts = environment.get("category_contracts")
            if not isinstance(contracts, dict) or set(contracts) != required_categories:
                errors.append(f"{h4env_id}: executable contract category coverage differs")
            else:
                for category, contract in contracts.items():
                    executable = Path(str(contract.get("resolved_executable", ""))).expanduser()
                    synthetic_parts = {part.lower() for part in executable.parts}
                    if (
                        os.environ.get("ANDROID_HARMONY_TEST_FIXTURES") != "1"
                        and ("tests" in synthetic_parts or "fake_harmony.py" in str(executable).lower())
                    ):
                        errors.append(
                            f"{h4env_id}: synthetic test executable cannot produce formal evidence: {category}"
                        )
                    required_tokens = contract.get("required_argv_tokens")
                    success_tokens = contract.get("success_output_contains")
                    error_tokens = contract.get("error_output_contains")
                    if (
                        set(contract) != {
                            "resolved_executable", "executable_sha256", "required_argv_tokens",
                            "success_output_contains", "error_output_contains",
                        }
                        or not executable.is_absolute()
                        or str(executable.resolve()) != str(executable)
                        or not executable.is_file()
                        or not os.access(executable, os.X_OK)
                        or sha256_file(executable) != contract.get("executable_sha256")
                        or any(
                            not isinstance(values, list) or not values
                            or any(not isinstance(item, str) or not item for item in values)
                            for values in (required_tokens, success_tokens, error_tokens)
                        )
                        or (category in {
                            "BUNDLE_CHECK", "DEVICE_CHECK", "CLEAN_INSTALL", "SEED_RESET",
                            "NETWORK_PROFILE", "PERMISSION_PROFILE", "LAUNCH", "NAVIGATE",
                            "BUSINESS_ASSERT", "SCREENSHOT_CAPTURE", "UITEST_SNAPSHOT_CAPTURE",
                        } and serial not in required_tokens)
                        or (category in {
                            "BUNDLE_CHECK", "SIGNING_CHECK", "CLEAN_INSTALL", "SEED_RESET",
                            "PERMISSION_PROFILE", "LAUNCH", "NAVIGATE", "BUSINESS_ASSERT",
                            "SCREENSHOT_CAPTURE", "UITEST_SNAPSHOT_CAPTURE",
                        } and bundle not in required_tokens)
                    ):
                        errors.append(f"{h4env_id}: invalid frozen executable contract: {category}")
                base_contracts = (
                    base_environment.get("toolchain", {}).get("category_contracts", {})
                    if isinstance(base_environment.get("toolchain"), dict) else {}
                )
                base_category_map = {
                    "TOOLCHAIN": "TOOLCHAIN", "CLEAN_BUILD": "CLEAN_BUILD",
                    "BUNDLE_CHECK": "BUNDLE_CHECK", "SIGNING_CHECK": "SIGNING_CHECK",
                    "DEVICE_CHECK": "DEVICE", "CLEAN_INSTALL": "INSTALL", "LAUNCH": "LAUNCH",
                    "SCREENSHOT_CAPTURE": "SCREENSHOT_CAPTURE",
                }
                for category, base_category in base_category_map.items():
                    phase4_contract = contracts.get(category, {})
                    base_contract = base_contracts.get(base_category, {})
                    if (
                        phase4_contract.get("resolved_executable") != base_contract.get("resolved_executable")
                        or phase4_contract.get("executable_sha256") != base_contract.get("executable_sha256")
                    ):
                        errors.append(f"{h4env_id}: {category} executable differs from base HENV")
        except (ValueError, OSError) as exc:
            errors.append(f"{h4env_id}: {exc}")

    locked_h4envs = input_lock.get("h4envs")
    if not isinstance(locked_h4envs, list):
        errors.append("Phase 4 input lock h4envs must be an array")
        locked_h4envs = []
    locked_h4env_by_id: dict[str, dict[str, Any]] = {}
    h4env_record_keys = {
        "h4env_id", "source_android_env_id", "base_henv_id", "device_id", "relative_path", "sha256",
    }
    for record in locked_h4envs:
        if not isinstance(record, dict):
            errors.append("Phase 4 h4envs contains a non-object record")
            continue
        h4env_id = str(record.get("h4env_id", ""))
        environment = environments.get(h4env_id)
        relative = f"environments/{h4env_id}/phase4-environment.json"
        env_path = phase_dir / relative
        if (
            set(record) != h4env_record_keys
            or not environment
            or h4env_id in locked_h4env_by_id
            or record.get("relative_path") != relative
            or record.get("source_android_env_id") != environment.get("source_android_env_id")
            or record.get("base_henv_id") != environment.get("base_henv_id")
            or record.get("device_id") != environment.get("device_id")
            or not env_path.is_file()
            or record.get("sha256") != sha256_file(env_path)
        ):
            errors.append(f"Phase 4 input-lock H4ENV record differs: {h4env_id!r}")
            continue
        locked_h4env_by_id[h4env_id] = record
    if set(locked_h4env_by_id) != set(environments):
        errors.append("Phase 4 input-lock H4ENV records do not exactly cover frozen environments")

    project = phase_dir / "harmony-project"
    source_snapshot_sha256, _ = phase4_project_snapshot(project, errors)

    # Final HBUILD packages must be one-to-one with the required H4ENV set.
    build_ids_value = stage_report.get("build_ids")
    build_ids = build_ids_value if isinstance(build_ids_value, list) else []
    if len(build_ids) != len(set(build_ids)) or any(not isinstance(item, str) or not ID_RE.fullmatch(item) for item in build_ids):
        errors.append("Phase 4 report has unsafe or duplicate final HBUILD-IDs")
        build_ids = []
    builds: dict[str, dict[str, Any]] = {}
    build_by_env: dict[str, str] = {}
    artifact_hashes: list[str] = []
    for build_id in build_ids:
        build_dir = phase_dir / "builds" / build_id
        verify_sealed_package(build_dir, build_id, "PASS", f"HBUILD {build_id}", errors)
        try:
            metadata = load_json(build_dir / "metadata.json")
            artifact_manifest = load_json(build_dir / "artifact-manifest.json")
            builds[build_id] = metadata
            h4env_id = str(metadata.get("h4env_id", ""))
            environment = environments.get(h4env_id, {})
            if h4env_id in build_by_env:
                errors.append(f"More than one final HBUILD is selected for {h4env_id}")
            build_by_env[h4env_id] = build_id
            if (
                metadata.get("hbuild_id") != build_id
                or metadata.get("status") != "PASS"
                or metadata.get("executed_by") != phase4_ownership.get("verification_executor_id")
                or metadata.get("input_lock_sha256") != input_lock_sha256
                or metadata.get("source_snapshot_sha256") != source_snapshot_sha256
                or h4env_id not in environments
                or not metadata.get("created_at")
                or metadata.get("bundle_name") != environment.get("base_application", {}).get("bundle_name")
                or metadata.get("device_id") != environment.get("device_id")
                or metadata.get("device_serial") != environment.get("emulator", {}).get("serial")
                or metadata.get("environment_sha256")
                != sha256_file(phase_dir / "environments" / h4env_id / "phase4-environment.json")
            ):
                errors.append(f"{build_id}: build metadata, executor, or snapshot is invalid")
            if environment:
                validate_phase4_commands(
                    build_dir,
                    metadata.get("commands"),
                    environment,
                    ["TOOLCHAIN", "CLEAN_BUILD", "BUNDLE_CHECK", "SIGNING_CHECK"],
                    f"HBUILD {build_id}",
                    errors,
                )
            artifacts = artifact_manifest.get("artifacts") if isinstance(artifact_manifest.get("artifacts"), list) else []
            primary = metadata.get("primary_artifact") if isinstance(metadata.get("primary_artifact"), dict) else {}
            if metadata.get("artifact_count") != 1 or len(artifacts) != 1 or primary != artifacts[0]:
                errors.append(f"{build_id}: artifact manifest/count/primary artifact differs")
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    errors.append(f"{build_id}: artifact entry is not an object")
                    continue
                sealed = safe_relative_path(
                    build_dir, str(artifact.get("sealed_relative_path", "")), f"{build_id} artifact", errors
                )
                digest = str(artifact.get("sha256", ""))
                if (
                    not sealed
                    or not sealed.is_file()
                    or not SHA256_RE.fullmatch(digest)
                    or sha256_file(sealed) != digest
                    or sealed.stat().st_size != artifact.get("size")
                    or not zipfile.is_zipfile(sealed)
                ):
                    errors.append(f"{build_id}: sealed HAP artifact is invalid")
                else:
                    try:
                        with zipfile.ZipFile(sealed) as archive:
                            members = archive.infolist()
                            if (
                                not members
                                or len(members) > 100_000
                                or sum(item.file_size for item in members) > 2 * 1024 * 1024 * 1024
                                or archive.testzip() is not None
                                or any(
                                    Path(item.filename).is_absolute() or ".." in Path(item.filename).parts
                                    for item in members
                                )
                                or not any(Path(item.filename).name in {"module.json", "config.json"} for item in members)
                            ):
                                raise ValueError("invalid HAP structure")
                    except (OSError, ValueError, zipfile.BadZipFile) as exc:
                        errors.append(f"{build_id}: sealed HAP structure is invalid: {exc}")
                    artifact_hashes.append(digest)
            source_snapshot = load_json(build_dir / "source-snapshot.json")
            if source_snapshot.get("snapshot_sha256") != source_snapshot_sha256:
                errors.append(f"{build_id}: source-snapshot.json differs from the current project")
            clean_commands = [
                item for item in metadata.get("commands", [])
                if isinstance(item, dict) and item.get("category") == "CLEAN_BUILD"
            ]
            if len(clean_commands) != 1:
                errors.append(f"{build_id}: CLEAN_BUILD command is not unique")
            elif artifacts:
                clean_state = clean_commands[0].get("artifact_state_after_clean_build")
                if (
                    not isinstance(clean_state, dict)
                    or clean_state.get("sha256") != artifacts[0].get("sha256")
                    or clean_state.get("size") != artifacts[0].get("size")
                    or artifacts[0].get("produced_by_command_id") != clean_commands[0].get("command_id")
                ):
                    errors.append(f"{build_id}: CLEAN_BUILD does not bind the final HAP")
        except (ValueError, OSError) as exc:
            errors.append(f"{build_id}: {exc}")
    if set(build_by_env) != required_h4env_ids:
        errors.append("Final HBUILD coverage differs from the frozen H4ENV set")

    # Android source evidence is copied once into the sealed Phase 4 input archive.
    phase2_inventory_rows = [
        row for row in read_csv_rows(run_dir / "phase-02-android-inventory" / "inventory.csv")
        if row.get("row_status") != "SUPERSEDED"
    ]
    phase2_inventory = index_unique_rows(
        phase2_inventory_rows, "inventory_id", "active Phase 2 inventory", errors
    )
    locked_inventory_ids = input_lock.get("phase2_inventory_ids")
    if not isinstance(locked_inventory_ids, list) or set(locked_inventory_ids) != set(phase2_inventory):
        errors.append("Phase 4 input lock inventory IDs differ from active Phase 2 inventory")
    phase2_asset_rows = read_csv_rows(run_dir / "phase-02-android-inventory" / "asset-inventory.csv")
    phase2_assets = index_unique_rows(phase2_asset_rows, "asset_id", "Phase 2 asset inventory", errors)
    locked_asset_ids = input_lock.get("phase2_asset_ids")
    if not isinstance(locked_asset_ids, list) or set(locked_asset_ids) != set(phase2_assets):
        errors.append("Phase 4 input lock Asset-IDs differ from Phase 2")
    locked_asset_files = input_lock.get("phase2_asset_files")
    if not isinstance(locked_asset_files, list):
        errors.append("Phase 4 input lock lacks phase2_asset_files")
        locked_asset_files = []
    locked_assets_by_id: dict[str, dict[str, Any]] = {}
    locked_asset_snapshots: set[Path] = set()
    for record in locked_asset_files:
        if not isinstance(record, dict):
            errors.append("Phase 4 phase2_asset_files contains a non-object record")
            continue
        asset_id = str(record.get("asset_id", ""))
        source_row = phase2_assets.get(asset_id)
        if (
            set(record) != {"asset_id", "source_path", "snapshot_path", "sha256", "size"}
            or not source_row
            or asset_id in locked_assets_by_id
        ):
            errors.append(f"Phase 4 asset input has an unknown or duplicate Asset-ID: {asset_id!r}")
            continue
        try:
            source = Path(str(record.get("source_path", ""))).expanduser().resolve()
            snapshot = Path(str(record.get("snapshot_path", ""))).expanduser().resolve()
            expected_source = (
                run_dir / "phase-02-android-inventory" / str(source_row.get("archive_path", ""))
            ).resolve()
            expected_snapshot = (
                phase_dir / "inputs" / "phase2-assets" / "files" / asset_id / expected_source.name
            ).resolve()
            source.relative_to((run_dir / "phase-02-android-inventory" / "asset-package").resolve())
            snapshot.relative_to((phase_dir / "inputs" / "phase2-assets").resolve())
            digest = str(record.get("sha256", ""))
            if (
                source != expected_source
                or snapshot != expected_snapshot
                or snapshot in locked_asset_snapshots
                or not source.is_file()
                or not snapshot.is_file()
                or source.is_symlink()
                or snapshot.is_symlink()
                or digest != source_row.get("sha256")
                or sha256_file(source) != digest
                or sha256_file(snapshot) != digest
                or source.stat().st_size != record.get("size")
                or snapshot.stat().st_size != record.get("size")
                or snapshot.stat().st_mode & 0o222
            ):
                raise ValueError("source/snapshot path, hash, or size differs")
            locked_assets_by_id[asset_id] = record
            locked_asset_snapshots.add(snapshot)
        except (OSError, ValueError) as exc:
            errors.append(f"Phase 4 asset input {asset_id}: {exc}")
    if set(locked_assets_by_id) != set(phase2_assets):
        errors.append("Phase 4 asset snapshots do not exactly cover Phase 2 asset inventory")
    phase2_evidence_rows = read_csv_rows(run_dir / "phase-02-android-inventory" / "evidence-index.csv")
    phase2_evidence = index_unique_rows(phase2_evidence_rows, "evidence_id", "Phase 2 evidence index", errors)
    expected_android_evidence_ids = {row.get("evidence_id", "") for row in phase2_inventory.values()}
    package_record_values = input_lock.get("android_evidence")
    if not isinstance(package_record_values, list):
        errors.append("Phase 4 input lock android_evidence must be an array")
        package_record_values = []
    package_records: dict[str, dict[str, Any]] = {}
    android_record_keys = {
        "evidence_id", "inventory_id", "source_path", "snapshot_path", "manifest_sha256",
        "metadata_sha256", "screenshot_sha256", "layout_sha256", "sha256", "size", "file_count",
    }
    for value in package_record_values:
        if not isinstance(value, dict):
            errors.append("Phase 4 android_evidence contains a non-object record")
            continue
        evidence_id = str(value.get("evidence_id", ""))
        if set(value) != android_record_keys or not ID_RE.fullmatch(evidence_id) or evidence_id in package_records:
            errors.append(f"Invalid or duplicate Phase 4 Android evidence record: {evidence_id!r}")
            continue
        package_records[evidence_id] = value
    if set(package_records) != expected_android_evidence_ids:
        errors.append("Phase 4 Android evidence archive does not exactly cover active inventory")
    android_copies: dict[str, Path] = {}
    for evidence_id in sorted(expected_android_evidence_ids):
        record = package_records.get(evidence_id)
        index_row = phase2_evidence.get(evidence_id)
        if not isinstance(record, dict) or not index_row:
            continue
        try:
            source = Path(str(record.get("source_path", ""))).expanduser().resolve()
            snapshot = Path(str(record.get("snapshot_path", ""))).expanduser().resolve()
            expected_source = (
                run_dir / "phase-02-android-inventory" / str(index_row.get("relative_path", ""))
            ).resolve()
            expected_snapshot = (phase_dir / "inputs" / "android-evidence" / evidence_id).resolve()
            source.relative_to((run_dir / "phase-02-android-inventory").resolve())
            snapshot.relative_to((phase_dir / "inputs" / "android-evidence").resolve())
            if source != expected_source or snapshot != expected_snapshot or not source.is_dir() or not snapshot.is_dir():
                raise ValueError("source/snapshot evidence path is noncanonical")
            source_manifest = verify_exact_manifest(
                source, "manifest.sha256", {"manifest.sha256", "COMMITTED"},
                f"Android evidence source {evidence_id}", errors,
            )
            snapshot_manifest = verify_exact_manifest(
                snapshot, "manifest.sha256", {"manifest.sha256", "COMMITTED"},
                f"Android evidence snapshot {evidence_id}", errors,
            )
            if source_manifest != snapshot_manifest:
                errors.append(f"Android evidence snapshot differs from source: {evidence_id}")
            expected_hashes = {
                "manifest_sha256": sha256_file(snapshot / "manifest.sha256"),
                "metadata_sha256": sha256_file(snapshot / "metadata.json"),
                "screenshot_sha256": sha256_file(snapshot / "screenshot.png"),
                "layout_sha256": sha256_file(snapshot / "layout.json"),
            }
            package_sha256, package_size, package_file_count = directory_snapshot_facts(snapshot)
            source_sha256, source_size, source_file_count = directory_snapshot_facts(source)
            android_metadata = load_json(snapshot / "metadata.json")
            try:
                android_layout = json.loads(
                    (snapshot / "layout.json").read_text(encoding="utf-8")
                )
            except json.JSONDecodeError as exc:
                raise ValueError(f"Android layout JSON is invalid: {exc}") from exc
            validate_complete_png(snapshot / "screenshot.png")
            if (
                record.get("inventory_id") != index_row.get("inventory_id")
                or android_metadata.get("evidence_id") != evidence_id
                or android_metadata.get("inventory_id") != index_row.get("inventory_id")
                or android_metadata.get("status") != "SEALED"
                or not android_layout
                or not (snapshot / "steps.md").is_file()
                or any(record.get(key) != value for key, value in expected_hashes.items())
                or record.get("sha256") != package_sha256
                or record.get("size") != package_size
                or record.get("file_count") != package_file_count
                or (package_sha256, package_size, package_file_count)
                != (source_sha256, source_size, source_file_count)
            ):
                errors.append(f"Android evidence input-lock hashes differ: {evidence_id}")
            marker = snapshot / "COMMITTED"
            if not marker.is_file() or marker.read_text(encoding="utf-8").strip() != evidence_id:
                errors.append(f"Android evidence snapshot is not COMMITTED: {evidence_id}")
            if any(path.stat().st_mode & 0o222 for path in (snapshot, *snapshot.rglob("*"))):
                errors.append(f"Android evidence snapshot is not read-only: {evidence_id}")
            android_copies[evidence_id] = snapshot
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            errors.append(f"Android evidence {evidence_id}: {exc}")

    phase3_snapshot_path = run_dir / STAGE4_INPUT_RELATIVES["phase3_scaffold_snapshot_sha256"]
    try:
        phase3_snapshot = load_json(phase3_snapshot_path)
        if input_lock.get("phase3_source_snapshot_sha256") != phase3_snapshot.get("snapshot_sha256"):
            errors.append("Phase 4 input lock cites another Phase 3 source snapshot")
        raw_phase3_entries = phase3_snapshot.get("entries")
        expected_initial_entries: list[dict[str, Any]] = []
        if not isinstance(raw_phase3_entries, list):
            raise ValueError("Phase 3 snapshot entries are missing")
        for entry in raw_phase3_entries:
            if not isinstance(entry, dict):
                raise ValueError("Phase 3 snapshot contains a malformed entry")
            path_value = str(entry.get("path", ""))
            prefix = "harmony-project/"
            if not path_value.startswith(prefix):
                continue
            expected_initial_entries.append({
                "path": path_value.removeprefix(prefix),
                "sha256": entry.get("sha256"),
                "size": entry.get("size"),
            })
        phase3_initial_assets = index_unique_rows(
            read_csv_rows(run_dir / "phase-03-harmony-scaffold" / "asset-registry.csv"),
            "asset_id", "Phase 3 asset registry for initial snapshot", errors,
        )
        for asset_id, placement in phase3_initial_assets.items():
            if placement.get("planned_mode") != "DIRECT_COPY":
                continue
            source_asset = phase2_assets.get(asset_id, {})
            locked_asset = locked_assets_by_id.get(asset_id, {})
            snapshot_path = Path(str(locked_asset.get("snapshot_path", ""))).expanduser()
            if not snapshot_path.is_file():
                errors.append(f"Initial DIRECT_COPY asset snapshot is missing: {asset_id}")
                continue
            direct_entry = {
                "path": str(placement.get("target_path", "")),
                "sha256": source_asset.get("sha256"),
                "size": snapshot_path.stat().st_size,
            }
            existing_direct = next(
                (item for item in expected_initial_entries if item.get("path") == direct_entry["path"]),
                None,
            )
            if existing_direct and existing_direct != direct_entry:
                errors.append(f"Phase 3 project conflicts with DIRECT_COPY asset: {asset_id}")
            elif not existing_direct:
                expected_initial_entries.append(direct_entry)
        generation_manifest_value = load_json(
            phase_dir / "ui-test-snapshot-generation-manifest.json"
        )
        for generated in generation_manifest_value.get("generated_files", []):
            if not isinstance(generated, dict):
                errors.append("UiTest generation manifest contains a non-object file record")
                continue
            generated_path = safe_relative_path(
                project, str(generated.get("relative_path", "")),
                "generated UiTest source", errors,
            )
            if not generated_path or not generated_path.is_file():
                continue
            expected_initial_entries.append({
                "path": generated_path.relative_to(project).as_posix(),
                "sha256": sha256_file(generated_path),
                "size": generated_path.stat().st_size,
            })
        expected_initial_entries.sort(key=lambda item: str(item.get("path", "")))
        initial_snapshot = load_json(phase_dir / "initial-project-snapshot.json")
        initial_canonical = json.dumps(
            expected_initial_entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        expected_initial_sha = hashlib.sha256(initial_canonical.encode("utf-8")).hexdigest()
        if (
            set(initial_snapshot) != {"entry_count", "entries", "snapshot_sha256"}
            or initial_snapshot.get("entry_count") != len(expected_initial_entries)
            or initial_snapshot.get("entries") != expected_initial_entries
            or initial_snapshot.get("snapshot_sha256") != expected_initial_sha
            or phase_manifest.get("initial_project_snapshot_sha256") != expected_initial_sha
        ):
            actual_entries = initial_snapshot.get("entries")
            expected_by_path = {
                str(item.get("path", "")): item for item in expected_initial_entries
            }
            actual_by_path = {
                str(item.get("path", "")): item for item in actual_entries
            } if isinstance(actual_entries, list) else {}
            differing_paths = sorted(
                path for path in set(expected_by_path) | set(actual_by_path)
                if expected_by_path.get(path) != actual_by_path.get(path)
            )
            errors.append(
                "Phase 4 initial project snapshot differs from accepted Phase 3 source; "
                f"expected={expected_initial_sha}/{len(expected_initial_entries)}, "
                f"actual={initial_snapshot.get('snapshot_sha256')}/{initial_snapshot.get('entry_count')}, "
                f"differing_paths={differing_paths[:10]}"
            )
    except ValueError as exc:
        errors.append(str(exc))

    # Every active Android state gets one accepted parity row on each mapped H4ENV.
    included_features = set(scope.get("migration_scope", {}).get("included_features", []))
    source_rows = {
        inventory_id: row for inventory_id, row in phase2_inventory.items()
        if row.get("feature_id") in included_features
    }
    expected_pairs: set[tuple[str, str]] = set()
    for inventory_id, source in source_rows.items():
        matched = [
            h4env_id for h4env_id, environment in environments.items()
            if environment.get("source_android_env_id") == source.get("env_id")
        ]
        if not matched:
            errors.append(f"No required H4ENV maps active inventory row {inventory_id}")
        expected_pairs.update((inventory_id, h4env_id) for h4env_id in matched)
    parity_rows = read_csv_rows(phase_dir / "parity-map.csv")
    parity = index_unique_rows(parity_rows, "parity_id", "Phase 4 parity map", errors)
    actual_pairs = {(row.get("inventory_id", ""), row.get("h4env_id", "")) for row in parity.values()}
    if actual_pairs != expected_pairs or len(actual_pairs) != len(parity):
        errors.append(
            "Phase 4 parity coverage differs from active Android states × required H4ENV; "
            f"missing={sorted(expected_pairs - actual_pairs)[:5]}, extra={sorted(actual_pairs - expected_pairs)[:5]}"
        )

    # Recompute every feature work order. Gate 4 must not trust self-declared implementation rows.
    phase3_dir = run_dir / "phase-03-harmony-scaffold"
    architecture = index_unique_rows(
        read_csv_rows(phase3_dir / "architecture-map.csv"),
        "source_row_key", "Phase 3 architecture map", errors,
    )
    # Recompute the immutable observable contract from Phase 2 and Phase 3.
    # Gate 4 must not trust a model-edited contract or its self-updated hash files.
    try:
        contract_doc = load_json(phase_dir / "migration-unit-contracts.json")
        units = contract_doc.get("units")
        if contract_doc.get("schema_version") != 1 or not isinstance(units, list):
            raise ValueError("Phase 4 migration-unit contract schema differs")
        units_by_parity = {
            str(row.get("parity_id", "")): row for row in units if isinstance(row, dict)
        }
        if len(units_by_parity) != len(units) or set(units_by_parity) != set(parity):
            raise ValueError("Phase 4 migration-unit contracts do not exactly cover parity rows")
        page_doc = load_json(
            run_dir / "phase-02-android-inventory" / "static-analysis" / "pages.json"
        )
        component_doc = load_json(
            run_dir / "phase-02-android-inventory" / "static-analysis" / "components.json"
        )
        event_doc = load_json(
            run_dir / "phase-02-android-inventory" / "static-analysis" / "events.json"
        )
        transition_doc = load_json(
            run_dir / "phase-02-android-inventory" / "static-analysis" / "transitions.json"
        )
        observation_doc = load_json(
            run_dir / "phase-02-android-inventory" / "runtime-observations.json"
        )
        obligation_doc = load_json(phase3_dir / "advanced-obligations.json")
        static_pages = page_doc.get("pages", [])
        static_components = component_doc.get("components", [])
        static_events = event_doc.get("events", [])
        static_transitions = transition_doc.get("transitions", [])
        runtime_observations = observation_doc.get("observations", [])
        obligations = obligation_doc.get("obligations", [])
        if not all(isinstance(rows, list) and all(isinstance(row, dict) for row in rows) for rows in (
            static_pages, static_components, static_events, static_transitions,
            runtime_observations, obligations
        )):
            raise ValueError("Frozen Phase 2/3 semantic inputs are malformed")
        inventory_by_evidence = {
            str(row.get("evidence_id", "")): row for row in source_rows.values()
        }
        observed_by_state: dict[tuple[str, str, str], dict[str, set[str]]] = {}
        bucket_by_type = {
            "COMPONENT": "components", "EVENT": "events", "TRANSITION": "transitions"
        }
        for observation in runtime_observations:
            bucket = bucket_by_type.get(str(observation.get("subject_type", "")))
            if not bucket:
                continue
            observed_source = inventory_by_evidence.get(
                str(observation.get("after_evidence_id", ""))
            )
            if not observed_source:
                raise ValueError("Runtime observation is not bound to active state evidence")
            if (
                observation.get("page_id") != observed_source.get("page_id")
                or observation.get("env_id") != observed_source.get("env_id")
            ):
                raise ValueError("Runtime observation differs from its state evidence")
            observed_key = (
                str(observed_source.get("page_id", "")),
                str(observed_source.get("state_id", "")),
                str(observed_source.get("env_id", "")),
            )
            observed_by_state.setdefault(
                observed_key, {"components": set(), "events": set(), "transitions": set()}
            )[bucket].add(str(observation.get("subject_id", "")))
        pages_by_id = {str(row.get("page_id", "")): row for row in static_pages}
        surfaces = index_unique_rows(
            read_csv_rows(phase3_dir / "surface-registry.csv"),
            "surface_shell_id", "Phase 3 surface registry", errors,
        )
        contract_sha = sha256_file(phase_dir / "migration-unit-contracts.json")
        if (
            input_lock.get("migration_unit_contracts_sha256") != contract_sha
            or phase_manifest.get("migration_unit_contracts_sha256") != contract_sha
        ):
            raise ValueError("Phase 4 migration-unit contract hash binding differs")
        for parity_id, parity_row in parity.items():
            source = source_rows.get(str(parity_row.get("inventory_id", "")))
            if not source:
                raise ValueError(f"Unknown inventory in migration unit: {parity_id}")
            mapping = architecture.get(phase4_source_row_key(source), {})
            mapping_type = str(mapping.get("mapping_type", ""))
            target_id = str(
                mapping.get("route_id", "") if mapping_type == "ROUTE_PAGE"
                else mapping.get("surface_shell_id", "")
            )
            page_id = str(source.get("page_id", ""))
            page = pages_by_id.get(page_id)
            if not page:
                raise ValueError(f"Static page missing for migration unit: {page_id}")
            kinds = {str(value).upper() for value in page.get("kinds", [])}
            if any("BOTTOMSHEET" in kind or "BOTTOM_SHEET" in kind for kind in kinds):
                carrier = "SHEET"
            elif any("DIALOG" in kind for kind in kinds):
                carrier = "DIALOG"
            elif any("POPUP" in kind for kind in kinds):
                carrier = "POPUP"
            elif any("WIDGET" in kind for kind in kinds):
                carrier = "EMBEDDED_SURFACE"
            elif any("ACTIVITY" in kind for kind in kinds) or mapping_type == "ROUTE_PAGE":
                carrier = "PAGE"
            else:
                carrier = "EMBEDDED_SURFACE"
            surface_kind = str(surfaces.get(str(mapping.get("surface_shell_id", "")), {}).get(
                "surface_kind", ""
            )).upper().replace("-", "_")
            if mapping_type == "ROUTE_PAGE":
                actual_carrier = "PAGE"
            elif "BOTTOM" in surface_kind and "SHEET" in surface_kind:
                actual_carrier = "SHEET"
            elif "DIALOG" in surface_kind:
                actual_carrier = "DIALOG"
            elif "POPUP" in surface_kind or "MENU" in surface_kind:
                actual_carrier = "POPUP"
            else:
                actual_carrier = "EMBEDDED_SURFACE"
            unit = units_by_parity[parity_id]
            applicable = sorted(
                [row for row in obligations
                 if source.get("feature_id") in row.get("candidate_feature_ids", [])
                 and (not str(row.get("page_id", "")) or row.get("page_id") == page_id)],
                key=lambda row: str(row.get("subject_id", "")),
            )
            state_subjects = observed_by_state.get(
                (page_id, str(source.get("state_id", "")), str(source.get("env_id", ""))),
                {"components": set(), "events": set(), "transitions": set()},
            )
            expected_fields = {
                "migration_unit_id": "MUNIT-" + hashlib.sha256(
                    parity_id.encode("utf-8")
                ).hexdigest()[:20].upper(),
                "parity_id": parity_id,
                "inventory_id": source.get("inventory_id"),
                "feature_id": source.get("feature_id"),
                "page_id": page_id,
                "state_id": source.get("state_id"),
                "h4env_id": parity_row.get("h4env_id"),
                "android_entry_condition": source.get("entry_condition"),
                "android_action_summary": source.get("action_summary"),
                "android_expected_observable": source.get("expected_observable"),
                "expected_carrier": carrier,
                "scaffold_carrier": actual_carrier,
                "target_kind": mapping_type,
                "target_id": target_id,
                "page_component_ids": sorted({str(row.get("component_id", "")) for row in static_components if row.get("page_id") == page_id}),
                "page_event_ids": sorted({str(row.get("event_id", "")) for row in static_events if row.get("page_id") == page_id}),
                "page_transition_ids": sorted({str(row.get("transition_id", "")) for row in static_transitions if row.get("source_page_id") == page_id}),
                "required_component_ids": sorted(state_subjects["components"]),
                "required_event_ids": sorted(state_subjects["events"]),
                "required_transition_ids": sorted(state_subjects["transitions"]),
                "state_binding_basis": "PHASE2_AFTER_EVIDENCE",
                "required_obligation_ids": [str(row.get("subject_id", "")) for row in applicable],
                "required_obligation_types": {
                    str(row.get("subject_id", "")): str(row.get("subject_type", ""))
                    for row in applicable
                },
                "required_business_rule_ids": sorted(phase4_json_string_list(source.get("business_rule_refs", "[]"), f"{parity_id}.business_rule_refs", errors)),
                "required_data_dependency_ids": sorted(phase4_json_string_list(source.get("data_dependency_refs", "[]"), f"{parity_id}.data_dependency_refs", errors)),
                "required_system_capability_ids": sorted(phase4_json_string_list(source.get("system_capability_refs", "[]"), f"{parity_id}.system_capability_refs", errors)),
                "required_third_party_dependency_ids": sorted(phase4_json_string_list(source.get("third_party_dependency_refs", "[]"), f"{parity_id}.third_party_dependency_refs", errors)),
                "simplification_policy": "FORBIDDEN",
                "native_optimization_policy": "INTERNAL_ONLY_UNLESS_APPROVED",
                "max_automatic_repair_attempts": 2,
            }
            if any(unit.get(field) != value for field, value in expected_fields.items()):
                raise ValueError(f"Migration-unit observable contract differs: {parity_id}")
            if carrier != actual_carrier:
                raise ValueError(f"Harmony carrier changes Android semantics: {parity_id}")
            expected_locators = {
                str(row.get("component_id", "")): {
                    "resource_id": str(row.get("resource_id", "")),
                    "text": str(row.get("text", "")),
                    "type": str(row.get("type", "")),
                }
                for row in static_components if row.get("page_id") == page_id
            }
            if unit.get("component_locators") != expected_locators:
                raise ValueError(f"Migration-unit component locators differ: {parity_id}")
            if set(unit) != set(expected_fields) | {"component_locators"}:
                raise ValueError(f"Migration-unit contract fields differ: {parity_id}")
    except ValueError as exc:
        errors.append(str(exc))
    modules = index_unique_rows(
        read_csv_rows(phase3_dir / "module-registry.csv"),
        "harmony_module_id", "Phase 3 module registry", errors,
    )
    phase3_capabilities_for_orders = index_unique_rows(
        read_csv_rows(phase3_dir / "capability-contracts.csv"),
        "capability_requirement_id", "Phase 3 capability contracts", errors,
    )
    page_order_mode = (phase_dir / "page-work-order-registry.csv").is_file()
    # P4 分层验证：页合同 tier 映射（CORE=全证据深验 / LITE=轻证）；
    # feature 模式（无页合同）与缺省键一律 CORE（向后兼容）。
    page_verification_tier: dict[str, str] = {}
    feature_registry_rows = (
        [] if page_order_mode
        else read_csv_rows(phase_dir / "feature-work-order-registry.csv")
    )
    feature_registry = index_unique_rows(
        feature_registry_rows, "work_order_id", "Phase 4 feature work-order registry", errors,
    )
    registry_by_feature: dict[str, list[dict[str, str]]] = {}
    for row in feature_registry_rows:
        if row.get("status") == "ISSUED":
            registry_by_feature.setdefault(row.get("feature_id", ""), []).append(row)
    if not page_order_mode and (set(registry_by_feature) != included_features or any(
        len(rows) != 1 for rows in registry_by_feature.values()
    )):
        errors.append("Phase 4 must have exactly one active feature work order per included feature")
    feature_order_keys = {
        "schema_version", "work_order_id", "run_id", "phase", "feature_id", "status",
        "issued_at", "issued_by", "phase4_manifest_sha256", "stage04_input_lock_sha256",
        "ownership", "visual_asset_agent_id", "source_inventory_ids", "parity_ids",
        "harmony_module_ids", "targets", "required_h4env_ids", "asset_ids",
        "capability_requirement_ids", "capability_contract_ids", "exclusive_code_paths",
        "completion_conditions",
    }
    feature_role_keys = {
        "feature_owner_id", "ui_agent_id", "business_data_agent_id",
        "native_capability_agent_id",
    }
    feature_orders: dict[str, dict[str, Any]] = {}
    feature_actor_ids: dict[str, set[str]] = {}
    feature_exclusive_paths: dict[str, list[Path]] = {}
    all_exclusive_paths: list[tuple[Path, str]] = []
    capability_order_owners: dict[str, str] = {}
    phase4_manifest_sha256 = sha256_file(phase_dir / "phase-manifest.json")
    if page_order_mode:
        try:
            validate_order_coverage(phase_dir)
            page_contracts = _page_contracts(phase_dir)
            page_verification_tier = {
                str(page_id): str(contract.get("verification_tier") or "CORE").strip().upper()
                for page_id, (contract, _, _) in page_contracts.items()
            }
            governed_orders = _registered_orders(phase_dir, page_contracts)
            page_orders = {
                str(order["page_id"]): order
                for order in governed_orders if order.get("page_id")
            }
            capability_order_owners = {
                str(order["capability_id"]): str(order["owner_id"])
                for order in governed_orders if order.get("capability_id")
            }
            page_ledger = index_unique_rows(
                read_csv_rows(phase_dir / "page-implementation-ledger.csv"),
                "page_id", "Phase 4 page implementation ledger", errors,
            )
            if set(page_ledger) != set(page_contracts) or set(page_orders) != set(page_contracts):
                errors.append("Phase 4 page work-order/ledger/contract coverage differs")
            seen_page_owners: set[str] = set()
            seen_ui_agents: set[str] = set()
            seen_task_ids: set[str] = set()
            for page_id, (contract, _, contract_sha256) in page_contracts.items():
                order = page_orders.get(page_id, {})
                row = page_ledger.get(page_id, {})
                owner_id = str(order.get("owner_id", ""))
                ui_agent_id = str(order.get("ui_understanding_agent_id", ""))
                task_id = str(order.get("codearts_task_id", ""))
                expected_states = sorted(str(item["state_id"]) for item in contract.get("states", []))
                expected_paths = order.get("exclusive_code_paths")
                try:
                    ledger_states = json.loads(row.get("state_ids", ""))
                    ledger_paths = json.loads(row.get("exclusive_code_paths", ""))
                except (TypeError, json.JSONDecodeError):
                    ledger_states = ledger_paths = None
                if (
                    row.get("work_order_id") != order.get("work_order_id")
                    or row.get("owner_id") != owner_id
                    or row.get("ui_understanding_agent_id") != ui_agent_id
                    or row.get("codearts_task_id") != task_id
                    or row.get("contract_sha256") != contract_sha256
                    or ledger_states != expected_states
                    or ledger_paths != expected_paths
                    or row.get("status") != "ACCEPTED"
                    or not row.get("updated_at")
                ):
                    errors.append(f"Page implementation ledger differs from frozen page order: {page_id}")
                if owner_id in seen_page_owners:
                    errors.append(f"Page owner is reused across pages: {owner_id}")
                if ui_agent_id in seen_ui_agents:
                    errors.append(f"UI-understanding agent is reused across pages: {ui_agent_id}")
                if task_id in seen_task_ids:
                    errors.append(f"CodeArts task ID is reused: {task_id}")
                seen_page_owners.add(owner_id)
                seen_ui_agents.add(ui_agent_id)
                seen_task_ids.add(task_id)
                actors = {owner_id, ui_agent_id}
                feature_ids = {
                    str(value) for value in contract.get("feature_ids", [])
                    if isinstance(value, str) and value
                }
                for feature_id in feature_ids:
                    feature_actor_ids.setdefault(feature_id, set()).update(actors)
                    feature_exclusive_paths.setdefault(feature_id, [])
                for code_relative in expected_paths if isinstance(expected_paths, list) else []:
                    code_path = safe_relative_path(
                        project, str(code_relative), f"exclusive page code path for {page_id}", errors
                    )
                    if not code_path:
                        continue
                    for existing, existing_owner in all_exclusive_paths:
                        try:
                            code_path.relative_to(existing)
                            overlaps = True
                        except ValueError:
                            try:
                                existing.relative_to(code_path)
                                overlaps = True
                            except ValueError:
                                overlaps = False
                        if overlaps:
                            errors.append(
                                f"Page/capability exclusive code ownership overlaps: {page_id}/{existing_owner}"
                            )
                    all_exclusive_paths.append((code_path, page_id))
                    for feature_id in feature_ids:
                        feature_exclusive_paths[feature_id].append(code_path)
            for order in governed_orders:
                capability_id = str(order.get("capability_id", ""))
                if not capability_id:
                    continue
                task_id = str(order.get("codearts_task_id", ""))
                if task_id in seen_task_ids:
                    errors.append(f"CodeArts task ID is reused: {task_id}")
                seen_task_ids.add(task_id)
                for code_relative in order.get("exclusive_code_paths", []):
                    code_path = safe_relative_path(
                        project, str(code_relative),
                        f"exclusive capability code path for {capability_id}", errors,
                    )
                    if not code_path:
                        continue
                    for existing, existing_owner in all_exclusive_paths:
                        try:
                            code_path.relative_to(existing)
                            overlaps = True
                        except ValueError:
                            try:
                                existing.relative_to(code_path)
                                overlaps = True
                            except ValueError:
                                overlaps = False
                        if overlaps:
                            errors.append(
                                "Page/capability exclusive code ownership overlaps: "
                                f"{capability_id}/{existing_owner}"
                            )
                    all_exclusive_paths.append((code_path, capability_id))
            if set(feature_actor_ids) != included_features:
                errors.append("Page contracts do not exactly cover included feature ownership")
        except (OSError, TypeError, ValueError) as exc:
            errors.append(f"Cannot validate Phase 4 page/capability orders: {exc}")

    for feature_id in sorted(set() if page_order_mode else included_features):
        rows = registry_by_feature.get(feature_id, [])
        if len(rows) != 1:
            continue
        registry_row = rows[0]
        feature_order_id = str(registry_row.get("work_order_id", ""))
        relative = f"feature-work-orders/{feature_order_id}.json"
        order_path = safe_relative_path(
            phase_dir, relative, f"feature work order {feature_order_id}", errors
        )
        if (
            not ID_RE.fullmatch(feature_order_id)
            or registry_row.get("relative_path") != relative
            or registry_row.get("status") != "ISSUED"
            or not order_path
            or not order_path.is_file()
        ):
            errors.append(f"Invalid registered feature work order: {feature_id}")
            continue
        try:
            order = load_json(order_path)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        actors = order.get("ownership") if isinstance(order.get("ownership"), dict) else {}
        actor_values = {
            str(actors.get(key, "")) for key in feature_role_keys
            if isinstance(actors.get(key), str) and ACTOR_RE.fullmatch(str(actors.get(key)))
        }
        feature_sources = {
            inventory_id: source for inventory_id, source in source_rows.items()
            if source.get("feature_id") == feature_id
        }
        feature_parity = {
            parity_id: row for parity_id, row in parity.items()
            if row.get("feature_id") == feature_id
        }
        expected_modules: set[str] = set()
        expected_targets: list[dict[str, str]] = []
        expected_assets: set[str] = set()
        for inventory_id, source in feature_sources.items():
            try:
                row_key = phase4_source_row_key(source)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            mapping = architecture.get(row_key, {})
            module_id = str(mapping.get("harmony_module_id", ""))
            mapping_type = str(mapping.get("mapping_type", ""))
            target_id = str(
                mapping.get("route_id", "")
                if mapping_type == "ROUTE_PAGE"
                else mapping.get("surface_shell_id", "")
            )
            if module_id:
                expected_modules.add(module_id)
            expected_targets.append({
                "source_row_key": row_key,
                "harmony_module_id": module_id,
                "target_kind": mapping_type,
                "target_id": target_id,
            })
            for asset_id in phase4_json_string_list(
                source.get("asset_ids", "[]"), f"{inventory_id}.asset_ids", errors
            ):
                if asset_id != "NONE_FOUND":
                    expected_assets.add(asset_id)
        expected_targets.sort(key=lambda item: item["source_row_key"])
        feature_capabilities = {
            requirement_id: row for requirement_id, row in phase3_capabilities_for_orders.items()
            if row.get("source_feature_id") == feature_id
        }
        expected_h4envs = sorted({row.get("h4env_id", "") for row in feature_parity.values()})
        if (
            set(order) != feature_order_keys
            or registry_row.get("work_order_sha256") != sha256_file(order_path)
            or registry_row.get("issued_by") != phase4_ownership.get("implementation_lead_id")
            or registry_row.get("issued_at") != order.get("issued_at")
            or order_path.stat().st_mode & 0o222
            or order.get("schema_version") != "1.0"
            or order.get("work_order_id") != feature_order_id
            or order.get("run_id") != scope.get("run_id")
            or order.get("phase") != 4
            or order.get("feature_id") != feature_id
            or order.get("status") != "ISSUED"
            or order.get("issued_by") != phase4_ownership.get("implementation_lead_id")
            or order.get("phase4_manifest_sha256") != phase4_manifest_sha256
            or order.get("stage04_input_lock_sha256") != input_lock_sha256
            or set(actors) != feature_role_keys
            or len(actor_values) != len(feature_role_keys)
            or actor_values & set(role_values)
            or order.get("visual_asset_agent_id") != phase4_ownership.get("visual_asset_agent_id")
            or order.get("source_inventory_ids") != sorted(feature_sources)
            or order.get("parity_ids") != sorted(feature_parity)
            or order.get("harmony_module_ids") != sorted(expected_modules)
            or order.get("targets") != expected_targets
            or order.get("required_h4env_ids") != expected_h4envs
            or order.get("asset_ids") != sorted(expected_assets)
            or order.get("capability_requirement_ids") != sorted(feature_capabilities)
            or order.get("capability_contract_ids")
            != sorted({row.get("capability_contract_id", "") for row in feature_capabilities.values()})
            or not isinstance(order.get("completion_conditions"), list)
            or not order.get("completion_conditions")
            or any(not isinstance(item, str) or not item.strip() for item in order.get("completion_conditions", []))
        ):
            errors.append(f"Feature work order identity, actors, or frozen coverage differs: {feature_id}")
        raw_code_paths = order.get("exclusive_code_paths")
        code_paths: list[Path] = []
        if (
            not isinstance(raw_code_paths, list)
            or not raw_code_paths
            or any(not isinstance(item, str) or not item for item in raw_code_paths)
            or raw_code_paths != sorted(set(raw_code_paths))
        ):
            errors.append(f"Feature work order has invalid exclusive code paths: {feature_id}")
        else:
            for code_relative in raw_code_paths:
                code_path = safe_relative_path(
                    project, code_relative, f"exclusive code path for {feature_id}", errors
                )
                if not code_path:
                    continue
                if any(part in STAGE4_PROJECT_EXCLUDED_PARTS for part in code_path.relative_to(project).parts):
                    errors.append(f"Feature code ownership points into generated output: {feature_id}")
                    continue
                for existing, existing_feature in all_exclusive_paths:
                    overlaps = False
                    try:
                        code_path.relative_to(existing)
                        overlaps = True
                    except ValueError:
                        try:
                            existing.relative_to(code_path)
                            overlaps = True
                        except ValueError:
                            pass
                    if overlaps:
                        errors.append(
                            f"Feature exclusive code ownership overlaps: {feature_id}/{existing_feature}"
                        )
                code_paths.append(code_path)
                all_exclusive_paths.append((code_path, feature_id))
        feature_orders[feature_id] = order
        feature_actor_ids[feature_id] = actor_values
        feature_exclusive_paths[feature_id] = code_paths

    # Bind final parity implementation claims and visual rows to real, feature-owned source files.
    expected_visual_ids: set[str] = set()
    parity_visual_ids: dict[str, set[str]] = {}
    parity_asset_ids: dict[str, set[str]] = {}
    parity_decision_ids: dict[str, set[str]] = {}
    for parity_id, row in parity.items():
        feature_id = str(row.get("feature_id", ""))
        source = source_rows.get(row.get("inventory_id", ""), {})
        try:
            row_key = phase4_source_row_key(source)
        except ValueError as exc:
            errors.append(f"{parity_id}: {exc}")
            continue
        mapping = architecture.get(row_key, {})
        mapping_type = str(mapping.get("mapping_type", ""))
        target_id = str(
            mapping.get("route_id", "")
            if mapping_type == "ROUTE_PAGE"
            else mapping.get("surface_shell_id", "")
        )
        visual_ids = phase4_json_string_list(
            row.get("visual_element_ids", "[]"), f"{parity_id}.visual_element_ids", errors,
            allow_empty=False,
        )
        asset_ids = phase4_json_string_list(
            row.get("asset_ids", "[]"), f"{parity_id}.asset_ids", errors
        )
        decision_ids = phase4_json_string_list(
            row.get("nativeization_decision_ids", "[]"),
            f"{parity_id}.nativeization_decision_ids", errors,
        )
        expected_source_assets = {
            item for item in phase4_json_string_list(
                source.get("asset_ids", "[]"), f"{parity_id}.source_asset_ids", errors
            ) if item != "NONE_FOUND"
        }
        source_refs = phase4_json_string_list(
            row.get("harmony_source_refs", "[]"), f"{parity_id}.harmony_source_refs", errors,
            allow_empty=False,
        )
        for reference in source_refs:
            if ":" not in reference:
                errors.append(f"{parity_id}: source reference lacks a line number: {reference}")
                continue
            relative, line_value = reference.rsplit(":", 1)
            source_path = safe_relative_path(project, relative, f"{parity_id} source reference", errors)
            try:
                line = int(line_value)
            except ValueError:
                line = 0
            if (
                not source_path or not source_path.is_file() or line <= 0
                or line > len(source_path.read_text(encoding="utf-8", errors="replace").splitlines())
            ):
                errors.append(f"{parity_id}: invalid source reference: {reference}")
                continue
            owned = False
            for owned_path in feature_exclusive_paths.get(feature_id, []):
                if source_path == owned_path:
                    owned = True
                    break
                if owned_path.is_dir():
                    try:
                        source_path.relative_to(owned_path)
                        owned = True
                        break
                    except ValueError:
                        pass
            if not owned:
                errors.append(f"{parity_id}: source reference is outside frozen feature ownership")
        if (
            row.get("source_row_key") != row_key
            or row.get("harmony_module_id") != mapping.get("harmony_module_id")
            or row.get("target_kind") != mapping_type
            or row.get("target_id") != target_id
            or row.get("implemented_by") not in feature_actor_ids.get(feature_id, set())
            or set(asset_ids) != expected_source_assets
            or row.get("status") != "ACCEPTED"
        ):
            errors.append(f"{parity_id}: implementation source, target, assets, or actor differs")
        expected_visual_ids.update(visual_ids)
        parity_visual_ids[parity_id] = set(visual_ids)
        parity_asset_ids[parity_id] = set(asset_ids)
        parity_decision_ids[parity_id] = set(decision_ids)

    visual_rows = read_csv_rows(phase_dir / "visual-elements.csv")
    visual_elements = index_unique_rows(
        visual_rows, "visual_element_id", "Phase 4 visual elements", errors,
    )
    if set(visual_elements) != expected_visual_ids:
        errors.append("Phase 4 visual-element registry differs from parity declarations")
    scope_envs_for_visual = {
        str(item.get("env_id", "")): item
        for item in scope.get("environments", []) if isinstance(item, dict)
    }
    for visual_id, visual in visual_elements.items():
        parity_id = str(visual.get("parity_id", ""))
        parity_row = parity.get(parity_id, {})
        feature_id = str(parity_row.get("feature_id", ""))
        # P4 分层验证：LITE 页把逐视觉元素几何从门禁降为抽样（review 端
        # reviewed_visual_element_ids 抽样集），此处跳过逐元素几何匹配；
        # CORE 页保持逐元素几何不变。
        visual_page_tier = page_verification_tier.get(
            str(parity_row.get("page_id", "")), "CORE"
        )
        android_geometry = phase4_geometry(
            visual.get("android_geometry", ""), f"{visual_id}.android_geometry", errors
        )
        harmony_geometry = phase4_geometry(
            visual.get("harmony_geometry", ""), f"{visual_id}.harmony_geometry", errors
        )
        android_env = scope_envs_for_visual.get(parity_row.get("source_env_id", ""), {})
        resolution_match = re.fullmatch(r"(\d+)x(\d+)", str(android_env.get("resolution", "")))
        harmony_comparison = environments.get(parity_row.get("h4env_id", ""), {}).get("comparison", {})
        if android_geometry and resolution_match and (
            android_geometry["x"] + android_geometry["width"] > int(resolution_match.group(1))
            or android_geometry["y"] + android_geometry["height"] > int(resolution_match.group(2))
        ):
            errors.append(f"{visual_id}: Android geometry escapes the frozen screenshot")
        if harmony_geometry and isinstance(harmony_comparison, dict) and (
            harmony_geometry["x"] + harmony_geometry["width"]
            > float(harmony_comparison.get("screenshot_width", 0))
            or harmony_geometry["y"] + harmony_geometry["height"]
            > float(harmony_comparison.get("screenshot_height", 0))
        ):
            errors.append(f"{visual_id}: Harmony geometry escapes the frozen screenshot")
        try:
            android_spec = json.loads(visual.get("android_visual_spec", ""))
            harmony_spec = json.loads(visual.get("harmony_visual_spec", ""))
        except (TypeError, json.JSONDecodeError):
            android_spec = harmony_spec = None
        if android_geometry and harmony_geometry and resolution_match and isinstance(
            harmony_comparison, dict
        ) and visual_page_tier != "LITE":
            harmony_width = float(harmony_comparison.get("screenshot_width", 0))
            harmony_height = float(harmony_comparison.get("screenshot_height", 0))
            tolerance = float(harmony_comparison.get("geometry_tolerance_px", 0))
            if not normalized_geometry_matches(
                android_geometry, harmony_geometry,
                (float(resolution_match.group(1)), float(resolution_match.group(2))),
                (harmony_width, harmony_height), tolerance,
            ):
                errors.append(
                    f"{visual_id}: normalized Android/Harmony geometry exceeds "
                    f"the frozen {tolerance:g}px tolerance"
                )
        if (
            isinstance(android_spec, dict) and isinstance(harmony_spec, dict)
            and comparable_visual_spec(android_spec) != comparable_visual_spec(harmony_spec)
        ):
            errors.append(f"{visual_id}: Android/Harmony visual semantics differ")
        harmony_file = safe_relative_path(
            project, visual.get("harmony_file", ""), f"{visual_id} Harmony source", errors
        )
        symbol = str(visual.get("harmony_symbol", ""))
        source_text = (
            harmony_file.read_text(encoding="utf-8", errors="replace")
            if harmony_file and harmony_file.is_file() else ""
        )
        asset_id = str(visual.get("asset_id", ""))
        decision_id = str(visual.get("nativeization_decision_id", ""))
        if (
            visual_id not in parity_visual_ids.get(parity_id, set())
            or visual.get("android_evidence_id") != parity_row.get("android_evidence_id")
            or not visual.get("element_kind")
            or not isinstance(android_spec, dict) or not android_spec
            or not isinstance(harmony_spec, dict) or not harmony_spec
            or not harmony_file or not harmony_file.is_file()
            or not symbol or not re.search(rf"\b{re.escape(symbol)}\b", source_text)
            or visual.get("implemented_by") not in feature_actor_ids.get(feature_id, set())
            or visual.get("status") != "ACCEPTED"
            or (asset_id and (asset_id not in phase2_assets or asset_id not in parity_asset_ids.get(parity_id, set())))
            or (decision_id and decision_id not in parity_decision_ids.get(parity_id, set()))
        ):
            errors.append(f"{visual_id}: visual source/spec/asset/actor binding differs")

    local_attempt_rows = read_csv_rows(phase_dir / "attempt-ledger.csv")
    controller_attempt_rows = read_csv_rows(run_dir / "controller" / "phase4-attempt-ledger.csv")
    if local_attempt_rows != controller_attempt_rows:
        errors.append("Phase 4 local attempt ledger differs from the controller anchor")
    validate_phase4_attempt_chain(controller_attempt_rows, errors)
    attempts_by_parity: dict[str, int] = {}
    for attempt_row in controller_attempt_rows:
        attempt_parity = str(attempt_row.get("parity_id", ""))
        attempts_by_parity[attempt_parity] = attempts_by_parity.get(attempt_parity, 0) + 1
    if any(count > 3 for count in attempts_by_parity.values()):
        errors.append("Phase 4 automatic execution budget exceeds initial attempt plus two repairs")

    evidence_rows = read_csv_rows(phase_dir / "evidence-index.csv")
    evidence_index = index_unique_rows(evidence_rows, "evidence_id", "Phase 4 evidence index", errors)
    active_evidence = {key: row for key, row in evidence_index.items() if row.get("status") == "SEALED"}
    ledger_evidence_ids = {str(row.get("evidence_id", "")) for row in controller_attempt_rows}
    for attempted_id in ledger_evidence_ids:
        if attempted_id not in evidence_index and not (
            phase_dir / "attempts" / f"ATT-{attempted_id}.json"
        ).is_file():
            errors.append(f"{attempted_id}: anchored execution has neither evidence nor failure package")
    if not set(evidence_index) <= ledger_evidence_ids:
        errors.append("Phase 4 evidence index contains an unanchored execution")
    used_evidence_ids: set[str] = set()
    for parity_id, row in parity.items():
        source = source_rows.get(row.get("inventory_id", ""))
        if not source:
            errors.append(f"{parity_id}: parity references out-of-scope or missing inventory")
        else:
            for field, source_field in (
                ("feature_id", "feature_id"), ("page_id", "page_id"), ("state_id", "state_id"),
                ("source_env_id", "env_id"), ("android_evidence_id", "evidence_id"),
            ):
                if row.get(field) != source.get(source_field):
                    errors.append(f"{parity_id}: {field} differs from frozen Android inventory")
        if row.get("status") != "ACCEPTED":
            errors.append(f"{parity_id}: final parity status is not ACCEPTED")
        evidence_id = row.get("harmony_evidence_id", "")
        evidence_row = active_evidence.get(evidence_id)
        if not evidence_row or evidence_id in used_evidence_ids:
            errors.append(f"{parity_id}: final HEVD is missing, not SEALED, or reused")
            continue
        used_evidence_ids.add(evidence_id)
        for field in (
            "parity_id", "inventory_id", "feature_id", "page_id", "state_id", "h4env_id",
            "android_evidence_id",
        ):
            if evidence_row.get(field) != row.get(field):
                errors.append(f"{evidence_id}: evidence index {field} differs from parity")
        if evidence_row.get("hbuild_id") != build_by_env.get(row.get("h4env_id", "")):
            errors.append(f"{evidence_id}: evidence does not use the final HBUILD for its H4ENV")
    if used_evidence_ids != set(active_evidence):
        errors.append("Phase 4 active HEVD set does not exactly cover parity rows")

    # Recompute every final HEVD package, emulator screenshot, assertion, and artifact binding.
    capability_assertion_evidence: dict[str, set[str]] = {}
    for evidence_id in sorted(used_evidence_ids):
        row = active_evidence[evidence_id]
        # P4 分层验证：HEVD 重放按 evidence-index 的 verification_tier 列分流
        # （缺列/空值默认 CORE，向后兼容旧证据行）。哈希链/三类断言/截图
        # 分辨率等式对两级 tier 均保留。
        evidence_tier = str(row.get("verification_tier") or "CORE").strip().upper()
        if evidence_tier not in {"CORE", "LITE"}:
            errors.append(f"{evidence_id}: verification_tier must be CORE or LITE: {evidence_tier!r}")
            evidence_tier = "CORE"
        expected_relative = (
            f"evidence/{row.get('h4env_id', '')}/{row.get('feature_id', '')}/"
            f"{row.get('page_id', '')}/{row.get('state_id', '')}/{evidence_id}"
        )
        if row.get("relative_path") != expected_relative:
            errors.append(f"{evidence_id}: HEVD path is not canonical")
            continue
        evidence_dir = safe_relative_path(phase_dir, expected_relative, f"HEVD {evidence_id}", errors)
        if not evidence_dir or not evidence_dir.is_dir():
            continue
        verify_sealed_package(evidence_dir, evidence_id, "SEALED", f"HEVD {evidence_id}", errors)
        try:
            metadata_path = evidence_dir / "metadata.json"
            metadata = load_json(metadata_path)
            h4env_id = str(row.get("h4env_id", ""))
            environment = environments[h4env_id]
            build_id = str(row.get("hbuild_id", ""))
            build = builds[build_id]
            primary = build.get("primary_artifact", {})
            for field in (
                "evidence_id", "parity_id", "inventory_id", "feature_id", "page_id", "state_id",
                "h4env_id", "hbuild_id", "android_evidence_id",
            ):
                expected = evidence_id if field == "evidence_id" else row.get(field)
                if metadata.get(field) != expected:
                    errors.append(f"{evidence_id}: metadata {field} differs from evidence index")
            if (
                row.get("metadata_sha256") != sha256_file(metadata_path)
                or metadata.get("status") != "SEALED"
                or row.get("captured_by") != phase4_ownership.get("verification_executor_id")
                or metadata.get("captured_by") != phase4_ownership.get("verification_executor_id")
                or metadata.get("captured_at") != row.get("captured_at")
                or metadata.get("input_lock_sha256") != input_lock_sha256
                or metadata.get("source_snapshot_sha256") != source_snapshot_sha256
                or row.get("source_snapshot_sha256") != source_snapshot_sha256
                or metadata.get("build_artifact_sha256") != primary.get("sha256")
                or row.get("build_artifact_sha256") != primary.get("sha256")
                or str(metadata.get("device_type", "")).lower() != "emulator"
                or metadata.get("device_id") != environment.get("device_id")
                or metadata.get("device_serial") != environment.get("emulator", {}).get("serial")
                or metadata.get("bundle_name") != environment.get("base_application", {}).get("bundle_name")
                or metadata.get("target_kind") != parity.get(str(metadata.get("parity_id", "")), {}).get("target_kind")
                or metadata.get("target_id") != parity.get(str(metadata.get("parity_id", "")), {}).get("target_id")
            ):
                errors.append(f"{evidence_id}: executor, snapshot, emulator, or artifact binding differs")
            screenshot = evidence_dir / "screenshot.png"
            width, height = validate_complete_png(screenshot)
            screenshot_digest = sha256_file(screenshot)
            screenshot_record = metadata.get("screenshot") if isinstance(metadata.get("screenshot"), dict) else {}
            uitest_record = metadata.get("ui_test_snapshot") if isinstance(metadata.get("ui_test_snapshot"), dict) else {}
            assertion_record = metadata.get("assertions") if isinstance(metadata.get("assertions"), dict) else {}
            comparison = environment.get("comparison") if isinstance(environment.get("comparison"), dict) else {}
            if (
                row.get("screenshot_sha256") != screenshot_digest
                or screenshot_record.get("sha256") != screenshot_digest
                or screenshot_record.get("width") != width
                or screenshot_record.get("height") != height
                or (width, height)
                != (comparison.get("screenshot_width"), comparison.get("screenshot_height"))
            ):
                errors.append(f"{evidence_id}: screenshot bytes, metadata, or dimensions differ")
            generation = load_json(generation_manifest)
            probe_id = f"{row.get('page_id', '')}::{row.get('state_id', '')}"
            probes = [
                item for item in generation.get("probes", [])
                if isinstance(item, dict) and item.get("probe_id") == probe_id
            ]
            plans = [
                item for item in generation.get("page_plans", [])
                if isinstance(item, dict) and item.get("page_id") == row.get("page_id")
            ]
            page_contract = load_json(phase_dir / "page-contracts" / f"{row.get('page_id', '')}.json")
            test_hap = evidence_dir / "uitest-test.hap"
            uitest_commands = [
                item for item in metadata.get("commands", [])
                if isinstance(item, dict) and item.get("category") == "UITEST_SNAPSHOT_CAPTURE"
            ]
            if len(probes) != 1 or len(plans) != 1 or len(uitest_commands) != 1 or not test_hap.is_file():
                errors.append(f"{evidence_id}: UiTest probe, plan, command, or test HAP is missing")
            else:
                page_plan = safe_relative_path(
                    phase_dir, str(plans[0].get("relative_path", "")),
                    f"{evidence_id} ArkTS page plan", errors,
                )
                if not page_plan or not page_plan.is_file() or sha256_file(page_plan) != plans[0].get("sha256"):
                    errors.append(f"{evidence_id}: UiTest page plan hash differs")
                else:
                    command_sha256 = hashlib.sha256(json.dumps(
                        uitest_commands[0].get("argv"), ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")).hexdigest()
                    device_identity_sha256 = hashlib.sha256(json.dumps(
                        {"device_id": environment.get("device_id"), "serial": environment.get("emulator", {}).get("serial")},
                        sort_keys=True, separators=(",", ":"),
                    ).encode("utf-8")).hexdigest()
                    try:
                        if evidence_tier == "LITE":
                            # LITE 轻证：哈希绑定保留，逐组件严检与 EVENT/TRANSITION
                            # 精确相等降为结构化组件树对比（阈值 LITE_COMPONENT_OVERLAP_MIN）。
                            validate_uitest_evidence_lite(
                                evidence_dir, probes[0], page_id=str(row.get("page_id", "")),
                                state_id=str(row.get("state_id", "")),
                                bundle_name=str(environment.get("base_application", {}).get("bundle_name", "")),
                                carrier=str(page_contract.get("carrier_type", "")),
                                target_id=str(parity.get(str(row.get("parity_id", "")), {}).get("target_id", "")),
                                generation_manifest_sha256=sha256_file(generation_manifest),
                                page_plan_sha256=sha256_file(page_plan), test_hap_sha256=sha256_file(test_hap),
                                final_hap_sha256=str(primary.get("sha256", "")),
                                device_identity_sha256=device_identity_sha256, command_sha256=command_sha256,
                                expected_components=page_contract.get("components"),
                            )
                        else:
                            validate_uitest_evidence(
                                evidence_dir, probes[0], page_id=str(row.get("page_id", "")),
                                state_id=str(row.get("state_id", "")),
                                bundle_name=str(environment.get("base_application", {}).get("bundle_name", "")),
                                carrier=str(page_contract.get("carrier_type", "")),
                                target_id=str(parity.get(str(row.get("parity_id", "")), {}).get("target_id", "")),
                                generation_manifest_sha256=sha256_file(generation_manifest),
                                page_plan_sha256=sha256_file(page_plan), test_hap_sha256=sha256_file(test_hap),
                                final_hap_sha256=str(primary.get("sha256", "")),
                                device_identity_sha256=device_identity_sha256, command_sha256=command_sha256,
                                required_event_ids={
                                    str(item["event_id"]) for item in page_contract.get("interaction_bindings", [])
                                    if isinstance(item, dict) and item.get("event_id")
                                },
                                required_transition_ids={
                                    str(item["transition_id"]) for item in page_contract.get("transitions", [])
                                    if isinstance(item, dict) and item.get("transition_id")
                                },
                            )
                    except ValueError as exc:
                        errors.append(f"{evidence_id}: {exc}")
            if (
                uitest_record.get("path") != "ui-test-snapshot.json"
                or uitest_record.get("sha256") != sha256_file(evidence_dir / "ui-test-snapshot.json")
                or uitest_record.get("metadata_sha256") != sha256_file(evidence_dir / "ui-test-snapshot-metadata.json")
                or uitest_record.get("operation_trace_sha256") != sha256_file(evidence_dir / "ui-test-snapshot-operation-trace.json")
                or uitest_record.get("screenshot_sha256") != sha256_file(evidence_dir / "ui-test-snapshot.png")
                or uitest_record.get("test_hap_sha256") != sha256_file(test_hap)
                or uitest_record.get("final_hap_sha256") != primary.get("sha256")
            ):
                errors.append(f"{evidence_id}: UiTest snapshot metadata hashes differ")
            assertions = load_json(evidence_dir / "assertions.json")
            assertion_rows = assertions.get("assertions") if isinstance(assertions.get("assertions"), list) else []
            assertion_kinds = {
                str(item.get("kind", "")) for item in assertion_rows if isinstance(item, dict)
            }
            if (
                assertions.get("parity_id") != row.get("parity_id")
                or assertions.get("hbuild_id") != build_id
                or assertions.get("h4env_id") != h4env_id
                or assertions.get("device_id") != environment.get("device_id")
                or assertions.get("device_serial") != environment.get("emulator", {}).get("serial")
                or assertions.get("bundle_name") != environment.get("base_application", {}).get("bundle_name")
                or assertion_record.get("path") != "assertions.json"
                or assertion_record.get("sha256") != sha256_file(evidence_dir / "assertions.json")
                or not assertion_rows
                or any(not isinstance(item, dict) or item.get("status") != "PASS" for item in assertion_rows)
                or {"VISUAL_STATE", "BUSINESS_RESULT", "INTERACTION"} - assertion_kinds
            ):
                errors.append(f"{evidence_id}: live assertions are missing, failing, or misbound")
            for assertion in assertion_rows:
                if not isinstance(assertion, dict) or assertion.get("kind") != "CAPABILITY_RESULT":
                    continue
                subjects = assertion.get("subject_ids")
                if not isinstance(subjects, list):
                    errors.append(f"{evidence_id}: capability assertion lacks subject IDs")
                    continue
                for requirement_id in subjects:
                    if isinstance(requirement_id, str) and requirement_id:
                        capability_assertion_evidence.setdefault(requirement_id, set()).add(evidence_id)
            validate_phase4_commands(
                evidence_dir,
                metadata.get("commands"),
                environment,
                (
                    LITE_EVIDENCE_SEQUENCE if evidence_tier == "LITE"
                    else [
                        "DEVICE_CHECK", "CLEAN_INSTALL", "SEED_RESET", "NETWORK_PROFILE",
                        "PERMISSION_PROFILE", "LAUNCH", "NAVIGATE", "BUSINESS_ASSERT",
                        "SCREENSHOT_CAPTURE", "UITEST_SNAPSHOT_CAPTURE",
                    ]
                ),
                f"HEVD {evidence_id}",
                errors,
            )
            result_bindings = {
                "BUSINESS_ASSERT": ("assertions.json", evidence_dir / "assertions.json"),
                "SCREENSHOT_CAPTURE": ("screenshot.png", screenshot),
                "UITEST_SNAPSHOT_CAPTURE": ("ui-test-snapshot.json", evidence_dir / "ui-test-snapshot.json"),
            }
            for command in metadata.get("commands", []):
                if not isinstance(command, dict):
                    continue
                expected_result = result_bindings.get(command.get("category"))
                if expected_result and (
                    command.get("result_path") != expected_result[0]
                    or command.get("result_sha256") != sha256_file(expected_result[1])
                ):
                    errors.append(f"{evidence_id}: command result hash differs")
        except (KeyError, OSError, ValueError) as exc:
            errors.append(f"{evidence_id}: {exc}")

    # Final review is one immutable HREV per parity and recomputes both evidence sides.
    acceptance_rows = read_csv_rows(phase_dir / "acceptance-ledger.csv")
    review_local_decisions = index_unique_rows(
        read_csv_rows(phase_dir / "nativeization-decisions.csv"),
        "decision_id", "Phase 4 nativeization decisions for review", errors,
    )
    review_controller_decisions = index_unique_rows(
        read_csv_rows(run_dir / "controller" / "decision-log.csv"),
        "decision_id", "controller decisions for Phase 4 review", errors,
    )
    superseded_review_controller_decisions = {
        item.get("supersedes_id", "")
        for item in review_controller_decisions.values() if item.get("supersedes_id")
    }
    hrev_keys = {
        "parity_id", "visual_result", "functional_result", "asset_result",
        "reviewed_visual_element_ids", "differences", "notes", "review_id",
        "inventory_id", "android_evidence_id", "harmony_evidence_id",
        "android_manifest_sha256", "android_screenshot_sha256", "android_layout_sha256",
        "harmony_manifest_sha256", "harmony_screenshot_sha256", "harmony_ui_test_snapshot_sha256",
        "harmony_assertions_sha256", "reviewer_id", "reviewed_at", "decision",
        "attestations",
    }
    active_reviews = [row for row in acceptance_rows if row.get("status") != "SUPERSEDED"]
    reviews_by_parity: dict[str, list[dict[str, str]]] = {}
    for row in active_reviews:
        reviews_by_parity.setdefault(row.get("parity_id", ""), []).append(row)
    if set(reviews_by_parity) != set(parity) or any(len(rows) != 1 for rows in reviews_by_parity.values()):
        errors.append("Acceptance ledger does not contain exactly one active review per parity row")
    for parity_id, parity_row in parity.items():
        rows = reviews_by_parity.get(parity_id, [])
        if len(rows) != 1:
            continue
        row = rows[0]
        review_id = row.get("review_id", "")
        android_evidence_id = parity_row.get("android_evidence_id", "")
        harmony_evidence_id = parity_row.get("harmony_evidence_id", "")
        android_dir = android_copies.get(android_evidence_id)
        harmony_row = active_evidence.get(harmony_evidence_id)
        harmony_dir = (
            phase_dir / str(harmony_row.get("relative_path", "")) if harmony_row else None
        )
        review_path = phase_dir / "reviews" / f"{review_id}.json"
        try:
            review = load_json(review_path)
            review_attestations = (
                review.get("attestations")
                if isinstance(review.get("attestations"), dict)
                else {}
            )
            expected_hashes = {
                "android_manifest_sha256": sha256_file(android_dir / "manifest.sha256"),
                "android_screenshot_sha256": sha256_file(android_dir / "screenshot.png"),
                "android_layout_sha256": sha256_file(android_dir / "layout.json"),
                "harmony_manifest_sha256": sha256_file(harmony_dir / "manifest.sha256"),
                "harmony_screenshot_sha256": sha256_file(harmony_dir / "screenshot.png"),
                "harmony_ui_test_snapshot_sha256": sha256_file(harmony_dir / "ui-test-snapshot.json"),
                "harmony_assertions_sha256": sha256_file(harmony_dir / "assertions.json"),
                "comparison_sha256": sha256_file(review_path),
            }
            results = {
                dimension: str(review.get(f"{dimension}_result", ""))
                for dimension in ("visual", "functional", "asset")
            }
            reviewed_visual_ids = review.get("reviewed_visual_element_ids")
            differences = review.get("differences")
            # P4 分层验证：review JSON 的 verification_tier（review_parity 写入，
            # 缺省 CORE）须在值域内且与页合同 tier 一致；reviewed 视觉元素集
            # CORE=全集精确相等，LITE=非空抽样子集（⊆ 全集，全集亦可）。
            review_tier = str(review.get("verification_tier") or "CORE").strip().upper()
            expected_page_tier = page_verification_tier.get(
                str(parity_row.get("page_id", "")), "CORE"
            )
            difference_dimensions: set[str] = set()
            approved_difference_ids: set[str] = set()
            if isinstance(differences, list):
                for difference in differences:
                    if not isinstance(difference, dict):
                        errors.append(f"{parity_id}: review difference is not an object")
                        continue
                    dimension = str(difference.get("dimension", "")).lower()
                    difference_dimensions.add(dimension)
                    if (
                        dimension not in {"visual", "functional", "asset"}
                        or not str(difference.get("android_observation", "")).strip()
                        or not str(difference.get("harmony_observation", "")).strip()
                    ):
                        errors.append(f"{parity_id}: review difference is incomplete")
                    if results.get(dimension) == "APPROVED_DIFFERENCE":
                        decision_id = str(difference.get("decision_id", ""))
                        if not ID_RE.fullmatch(decision_id):
                            errors.append(f"{parity_id}: approved difference lacks a Decision-ID")
                        else:
                            approved_difference_ids.add(decision_id)
            nonmatch_dimensions = {
                dimension for dimension, result in results.items() if result != "MATCH"
            }
            if (
                set(review) - {"verification_tier"} != hrev_keys
                or review_path.stat().st_mode & 0o222
                or not isinstance(reviewed_visual_ids, list)
                or not reviewed_visual_ids_are_acceptable(
                    reviewed_visual_ids,
                    set(parity_visual_ids.get(parity_id, set())),
                    expected_page_tier,
                )
                or sorted(reviewed_visual_ids) != reviewed_visual_ids
                or review_tier not in {"CORE", "LITE"}
                or review_tier != expected_page_tier
                or not isinstance(differences, list)
                or difference_dimensions != nonmatch_dimensions
                or (bool(nonmatch_dimensions) != bool(differences))
            ):
                errors.append(f"{parity_id}: sealed review coverage or difference structure differs")
            for decision_id in approved_difference_ids:
                local_decision = review_local_decisions.get(decision_id, {})
                affected = phase4_json_string_list(
                    local_decision.get("affected_parity_ids", "[]"),
                    f"{decision_id}.affected_parity_ids", errors,
                )
                controller_decision_id = str(local_decision.get("controller_decision_id", ""))
                controller_decision = review_controller_decisions.get(controller_decision_id, {})
                if (
                    local_decision.get("status") != "APPROVED"
                    or local_decision.get("decision_class") != "PLATFORM_VISUAL"
                    or local_decision.get("approved_by")
                    != phase4_ownership.get("parity_acceptance_agent_id")
                    or parity_id not in affected
                    or controller_decision_id in superseded_review_controller_decisions
                    or controller_decision.get("decided_by")
                    != scope.get("ownership", {}).get("migration_controller_id")
                    or not str(controller_decision.get("decision", "")).strip()
                    or not str(controller_decision.get("rationale", "")).strip()
                ):
                    errors.append(f"{parity_id}: approved difference lacks live dual approval")
            if (
                not ID_RE.fullmatch(review_id)
                or row.get("status") != "ACCEPTED"
                or row.get("reviewer_id") != phase4_ownership.get("parity_acceptance_agent_id")
                or row.get("inventory_id") != parity_row.get("inventory_id")
                or row.get("android_evidence_id") != android_evidence_id
                or row.get("harmony_evidence_id") != harmony_evidence_id
                or any(row.get(key) != value for key, value in expected_hashes.items())
                or review.get("review_id") != review_id
                or review.get("parity_id") != parity_id
                or review.get("inventory_id") != parity_row.get("inventory_id")
                or review.get("android_evidence_id") != android_evidence_id
                or review.get("harmony_evidence_id") != harmony_evidence_id
                or review.get("decision") != "ACCEPTED"
                or review.get("reviewer_id") != phase4_ownership.get("parity_acceptance_agent_id")
                or review.get("reviewed_at") != row.get("reviewed_at")
                or any(
                    review.get(field) not in {"MATCH", "APPROVED_DIFFERENCE"}
                    for field in ("visual_result", "functional_result", "asset_result")
                )
                or review.get("functional_result") != "MATCH"
                or review.get("asset_result") != "MATCH"
                or not all(
                    review_attestations.get(field) is True
                    for field in ("opened_both_screenshots", "functional_results", "asset_provenance")
                )
                or any(review.get(key) != value for key, value in expected_hashes.items() if key != "comparison_sha256")
            ):
                errors.append(f"{parity_id}: acceptance review identity or evidence hashes differ")
        except (OSError, TypeError, ValueError) as exc:
            errors.append(f"{parity_id}: cannot verify acceptance review: {exc}")
    expected_review_ids = {
        row.get("review_id", "") for row in acceptance_rows if row.get("review_id")
    }
    actual_review_ids = {
        path.stem for path in (phase_dir / "reviews").glob("*.json")
        if path.is_file() and not path.is_symlink()
    }
    if expected_review_ids != actual_review_ids:
        errors.append("Phase 4 review files do not exactly match the acceptance ledger")

    implementation_rows = (
        [] if page_order_mode else read_csv_rows(phase_dir / "implementation-ledger.csv")
    )
    implementation = index_unique_rows(
        implementation_rows, "feature_id", "Phase 4 implementation ledger", errors
    )
    if not page_order_mode and set(implementation) != included_features:
        errors.append("Phase 4 implementation ledger differs from included feature scope")
    forbidden_implementers = {
        phase4_ownership.get("verification_executor_id"),
        phase4_ownership.get("parity_acceptance_agent_id"),
    }
    for feature_id, row in implementation.items():
        feature_order = feature_orders.get(feature_id, {})
        frozen_feature_actors = (
            feature_order.get("ownership")
            if isinstance(feature_order.get("ownership"), dict)
            else {}
        )
        actors = {
            row.get("feature_owner_id"), row.get("ui_agent_id"), row.get("business_data_agent_id"),
            row.get("native_capability_agent_id"), row.get("asset_agent_id"),
        }
        ledger_inventory_ids = phase4_json_string_list(
            row.get("source_inventory_ids", "[]"), f"{feature_id}.source_inventory_ids", errors,
            allow_empty=False,
        )
        ledger_module_ids = phase4_json_string_list(
            row.get("harmony_module_ids", "[]"), f"{feature_id}.harmony_module_ids", errors,
            allow_empty=False,
        )
        if (
            row.get("status") != "ACCEPTED"
            or "" in actors
            or actors & forbidden_implementers
            or row.get("work_order_id") != feature_order.get("work_order_id")
            or any(row.get(key) != frozen_feature_actors.get(key) for key in feature_role_keys)
            or row.get("asset_agent_id") != phase4_ownership.get("visual_asset_agent_id")
            or ledger_inventory_ids != feature_order.get("source_inventory_ids")
            or ledger_module_ids != feature_order.get("harmony_module_ids")
            or row.get("updated_by") not in {
                phase4_ownership.get("implementation_lead_id"),
                phase4_ownership.get("parity_acceptance_agent_id"),
            }
        ):
            errors.append(f"{feature_id}: implementation ledger differs from its frozen accepted work order")

    phase3_assets = index_unique_rows(
        read_csv_rows(run_dir / "phase-03-harmony-scaffold" / "asset-registry.csv"),
        "asset_id", "Phase 3 asset registry", errors,
    )
    asset_rows = read_csv_rows(phase_dir / "asset-migration.csv")
    migrated_assets = index_unique_rows(asset_rows, "asset_id", "Phase 4 asset migration", errors)
    conversion_contracts: dict[str, dict[str, Any]] = {}
    try:
        conversion_contract_file = load_json(phase_dir / "asset-conversion-contracts.json")
        if (
            set(conversion_contract_file) != {"schema_version", "created_at", "locked_by", "contracts"}
            or conversion_contract_file.get("schema_version") != "1.0"
            or conversion_contract_file.get("locked_by") != phase4_ownership.get("implementation_lead_id")
            or not conversion_contract_file.get("created_at")
            or not isinstance(conversion_contract_file.get("contracts"), list)
            or input_lock.get("asset_conversion_contracts_sha256")
            != sha256_file(phase_dir / "asset-conversion-contracts.json")
        ):
            errors.append("Phase 4 asset-conversion contract registry identity is invalid")
        raw_contracts = conversion_contract_file.get("contracts")
        if not isinstance(raw_contracts, list):
            raw_contracts = []
        contract_keys = {
            "contract_id", "source_extensions", "target_extensions", "resolved_executable",
            "executable_sha256", "argv_template", "required_argv_tokens",
            "success_output_contains", "error_output_contains",
        }
        for contract in raw_contracts:
            if not isinstance(contract, dict):
                errors.append("Asset-conversion contract contains a non-object entry")
                continue
            contract_id = str(contract.get("contract_id", ""))
            executable = Path(str(contract.get("resolved_executable", ""))).expanduser()
            source_extensions = contract.get("source_extensions")
            target_extensions = contract.get("target_extensions")
            argv_template = contract.get("argv_template")
            if (
                set(contract) != contract_keys
                or not ID_RE.fullmatch(contract_id)
                or contract_id in conversion_contracts
                or not isinstance(source_extensions, list)
                or not source_extensions
                or not isinstance(target_extensions, list)
                or not target_extensions
                or any(
                    not isinstance(item, str) or not re.fullmatch(r"\.[a-z0-9]+", item)
                    for item in source_extensions + target_extensions
                )
                or not executable.is_absolute()
                or not executable.is_file()
                or not os.access(executable, os.X_OK)
                or sha256_file(executable) != contract.get("executable_sha256")
                or not isinstance(argv_template, list)
                or not argv_template
                or argv_template[0] != str(executable.resolve())
                or sum(str(token).count("{SOURCE}") for token in argv_template) != 1
                or sum(str(token).count("{TARGET}") for token in argv_template) != 1
                or any(
                    not isinstance(contract.get(key), list)
                    for key in (
                        "required_argv_tokens", "success_output_contains", "error_output_contains"
                    )
                )
            ):
                errors.append(f"Invalid asset-conversion contract: {contract_id!r}")
                continue
            conversion_contracts[contract_id] = contract
    except (OSError, ValueError) as exc:
        errors.append(f"Cannot validate asset-conversion contracts: {exc}")
    local_decisions = index_unique_rows(
        read_csv_rows(phase_dir / "nativeization-decisions.csv"),
        "decision_id", "Phase 4 nativeization decisions", errors,
    )
    controller_decisions = index_unique_rows(
        read_csv_rows(run_dir / "controller" / "decision-log.csv"),
        "decision_id", "controller decision log", errors,
    )
    superseded_controller_decisions = {
        row.get("supersedes_id", "") for row in controller_decisions.values() if row.get("supersedes_id")
    }
    used_conversion_ids: set[str] = set()
    if set(migrated_assets) != set(phase2_assets) or set(migrated_assets) != set(phase3_assets):
        errors.append("Phase 4 asset migration does not exactly cover the frozen asset chain")
    for asset_id, row in migrated_assets.items():
        source = phase2_assets.get(asset_id, {})
        placement = phase3_assets.get(asset_id, {})
        source_digest = source.get("sha256") or source.get("source_sha256")
        row_source_digest = row.get("source_sha256") or row.get("asset_sha256")
        target_relative = row.get("target_resource_path") or row.get("target_path")
        target_digest = row.get("target_sha256")
        target = safe_relative_path(project, target_relative, f"Phase 4 asset target {asset_id}", errors)
        mode = row.get("migration_mode")
        verification_evidence_id = row.get("verification_evidence_id", "")
        expected_asset_lock = locked_assets_by_id.get(asset_id, {})
        try:
            expected_archive_relative = Path(
                str(expected_asset_lock.get("snapshot_path", ""))
            ).resolve().relative_to(phase_dir.resolve()).as_posix()
        except (OSError, ValueError):
            expected_archive_relative = ""
        source_features = phase4_json_string_list(
            source.get("feature_ids", "[]"), f"{asset_id}.source_feature_ids", errors
        )
        source_pages = phase4_json_string_list(
            source.get("page_ids", "[]"), f"{asset_id}.source_page_ids", errors
        )
        source_states = phase4_json_string_list(
            source.get("state_ids", "[]"), f"{asset_id}.source_state_ids", errors
        )
        row_features = phase4_json_string_list(
            row.get("feature_ids", "[]"), f"{asset_id}.feature_ids", errors
        )
        row_pages = phase4_json_string_list(
            row.get("page_ids", "[]"), f"{asset_id}.page_ids", errors
        )
        row_states = phase4_json_string_list(
            row.get("state_ids", "[]"), f"{asset_id}.state_ids", errors
        )
        expected_status = {
            "DIRECT_COPY": "DIRECT_COPY_VERIFIED",
            "FORMAT_CONVERSION": "CONVERSION_VERIFIED",
            "RECREATE_FROM_PUBLIC_UI": "RECREATED_VERIFIED",
        }.get(mode)
        if (
            row_source_digest != source_digest
            or row.get("source_path") != source.get("source_path")
            or row.get("archive_relative_path") != expected_archive_relative
            or row.get("file_type") != source.get("asset_type")
            or row_features != source_features
            or row_pages != source_pages
            or row_states != source_states
            or row.get("target_module_id") != placement.get("target_module_id")
            or target_relative != placement.get("target_path")
            or row.get("target_resource_symbol") != placement.get("target_symbol")
            or mode != placement.get("planned_mode")
            or row.get("status") != expected_status
            or row.get("migrated_by") != phase4_ownership.get("visual_asset_agent_id")
            or not target
            or not target.is_file()
            or not SHA256_RE.fullmatch(str(target_digest))
            or sha256_file(target) != target_digest
        ):
            errors.append(f"{asset_id}: final asset bytes, placement, or frozen owner differs")
        if mode == "DIRECT_COPY" and (
            target_digest != source_digest
            or row.get("conversion_record_id")
            or row.get("conversion_record_sha256")
            or verification_evidence_id
            or row.get("nativeization_decision_id")
        ):
            errors.append(f"{asset_id}: DIRECT_COPY bytes or empty audit fields differ")
        elif mode == "FORMAT_CONVERSION":
            conversion_id = row.get("conversion_record_id", "")
            if not ID_RE.fullmatch(conversion_id):
                errors.append(f"{asset_id}: invalid conversion record ID: {conversion_id!r}")
                continue
            if conversion_id in used_conversion_ids:
                errors.append(f"Asset conversion record is reused: {conversion_id}")
            used_conversion_ids.add(conversion_id)
            conversion_dir = phase_dir / "asset-conversions" / conversion_id
            conversion_entries = verify_sealed_package(
                conversion_dir, conversion_id, "PASS", f"asset conversion {conversion_id}", errors
            )
            try:
                metadata_path = conversion_dir / "metadata.json"
                metadata = load_json(metadata_path)
                contract_id = str(metadata.get("contract_id", ""))
                contract = conversion_contracts.get(contract_id, {})
                source_record = metadata.get("source") if isinstance(metadata.get("source"), dict) else {}
                target_record = metadata.get("target") if isinstance(metadata.get("target"), dict) else {}
                command = metadata.get("command") if isinstance(metadata.get("command"), dict) else {}
                success_matches = command.get("success_output_matches")
                error_matches = command.get("error_output_matches")
                argv = command.get("argv")
                source_snapshot = Path(str(source_record.get("snapshot_path", ""))).expanduser().resolve()
                expected_asset_lock = locked_assets_by_id.get(asset_id, {})
                sealed_target = safe_relative_path(
                    conversion_dir,
                    str(target_record.get("sealed_relative_path", "")),
                    f"conversion target {conversion_id}",
                    errors,
                )
                stdout = safe_relative_path(
                    conversion_dir, str(command.get("stdout_path", "")),
                    f"conversion stdout {conversion_id}", errors,
                )
                stderr = safe_relative_path(
                    conversion_dir, str(command.get("stderr_path", "")),
                    f"conversion stderr {conversion_id}", errors,
                )
                source_extension = PurePosixPath(str(source.get("archive_path", ""))).suffix.lower()
                target_extension = PurePosixPath(target_relative).suffix.lower()
                if (
                    set(conversion_entries)
                    != {
                        "metadata.json", f"output/{PurePosixPath(target_relative).name}",
                        "logs/stdout.log", "logs/stderr.log",
                    }
                    or
                    not ID_RE.fullmatch(conversion_id)
                    or row.get("conversion_record_sha256") != sha256_file(metadata_path)
                    or row.get("nativeization_decision_id")
                    or metadata.get("schema_version") != 1
                    or metadata.get("conversion_id") != conversion_id
                    or metadata.get("asset_id") != asset_id
                    or metadata.get("executed_by") != phase4_ownership.get("visual_asset_agent_id")
                    or not metadata.get("executed_at")
                    or metadata.get("status") != "PASS"
                    or metadata.get("input_lock_sha256") != input_lock_sha256
                    or not contract
                    or source_extension not in contract.get("source_extensions", [])
                    or target_extension not in contract.get("target_extensions", [])
                    or str(source_snapshot) != str(expected_asset_lock.get("snapshot_path", ""))
                    or source_record.get("sha256") != source_digest
                    or not source_snapshot.is_file()
                    or sha256_file(source_snapshot) != source_digest
                    or source_record.get("size") != source_snapshot.stat().st_size
                    or source_record.get("extension") != source_extension
                    or target_record.get("project_relative_path") != target_relative
                    or target_record.get("sealed_relative_path") != f"output/{PurePosixPath(target_relative).name}"
                    or not sealed_target
                    or not sealed_target.is_file()
                    or target_record.get("sha256") != target_digest
                    or sha256_file(sealed_target) != target_digest
                    or target_record.get("size") != sealed_target.stat().st_size
                    or target_record.get("extension") != target_extension
                    or command.get("category") != "ASSET_FORMAT_CONVERSION"
                    or command.get("argv_template") != contract.get("argv_template")
                    or command.get("resolved_executable") != contract.get("resolved_executable")
                    or command.get("executable_sha256") != contract.get("executable_sha256")
                    or command.get("required_argv_tokens") != contract.get("required_argv_tokens")
                    or command.get("success_output_contains") != contract.get("success_output_contains")
                    or command.get("error_output_contains") != contract.get("error_output_contains")
                    or success_matches != contract.get("success_output_contains")
                    or not isinstance(error_matches, list)
                    or bool(error_matches)
                    or not isinstance(argv, list)
                    or not argv
                    or argv[0] != contract.get("resolved_executable")
                    or any(
                        token not in command.get("argv_template", [])
                        for token in contract.get("required_argv_tokens", [])
                    )
                    or len(argv) != len(command.get("argv_template", []))
                    or any(
                        actual != planned
                        if planned not in {"{SOURCE}", "{TARGET}"}
                        else (
                            actual != str(source_snapshot)
                            if planned == "{SOURCE}"
                            else PurePosixPath(target_relative).name not in str(actual)
                        )
                        for planned, actual in zip(command.get("argv_template", []), argv)
                    )
                    or not any(str(source_snapshot) in str(token) for token in argv)
                    or not any(PurePosixPath(target_relative).name in str(token) for token in argv)
                    or not command.get("cwd")
                    or command.get("stdout_path") != "logs/stdout.log"
                    or command.get("stderr_path") != "logs/stderr.log"
                    or command.get("command_verdict") != "PASS"
                    or command.get("exit_code") != 0
                    or command.get("timed_out") is not False
                    or command.get("semantic_error") is not False
                    or not stdout
                    or not stderr
                    or not stdout.is_file()
                    or not stderr.is_file()
                    or command.get("stdout_sha256") != sha256_file(stdout)
                    or command.get("stderr_sha256") != sha256_file(stderr)
                    or verification_evidence_id not in used_evidence_ids
                ):
                    errors.append(f"{asset_id}: sealed conversion record, contract, output, or HEVD differs")
                if stdout and stderr and stdout.is_file() and stderr.is_file() and contract:
                    combined = stdout.read_text(encoding="utf-8", errors="replace") + "\n" + stderr.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    successes = [
                        item for item in contract.get("success_output_contains", []) if item in combined
                    ]
                    failures = [
                        item for item in contract.get("error_output_contains", [])
                        if item.lower() in combined.lower()
                    ]
                    if (
                        successes != command.get("success_output_matches")
                        or failures != command.get("error_output_matches")
                        or failures
                    ):
                        errors.append(f"{asset_id}: conversion output verdict differs from sealed logs")
            except (OSError, ValueError) as exc:
                errors.append(f"{asset_id}: cannot validate conversion package: {exc}")
        elif mode == "RECREATE_FROM_PUBLIC_UI":
            decision_id = row.get("nativeization_decision_id", "")
            decision = local_decisions.get(decision_id, {})
            controller_decision_id = decision.get("controller_decision_id", "")
            controller_decision = controller_decisions.get(controller_decision_id, {})
            evidence_row = active_evidence.get(verification_evidence_id, {})
            try:
                feature_ids = set(json.loads(source.get("feature_ids", "[]")))
                page_ids = set(json.loads(source.get("page_ids", "[]")))
                state_ids = set(json.loads(source.get("state_ids", "[]")))
                affected_parity_ids = set(json.loads(decision.get("affected_parity_ids", "[]")))
            except (TypeError, json.JSONDecodeError):
                feature_ids = page_ids = state_ids = affected_parity_ids = set()
            if (
                not decision_id
                or row.get("conversion_record_id")
                or row.get("conversion_record_sha256")
                or decision.get("decision_class") != "ASSET_RECREATION"
                or decision.get("status") != "APPROVED"
                or decision.get("approved_by") != phase4_ownership.get("parity_acceptance_agent_id")
                or not controller_decision
                or controller_decision_id in superseded_controller_decisions
                or controller_decision.get("decided_by") != scope.get("ownership", {}).get("migration_controller_id")
                or verification_evidence_id not in used_evidence_ids
                or evidence_row.get("feature_id") not in feature_ids
                or evidence_row.get("page_id") not in page_ids
                or evidence_row.get("state_id") not in state_ids
                or decision.get("feature_id") != evidence_row.get("feature_id")
                or decision.get("page_id") != evidence_row.get("page_id")
                or decision.get("state_id") != evidence_row.get("state_id")
                or evidence_row.get("parity_id") not in affected_parity_ids
            ):
                errors.append(f"{asset_id}: recreated asset lacks accepted HEVD and live dual approval")
        elif mode not in {"DIRECT_COPY", "FORMAT_CONVERSION", "RECREATE_FROM_PUBLIC_UI"}:
            errors.append(f"{asset_id}: unsupported Phase 4 asset migration mode: {mode!r}")

    referenced_decision_ids: set[str] = set()
    for decision_ids in parity_decision_ids.values():
        referenced_decision_ids.update(decision_ids)
    referenced_decision_ids.update(
        row.get("nativeization_decision_id", "")
        for row in migrated_assets.values() if row.get("nativeization_decision_id")
    )
    if set(local_decisions) != referenced_decision_ids:
        errors.append("Phase 4 nativeization decisions are missing, unused, or not parity-bound")
    for decision_id, decision in local_decisions.items():
        affected = phase4_json_string_list(
            decision.get("affected_parity_ids", "[]"),
            f"{decision_id}.affected_parity_ids", errors, allow_empty=False,
        )
        invariants = phase4_json_string_list(
            decision.get("invariants", "[]"), f"{decision_id}.invariants", errors,
            allow_empty=False,
        )
        controller_decision_id = str(decision.get("controller_decision_id", ""))
        controller_decision = controller_decisions.get(controller_decision_id, {})
        bound_rows = [parity.get(parity_id, {}) for parity_id in affected]
        if (
            decision.get("decision_class") != "PLATFORM_VISUAL"
            or decision.get("status") != "APPROVED"
            or decision.get("approved_by") != phase4_ownership.get("parity_acceptance_agent_id")
            or not decision.get("approved_at")
            or not str(decision.get("android_behavior", "")).strip()
            or not str(decision.get("harmony_behavior", "")).strip()
            or not str(decision.get("reason", "")).strip()
            or not invariants
            or any(not row for row in bound_rows)
            or any(row.get("feature_id") != decision.get("feature_id") for row in bound_rows)
            or any(row.get("page_id") != decision.get("page_id") for row in bound_rows)
            or any(row.get("state_id") != decision.get("state_id") for row in bound_rows)
            or decision.get("android_evidence_id")
            not in {row.get("android_evidence_id") for row in bound_rows}
            or not controller_decision
            or controller_decision_id in superseded_controller_decisions
            or controller_decision.get("decided_by")
            != scope.get("ownership", {}).get("migration_controller_id")
            or not str(controller_decision.get("decision", "")).strip()
            or not str(controller_decision.get("rationale", "")).strip()
        ):
            errors.append(f"{decision_id}: nativeization decision lacks complete parity-bound dual approval")

    conversion_root = phase_dir / "asset-conversions"
    conversion_children = list(conversion_root.iterdir()) if conversion_root.is_dir() else []
    actual_conversion_ids = {
        path.name for path in conversion_children
        if path.is_dir() and not path.is_symlink()
    }
    if not conversion_root.is_dir() or actual_conversion_ids != used_conversion_ids or any(
        path.is_file() or path.is_symlink() for path in conversion_children
    ):
        errors.append("Phase 4 asset-conversion packages do not exactly match converted assets")

    # Enforce the source-first asset policy independently of the mutable policy file.
    try:
        asset_policy = load_json(phase_dir / "asset-policy.json")
        required_policy_keys = {
            "policy_version", "direct_copy_hash_required", "format_conversion_requires_command",
            "native_system_resource_requires_decision", "unregistered_project_visuals_allowed",
            "allowed_visual_extensions", "allowed_untracked_visual_paths", "forbidden_inline_glyphs",
            "forbidden_implementation_tokens", "mp4_allowed",
        }
        required_forbidden_tokens = {"TODO", "FIXME", "MOCK_ONLY", "STUB_ONLY", "FAKE_DATA"}
        visual_extensions = asset_policy.get("allowed_visual_extensions")
        forbidden_tokens = asset_policy.get("forbidden_implementation_tokens")
        forbidden_glyphs = asset_policy.get("forbidden_inline_glyphs")
        if (
            set(asset_policy) != required_policy_keys
            or asset_policy.get("policy_version") != 1
            or asset_policy.get("direct_copy_hash_required") is not True
            or asset_policy.get("format_conversion_requires_command") is not True
            or asset_policy.get("native_system_resource_requires_decision") is not True
            or asset_policy.get("unregistered_project_visuals_allowed") is not False
            or asset_policy.get("allowed_untracked_visual_paths") != []
            or asset_policy.get("mp4_allowed") is not False
            or not isinstance(visual_extensions, list)
            or not visual_extensions
            or any(not isinstance(item, str) or not re.fullmatch(r"\.[a-z0-9]+", item) for item in visual_extensions)
            or not isinstance(forbidden_tokens, list)
            or not required_forbidden_tokens <= set(forbidden_tokens)
            or not isinstance(forbidden_glyphs, list)
            or not forbidden_glyphs
        ):
            errors.append("Phase 4 asset policy is weakened or malformed")
            visual_extensions = []
            forbidden_tokens = sorted(required_forbidden_tokens)
            forbidden_glyphs = ["✓", "✔"]
        registered_visual_paths = {
            str(row.get("target_resource_path") or row.get("target_path") or "")
            for row in migrated_assets.values()
        }
        initial_project_snapshot = load_json(phase_dir / "initial-project-snapshot.json")
        initial_visual_paths = {
            str(entry.get("path", ""))
            for entry in initial_project_snapshot.get("entries", [])
            if isinstance(entry, dict)
            and Path(str(entry.get("path", ""))).suffix.lower() in set(visual_extensions)
        }
        allowed_visual_paths = registered_visual_paths | initial_visual_paths
        actual_visual_paths: set[str] = set()
        source_extensions = {".ets", ".ts", ".js", ".json", ".json5", ".c", ".cc", ".cpp", ".h", ".hpp"}
        for path in project.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(project)
            if any(part in STAGE4_PROJECT_EXCLUDED_PARTS for part in relative.parts):
                continue
            suffix = path.suffix.lower()
            if suffix in set(visual_extensions):
                actual_visual_paths.add(relative.as_posix())
            if suffix in source_extensions:
                text = path.read_text(encoding="utf-8", errors="replace")
                for token in set(forbidden_tokens) | required_forbidden_tokens:
                    if token and token in text:
                        errors.append(f"Production source contains forbidden token {token!r}: {relative}")
                for glyph in forbidden_glyphs:
                    if isinstance(glyph, str) and glyph and glyph in text:
                        errors.append(f"Production source contains forbidden inline glyph {glyph!r}: {relative}")
        if actual_visual_paths != allowed_visual_paths:
            errors.append(
                "Project visual files differ from the frozen template baseline and asset migration registry; "
                f"missing={sorted(allowed_visual_paths - actual_visual_paths)[:5]}, "
                f"extra={sorted(actual_visual_paths - allowed_visual_paths)[:5]}"
            )
    except (OSError, ValueError) as exc:
        errors.append(f"Cannot validate the Phase 4 asset policy/project scan: {exc}")

    capability_source = index_unique_rows(
        read_csv_rows(run_dir / "phase-03-harmony-scaffold" / "capability-contracts.csv"),
        "capability_requirement_id", "Phase 3 capability contracts", errors,
    )
    capability = index_unique_rows(
        read_csv_rows(phase_dir / "capability-implementation.csv"),
        "capability_requirement_id", "Phase 4 capability implementation", errors,
    )
    if set(capability) != set(capability_source):
        errors.append("Phase 4 capability implementation coverage differs from Phase 3")
    for requirement_id, row in capability.items():
        source_contract = capability_source.get(requirement_id, {})
        try:
            evidence_ids = json.loads(row.get("verification_evidence_ids", ""))
        except json.JSONDecodeError:
            evidence_ids = None
        feature_id = str(row.get("feature_id", ""))
        feature_order = feature_orders.get(feature_id, {})
        feature_ownership = (
            feature_order.get("ownership")
            if isinstance(feature_order.get("ownership"), dict)
            else {}
        )
        implementation_file = safe_relative_path(
            project, row.get("implementation_file", ""),
            f"capability implementation {requirement_id}", errors,
        )
        implementation_symbol = str(row.get("implementation_symbol", ""))
        implementation_text = (
            implementation_file.read_text(encoding="utf-8", errors="replace")
            if implementation_file and implementation_file.is_file() else ""
        )
        if (
            row.get("status") != "IMPLEMENTED"
            or row.get("capability_contract_id") != source_contract.get("capability_contract_id")
            or feature_id != source_contract.get("source_feature_id")
            or row.get("harmony_module_id") != source_contract.get("harmony_module_id")
            or row.get("contract_file") != source_contract.get("contract_file")
            or row.get("contract_symbol") != source_contract.get("contract_symbol")
            or row.get("implemented_by") != (
                capability_order_owners.get(requirement_id)
                if page_order_mode
                else feature_ownership.get("native_capability_agent_id")
            )
            or row.get("implemented_by") in forbidden_implementers
            or not isinstance(evidence_ids, list)
            or not evidence_ids
            or evidence_ids != sorted(set(evidence_ids))
            or any(item not in used_evidence_ids for item in evidence_ids)
            or not set(evidence_ids) <= capability_assertion_evidence.get(requirement_id, set())
            or not implementation_file
            or not implementation_file.is_file()
            or not implementation_symbol
            or not re.search(rf"\b{re.escape(implementation_symbol)}\b", implementation_text)
        ):
            errors.append(f"{requirement_id}: capability implementation, actor, source, or evidence differs")

    # Local Phase 4 rework and the controller mirror must be an exact closed double ledger.
    local_rework = read_csv_rows(phase_dir / "rework-tickets.csv")
    controller_rework = [
        row for row in read_csv_rows(run_dir / "controller" / "rework-log.csv")
        if row.get("phase") == "4"
    ]
    local_ids = [row.get("ticket_id", "") for row in local_rework]
    controller_ids = [row.get("rework_id", "") for row in controller_rework]
    if (
        len(local_ids) != len(set(local_ids))
        or len(controller_ids) != len(set(controller_ids))
        or set(local_ids) != set(controller_ids)
    ):
        errors.append("Phase 4 rework ledger and controller mirror contain different or duplicate Ticket-IDs")
    allowed_responsible = prior_actor_ids | {
        phase4_ownership.get("implementation_lead_id"),
        phase4_ownership.get("visual_asset_agent_id"),
        phase4_ownership.get("verification_executor_id"),
    }
    for actors in feature_actor_ids.values():
        allowed_responsible.update(actors)
    for local in local_rework:
        ticket_id = local.get("ticket_id", "")
        matches = [row for row in controller_rework if row.get("rework_id") == ticket_id]
        if (
            not ID_RE.fullmatch(ticket_id)
            or local.get("status") != "CLOSED"
            or local.get("opened_by") != phase4_ownership.get("parity_acceptance_agent_id")
            or local.get("closed_by") != phase4_ownership.get("parity_acceptance_agent_id")
            or local.get("responsible_agent") not in allowed_responsible
            or local.get("responsible_agent") == phase4_ownership.get("parity_acceptance_agent_id")
            or local.get("resolution_verification_id") not in used_evidence_ids
            or len(matches) != 1
        ):
            errors.append(f"Phase 4 rework authority or lifecycle is invalid: {ticket_id!r}")
            continue
        mirrored = matches[0]
        expected_fields = {
            "created_at": local.get("opened_at", ""),
            "record_id": local.get("record_id", ""),
            "evidence_id": local.get("failed_verification_id", ""),
            "gate_rule": local.get("problem_type", ""),
            "reason": local.get("notes", ""),
            "assigned_to": local.get("responsible_agent", ""),
            "completion_condition": local.get("completion_condition", ""),
            "status": "CLOSED",
            "resolved_at": local.get("closed_at", ""),
            "resolution_evidence_id": local.get("resolution_verification_id", ""),
            "reviewed_by": local.get("closed_by", ""),
        }
        if any(mirrored.get(field, "") != value for field, value in expected_fields.items()):
            errors.append(f"Phase 4 controller rework mirror differs: {ticket_id}")
    if any(row.get("status") != "CLOSED" for row in controller_rework):
        errors.append("Controller has open Phase 4 rework")

    # The final independent report is itself bound by CLOSED; verify its reviewer and summaries.
    report_counts = stage_report.get("counts") if isinstance(stage_report.get("counts"), dict) else {}
    expected_counts = {
        "features": len(included_features) if page_order_mode else len(implementation),
        "parity_rows": len(parity),
        "active_evidence": len(used_evidence_ids),
        "assets": len(migrated_assets),
        "capabilities": len(capability),
        "nativeization_decisions": len(local_decisions),
        "open_rework": 0,
    }
    if any(report_counts.get(key) != value for key, value in expected_counts.items()):
        errors.append("Phase 4 report counts differ from the sealed ledgers")
    report_artifacts = stage_report.get("artifact_hashes")
    if (
        not isinstance(report_artifacts, list)
        or sorted(report_artifacts) != sorted(artifact_hashes)
    ):
        errors.append("Phase 4 report artifact hashes differ from final HBUILD packages")
    if (
        stage_report.get("phase") != 4
        or stage_report.get("run_id") != scope.get("run_id")
        or stage_report.get("verdict") != "PASS"
        or stage_report.get("final_verdict") != "PASS"
        or stage_report.get("implementation_chain_closed") is not True
        or stage_report.get("reviewer_role") != "parity-acceptance-agent"
        or stage_report.get("reviewer_id") != phase4_ownership.get("parity_acceptance_agent_id")
        or stage_report.get("work_order_id") != work_order_id
        or stage_report.get("input_lock_sha256") != input_lock_sha256
        or stage_report.get("source_snapshot_sha256") != source_snapshot_sha256
        or stage_report.get("build_ids") != sorted(build_ids)
        or stage_report.get("errors") != []
    ):
        errors.append("Phase 4 final report identity, reviewer, snapshot, or verdict is invalid")

    ledger_rows = read_csv_rows(run_dir / "controller" / "task-ledger.csv")
    phase3_tasks = [row for row in ledger_rows if row.get("phase") == "3"]
    phase4_tasks = [row for row in ledger_rows if row.get("phase") == "4"]
    if (
        len(phase3_tasks) != 1
        or phase3_tasks[0].get("status") != "PASS"
        or phase3_tasks[0].get("owner") != phase3_ownership.get("architecture_lead_id")
    ):
        errors.append("Controller task ledger does not retain the frozen Phase 3 PASS")
    if (
        len(phase4_tasks) != 1
        or phase4_tasks[0].get("status") not in {"IN_PROGRESS", "PASS"}
        or phase4_tasks[0].get("owner") != phase4_ownership.get("implementation_lead_id")
    ):
        errors.append("Controller task ledger does not have the assigned Phase 4 task")

    return (
        errors,
        warnings,
        sorted(build_ids),
        sorted(used_evidence_ids),
        str(phase4_ownership.get("implementation_lead_id") or "") or None,
        work_order_id if ID_RE.fullmatch(work_order_id) else None,
    )


def atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"Refusing to replace symbolic-link target: {path}")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def update_task_ledger(
    run_dir: Path,
    phase: int,
    verdict: str,
    errors: list[str],
    ownership: dict[str, Any],
    phase3_owner: str | None = None,
    phase4_owner: str | None = None,
) -> None:
    path = run_dir / "controller" / "task-ledger.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    target = str(phase)
    matches = [row for row in rows if row.get("phase") == target]
    if len(matches) != 1 or not fieldnames:
        raise ValueError(f"Task ledger has no unique phase {phase} row")
    matches[0]["status"] = verdict
    matches[0]["updated_at"] = utc_now()
    matches[0]["notes"] = "; ".join(errors[:3])
    for row in rows:
        if row.get("phase") == "1":
            row["owner"] = str(ownership.get("migration_controller_id", row.get("owner", "")))
        elif row.get("phase") == "2":
            row["owner"] = str(ownership.get("inventory_lead_id", row.get("owner", "")))
        elif row.get("phase") == "3" and phase3_owner:
            row["owner"] = phase3_owner
        elif row.get("phase") == "4" and phase4_owner:
            row["owner"] = phase4_owner
    if path.is_symlink():
        raise ValueError(f"Refusing symbolic-link task ledger: {path}")
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


def _gmi_run(run_dir: Path) -> bool:
    """gmi 模式判别（与 init_scaffold is_gmi_phase2 一致）：
    phase-02-android-inventory/phase-manifest.json 存在 generator==gmi，
    或 gmi/phase-2-closure.json 存在。"""
    p2 = run_dir / "phase-02-android-inventory"
    gmi_closure = p2 / "gmi" / "phase-2-closure.json"
    if gmi_closure.exists():
        return True
    try:
        pf = json.loads((p2 / "phase-manifest.json").read_text(encoding="utf-8"))
        return str(pf.get("generator", "")).startswith("gmi")
    except (ValueError, OSError):
        return False


GMI_REQUIRED_CANDIDATES = (
    "code-map.candidates.full.csv", "business-rules.candidates.csv",
    "asset-mapping.candidates.csv", "inventory.candidates.csv",
    "page-fields.candidates.csv", "third-party-dependencies.candidates.csv",
    "field-options.candidates.csv", "navigation-relations.candidates.csv",
    "behavior.candidates.csv", "risk-probes.candidates.csv",
    "color-palette.candidates.csv", "motion.candidates.csv",
    "phase-2-completeness.csv",
)


def _gmi_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        return load_json(path)
    except ValueError as exc:
        errors.append(str(exc))
        return {}


def _gmi_seal(report: Path, closed: Path, label: str, errors: list[str]) -> None:
    if not report.is_file() or not closed.is_file():
        errors.append(f"{label} report/CLOSED missing")
        return
    expected = closed.read_text(encoding="utf-8", errors="replace").strip().split()[0]
    if not SHA256_RE.fullmatch(expected) or expected != sha256_file(report):
        errors.append(f"{label} CLOSED does not bind the gate report")


def _gmi_phase2_inputs(run_dir: Path, errors: list[str]) -> tuple[dict[str, Any], Path, Path, Path]:
    """Resolve both native p2/gmi and adapter-produced run-root GMI layouts."""
    p2 = run_dir / "phase-02-android-inventory"
    nested = p2 / "gmi"
    nested_closure = nested / "phase-2-closure.json"
    if nested_closure.is_file():
        return (
            _gmi_json(nested_closure, errors),
            nested / "candidates",
            nested / "coverage",
            nested / "runtime-evidence",
        )

    # 原生独立布局：closure 写在 phase-02-android-inventory 根（gmi_closure 默认输出）
    p2_closure = p2 / "phase-2-closure.json"
    if p2_closure.is_file():
        rt_dir = p2 / "runtime-evidence"
        if not rt_dir.is_dir():
            rt_dir = nested / "runtime-evidence"
        return (
            _gmi_json(p2_closure, errors),
            p2 / "candidates",
            p2 / "coverage",
            rt_dir,
        )


    closure_report = _gmi_json(p2 / "closure-report.json", errors)
    embedded = closure_report.get("gmi_closure")
    closure = {"gate": embedded} if isinstance(embedded, dict) else {}
    if not closure:
        errors.append("gmi phase-2 closure is missing (neither p2/gmi certificate nor embedded adapter gate)")
    candidates = run_dir / "candidates"
    if not candidates.is_dir():
        candidates = p2 / "candidates"
    return closure, candidates, run_dir / "coverage", run_dir / "runtime-evidence"


def _validate_gmi_phase2(run_dir: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    closure, candidates, coverage_dir, runtime = _gmi_phase2_inputs(run_dir, errors)
    gate = closure.get("gate") if isinstance(closure.get("gate"), dict) else {}
    if not gate:
        errors.append("gmi closure gate is missing")
    else:
        if gate.get("unmapped") != 0:
            errors.append(f"gmi closure UNMAPPED={gate.get('unmapped')} (must be 0)")
        # gmi_closure.py 只产出布尔 audit_passed（无 audit_discrepancy 字段），
        # 主判定以 audit_passed 为准；数值型 audit_discrepancy 非 0 时仍报错以兼容旧布局
        if gate.get("audit_passed") is not True:
            errors.append(f"gmi closure audit_passed={gate.get('audit_passed')!r} (must be true)")
        legacy_discrepancy = gate.get("audit_discrepancy")
        if (
            isinstance(legacy_discrepancy, (int, float))
            and not isinstance(legacy_discrepancy, bool)
            and legacy_discrepancy != 0
        ):
            errors.append(f"gmi closure audit_discrepancy={legacy_discrepancy} (must be 0)")

    for name in GMI_REQUIRED_CANDIDATES:
        path = candidates / name
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"gmi candidate table missing/empty: {name}")
    if not (candidates / "manifest.sha256").is_file():
        errors.append("gmi candidates manifest.sha256 missing")

    coverage = coverage_dir / "coverage-ledger.csv"
    if not coverage.is_file():
        errors.append("gmi coverage-ledger.csv missing")
    else:
        gaps = [
            row for row in read_csv_rows(coverage)
            if str(row.get("status", "")).upper() in {"GAP", "UNMAPPED"}
            or str(row.get("disposition", "")).upper() == "UNMAPPED"
        ]
        if gaps:
            errors.append(f"gmi coverage has {len(gaps)} unmapped/gap rows")

    for name in ("runtime-gate.csv", "audit-replay.csv"):
        if not (runtime / name).is_file():
            errors.append(f"gmi runtime evidence missing: {name}")
    audit_rows = read_csv_rows(runtime / "audit-replay.csv")
    discrepancies = [row for row in audit_rows if str(row.get("discrepancy", "")).upper() == "YES"]
    if discrepancies:
        errors.append(f"gmi audit replay discrepancies={len(discrepancies)}")
    return errors, warnings


def _validate_gmi_phase3(run_dir: Path) -> tuple[list[str], list[str]]:
    errors, warnings = _validate_gmi_phase2(run_dir)
    p2 = run_dir / "phase-02-android-inventory"
    p3 = run_dir / "phase-03-harmony-scaffold"
    report = _gmi_json(p3 / "stage-03-gate-report.json", errors)
    if str(report.get("verdict", report.get("final_verdict", ""))).upper() != "PASS":
        errors.append("gmi stage-03 gate is not PASS")
    _gmi_seal(p3 / "stage-03-gate-report.json", p3 / "CLOSED", "gmi stage-03", errors)
    if not (p3 / "stage-03-closure-manifest.sha256").is_file():
        errors.append("gmi stage-03 closure manifest missing")

    inventory_ids = {row.get("inventory_id", "") for row in read_csv_rows(p2 / "inventory.csv") if row.get("inventory_id")}
    mapped_ids = {row.get("inventory_id", "") for row in read_csv_rows(p3 / "architecture-map.csv") if row.get("inventory_id")}
    missing = sorted(inventory_ids - mapped_ids)
    if missing:
        errors.append(f"gmi stage-03 architecture mappings missing={missing[:5]}")
    modules = read_csv_rows(p3 / "module-registry.csv")
    if not modules or any(str(row.get("status", "")).upper() != "READY" for row in modules):
        errors.append("gmi stage-03 module registry is missing or not READY")
    return errors, warnings


def _gmi_code_paths(raw: str) -> list[str]:
    try:
        values = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(value) for value in values] if isinstance(values, list) else []


def _gmi_placeholder_page(path: Path, page_id: str) -> str:
    """Detect the exact low-information shells CodeArts previously emitted as page implementations."""
    text = path.read_text(encoding="utf-8", errors="replace")
    compact = re.sub(r"\s+", " ", text)
    if "ArkUI migration scaffold" in text:
        return "generic ArkUI scaffold marker"
    rendered_identity = bool(re.search(r"Text\s*\(\s*['\"](?:ROUTE-|PAGE-)", text))
    ui_calls = len(re.findall(
        r"\b(?:Text|Button|Image|TextInput|TextArea|List|Grid|Web|Checkbox|Radio|Toggle|Switch|"
        r"Scroll|Column|Row|Stack)\s*\(", text
    ))
    if rendered_identity and ui_calls <= 8:
        return "renders Route-ID/Page-ID instead of the frozen page UI"
    if (
        re.search(r"Button\s*\(\s*['\"]Back['\"]\s*\)", compact)
        and ui_calls <= 8
        and not re.search(r"\b(?:TextInput|TextArea|List|Grid|Web|Checkbox|Radio|Toggle|Switch)\s*\(", text)
    ):
        return "Back-only navigation shell"
    return ""


def _validate_gmi_phase4_outputs(run_dir: Path, inventory_rows: list[dict[str, str]]) -> list[str]:
    """Recompute CodeArts-critical P4 invariants without trusting a model-authored PASS report."""
    errors: list[str] = []
    p4 = run_dir / "phase-04-harmony-implementation"
    inventory_ids = {row.get("inventory_id", "") for row in inventory_rows if row.get("inventory_id")}
    inventory_pages = {row.get("page_id", "") for row in inventory_rows if row.get("page_id")}

    implementation = read_csv_rows(p4 / "page-implementation-ledger.csv")
    impl_by_page = {row.get("page_id", ""): row for row in implementation if row.get("page_id")}
    if set(impl_by_page) != inventory_pages:
        errors.append("gmi stage-04 implementation ledger does not exactly cover inventory pages")
    for page_id in sorted(inventory_pages):
        row = impl_by_page.get(page_id, {})
        if row.get("status") != "ACCEPTED":
            errors.append(f"gmi stage-04 page implementation is not ACCEPTED: {page_id}")
            continue
        paths = _gmi_code_paths(row.get("exclusive_code_paths", ""))
        if not paths:
            errors.append(f"gmi stage-04 page has no exclusive ArkTS path: {page_id}")
            continue
        for relative in paths:
            pure = PurePosixPath(relative)
            if (
                pure.is_absolute() or ".." in pure.parts
                or not relative.startswith("harmony-project/")
                or pure.suffix.lower() not in {".ets", ".ts"}
            ):
                errors.append(f"gmi stage-04 page has unsafe/non-ArkTS code path: {page_id}: {relative}")
                continue
            source = p4 / Path(*pure.parts)
            if not source.is_file():
                errors.append(f"gmi stage-04 page source is missing: {page_id}: {relative}")
                continue
            reason = _gmi_placeholder_page(source, page_id)
            if reason:
                errors.append(f"gmi stage-04 placeholder page rejected: {page_id}: {reason}")

    parity = [row for row in read_csv_rows(p4 / "parity-map.csv") if row.get("status") != "SUPERSEDED"]
    parity_ids = {row.get("parity_id", "") for row in parity if row.get("parity_id")}
    if {row.get("inventory_id", "") for row in parity} != inventory_ids or any(
        row.get("status") != "ACCEPTED" for row in parity
    ):
        errors.append("gmi stage-04 parity map is incomplete or not ACCEPTED")

    evidence = [row for row in read_csv_rows(p4 / "evidence-index.csv") if row.get("status") == "SEALED"]
    evidence_by_parity = {row.get("parity_id", ""): row for row in evidence if row.get("parity_id")}
    if (
        set(evidence_by_parity) != parity_ids
        or {row.get("inventory_id", "") for row in evidence} != inventory_ids
        or len(evidence_by_parity) != len(evidence)
    ):
        errors.append("gmi stage-04 sealed evidence does not exactly cover parity rows")
    screenshot_targets: dict[str, set[tuple[str, str]]] = {}
    for parity_id, row in evidence_by_parity.items():
        relative = row.get("relative_path", "")
        evidence_dir = safe_relative_path(p4, relative, f"gmi HEVD {row.get('evidence_id', '')}", errors)
        if not evidence_dir or not evidence_dir.is_dir():
            continue
        required = (
            "manifest.sha256", "COMMITTED", "metadata.json", "screenshot.png", "assertions.json",
            "ui-test-snapshot.json", "ui-test-snapshot-metadata.json", "ui-test-snapshot-operation-trace.json",
            "ui-test-snapshot.png", "uitest-test.hap",
        )
        missing = [name for name in required if not (evidence_dir / name).is_file()]
        if missing:
            errors.append(f"gmi stage-04 evidence package is incomplete: {parity_id}: {missing[:4]}")
            continue
        try:
            verify_sealed_package(
                evidence_dir, str(row.get("evidence_id", "")), "SEALED",
                f"gmi HEVD {row.get('evidence_id', '')}", errors,
            )
            metadata = _gmi_json(evidence_dir / "metadata.json", errors)
            if (
                metadata.get("status") != "SEALED"
                or metadata.get("evidence_id") != row.get("evidence_id")
                or metadata.get("parity_id") != parity_id
                or metadata.get("page_id") != row.get("page_id")
                or metadata.get("state_id") != row.get("state_id")
            ):
                errors.append(f"gmi stage-04 evidence metadata is not sealed/bound: {parity_id}")
            validate_complete_png(evidence_dir / "screenshot.png")
            screenshot_hash = sha256_file(evidence_dir / "screenshot.png")
            if row.get("screenshot_sha256") != screenshot_hash:
                errors.append(f"gmi stage-04 screenshot hash differs: {parity_id}")
            parity_row = next((item for item in parity if item.get("parity_id") == parity_id), {})
            target = (parity_row.get("page_id", ""), parity_row.get("state_id", ""))
            screenshot_targets.setdefault(screenshot_hash, set()).add(target)
        except ValueError as exc:
            errors.append(str(exc))
    for digest, targets in screenshot_targets.items():
        if len(targets) > 1:
            errors.append(f"gmi stage-04 screenshot reused across page/state targets: {digest}: {sorted(targets)}")

    reviews = [row for row in read_csv_rows(p4 / "acceptance-ledger.csv") if row.get("status") != "SUPERSEDED"]
    review_parity = [row.get("parity_id", "") for row in reviews]
    if (
        set(review_parity) != parity_ids
        or {row.get("inventory_id", "") for row in reviews} != inventory_ids
        or len(review_parity) != len(set(review_parity))
        or any(row.get("status") != "ACCEPTED" for row in reviews)
    ):
        errors.append("gmi stage-04 acceptance ledger lacks exactly one ACCEPTED review per parity row")
    verify_phase4_closure(p4, errors)
    return errors


def _validate_gmi_phase4(run_dir: Path) -> tuple[list[str], list[str]]:
    errors, warnings = _validate_gmi_phase3(run_dir)
    p2 = run_dir / "phase-02-android-inventory"
    p4 = run_dir / "phase-04-harmony-implementation"
    report = _gmi_json(p4 / "stage-04-gate-report.json", errors)
    if str(report.get("verdict", report.get("final_verdict", ""))).upper() != "PASS":
        errors.append("gmi stage-04 gate is not PASS")
    _gmi_seal(p4 / "stage-04-gate-report.json", p4 / "CLOSED", "gmi stage-04", errors)
    if not (p4 / "stage-04-closure-manifest.sha256").is_file():
        errors.append("gmi stage-04 closure manifest missing")

    inventory_rows = read_csv_rows(p2 / "inventory.csv")
    inventory_pages = {row.get("page_id", "") for row in inventory_rows if row.get("page_id")}
    registry = read_csv_rows(p4 / "page-contract-registry.csv")
    contract_pages = {row.get("page_id", "") for row in registry if row.get("page_id")}
    if contract_pages != inventory_pages:
        errors.append(
            f"gmi stage-04 page contract coverage differs: "
            f"missing={sorted(inventory_pages - contract_pages)[:5]}, extra={sorted(contract_pages - inventory_pages)[:5]}"
        )
    layout_errors: list[str] = []
    _, candidates, _, _ = _gmi_phase2_inputs(run_dir, layout_errors)
    behavior_pages = {
        row.get("page_id", "") for row in read_csv_rows(candidates / "behavior.candidates.csv")
        if row.get("page_id")
    }
    for row in registry:
        page_id = row.get("page_id", "")
        relative = row.get("relative_path", "")
        expected_relative = f"page-contracts/{page_id}.json"
        if relative != expected_relative:
            errors.append(f"gmi stage-04 contract path differs for {page_id}: {relative}")
            contract = {}
        else:
            contract = _gmi_json(p4 / expected_relative, errors)
        components = contract.get("components")
        if not isinstance(components, list) or not components:
            errors.append(f"gmi stage-04 contract has no components: {page_id}")
        bindings = contract.get("behavior_bindings")
        if page_id in behavior_pages and (not isinstance(bindings, list) or not bindings):
            errors.append(f"gmi stage-04 contract lost behavior bindings: {page_id}")
    pending = [row for row in read_csv_rows(p2 / "evidence-index.csv") if row.get("status") == "PENDING_RUNTIME_VERIFY"]
    if pending:
        errors.append(f"gmi stage-04 still has PENDING_RUNTIME_VERIFY pages={len(pending)}")
    errors.extend(_validate_gmi_phase4_outputs(run_dir, inventory_rows))
    return errors, warnings


def _validate_gmi_equivalent(run_dir: Path, phase: int) -> tuple[list[str], list[str]]:
    if phase == 2:
        return _validate_gmi_phase2(run_dir)
    if phase == 3:
        return _validate_gmi_phase3(run_dir)
    if phase >= 4:
        return _validate_gmi_phase4(run_dir)
    return [], []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--phase", required=True, type=int, choices=(1, 2, 3, 4))
    parser.add_argument("--write", action="store_true", help="Update controller/gate-report.json")
    args = parser.parse_args()

    run_input = Path(args.run_dir).expanduser().absolute()
    if run_input.is_symlink():
        parser.error("Migration run must not be a symbolic link")
    run_dir = run_input.resolve()
    _gmi_mode = _gmi_run(run_dir)
    try:
        scope = load_json(run_dir / "controller" / "scope.json")
        errors, warnings, baseline_env_id, facts = validate_phase1(run_dir, scope)
    except ValueError as exc:
        errors, warnings, baseline_env_id, facts = [str(exc)], [], None, {}

    if args.phase in {2, 3, 4} and not errors and _gmi_run(run_dir):
        # gmi 模式：legacy 校验仅对旧证据链有效；对 gmi run 使用等价门禁
        # （phase-2-closure.json + coverage UNMAPPED=0 + audit 0 discrepancy）
        gel, gew = _validate_gmi_equivalent(run_dir, args.phase)
        errors.extend(gel)
        warnings.extend(gew)

    if args.phase in {2, 3, 4} and not errors and not _gmi_mode:
        phase_errors, phase_warnings = validate_phase2(run_dir, scope, baseline_env_id, facts)
        errors.extend(phase_errors)
        warnings.extend(phase_warnings)

    harmony_environment_id = None
    verification_id = None
    phase3_owner = None
    phase3_work_order_id = None
    if args.phase in {3, 4} and not errors and not _gmi_mode:
        (
            phase_errors,
            phase_warnings,
            harmony_environment_id,
            verification_id,
            phase3_owner,
            phase3_work_order_id,
        ) = validate_phase3(run_dir, scope, facts)
        errors.extend(phase_errors)
        warnings.extend(phase_warnings)

    harmony_build_ids: list[str] = []
    harmony_evidence_ids: list[str] = []
    phase4_owner = None
    phase4_work_order_id = None
    if args.phase == 4 and not errors and not _gmi_mode:
        (
            phase_errors,
            phase_warnings,
            harmony_build_ids,
            harmony_evidence_ids,
            phase4_owner,
            phase4_work_order_id,
        ) = validate_phase4(run_dir, scope, facts)
        errors.extend(phase_errors)
        warnings.extend(phase_warnings)

    report = {
        "run_id": scope.get("run_id") if "scope" in locals() else None,
        "phase": args.phase,
        "verdict": "PASS" if not errors else "FAIL",
        "baseline_env_id": baseline_env_id,
        "harmony_environment_id": harmony_environment_id,
        "verification_id": verification_id,
        "phase3_work_order_id": phase3_work_order_id,
        "phase4_work_order_id": phase4_work_order_id,
        "harmony_build_ids": harmony_build_ids,
        "harmony_evidence_ids": harmony_evidence_ids,
        "scope_sha256": facts.get("scope_sha256"),
        "run_manifest_sha256": facts.get("run_manifest_sha256"),
        "source_revision": facts.get("source_revision"),
        "apk_sha256": facts.get("apk_sha256"),
        "included_features": facts.get("included_features", []),
        "checked_at": utc_now(),
        "errors": errors,
        "warnings": warnings,
    }
    if args.write:
        try:
            atomic_json(run_dir / "controller" / "gate-report.json", report)
            update_task_ledger(
                run_dir,
                args.phase,
                report["verdict"],
                errors,
                scope.get("ownership", {}),
                phase3_owner,
                phase4_owner,
            )
        except ValueError as exc:
            parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
