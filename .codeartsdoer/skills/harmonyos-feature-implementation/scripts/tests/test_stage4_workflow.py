#!/usr/bin/env python3
"""Minimal full-chain test for governed Phase 4 and controller Gate 4 (gmi path)."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SKILL = HERE.parents[1]
BUNDLE = HERE.parents[2]
CONTROLLER = BUNDLE / "android-harmony-migration-controller"
STAGE3_TESTS = BUNDLE / "harmonyos-migration-scaffold" / "scripts" / "tests"
sys.path.insert(0, str(STAGE3_TESTS))
sys.path.insert(0, str(SKILL / "scripts"))

import stage4_work_orders  # noqa: E402
from gmi_phase2_fixture import LOGIN_PAGE, OWNERSHIP, build_gmi_run  # noqa: E402
from test_stage3_workflow import (  # noqa: E402
    create_project_and_registries,
    freeze_environment,
    initialize_phase3,
    read_csv,
    verification_plan,
)


FAKE = (STAGE3_TESTS / "fake_harmony.py").resolve()
PAGE_ID = LOGIN_PAGE["page_id"]
ROUTE_ID = "ROUTE-LOGINACTIVITY"
INVENTORY_ID = "INV-LOGINACTIVITY"


def run_cmd(*args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["ANDROID_HARMONY_TEST_FIXTURES"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        list(args), text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, env=environment,
    )
    if completed.returncode != expect:
        raise AssertionError(
            f"Expected exit {expect}, got {completed.returncode}\nCOMMAND: {args}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def run_relative(run_dir: Path, target: Path) -> str:
    return target.resolve().relative_to(run_dir.resolve()).as_posix()


def record_human_approval(run_dir: Path, phase: int, review_id: str) -> None:
    gate_report = run_dir / "controller" / "gate-report.json"
    run_cmd(
        sys.executable, str(CONTROLLER / "scripts" / "generate_review_summary.py"),
        "--run-dir", str(run_dir), "--phase", str(phase),
        "--gate-report", str(gate_report),
    )
    run_cmd(
        sys.executable, str(CONTROLLER / "scripts" / "record_human_review.py"),
        "--run-dir", str(run_dir), "--phase", str(phase),
        "--gate-report", str(gate_report),
        "--review-id", review_id, "--reviewer", "human-reviewer-fixture",
        "--decision", "APPROVED",
    )


def record_team_receipt(
    run_dir: Path,
    order: Path,
    role_key: str,
    actor_id: str,
    task_id: str,
    artifact: Path,
) -> None:
    run_cmd(
        sys.executable, str(CONTROLLER / "scripts" / "record_team_execution.py"),
        "--run-dir", str(run_dir), "--work-order", run_relative(run_dir, order),
        "--role-key", role_key, "--actor-id", actor_id,
        "--platform-task-id", task_id,
        "--started-at", "2026-08-25T00:00:00Z",
        "--ended-at", "2026-08-25T01:00:00Z",
        "--terminal-task-state", "SUCCEEDED",
        "--artifact", run_relative(run_dir, artifact),
    )


def close_stage3(root: Path) -> Path:
    run_dir, _ = build_gmi_run(root, [LOGIN_PAGE])
    order = run_dir / "controller" / "work-orders" / "PHASE3-GMI-FIXTURE.json"
    workspace = initialize_phase3(run_dir)
    create_project_and_registries(workspace)
    freeze_environment(workspace, root / "henv.json")
    plan = root / "stage3-plan.json"
    plan.write_text(
        json.dumps(verification_plan("HVER-001", "HSCREEN-LOGIN", "STAGE4"), indent=2) + "\n",
        encoding="utf-8",
    )
    run_cmd(
        sys.executable, str(BUNDLE / "harmonyos-migration-scaffold" / "scripts" / "run_verification.py"),
        "--workspace", str(workspace), "--plan", str(plan),
    )
    run_cmd(
        sys.executable, str(BUNDLE / "harmonyos-migration-scaffold" / "scripts" / "validate_stage3.py"),
        "--workspace", str(workspace), "--henv-id", "HENV-001",
        "--verification-id", "HVER-001", "--reviewer", OWNERSHIP["architecture_acceptance_agent_id"],
        "--decision", "PASS", "--attest-real-file-review", "--attest-placeholder-boundaries",
        "--attest-contract-only", "--attest-dependency-review", "--attest-runtime-smoke",
        "--attest-screenshot-review",
    )
    run_cmd(
        sys.executable, str(CONTROLLER / "scripts" / "validate_gate.py"),
        "--run-dir", str(run_dir), "--phase", "3", "--write",
    )
    record_human_approval(run_dir, 3, "HREV-PHASE-03-FEATURE")
    phase3_receipts = [
        ("architecture_lead_id", OWNERSHIP["architecture_lead_id"], "TASK-P3-ARCH", workspace / "architecture-map.csv"),
        ("toolchain_agent_id", OWNERSHIP["toolchain_agent_id"], "TASK-P3-TOOL", workspace / "verification" / "HVER-001" / "COMMITTED"),
        ("navigation_agent_id", OWNERSHIP["navigation_agent_id"], "TASK-P3-NAV", workspace / "route-registry.csv"),
        ("public_ui_agent_id", OWNERSHIP["public_ui_agent_id"], "TASK-P3-UI", workspace / "public-ui-registry.csv"),
        ("capability_contract_agent_id", OWNERSHIP["capability_contract_agent_id"], "TASK-P3-CAP", workspace / "capability-contracts.csv"),
        ("architecture_acceptance_agent_id", OWNERSHIP["architecture_acceptance_agent_id"], "TASK-P3-ACCEPT", workspace / "stage-03-gate-report.json"),
    ]
    for role_key, actor_id, task_id, artifact in phase3_receipts:
        record_team_receipt(run_dir, order, role_key, actor_id, task_id, artifact)
    return run_dir


def category_contracts(scope: dict[str, object]) -> dict[str, dict[str, object]]:
    executable = str(FAKE)
    executable_sha = sha256(FAKE)
    environment = scope["environments"][0]  # type: ignore[index]
    serial = "fixture-001"
    bundle = "com.example.fixture"
    network = str(environment["network_profile"])  # type: ignore[index]
    permissions = str(environment["permissions_profile"])  # type: ignore[index]
    values = {
        "TOOLCHAIN": (["toolchain"], "TOOLCHAIN_OK"),
        "CLEAN_BUILD": (["build", "{ARTIFACT}"], "BUILD_OK"),
        "BUNDLE_CHECK": (["bundle", serial, bundle], "BUNDLE_OK"),
        "SIGNING_CHECK": (["signing", bundle], "SIGNING_OK"),
        "DEVICE_CHECK": (["device", serial], "DEVICE_OK"),
        "CLEAN_INSTALL": (["install", serial, bundle, "{ARTIFACT}"], "INSTALL_OK"),
        "SEED_RESET": (["seed-reset", serial, bundle], "SEED_RESET_OK"),
        "NETWORK_PROFILE": (["network-profile", serial, network], "NETWORK_PROFILE_OK"),
        "PERMISSION_PROFILE": (
            ["permission-profile", serial, bundle, permissions], "PERMISSION_PROFILE_OK"
        ),
        "LAUNCH": (["launch", serial, bundle, "EntryAbility"], "LAUNCH_OK"),
        "NAVIGATE": (["navigate", serial, bundle, ROUTE_ID], "NAVIGATE_OK"),
        "BUSINESS_ASSERT": (
            ["business-assert", serial, bundle, ROUTE_ID, "{ASSERTIONS}"],
            "BUSINESS_ASSERT_OK",
        ),
        "SCREENSHOT_CAPTURE": (
            ["screenshot", serial, bundle, ROUTE_ID, "{SCREENSHOT}"],
            "SCREENSHOT_OK",
        ),
        "UITEST_SNAPSHOT_CAPTURE": (
            ["uitest-snapshot", serial, bundle, ROUTE_ID, "{TEST_HAP}", "{UITEST_RESULT}"],
            "UITEST_SNAPSHOT_OK"
        ),
    }
    return {
        category: {
            "resolved_executable": executable,
            "executable_sha256": executable_sha,
            "required_argv_tokens": required,
            "success_output_contains": [success],
            "error_output_contains": ["Error:", "Failed:", "Failure:"],
        }
        for category, (required, success) in values.items()
    }


def phase4_environment(run_dir: Path, target: Path) -> None:
    scope = json.loads((run_dir / "controller" / "scope.json").read_text(encoding="utf-8"))
    value = {
        "h4env_id": "H4ENV-001",
        "source_android_env_id": "ENV-001",
        "base_henv_id": "HENV-001",
        "device_id": "HDEVICE-001",
        "device_serial": "fixture-001",
        "bundle_name": "com.example.fixture",
        "created_by": "implementation-lead-4",
        "required": True,
        "device_selector_tokens": ["--serial", "fixture-001"],
        "category_contracts": category_contracts(scope),
        "comparison": {
            "screenshot_width": 320,
            "screenshot_height": 640,
            "content_bounds": [0, 0, 320, 640],
            "geometry_tolerance_px": 2,
            "excluded_platform_regions": [],
        },
    }
    target.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def build_plan(target: Path, build_id: str) -> None:
    executable = str(FAKE)
    value = {
        "hbuild_id": build_id,
        "h4env_id": "H4ENV-001",
        "executed_by": "verification-executor-4",
        "commands": [
            {"command_id": "H4CMD-BUILD-TOOL", "category": "TOOLCHAIN", "cwd": ".",
             "argv": [executable, "toolchain"]},
            {"command_id": "H4CMD-BUILD-CLEAN", "category": "CLEAN_BUILD", "cwd": ".",
             "argv": [executable, "build", "--artifact", "{ARTIFACT}"]},
            {"command_id": "H4CMD-BUILD-BUNDLE", "category": "BUNDLE_CHECK", "cwd": ".",
             "argv": [executable, "bundle", "--serial", "fixture-001", "--bundle", "com.example.fixture"]},
            {"command_id": "H4CMD-BUILD-SIGN", "category": "SIGNING_CHECK", "cwd": ".",
             "argv": [executable, "signing", "--bundle", "com.example.fixture"]},
        ],
        "artifact_paths": ["build/app.hap"],
    }
    target.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def state_plan(
    target: Path,
    parity_id: str,
    implemented_by: str,
    steps: Path,
    *,
    evidence_id: str,
    build_id: str,
    supersedes_evidence_id: str = "",
    test_hap_path: str = "uitest-test.hap",
) -> None:
    executable = str(FAKE)
    serial = "fixture-001"
    bundle = "com.example.fixture"
    commands = [
        ("DEVICE_CHECK", [executable, "device", "--serial", serial]),
        ("CLEAN_INSTALL", [executable, "install", "--serial", serial, "--bundle", bundle,
                           "--artifact", "{ARTIFACT}"]),
        ("SEED_RESET", [executable, "seed-reset", "--serial", serial, "--bundle", bundle]),
        ("NETWORK_PROFILE", [executable, "network-profile", "--serial", serial,
                             "--profile", "normal"]),
        ("PERMISSION_PROFILE", [executable, "permission-profile", "--serial", serial,
                                "--bundle", bundle, "--profile", "fresh-install"]),
        ("LAUNCH", [executable, "launch", "--serial", serial, "--bundle", bundle,
                    "--ability", "EntryAbility"]),
        ("NAVIGATE", [executable, "navigate", "--serial", serial, "--bundle", bundle,
                      "--target", ROUTE_ID]),
        ("BUSINESS_ASSERT", [executable, "business-assert", "--serial", serial,
                             "--bundle", bundle, "--target", ROUTE_ID,
                             "--parity", parity_id, "--build", build_id,
                             "--env", "H4ENV-001", "--output", "{ASSERTIONS}"]),
        ("SCREENSHOT_CAPTURE", [executable, "screenshot", "--serial", serial,
                                "--bundle", bundle, "--target", ROUTE_ID,
                                "--output", "{SCREENSHOT}", "--width", "320", "--height", "640"]),
        ("UITEST_SNAPSHOT_CAPTURE", [executable, "uitest-snapshot", "--serial", serial,
                                     "--bundle", bundle, "--target", ROUTE_ID,
                                     "--test-hap", "{TEST_HAP}",
                                     "--output", "{UITEST_RESULT}"]),
    ]
    value = {
        "evidence_id": evidence_id,
        "parity_id": parity_id,
        "hbuild_id": build_id,
        "h4env_id": "H4ENV-001",
        "test_hap_path": test_hap_path,
        "supersedes_evidence_id": supersedes_evidence_id,
        "implemented_by": implemented_by,
        "executed_by": "verification-executor-4",
        "steps_file": str(steps),
        "commands": [
            {"command_id": f"H4CMD-STATE-{number:02d}", "category": category,
             "cwd": ".", "argv": argv}
            for number, (category, argv) in enumerate(commands, start=1)
        ],
        "assertions": [
            {"assertion_id": "ASSERT-VISUAL", "kind": "VISUAL_STATE", "expected": "visible"},
            {"assertion_id": "ASSERT-BUSINESS", "kind": "BUSINESS_RESULT", "expected": "login-ready",
             "subject_ids": ["BR-AUTH-NONE", "DATA-AUTH-NONE", "SYS-AUTH-NONE", "SDK-AUTH-NONE"]},
            {"assertion_id": "ASSERT-INTERACTION", "kind": "INTERACTION", "expected": "tap-ready"},
            {"assertion_id": "ASSERT-ANDROID-OBSERVABLE", "kind": "ANDROID_EXPECTED_OBSERVABLE",
             "expected": "LoginActivity displayed", "subject_ids": [INVENTORY_ID]},
        ],
    }
    target.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class Stage4WorkflowTest(unittest.TestCase):
    def test_full_stage4_and_controller_gate4_detect_post_close_tamper(self) -> None:
        with tempfile.TemporaryDirectory(prefix="harmony-stage4-skill-test-") as temp_name:
            root = Path(temp_name)
            run_dir = close_stage3(root)
            issued = run_cmd(
                sys.executable, str(CONTROLLER / "scripts" / "issue_phase4_work_order.py"),
                "--run-dir", str(run_dir), "--issued-by", "fixture-controller",
                "--implementation-lead-id", "implementation-lead-4",
                "--visual-asset-agent-id", "visual-asset-agent-4",
                "--verification-executor-id", "verification-executor-4",
                "--parity-acceptance-agent-id", "parity-acceptance-4",
            )
            phase4_order = Path(json.loads(issued.stdout)["work_order"])
            env_config = root / "h4env.json"
            phase4_environment(run_dir, env_config)
            initialized = run_cmd(
                sys.executable, str(SKILL / "scripts" / "init_implementation.py"),
                "--run-dir", str(run_dir), "--work-order", str(phase4_order),
                "--implementation-lead", "implementation-lead-4",
                "--environment-config", str(env_config),
            )
            workspace = Path(json.loads(initialized.stdout)["workspace"])
            # 原独立签发 CLI 已删除（职能并入 stage4_work_orders.py）：
            # 页面工单改由库函数直接签发，参数与原 CLI 一一对应。
            page_order = stage4_work_orders.issue_page_order(
                workspace, PAGE_ID, "page-owner-4", "TASK-P4-PAGE-LOGIN",
                ("harmony-project/entry/src/LoginShell.ets",), "ui-agent-4",
            )
            page_order_id = json.loads(page_order.read_text(encoding="utf-8"))["work_order_id"]
            # gmi 唯一路径下 system-capabilities 是显式 NONE 哨兵（capability-contracts
            # 为空），不存在可签发共享能力工单的真实 Capability-ID；capability 工单
            # 机制由 test_stage4_work_orders.py 直接以合成合同覆盖。

            source = workspace / "harmony-project" / "entry" / "src" / "LoginShell.ets"
            source.write_text(
                "export const FEATURE_ID = 'FEATURE-AUTH'\n"
                f"export const PAGE_ID = '{PAGE_ID}'\n"
                f"export const ROUTE_ID = '{ROUTE_ID}'\n"
                "export const LoginState = 'ready'\n"
                "export struct LoginShell {}\n",
                encoding="utf-8",
            )

            parity_fields, parity_rows = read_csv(workspace / "parity-map.csv")
            self.assertEqual(len(parity_rows), 1)
            parity = parity_rows[0]
            parity_id = parity["parity_id"]
            parity.update({
                "harmony_source_refs": '["entry/src/LoginShell.ets:1"]',
                "implemented_by": "ui-agent-4",
                "status": "IMPLEMENTED",
            })
            write_csv(workspace / "parity-map.csv", parity_fields, parity_rows)

            visual_fields, visual_rows = read_csv(workspace / "visual-elements.csv")
            self.assertEqual(len(visual_rows), 1)
            visual_rows[0].update({
                "android_visual_spec": '{"kind":"page-root","source":"android-evidence"}',
                "harmony_visual_spec": '{"kind":"page-root","source":"LoginShell"}',
                "harmony_file": "entry/src/LoginShell.ets",
                "harmony_symbol": "LoginShell",
                "implemented_by": "ui-agent-4",
                "status": "IMPLEMENTED",
            })
            write_csv(workspace / "visual-elements.csv", visual_fields, visual_rows)

            build = root / "build-plan-001.json"
            build_plan(build, "HBUILD-001")
            run_cmd(
                sys.executable, str(SKILL / "scripts" / "run_build.py"),
                "--workspace", str(workspace), "--plan", str(build),
            )
            first_build = json.loads(
                (workspace / "builds" / "HBUILD-001" / "metadata.json").read_text(encoding="utf-8")
            )
            first_hap = workspace / "builds" / "HBUILD-001" / first_build["primary_artifact"]["sealed_relative_path"]
            (workspace / "uitest-test.hap").write_bytes(first_hap.read_bytes())
            steps = workspace / "login-state-steps.md"
            steps.write_text("1. Reset seed.\n2. Launch.\n3. Open login.\n", encoding="utf-8")
            state = root / "state-plan-001.json"
            state_plan(
                state, parity_id, "ui-agent-4", steps,
                evidence_id="HEVD-LOGIN-001", build_id="HBUILD-001",
            )
            run_cmd(
                sys.executable, str(SKILL / "scripts" / "capture_state.py"),
                "--workspace", str(workspace), "--plan", str(state),
            )

            run_cmd(
                sys.executable, str(SKILL / "scripts" / "manage_stage4_rework.py"),
                "--workspace", str(workspace), "--action", "open",
                "--reviewer", "parity-acceptance-4", "--ticket-id", "H4REWORK-LOGIN-001",
                "--page-id", PAGE_ID, "--problem-type", "VISUAL",
                "--record-id", parity_id, "--failed-verification-id", "HEVD-LOGIN-001",
                "--severity", "HIGH", "--reason", "The first visual state needs correction.",
                "--completion-condition", "A newer build and HEVD pass the same frozen state.",
                "--confirmed-by", "implementation-lead-4",
            )
            time.sleep(1.1)
            source.write_text(
                source.read_text(encoding="utf-8")
                + "export const VisualCorrection = 'reviewed'\n",
                encoding="utf-8",
            )
            corrected_build = root / "build-plan-002.json"
            build_plan(corrected_build, "HBUILD-002")
            run_cmd(
                sys.executable, str(SKILL / "scripts" / "run_build.py"),
                "--workspace", str(workspace), "--plan", str(corrected_build),
            )
            corrected_state = root / "state-plan-002.json"
            state_plan(
                corrected_state, parity_id, "ui-agent-4", steps,
                evidence_id="HEVD-LOGIN-002", build_id="HBUILD-002",
                supersedes_evidence_id="HEVD-LOGIN-001",
            )
            run_cmd(
                sys.executable, str(SKILL / "scripts" / "capture_state.py"),
                "--workspace", str(workspace), "--plan", str(corrected_state),
            )
            run_cmd(
                sys.executable, str(SKILL / "scripts" / "manage_stage4_rework.py"),
                "--workspace", str(workspace), "--action", "close",
                "--reviewer", "parity-acceptance-4", "--ticket-id", "H4REWORK-LOGIN-001",
                "--confirmed-by", "implementation-lead-4",
                "--resolution-verification-id", "HEVD-LOGIN-002",
            )

            comparison = root / "comparison.json"
            comparison.write_text(json.dumps({
                "parity_id": parity_id,
                "visual_result": "MATCH",
                "functional_result": "MATCH",
                "asset_result": "MATCH",
                "reviewed_visual_element_ids": [visual_rows[0]["visual_element_id"]],
                "differences": [],
                "notes": "Both sealed screenshots and state results were reviewed.",
            }, indent=2) + "\n", encoding="utf-8")
            run_cmd(
                sys.executable, str(SKILL / "scripts" / "review_parity.py"),
                "--workspace", str(workspace), "--parity-id", parity_id,
                "--comparison", str(comparison), "--reviewer", "parity-acceptance-4",
                "--decision", "ACCEPTED", "--attest-opened-both-screenshots",
                "--attest-functional-results", "--attest-asset-provenance",
            )

            ledger_fields, ledger_rows = read_csv(workspace / "page-implementation-ledger.csv")
            ledger_rows[0].update({
                "work_order_id": page_order_id,
                "status": "ACCEPTED",
                "updated_at": "2026-08-25T00:00:00Z",
            })
            write_csv(workspace / "page-implementation-ledger.csv", ledger_fields, ledger_rows)
            visual_fields, visual_rows = read_csv(workspace / "visual-elements.csv")
            visual_rows[0]["status"] = "ACCEPTED"
            write_csv(workspace / "visual-elements.csv", visual_fields, visual_rows)
            validation_args = (
                sys.executable, str(SKILL / "scripts" / "validate_stage4.py"),
                "--workspace", str(workspace), "--build-id", "HBUILD-002",
                "--reviewer", "parity-acceptance-4", "--decision", "PASS",
                "--attest-visual-review", "--attest-functional-parity",
                "--attest-asset-provenance", "--attest-nativeization-review",
            )
            ledger_fields_neg, ledger_rows_neg = read_csv(workspace / "page-implementation-ledger.csv")
            ledger_rows_neg[0]["status"] = "IMPLEMENTED"
            write_csv(workspace / "page-implementation-ledger.csv", ledger_fields_neg, ledger_rows_neg)
            run_cmd(*validation_args, expect=2)
            self.assertFalse((workspace / "CLOSED").exists())
            self.assertFalse((workspace / "stage-04-gate-report.json").exists())
            self.assertFalse((workspace / "stage-04-closure-manifest.sha256").exists())
            ledger_rows_neg[0]["status"] = "ACCEPTED"
            write_csv(workspace / "page-implementation-ledger.csv", ledger_fields_neg, ledger_rows_neg)

            local = run_cmd(
                *validation_args,
            )
            self.assertEqual(json.loads(local.stdout)["final_verdict"], "PASS")
            gate4 = run_cmd(
                sys.executable, str(CONTROLLER / "scripts" / "validate_gate.py"),
                "--run-dir", str(run_dir), "--phase", "4", "--write",
            )
            self.assertEqual(json.loads(gate4.stdout)["verdict"], "PASS")
            self.assertTrue((workspace / "CLOSED").is_file())
            record_human_approval(run_dir, 4, "HREV-PHASE-04-PAGE")

            phase4_receipts = [
                (phase4_order, "implementation_lead_id", "implementation-lead-4", "TASK-P4-LEAD", workspace / "phase-manifest.json"),
                (phase4_order, "visual_asset_agent_id", "visual-asset-agent-4", "TASK-P4-ASSET", workspace / "asset-migration.csv"),
                (phase4_order, "verification_executor_id", "verification-executor-4", "TASK-P4-VERIFY", workspace / "evidence-index.csv"),
                (phase4_order, "parity_acceptance_agent_id", "parity-acceptance-4", "TASK-P4-ACCEPT", workspace / "stage-04-gate-report.json"),
            ]
            phase4_receipts.extend([
                (page_order, "page_owner_id", "page-owner-4", "TASK-P4-PAGE-LOGIN", workspace / "page-implementation-ledger.csv"),

            ])
            for order_path, role_key, actor_id, task_id, artifact in phase4_receipts:
                record_team_receipt(run_dir, order_path, role_key, actor_id, task_id, artifact)
            audit = run_cmd(
                sys.executable, str(CONTROLLER / "scripts" / "audit_delivery.py"),
                "--run-dir", str(run_dir), "--through-phase", "4",
            )
            self.assertEqual(json.loads(audit.stdout)["verdict"], "PASS")

            source.chmod(0o644)
            source.write_text(source.read_text(encoding="utf-8") + "// tampered\n", encoding="utf-8")
            run_cmd(
                sys.executable, str(CONTROLLER / "scripts" / "validate_gate.py"),
                "--run-dir", str(run_dir), "--phase", "4", expect=1,
            )


if __name__ == "__main__":
    unittest.main()
