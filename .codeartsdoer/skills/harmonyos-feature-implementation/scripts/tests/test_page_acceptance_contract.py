#!/usr/bin/env python3
"""Contract tests for immutable, Phase 2-derived page acceptance inputs."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))

import page_acceptance_contract as contract_module  # noqa: E402
from page_acceptance_contract import (  # noqa: E402
    canonical_contract_sha256,
    compile_page_contracts,
    publish_page_contracts,
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["id"]
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PageAcceptanceContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="page-contract-")
        self.root = Path(self.temp.name)
        self.phase2 = self.root / "phase2"
        self.phase3 = self.root / "phase3"
        build_fixture(self.phase2, self.phase3)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_compiles_one_contract_per_page_with_all_states(self) -> None:
        contracts = compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))
        self.assertEqual(["PAGE-CALCULATOR", "PAGE-HISTORY"], [c["page_id"] for c in contracts])
        calculator = contracts[0]
        self.assertEqual(
            {"STATE-EMPTY", "STATE-RESULT", "STATE-ERROR"},
            {s["state_id"] for s in calculator["states"]},
        )
        self.assertIn("TRANS-CALC-HISTORY", {t["transition_id"] for t in calculator["transitions"]})
        self.assertIn("SIDE-CLIPBOARD", {e["side_effect_id"] for e in calculator["side_effects"]})
        self.assertEqual(["Calculator.kt:1"], [item["code_ref"] for item in calculator["code_map"]])
        self.assertEqual(["H4ENV-001"], calculator["required_h4env_ids"])
        self.assertEqual(0.98, calculator["comparison_policy"]["application_region_ssim"])

    def test_rejects_page_when_inventory_references_missing_evidence(self) -> None:
        remove_android_evidence(self.phase2, "EVID-RESULT")
        with self.assertRaisesRegex(ValueError, "PAGE-CALCULATOR.*EVID-RESULT"):
            compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))

    def test_rejects_duplicate_static_ids_and_blank_page_ids(self) -> None:
        pages = read_json(self.phase2 / "static-analysis" / "pages.json")
        pages["pages"].append(dict(pages["pages"][0]))
        write_json(self.phase2 / "static-analysis" / "pages.json", pages)
        with self.assertRaisesRegex(ValueError, "duplicate Page-ID.*PAGE-CALCULATOR"):
            compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))
        build_fixture(self.phase2, self.phase3)
        pages = read_json(self.phase2 / "static-analysis" / "pages.json")
        pages["pages"][0]["page_id"] = ""
        write_json(self.phase2 / "static-analysis" / "pages.json", pages)
        with self.assertRaisesRegex(ValueError, "empty.*Page-ID"):
            compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))

    def test_rejects_orphan_references_and_missing_evidence_payloads(self) -> None:
        components = read_json(self.phase2 / "static-analysis" / "components.json")
        components["components"][0]["page_id"] = "PAGE-ORPHAN"
        write_json(self.phase2 / "static-analysis" / "components.json", components)
        with self.assertRaisesRegex(ValueError, "orphan.*PAGE-ORPHAN"):
            compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))
        build_fixture(self.phase2, self.phase3)
        evidence = self.phase2 / "evidence" / "ENV-001" / "PAGE-CALCULATOR" / "STATE-EMPTY" / "EVID-EMPTY"
        (evidence / "screenshot.png").unlink()
        with self.assertRaisesRegex(ValueError, "PAGE-CALCULATOR.*EVID-EMPTY.*screenshot"):
            compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))
        build_fixture(self.phase2, self.phase3)
        (evidence / "layout.json").unlink()
        with self.assertRaisesRegex(ValueError, "PAGE-CALCULATOR.*EVID-EMPTY.*layout"):
            compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))

    def test_rejects_uncovered_runtime_states_and_missing_phase3_route(self) -> None:
        observations = read_json(self.phase2 / "runtime-observations.json")
        observations["observations"] = [
            row for row in observations["observations"] if row["after_evidence_id"] != "EVID-ERROR"
        ]
        write_json(self.phase2 / "runtime-observations.json", observations)
        with self.assertRaisesRegex(ValueError, "PAGE-CALCULATOR.*STATE-ERROR.*runtime"):
            compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))
        build_fixture(self.phase2, self.phase3)
        routes = read_csv(self.phase3 / "route-registry.csv")
        write_csv(self.phase3 / "route-registry.csv", [row for row in routes if row["page_id"] != "PAGE-HISTORY"])
        with self.assertRaisesRegex(ValueError, "PAGE-HISTORY.*Phase 3 route"):
            compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))

    def test_contract_ordering_and_hash_are_stable(self) -> None:
        first = compile_page_contracts(self.phase2, self.phase3, ("H4ENV-002", "H4ENV-001"))
        second = compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001", "H4ENV-002"))
        self.assertEqual(first, second)
        self.assertEqual(
            [canonical_contract_sha256(contract) for contract in first],
            [canonical_contract_sha256(contract) for contract in second],
        )
        self.assertEqual(["STATE-EMPTY", "STATE-ERROR", "STATE-RESULT"], [
            state["state_id"] for state in first[0]["states"]
        ])

    def test_rejects_unsafe_page_ids_before_contract_path_construction(self) -> None:
        pages = read_json(self.phase2 / "static-analysis" / "pages.json")
        pages["pages"][0]["page_id"] = "../escape"
        write_json(self.phase2 / "static-analysis" / "pages.json", pages)
        with self.assertRaisesRegex(ValueError, "unsafe Page-ID.*escape"):
            compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))
        build_fixture(self.phase2, self.phase3)
        contract = json.loads(json.dumps(compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))[0]))
        contract["page_id"] = "../escape"
        with self.assertRaisesRegex(ValueError, "unsafe Page-ID.*escape"):
            publish_page_contracts([contract], self.root / "published")

    def test_keeps_page_scoped_side_effects_on_their_declared_page(self) -> None:
        contracts = compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))
        history = next(contract for contract in contracts if contract["page_id"] == "PAGE-HISTORY")
        self.assertNotIn("SIDE-CLIPBOARD", {row["side_effect_id"] for row in history["side_effects"]})

    def test_rejects_runtime_observation_bound_to_another_state_evidence(self) -> None:
        observations = read_json(self.phase2 / "runtime-observations.json")
        error = next(row for row in observations["observations"] if row["state_id"] == "STATE-ERROR")
        error["after_evidence_id"] = "EVID-RESULT"
        write_json(self.phase2 / "runtime-observations.json", observations)
        with self.assertRaisesRegex(ValueError, "PAGE-CALCULATOR.*STATE-ERROR.*runtime"):
            compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))

    def test_rejects_inventory_state_missing_from_static_state_candidates(self) -> None:
        states = read_json(self.phase2 / "static-analysis" / "state-candidates.json")
        states["states"] = [row for row in states["states"] if row["state_id"] != "STATE-ERROR"]
        write_json(self.phase2 / "static-analysis" / "state-candidates.json", states)
        with self.assertRaisesRegex(ValueError, "PAGE-CALCULATOR.*STATE-ERROR.*static"):
            compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))

    def test_rejects_cross_page_event_component_and_transition_bindings(self) -> None:
        events = read_json(self.phase2 / "static-analysis" / "events.json")
        events["events"][0]["component_id"] = "COMP-HISTORY-LIST"
        write_json(self.phase2 / "static-analysis" / "events.json", events)
        with self.assertRaisesRegex(ValueError, "EVENT-OPEN-HISTORY.*component.*PAGE-HISTORY"):
            compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))
        build_fixture(self.phase2, self.phase3)
        transitions = read_json(self.phase2 / "static-analysis" / "transitions.json")
        transitions["transitions"][0]["source_page_id"] = "PAGE-HISTORY"
        transitions["transitions"][0]["target_page_id"] = "PAGE-CALCULATOR"
        write_json(self.phase2 / "static-analysis" / "transitions.json", transitions)
        with self.assertRaisesRegex(ValueError, "TRANS-CALC-HISTORY.*event.*PAGE-CALCULATOR"):
            compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))

    def test_rejects_incomplete_or_undeclared_contract_structure(self) -> None:
        contract = json.loads(json.dumps(compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))[0]))
        del contract["visible_text"]
        with self.assertRaisesRegex(ValueError, "visible_text"):
            publish_page_contracts([contract], self.root / "published")
        contract = json.loads(json.dumps(compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))[0]))
        contract["undeclared"] = "must not be silently accepted"
        with self.assertRaisesRegex(ValueError, "undeclared"):
            publish_page_contracts([contract], self.root / "published")
        schema = read_json(SCRIPTS.parent / "assets" / "page-acceptance-contract.schema.json")
        self.assertFalse(schema["additionalProperties"])

    def test_publish_rolls_back_the_whole_set_when_registry_replace_fails(self) -> None:
        destination = self.root / "published"
        original = compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))
        publish_page_contracts(original, destination)
        registry_before = (destination / "page-contract-registry.csv").read_bytes()
        contracts_before = {
            path.name: path.read_bytes() for path in (destination / "page-contracts").glob("*.json")
        }
        changed = json.loads(json.dumps(original))
        changed[0]["page_name"] = "Changed after initial publication"
        real_replace = contract_module.os.replace
        failed = False

        def fail_registry_replace(source: object, target: object) -> None:
            nonlocal failed
            if not failed and Path(target).name == "page-contract-registry.csv":
                failed = True
                raise OSError("injected registry publish failure")
            real_replace(source, target)

        with patch.object(contract_module.os, "replace", side_effect=fail_registry_replace):
            with self.assertRaisesRegex(OSError, "injected registry"):
                publish_page_contracts(changed, destination)
        self.assertEqual(registry_before, (destination / "page-contract-registry.csv").read_bytes())
        self.assertEqual(
            contracts_before,
            {path.name: path.read_bytes() for path in (destination / "page-contracts").glob("*.json")},
        )


def remove_android_evidence(phase2: Path, evidence_id: str) -> None:
    rows = read_csv(phase2 / "evidence-index.csv")
    write_csv(phase2 / "evidence-index.csv", [row for row in rows if row["evidence_id"] != evidence_id])


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def build_fixture(phase2: Path, phase3: Path) -> None:
    import shutil

    shutil.rmtree(phase2, ignore_errors=True)
    shutil.rmtree(phase3, ignore_errors=True)
    states = [
        ("INV-EMPTY", "PAGE-CALCULATOR", "Calculator", "STATE-EMPTY", "EVID-EMPTY"),
        ("INV-RESULT", "PAGE-CALCULATOR", "Calculator", "STATE-RESULT", "EVID-RESULT"),
        ("INV-ERROR", "PAGE-CALCULATOR", "Calculator", "STATE-ERROR", "EVID-ERROR"),
        ("INV-HISTORY", "PAGE-HISTORY", "History", "STATE-HISTORY", "EVID-HISTORY"),
    ]
    inventory = []
    evidence_rows = []
    observations = []
    for inventory_id, page_id, page_name, state_id, evidence_id in states:
        relative = f"evidence/ENV-001/{page_id}/{state_id}/{evidence_id}"
        inventory.append({
            "inventory_id": inventory_id, "feature_id": "FEATURE-CALC", "page_id": page_id,
            "page_name": page_name, "state_id": state_id, "state_name": state_id,
            "env_id": "ENV-001", "evidence_id": evidence_id, "entry_condition": "Open app",
            "action_summary": "Exercise state", "expected_observable": state_id,
            "business_rule_refs": "BR-CALC", "data_dependency_refs": "DATA-CALC",
            "system_capability_refs": "SYS-CALC", "asset_ids": "ASSET-ICON", "row_status": "REVIEWED",
        })
        evidence_rows.append({
            "evidence_id": evidence_id, "inventory_id": inventory_id, "feature_id": "FEATURE-CALC",
            "page_id": page_id, "state_id": state_id, "env_id": "ENV-001",
            "relative_path": relative, "status": "ACCEPTED",
        })
        evidence = phase2 / relative
        write_json(evidence / "metadata.json", {"evidence_id": evidence_id, "page_id": page_id, "state_id": state_id})
        (evidence / "screenshot.png").write_bytes(b"not-a-real-png-but-frozen-evidence")
        write_json(evidence / "layout.json", {"components": [{"component_id": "COMP-CALC-RESULT", "x": 1, "y": 2, "width": 3, "height": 4}]})
        observations.append({
            "observation_id": f"OBS-{state_id}", "subject_type": "PAGE", "subject_id": page_id,
            "page_id": page_id, "state_id": state_id, "env_id": "ENV-001", "after_evidence_id": evidence_id,
        })
    write_csv(phase2 / "inventory.csv", inventory)
    write_csv(phase2 / "evidence-index.csv", evidence_rows)
    write_csv(phase2 / "asset-inventory.csv", [{
        "asset_id": "ASSET-ICON", "feature_ids": "FEATURE-CALC", "page_ids": "PAGE-CALCULATOR",
        "state_ids": "STATE-EMPTY|STATE-RESULT|STATE-ERROR", "archive_path": "asset-package/files/ASSET-ICON/icon.svg",
        "sha256": "a" * 64,
    }])
    write_csv(phase2 / "catalogs" / "code-map.csv", [{
        "code_ref": "Calculator.kt:1", "feature_id": "FEATURE-CALC", "page_id": "PAGE-CALCULATOR", "status": "VERIFIED",
    }])
    write_csv(phase2 / "catalogs" / "business-rules.csv", [{
        "business_rule_id": "BR-CALC", "feature_id": "FEATURE-CALC", "page_id": "PAGE-CALCULATOR", "state_id": "STATE-EMPTY", "status": "VERIFIED",
    }])
    write_csv(phase2 / "catalogs" / "data-dependencies.csv", [{
        "data_dependency_id": "DATA-CALC", "feature_id": "FEATURE-CALC", "status": "VERIFIED",
    }])
    write_csv(phase2 / "catalogs" / "system-capabilities.csv", [{
        "system_capability_id": "SYS-CALC", "feature_id": "FEATURE-CALC", "status": "VERIFIED",
    }])
    write_json(phase2 / "static-analysis" / "pages.json", {"pages": [
        {"page_id": "PAGE-CALCULATOR", "page_name": "Calculator", "candidate_feature_ids": ["FEATURE-CALC"]},
        {"page_id": "PAGE-HISTORY", "page_name": "History", "candidate_feature_ids": ["FEATURE-CALC"]},
    ]})
    write_json(phase2 / "static-analysis" / "components.json", {"components": [
        {"component_id": "COMP-CALC-RESULT", "page_id": "PAGE-CALCULATOR", "resource_id": "result", "text": "0", "type": "Text"},
        {"component_id": "COMP-HISTORY-LIST", "page_id": "PAGE-HISTORY", "resource_id": "history", "text": "History", "type": "List"},
    ]})
    write_json(phase2 / "static-analysis" / "events.json", {"events": [
        {"event_id": "EVENT-OPEN-HISTORY", "page_id": "PAGE-CALCULATOR", "component_id": "COMP-CALC-RESULT"},
    ]})
    write_json(phase2 / "static-analysis" / "transitions.json", {"transitions": [
        {"transition_id": "TRANS-CALC-HISTORY", "source_page_id": "PAGE-CALCULATOR", "target_page_id": "PAGE-HISTORY", "event_id": "EVENT-OPEN-HISTORY"},
    ]})
    write_json(phase2 / "static-analysis" / "state-candidates.json", {"states": [
        {"state_id": state_id, "page_id": page_id} for _, page_id, _, state_id, _ in states
    ]})
    write_json(phase2 / "static-analysis" / "advanced-analysis.json", {"side_effects": [
        {"side_effect_id": "SIDE-CLIPBOARD", "feature_id": "FEATURE-CALC", "page_id": "PAGE-CALCULATOR", "state_id": "STATE-RESULT"},
    ]})
    write_json(phase2 / "runtime-observations.json", {"observations": observations})
    write_csv(phase3 / "module-registry.csv", [{"harmony_module_id": "MODULE-CALC", "status": "READY"}])
    write_csv(phase3 / "architecture-map.csv", [{
        "inventory_id": inventory_id, "feature_id": "FEATURE-CALC", "page_id": page_id,
        "state_id": state_id, "env_id": "ENV-001", "evidence_id": evidence_id,
        "mapping_type": "ROUTE_PAGE", "route_id": f"ROUTE-{page_id.removeprefix('PAGE-')}",
        "harmony_module_id": "MODULE-CALC", "mapping_status": "SHELL_CREATED_PENDING_IMPLEMENTATION",
    } for inventory_id, page_id, _, state_id, evidence_id in states])
    write_csv(phase3 / "route-registry.csv", [
        {"route_id": "ROUTE-CALCULATOR", "page_id": "PAGE-CALCULATOR", "harmony_module_id": "MODULE-CALC", "status": "READY"},
        {"route_id": "ROUTE-HISTORY", "page_id": "PAGE-HISTORY", "harmony_module_id": "MODULE-CALC", "status": "READY"},
    ])


if __name__ == "__main__":
    unittest.main()
