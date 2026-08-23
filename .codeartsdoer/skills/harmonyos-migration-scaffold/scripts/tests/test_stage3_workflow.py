#!/usr/bin/env python3
"""End-to-end and adversarial tests for the governed Phase 3 scaffold."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from phase2_fixture import CONTROLLER_SKILL, build_closed_phase2, sha256, write_csv


SKILL = HERE.parents[1]
FAKE_HARMONY = (HERE / "fake_harmony.py").resolve()


def run_cmd(
    *args: str,
    expect: int = 0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(args), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, check=False,
    )
    if completed.returncode != expect:
        raise AssertionError(
            f"Expected exit {expect}, got {completed.returncode}\nCOMMAND: {args}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def issue_phase3(run_dir: Path) -> Path:
    issued = run_cmd(
        sys.executable,
        str(CONTROLLER_SKILL / "scripts" / "issue_phase3_work_order.py"),
        "--run-dir", str(run_dir),
        "--issued-by", "migration-controller-1",
        "--architecture-lead-id", "architecture-lead-1",
        "--toolchain-agent-id", "toolchain-agent-1",
        "--navigation-agent-id", "navigation-agent-1",
        "--public-ui-agent-id", "public-ui-agent-1",
        "--capability-contract-agent-id", "capability-agent-1",
        "--architecture-acceptance-agent-id", "architecture-acceptance-1",
    )
    return Path(json.loads(issued.stdout)["work_order"])


def initialize_phase3(run_dir: Path, work_order: Path) -> Path:
    initialized = run_cmd(
        sys.executable, str(SKILL / "scripts" / "init_scaffold.py"),
        "--run-dir", str(run_dir), "--work-order", str(work_order),
        "--architecture-lead", "architecture-lead-1",
    )
    return Path(json.loads(initialized.stdout)["workspace"])


def create_project_and_registries(workspace: Path) -> None:
    project = workspace / "harmony-project"
    entry = project / "entry"
    source = entry / "src"
    source.mkdir(parents=True, exist_ok=True)
    (entry / "module.json5").write_text("{ module: { name: 'entry' } }\n", encoding="utf-8")
    (project / "oh-package-lock.json5").write_text("{ lockfileVersion: 3 }\n", encoding="utf-8")
    (source / "LoginShell.ets").write_text(
        "const FEATURE_ID = 'FEATURE-AUTH'\n"
        "const PAGE_ID = 'PAGE-LOGIN'\n"
        "const PAGE_SHELL_ID = 'PSHELL-LOGIN'\n"
        "const ROUTE_ID = 'ROUTE-LOGIN'\n"
        "export struct LoginShell {}\n",
        encoding="utf-8",
    )
    (source / "Routes.ets").write_text(
        "export const LoginRouteRegistry = 'ROUTE-LOGIN'\n", encoding="utf-8"
    )
    foundation_symbols = {
        "COLOR": "ColorTokens",
        "TYPOGRAPHY": "TypographyTokens",
        "SPACING": "SpacingTokens",
        "THEME": "AppTheme",
        "PAGE_CONTAINER": "PageContainer",
        "LOADING_SHELL": "CommonLoadingShell",
        "EMPTY_SHELL": "CommonEmptyShell",
        "ERROR_SHELL": "CommonErrorShell",
        "RESPONSIVE_RULE": "ResponsiveRules",
    }
    (source / "Foundation.ets").write_text(
        "\n".join(f"export interface {symbol} {{}}" for symbol in foundation_symbols.values()) + "\n",
        encoding="utf-8",
    )

    write_csv(
        workspace / "module-registry.csv",
        [
            "harmony_module_id", "module_name", "layer", "module_path", "build_config_path",
            "feature_ids", "declared_dependencies", "created_by", "status", "notes",
        ],
        [
            {
                "harmony_module_id": "HMOD-ENTRY", "module_name": "entry", "layer": "app",
                "module_path": "entry", "build_config_path": "entry/module.json5",
                "feature_ids": "FEATURE-AUTH", "declared_dependencies": "",
                "created_by": "toolchain-agent-1", "status": "READY", "notes": "",
            }
        ],
    )
    asset_fields, asset_rows = read_csv(workspace / "asset-registry.csv")
    if len(asset_rows) != 1 or asset_rows[0]["asset_id"] != "ASSET-AUTH-LOGO":
        raise AssertionError("Phase 3 did not seed one row for the real Phase 2 asset")
    asset_rows[0].update(
        {
            "target_module_id": "HMOD-ENTRY",
            "target_path": "entry/src/main/resources/base/media/login_logo.svg",
            "target_symbol": "login_logo",
            "planned_mode": "DIRECT_COPY",
            "decision": "COPY_UNCHANGED",
            "created_by": "architecture-lead-1",
            "status": "READY",
            "notes": "Planned landing only; Phase 3 does not copy the asset into implementation.",
        }
    )
    write_csv(workspace / "asset-registry.csv", asset_fields, asset_rows)
    architecture_fields, architecture = read_csv(workspace / "architecture-map.csv")
    if len(architecture) != 1:
        raise AssertionError(f"Expected one frozen Phase 2 source row, got {len(architecture)}")
    architecture[0].update(
        {
            "mapping_type": "ROUTE_PAGE",
            "harmony_module_id": "HMOD-ENTRY",
            "route_id": "ROUTE-LOGIN",
            "surface_shell_id": "",
            "page_shell_id": "PSHELL-LOGIN",
            "shell_file": "entry/src/LoginShell.ets",
            "screenshot_ids": "HSCREEN-LOGIN",
            "verification_id": "HVER-001",
            "mapped_by": "navigation-agent-1",
            "mapping_status": "SHELL_CREATED_PENDING_IMPLEMENTATION",
        }
    )
    write_csv(workspace / "architecture-map.csv", architecture_fields, architecture)
    write_csv(
        workspace / "route-registry.csv",
        [
            "route_id", "page_id", "page_shell_id", "harmony_module_id", "route_pattern",
            "registry_file", "registry_symbol", "page_shell_file", "feature_ids", "created_by",
            "status", "notes",
        ],
        [
            {
                "route_id": "ROUTE-LOGIN", "page_id": "PAGE-LOGIN",
                "page_shell_id": "PSHELL-LOGIN", "harmony_module_id": "HMOD-ENTRY",
                "route_pattern": "/login", "registry_file": "entry/src/Routes.ets",
                "registry_symbol": "LoginRouteRegistry", "page_shell_file": "entry/src/LoginShell.ets",
                "feature_ids": "FEATURE-AUTH", "created_by": "navigation-agent-1",
                "status": "READY", "notes": "",
            }
        ],
    )

    public_rows: list[dict[str, str]] = []
    for number, (foundation_type, symbol) in enumerate(foundation_symbols.items(), start=1):
        public_rows.append(
            {
                "foundation_id": f"FOUNDATION-{number:03d}",
                "foundation_type": foundation_type,
                "harmony_module_id": "HMOD-ENTRY",
                "file_path": "entry/src/Foundation.ets",
                "symbol": symbol,
                "may_bind_to_placeholder": (
                    "false" if foundation_type in {"LOADING_SHELL", "EMPTY_SHELL", "ERROR_SHELL"}
                    else "true"
                ),
                "created_by": "public-ui-agent-1",
                "status": "READY",
                "notes": "",
            }
        )
    write_csv(
        workspace / "public-ui-registry.csv",
        [
            "foundation_id", "foundation_type", "harmony_module_id", "file_path", "symbol",
            "may_bind_to_placeholder", "created_by", "status", "notes",
        ],
        public_rows,
    )

    _, capabilities = read_csv(workspace / "capability-contracts.csv")
    if capabilities:
        raise AssertionError("Explicit Phase 2 NONE sentinels must not create Harmony capability work")
    status_fields, statuses = read_csv(workspace / "migration-status.csv")
    if len(statuses) != 1 or statuses[0]["source_kind"] != "INVENTORY_ROW":
        raise AssertionError("Phase 3 status seed does not match the one visual source row")
    statuses[0].update(
        {
            "target_id": "ROUTE-LOGIN",
            "status": "SHELL_CREATED_PENDING_IMPLEMENTATION",
            "updated_by": "navigation-agent-1",
        }
    )
    write_csv(workspace / "migration-status.csv", status_fields, statuses)


def freeze_environment(workspace: Path, config_path: Path) -> dict[str, object]:
    project = workspace / "harmony-project"
    executable = str(FAKE_HARMONY)
    category_values = {
        "TOOLCHAIN": ("toolchain", "TOOLCHAIN_OK", ["toolchain"]),
        "DEVICE": ("device", "DEVICE_OK", ["device", "fixture-001"]),
        "BUNDLE_CHECK": (
            "bundle", "BUNDLE_OK", ["bundle", "fixture-001", "com.example.fixture"]
        ),
        "SIGNING_CHECK": (
            "signing", "SIGNING_OK", ["signing", "com.example.fixture"]
        ),
        "CLEAN_BUILD": ("build", "BUILD_OK", ["build", "build/app.hap"]),
        "INSTALL": ("install", "INSTALL_OK", ["install", "fixture-001", "build/app.hap"]),
        "LAUNCH": (
            "launch", "LAUNCH_OK",
            ["launch", "fixture-001", "com.example.fixture", "EntryAbility"],
        ),
        "ROUTE_SMOKE": (
            "smoke", "SMOKE_OK", ["smoke", "fixture-001", "com.example.fixture"]
        ),
        "SCREENSHOT_CAPTURE": (
            "screenshot", "SCREENSHOT_OK", ["screenshot", "fixture-001"]
        ),
    }
    contracts = {
        category: {
            "executable": executable,
            "required_argv_tokens": required,
            "success_output_contains": [success],
            "error_output_contains": ["Error:", "Failed:", "Failure:"],
        }
        for category, (_action, success, required) in category_values.items()
    }
    environment = {
        "henv_id": "HENV-001",
        "created_at": "2026-08-23T00:00:00Z",
        "created_by": "architecture-lead-1",
        "host": {"os": "test", "os_version": "1", "architecture": "arm64"},
        "toolchain": {
            "deveco_studio_version": "fixture",
            "harmonyos_sdk_api_target": "fixture-api",
            "compatible_api": "fixture-api",
            "build_tool_version": "fixture",
            "package_manager_version": "fixture",
            "runtime_version": "fixture",
            "category_contracts": contracts,
        },
        "application": {
            "bundle_name": "com.example.fixture",
            "product_name": "default",
            "build_mode": "debug",
            "dependency_lock_file": "oh-package-lock.json5",
            "dependency_lock_sha256": sha256(project / "oh-package-lock.json5"),
        },
        "signing": {
            "configuration_reference": "SIGNING-CONFIG-REF-001",
            "certificate_alias": "fixture",
            "certificate_fingerprint_sha256": "A" * 64,
            "certificate_expires_at": "2030-01-01T00:00:00Z",
            "secret_storage_reference": "KEYCHAIN-REF-001",
            "secrets_embedded": False,
        },
        "bundle_conflict_check_scope": "LOCAL_DEVICE",
        "target_device_classes": ["phone"],
        "devices": [
            {
                "device_id": "HDEVICE-001",
                "is_baseline": True,
                "required": True,
                "screenshot_required": True,
                "device_type": "emulator",
                "model": "Fixture",
                "serial": "fixture-001",
                "os_version": "fixture",
                "api_level": "fixture-api",
                "resolution": "320x640",
            }
        ],
    }
    config_path.write_text(json.dumps(environment, indent=2) + "\n", encoding="utf-8")
    run_cmd(
        sys.executable, str(SKILL / "scripts" / "freeze_environment.py"),
        "--workspace", str(workspace), "--config", str(config_path),
        "--frozen-by", "not-the-architecture-lead", expect=2,
    )
    run_cmd(
        sys.executable, str(SKILL / "scripts" / "freeze_environment.py"),
        "--workspace", str(workspace), "--config", str(config_path),
        "--frozen-by", "architecture-lead-1",
    )
    return environment


def verification_plan(
    verification_id: str,
    screenshot_id: str,
    suffix: str,
    *,
    executed_by: str = "toolchain-agent-1",
) -> dict[str, object]:
    executable = str(FAKE_HARMONY)
    result_path = f"build/results/route-{suffix}.json"
    screenshot_path = f"build/screenshots/{screenshot_id}.png"
    return {
        "verification_id": verification_id,
        "henv_id": "HENV-001",
        "executed_by": executed_by,
        "commands": [
            {
                "command_id": f"CMD-{suffix}-TOOLCHAIN", "category": "TOOLCHAIN", "cwd": ".",
                "argv": [executable, "toolchain"],
            },
            {
                "command_id": f"CMD-{suffix}-DEVICE", "category": "DEVICE",
                "device_id": "HDEVICE-001", "cwd": ".",
                "argv": [executable, "device", "--serial", "fixture-001"],
            },
            {
                "command_id": f"CMD-{suffix}-BUNDLE", "category": "BUNDLE_CHECK",
                "device_id": "HDEVICE-001", "cwd": ".",
                "argv": [
                    executable, "bundle", "--serial", "fixture-001",
                    "--bundle", "com.example.fixture",
                ],
            },
            {
                "command_id": f"CMD-{suffix}-SIGNING", "category": "SIGNING_CHECK", "cwd": ".",
                "argv": [executable, "signing", "--bundle", "com.example.fixture"],
            },
            {
                "command_id": f"CMD-{suffix}-BUILD", "category": "CLEAN_BUILD", "cwd": ".",
                "argv": [executable, "build", "--artifact", "build/app.hap"],
            },
            {
                "command_id": f"CMD-{suffix}-INSTALL", "category": "INSTALL",
                "device_id": "HDEVICE-001", "cwd": ".",
                "argv": [
                    executable, "install", "--serial", "fixture-001",
                    "--artifact", "build/app.hap",
                ],
            },
            {
                "command_id": f"CMD-{suffix}-LAUNCH", "category": "LAUNCH",
                "device_id": "HDEVICE-001", "cwd": ".",
                "argv": [
                    executable, "launch", "--serial", "fixture-001",
                    "--bundle", "com.example.fixture", "--ability", "EntryAbility",
                ],
            },
            {
                "command_id": f"CMD-{suffix}-SMOKE", "category": "ROUTE_SMOKE",
                "device_id": "HDEVICE-001", "target_kind": "ROUTE_PAGE",
                "target_id": "ROUTE-LOGIN", "page_id": "PAGE-LOGIN",
                "page_shell_id": "PSHELL-LOGIN", "result_output_path": result_path,
                "cwd": ".",
                "argv": [
                    executable, "smoke", "--serial", "fixture-001",
                    "--bundle", "com.example.fixture", "--kind", "ROUTE_PAGE",
                    "--target", "ROUTE-LOGIN", "--page", "PAGE-LOGIN",
                    "--shell", "PSHELL-LOGIN", "--result", result_path,
                ],
            },
            {
                "command_id": f"CMD-{suffix}-SCREEN", "category": "SCREENSHOT_CAPTURE",
                "device_id": "HDEVICE-001", "screenshot_id": screenshot_id,
                "target_kind": "ROUTE_PAGE", "target_id": "ROUTE-LOGIN",
                "feature_ids": ["FEATURE-AUTH"], "page_id": "PAGE-LOGIN",
                "page_shell_id": "PSHELL-LOGIN", "smoke_command_id": f"CMD-{suffix}-SMOKE",
                "output_path": screenshot_path, "cwd": ".",
                "argv": [
                    executable, "screenshot", "--serial", "fixture-001",
                    "--target", "ROUTE-LOGIN", "--output", screenshot_path,
                    "--width", "320", "--height", "640",
                ],
            },
        ],
        "artifact_paths": ["build/app.hap"],
    }


class Stage3WorkflowTest(unittest.TestCase):
    def test_initialization_creates_template_project_and_locks_advanced_handoff(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stage3-template-test-") as temp_name:
            run_dir, _ = build_closed_phase2(Path(temp_name))
            workspace = initialize_phase3(run_dir, issue_phase3(run_dir))
            for relative in (
                "AppScope/app.json5", "build-profile.json5", "entry/src/main/module.json5",
                "entry/src/main/ets/entryability/EntryAbility.ets",
                "entry/src/main/ets/pages/Index.ets",
            ):
                self.assertTrue((workspace / "harmony-project" / relative).is_file(), relative)
            generation = json.loads((workspace / "template-generation.json").read_text(encoding="utf-8"))
            self.assertEqual(generation["template_id"], "ARKUI-STAGE-TEMPLATE-V1")
            self.assertEqual(generation["bundle_name"], "com.example.fixture")
            self.assertGreaterEqual(generation["generated_file_count"], 30)
            input_lock = json.loads((workspace / "stage-03-input-lock.json").read_text(encoding="utf-8"))
            self.assertIn("phase2_advanced", input_lock)
            self.assertIn("arkui_template", input_lock)
            obligations = json.loads((workspace / "advanced-obligations.json").read_text(encoding="utf-8"))
            self.assertEqual(obligations["obligations"], [])

    def test_secure_atomic_write_and_full_png_validation(self) -> None:
        spec = importlib.util.spec_from_file_location("stage3_common", SKILL / "scripts" / "_common.py")
        assert spec and spec.loader
        common = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(common)
        with tempfile.TemporaryDirectory(prefix="stage3-common-test-") as temp_name:
            root = Path(temp_name)
            victim = root / "victim.txt"
            victim.write_text("untouched\n", encoding="utf-8")
            try:
                (root / "report.json.tmp").symlink_to(victim)
            except OSError as exc:
                if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                    self.skipTest("Windows symbolic-link privilege is unavailable")
                raise
            common.atomic_text(root / "report.json", "safe\n")
            self.assertEqual(victim.read_text(encoding="utf-8"), "untouched\n")
            target = root / "linked.json"
            target.symlink_to(victim)
            with self.assertRaises(ValueError):
                common.atomic_text(target, "blocked\n")
            truncated = root / "truncated.png"
            truncated.write_bytes(
                b"\\x89PNG\\r\\n\\x1a\\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 320, 640)
            )
            with self.assertRaises(ValueError):
                common.png_dimensions(truncated)

    def test_governed_stage3_and_controller_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="harmony-stage3-skill-test-") as temp_name:
            root = Path(temp_name)
            run_dir, _scope_path = build_closed_phase2(root)
            work_order = issue_phase3(run_dir)
            workspace = initialize_phase3(run_dir, work_order)
            self.assertEqual(
                json.loads((workspace / "phase-manifest.json").read_text())["ownership"]
                ["architecture_acceptance_agent_id"],
                "architecture-acceptance-1",
            )
            create_project_and_registries(workspace)
            config_path = root / "henv.json"
            freeze_environment(workspace, config_path)

            wrong_plan = root / "wrong-role-plan.json"
            wrong_plan.write_text(
                json.dumps(
                    verification_plan(
                        "HVER-WRONG", "HSCREEN-WRONG", "WRONG",
                        executed_by="architecture-acceptance-1",
                    ),
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            run_cmd(
                sys.executable, str(SKILL / "scripts" / "run_verification.py"),
                "--workspace", str(workspace), "--plan", str(wrong_plan), expect=2,
            )

            stale_plan_value = verification_plan("HVER-STALE", "HSCREEN-STALE", "STALE")
            stale_result = workspace / "harmony-project" / "build" / "results" / "route-STALE.json"
            stale_result.parent.mkdir(parents=True, exist_ok=True)
            stale_result.write_text('{"status":"PASS"}\n', encoding="utf-8")
            stale_plan = root / "stale-plan.json"
            stale_plan.write_text(json.dumps(stale_plan_value, indent=2) + "\n", encoding="utf-8")
            run_cmd(
                sys.executable, str(SKILL / "scripts" / "run_verification.py"),
                "--workspace", str(workspace), "--plan", str(stale_plan), expect=2,
            )
            stale_result.unlink()

            error_plan = root / "error-plan.json"
            error_plan.write_text(
                json.dumps(verification_plan("HVER-ERROR", "HSCREEN-ERROR", "ERROR"), indent=2) + "\n",
                encoding="utf-8",
            )
            error_env = os.environ.copy()
            error_env["FAKE_HARMONY_ERROR_ZERO"] = "install"
            run_cmd(
                sys.executable, str(SKILL / "scripts" / "run_verification.py"),
                "--workspace", str(workspace), "--plan", str(error_plan),
                env=error_env, expect=1,
            )
            error_metadata = json.loads(
                (workspace / "verification" / "HVER-ERROR" / "metadata.json").read_text()
            )
            self.assertEqual(error_metadata["status"], "FAIL")
            self.assertTrue(any("INSTALL" in error for error in error_metadata["errors"]))

            rework_open = (
                sys.executable, str(SKILL / "scripts" / "manage_stage3_rework.py"),
                "--workspace", str(workspace), "--action", "open",
                "--ticket-id", "REWORK-STAGE3-001", "--problem-type", "BUILD",
                "--source-or-mapping-id", "ROUTE-LOGIN",
                "--failed-verification-id", "HVER-ERROR", "--severity", "HIGH",
                "--reason", "Install output contained a frozen error marker",
                "--completion-condition", "A new sealed HVER passes all nine categories",
                "--confirmed-by", "architecture-lead-1",
            )
            run_cmd(*rework_open, "--reviewer", "navigation-agent-1", expect=2)
            run_cmd(*rework_open, "--reviewer", "architecture-acceptance-1")

            good_plan = root / "verification-plan.json"
            good_plan.write_text(
                json.dumps(verification_plan("HVER-001", "HSCREEN-LOGIN", "GOOD"), indent=2) + "\n",
                encoding="utf-8",
            )
            run_cmd(
                sys.executable, str(SKILL / "scripts" / "run_verification.py"),
                "--workspace", str(workspace), "--plan", str(good_plan),
            )
            sealed_artifact = workspace / "verification" / "HVER-001" / "artifacts" / "ART-001-app.hap"
            self.assertTrue(sealed_artifact.is_file())
            self.assertEqual(sealed_artifact.stat().st_mode & 0o222, 0)

            rework_close = (
                sys.executable, str(SKILL / "scripts" / "manage_stage3_rework.py"),
                "--workspace", str(workspace), "--action", "close",
                "--ticket-id", "REWORK-STAGE3-001",
                "--correction-verification-id", "HVER-001",
            )
            run_cmd(*rework_close, "--reviewer", "navigation-agent-1", expect=2)
            run_cmd(*rework_close, "--reviewer", "architecture-acceptance-1")

            validation_base = (
                sys.executable, str(SKILL / "scripts" / "validate_stage3.py"),
                "--workspace", str(workspace), "--henv-id", "HENV-001",
                "--verification-id", "HVER-001", "--decision", "PASS",
                "--attest-real-file-review", "--attest-placeholder-boundaries",
                "--attest-contract-only", "--attest-dependency-review",
                "--attest-runtime-smoke", "--attest-screenshot-review",
            )
            run_cmd(*validation_base, "--reviewer", "navigation-agent-1", expect=2)
            passed = run_cmd(*validation_base, "--reviewer", "architecture-acceptance-1")
            self.assertEqual(json.loads(passed.stdout)["counts"]["assets"], 1)
            self.assertTrue((workspace / "stage-03-closure-manifest.sha256").is_file())
            self.assertTrue((workspace / "CLOSED").is_file())

            run_cmd(
                sys.executable, str(SKILL / "scripts" / "run_verification.py"),
                "--workspace", str(workspace), "--plan", str(good_plan), expect=2,
            )
            run_cmd(
                sys.executable, str(CONTROLLER_SKILL / "scripts" / "validate_gate.py"),
                "--run-dir", str(run_dir), "--phase", "3", "--write",
            )
            ledger = {
                row["phase"]: row
                for row in csv.DictReader(
                    (run_dir / "controller" / "task-ledger.csv").read_text().splitlines()
                )
            }
            self.assertEqual(ledger["3"]["status"], "PASS")
            self.assertEqual(ledger["3"]["owner"], "architecture-lead-1")

            controller_rework_path = run_dir / "controller" / "rework-log.csv"
            rework_fields, rework_rows = read_csv(controller_rework_path)
            self.assertEqual(len(rework_rows), 1)
            original_assignee = rework_rows[0]["assigned_to"]
            rework_rows[0]["assigned_to"] = "tampered-agent"
            write_csv(controller_rework_path, rework_fields, rework_rows)
            run_cmd(
                sys.executable, str(CONTROLLER_SKILL / "scripts" / "validate_gate.py"),
                "--run-dir", str(run_dir), "--phase", "3", expect=1,
            )
            rework_rows[0]["assigned_to"] = original_assignee
            write_csv(controller_rework_path, rework_fields, rework_rows)

            shell = workspace / "harmony-project" / "entry" / "src" / "LoginShell.ets"
            shell.chmod(0o644)
            shell.write_text(shell.read_text(encoding="utf-8") + "// post-closure tamper\n", encoding="utf-8")
            run_cmd(
                sys.executable, str(CONTROLLER_SKILL / "scripts" / "validate_gate.py"),
                "--run-dir", str(run_dir), "--phase", "3", expect=1,
            )


if __name__ == "__main__":
    unittest.main()
