#!/usr/bin/env python3
"""Contract tests for page-owned and capability-owned Phase 4 work orders."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))

import stage4_work_orders as orders_module  # noqa: E402
from page_acceptance_contract import canonical_contract_sha256  # noqa: E402
from stage4_work_orders import (  # noqa: E402
    issue_capability_order,
    issue_page_order,
    validate_order_coverage,
)


CAPABILITY_PATHS = (
    "entry/src/main/ets/capabilities/CalcInterface.ets",
    "entry/src/main/ets/capabilities/Calc.ets",
    "entry/src/ohosTest/ets/test/Calc.test.ets",
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Stage4WorkOrdersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="stage4-orders-")
        self.ws = Path(self.temp.name) / "phase-04-harmony-implementation"
        self.ws.mkdir(parents=True)
        (self.ws / "harmony-project").mkdir()
        self._add_page("PAGE-A", ("STATE-A0", "STATE-A1"), ("CAP-CALC",))
        self._add_page("PAGE-B", ("STATE-B0",), ("CAP-CALC",))
        write_csv(
            self.ws / "page-contract-registry.csv",
            ["page_id", "page_name", "relative_path", "contract_sha256", "state_count", "feature_ids", "required_h4env_ids", "status"],
            [self._registry_row("PAGE-A"), self._registry_row("PAGE-B")],
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _contract_path(self, page_id: str) -> Path:
        return self.ws / "page-contracts" / f"{page_id}.json"

    def _add_page(self, page_id: str, states: tuple[str, ...], capabilities: tuple[str, ...]) -> None:
        contract = {
            "schema_version": "page-acceptance-contract-v1",
            "page_id": page_id,
            "page_name": page_id.removeprefix("PAGE-"),
            "carrier_type": "PAGE",
            "feature_ids": ["FEATURE-CALC"],
            "states": [
                {
                    "state_id": state_id,
                    "state_name": state_id,
                    "records": [{"state_id": state_id, "entry_condition": "launch", "action_summary": "observe"}],
                }
                for state_id in states
            ],
            "components": [
                {
                    "component_id": f"COMP-{page_id}",
                    "page_id": page_id,
                    "type": "Button",
                    "text": "=",
                    "state_ids": ",".join(states),
                }
            ],
            "source_geometry": [{"component_id": f"COMP-{page_id}", "bounds": "0,0,10,10"}],
            "visible_text": [{"component_id": f"COMP-{page_id}", "text": "="}],
            "assets": [{"asset_id": f"ASSET-{page_id}", "component_id": f"COMP-{page_id}"}],
            "entry_conditions": [{"state_id": state_id, "condition": "launch"} for state_id in states],
            "interaction_bindings": [],
            "transitions": [],
            "business_rules": [{"business_rule_id": "BR-CALC", "page_id": page_id}],
            "data_dependencies": [],
            "side_effects": [{"side_effect_id": "SIDE-CLIPBOARD", "page_id": page_id}] if page_id == "PAGE-A" else [],
            "system_capabilities": [
                {"system_capability_id": capability_id, "feature_id": "FEATURE-CALC"}
                for capability_id in capabilities
            ],
            "phase3_targets": [{"state_id": state_id, "harmony_module_id": "MODULE-CALC", "target_kind": "ROUTE_PAGE", "target_id": f"ROUTE-{page_id}"} for state_id in states],
            "required_h4env_ids": ["H4ENV-001"],
            "comparison_policy": {"geometry_tolerance": "max(2dp, 0.5%)", "application_region_ssim": 0.98, "changed_pixel_ratio": 0.02, "required_element_masks": "STRICTER_REQUIRED_ELEMENT_MASKS"},
            "android_evidence_hashes": [{"evidence_id": f"EVID-{page_id}", "screenshot_sha256": "a" * 64}],
        }
        write_json(self._contract_path(page_id), contract)

    def _registry_row(self, page_id: str) -> dict[str, str]:
        contract = json.loads(self._contract_path(page_id).read_text(encoding="utf-8"))
        return {
            "page_id": page_id,
            "page_name": str(contract["page_name"]),
            "relative_path": f"page-contracts/{page_id}.json",
            "contract_sha256": canonical_contract_sha256(contract),
            "state_count": str(len(contract["states"])),
            "feature_ids": "FEATURE-CALC",
            "required_h4env_ids": "H4ENV-001",
            "status": "LOCKED",
        }

    def test_each_page_requires_distinct_owner_and_codearts_task(self) -> None:
        issue_page_order(self.ws, "PAGE-A", "owner-a", "TASK-100", ("entry/src/main/ets/pages/A.ets",))
        with self.assertRaisesRegex(ValueError, "owner.*already bound"):
            issue_page_order(self.ws, "PAGE-B", "owner-a", "TASK-101", ("entry/src/main/ets/pages/B.ets",))
        with self.assertRaisesRegex(ValueError, "task.*already bound"):
            issue_page_order(self.ws, "PAGE-B", "owner-b", "TASK-100", ("entry/src/main/ets/pages/B.ets",))

    def test_page_and_capability_code_paths_cannot_overlap(self) -> None:
        issue_page_order(self.ws, "PAGE-A", "owner-a", "TASK-100", ("entry/src/main/ets/pages/A.ets",))
        with self.assertRaisesRegex(ValueError, "exclusive code path"):
            issue_capability_order(
                self.ws, "CAP-CALC", "cap-owner", "TASK-200", ("PAGE-A", "PAGE-B"),
                CAPABILITY_PATHS + ("entry/src/main/ets/pages/A.ets",),
            )

    def test_parent_and_child_code_paths_also_overlap(self) -> None:
        issue_page_order(self.ws, "PAGE-A", "owner-a", "TASK-100", ("entry/src/main/ets/pages",))
        with self.assertRaisesRegex(ValueError, "exclusive code path"):
            issue_page_order(self.ws, "PAGE-B", "owner-b", "TASK-101", ("entry/src/main/ets/pages/B.ets",))

    def test_code_path_ownership_is_case_insensitive(self) -> None:
        issue_page_order(self.ws, "PAGE-A", "owner-a", "TASK-100", ("entry/src/main/ets/pages/A.ets",))
        with self.assertRaisesRegex(ValueError, "exclusive code path"):
            issue_page_order(self.ws, "PAGE-B", "owner-b", "TASK-101", ("ENTRY/SRC/MAIN/ETS/PAGES/a.ets",))

    def test_page_and_capability_owners_cannot_be_the_same_actor(self) -> None:
        issue_capability_order(
            self.ws, "CAP-CALC", "shared-owner", "TASK-200", ("PAGE-A", "PAGE-B"),
            CAPABILITY_PATHS,
        )
        with self.assertRaisesRegex(ValueError, "owner.*already bound"):
            issue_page_order(self.ws, "PAGE-A", "shared-owner", "TASK-100", ("entry/src/main/ets/pages/A.ets",))

    def test_page_order_is_contract_bound_and_contains_every_state_and_check(self) -> None:
        path = issue_page_order(
            self.ws, "PAGE-A", "owner-a", "TASK-100", ("entry/src/main/ets/pages/A.ets",),
            ui_understanding_agent_id="ui-agent-a",
        )
        order = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(4, order["phase"])
        self.assertEqual(["STATE-A0", "STATE-A1"], order["state_ids"])
        self.assertEqual(["CAP-CALC"], order["capability_dependencies"])
        self.assertEqual(
            canonical_contract_sha256(json.loads(self._contract_path("PAGE-A").read_text(encoding="utf-8"))),
            order["page_contract_sha256"],
        )
        self.assertEqual("ui-agent-a", order["ui_understanding_agent_id"])
        self.assertEqual("SEPARATE", order["ui_understanding_role_mode"])
        plan_path = self.ws / order["arkts_page_plan_path"]
        self.assertTrue(plan_path.is_file())
        self.assertEqual(sha256(plan_path), order["arkts_page_plan_sha256"])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual("FROZEN_CONTRACT_ONLY_NO_FREE_INFERENCE", plan["ui_understanding_policy"])
        self.assertEqual(order["page_contract_sha256"], plan["source_contract_sha256"])
        self.assertEqual(
            ["BEHAVIOR", "COMPONENT_TREE", "GEOMETRY", "NAVIGATION", "SCREENSHOT", "SIDE_EFFECT"],
            order["required_parity_checks"],
        )
        self.assertIn("validate_stage4.py", order["completion_command"])
        with (self.ws / "page-work-order-registry.csv").open(encoding="utf-8", newline="") as stream:
            registry = list(csv.DictReader(stream))
        self.assertEqual("TASK-100", registry[0]["codearts_task_id"])
        self.assertEqual("ui-agent-a", registry[0]["ui_understanding_agent_id"])

    def test_ui_understanding_agent_is_page_scoped_and_may_be_combined_with_owner(self) -> None:
        first = issue_page_order(
            self.ws, "PAGE-A", "owner-a", "TASK-100", ("entry/src/main/ets/pages/A.ets",),
            ui_understanding_agent_id="ui-agent-a",
        )
        self.assertEqual("SEPARATE", json.loads(first.read_text(encoding="utf-8"))["ui_understanding_role_mode"])
        with self.assertRaisesRegex(ValueError, "UI understanding agent.*another page"):
            issue_page_order(
                self.ws, "PAGE-B", "owner-b", "TASK-101", ("entry/src/main/ets/pages/B.ets",),
                ui_understanding_agent_id="ui-agent-a",
            )

        combined = issue_page_order(
            self.ws, "PAGE-B", "owner-b", "TASK-101", ("entry/src/main/ets/pages/B.ets",),
            ui_understanding_agent_id="owner-b",
        )
        self.assertEqual("PAGE_OWNER_COMBINED", json.loads(combined.read_text(encoding="utf-8"))["ui_understanding_role_mode"])

    def test_capability_order_requires_exact_consumers_and_embeds_contracts(self) -> None:
        with self.assertRaisesRegex(ValueError, "consumer pages differ"):
            issue_capability_order(
                self.ws, "CAP-CALC", "cap-owner", "TASK-200", ("PAGE-A",),
                CAPABILITY_PATHS,
            )
        path = issue_capability_order(
            self.ws, "CAP-CALC", "cap-owner", "TASK-200", ("PAGE-B", "PAGE-A"),
            CAPABILITY_PATHS,
        )
        order = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(4, order["phase"])
        self.assertEqual(["PAGE-A", "PAGE-B"], order["consumer_page_ids"])
        self.assertEqual(2, len(order["behavior_contracts"]))
        self.assertEqual(["SIDE-CLIPBOARD"], [row["side_effect_id"] for row in order["side_effect_contracts"]])

    def test_codearts_task_id_is_unique_across_page_and_capability_orders(self) -> None:
        issue_page_order(self.ws, "PAGE-A", "owner-a", "TASK-100", ("entry/src/main/ets/pages/A.ets",))
        with self.assertRaisesRegex(ValueError, "task.*already bound"):
            issue_capability_order(
                self.ws, "CAP-CALC", "cap-owner", "TASK-100", ("PAGE-A", "PAGE-B"),
                CAPABILITY_PATHS,
            )

    def test_rejects_placeholder_codearts_task_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "real CodeArts task ID"):
            issue_page_order(self.ws, "PAGE-A", "owner-a", "__FILL_TASK_ID__", ("entry/src/main/ets/pages/A.ets",))

    def test_coverage_gate_requires_every_page_and_shared_capability_order(self) -> None:
        issue_page_order(self.ws, "PAGE-A", "owner-a", "TASK-100", ("entry/src/main/ets/pages/A.ets",))
        with self.assertRaisesRegex(ValueError, "missing page orders.*PAGE-B"):
            validate_order_coverage(self.ws)
        issue_page_order(self.ws, "PAGE-B", "owner-b", "TASK-101", ("entry/src/main/ets/pages/B.ets",))
        with self.assertRaisesRegex(ValueError, "missing capability orders.*CAP-CALC"):
            validate_order_coverage(self.ws)
        issue_capability_order(
            self.ws, "CAP-CALC", "cap-owner", "TASK-200", ("PAGE-A", "PAGE-B"),
            CAPABILITY_PATHS,
        )
        self.assertEqual({"pages": 2, "capabilities": 1}, validate_order_coverage(self.ws))

    def test_page_issuance_rolls_back_order_and_registry_when_ledger_write_fails(self) -> None:
        with self.assertRaises(ValueError):
            validate_order_coverage(self.ws)
        real_write_csv = orders_module.write_csv

        def fail_ledger(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
            if Path(path).name == "page-implementation-ledger.csv":
                raise OSError("injected ledger write failure")
            real_write_csv(path, fields, rows)

        with patch.object(orders_module, "write_csv", side_effect=fail_ledger):
            with self.assertRaisesRegex(OSError, "injected ledger"):
                issue_page_order(self.ws, "PAGE-A", "owner-a", "TASK-100", ("entry/src/main/ets/pages/A.ets",))
        with (self.ws / "page-work-order-registry.csv").open(encoding="utf-8", newline="") as stream:
            self.assertEqual([], list(csv.DictReader(stream)))
        self.assertEqual([], list((self.ws / "page-work-orders").glob("*.json")))

    def test_registered_page_order_revalidates_complete_frozen_schema(self) -> None:
        path = issue_page_order(self.ws, "PAGE-A", "owner-a", "TASK-100", ("entry/src/main/ets/pages/A.ets",))
        path.chmod(0o644)
        order = json.loads(path.read_text(encoding="utf-8"))
        order["state_ids"] = ["STATE-A0"]
        write_json(path, order)
        registry_path = self.ws / "page-work-order-registry.csv"
        with registry_path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
            fields = list(rows[0])
        rows[0]["work_order_sha256"] = sha256(path)
        write_csv(registry_path, fields, rows)
        with self.assertRaisesRegex(ValueError, "page work-order schema"):
            issue_page_order(self.ws, "PAGE-B", "owner-b", "TASK-101", ("entry/src/main/ets/pages/B.ets",))

    def test_registered_order_path_must_be_canonical_and_contained(self) -> None:
        issue_page_order(self.ws, "PAGE-A", "owner-a", "TASK-100", ("entry/src/main/ets/pages/A.ets",))
        registry_path = self.ws / "page-work-order-registry.csv"
        with registry_path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
            fields = list(rows[0])
        rows[0]["relative_path"] = "page-work-orders/../page-work-orders/" + rows[0]["work_order_id"] + ".json"
        write_csv(registry_path, fields, rows)
        with self.assertRaisesRegex(ValueError, "canonical"):
            issue_page_order(self.ws, "PAGE-B", "owner-b", "TASK-101", ("entry/src/main/ets/pages/B.ets",))

    def test_capability_order_requires_interface_implementation_and_test_paths(self) -> None:
        with self.assertRaisesRegex(ValueError, "interface.*implementation.*test"):
            issue_capability_order(
                self.ws, "CAP-CALC", "cap-owner", "TASK-200", ("PAGE-A", "PAGE-B"),
                ("entry/src/main/ets/capabilities/Calc.ets",),
            )

    def test_legacy_feature_order_cli_fails_closed(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "issue_feature_work_order.py"), "--workspace", str(self.ws), "--feature-id", "FEATURE-CALC"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("page and capability orders", result.stderr)


if __name__ == "__main__":
    unittest.main()
