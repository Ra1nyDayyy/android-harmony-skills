#!/usr/bin/env python3
"""Generate conservation-bound UiTest page probes under ohosTest only."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from _common import load_json, sha256_file, validate_id
from arkts_page_plan import compile_arkts_page_plan, validate_arkts_page_plan
from page_acceptance_contract import canonical_contract_sha256


SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSETS = SKILL_ROOT / "assets" / "uitest-snapshot"
TEST_RELATIVE_ROOT = PurePosixPath("entry/src/ohosTest/ets/test")
GENERATED_NAMES = (
    "UiTestSnapshot.test.ets",
    "UiTestPageProbeRegistry.ets",
    "UiTestRunBinding.ets",
)
RUNTIME_HASH_FIELDS = (
    "test_hap_sha256",
    "final_hap_sha256",
    "device_identity_sha256",
    "command_sha256",
)


def _canonical_sha(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _workspace(value: Path) -> Path:
    workspace = Path(value).resolve(strict=True)
    project = workspace / "harmony-project"
    if workspace.is_symlink() or not project.is_dir():
        raise ValueError("Phase 4 workspace must contain a regular harmony-project")
    main = project / "entry" / "src" / "main"
    if main.exists() and any(path.name in GENERATED_NAMES for path in main.rglob("*")):
        raise ValueError("UiTest probe files are forbidden in production main source")
    return workspace


def _contracts(workspace: Path) -> list[tuple[dict[str, Any], str, str]]:
    registry = workspace / "page-contract-registry.csv"
    try:
        with registry.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
    except FileNotFoundError as exc:
        raise ValueError("Page contract registry is missing") from exc
    if not rows:
        raise ValueError("Page contract registry is empty")
    result: list[tuple[dict[str, Any], str, str]] = []
    seen: set[str] = set()
    for row in rows:
        page_id = validate_id(row.get("page_id", ""), "Page-ID")
        if page_id in seen:
            raise ValueError(f"Duplicate Page-ID in contract registry: {page_id}")
        seen.add(page_id)
        relative = row.get("relative_path", "")
        expected = f"page-contracts/{page_id}.json"
        if relative != expected:
            raise ValueError(f"{page_id} contract path is not canonical")
        path = workspace / Path(*PurePosixPath(relative).parts)
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"{page_id} page contract is missing")
        contract = load_json(path)
        if not isinstance(contract, dict) or contract.get("page_id") != page_id:
            raise ValueError(f"{page_id} page contract identity differs")
        digest = canonical_contract_sha256(contract)
        if digest != row.get("contract_sha256"):
            raise ValueError(f"{page_id} page contract hash differs")
        result.append((contract, relative, digest))
    return sorted(result, key=lambda item: str(item[0]["page_id"]))


def _component_locators(plan: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "component_id": str(component["component_id"]),
            "locator_strategy": str(component["locator"]["strategy"]),
            "locator_value": str(component["locator"]["value"]),
        }
        for component in plan.get("components", [])
        if isinstance(component, dict)
    ]


def _probes(plan: dict[str, Any], plans_by_page: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    page_id = str(plan["page_id"])
    components = plan.get("components")
    states = plan.get("states")
    if not isinstance(components, list) or not components:
        raise ValueError(f"{page_id} has no required component plan")
    if not isinstance(states, list) or not states:
        raise ValueError(f"{page_id} has no required state plan")
    actions = [
        {
            "event_id": str(row.get("event_id", "")),
            "component_id": str(row.get("component_id", "")),
            "action": str(row.get("action", "")),
        }
        for row in plan.get("events_actions", [])
        if isinstance(row, dict)
    ]
    if len(actions) > 1:
        raise ValueError(
            f"{page_id} requires isolated action probes; multiple actions may not share one frozen State-ID run"
        )
    transitions = []
    action_event_ids = {row["event_id"] for row in actions if row["event_id"] and row["component_id"]}
    for row in plan.get("transitions", []):
        if not isinstance(row, dict) or not row.get("transition_id"):
            continue
        event_id = str(row.get("event_id", ""))
        target_page_id = str(row.get("target_page_id", ""))
        target_plan = plans_by_page.get(target_page_id)
        if not event_id or event_id not in action_event_ids or not target_plan:
            raise ValueError(
                f"{page_id} transition {row.get('transition_id')} lacks a frozen event/action/target probe"
            )
        target_locators = _component_locators(target_plan)
        if not target_locators:
            raise ValueError(f"{page_id} transition {row.get('transition_id')} has no target locator")
        transitions.append({
            "transition_id": str(row["transition_id"]),
            "event_id": event_id,
            "source_page_id": str(row.get("source_page_id", "")),
            "target_page_id": target_page_id,
            "target_components": target_locators,
            "return_action": "PRESS_BACK",
        })
    result = []
    state_targets = plan["carrier"]["state_targets"]
    for state in sorted(states, key=lambda row: str(row["state_id"])):
        state_id = str(state.get("state_id", ""))
        records = state.get("records")
        target = state_targets.get(state_id)
        if not state_id or not isinstance(records, list) or not records or not isinstance(target, dict):
            raise ValueError(f"{page_id} {state_id or '<missing>'} state probe is incomplete")
        first = records[0]
        required_components = []
        for component in components:
            source = component["source_record"]
            component_states = source.get("state_ids", source.get("state_id", ""))
            if isinstance(component_states, str) and component_states:
                declared = {item.strip() for item in component_states.replace("|", ",").split(",") if item.strip()}
                if state_id not in declared:
                    continue
            required_components.append({
                "component_id": component["component_id"],
                "source_type": component["source_type"],
                "arkts_type": component["arkts_type"],
                "locator_strategy": component["locator"]["strategy"],
                "locator_value": component["locator"]["value"],
                "expected_text": str(source.get("text", "")),
            })
        if not required_components:
            raise ValueError(f"{page_id} {state_id} has no required component locator")
        result.append({
            "probe_id": f"{page_id}::{state_id}",
            "page_id": page_id,
            "state_id": state_id,
            "target_id": target["target_id"],
            "entry_condition": str(first.get("entry_condition", "")),
            "action_summary": str(first.get("action_summary", "")),
            "required_components": required_components,
            "declared_actions": actions,
            "declared_transitions": transitions,
            "result_directory": f"ui-test-snapshot/{page_id}/{state_id}",
        })
    return result


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(value)


def _publish_transaction(staged: Path, workspace: Path, relative_paths: list[PurePosixPath]) -> None:
    backups = staged / ".backups"
    moved: list[tuple[Path, Path | None]] = []
    try:
        for index, relative in enumerate(relative_paths):
            source = staged / Path(*relative.parts)
            target = workspace / Path(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            backup: Path | None = None
            if target.exists():
                if target.is_symlink() or not target.is_file():
                    raise ValueError(f"Refusing to replace non-regular generated target: {relative}")
                target.chmod(0o644)
                backup = backups / f"{index}.bak"
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, backup)
            os.replace(source, target)
            moved.append((target, backup))
    except Exception:
        for target, backup in reversed(moved):
            if target.exists():
                target.chmod(0o644)
                target.unlink()
            if backup and backup.exists():
                os.replace(backup, target)
        raise


def prepare_uitest_probe(workspace: Path) -> dict[str, Any]:
    workspace = _workspace(workspace)
    contracts = _contracts(workspace)
    staging_root = workspace / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="uitest-snapshot-", dir=staging_root) as temp_name:
        staged = Path(temp_name)
        plan_records: list[dict[str, str]] = []
        probes: list[dict[str, Any]] = []
        plans_by_page: dict[str, dict[str, Any]] = {}
        for contract, contract_relative, contract_digest in contracts:
            plan = compile_arkts_page_plan(contract, contract_digest)
            validate_arkts_page_plan(plan, contract, contract_digest)
            page_id = str(contract["page_id"])
            plans_by_page[page_id] = plan
            plan_relative = PurePosixPath(f"arkts-page-plans/{page_id}/arkts-page-plan.json")
            plan_path = staged / Path(*plan_relative.parts)
            _write_text(plan_path, json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            plan_records.append({
                "page_id": page_id,
                "relative_path": plan_relative.as_posix(),
                "sha256": sha256_file(plan_path),
                "source_contract_relative_path": contract_relative,
                "source_contract_sha256": contract_digest,
            })
        for page_id in sorted(plans_by_page):
            probes.extend(_probes(plans_by_page[page_id], plans_by_page))
        probes.sort(key=lambda row: row["probe_id"])
        if len({row["probe_id"] for row in probes}) != len(probes):
            raise ValueError("UiTest probe IDs are not unique")
        test_root = PurePosixPath("harmony-project") / TEST_RELATIVE_ROOT
        registry_template = (ASSETS / "UiTestPageProbeRegistry.ets").read_text(encoding="utf-8")
        registry_text = registry_template.replace(
            "__UI_TEST_PAGE_PROBES__",
            json.dumps(probes, ensure_ascii=False, indent=2, sort_keys=True),
        )
        generated_values = {
            "UiTestSnapshot.test.ets": (ASSETS / "UiTestSnapshot.test.ets").read_text(encoding="utf-8"),
            "UiTestPageProbeRegistry.ets": registry_text,
            "UiTestRunBinding.ets": (ASSETS / "UiTestRunBinding.ets").read_text(encoding="utf-8"),
        }
        generated_records = []
        generated_relatives: list[PurePosixPath] = []
        for name, value in generated_values.items():
            relative = test_root / name
            path = staged / Path(*relative.parts)
            _write_text(path, value)
            generated_relatives.append(relative)
            generated_records.append({
                "relative_path": relative.relative_to("harmony-project").as_posix(),
                "sha256": sha256_file(path),
            })
        manifest = {
            "schema_version": "ui-test-snapshot-generation-v1",
            "generation_id": "UITEST-GEN-" + _canonical_sha({"plans": plan_records, "probes": probes, "files": generated_records})[:20].upper(),
            "page_plans": plan_records,
            "probes": probes,
            "generated_files": sorted(generated_records, key=lambda row: row["relative_path"]),
            "required_runtime_hash_fields": list(RUNTIME_HASH_FIELDS),
            "production_source_policy": "OHOS_TEST_ONLY",
        }
        manifest_relative = PurePosixPath("ui-test-snapshot-generation-manifest.json")
        manifest_path = staged / manifest_relative
        _write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        relatives = [PurePosixPath(record["relative_path"]) for record in plan_records]
        relatives.extend(generated_relatives)
        relatives.append(manifest_relative)
        _publish_transaction(staged, workspace, relatives)
    final_manifest = workspace / "ui-test-snapshot-generation-manifest.json"
    return {
        "manifest": str(final_manifest),
        "generation_id": manifest["generation_id"],
        "page_ids": [str(contract[0]["page_id"]) for contract in contracts],
        "probe_count": len(probes),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    try:
        result = prepare_uitest_probe(Path(args.workspace))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
