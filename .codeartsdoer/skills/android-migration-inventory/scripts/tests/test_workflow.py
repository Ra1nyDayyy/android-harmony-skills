#!/usr/bin/env python3
"""Offline end-to-end and adversarial tests for the first two migration skills."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
INVENTORY_SKILL = HERE.parents[1]
BUNDLE = INVENTORY_SKILL.parent
CONTROLLER_SKILL = BUNDLE / "android-harmony-migration-controller"
FAKE_ANDROID = HERE / "fake_android.py"


def run(*args: str, env: dict[str, str] | None = None, expect: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(args), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False
    )
    if completed.returncode != expect:
        raise AssertionError(
            f"Expected exit {expect}, got {completed.returncode}\nCOMMAND: {args}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class WorkflowTest(unittest.TestCase):
    def test_atomic_writes_and_path_containment(self) -> None:
        spec = importlib.util.spec_from_file_location("inventory_common", INVENTORY_SKILL / "scripts" / "_common.py")
        assert spec and spec.loader
        common = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(common)
        with tempfile.TemporaryDirectory(prefix="android-migration-path-test-") as temp_name:
            root = Path(temp_name) / "workspace"
            root.mkdir()
            victim = Path(temp_name) / "victim.txt"
            victim.write_text("untouched\n", encoding="utf-8")
            (root / "inventory.csv.tmp").symlink_to(victim)
            common.write_csv(root / "inventory.csv", ["id"], [{"id": "SAFE"}])
            self.assertEqual(victim.read_text(encoding="utf-8"), "untouched\n")
            self.assertFalse((root / "inventory.csv").is_symlink())
            linked_target = root / "linked.csv"
            linked_target.symlink_to(victim)
            with self.assertRaises(ValueError):
                common.write_csv(linked_target, ["id"], [{"id": "BLOCKED"}])
            with self.assertRaises(ValueError):
                common.assert_no_symlink(root / ".." / "victim.txt", root)

    def test_none_found_commits_empty_asset_package_without_fake_rows(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "inventory_asset_common", INVENTORY_SKILL / "scripts" / "_common.py"
        )
        assert spec and spec.loader
        common = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(common)
        with tempfile.TemporaryDirectory(prefix="android-empty-asset-test-") as temp_name:
            root = Path(temp_name)
            workspace = root / "workspace"
            project = root / "android-project"
            (workspace / "asset-package" / "files").mkdir(parents=True)
            project.mkdir()
            write_csv(workspace / "asset-inventory.csv", common.ASSET_INVENTORY_FIELDS, [])
            manifest = workspace / "asset-package" / "manifest.sha256"
            manifest.write_text("", encoding="utf-8")
            (workspace / "asset-package" / "COMMITTED").write_text(
                hashlib.sha256(b"").hexdigest() + "\n", encoding="utf-8"
            )
            rows = common.verify_asset_chain(
                workspace,
                {
                    "android_project_root": str(project),
                    "included_features": ["FEATURE-AUTH"],
                    "ownership": {
                        "code_map_agent_id": "code-map-agent-1",
                        "coverage_checker_id": "coverage-checker-1",
                    },
                },
                [{
                    "inventory_id": "INV-NO-ASSET", "feature_id": "FEATURE-AUTH",
                    "page_id": "PAGE-LOGIN", "state_id": "STATE-DEFAULT",
                    "row_status": "CAPTURED", "asset_ids": '["NONE_FOUND"]',
                }],
            )
            self.assertEqual(rows, [])
            self.assertEqual(list((workspace / "asset-package" / "files").iterdir()), [])

    def test_recheck_routing_and_controller_sync(self) -> None:
        with tempfile.TemporaryDirectory(prefix="android-migration-recheck-test-") as temp_name:
            run_dir = Path(temp_name) / "MIG-RECHECK-TEST"
            workspace = run_dir / "phase-02-android-inventory"
            controller = run_dir / "controller"
            (workspace / ".locks").mkdir(parents=True)
            controller.mkdir()
            ownership = {
                "migration_controller_id": "migration-controller-1",
                "inventory_lead_id": "inventory-lead-1",
                "code_map_agent_id": "code-map-agent-1",
                "runtime_state_agent_ids": ["runtime-state-agent-1"],
                "business_rule_agent_id": "business-rule-agent-1",
                "data_dependency_agent_id": "data-dependency-agent-1",
                "evidence_administrator_id": "evidence-administrator-1",
                "coverage_checker_id": "coverage-checker-1",
            }
            (workspace / "phase-manifest.json").write_text(
                json.dumps({
                    "run_id": "MIG-RECHECK-TEST", "phase": 2,
                    "status": "IN_PROGRESS", "ownership": ownership,
                }) + "\n",
                encoding="utf-8",
            )
            (controller / "scope.json").write_text(
                json.dumps({"run_id": "MIG-RECHECK-TEST", "ownership": ownership}) + "\n",
                encoding="utf-8",
            )
            recheck_fields = [
                "rework_id", "opened_at", "inventory_id", "feature_id", "page_id", "state_id",
                "env_id", "evidence_id", "severity", "problem_code", "reason", "assigned_to",
                "completion_condition", "status", "resolved_at", "resolution_evidence_id", "closed_by",
            ]
            controller_fields = [
                "rework_id", "created_at", "phase", "record_id", "feature_id", "page_id", "state_id",
                "env_id", "evidence_id", "gate_rule", "reason", "assigned_to", "completion_condition",
                "status", "resolved_at", "resolution_evidence_id", "reviewed_by",
            ]
            write_csv(workspace / "rechecks.csv", recheck_fields, [])
            write_csv(controller / "rework-log.csv", controller_fields, [])
            write_csv(
                workspace / "inventory.csv",
                [
                    "inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id",
                    "responsible_agent",
                ],
                [{
                    "inventory_id": "INV-RECHECK-001", "feature_id": "FEATURE-AUTH",
                    "page_id": "PAGE-LOGIN", "state_id": "STATE-DEFAULT", "env_id": "ENV-001",
                    "evidence_id": "EVD-OLD-001", "responsible_agent": "runtime-state-agent-1",
                }],
            )
            write_csv(
                workspace / "evidence-index.csv",
                ["evidence_id", "feature_id", "page_id", "state_id", "env_id", "status", "captured_at"],
                [{
                    "evidence_id": "EVD-NEW-001", "feature_id": "FEATURE-AUTH",
                    "page_id": "PAGE-LOGIN", "state_id": "STATE-DEFAULT", "env_id": "ENV-001",
                    "status": "SEALED", "captured_at": "9999-12-31T23:59:59Z",
                }],
            )

            base_open = (
                sys.executable, str(INVENTORY_SKILL / "scripts" / "manage_recheck.py"),
                "--workspace", str(workspace), "--action", "open",
                "--reviewer", "coverage-checker-1", "--inventory-id", "INV-RECHECK-001",
                "--severity", "HIGH", "--reason", "fixture problem",
                "--completion-condition", "capture replacement evidence",
            )
            run(
                *base_open, "--rework-id", "RW-WRONG-REVIEWER", "--problem-code", "STATE",
                "--reviewer", "runtime-state-agent-1", expect=2,
            )
            run(
                *base_open, "--rework-id", "RW-WRONG-ROUTE", "--problem-code", "STATE",
                "--assigned-to", "code-map-agent-1", expect=2,
            )
            self.assertEqual(list(csv.DictReader((workspace / "rechecks.csv").read_text().splitlines())), [])
            self.assertEqual(list(csv.DictReader((controller / "rework-log.csv").read_text().splitlines())), [])

            routes = {
                "ENV": "inventory-lead-1",
                "CODE": "code-map-agent-1",
                "STATE": "runtime-state-agent-1",
                "RULE": "business-rule-agent-1",
                "API": "data-dependency-agent-1",
                "HASH": "evidence-administrator-1",
            }
            for number, (problem_code, expected_owner) in enumerate(routes.items(), start=1):
                command = [
                    *base_open, "--rework-id", f"RW-ROUTE-{number:03d}",
                    "--problem-code", problem_code,
                ]
                if problem_code == "CODE":
                    command.extend(["--assigned-to", expected_owner])
                run(*command)

            local_rows = {
                row["problem_code"]: row
                for row in csv.DictReader((workspace / "rechecks.csv").read_text().splitlines())
            }
            controller_rows = {
                row["gate_rule"]: row
                for row in csv.DictReader((controller / "rework-log.csv").read_text().splitlines())
            }
            self.assertEqual({code: row["assigned_to"] for code, row in local_rows.items()}, routes)
            self.assertEqual({code: row["assigned_to"] for code, row in controller_rows.items()}, routes)
            self.assertEqual({row["status"] for row in local_rows.values()}, {"OPEN"})
            self.assertEqual({row["status"] for row in controller_rows.values()}, {"REWORK"})
            self.assertEqual(
                {row["reviewed_by"] for row in controller_rows.values()}, {"coverage-checker-1"}
            )

            runtime_rework_id = local_rows["STATE"]["rework_id"]
            close_base = (
                sys.executable, str(INVENTORY_SKILL / "scripts" / "manage_recheck.py"),
                "--workspace", str(workspace), "--action", "close",
                "--rework-id", runtime_rework_id, "--resolution-evidence-id", "EVD-NEW-001",
            )
            run(*close_base, "--reviewer", "runtime-state-agent-1", expect=2)
            run(
                *close_base, "--reviewer", "coverage-checker-1",
                "--assigned-to", "code-map-agent-1", expect=2,
            )
            run(
                *close_base, "--reviewer", "coverage-checker-1",
                "--assigned-to", "runtime-state-agent-1",
            )
            closed_local = next(
                row for row in csv.DictReader((workspace / "rechecks.csv").read_text().splitlines())
                if row["rework_id"] == runtime_rework_id
            )
            closed_controller = next(
                row for row in csv.DictReader((controller / "rework-log.csv").read_text().splitlines())
                if row["rework_id"] == runtime_rework_id
            )
            self.assertEqual(closed_local["status"], "CLOSED")
            self.assertEqual(closed_controller["status"], "CLOSED")
            self.assertEqual(closed_local["closed_by"], "coverage-checker-1")
            self.assertEqual(closed_controller["reviewed_by"], "coverage-checker-1")
            self.assertEqual(closed_local["resolved_at"], closed_controller["resolved_at"])
            self.assertEqual(closed_local["resolution_evidence_id"], "EVD-NEW-001")
            self.assertEqual(closed_controller["resolution_evidence_id"], "EVD-NEW-001")

    def test_hardened_controller_inventory_and_closure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="android-migration-skill-test-") as temp_name:
            root = Path(temp_name)
            project = root / "android-project"
            (project / "app").mkdir(parents=True)
            (project / "settings.gradle").write_text("rootProject.name='Fixture'\n", encoding="utf-8")
            source_lines = [f"// fixture line {number}" for number in range(1, 61)]
            source_lines[9] = "fun renderDefaultLogin() = Unit"
            source_lines[41] = "fun renderInvalidCodeError() = Unit"
            (project / "app" / "Login.kt").write_text("\n".join(source_lines) + "\n", encoding="utf-8")
            (project / "app" / "build.gradle").write_text("plugins { id 'com.android.application' }\n", encoding="utf-8")
            (project / "app" / "src" / "main").mkdir(parents=True)
            (project / "app" / "src" / "main" / "AndroidManifest.xml").write_text(
                '<manifest package="com.example.fixture"><application /></manifest>\n', encoding="utf-8"
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
            apk_hash = hashlib.sha256(apk.read_bytes()).hexdigest()
            runs = root / "runs"

            created = run(
                sys.executable,
                str(CONTROLLER_SKILL / "scripts" / "init_migration.py"),
                "--output", str(runs),
                "--project-root", str(project),
                "--project-name", "Fixture",
            )
            run_dir = Path(json.loads(created.stdout)["run_dir"])
            scope_path = run_dir / "controller" / "scope.json"
            scope = json.loads(scope_path.read_text(encoding="utf-8"))
            scope["android"].update(
                {
                    "source_revision": revision,
                    "source_revision_kind": "git-commit",
                    "apk_path": str(apk),
                    "apk_sha256": apk_hash,
                    "application_id": "com.example.fixture",
                    "app_version": "1.0.0",
                    "app_build": "100",
                    "build_variant": "debug",
                }
            )
            scope["target"]["sdk_or_api_target"] = "API-TEST"
            scope["migration_scope"]["included_features"] = ["FEATURE-AUTH"]
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
            scope_path.write_text(json.dumps(scope, indent=2) + "\n", encoding="utf-8")

            bad_scope = json.loads(scope_path.read_text(encoding="utf-8"))
            bad_scope["android"]["source_revision"] = "PENDING_CONFIRMATION"
            bad_file = root / "not-an-apk.md"
            bad_file.write_text("not an APK\n", encoding="utf-8")
            bad_scope["android"]["apk_path"] = str(bad_file)
            bad_scope["android"]["apk_sha256"] = hashlib.sha256(bad_file.read_bytes()).hexdigest()
            scope_path.write_text(json.dumps(bad_scope, indent=2) + "\n", encoding="utf-8")
            run(
                sys.executable, str(CONTROLLER_SKILL / "scripts" / "validate_gate.py"),
                "--run-dir", str(run_dir), "--phase", "1", expect=1,
            )
            scope_path.write_text(json.dumps(scope, indent=2) + "\n", encoding="utf-8")

            run(
                sys.executable,
                str(CONTROLLER_SKILL / "scripts" / "validate_gate.py"),
                "--run-dir", str(run_dir), "--phase", "1", "--write",
            )
            issued = run(
                sys.executable,
                str(CONTROLLER_SKILL / "scripts" / "issue_phase2_work_order.py"),
                "--run-dir", str(run_dir),
                "--issued-by", "migration-controller-1",
            )
            work_order = Path(json.loads(issued.stdout)["work_order"])

            command_log = root / "fake-android.jsonl"
            process_env = os.environ.copy()
            process_env["FAKE_ANDROID_LOG"] = str(command_log)
            initialized = run(
                sys.executable,
                str(INVENTORY_SKILL / "scripts" / "init_inventory.py"),
                "--run-dir", str(run_dir),
                "--scope", str(scope_path),
                "--work-order", str(work_order),
                "--frozen-by", "inventory-lead-1",
                "--android-bin", str(FAKE_ANDROID),
                env=process_env,
            )
            workspace = Path(json.loads(initialized.stdout)["workspace"])
            asset_mapping = root / "asset-mapping.json"
            asset_mapping.write_text(json.dumps({
                "schema_version": 1,
                "assets": [{
                    "asset_id": "ASSET-AUTH-LOGO",
                    "source_path": "app/src/main/res/drawable/login_logo.svg",
                    "source_sha256": hashlib.sha256(asset_source.read_bytes()).hexdigest(),
                    "asset_type": "VECTOR_IMAGE",
                    "feature_ids": ["FEATURE-AUTH"],
                    "page_ids": ["PAGE-LOGIN"],
                    "state_ids": ["STATE-DEFAULT"],
                    "notes": "Real login logo archived from the frozen Android source.",
                }],
            }, indent=2) + "\n", encoding="utf-8")
            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "archive_assets.py"),
                "--workspace", str(workspace), "--mapping", str(asset_mapping),
                "--archived-by", "code-map-agent-1",
            )
            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "attest_environment.py"),
                "--workspace", str(workspace), "--env-id", "ENV-001",
                "--inventory-lead-id", "inventory-lead-1", "--account-ready", "--seed-ready",
                "--network-ready", "--permissions-ready", "--notes", "fixture readiness verified",
            )

            default_steps = root / "steps-default.md"
            default_steps.write_text("1. Launch the signed-out app.\n2. Observe the login form.\n", encoding="utf-8")
            error_steps = root / "steps-error.md"
            error_steps.write_text("1. Enter an invalid code.\n2. Submit and observe the inline error.\n", encoding="utf-8")

            frozen_environments = (workspace / "environments.json").read_text(encoding="utf-8")
            mutated_environments = json.loads(frozen_environments)
            mutated_environments["environments"][0]["app_version"] = "9.9.9"
            (workspace / "environments.json").write_text(json.dumps(mutated_environments), encoding="utf-8")
            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "capture_state.py"),
                "--workspace", str(workspace), "--inventory-id", "INV-ENV-MUTATION",
                "--feature-id", "FEATURE-AUTH", "--page-id", "PAGE-LOGIN",
                "--state-id", "STATE-DEFAULT", "--env-id", "ENV-001", "--steps", str(default_steps),
                "--issued-by", "evidence-administrator-1", "--captured-by", "runtime-state-agent-1",
                "--android-bin", str(FAKE_ANDROID), "--adb-bin", str(FAKE_ANDROID), env=process_env, expect=2,
            )
            (workspace / "environments.json").write_text(frozen_environments, encoding="utf-8")

            frozen_apk = apk.read_bytes()
            apk.write_bytes(frozen_apk + b"changed")
            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "capture_state.py"),
                "--workspace", str(workspace), "--inventory-id", "INV-APK-MUTATION",
                "--feature-id", "FEATURE-AUTH", "--page-id", "PAGE-LOGIN",
                "--state-id", "STATE-DEFAULT", "--env-id", "ENV-001", "--steps", str(default_steps),
                "--issued-by", "evidence-administrator-1", "--captured-by", "runtime-state-agent-1",
                "--android-bin", str(FAKE_ANDROID), "--adb-bin", str(FAKE_ANDROID), env=process_env, expect=2,
            )
            apk.write_bytes(frozen_apk)

            source_file = project / "app" / "Login.kt"
            frozen_source = source_file.read_text(encoding="utf-8")
            source_file.write_text(frozen_source + "// dirty\n", encoding="utf-8")
            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "capture_state.py"),
                "--workspace", str(workspace), "--inventory-id", "INV-SOURCE-MUTATION",
                "--feature-id", "FEATURE-AUTH", "--page-id", "PAGE-LOGIN",
                "--state-id", "STATE-DEFAULT", "--env-id", "ENV-001", "--steps", str(default_steps),
                "--issued-by", "evidence-administrator-1", "--captured-by", "runtime-state-agent-1",
                "--android-bin", str(FAKE_ANDROID), "--adb-bin", str(FAKE_ANDROID), env=process_env, expect=2,
            )
            source_file.write_text(frozen_source, encoding="utf-8")

            first = run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "capture_state.py"),
                "--workspace", str(workspace),
                "--inventory-id", "INV-AUTH-LOGIN-DEFAULT",
                "--feature-id", "FEATURE-AUTH", "--page-id", "PAGE-LOGIN",
                "--state-id", "STATE-DEFAULT", "--env-id", "ENV-001",
                "--steps", str(default_steps),
                "--issued-by", "evidence-administrator-1",
                "--captured-by", "runtime-state-agent-1",
                "--launch", "--android-bin", str(FAKE_ANDROID), "--adb-bin", str(FAKE_ANDROID),
                env=process_env,
            )
            first_evidence = json.loads(first.stdout)["evidence_id"]

            replacement = run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "capture_state.py"),
                "--workspace", str(workspace),
                "--inventory-id", "INV-AUTH-LOGIN-DEFAULT",
                "--feature-id", "FEATURE-AUTH", "--page-id", "PAGE-LOGIN",
                "--state-id", "STATE-DEFAULT", "--env-id", "ENV-001",
                "--steps", str(default_steps),
                "--issued-by", "evidence-administrator-1",
                "--captured-by", "runtime-state-agent-1",
                "--supersedes-evidence", first_evidence,
                "--launch", "--android-bin", str(FAKE_ANDROID), "--adb-bin", str(FAKE_ANDROID),
                env=process_env,
            )
            default_evidence = json.loads(replacement.stdout)["evidence_id"]

            error_env = process_env.copy()
            error_env["FAKE_ANDROID_STATE"] = "invalid-code"
            second = run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "capture_state.py"),
                "--workspace", str(workspace),
                "--inventory-id", "INV-AUTH-LOGIN-ERROR",
                "--feature-id", "FEATURE-AUTH", "--page-id", "PAGE-LOGIN",
                "--state-id", "STATE-INVALID-CODE", "--env-id", "ENV-001",
                "--steps", str(error_steps),
                "--issued-by", "evidence-administrator-1",
                "--captured-by", "runtime-state-agent-1",
                "--previous-evidence", default_evidence, "--include-diff",
                "--android-bin", str(FAKE_ANDROID), "--adb-bin", str(FAKE_ANDROID),
                env=error_env,
            )
            second_evidence = json.loads(second.stdout)["evidence_id"]

            for anchored_evidence in (first_evidence, default_evidence, second_evidence):
                run(
                    sys.executable,
                    str(CONTROLLER_SKILL / "scripts" / "anchor_phase2_evidence.py"),
                    "--run-dir", str(run_dir), "--evidence-id", anchored_evidence,
                    "--anchored-by", "migration-controller-1",
                )

            index_before_failure = (workspace / "evidence-index.csv").read_text(encoding="utf-8")
            zero_error_env = process_env.copy()
            zero_error_env["FAKE_ANDROID_ERROR_ZERO"] = "screen capture"
            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "capture_state.py"),
                "--workspace", str(workspace),
                "--inventory-id", "INV-AUTH-FAILED-CAPTURE",
                "--feature-id", "FEATURE-AUTH", "--page-id", "PAGE-LOGIN",
                "--state-id", "STATE-FAILED-CAPTURE", "--env-id", "ENV-001",
                "--steps", str(default_steps),
                "--issued-by", "evidence-administrator-1",
                "--captured-by", "runtime-state-agent-1",
                "--android-bin", str(FAKE_ANDROID), "--adb-bin", str(FAKE_ANDROID),
                env=zero_error_env, expect=2,
            )
            self.assertEqual(index_before_failure, (workspace / "evidence-index.csv").read_text(encoding="utf-8"))
            self.assertEqual(len(list((workspace / "attempts").glob("ATT-*.json"))), 2)

            claims = [
                {
                    "inventory_id": "INV-AUTH-LOGIN-DEFAULT", "feature_id": "FEATURE-AUTH",
                    "feature_name": "Authentication", "page_id": "PAGE-LOGIN", "page_name": "Login",
                    "state_id": "STATE-DEFAULT", "state_name": "Default", "env_id": "ENV-001",
                    "evidence_id": default_evidence, "entry_condition": "App opened while signed out",
                    "action_summary": "Open login", "expected_observable": "Login form is visible",
                    "actual_observable": "Login form is visible", "code_refs": ["app/Login.kt:10"],
                    "business_rule_refs": ["BR-AUTH-DEFAULT-NONE"],
                    "data_dependency_refs": ["DATA-AUTH-NONE"],
                    "system_capability_refs": ["SYS-AUTH-NONE"],
                    "third_party_dependency_refs": ["SDK-AUTH-NONE"],
                    "asset_ids": ["ASSET-AUTH-LOGO"],
                    "responsible_agent": "runtime-state-agent-1", "row_status": "CAPTURED",
                },
                {
                    "inventory_id": "INV-AUTH-LOGIN-ERROR", "feature_id": "FEATURE-AUTH",
                    "feature_name": "Authentication", "page_id": "PAGE-LOGIN", "page_name": "Login",
                    "state_id": "STATE-INVALID-CODE", "state_name": "Invalid code", "env_id": "ENV-001",
                    "evidence_id": second_evidence, "entry_condition": "Invalid verification code submitted",
                    "transition_from_state_id": "STATE-DEFAULT", "predecessor_evidence_id": default_evidence,
                    "action_summary": "Submit invalid code", "expected_observable": "Error is visible",
                    "actual_observable": "Error is visible", "code_refs": ["app/Login.kt:42"],
                    "business_rule_refs": ["BR-AUTH-AUDIT"],
                    "data_dependency_refs": ["DATA-AUTH-NONE"],
                    "system_capability_refs": ["SYS-AUTH-NONE"],
                    "third_party_dependency_refs": ["SDK-AUTH-NONE"],
                    "asset_ids": ["NONE_FOUND"],
                    "responsible_agent": "runtime-state-agent-1", "row_status": "CAPTURED",
                },
            ]
            claims_path = workspace / "claims" / "auth.json"
            claims_path.write_text(json.dumps(claims, indent=2) + "\n", encoding="utf-8")

            invalid_claims = json.loads(json.dumps(claims))
            invalid_claims[1]["transition_from_state_id"] = "STATE-DOES-NOT-EXIST"
            claims_path.write_text(json.dumps(invalid_claims, indent=2) + "\n", encoding="utf-8")
            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "build_inventory.py"),
                "--workspace", str(workspace), "--claims", str(claims_path), expect=2,
            )
            claims_path.write_text(json.dumps(claims, indent=2) + "\n", encoding="utf-8")
            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "build_inventory.py"),
                "--workspace", str(workspace), "--claims", str(claims_path),
            )

            coverage_fields = [
                "feature_id", "feature_name", "applicable_env_ids", "code_mapped", "runtime_states_captured",
                "business_rules_mapped", "data_dependencies_mapped", "status", "owner", "notes",
            ]
            write_csv(workspace / "coverage-ledger.csv", coverage_fields, [{
                "feature_id": "FEATURE-AUTH", "feature_name": "Authentication",
                "applicable_env_ids": '["ENV-001"]', "code_mapped": "true",
                "runtime_states_captured": "true", "business_rules_mapped": "true",
                "data_dependencies_mapped": "true", "status": "COMPLETE",
                "owner": "inventory-lead-1", "notes": "Two observable login states verified",
            }])
            code_fields = [
                "code_ref", "feature_id", "page_id", "state_candidate_id", "component_type", "symbol",
                "file_path", "line", "coverage_disposition", "owner", "status", "notes",
            ]
            write_csv(workspace / "catalogs" / "code-map.csv", code_fields, [
                {
                    "code_ref": "app/Login.kt:10", "feature_id": "FEATURE-AUTH", "page_id": "PAGE-LOGIN",
                    "state_candidate_id": "STATE-DEFAULT", "component_type": "function",
                    "symbol": "renderDefaultLogin", "file_path": "app/Login.kt", "line": "10",
                    "coverage_disposition": "IN_SCOPE", "owner": "code-map-agent-1",
                    "status": "VERIFIED", "notes": "Runtime-correlated",
                },
                {
                    "code_ref": "app/Login.kt:42", "feature_id": "FEATURE-AUTH", "page_id": "PAGE-LOGIN",
                    "state_candidate_id": "STATE-INVALID-CODE", "component_type": "function",
                    "symbol": "renderInvalidCodeError", "file_path": "app/Login.kt", "line": "42",
                    "coverage_disposition": "IN_SCOPE", "owner": "code-map-agent-1",
                    "status": "VERIFIED", "notes": "Runtime-correlated",
                },
            ])
            write_csv(
                workspace / "catalogs" / "business-rules.csv",
                ["business_rule_id", "feature_id", "page_id", "state_id", "condition", "outcome", "code_refs", "test_refs", "owner", "status", "notes"],
                [
                    {
                        "business_rule_id": "BR-AUTH-DEFAULT-NONE", "feature_id": "FEATURE-AUTH",
                        "page_id": "PAGE-LOGIN", "state_id": "STATE-DEFAULT", "condition": "NONE_FOUND",
                        "outcome": "NO_RULE_BEYOND_VISIBLE_STATE", "code_refs": '["app/Login.kt:10"]',
                        "test_refs": "[]", "owner": "business-rule-agent-1", "status": "VERIFIED",
                        "notes": "Explicit no-rule audit for the default state",
                    },
                    {
                        "business_rule_id": "BR-AUTH-AUDIT", "feature_id": "FEATURE-AUTH", "page_id": "PAGE-LOGIN",
                        "state_id": "STATE-INVALID-CODE", "condition": "Invalid code submitted", "outcome": "Inline error",
                        "code_refs": '["app/Login.kt:42"]', "test_refs": "[]", "owner": "business-rule-agent-1",
                        "status": "VERIFIED", "notes": "Explicit audit row",
                    },
                ],
            )
            write_csv(
                workspace / "catalogs" / "data-dependencies.csv",
                ["data_dependency_id", "feature_id", "dependency_type", "name", "direction", "source_ref", "sensitive", "migration_risk", "owner", "status", "notes"],
                [{
                    "data_dependency_id": "DATA-AUTH-NONE", "feature_id": "FEATURE-AUTH", "dependency_type": "NONE",
                    "name": "NONE_FOUND", "direction": "NONE", "source_ref": "app/Login.kt:10", "sensitive": "false",
                    "migration_risk": "none", "owner": "data-dependency-agent-1", "status": "VERIFIED",
                    "notes": "Explicitly audited; fixture has no external data dependency",
                }],
            )
            write_csv(
                workspace / "catalogs" / "system-capabilities.csv",
                ["system_capability_id", "feature_id", "capability_type", "name", "permission_or_api", "source_ref", "migration_risk", "owner", "status", "notes"],
                [{
                    "system_capability_id": "SYS-AUTH-NONE", "feature_id": "FEATURE-AUTH", "capability_type": "NONE",
                    "name": "NONE_FOUND", "permission_or_api": "NONE", "source_ref": "app/Login.kt:10",
                    "migration_risk": "none", "owner": "data-dependency-agent-1", "status": "VERIFIED",
                    "notes": "Explicitly audited; fixture has no system capability",
                }],
            )
            write_csv(
                workspace / "catalogs" / "third-party-dependencies.csv",
                ["third_party_dependency_id", "feature_id", "name", "version", "purpose", "source_ref", "data_shared", "migration_risk", "owner", "status", "notes"],
                [{
                    "third_party_dependency_id": "SDK-AUTH-NONE", "feature_id": "FEATURE-AUTH", "name": "NONE_FOUND",
                    "version": "NONE", "purpose": "NONE", "source_ref": "app/Login.kt:10", "data_shared": "false",
                    "migration_risk": "none", "owner": "data-dependency-agent-1", "status": "VERIFIED",
                    "notes": "Explicitly audited; fixture has no third-party SDK",
                }],
            )

            archived_asset = workspace / "asset-package" / "files" / "ASSET-AUTH-LOGO" / "login_logo.svg"
            archived_asset_bytes = archived_asset.read_bytes()
            archived_asset.write_bytes(archived_asset_bytes + b"tamper")
            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "validate_evidence.py"),
                "--workspace", str(workspace), "--reviewer", "coverage-checker-1",
                "--decision", "PASS", "--attest-visual-review", "--attest-source-runtime-crosscheck",
                expect=1,
            )
            archived_asset.write_bytes(archived_asset_bytes)

            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "validate_evidence.py"),
                "--workspace", str(workspace), "--reviewer", "evidence-administrator-1",
                "--decision", "PASS", "--attest-visual-review", "--attest-source-runtime-crosscheck",
                expect=1,
            )

            index_original = (workspace / "evidence-index.csv").read_text(encoding="utf-8")
            index_rows = read_rows = list(csv.DictReader(index_original.splitlines()))
            for row in index_rows:
                if row["evidence_id"] == default_evidence:
                    row["relative_path"] = "../outside"
            write_csv(workspace / "evidence-index.csv", list(read_rows[0].keys()), index_rows)
            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "build_inventory.py"),
                "--workspace", str(workspace), "--claims", str(claims_path), expect=2,
            )
            (workspace / "evidence-index.csv").write_text(index_original, encoding="utf-8")

            active_index = next(row for row in csv.DictReader(index_original.splitlines()) if row["evidence_id"] == default_evidence)
            evidence_dir = workspace / active_index["relative_path"]
            screenshot = evidence_dir / "screenshot.png"
            metadata_path = evidence_dir / "metadata.json"
            manifest_path = evidence_dir / "manifest.sha256"
            original_screenshot = screenshot.read_bytes()
            original_metadata = metadata_path.read_text(encoding="utf-8")
            original_manifest = manifest_path.read_text(encoding="utf-8")
            second_index = next(
                row for row in csv.DictReader(index_original.splitlines())
                if row["evidence_id"] == second_evidence
            )
            replacement_screenshot = (workspace / second_index["relative_path"] / "screenshot.png").read_bytes()
            screenshot.chmod(0o644)
            metadata_path.chmod(0o644)
            manifest_path.chmod(0o644)
            screenshot.write_bytes(replacement_screenshot)
            changed_metadata = json.loads(original_metadata)
            for artifact in changed_metadata["artifacts"]:
                if artifact["relative_path"] == "screenshot.png":
                    artifact["sha256"] = hashlib.sha256(replacement_screenshot).hexdigest()
                    artifact["size_bytes"] = len(replacement_screenshot)
            metadata_path.write_text(json.dumps(changed_metadata, indent=2) + "\n", encoding="utf-8")
            manifest_lines_changed = []
            for line in original_manifest.splitlines():
                if line.endswith("  screenshot.png"):
                    line = hashlib.sha256(screenshot.read_bytes()).hexdigest() + "  screenshot.png"
                elif line.endswith("  metadata.json"):
                    line = hashlib.sha256(metadata_path.read_bytes()).hexdigest() + "  metadata.json"
                manifest_lines_changed.append(line)
            manifest_path.write_text("\n".join(manifest_lines_changed) + "\n", encoding="utf-8")
            locally_rehashed_index = list(csv.DictReader(index_original.splitlines()))
            for row in locally_rehashed_index:
                if row["evidence_id"] == default_evidence:
                    row["metadata_sha256"] = hashlib.sha256(metadata_path.read_bytes()).hexdigest()
            write_csv(
                workspace / "evidence-index.csv", list(locally_rehashed_index[0].keys()), locally_rehashed_index
            )
            screenshot.chmod(0o444)
            metadata_path.chmod(0o444)
            manifest_path.chmod(0o444)
            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "validate_evidence.py"),
                "--workspace", str(workspace), "--reviewer", "coverage-checker-1", "--decision", "PASS",
                "--attest-visual-review", "--attest-source-runtime-crosscheck", expect=1,
            )
            screenshot.chmod(0o644)
            metadata_path.chmod(0o644)
            manifest_path.chmod(0o644)
            screenshot.write_bytes(original_screenshot)
            metadata_path.write_text(original_metadata, encoding="utf-8")
            manifest_path.write_text(original_manifest, encoding="utf-8")
            (workspace / "evidence-index.csv").write_text(index_original, encoding="utf-8")
            screenshot.chmod(0o444)
            metadata_path.chmod(0o444)
            manifest_path.chmod(0o444)

            recheck_fields = (workspace / "rechecks.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
            write_csv(workspace / "rechecks.csv", recheck_fields, [{
                "rework_id": "RW-CRITICAL-001", "opened_at": "2026-01-01T00:00:00Z",
                "inventory_id": "INV-AUTH-LOGIN-ERROR", "feature_id": "FEATURE-AUTH",
                "page_id": "PAGE-LOGIN", "state_id": "STATE-INVALID-CODE", "env_id": "ENV-001",
                "evidence_id": second_evidence, "severity": "CRITICAL", "problem_code": "STATE",
                "reason": "fixture probe", "assigned_to": "runtime-state-agent-1",
                "completion_condition": "new evidence", "status": "CLOSED",
                "resolved_at": "2026-01-01T01:00:00Z", "resolution_evidence_id": second_evidence,
                "closed_by": "runtime-state-agent-1",
            }])
            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "validate_evidence.py"),
                "--workspace", str(workspace), "--reviewer", "coverage-checker-1", "--decision", "PASS",
                "--attest-visual-review", "--attest-source-runtime-crosscheck", expect=1,
            )
            write_csv(workspace / "rechecks.csv", recheck_fields, [])

            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "validate_evidence.py"),
                "--workspace", str(workspace), "--reviewer", "coverage-checker-1", "--decision", "PASS",
                "--attest-visual-review", "--attest-source-runtime-crosscheck",
            )
            run(
                sys.executable, str(CONTROLLER_SKILL / "scripts" / "validate_gate.py"),
                "--run-dir", str(run_dir), "--phase", "2", "--write",
            )
            ledger = {row["phase"]: row for row in csv.DictReader((run_dir / "controller" / "task-ledger.csv").read_text(encoding="utf-8").splitlines())}
            self.assertEqual(ledger["1"]["status"], "PASS")
            self.assertEqual(ledger["2"]["status"], "PASS")

            closure = json.loads((workspace / "closure-report.json").read_text(encoding="utf-8"))
            self.assertTrue(closure["evidence_chain_closed"])
            self.assertEqual(set(closure["covered_feature_ids"]), {"FEATURE-AUTH"})
            self.assertTrue((workspace / "CLOSED").is_file())
            self.assertEqual(json.loads((workspace / "phase-manifest.json").read_text())["status"], "CLOSED")
            final_inventory = csv.DictReader((workspace / "inventory.csv").read_text(encoding="utf-8").splitlines())
            self.assertEqual({row["row_status"] for row in final_inventory}, {"REVIEWED"})
            final_assets = list(csv.DictReader(
                (workspace / "asset-inventory.csv").read_text(encoding="utf-8").splitlines()
            ))
            self.assertEqual([row["asset_id"] for row in final_assets], ["ASSET-AUTH-LOGO"])
            self.assertEqual({row["status"] for row in final_assets}, {"REVIEWED"})
            self.assertEqual({row["reviewed_by"] for row in final_assets}, {"coverage-checker-1"})
            self.assertEqual(closure["archived_assets"], 1)
            final_index = csv.DictReader((workspace / "evidence-index.csv").read_text(encoding="utf-8").splitlines())
            accepted_statuses = {row["status"] for row in final_index}
            self.assertEqual(accepted_statuses, {"ACCEPTED", "SUPERSEDED"})

            commands = [json.loads(line) for line in command_log.read_text(encoding="utf-8").splitlines()]
            diff_index = next(i for i, command in enumerate(commands) if "--diff" in command)
            self.assertEqual(commands[diff_index][0], "layout")
            self.assertEqual(commands[diff_index + 1][0], "layout")
            self.assertEqual(commands[diff_index + 2][:2], ["screen", "capture"])
            self.assertIn("--device=emulator-5554", commands[diff_index + 2])
            default_dir = workspace / next(row["relative_path"] for row in csv.DictReader(index_original.splitlines()) if row["evidence_id"] == default_evidence)
            error_dir = workspace / next(row["relative_path"] for row in csv.DictReader(index_original.splitlines()) if row["evidence_id"] == second_evidence)
            self.assertNotEqual(sha256(default_dir / "screenshot.png"), sha256(error_dir / "screenshot.png"))
            self.assertNotEqual(sha256(default_dir / "layout.json"), sha256(error_dir / "layout.json"))
            self.assertIn("Invalid verification code", (error_dir / "layout.json").read_text(encoding="utf-8"))

            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "capture_state.py"),
                "--workspace", str(workspace), "--inventory-id", "INV-CLOSED-WRITE",
                "--feature-id", "FEATURE-AUTH", "--page-id", "PAGE-LOGIN",
                "--state-id", "STATE-CLOSED", "--env-id", "ENV-001", "--steps", str(default_steps),
                "--issued-by", "evidence-administrator-1", "--captured-by", "runtime-state-agent-1",
                "--android-bin", str(FAKE_ANDROID), "--adb-bin", str(FAKE_ANDROID), env=process_env, expect=2,
            )

            screenshot = default_dir / "screenshot.png"
            screenshot.chmod(0o644)
            screenshot.write_bytes(screenshot.read_bytes() + b"post-closure-tamper")
            run(
                sys.executable, str(CONTROLLER_SKILL / "scripts" / "validate_gate.py"),
                "--run-dir", str(run_dir), "--phase", "2", expect=1,
            )
            run(
                sys.executable, str(INVENTORY_SKILL / "scripts" / "validate_evidence.py"),
                "--workspace", str(workspace), "--reviewer", "coverage-checker-1", "--decision", "PASS",
                "--attest-visual-review", "--attest-source-runtime-crosscheck", expect=1,
            )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
