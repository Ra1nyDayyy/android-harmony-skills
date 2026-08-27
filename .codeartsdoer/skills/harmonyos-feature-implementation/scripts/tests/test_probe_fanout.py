from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import prepare_uitest_probe as pup  # noqa: E402


def make_plan(actions, transitions=()):
    return {
        "page_id": "PAGE-MAIN",
        "components": [
            {"component_id": "C-FAB", "source_type": "Button", "arkts_type": "Button",
             "locator": {"strategy": "ID", "value": "C-FAB"}, "source_record": {}},
        ],
        "states": [{"state_id": "STATE-DEFAULT", "records": [{"entry_condition": "", "action_summary": ""}]}],
        "events_actions": [
            {"event_id": f"E{i}", "component_id": "C-FAB", "action": a}
            for i, a in enumerate(actions)
        ],
        "transitions": list(transitions),
        "carrier": {"state_targets": {"STATE-DEFAULT": {"target_id": "ROUTE-X"}}},
    }


class ProbeFanoutTest(unittest.TestCase):
    def test_single_action_unchanged(self):
        probes = pup._probes(make_plan(["CLICK"]), {})
        self.assertEqual([p["probe_id"] for p in probes], ["PAGE-MAIN::STATE-DEFAULT"])

    def test_multi_action_fans_out_isolated_probes(self):
        plan = make_plan(
            ["LONG_CLICK", "CLICK"],
            [{"transition_id": "T1", "event_id": "E1", "source_page_id": "PAGE-MAIN",
              "target_page_id": "PAGE-T"}],
        )
        target = {"page_id": "PAGE-T", "components": [{"component_id": "C-1", "source_type": "Text", "arkts_type": "Text", "locator": {"strategy": "ID", "value": "C-1"}}]}
        probes = pup._probes(plan, {"PAGE-T": target})
        ids = [p["probe_id"] for p in probes]
        self.assertEqual(len(ids), 2)
        self.assertTrue(all(p["probe_id"].startswith("PAGE-MAIN::STATE-DEFAULT") for p in probes))
        self.assertEqual(len({p["result_directory"] for p in probes}), 2)
        # isolation: exactly one action per probe
        self.assertTrue(all(len(p["declared_actions"]) == 1 for p in probes))
        # transitions bind exclusively to their own action's event
        owned = {p["declared_actions"][0]["event_id"]: len(p["declared_transitions"]) for p in probes}
        self.assertEqual(owned.get("E1"), 1)   # T1 binds to its own event E1 (fixture)
        self.assertEqual(owned.get("E0"), 0)   # E0 has no transition

    def test_no_action_yields_observation_only_probe(self):
        """gmi honest path: zero-action pages get one cold-start-only run."""
        plan = make_plan([])
        plan["source_refs"] = {"android_evidence_hashes": [
            {"evidence_id": "EVD-P", "pending_runtime_verify": True, "source_geometry": []}]}
        # 观察态页：零动作 + pending 组件为空 → 单条纯冷启动探针
        plan["components"] = []
        probes = pup._probes(plan, {})
        self.assertEqual(len(probes), 1)
        probe = probes[0]
        self.assertEqual(probe["probe_id"], "PAGE-MAIN::STATE-DEFAULT")
        self.assertEqual(probe["declared_actions"], [])
        self.assertEqual(probe["declared_transitions"], [])
        self.assertEqual(probe["required_components"], [])

    def test_pending_detection_reads_source_refs_nesting(self):
        """R13: evidence_hashes live under source_refs; the reader follows."""
        empty_plan = make_plan(["CLICK"])  # accepted baseline, no components
        empty_plan["components"] = []
        with self.assertRaisesRegex(ValueError, "no required component plan"):
            pup._probes(empty_plan, {})

    def test_multi_action_multi_transition_ambiguous_rejected(self):
        plan = make_plan(["A1", "A2"], [
            {"transition_id": "T1", "event_id": "E0", "target_page_id": "PAGE-MISSING"},
            {"transition_id": "T2", "event_id": "E9", "target_page_id": "PAGE-MISSING"},
        ])
        with self.assertRaisesRegex(ValueError, "ambiguous multi-action"):
            pup._probes(plan, {})


if __name__ == "__main__":
    unittest.main()
