#!/usr/bin/env python3
"""Build one real, closed Phase 1/2 fixture for Phase 3 integration tests."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCAFFOLD_SKILL = HERE.parents[1]
BUNDLE = SCAFFOLD_SKILL.parent
CONTROLLER_SKILL = BUNDLE / "android-harmony-migration-controller"
INVENTORY_SKILL = BUNDLE / "android-migration-inventory"
FAKE_ANDROID = INVENTORY_SKILL / "scripts" / "tests" / "fake_android.py"


def run(*args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(args), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    if completed.returncode != expect:
        raise AssertionError(
            f"Expected exit {expect}, got {completed.returncode}\nCOMMAND: {args}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record_human_approval(run_dir: Path, phase: int, review_id: str) -> None:
    review_input = run_dir / "controller" / f"phase-{phase:02d}-review-input.json"
    review_input.write_text(
        json.dumps({"coverage": {}, "exceptions": [], "top_risks": []}) + "\n",
        encoding="utf-8",
    )
    run(
        sys.executable, str(CONTROLLER_SKILL / "scripts" / "generate_review_summary.py"),
        "--run-dir", str(run_dir), "--phase", str(phase),
        "--gate-report", str(run_dir / "controller" / "gate-report.json"),
        "--input", str(review_input),
    )
    run(
        sys.executable, str(CONTROLLER_SKILL / "scripts" / "record_human_review.py"),
        "--run-dir", str(run_dir), "--phase", str(phase),
        "--gate-report", str(run_dir / "controller" / "gate-report.json"),
        "--review-id", review_id,
        "--reviewer", "fixture-human-reviewer",
        "--decision", "APPROVED",
    )


def record_team_receipt(
    run_dir: Path,
    work_order: Path,
    role_key: str,
    actor_id: str,
    platform_task_id: str,
    artifact: Path,
) -> None:
    run(
        sys.executable,
        str(CONTROLLER_SKILL / "scripts" / "record_team_execution.py"),
        "--run-dir", str(run_dir),
        "--work-order", work_order.relative_to(run_dir).as_posix(),
        "--role-key", role_key,
        "--actor-id", actor_id,
        "--platform-task-id", platform_task_id,
        "--started-at", "2026-08-24T10:00:00Z",
        "--ended-at", "2026-08-24T10:05:00Z",
        "--terminal-task-state", "SUCCEEDED",
        "--artifact", artifact.relative_to(run_dir).as_posix(),
    )


def build_closed_phase2(root: Path) -> tuple[Path, Path]:
    """Run the real Phase 1/2 scripts and return (run_dir, scope_path)."""
    project = root / "android-project"
    (project / "app" / "src" / "main").mkdir(parents=True)
    source_lines = [f"// fixture line {number}" for number in range(1, 31)]
    source_lines[9] = "fun renderLogin() = Unit"
    (project / "app" / "Login.kt").write_text("\n".join(source_lines) + "\n", encoding="utf-8")
    (project / "settings.gradle").write_text("rootProject.name='Fixture'\n", encoding="utf-8")
    (project / "app" / "build.gradle").write_text(
        "plugins { id 'com.android.application' }\n", encoding="utf-8"
    )
    (project / "app" / "src" / "main" / "AndroidManifest.xml").write_text(
        '<manifest package="com.example.fixture"><application /></manifest>\n',
        encoding="utf-8",
    )
    asset_source = project / "app" / "src" / "main" / "res" / "drawable" / "login_logo.svg"
    asset_source.parent.mkdir(parents=True)
    asset_source.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
        '<circle cx="16" cy="16" r="14" fill="#3367D6"/></svg>\n',
        encoding="utf-8",
    )
    run("git", "init", "-q", str(project))
    run("git", "-C", str(project), "config", "user.email", "fixture@example.invalid")
    run("git", "-C", str(project), "config", "user.name", "Fixture")
    run("git", "-C", str(project), "add", ".")
    run("git", "-C", str(project), "commit", "-q", "-m", "fixture baseline")
    revision = run("git", "-C", str(project), "rev-parse", "HEAD").stdout.strip()

    apk = root / "fixture.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("AndroidManifest.xml", b"binary-manifest-fixture")
        archive.writestr("classes.dex", b"dex\n035\x00fixture")

    created = run(
        sys.executable,
        str(CONTROLLER_SKILL / "scripts" / "init_migration.py"),
        "--output", str(root / "runs"),
        "--project-root", str(project),
        "--project-name", "Fixture",
        "--run-id", "MIG-STAGE3-TEST",
    )
    run_dir = Path(json.loads(created.stdout)["run_dir"])
    scope_path = run_dir / "controller" / "scope.json"
    scope = json.loads(scope_path.read_text(encoding="utf-8"))
    scope["android"].update(
        {
            "source_revision": revision,
            "source_revision_kind": "git-commit",
            "apk_path": str(apk),
            "apk_sha256": sha256(apk),
            "application_id": "com.example.fixture",
            "app_version": "1.0.0",
            "app_build": "100",
            "build_variant": "debug",
        }
    )
    scope["target"]["sdk_or_api_target"] = "API-TEST"
    scope["migration_scope"]["included_features"] = ["FEATURE-AUTH"]
    scope["migration_scope"]["excluded_features"] = []
    scope["ownership"] = {
        "migration_controller_id": "migration-controller-1",
        "inventory_lead_id": "inventory-lead-1",
        "code_map_agent_id": "code-map-agent-1",
        "runtime_state_agent_ids": ["runtime-state-agent-1"],
        "business_rule_agent_id": "business-rule-agent-1",
        "data_dependency_agent_id": "data-dependency-agent-1",
        "evidence_administrator_id": "evidence-administrator-1",
        "coverage_checker_id": "coverage-checker-1",
    }
    scope["pending_confirmations"] = []
    scope["tool_policy"]["apk_analyzer_bin"] = str(FAKE_ANDROID)
    scope["environments"][0].update(
        {
            "account_id": "ACCOUNT-TEST",
            "account_role": "USER",
            "seed_data_id": "SEED-AUTH-01",
            "seed_reset_ref": "docs/seed-auth.md",
            "network_conditions_ref": "normal-network-profile",
            "network_toggle_available": True,
            "emulator_model": "Pixel-Test",
            "device_serial": "emulator-5554",
            "resolution": "1080x2400",
            "density_dpi": 420,
            "android_api_level": 35,
            "orientation": "portrait",
        }
    )
    scope_path.write_text(json.dumps(scope, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    run(
        sys.executable, str(CONTROLLER_SKILL / "scripts" / "validate_gate.py"),
        "--run-dir", str(run_dir), "--phase", "1", "--write",
    )
    record_human_approval(run_dir, 1, "HREV-PHASE-01-SCAFFOLD")
    issued = run(
        sys.executable, str(CONTROLLER_SKILL / "scripts" / "issue_phase2_work_order.py"),
        "--run-dir", str(run_dir), "--issued-by", "migration-controller-1",
    )
    phase2_work_order = Path(json.loads(issued.stdout)["work_order"])
    initialized = run(
        sys.executable, str(INVENTORY_SKILL / "scripts" / "init_inventory.py"),
        "--run-dir", str(run_dir), "--scope", str(scope_path),
        "--work-order", str(phase2_work_order), "--frozen-by", "inventory-lead-1",
        "--android-bin", str(FAKE_ANDROID),
    )
    workspace = Path(json.loads(initialized.stdout)["workspace"])
    asset_mapping = root / "asset-mapping.json"
    asset_mapping.write_text(json.dumps({
        "schema_version": 1,
        "assets": [{
            "asset_id": "ASSET-AUTH-LOGO",
            "source_path": "app/src/main/res/drawable/login_logo.svg",
            "source_sha256": sha256(asset_source),
            "asset_type": "VECTOR_IMAGE",
            "feature_ids": ["FEATURE-AUTH"],
            "page_ids": ["PAGE-LOGIN"],
            "state_ids": ["STATE-DEFAULT"],
            "notes": "Real asset fixture",
        }],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run(
        sys.executable, str(INVENTORY_SKILL / "scripts" / "archive_assets.py"),
        "--workspace", str(workspace), "--mapping", str(asset_mapping),
        "--archived-by", "code-map-agent-1",
    )
    run(
        sys.executable, str(INVENTORY_SKILL / "scripts" / "attest_environment.py"),
        "--workspace", str(workspace), "--env-id", "ENV-001",
        "--inventory-lead-id", "inventory-lead-1", "--account-ready", "--seed-ready",
        "--network-ready", "--permissions-ready", "--notes", "fixture ready",
    )

    steps = root / "login-steps.md"
    steps.write_text("1. Launch the signed-out app.\n2. Observe the login form.\n", encoding="utf-8")
    captured = run(
        sys.executable, str(INVENTORY_SKILL / "scripts" / "capture_state.py"),
        "--workspace", str(workspace), "--inventory-id", "INV-AUTH-LOGIN-DEFAULT",
        "--feature-id", "FEATURE-AUTH", "--page-id", "PAGE-LOGIN",
        "--state-id", "STATE-DEFAULT", "--env-id", "ENV-001", "--steps", str(steps),
        "--issued-by", "evidence-administrator-1", "--captured-by", "runtime-state-agent-1",
        "--launch", "--android-bin", str(FAKE_ANDROID), "--adb-bin", str(FAKE_ANDROID),
    )
    evidence_id = json.loads(captured.stdout)["evidence_id"]
    run(
        sys.executable, str(CONTROLLER_SKILL / "scripts" / "anchor_phase2_evidence.py"),
        "--run-dir", str(run_dir), "--evidence-id", evidence_id,
        "--anchored-by", "migration-controller-1",
    )

    claims = [
        {
            "inventory_id": "INV-AUTH-LOGIN-DEFAULT",
            "feature_id": "FEATURE-AUTH",
            "feature_name": "Authentication",
            "page_id": "PAGE-LOGIN",
            "page_name": "Login",
            "state_id": "STATE-DEFAULT",
            "state_name": "Default",
            "env_id": "ENV-001",
            "evidence_id": evidence_id,
            "entry_condition": "App opened while signed out",
            "action_summary": "Open login",
            "expected_observable": "Login form is visible",
            "actual_observable": "Login form is visible",
            "code_refs": ["app/Login.kt:10"],
            "business_rule_refs": ["BR-AUTH-NONE"],
            "data_dependency_refs": ["DATA-AUTH-NONE"],
            "system_capability_refs": ["SYS-AUTH-NONE"],
            "third_party_dependency_refs": ["SDK-AUTH-NONE"],
            "asset_ids": ["ASSET-AUTH-LOGO"],
            "responsible_agent": "runtime-state-agent-1",
            "row_status": "CAPTURED",
        }
    ]
    claims_path = workspace / "claims" / "auth.json"
    claims_path.write_text(json.dumps(claims, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    run(
        sys.executable, str(INVENTORY_SKILL / "scripts" / "build_inventory.py"),
        "--workspace", str(workspace), "--claims", str(claims_path),
    )

    write_csv(
        workspace / "coverage-ledger.csv",
        [
            "feature_id", "feature_name", "applicable_env_ids", "code_mapped",
            "runtime_states_captured", "business_rules_mapped", "data_dependencies_mapped",
            "status", "owner", "notes",
        ],
        [
            {
                "feature_id": "FEATURE-AUTH", "feature_name": "Authentication",
                "applicable_env_ids": '["ENV-001"]', "code_mapped": "true",
                "runtime_states_captured": "true", "business_rules_mapped": "true",
                "data_dependencies_mapped": "true", "status": "COMPLETE",
                "owner": "inventory-lead-1", "notes": "Fixture coverage",
            }
        ],
    )
    write_csv(
        workspace / "catalogs" / "code-map.csv",
        [
            "code_ref", "feature_id", "page_id", "state_candidate_id", "component_type",
            "symbol", "file_path", "line", "coverage_disposition", "owner", "status", "notes",
        ],
        [
            {
                "code_ref": "app/Login.kt:10", "feature_id": "FEATURE-AUTH",
                "page_id": "PAGE-LOGIN", "state_candidate_id": "STATE-DEFAULT",
                "component_type": "function", "symbol": "renderLogin", "file_path": "app/Login.kt",
                "line": "10", "coverage_disposition": "IN_SCOPE", "owner": "code-map-agent-1",
                "status": "VERIFIED", "notes": "Runtime correlated",
            }
        ],
    )
    write_csv(
        workspace / "catalogs" / "business-rules.csv",
        [
            "business_rule_id", "feature_id", "page_id", "state_id", "condition", "outcome",
            "code_refs", "test_refs", "owner", "status", "notes",
        ],
        [
            {
                "business_rule_id": "BR-AUTH-NONE", "feature_id": "FEATURE-AUTH",
                "page_id": "PAGE-LOGIN", "state_id": "STATE-DEFAULT", "condition": "NONE_FOUND",
                "outcome": "NO_RULE_BEYOND_VISIBLE_STATE", "code_refs": '["app/Login.kt:10"]',
                "test_refs": "[]", "owner": "business-rule-agent-1", "status": "VERIFIED",
                "notes": "Explicit no-rule audit",
            }
        ],
    )
    write_csv(
        workspace / "catalogs" / "data-dependencies.csv",
        [
            "data_dependency_id", "feature_id", "dependency_type", "name", "direction",
            "source_ref", "sensitive", "migration_risk", "owner", "status", "notes",
        ],
        [
            {
                "data_dependency_id": "DATA-AUTH-NONE", "feature_id": "FEATURE-AUTH",
                "dependency_type": "NONE", "name": "NONE_FOUND", "direction": "NONE",
                "source_ref": "app/Login.kt:10", "sensitive": "false", "migration_risk": "none",
                "owner": "data-dependency-agent-1", "status": "VERIFIED", "notes": "No dependency",
            }
        ],
    )
    write_csv(
        workspace / "catalogs" / "system-capabilities.csv",
        [
            "system_capability_id", "feature_id", "capability_type", "name",
            "permission_or_api", "source_ref", "migration_risk", "owner", "status", "notes",
        ],
        [
            {
                "system_capability_id": "SYS-AUTH-NONE", "feature_id": "FEATURE-AUTH",
                "capability_type": "NONE", "name": "NONE_FOUND", "permission_or_api": "NONE",
                "source_ref": "app/Login.kt:10", "migration_risk": "none",
                "owner": "data-dependency-agent-1", "status": "VERIFIED", "notes": "No capability",
            }
        ],
    )
    write_csv(
        workspace / "catalogs" / "third-party-dependencies.csv",
        [
            "third_party_dependency_id", "feature_id", "name", "version", "purpose",
            "source_ref", "data_shared", "migration_risk", "owner", "status", "notes",
        ],
        [
            {
                "third_party_dependency_id": "SDK-AUTH-NONE", "feature_id": "FEATURE-AUTH",
                "name": "NONE_FOUND", "version": "NONE", "purpose": "NONE",
                "source_ref": "app/Login.kt:10", "data_shared": "false", "migration_risk": "none",
                "owner": "data-dependency-agent-1", "status": "VERIFIED", "notes": "No SDK",
            }
        ],
    )

    static = workspace / "static-analysis"
    static_artifacts = {
        "project-index.json": {
            "schema_version": 1, "source_revision": revision, "generated_by": "code-map-agent-1",
        },
        "pages.json": {"schema_version": 1, "pages": [{
            "page_id": "PAGE-LOGIN", "symbol": "LoginActivity",
            "kinds": ["ACTIVITY"],
            "candidate_feature_ids": ["FEATURE-AUTH"],
        }]},
        "components.json": {"schema_version": 1, "components": [{
            "component_id": "COMP-LOGIN-ROOT", "page_id": "PAGE-LOGIN",
            "resource_id": "login", "text": "Login", "type": "TextView", "attributes": {},
        }]},
        "events.json": {"schema_version": 1, "events": []},
        "transitions.json": {"schema_version": 1, "transitions": []},
        "state-candidates.json": {"schema_version": 1, "states": [{
            "state_id": "STATE-DEFAULT", "page_id": "PAGE-LOGIN",
        }]},
        "runtime-tasks.json": {"schema_version": 1, "tasks": [{
            "task_id": "RTASK-PAGE-LOGIN", "task_type": "VERIFY_PAGE_DEFAULT_STATE",
            "subject_id": "PAGE-LOGIN", "page_id": "PAGE-LOGIN",
        }]},
        "advanced-analysis.json": {
            "schema_version": 1, "dynamic_risks": [], "side_effects": [], "scenarios": [],
        },
    }
    for name, value in static_artifacts.items():
        (static / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    (static / "code-map.candidates.csv").write_text("code_ref\n", encoding="utf-8")
    static_names = sorted([*static_artifacts, "code-map.candidates.csv"])
    (static / "manifest.sha256").write_text(
        "".join(f"{sha256(static / name)}  {name}\n" for name in static_names), encoding="utf-8"
    )
    (static / "COMMITTED").write_text(sha256(static / "manifest.sha256") + "\n", encoding="utf-8")
    (workspace / "runtime-observations.json").write_text(json.dumps({
        "schema_version": 1,
        "observations": [
            {
                "observation_id": "OBS-PAGE-LOGIN", "subject_type": "PAGE",
                "subject_id": "PAGE-LOGIN", "page_id": "PAGE-LOGIN", "env_id": "ENV-001",
                "before_evidence_id": "", "after_evidence_id": evidence_id,
                "locator_field": "", "locator_value": "", "locator_occurrence": 0,
            },
            {
                "observation_id": "OBS-STATE-LOGIN-DEFAULT", "subject_type": "STATE",
                "subject_id": "STATE-DEFAULT", "page_id": "PAGE-LOGIN", "env_id": "ENV-001",
                "before_evidence_id": "", "after_evidence_id": evidence_id,
                "locator_field": "", "locator_value": "", "locator_occurrence": 0,
            },
            {
                "observation_id": "OBS-COMP-LOGIN", "subject_type": "COMPONENT",
                "subject_id": "COMP-LOGIN-ROOT", "page_id": "PAGE-LOGIN", "env_id": "ENV-001",
                "before_evidence_id": "", "after_evidence_id": evidence_id,
                "locator_field": "", "locator_value": "", "locator_occurrence": 0,
            },
        ],
    }, indent=2) + "\n", encoding="utf-8")

    run(
        sys.executable, str(INVENTORY_SKILL / "scripts" / "validate_evidence.py"),
        "--workspace", str(workspace), "--reviewer", "coverage-checker-1", "--decision", "PASS",
        "--attest-visual-review", "--attest-source-runtime-crosscheck",
    )
    run(
        sys.executable, str(CONTROLLER_SKILL / "scripts" / "validate_gate.py"),
        "--run-dir", str(run_dir), "--phase", "2", "--write",
    )
    record_human_approval(run_dir, 2, "HREV-PHASE-02-SCAFFOLD")
    phase2_receipts = [
        ("inventory_lead_id", "inventory-lead-1", "TASK-P2-LEAD", workspace / "phase-manifest.json"),
        ("code_map_agent_id", "code-map-agent-1", "TASK-P2-CODE", workspace / "static-analysis" / "COMMITTED"),
        ("runtime_state_agent_ids", "runtime-state-agent-1", "TASK-P2-RUNTIME", workspace / "evidence-index.csv"),
        ("business_rule_agent_id", "business-rule-agent-1", "TASK-P2-RULE", workspace / "catalogs" / "business-rules.csv"),
        ("data_dependency_agent_id", "data-dependency-agent-1", "TASK-P2-DATA", workspace / "catalogs" / "data-dependencies.csv"),
        ("evidence_administrator_id", "evidence-administrator-1", "TASK-P2-EVIDENCE", workspace / "evidence-index.csv"),
        ("coverage_checker_id", "coverage-checker-1", "TASK-P2-COVERAGE", workspace / "closure-report.json"),
    ]
    for role_key, actor_id, task_id, artifact in phase2_receipts:
        record_team_receipt(run_dir, phase2_work_order, role_key, actor_id, task_id, artifact)
    return run_dir, scope_path
