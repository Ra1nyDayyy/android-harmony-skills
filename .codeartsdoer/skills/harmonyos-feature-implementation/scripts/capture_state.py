#!/usr/bin/env python3
"""Run one frozen HarmonyOS emulator state journey and seal HEVD evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from uitest_snapshot import validate_uitest_evidence
from _stage4_audit import validate_uitest_evidence_lite

from _common import (
    assert_no_secrets,
    atomic_json,
    atomic_text,
    build_project_snapshot,
    csv_fieldnames,
    exclusive_lock,
    frozen_category_contracts,
    frozen_output_verdict,
    load_json,
    make_tree_read_only,
    manifest_text,
    png_dimensions,
    read_csv,
    run_command,
    safe_relative_path,
    selector_is_present,
    sha256_file,
    split_multi,
    utc_now,
    validate_actor,
    validate_frozen_command,
    validate_hap,
    validate_id,
    verify_manifest,
    write_csv,
)


SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSETS = SKILL_ROOT / "assets"
REQUIRED_SEQUENCE = [
    "DEVICE_CHECK", "CLEAN_INSTALL", "SEED_RESET", "NETWORK_PROFILE", "PERMISSION_PROFILE",
    "LAUNCH", "NAVIGATE", "BUSINESS_ASSERT", "SCREENSHOT_CAPTURE", "UITEST_SNAPSHOT_CAPTURE",
]
# P4 分层验证（LITE 轻证）：命令记录要求裁为 6 类子序列——保留设备/安装/启动/
# 导航/截图/UiTest，去 SEED_RESET/NETWORK/PERMISSION/BUSINESS_ASSERT；三类断言
# 证据由采集端从实拍观测合成并完整走 validate_assertion_result 判定。
LITE_REQUIRED_SEQUENCE = [
    "DEVICE_CHECK", "CLEAN_INSTALL", "LAUNCH", "NAVIGATE",
    "SCREENSHOT_CAPTURE", "UITEST_SNAPSHOT_CAPTURE",
]
VERIFICATION_TIERS = ("CORE", "LITE")


def resolve_verification_tier(*sources: object) -> str:
    """Resolve the page's P4 verification tier (CORE deep / LITE light) fail-closed.

    来源依次为 plan 显式声明与迁移单元合同透传（单一真相源为页合同）；
    全部缺省视为 CORE（向后兼容）。多处声明且不一致时拒绝；值域外拒绝。
    """
    resolved: list[str] = []
    for source in sources:
        if source is None:
            continue
        tier = str(source).strip().upper()
        if tier not in VERIFICATION_TIERS:
            raise ValueError(f"verification_tier must be one of {list(VERIFICATION_TIERS)}: {source!r}")
        resolved.append(tier)
    if not resolved:
        return "CORE"
    if len(set(resolved)) != 1:
        raise ValueError(f"verification_tier declarations disagree: {sorted(set(resolved))}")
    return resolved[0]
REQUIRED_ASSERTIONS = {"VISUAL_STATE", "BUSINESS_RESULT", "INTERACTION"}
BUNDLE_CATEGORIES = {
    "CLEAN_INSTALL", "SEED_RESET", "PERMISSION_PROFILE", "LAUNCH", "NAVIGATE", "BUSINESS_ASSERT",
    "SCREENSHOT_CAPTURE", "UITEST_SNAPSHOT_CAPTURE",
}
TARGET_CATEGORIES = {"NAVIGATE", "BUSINESS_ASSERT", "SCREENSHOT_CAPTURE", "UITEST_SNAPSHOT_CAPTURE"}
COMMAND_PLACEHOLDERS = {
    "CLEAN_INSTALL": {"{ARTIFACT}"},
    "BUSINESS_ASSERT": {"{ASSERTIONS}"},
    "SCREENSHOT_CAPTURE": {"{SCREENSHOT}"},
    "UITEST_SNAPSHOT_CAPTURE": {"{TEST_HAP}", "{UITEST_RESULT}"},
}


def package_file_set(directory: Path) -> set[str]:
    return {
        path.relative_to(directory).as_posix() for path in directory.rglob("*")
        if path.is_file()
        and path.relative_to(directory).as_posix() not in {"manifest.sha256", "COMMITTED"}
    }


def substitute_exact(argv: list[str], replacements: dict[str, str]) -> list[str]:
    return [replacements.get(token, token) for token in argv]


ATTEMPT_LEDGER_FIELDS = [
    "execution_id", "parity_id", "evidence_id", "started_at", "executed_by",
    "previous_chain_sha256", "chain_sha256",
]


def validate_attempt_chain(rows: list[dict[str, str]]) -> None:
    previous = "0" * 64
    seen: set[str] = set()
    for row in rows:
        if set(row) != set(ATTEMPT_LEDGER_FIELDS):
            raise ValueError("Phase 4 attempt ledger columns differ")
        execution_id = str(row.get("execution_id", ""))
        if not execution_id or execution_id in seen or row.get("previous_chain_sha256") != previous:
            raise ValueError("Phase 4 attempt ledger identity or chain predecessor differs")
        material = {field: row.get(field, "") for field in ATTEMPT_LEDGER_FIELDS[:-1]}
        expected = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if row.get("chain_sha256") != expected:
            raise ValueError("Phase 4 attempt ledger hash chain differs")
        seen.add(execution_id)
        previous = expected


def reserve_execution(
    workspace: Path, parity_id: str, evidence_id: str, executed_by: str, max_repairs: int,
) -> None:
    """Append a controller-anchored execution before commands run; rows cannot be hidden locally."""
    local_path = workspace / "attempt-ledger.csv"
    controller_path = workspace.parent / "controller" / "phase4-attempt-ledger.csv"
    started_at = utc_now()
    execution_id = "EXEC-" + hashlib.sha256(
        f"{parity_id}|{evidence_id}".encode("utf-8")
    ).hexdigest()[:20].upper()
    with exclusive_lock(workspace.parent / "controller" / ".phase4-attempt-ledger.lock"):
        controller_rows = read_csv(controller_path)
        validate_attempt_chain(controller_rows)
        local_rows = read_csv(local_path)
        validate_attempt_chain(local_rows)
        if controller_rows != local_rows:
            raise ValueError("Local and controller Phase 4 attempt ledgers differ")
        used = sum(1 for row in controller_rows if row.get("parity_id") == parity_id)
        if used > max_repairs:
            raise ValueError(
                f"Automatic repair budget exhausted for {parity_id}; "
                "emit a grouped error report for human-assisted repair"
            )
        if any(row.get("evidence_id") == evidence_id for row in controller_rows):
            raise ValueError("Evidence-ID was already used by an earlier execution")
        previous = controller_rows[-1]["chain_sha256"] if controller_rows else "0" * 64
        row = {
            "execution_id": execution_id, "parity_id": parity_id,
            "evidence_id": evidence_id, "started_at": started_at,
            "executed_by": executed_by, "previous_chain_sha256": previous,
        }
        row["chain_sha256"] = hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        new_rows = [*controller_rows, row]
        write_csv(controller_path, ATTEMPT_LEDGER_FIELDS, new_rows)
        write_csv(local_path, ATTEMPT_LEDGER_FIELDS, new_rows)


def validate_assertion_result(
    path: Path,
    planned: list[dict[str, Any]],
    bindings: dict[str, str],
    required_obligation_ids: set[str] | None = None,
) -> dict[str, Any]:
    required_obligation_ids = required_obligation_ids or set()
    value = load_json(path)
    if not isinstance(value, dict):
        raise ValueError("BUSINESS_ASSERT output must be a JSON object")
    for field, expected in bindings.items():
        if str(value.get(field, "")) != expected:
            raise ValueError(f"BUSINESS_ASSERT output {field} differs from frozen identity")
    results = value.get("assertions")
    if not isinstance(results, list) or not all(isinstance(item, dict) for item in results):
        raise ValueError("BUSINESS_ASSERT output lacks an assertions array")
    planned_by_id = {item["assertion_id"]: item for item in planned}
    result_by_id: dict[str, dict[str, Any]] = {}
    for result in results:
        assertion_id = str(result.get("assertion_id", ""))
        if assertion_id in result_by_id:
            raise ValueError(f"Duplicate generated Assertion-ID: {assertion_id}")
        result_by_id[assertion_id] = result
    if set(result_by_id) != set(planned_by_id):
        raise ValueError("Generated assertion ID set differs from the frozen plan")
    for assertion_id, plan in planned_by_id.items():
        result = result_by_id[assertion_id]
        if result.get("kind") != plan["kind"] or result.get("expected") != plan["expected"]:
            raise ValueError(f"Generated assertion identity/expected value differs: {assertion_id}")
        if result.get("subject_ids", []) != plan.get("subject_ids", []):
            raise ValueError(f"Generated assertion subject_ids differ from the frozen plan: {assertion_id}")
        actual = result.get("actual")
        if actual is None or (isinstance(actual, str) and not actual.strip()):
            raise ValueError(f"Generated assertion actual value is empty: {assertion_id}")
        operator = str(plan.get("operator", "EQUALS"))
        expected = plan["expected"]
        if operator == "EQUALS":
            passed = actual == expected
        elif operator == "CONTAINS":
            passed = str(expected) in str(actual)
        elif operator == "REGEX":
            passed = re.search(str(expected), str(actual)) is not None
        elif operator == "JSON_EQUALS":
            passed = actual == expected
        elif operator == "NUMERIC_RANGE":
            passed = (
                isinstance(expected, dict)
                and isinstance(actual, (int, float))
                and isinstance(expected.get("min"), (int, float))
                and isinstance(expected.get("max"), (int, float))
                and float(expected["min"]) <= float(actual) <= float(expected["max"])
            )
        else:
            raise ValueError(f"Unsupported deterministic assertion operator: {operator}")
        if not passed:
            raise ValueError(
                f"Generated assertion differs: {assertion_id}; operator={operator}, "
                f"expected={expected!r}, actual={actual!r}"
            )
        if result.get("status") not in (None, "PASS"):
            raise ValueError(f"Generated assertion status contradicts deterministic result: {assertion_id}")
        result["operator"] = operator
        result["status"] = "PASS"
    kinds = {item["kind"] for item in planned}
    if REQUIRED_ASSERTIONS - kinds:
        raise ValueError(f"Generated evidence lacks required assertion kinds: {sorted(REQUIRED_ASSERTIONS - kinds)}")
    covered_obligations = {
        str(subject_id)
        for result in results
        for subject_id in result.get("subject_ids", [])
        if isinstance(subject_id, str)
    }
    if not required_obligation_ids <= covered_obligations:
        raise ValueError(
            "Generated assertions do not cover every advanced obligation: "
            f"{sorted(required_obligation_ids - covered_obligations)}"
        )
    atomic_json(path, value)
    return value


def synthesize_lite_assertions(
    path: Path,
    planned: list[dict[str, Any]],
    bindings: dict[str, str],
    observed: dict[str, Any],
    required_obligation_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Materialize LITE assertions from live observations, then judge them normally.

    P4 分层验证：LITE 页没有 BUSINESS_ASSERT 命令通道，三类断言的实际值改由
    实拍观测构成（组件树一致率 / UiTest 组件数与 trace 条数 / 截图分辨率），
    随后仍完整走 validate_assertion_result 的确定性 operator 判定、三类覆盖
    与 obligation 覆盖校验（fail-closed 不放宽）。
    """
    rows: list[dict[str, Any]] = []
    for item in planned:
        kind = str(item.get("kind", ""))
        if kind == "VISUAL_STATE":
            actual = f"lite-component-overlap={float(observed['component_overlap']):.3f}"
        elif kind == "BUSINESS_RESULT":
            actual = (
                f"uitest-components={int(observed['component_count'])};"
                f"trace={int(observed['trace_count'])}"
            )
        elif kind == "INTERACTION":
            actual = (
                f"screenshot={int(observed['screenshot_width'])}x"
                f"{int(observed['screenshot_height'])}"
            )
        else:
            actual = str(observed.get("observed_default", "lite-observed"))
        row = {
            "assertion_id": item["assertion_id"],
            "kind": kind,
            "expected": item["expected"],
            "actual": actual,
            "status": "PASS",
        }
        if item.get("subject_ids") is not None:
            row["subject_ids"] = list(item["subject_ids"])
        rows.append(row)
    value: dict[str, Any] = {**bindings, "assertions": rows}
    atomic_json(path, value)
    return validate_assertion_result(path, planned, bindings, required_obligation_ids)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve()
    plan_path = Path(args.plan).expanduser().resolve()
    project = workspace / "harmony-project"
    try:
        phase_manifest = load_json(workspace / "phase-manifest.json")
        plan = load_json(plan_path)
        if phase_manifest.get("phase") != 4 or not project.is_dir():
            raise ValueError("Not an initialized Phase 4 workspace")
        if (workspace / "CLOSED").exists():
            raise ValueError("Phase 4 is CLOSED; new evidence is prohibited")
        if not isinstance(plan, dict):
            raise ValueError("State verification plan must be an object")
        assert_no_secrets(plan)
        if ".mp4" in json.dumps(plan, ensure_ascii=False).lower():
            raise ValueError("MP4 is prohibited in Phase 4 plans")
        evidence_id = validate_id(str(plan.get("evidence_id", "")), "HEVD-ID")
        parity_id = validate_id(str(plan.get("parity_id", "")), "Parity-ID")
        hbuild_id = validate_id(str(plan.get("hbuild_id", "")), "HBUILD-ID")
        h4env_id = validate_id(str(plan.get("h4env_id", "")), "H4ENV-ID")
        if not evidence_id.startswith("HEVD-"):
            raise ValueError("Formal Harmony evidence IDs must start with HEVD-")
        implemented_by = validate_actor(str(plan.get("implemented_by", "")), "feature implementer")
        executed_by = validate_actor(str(plan.get("executed_by", "")), "emulator verification executor")
        ownership = phase_manifest.get("ownership", {})
        compat_roles = phase_manifest.get("roles", {})
        expected_executor = ownership.get("verification_executor_id") or compat_roles.get(
            "verification_executor"
        )
        expected_parity = ownership.get("parity_acceptance_agent_id") or compat_roles.get(
            "parity_checker"
        )
        if executed_by != expected_executor:
            raise ValueError("Only the frozen emulator verification executor may capture formal evidence")
        if implemented_by in {executed_by, expected_parity}:
            raise ValueError("Feature implementer must differ from executor and parity checker")
        supersedes = str(plan.get("supersedes_evidence_id", ""))
        if supersedes:
            validate_id(supersedes, "superseded HEVD-ID")

        parity_rows = read_csv(workspace / "parity-map.csv")
        parity = next((row for row in parity_rows if row.get("parity_id") == parity_id), None)
        if not parity:
            raise ValueError(f"Unknown parity record: {parity_id}")
        if parity.get("h4env_id") != h4env_id:
            raise ValueError("Plan H4ENV differs from parity record")
        if parity.get("implemented_by") != implemented_by:
            raise ValueError("Plan implementer differs from parity record")
        current_evidence = parity.get("harmony_evidence_id", "")
        if current_evidence:
            if not supersedes or supersedes != current_evidence:
                raise ValueError("Parity already has evidence; a correction must explicitly supersede it")
        elif supersedes:
            raise ValueError("Cannot supersede evidence that is not the current parity evidence")
        if parity.get("status") not in {"IMPLEMENTED", "REWORK", "EVIDENCED"}:
            raise ValueError(f"Parity row is not ready for evidence: {parity.get('status')}")

        contract_path = workspace / "migration-unit-contracts.json"
        contract_file = load_json(contract_path)
        input_lock = load_json(workspace / "stage-04-input-lock.json")
        contract_sha = sha256_file(contract_path)
        if (
            contract_file.get("schema_version") != 1
            or input_lock.get("migration_unit_contracts_sha256") != contract_sha
            or phase_manifest.get("migration_unit_contracts_sha256") != contract_sha
            or not isinstance(contract_file.get("units"), list)
        ):
            raise ValueError("Migration-unit contracts are missing, changed, or malformed")
        matches = [
            row for row in contract_file["units"]
            if isinstance(row, dict) and row.get("parity_id") == parity_id
        ]
        if len(matches) != 1:
            raise ValueError(f"Parity must bind exactly one migration unit: {parity_id}")
        migration_contract = matches[0]
        carrier_deviation = migration_contract.get("carrier_deviation")
        carriers_equal = (
            migration_contract.get("expected_carrier")
            == migration_contract.get("scaffold_carrier")
        )
        if not carriers_equal:
            # Named-deviation tolerance (SKILL.md): a mismatched carrier pair
            # passes only when the contract carries a deviation block that is
            # exactly equal to the corresponding sealed record inside
            # stage-04-input-lock.json. Anything else stays fail-closed.
            applied_map = {
                item.get("page_id"): item
                for item in input_lock.get("phase4_carrier_deviations_applied", [])
                if isinstance(item, dict)
            }
            declared_ok = isinstance(carrier_deviation, dict) and (
                applied_map.get(carrier_deviation.get("page_id"))
                == carrier_deviation
                and isinstance(carrier_deviation.get("inventory_id"), str)
            )
            if not declared_ok:
                raise ValueError("Migration-unit identity, carrier, or anti-simplification policy differs")
        if (
            migration_contract.get("inventory_id") != parity.get("inventory_id")
            or migration_contract.get("feature_id") != parity.get("feature_id")
            or migration_contract.get("page_id") != parity.get("page_id")
            or migration_contract.get("state_id") != parity.get("state_id")
            or migration_contract.get("h4env_id") != h4env_id
            or migration_contract.get("target_kind") != parity.get("target_kind")
            or migration_contract.get("target_id") != parity.get("target_id")
            or (carriers_equal is False and not isinstance(carrier_deviation, dict))
            or migration_contract.get("simplification_policy") != "FORBIDDEN"
            or migration_contract.get("native_optimization_policy")
            != "INTERNAL_ONLY_UNLESS_APPROVED"
            or migration_contract.get("max_automatic_repair_attempts") != 2
        ):
            raise ValueError("Migration-unit identity, carrier, or anti-simplification policy differs")
        # P4 分层验证：plan 显式声明与迁移单元透传（页合同真相源）必须一致，缺省 CORE。
        verification_tier = resolve_verification_tier(
            plan.get("verification_tier"),
            migration_contract.get("verification_tier"),
        )
        expected_sequence = LITE_REQUIRED_SEQUENCE if verification_tier == "LITE" else REQUIRED_SEQUENCE

        index_rows = read_csv(workspace / "evidence-index.csv")
        if any(row.get("evidence_id") == evidence_id for row in index_rows):
            raise ValueError(f"HEVD-ID already exists: {evidence_id}")
        if supersedes:
            old_index = next((row for row in index_rows if row.get("evidence_id") == supersedes), None)
            if not old_index or old_index.get("parity_id") != parity_id or old_index.get("status") != "SEALED":
                raise ValueError("Superseded evidence is missing, belongs to another parity row, or is not SEALED")
        reserve_execution(
            workspace, parity_id, evidence_id, executed_by,
            int(migration_contract["max_automatic_repair_attempts"]),
        )

        env_path = workspace / "environments" / h4env_id / "phase4-environment.json"
        environment = load_json(env_path)
        env_row = next(
            (
                row for row in read_csv(workspace / "environments" / "h4env-registry.csv")
                if row.get("h4env_id") == h4env_id
            ),
            None,
        )
        if (
            not env_row or env_row.get("status") != "FROZEN"
            or sha256_file(env_path) != env_row.get("environment_sha256")
        ):
            raise ValueError(f"H4ENV is missing, changed, or not FROZEN: {h4env_id}")
        if str(environment.get("emulator", {}).get("device_type", "")).lower() != "emulator":
            raise ValueError("Formal Phase 4 evidence must use a HarmonyOS emulator")
        selector_tokens = environment.get("device_selector_tokens", [])
        serial = str(environment.get("emulator", {}).get("serial", ""))
        device_id = str(environment.get("device_id", ""))
        bundle_name = str(environment.get("base_application", {}).get("bundle_name", ""))
        if not isinstance(selector_tokens, list) or not selector_tokens or not serial or not bundle_name:
            raise ValueError("H4ENV lacks selector, serial, or Bundle identity")
        contracts = frozen_category_contracts(environment)

        build_dir = workspace / "builds" / hbuild_id
        build_metadata = load_json(build_dir / "metadata.json")
        if build_metadata.get("status") != "PASS" or build_metadata.get("h4env_id") != h4env_id:
            raise ValueError("Selected build is not passing for this H4ENV")
        build_manifest = build_dir / "manifest.sha256"
        committed = build_dir / "COMMITTED"
        if not committed.is_file() or not build_manifest.is_file() or (
            f"manifest_sha256={sha256_file(build_manifest)}" not in committed.read_text(encoding="utf-8")
        ):
            raise ValueError("Selected HBUILD COMMITTED marker does not bind its manifest")
        build_manifest_errors = verify_manifest(build_dir, package_file_set(build_dir))
        if build_manifest_errors:
            raise ValueError("Selected HBUILD package is invalid: " + "; ".join(build_manifest_errors))
        current_snapshot = build_project_snapshot(project)
        if current_snapshot["snapshot_sha256"] != build_metadata.get("source_snapshot_sha256"):
            raise ValueError("Current Phase 4 source differs from the sealed HBUILD snapshot")
        primary = build_metadata.get("primary_artifact")
        if not isinstance(primary, dict):
            raise ValueError("HBUILD has no primary artifact")
        artifact = safe_relative_path(
            build_dir, str(primary.get("sealed_relative_path", "")), "sealed build artifact"
        )
        if not artifact.is_file() or sha256_file(artifact) != primary.get("sha256"):
            raise ValueError("HBUILD primary artifact has changed")
        validate_hap(artifact)

        generation_lock = input_lock.get("ui_test_snapshot_generation")
        generation_manifest_path = workspace / "ui-test-snapshot-generation-manifest.json"
        if (
            not isinstance(generation_lock, dict)
            or generation_lock.get("relative_path") != "ui-test-snapshot-generation-manifest.json"
            or generation_lock.get("contract") != "ui-test-snapshot-generation-v1"
            or generation_lock.get("sha256") != sha256_file(generation_manifest_path)
        ):
            raise ValueError("UiTest generation manifest is missing or hash-mismatched")
        generation_manifest = load_json(generation_manifest_path)
        probe_id = f"{parity['page_id']}::{parity['state_id']}"
        probe_matches = [
            row for row in generation_manifest.get("probes", [])
            if isinstance(row, dict) and row.get("probe_id") == probe_id
        ]
        plan_matches = [
            row for row in generation_manifest.get("page_plans", [])
            if isinstance(row, dict) and row.get("page_id") == parity["page_id"]
        ]
        if len(probe_matches) != 1 or len(plan_matches) != 1:
            raise ValueError("UiTest generation manifest lacks the exact Page-ID/State-ID probe")
        probe = probe_matches[0]
        plan_record = plan_matches[0]
        page_plan = safe_relative_path(
            workspace, str(plan_record.get("relative_path", "")), "ArkTS page plan"
        )
        if not page_plan.is_file() or sha256_file(page_plan) != plan_record.get("sha256"):
            raise ValueError("UiTest page plan is missing or hash-mismatched")
        test_hap_value = str(plan.get("test_hap_path", ""))
        test_hap_candidate = Path(test_hap_value).expanduser()
        test_hap = (
            test_hap_candidate.resolve() if test_hap_candidate.is_absolute()
            else safe_relative_path(workspace, test_hap_value, "UiTest HAP")
        )
        if not test_hap.is_file():
            raise ValueError("State plan test_hap_path is missing")
        validate_hap(test_hap)
        test_hap_sha256 = sha256_file(test_hap)
        final_hap_sha256 = sha256_file(artifact)
        device_identity_sha256 = hashlib.sha256(json.dumps(
            {"device_id": device_id, "serial": serial},
            sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()

        steps_value = str(plan.get("steps_file", ""))
        if not steps_value or steps_value.lower().endswith(".mp4"):
            raise ValueError("steps_file must be a non-MP4 text file")
        steps_candidate = Path(steps_value).expanduser()
        steps = (
            steps_candidate.resolve() if steps_candidate.is_absolute()
            else safe_relative_path(workspace, steps_value, "steps file")
        )
        if not steps.is_file() or steps.stat().st_size == 0:
            raise ValueError("Steps file must exist and be nonempty")

        raw_assertions = plan.get("assertions")
        if not isinstance(raw_assertions, list) or not raw_assertions:
            raise ValueError("State plan requires assertion expectations")
        assertions: list[dict[str, Any]] = []
        assertion_ids: set[str] = set()
        assertion_kinds: set[str] = set()
        for assertion in raw_assertions:
            if not isinstance(assertion, dict):
                raise ValueError("Every assertion expectation must be an object")
            if not set(assertion).issubset({"assertion_id", "kind", "expected", "subject_ids", "operator"}) or not {
                "assertion_id", "kind", "expected"
            }.issubset(assertion):
                raise ValueError(
                    "Plan assertions may contain assertion_id, kind, expected, optional operator/subject_ids; "
                    "actual/status must be generated live"
                )
            assertion_id = validate_id(str(assertion.get("assertion_id", "")), "Assertion-ID")
            if assertion_id in assertion_ids:
                raise ValueError(f"Duplicate Assertion-ID: {assertion_id}")
            assertion_ids.add(assertion_id)
            kind = str(assertion.get("kind", ""))
            expected = assertion.get("expected")
            if not kind or expected is None or (isinstance(expected, str) and not expected.strip()):
                raise ValueError(f"Assertion expectation is empty: {assertion_id}")
            operator = str(assertion.get("operator", "EQUALS"))
            if operator not in {"EQUALS", "CONTAINS", "REGEX", "JSON_EQUALS", "NUMERIC_RANGE"}:
                raise ValueError(f"Unsupported deterministic assertion operator: {operator}")
            subject_ids = assertion.get("subject_ids", [])
            if not isinstance(subject_ids, list) or any(
                not isinstance(item, str) or not item for item in subject_ids
            ):
                raise ValueError(f"Assertion subject_ids must be a string array: {assertion_id}")
            for subject_id in subject_ids:
                validate_id(subject_id, f"{assertion_id} subject ID")
            assertion_kinds.add(kind)
            normalized_assertion: dict[str, Any] = {
                "assertion_id": assertion_id,
                "kind": kind,
                "expected": expected,
                "operator": operator,
            }
            if "subject_ids" in assertion:
                normalized_assertion["subject_ids"] = list(subject_ids)
            assertions.append(normalized_assertion)
        if REQUIRED_ASSERTIONS - assertion_kinds:
            raise ValueError(
                f"State plan lacks required assertion kinds: {sorted(REQUIRED_ASSERTIONS - assertion_kinds)}"
            )
        observable_assertions = [
            item for item in assertions
            if item["kind"] == "ANDROID_EXPECTED_OBSERVABLE"
        ]
        if len(observable_assertions) != 1 or (
            observable_assertions[0]["expected"]
            != migration_contract.get("android_expected_observable")
            or observable_assertions[0].get("subject_ids")
            != [migration_contract.get("inventory_id")]
        ):
            raise ValueError(
                "State plan must contain exactly one Android expected-observable assertion "
                "bound to the frozen Inventory-ID"
            )
        planned_subject_ids = {
            subject_id
            for assertion in assertions
            for subject_id in assertion.get("subject_ids", [])
        }
        required_obligation_ids = set(migration_contract.get("required_obligation_ids", []))
        required_semantic_ids = required_obligation_ids | {
            str(subject_id)
            for field in (
                "required_business_rule_ids", "required_data_dependency_ids",
                "required_system_capability_ids", "required_third_party_dependency_ids",
            )
            for subject_id in migration_contract.get(field, [])
        }
        if not required_semantic_ids <= planned_subject_ids:
            raise ValueError(
                "State plan omits frozen function/data/system obligations: "
                f"{sorted(required_semantic_ids - planned_subject_ids)}"
            )

        commands = plan.get("commands")
        if not isinstance(commands, list) or len(commands) != len(expected_sequence):
            raise ValueError(f"State plan requires exactly this command sequence: {expected_sequence}")
        normalized: list[dict[str, Any]] = []
        command_ids: set[str] = set()
        categories: list[str] = []
        all_placeholders = {
            placeholder for placeholders in COMMAND_PLACEHOLDERS.values()
            for placeholder in placeholders
        }
        for index, command in enumerate(commands):
            if not isinstance(command, dict):
                raise ValueError(f"commands[{index}] must be an object")
            command_id = validate_id(str(command.get("command_id", "")), "Command-ID")
            if command_id in command_ids:
                raise ValueError(f"Duplicate Command-ID: {command_id}")
            command_ids.add(command_id)
            category = str(command.get("category", ""))
            categories.append(category)
            cwd = safe_relative_path(project, str(command.get("cwd", ".")), "state command cwd")
            if not cwd.is_dir():
                raise ValueError(f"State command cwd is not a directory: {cwd}")
            argv, contract = validate_frozen_command(category, command.get("argv"), contracts)
            if not selector_is_present(argv, selector_tokens) or serial not in argv:
                raise ValueError(f"{command_id}: command lacks the exact frozen selector/serial")
            if category in BUNDLE_CATEGORIES and bundle_name not in argv:
                raise ValueError(f"{command_id}: command lacks the exact frozen Bundle")
            if category in TARGET_CATEGORIES and parity["target_id"] not in argv:
                raise ValueError(f"{command_id}: command lacks the exact Phase 3 target ID")
            required_placeholders = COMMAND_PLACEHOLDERS.get(category, set())
            present_placeholders = {item for item in argv if item in all_placeholders}
            if present_placeholders != required_placeholders:
                raise ValueError(
                    f"{command_id}: {category} must contain exactly these placeholders: "
                    f"{sorted(required_placeholders)}"
                )
            normalized.append(
                {
                    "command_id": command_id,
                    "category": category,
                    "cwd": cwd,
                    "plan_argv": argv,
                    "contract": contract,
                }
            )
        if categories != expected_sequence:
            raise ValueError(f"State command order differs; expected {expected_sequence}, got {categories}")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    final_relative = (
        Path("evidence") / h4env_id / parity["page_id"]
        / parity["state_id"] / evidence_id
    )
    final_dir = safe_relative_path(
        workspace, final_relative.as_posix(), "evidence target", must_exist=False
    )
    if final_dir.exists():
        parser.error(f"Evidence target already exists: {final_dir}")

    lock_material = (
        f"{environment['base_henv_id']}|{environment['device_id']}|"
        f"{environment['emulator'].get('serial')}"
    )
    lock_name = hashlib.sha256(lock_material.encode("utf-8")).hexdigest() + ".lock"
    global_lock = Path(tempfile.gettempdir()) / "codex-harmony-emulator-locks" / lock_name
    command_records: list[dict[str, Any]] = []
    errors: list[str] = []
    captured_at = utc_now()
    assertions_result: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix=f".{evidence_id}-", dir=workspace / ".staging") as temp_name:
        staging = Path(temp_name)
        logs = staging / "logs"
        logs.mkdir()
        screenshot = staging / "screenshot.png"
        uitest_result = staging / "ui-test-snapshot.json"
        uitest_metadata = staging / "ui-test-snapshot-metadata.json"
        uitest_trace = staging / "ui-test-snapshot-operation-trace.json"
        uitest_screenshot = staging / "ui-test-snapshot.png"
        assertions_path = staging / "assertions.json"
        shutil.copyfile(steps, staging / "steps.md")
        shutil.copyfile(test_hap, staging / "uitest-test.hap")
        replacements = {
            "{ARTIFACT}": str(artifact),
            "{ASSERTIONS}": str(assertions_path),
            "{SCREENSHOT}": str(screenshot),
            "{UITEST_RESULT}": str(uitest_result),
            "{TEST_HAP}": str(test_hap),
        }
        bindings = {
            "parity_id": parity_id,
            "hbuild_id": hbuild_id,
            "h4env_id": h4env_id,
            "device_id": device_id,
            "device_serial": serial,
            "bundle_name": bundle_name,
        }
        try:
            with exclusive_lock(global_lock, timeout=30.0):
                for command in normalized:
                    contract = command["contract"]
                    if sha256_file(Path(contract["resolved_executable"])) != contract["executable_sha256"]:
                        errors.append(
                            f"{command['command_id']}: frozen executable changed before execution"
                        )
                        break
                    output_path = {
                        "BUSINESS_ASSERT": assertions_path,
                        "SCREENSHOT_CAPTURE": screenshot,
                        "UITEST_SNAPSHOT_CAPTURE": uitest_result,
                    }.get(command["category"])
                    if output_path is not None and output_path.exists():
                        errors.append(
                            f"{command['command_id']}: output existed immediately before execution"
                        )
                        break
                    argv = substitute_exact(command["plan_argv"], replacements)
                    raw = run_command(argv, command["cwd"], args.timeout)
                    stdout = raw.pop("stdout")
                    stderr = raw.pop("stderr")
                    stdout_path = logs / f"{command['command_id']}.stdout.log"
                    stderr_path = logs / f"{command['command_id']}.stderr.log"
                    atomic_text(stdout_path, stdout)
                    atomic_text(stderr_path, stderr)
                    output_ok, success_hits, error_hits = frozen_output_verdict(
                        stdout, stderr, contract
                    )
                    passed = (
                        raw.get("exit_code") == 0 and raw.get("timed_out") is False
                        and raw.get("semantic_error") is False and output_ok
                    )
                    record = {
                        **raw,
                        "command_id": command["command_id"],
                        "category": command["category"],
                        "plan_argv": command["plan_argv"],
                        "argv": argv,
                        "resolved_executable": contract["resolved_executable"],
                        "executable_sha256": contract["executable_sha256"],
                        "required_argv_tokens": contract["required_argv_tokens"],
                        "success_output_contains": contract["success_output_contains"],
                        "error_output_contains": contract["error_output_contains"],
                        "success_output_matches": success_hits,
                        "error_output_matches": error_hits,
                        "stdout_path": stdout_path.relative_to(staging).as_posix(),
                        "stdout_sha256": sha256_file(stdout_path),
                        "stderr_path": stderr_path.relative_to(staging).as_posix(),
                        "stderr_sha256": sha256_file(stderr_path),
                        "command_verdict": "PASS" if passed else "FAIL",
                    }
                    command_records.append(record)
                    if not passed:
                        errors.append(
                            f"{command['command_id']} ({command['category']}) failed: "
                            f"exit={raw.get('exit_code')}, success_markers={success_hits}, "
                            f"error_markers={error_hits}"
                        )
                        break
                    try:
                        if command["category"] == "BUSINESS_ASSERT":
                            assertions_result = validate_assertion_result(
                                assertions_path, assertions, bindings, required_obligation_ids
                            )
                            record["result_path"] = "assertions.json"
                            record["result_sha256"] = sha256_file(assertions_path)
                        elif command["category"] == "SCREENSHOT_CAPTURE":
                            width, height = png_dimensions(screenshot)
                            expected_width = environment["comparison"]["screenshot_width"]
                            expected_height = environment["comparison"]["screenshot_height"]
                            if (width, height) != (expected_width, expected_height):
                                raise ValueError(
                                    f"Emulator screenshot dimensions differ: expected "
                                    f"{expected_width}x{expected_height}, got {width}x{height}"
                                )
                            record["result_path"] = "screenshot.png"
                            record["result_sha256"] = sha256_file(screenshot)
                        elif command["category"] == "UITEST_SNAPSHOT_CAPTURE":
                            command_sha256 = hashlib.sha256(json.dumps(
                                argv, ensure_ascii=False, separators=(",", ":")
                            ).encode("utf-8")).hexdigest()
                            raw_uitest_metadata = load_json(uitest_metadata)
                            required_raw = {
                                "probe_id": probe_id, "page_id": parity["page_id"],
                                "state_id": parity["state_id"],
                                "test_hap_sha256": test_hap_sha256,
                                "final_hap_sha256": final_hap_sha256,
                                "device_identity_sha256": device_identity_sha256,
                                "command_sha256": command_sha256,
                            }
                            if not isinstance(raw_uitest_metadata, dict) or any(
                                raw_uitest_metadata.get(field) != expected
                                for field, expected in required_raw.items()
                            ):
                                raise ValueError("UiTest runtime metadata differs from frozen run binding")
                            atomic_json(uitest_metadata, {
                                "schema_version": "ui-test-snapshot-evidence-v1",
                                "probe_id": probe_id, "page_id": parity["page_id"],
                                "state_id": parity["state_id"], "bundle_name": bundle_name,
                                "carrier": migration_contract["expected_carrier"],
                                "target_id": parity["target_id"],
                                "result_path": "ui-test-snapshot.json",
                                "result_sha256": sha256_file(uitest_result),
                                "operation_trace_path": "ui-test-snapshot-operation-trace.json",
                                "operation_trace_sha256": sha256_file(uitest_trace),
                                "screenshot_path": "ui-test-snapshot.png",
                                "screenshot_sha256": sha256_file(uitest_screenshot),
                                "generation_manifest_sha256": sha256_file(generation_manifest_path),
                                "page_plan_sha256": sha256_file(page_plan),
                                "test_hap_sha256": test_hap_sha256,
                                "final_hap_sha256": final_hap_sha256,
                                "device_identity_sha256": device_identity_sha256,
                                "command_sha256": command_sha256,
                            })
                            if verification_tier == "LITE":
                                # LITE 轻证：哈希绑定保留，逐组件严检降为结构化组件树
                                # 对比（expected=页合同组件树，阈值见 LITE_COMPONENT_OVERLAP_MIN）。
                                page_contract_doc = load_json(
                                    workspace / "page-contracts" / f"{parity['page_id']}.json"
                                )
                                lite_result = validate_uitest_evidence_lite(
                                    staging, probe,
                                    page_id=parity["page_id"], state_id=parity["state_id"],
                                    bundle_name=bundle_name,
                                    carrier=str(migration_contract["expected_carrier"]),
                                    target_id=parity["target_id"],
                                    generation_manifest_sha256=sha256_file(generation_manifest_path),
                                    page_plan_sha256=sha256_file(page_plan),
                                    test_hap_sha256=test_hap_sha256,
                                    final_hap_sha256=final_hap_sha256,
                                    device_identity_sha256=device_identity_sha256,
                                    command_sha256=command_sha256,
                                    expected_components=page_contract_doc.get("components"),
                                )
                                lite_components = lite_result["components"]
                                lite_trace = lite_result["operation_trace"]
                            else:
                                validate_uitest_evidence(
                                    staging, probe,
                                    page_id=parity["page_id"], state_id=parity["state_id"],
                                    bundle_name=bundle_name,
                                    carrier=str(migration_contract["expected_carrier"]),
                                    target_id=parity["target_id"],
                                    generation_manifest_sha256=sha256_file(generation_manifest_path),
                                    page_plan_sha256=sha256_file(page_plan),
                                    test_hap_sha256=test_hap_sha256,
                                    final_hap_sha256=final_hap_sha256,
                                    device_identity_sha256=device_identity_sha256,
                                    command_sha256=command_sha256,
                                    required_event_ids=set(migration_contract.get("required_event_ids", [])),
                                    required_transition_ids=set(migration_contract.get("required_transition_ids", [])),
                                )
                                lite_result = None
                                lite_components = lite_trace = None
                            uitest_width, uitest_height = png_dimensions(uitest_screenshot)
                            if (uitest_width, uitest_height) != (
                                environment["comparison"]["screenshot_width"],
                                environment["comparison"]["screenshot_height"],
                            ):
                                raise ValueError("UiTest screenshot dimensions differ from frozen environment")
                            if verification_tier == "LITE":
                                # LITE 无 BUSINESS_ASSERT 命令：三类断言从实拍观测合成，
                                # 判定链（operator/三类/obligation 覆盖）不放宽。
                                assertions_result = synthesize_lite_assertions(
                                    assertions_path,
                                    assertions,
                                    bindings,
                                    {
                                        "component_overlap": lite_result["lite_component_overlap"],
                                        "component_count": len(lite_components),
                                        "trace_count": len(lite_trace),
                                        "screenshot_width": uitest_width,
                                        "screenshot_height": uitest_height,
                                    },
                                    required_obligation_ids,
                                )
                            record["result_path"] = "ui-test-snapshot.json"
                            record["result_sha256"] = sha256_file(uitest_result)
                    except (ValueError, OSError, KeyError) as exc:
                        errors.append(str(exc))
                        break
        except (RuntimeError, OSError) as exc:
            errors.append(str(exc))

        if not errors:
            final_snapshot = build_project_snapshot(project)
            if final_snapshot["snapshot_sha256"] != build_metadata["source_snapshot_sha256"]:
                errors.append("State commands changed controlled source files")
        if not errors and not assertions_result:
            errors.append("Live BUSINESS_ASSERT output was not generated")

        if errors:
            attempt = {
                "attempt_id": "ATT-" + evidence_id,
                "evidence_id": evidence_id,
                "parity_id": parity_id,
                "h4env_id": h4env_id,
                "hbuild_id": hbuild_id,
                "attempted_at": utc_now(),
                "executed_by": executed_by,
                "commands": command_records,
                "errors": errors,
                "valid_evidence_id": None,
            }
            atomic_json(workspace / "attempts" / f"ATT-{evidence_id}.json", attempt)
            print(json.dumps(attempt, ensure_ascii=False, indent=2))
            return 1

        width, height = png_dimensions(screenshot)
        metadata = {
            "evidence_id": evidence_id,
            "supersedes_evidence_id": supersedes,
            "parity_id": parity_id,
            "migration_unit_id": migration_contract["migration_unit_id"],
            "migration_unit_contracts_sha256": contract_sha,
            "inventory_id": parity["inventory_id"],
            "feature_id": parity["feature_id"],
            "page_id": parity["page_id"],
            "state_id": parity["state_id"],
            "source_env_id": parity["source_env_id"],
            "android_evidence_id": parity["android_evidence_id"],
            "h4env_id": h4env_id,
            "base_henv_id": environment["base_henv_id"],
            "device_id": device_id,
            "device_type": "emulator",
            "device_serial": serial,
            "bundle_name": bundle_name,
            "hbuild_id": hbuild_id,
            "source_snapshot_sha256": build_metadata["source_snapshot_sha256"],
            "build_artifact_sha256": primary["sha256"],
            "input_lock_sha256": sha256_file(workspace / "stage-04-input-lock.json"),
            "environment_sha256": sha256_file(env_path),
            "target_kind": parity["target_kind"],
            "target_id": parity["target_id"],
            "asset_ids": split_multi(parity.get("asset_ids", "")),
            "nativeization_decision_ids": split_multi(
                parity.get("nativeization_decision_ids", "")
            ),
            "implemented_by": implemented_by,
            "captured_by": executed_by,
            "captured_at": captured_at,
            # P4 分层验证：tier 随证据密封（CORE=全证据深验；LITE=轻证）。
            "verification_tier": verification_tier,
            "assertions": {
                "path": "assertions.json",
                "sha256": sha256_file(assertions_path),
                "command_id": next(
                    item["command_id"] for item in command_records
                    if item["category"] == (
                        "UITEST_SNAPSHOT_CAPTURE" if verification_tier == "LITE"
                        else "BUSINESS_ASSERT"
                    )
                ),
            },
            "screenshot": {
                "path": "screenshot.png",
                "sha256": sha256_file(screenshot),
                "width": width,
                "height": height,
                "command_id": next(
                    item["command_id"] for item in command_records
                    if item["category"] == "SCREENSHOT_CAPTURE"
                ),
            },
            "ui_test_snapshot": {
                "path": "ui-test-snapshot.json",
                "sha256": sha256_file(uitest_result),
                "metadata_sha256": sha256_file(uitest_metadata),
                "operation_trace_sha256": sha256_file(uitest_trace),
                "screenshot_sha256": sha256_file(uitest_screenshot),
                "generation_manifest_sha256": sha256_file(generation_manifest_path),
                "page_plan_sha256": sha256_file(page_plan),
                "test_hap_sha256": test_hap_sha256,
                "test_hap_path": "uitest-test.hap",
                "final_hap_sha256": final_hap_sha256,
                "device_identity_sha256": device_identity_sha256,
                "command_id": next(
                    item["command_id"] for item in command_records
                    if item["category"] == "UITEST_SNAPSHOT_CAPTURE"
                ),
            },
            "commands": command_records,
            "status": "SEALED",
        }
        atomic_json(staging / "metadata.json", metadata)
        relative_names = package_file_set(staging)
        atomic_text(staging / "manifest.sha256", manifest_text(staging, sorted(relative_names)))
        manifest_sha = sha256_file(staging / "manifest.sha256")
        atomic_text(
            staging / "COMMITTED",
            f"{evidence_id} SEALED manifest_sha256={manifest_sha} committed_at={utc_now()}\n",
        )
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(final_dir)

    parity_fields = csv_fieldnames(ASSETS / "parity-map.template.csv")
    index_fields = csv_fieldnames(ASSETS / "evidence-index.template.csv")
    registry_lock = workspace / ".locks" / "phase4-registries.lock"
    try:
        with exclusive_lock(registry_lock):
            # Re-read under the lock. Two parallel captures must merge rather than
            # overwriting one another with pre-execution registry snapshots.
            current_parity_rows = read_csv(workspace / "parity-map.csv")
            current_index_rows = read_csv(workspace / "evidence-index.csv")
            if any(row.get("evidence_id") == evidence_id for row in current_index_rows):
                raise ValueError(f"HEVD-ID was committed concurrently: {evidence_id}")
            target_parity = next(
                (row for row in current_parity_rows if row.get("parity_id") == parity_id), None
            )
            if not target_parity:
                raise ValueError("Parity row disappeared during capture")
            current_id = target_parity.get("harmony_evidence_id", "")
            if current_id != (supersedes or ""):
                raise ValueError("Parity evidence changed concurrently; retry with a new HEVD-ID")
            if supersedes:
                old = next(
                    (
                        row for row in current_index_rows
                        if row.get("evidence_id") == supersedes
                        and row.get("parity_id") == parity_id
                        and row.get("status") == "SEALED"
                    ),
                    None,
                )
                if not old:
                    raise ValueError("Superseded evidence changed concurrently")
                old["status"] = "SUPERSEDED"
            target_parity["harmony_evidence_id"] = evidence_id
            target_parity["status"] = "EVIDENCED"
            current_index_rows.append(
                {
                    "evidence_id": evidence_id,
                    "parity_id": parity_id,
                    "inventory_id": parity["inventory_id"],
                    "feature_id": parity["feature_id"],
                    "page_id": parity["page_id"],
                    "state_id": parity["state_id"],
                    "h4env_id": h4env_id,
                    "hbuild_id": hbuild_id,
                    "android_evidence_id": parity["android_evidence_id"],
                    "relative_path": final_relative.as_posix(),
                    "metadata_sha256": sha256_file(final_dir / "metadata.json"),
                    "screenshot_sha256": sha256_file(final_dir / "screenshot.png"),
                    "source_snapshot_sha256": build_metadata["source_snapshot_sha256"],
                    "build_artifact_sha256": primary["sha256"],
                    "captured_by": executed_by,
                    "captured_at": captured_at,
                    "verification_tier": verification_tier,
                    "status": "SEALED",
                    "supersedes_evidence_id": supersedes,
                }
            )
            write_csv(workspace / "parity-map.csv", parity_fields, current_parity_rows)
            try:
                write_csv(workspace / "evidence-index.csv", index_fields, current_index_rows)
            except Exception:
                # The locked copy is authoritative for rollback; do not restore a
                # stale pre-execution snapshot.
                target_parity["harmony_evidence_id"] = supersedes
                target_parity["status"] = parity.get("status", "IMPLEMENTED")
                write_csv(workspace / "parity-map.csv", parity_fields, current_parity_rows)
                raise
    except Exception as exc:
        shutil.rmtree(final_dir)
        parser.error(f"Evidence registry commit failed; no valid HEVD was issued: {exc}")

    try:
        make_tree_read_only(final_dir)
    except (OSError, ValueError) as exc:
        parser.error(f"Evidence was registered but could not be made read-only: {exc}")
    print(json.dumps({"evidence_id": evidence_id, "path": str(final_dir)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
