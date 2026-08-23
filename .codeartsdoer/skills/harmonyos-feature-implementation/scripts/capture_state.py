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

from arkui_inspector import (
    bind_required_components, validate_and_normalize, validate_operation_snapshot,
)

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
    "LAUNCH", "NAVIGATE", "BUSINESS_ASSERT", "SCREENSHOT_CAPTURE", "UI_TREE_CAPTURE",
]
REQUIRED_ASSERTIONS = {"VISUAL_STATE", "BUSINESS_RESULT", "INTERACTION"}
BUNDLE_CATEGORIES = {
    "CLEAN_INSTALL", "SEED_RESET", "PERMISSION_PROFILE", "LAUNCH", "NAVIGATE", "BUSINESS_ASSERT",
    "SCREENSHOT_CAPTURE", "UI_TREE_CAPTURE",
}
TARGET_CATEGORIES = {"NAVIGATE", "BUSINESS_ASSERT", "SCREENSHOT_CAPTURE", "UI_TREE_CAPTURE"}
OUTPUT_PLACEHOLDERS = {
    "CLEAN_INSTALL": "{ARTIFACT}",
    "BUSINESS_ASSERT": "{ASSERTIONS}",
    "SCREENSHOT_CAPTURE": "{SCREENSHOT}",
    "UI_TREE_CAPTURE": "{UI_TREE}",
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


def validate_ui_tree(
    path: Path,
    bundle_name: str,
    device_id: str,
    serial: str,
    contract: dict[str, Any],
) -> dict[str, Any]:
    value = validate_and_normalize(load_json(path))
    if value.get("bundle_name") != bundle_name:
        raise ValueError("UI tree Bundle differs from the frozen application")
    if not isinstance(value.get("window"), dict) or not value["window"]:
        raise ValueError("UI tree window object is empty")
    if value.get("carrier") != contract.get("expected_carrier"):
        raise ValueError(
            f"Runtime carrier differs: expected {contract.get('expected_carrier')}, "
            f"got {value.get('carrier')}"
        )
    if value.get("target_id") != contract.get("target_id"):
        raise ValueError("Runtime route/surface target differs from the frozen migration unit")
    locators = contract.get("component_locators", {})
    nodes = value["nodes"]
    value["component_bindings"] = bind_required_components(
        nodes, contract.get("required_component_ids", []), locators
    )
    traces = value.get("operation_trace", [])
    if not isinstance(traces, list) or any(not isinstance(item, dict) for item in traces):
        raise ValueError("UI tree operation_trace must be an object array")
    traced: dict[tuple[str, str], dict[str, Any]] = {}
    for trace in traces:
        subject_type = str(trace.get("subject_type", ""))
        subject_id = str(trace.get("subject_id", ""))
        key = (subject_type, subject_id)
        if subject_type not in {"EVENT", "TRANSITION"} or not subject_id or key in traced:
            raise ValueError("Operation trace has an invalid or duplicate subject identity")
        before = trace.get("before_snapshot")
        after = trace.get("after_snapshot")
        if not isinstance(before, dict) or not before or not isinstance(after, dict) or not after:
            raise ValueError(f"Operation trace lacks raw before/after snapshots: {subject_id}")
        try:
            validate_operation_snapshot(before)
            validate_operation_snapshot(after)
        except ValueError as exc:
            raise ValueError(
                f"Operation trace is not backed by ArkUI Inspector snapshots: {subject_id}: {exc}"
            ) from exc
        if not str(trace.get("action", "")).strip():
            raise ValueError(f"Operation trace lacks the executed action: {subject_id}")
        changed = json.dumps(before, sort_keys=True, ensure_ascii=False) != json.dumps(
            after, sort_keys=True, ensure_ascii=False
        )
        if not changed and not str(trace.get("observable_result", "")).strip():
            raise ValueError(f"Operation trace proves no state or observable change: {subject_id}")
        traced[key] = trace
    for subject_type, required_field in (
        ("EVENT", "required_event_ids"), ("TRANSITION", "required_transition_ids")
    ):
        required = set(contract.get(required_field, []))
        observed = {subject_id for trace_type, subject_id in traced if trace_type == subject_type}
        if required != observed:
            raise ValueError(
                f"Runtime operation trace differs from {required_field}: "
                f"missing={sorted(required - observed)}, extra={sorted(observed - required)}"
            )
    bounds = value.get("bounds")
    if not isinstance(bounds, dict) or any(
        not isinstance(bounds.get(field), (int, float))
        for field in ("x", "y", "width", "height")
    ) or bounds["width"] <= 0 or bounds["height"] <= 0:
        raise ValueError("UI tree bounds are missing or invalid")
    device = value.get("device")
    if not isinstance(device, dict) or device.get("device_id") != device_id or device.get(
        "serial"
    ) != serial:
        raise ValueError("UI tree device identity differs from the frozen emulator")
    atomic_json(path, value)
    return value


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
        legacy_roles = phase_manifest.get("roles", {})
        expected_executor = ownership.get("verification_executor_id") or legacy_roles.get(
            "verification_executor"
        )
        expected_parity = ownership.get("parity_acceptance_agent_id") or legacy_roles.get(
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
        if (
            migration_contract.get("inventory_id") != parity.get("inventory_id")
            or migration_contract.get("feature_id") != parity.get("feature_id")
            or migration_contract.get("page_id") != parity.get("page_id")
            or migration_contract.get("state_id") != parity.get("state_id")
            or migration_contract.get("h4env_id") != h4env_id
            or migration_contract.get("target_kind") != parity.get("target_kind")
            or migration_contract.get("target_id") != parity.get("target_id")
            or migration_contract.get("expected_carrier") != migration_contract.get("scaffold_carrier")
            or migration_contract.get("simplification_policy") != "FORBIDDEN"
            or migration_contract.get("native_optimization_policy")
            != "INTERNAL_ONLY_UNLESS_APPROVED"
            or migration_contract.get("max_automatic_repair_attempts") != 2
        ):
            raise ValueError("Migration-unit identity, carrier, or anti-simplification policy differs")

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
        if not isinstance(commands, list) or len(commands) != len(REQUIRED_SEQUENCE):
            raise ValueError(f"State plan requires exactly this command sequence: {REQUIRED_SEQUENCE}")
        normalized: list[dict[str, Any]] = []
        command_ids: set[str] = set()
        categories: list[str] = []
        all_placeholders = set(OUTPUT_PLACEHOLDERS.values())
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
            required_placeholder = OUTPUT_PLACEHOLDERS.get(category)
            present_placeholders = {item for item in argv if item in all_placeholders}
            if required_placeholder and present_placeholders != {required_placeholder}:
                raise ValueError(
                    f"{command_id}: {category} must contain only its exact {required_placeholder} placeholder"
                )
            if not required_placeholder and present_placeholders:
                raise ValueError(f"{command_id}: unexpected output/artifact placeholder")
            normalized.append(
                {
                    "command_id": command_id,
                    "category": category,
                    "cwd": cwd,
                    "plan_argv": argv,
                    "contract": contract,
                }
            )
        if categories != REQUIRED_SEQUENCE:
            raise ValueError(f"State command order differs; expected {REQUIRED_SEQUENCE}, got {categories}")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))

    final_relative = (
        Path("evidence") / h4env_id / parity["feature_id"] / parity["page_id"]
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
        ui_tree = staging / "ui-tree.json"
        assertions_path = staging / "assertions.json"
        shutil.copyfile(steps, staging / "steps.md")
        replacements = {
            "{ARTIFACT}": str(artifact),
            "{ASSERTIONS}": str(assertions_path),
            "{SCREENSHOT}": str(screenshot),
            "{UI_TREE}": str(ui_tree),
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
                        "UI_TREE_CAPTURE": ui_tree,
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
                        elif command["category"] == "UI_TREE_CAPTURE":
                            validate_ui_tree(
                                ui_tree, bundle_name, device_id, serial, migration_contract
                            )
                            record["result_path"] = "ui-tree.json"
                            record["result_sha256"] = sha256_file(ui_tree)
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
            "assertions": {
                "path": "assertions.json",
                "sha256": sha256_file(assertions_path),
                "command_id": next(
                    item["command_id"] for item in command_records
                    if item["category"] == "BUSINESS_ASSERT"
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
            "ui_tree": {
                "path": "ui-tree.json",
                "sha256": sha256_file(ui_tree),
                "command_id": next(
                    item["command_id"] for item in command_records
                    if item["category"] == "UI_TREE_CAPTURE"
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
