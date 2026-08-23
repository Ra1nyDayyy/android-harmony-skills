#!/usr/bin/env python3
"""Initialize Phase 2 after validating Android CLI and the frozen controller scope."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from _common import (
    atomic_json,
    atomic_text,
    load_json,
    read_csv,
    require_success,
    resolve_executable,
    run_command,
    sha256_file,
    utc_now,
    validate_id,
    write_csv,
)


SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSETS = SKILL_ROOT / "assets"
PHASE_NAME = "phase-02-android-inventory"


def validate_scope(scope: dict) -> tuple[list[dict], str, dict, list[str]]:
    policy = scope.get("tool_policy", {})
    if policy.get("runtime_ui_tool") != "android-cli" or policy.get("layout_inspector_allowed") is not False:
        raise ValueError("Scope must require android-cli and prohibit Layout Inspector")
    environments = scope.get("environments")
    if not isinstance(environments, list) or not environments:
        raise ValueError("Scope has no environments")
    baseline = [env for env in environments if isinstance(env, dict) and env.get("is_baseline") is True]
    if len(baseline) != 1:
        raise ValueError("Scope must contain exactly one baseline environment")
    for env in environments:
        validate_id(env.get("env_id", ""), "ENV-ID")
    ownership = scope.get("ownership")
    if not isinstance(ownership, dict):
        raise ValueError("Scope has no frozen ownership table")
    required_roles = (
        "migration_controller_id", "inventory_lead_id", "code_map_agent_id", "business_rule_agent_id",
        "data_dependency_agent_id", "evidence_administrator_id", "coverage_checker_id",
    )
    role_ids = []
    for role in required_roles:
        value = ownership.get(role)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Scope ownership is missing {role}")
        role_ids.append(value)
    runtime_agents = ownership.get("runtime_state_agent_ids")
    if not isinstance(runtime_agents, list) or not runtime_agents or any(not isinstance(value, str) or not value.strip() for value in runtime_agents):
        raise ValueError("Scope ownership is missing runtime_state_agent_ids")
    role_ids.extend(runtime_agents)
    if len(set(role_ids)) != len(role_ids):
        raise ValueError("Frozen controller roles must be distinct")
    included_features = scope.get("migration_scope", {}).get("included_features")
    if not isinstance(included_features, list) or not included_features:
        raise ValueError("Scope has no included Feature-IDs")
    for feature_id in included_features:
        validate_id(feature_id, "Feature-ID")
    return environments, baseline[0]["env_id"], ownership, included_features


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--work-order", required=True)
    parser.add_argument("--frozen-by", required=True)
    parser.add_argument("--android-bin", default="android")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    run_input = Path(args.run_dir).expanduser().absolute()
    scope_input = Path(args.scope).expanduser().absolute()
    work_order_input = Path(args.work_order).expanduser().absolute()
    if run_input.is_symlink() or scope_input.is_symlink() or work_order_input.is_symlink():
        parser.error("Run directory, scope, and work order must not be symbolic links")
    run_dir = run_input.resolve()
    scope_path = scope_input.resolve()
    work_order_path = work_order_input.resolve()
    if not run_dir.is_dir():
        parser.error(f"Migration run does not exist: {run_dir}")
    expected_scope = (run_dir / "controller" / "scope.json").resolve()
    if scope_path != expected_scope:
        parser.error(f"Scope must be the controller-owned file: {expected_scope}")
    work_orders_root = (run_dir / "controller" / "work-orders").resolve()
    try:
        work_order_path.relative_to(work_orders_root)
    except ValueError:
        parser.error(f"Work order must be controller-owned below: {work_orders_root}")
    gate = load_json(run_dir / "controller" / "gate-report.json")
    if gate.get("phase") != 1 or gate.get("verdict") != "PASS":
        parser.error("Controller Phase 1 gate must be PASS before Phase 2 initialization")
    scope = load_json(scope_path)
    work_order = load_json(work_order_path)
    run_manifest = load_json(run_dir / "run-manifest.json")
    if scope.get("run_id") != run_manifest.get("run_id") or scope.get("project_id") != run_manifest.get("project_id"):
        parser.error("Controller scope identity does not match run-manifest.json")
    scope_sha256 = sha256_file(scope_path)
    run_manifest_sha256 = sha256_file(run_dir / "run-manifest.json")
    if gate.get("scope_sha256") != scope_sha256 or gate.get("run_manifest_sha256") != run_manifest_sha256:
        parser.error("Controller scope or run manifest changed after the Phase 1 PASS")
    if gate.get("source_revision") != scope.get("android", {}).get("source_revision"):
        parser.error("Controller source revision changed after the Phase 1 PASS")
    environments, baseline_env_id, ownership, included_features = validate_scope(scope)
    expected_work_order_id = f"WO-PHASE-02-{scope_sha256[:12].upper()}"
    work_order_sha256 = sha256_file(work_order_path)
    registry_rows = [
        row for row in read_csv(run_dir / "controller" / "work-order-registry.csv")
        if row.get("work_order_id") == work_order.get("work_order_id")
    ]
    if (
        work_order.get("phase") != 2
        or work_order.get("status") != "ISSUED"
        or work_order.get("work_order_id") != expected_work_order_id
        or work_order.get("run_id") != scope.get("run_id")
        or work_order.get("scope_sha256") != scope_sha256
        or work_order.get("ownership") != ownership
        or work_order.get("baseline_env_id") != baseline_env_id
        or work_order.get("included_features") != included_features
        or work_order.get("issued_by") != ownership.get("migration_controller_id")
        or work_order.get("required_skill") != "android-migration-inventory"
        or work_order.get("runtime_ui_tool") != "android-cli"
        or work_order.get("layout_inspector_allowed") is not False
        or work_order.get("mp4_allowed") is not False
        or len(registry_rows) != 1
        or registry_rows[0].get("phase") != "2"
        or registry_rows[0].get("relative_path") != work_order_path.relative_to(run_dir).as_posix()
        or registry_rows[0].get("scope_sha256") != scope_sha256
        or registry_rows[0].get("work_order_sha256") != work_order_sha256
        or registry_rows[0].get("issued_by") != ownership.get("migration_controller_id")
        or registry_rows[0].get("status") != "ISSUED"
    ):
        parser.error("Phase 2 work order does not match the frozen controller scope")
    if args.frozen_by != ownership["inventory_lead_id"]:
        parser.error("--frozen-by must equal the controller-assigned inventory lead")

    phase_dir = run_dir / PHASE_NAME
    if phase_dir.exists():
        parser.error(f"Phase 2 workspace already exists; overwrite is prohibited: {phase_dir}")

    try:
        android_bin = resolve_executable(args.android_bin)
    except ValueError as exc:
        parser.error(str(exc))

    project_root = Path(scope["android"]["project_root"]).expanduser().resolve()
    apk_path = Path(scope["android"]["apk_path"]).expanduser().resolve()
    if not project_root.is_dir() or not apk_path.is_file():
        parser.error("Frozen Android project root or APK no longer exists")
    current_apk_sha256 = sha256_file(apk_path)
    if current_apk_sha256 != gate.get("apk_sha256") or current_apk_sha256 != scope["android"].get("apk_sha256"):
        parser.error("Frozen APK changed after the Phase 1 PASS")

    source_commands = []
    for label, argv in (
        ("git-revision", ["git", "-C", str(project_root), "rev-parse", "HEAD"]),
        ("git-worktree", ["git", "-C", str(project_root), "status", "--porcelain", "--untracked-files=all"]),
    ):
        record = run_command(argv, timeout=30)
        source_commands.append({"label": label, **record})
        try:
            require_success(record, label)
        except RuntimeError as exc:
            parser.error(f"Source baseline preflight failed; Phase 2 is BLOCKED: {exc}")
    if source_commands[0]["stdout"].strip() != scope["android"]["source_revision"]:
        parser.error("Android Git HEAD changed after the Phase 1 PASS")
    if source_commands[1]["stdout"].strip():
        parser.error("Android worktree changed after the Phase 1 PASS")

    commands = []
    for label, argv in (
        ("android-version", [android_bin, "--version"]),
        ("android-info", [android_bin, "info"]),
        ("android-describe", [android_bin, "describe", f"--project_dir={project_root}"]),
        ("android-run-help", [android_bin, "run", "--help"]),
        ("android-layout-help", [android_bin, "layout", "--help"]),
        ("android-screen-help", [android_bin, "screen", "--help"]),
    ):
        record = run_command(argv, timeout=args.timeout)
        commands.append({"label": label, **record})
        try:
            require_success(record, label)
        except RuntimeError as exc:
            parser.error(f"Android CLI preflight failed; Phase 2 is BLOCKED: {exc}")
        if not record["stdout"].strip() and not record["stderr"].strip():
            parser.error(f"Android CLI preflight returned no usable output for {label}; Phase 2 is BLOCKED")

    cli_version = commands[0]["stdout"].strip() or commands[0]["stderr"].strip()
    frozen_at = utc_now()
    enriched = []
    for original in environments:
        env = dict(original)
        env.update(
            {
                "status": "FROZEN",
                "frozen_by": args.frozen_by,
                "frozen_at": frozen_at,
                "android_cli_version": cli_version,
                "application_id": scope["android"]["application_id"],
                "app_version": scope["android"]["app_version"],
                "app_build": scope["android"]["app_build"],
                "build_variant": scope["android"]["build_variant"],
                "source_revision": scope["android"]["source_revision"],
                "apk_sha256": current_apk_sha256,
            }
        )
        enriched.append(env)

    with tempfile.TemporaryDirectory(prefix=f".{PHASE_NAME}-", dir=run_dir) as temp_name:
        temp_dir = Path(temp_name)
        for name in (
            "evidence", "claims", "attempts", "tooling", "catalogs",
            "environment-attestations", "asset-package", "static-analysis", "probe-evidence",
            ".locks", ".staging",
        ):
            (temp_dir / name).mkdir()
        (temp_dir / "asset-package" / "files").mkdir()
        for source, target in (
            ("inventory.template.csv", "inventory.csv"),
            ("asset-inventory.template.csv", "asset-inventory.csv"),
            ("evidence-index.template.csv", "evidence-index.csv"),
            ("rechecks.template.csv", "rechecks.csv"),
        ):
            shutil.copyfile(ASSETS / source, temp_dir / target)
        for source, target in (
            ("code-map.template.csv", "code-map.csv"),
            ("business-rules.template.csv", "business-rules.csv"),
            ("data-dependencies.template.csv", "data-dependencies.csv"),
            ("system-capabilities.template.csv", "system-capabilities.csv"),
            ("third-party-dependencies.template.csv", "third-party-dependencies.csv"),
        ):
            shutil.copyfile(ASSETS / source, temp_dir / "catalogs" / target)
        coverage_fields = [
            "feature_id", "feature_name", "applicable_env_ids", "code_mapped", "runtime_states_captured",
            "business_rules_mapped", "data_dependencies_mapped", "status", "owner", "notes",
        ]
        environment_ids = [env["env_id"] for env in enriched]
        write_csv(
            temp_dir / "coverage-ledger.csv",
            coverage_fields,
            [
                {
                    "feature_id": feature_id,
                    "feature_name": "",
                    "applicable_env_ids": json.dumps(environment_ids, separators=(",", ":")),
                    "code_mapped": "false",
                    "runtime_states_captured": "false",
                    "business_rules_mapped": "false",
                    "data_dependencies_mapped": "false",
                    "status": "OPEN",
                    "owner": ownership["inventory_lead_id"],
                    "notes": "",
                }
                for feature_id in included_features
            ],
        )
        atomic_json(
            temp_dir / "environments.json",
            {"baseline_env_id": baseline_env_id, "frozen_at": frozen_at, "environments": enriched},
        )
        atomic_json(temp_dir / "runtime-observations.json", {"schema_version": 1, "observations": []})
        atomic_json(temp_dir / "advanced-observations.json", {"schema_version": 1, "observations": []})
        write_csv(
            temp_dir / "probe-evidence-index.csv",
            [
                "probe_evidence_id", "candidate_id", "page_id", "env_id",
                "relative_path", "metadata_sha256", "status",
            ],
            [],
        )
        environment_registry_sha256 = sha256_file(temp_dir / "environments.json")
        shutil.copyfile(scope_path, temp_dir / "controller-scope.snapshot.json")
        atomic_json(temp_dir / "tooling" / "android-cli-preflight.json", {"commands": commands})
        atomic_json(temp_dir / "tooling" / "source-preflight.json", {"commands": source_commands})
        (temp_dir / "tooling" / "android-describe.txt").write_text(
            commands[2]["stdout"], encoding="utf-8"
        )
        atomic_json(
            temp_dir / "phase-manifest.json",
            {
                "run_id": scope["run_id"],
                "phase": 2,
                "status": "IN_PROGRESS",
                "baseline_env_id": baseline_env_id,
                "initialized_at": frozen_at,
                "initialized_by": args.frozen_by,
                "capture_tool": "android-cli",
                "layout_inspector_allowed": False,
                "mp4_allowed": False,
                "scope_sha256": scope_sha256,
                "run_manifest_sha256": run_manifest_sha256,
                "environment_registry_sha256": environment_registry_sha256,
                "ownership": ownership,
                "included_features": included_features,
                "work_order_id": work_order.get("work_order_id"),
                "work_order_sha256": work_order_sha256,
                "android_project_root": str(project_root),
                "apk_path": str(apk_path),
                "apk_sha256": current_apk_sha256,
                "source_revision": scope["android"]["source_revision"],
            },
        )
        temp_dir.rename(phase_dir)

    print(json.dumps({"workspace": str(phase_dir), "baseline_env_id": baseline_env_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
