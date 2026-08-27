#!/usr/bin/env python3
"""End-to-end and adversarial tests for the governed Phase 3 scaffold (gmi path)."""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from gmi_phase2_fixture import (
    AMBIGUOUS_PAGE,
    DIALOG_PAGE,
    LOGIN_PAGE,
    OWNERSHIP,
    build_gmi_run,
    sha256,
    write_rows,
)


SKILL = HERE.parents[1]
FAKE_HARMONY = (HERE / "fake_harmony.py").resolve()
# freeze_environment.resolve_frozen_executable requires the executable bit;
# keep the checked-in fake executable regardless of how the repo was synced.
FAKE_HARMONY.chmod(FAKE_HARMONY.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

ROUTE_ID = "ROUTE-LOGINACTIVITY"
PAGE_SHELL_ID = "PAGESHELL-LOGINACTIVITY"
PAGE_ID = "PAGE-LOGIN-A1"


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


def initialize_phase3(run_dir: Path) -> Path:
    initialized = run_cmd(
        sys.executable, str(SKILL / "scripts" / "init_scaffold.py"),
        "--run-dir", str(run_dir),
        "--work-order", str(run_dir / "controller" / "work-orders" / "PHASE3-GMI-FIXTURE.json"),
        "--architecture-lead", OWNERSHIP["architecture_lead_id"],
    )
    payload = initialized.stdout.strip().splitlines()[-1]
    return Path(json.loads(payload)["workspace"])


def create_project_and_registries(workspace: Path) -> None:
    """Simulate the Phase 3 logical roles completing the seeded registries."""
    project = workspace / "harmony-project"
    entry_src = project / "entry" / "src"
    entry_src.mkdir(parents=True, exist_ok=True)
    (project / "oh-package-lock.json5").write_text("{ lockfileVersion: 3 }\n", encoding="utf-8")
    (entry_src / "LoginShell.ets").write_text(
        "const FEATURE_ID = 'FEATURE-AUTH'\n"
        f"const PAGE_ID = '{PAGE_ID}'\n"
        f"const PAGE_SHELL_ID = '{PAGE_SHELL_ID}'\n"
        f"const ROUTE_ID = '{ROUTE_ID}'\n"
        "export struct LoginShell {}\n",
        encoding="utf-8",
    )
    (project / "entry" / "src" / "main" / "ets" / "pages").mkdir(parents=True, exist_ok=True)
    (project / "entry" / "src" / "main" / "ets" / "pages" / "loginactivity.ets").write_text(
        f"export const LOGINACTIVITY_ROUTE = '{ROUTE_ID}'\n"
        "export struct LoginActivity {}\n",
        encoding="utf-8",
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
    (entry_src / "Foundation.ets").write_text(
        "\n".join(f"export interface {symbol} {{}}" for symbol in foundation_symbols.values()) + "\n",
        encoding="utf-8",
    )

    asset_fields, asset_rows = read_csv(workspace / "asset-registry.csv")
    if len(asset_rows) != 1:
        raise AssertionError("Phase 3 did not seed one row for the real Phase 2 asset")
    asset_rows[0].update(
        {
            "target_module_id": "ENTRY",
            "target_path": "entry/src/main/resources/base/media/login_logo.svg",
            "target_symbol": "login_logo",
            "planned_mode": "DIRECT_COPY",
            "decision": "COPY_UNCHANGED",
            "created_by": OWNERSHIP["architecture_lead_id"],
            "status": "READY",
            "notes": "Planned landing only; Phase 3 does not copy the asset into implementation.",
        }
    )
    write_rows(workspace / "asset-registry.csv", asset_fields, asset_rows)

    architecture_fields, architecture = read_csv(workspace / "architecture-map.csv")
    if len(architecture) != 1:
        raise AssertionError(f"Expected one frozen Phase 2 source row, got {len(architecture)}")
    if architecture[0]["mapping_type"] != "ROUTE_PAGE":
        raise AssertionError(
            f"LoginActivity carrier must map ROUTE_PAGE, got {architecture[0]['mapping_type']}"
        )
    architecture[0].update(
        {
            "shell_file": "entry/src/LoginShell.ets",
            "screenshot_ids": "HSCREEN-LOGIN",
            "verification_id": "HVER-001",
            "mapping_status": "SHELL_CREATED_PENDING_IMPLEMENTATION",
        }
    )
    write_rows(workspace / "architecture-map.csv", architecture_fields, architecture)

    route_fields, route_rows = read_csv(workspace / "route-registry.csv")
    if len(route_rows) != 1 or route_rows[0]["route_id"] != ROUTE_ID:
        raise AssertionError("gmi seeding did not produce the expected route row")
    route_rows[0]["page_shell_file"] = "entry/src/LoginShell.ets"
    write_rows(workspace / "route-registry.csv", route_fields, route_rows)

    public_rows: list[dict[str, str]] = []
    for number, (foundation_type, symbol) in enumerate(foundation_symbols.items(), start=1):
        public_rows.append(
            {
                "foundation_id": f"FOUNDATION-{number:03d}",
                "foundation_type": foundation_type,
                "harmony_module_id": "ENTRY",
                "file_path": "entry/src/Foundation.ets",
                "symbol": symbol,
                "may_bind_to_placeholder": (
                    "false" if foundation_type in {"LOADING_SHELL", "EMPTY_SHELL", "ERROR_SHELL"}
                    else "true"
                ),
                "created_by": OWNERSHIP["public_ui_agent_id"],
                "status": "READY",
                "notes": "",
            }
        )
    write_csv_public(workspace, public_rows)

    _, capabilities = read_csv(workspace / "capability-contracts.csv")
    if capabilities:
        raise AssertionError("Explicit gmi NONE sentinels must not create Harmony capability work")
    status_fields, statuses = read_csv(workspace / "migration-status.csv")
    if len(statuses) != 1 or statuses[0]["source_kind"] != "INVENTORY_ROW":
        raise AssertionError("Phase 3 status seed does not match the one visual source row")
    statuses[0].update(
        {
            "target_id": ROUTE_ID,
            "status": "SHELL_CREATED_PENDING_IMPLEMENTATION",
            "updated_by": OWNERSHIP["navigation_agent_id"],
        }
    )
    write_rows(workspace / "migration-status.csv", status_fields, statuses)


def write_csv_public(workspace: Path, public_rows: list[dict[str, str]]) -> None:
    write_rows(
        workspace / "public-ui-registry.csv",
        [
            "foundation_id", "foundation_type", "harmony_module_id", "file_path", "symbol",
            "may_bind_to_placeholder", "created_by", "status", "notes",
        ],
        public_rows,
    )


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
        "created_by": OWNERSHIP["architecture_lead_id"],
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
        "--frozen-by", OWNERSHIP["architecture_lead_id"],
    )
    return environment


def verification_plan(
    verification_id: str,
    screenshot_id: str,
    suffix: str,
    *,
    executed_by: str = OWNERSHIP["toolchain_agent_id"],
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
                "target_id": ROUTE_ID, "page_id": PAGE_ID,
                "page_shell_id": PAGE_SHELL_ID, "result_output_path": result_path,
                "cwd": ".",
                "argv": [
                    executable, "smoke", "--serial", "fixture-001",
                    "--bundle", "com.example.fixture", "--kind", "ROUTE_PAGE",
                    f"--target", ROUTE_ID, f"--page", PAGE_ID,
                    f"--shell", PAGE_SHELL_ID, "--result", result_path,
                ],
            },
            {
                "command_id": f"CMD-{suffix}-SCREEN", "category": "SCREENSHOT_CAPTURE",
                "device_id": "HDEVICE-001", "screenshot_id": screenshot_id,
                "target_kind": "ROUTE_PAGE", "target_id": ROUTE_ID,
                "feature_ids": ["FEATURE-AUTH"], "page_id": PAGE_ID,
                "page_shell_id": PAGE_SHELL_ID, "smoke_command_id": f"CMD-{suffix}-SMOKE",
                "output_path": screenshot_path, "cwd": ".",
                "argv": [
                    executable, "screenshot", "--serial", "fixture-001",
                    f"--target", ROUTE_ID, "--output", screenshot_path,
                    "--width", "320", "--height", "640",
                ],
            },
        ],
        "artifact_paths": ["build/app.hap"],
    }


class Stage3WorkflowTest(unittest.TestCase):
    def test_initialization_creates_template_project_and_locks_advanced_handoff(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stage3-template-test-") as temp_name:
            run_dir, _ws = build_gmi_run(Path(temp_name), [LOGIN_PAGE])
            workspace = initialize_phase3(run_dir)
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
            self.assertEqual(input_lock["gmi_gate"]["audit"], "clean")
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

    def test_gmi_gate_missing_inputs_block_initialization(self) -> None:
        gate_files = (
            "runtime-evidence/audit-replay.csv",
            "coverage/coverage-ledger.csv",
            "runtime-evidence/runtime-gate.csv",
            "candidates/phase-2-completeness.csv",
        )
        with tempfile.TemporaryDirectory(prefix="stage3-gate-block-test-") as temp_name:
            run_dir, _ws = build_gmi_run(Path(temp_name), [LOGIN_PAGE])
            for relative in gate_files:
                target = run_dir / relative
                self.assertTrue(target.is_file(), relative)
                backup = target.with_suffix(target.suffix + ".bak")
                shutil.move(str(target), str(backup))
                completed = subprocess.run(
                    [
                        sys.executable, str(SKILL / "scripts" / "init_scaffold.py"),
                        "--run-dir", str(run_dir),
                        "--work-order", str(
                            run_dir / "controller" / "work-orders" / "PHASE3-GMI-FIXTURE.json"
                        ),
                        "--architecture-lead", OWNERSHIP["architecture_lead_id"],
                    ],
                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                )
                self.assertNotEqual(completed.returncode, 0, relative)
                self.assertIn("gmi-gate-incomplete", completed.stderr, relative)
                shutil.move(str(backup), str(target))
                # The blocked run must not have produced any Phase 3 workspace content
                # (the adapter's empty placeholder directory may remain).
                phase3_dir = run_dir / "phase-03-harmony-scaffold"
                self.assertFalse(
                    phase3_dir.exists() and any(phase3_dir.iterdir()), relative
                )

    def test_carrier_decides_mapping_type_and_routes_composable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="stage3-carrier-test-") as temp_name:
            run_dir, _ws = build_gmi_run(Path(temp_name), [LOGIN_PAGE, DIALOG_PAGE], with_asset=False)
            workspace = initialize_phase3(run_dir)
            _, architecture = read_csv(workspace / "architecture-map.csv")
            mapping = {row["page_id"]: row for row in architecture}
            self.assertEqual(mapping[LOGIN_PAGE["page_id"]]["mapping_type"], "ROUTE_PAGE")
            self.assertEqual(mapping[LOGIN_PAGE["page_id"]]["route_id"], ROUTE_ID)
            self.assertEqual(mapping[LOGIN_PAGE["page_id"]]["surface_shell_id"], "")
            self.assertEqual(mapping[DIALOG_PAGE["page_id"]]["mapping_type"], "VISUAL_SURFACE")
            self.assertEqual(mapping[DIALOG_PAGE["page_id"]]["surface_shell_id"], "SURFACE-FILTERDIALOG")
            self.assertEqual(mapping[DIALOG_PAGE["page_id"]]["route_id"], "")
            _, surfaces = read_csv(workspace / "surface-registry.csv")
            self.assertEqual(
                {row["surface_kind"] for row in surfaces}, {"DIALOG"},
                "non-routable carriers must surface as their detected kind",
            )

        with tempfile.TemporaryDirectory(prefix="stage3-composable-test-") as temp_name:
            # A3 semantic fix: a COMPOSABLE carrier (Jetpack Compose screen) is
            # normalized to SCREEN semantics and routed — no longer blocked as
            # carrier-undecidable; the COMPOSABLE->SCREEN decision stays on the
            # seeded mapping rows as an audit trail.
            run_dir, _ws = build_gmi_run(Path(temp_name), [AMBIGUOUS_PAGE], with_asset=False)
            workspace = initialize_phase3(run_dir)
            _, architecture = read_csv(workspace / "architecture-map.csv")
            mapping = {row["page_id"]: row for row in architecture}
            composable = mapping[AMBIGUOUS_PAGE["page_id"]]
            self.assertEqual(composable["mapping_type"], "ROUTE_PAGE")
            self.assertEqual(composable["route_id"], "ROUTE-HELPCOMPOSABLE")
            self.assertEqual(composable["surface_shell_id"], "")
            self.assertIn("COMPOSABLE->SCREEN", composable["notes"])
            _, routes = read_csv(workspace / "route-registry.csv")
            route_rows = [row for row in routes if row["page_id"] == AMBIGUOUS_PAGE["page_id"]]
            self.assertEqual(len(route_rows), 1)
            self.assertIn("COMPOSABLE->SCREEN", route_rows[0]["notes"])
            _, surfaces = read_csv(workspace / "surface-registry.csv")
            self.assertEqual(
                surfaces, [],
                "a COMPOSABLE carrier must not surface as a non-routable surface",
            )

    def test_governed_stage3_full_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="harmony-stage3-skill-test-") as temp_name:
            root = Path(temp_name)
            run_dir, _ws = build_gmi_run(root, [LOGIN_PAGE])
            workspace = initialize_phase3(run_dir)
            self.assertEqual(
                json.loads((workspace / "phase-manifest.json").read_text())["ownership"]
                ["architecture_acceptance_agent_id"],
                OWNERSHIP["architecture_acceptance_agent_id"],
            )
            create_project_and_registries(workspace)
            config_path = root / "henv.json"
            freeze_environment(workspace, config_path)

            wrong_plan = root / "wrong-role-plan.json"
            wrong_plan.write_text(
                json.dumps(
                    verification_plan(
                        "HVER-WRONG", "HSCREEN-WRONG", "WRONG",
                        executed_by=OWNERSHIP["architecture_acceptance_agent_id"],
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
                "--record-id", ROUTE_ID,
                "--feature-id", "FEATURE-AUTH", "--page-id", PAGE_ID,
                "--failed-verification-id", "HVER-ERROR", "--severity", "HIGH",
                "--reason", "Install output contained a frozen error marker",
                "--completion-condition", "A new sealed HVER passes all nine categories",
                "--confirmed-by", OWNERSHIP["architecture_lead_id"],
            )
            run_cmd(*rework_open, "--reviewer", OWNERSHIP["navigation_agent_id"], expect=2)
            run_cmd(*rework_open, "--reviewer", OWNERSHIP["architecture_acceptance_agent_id"])

            rework_fields, rework_rows = read_csv(workspace / "rework-tickets.csv")
            self.assertEqual(rework_rows[0]["record_id"], ROUTE_ID)
            self.assertEqual(rework_rows[0]["phase"], "3")
            self.assertEqual(rework_rows[0]["completion_condition"],
                             "A new sealed HVER passes all nine categories")
            self.assertTrue(rework_rows[0]["confirmed_at"])

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
                "--resolution-verification-id", "HVER-001",
            )
            run_cmd(*rework_close, "--reviewer", OWNERSHIP["navigation_agent_id"], expect=2)
            run_cmd(*rework_close, "--reviewer", OWNERSHIP["architecture_acceptance_agent_id"])

            validation_base = (
                sys.executable, str(SKILL / "scripts" / "validate_stage3.py"),
                "--workspace", str(workspace), "--henv-id", "HENV-001",
                "--verification-id", "HVER-001", "--decision", "PASS",
                "--attest-real-file-review", "--attest-placeholder-boundaries",
                "--attest-contract-only", "--attest-dependency-review",
                "--attest-runtime-smoke", "--attest-screenshot-review",
            )
            run_cmd(*validation_base, "--reviewer", OWNERSHIP["navigation_agent_id"], expect=2)
            passed = run_cmd(
                *validation_base, "--reviewer", OWNERSHIP["architecture_acceptance_agent_id"]
            )
            self.assertEqual(json.loads(passed.stdout)["counts"]["assets"], 1)
            self.assertTrue((workspace / "stage-03-closure-manifest.sha256").is_file())
            self.assertTrue((workspace / "CLOSED").is_file())

            run_cmd(
                sys.executable, str(SKILL / "scripts" / "run_verification.py"),
                "--workspace", str(workspace), "--plan", str(good_plan), expect=2,
            )
            run_cmd(
                sys.executable, str(SKILL / "scripts" / "manage_stage3_rework.py"),
                "--workspace", str(workspace), "--action", "close",
                "--ticket-id", "REWORK-STAGE3-001",
                "--resolution-verification-id", "HVER-001",
                "--reviewer", OWNERSHIP["architecture_acceptance_agent_id"],
                expect=2,
            )


if __name__ == "__main__":
    unittest.main()
