#!/usr/bin/env python3
"""Tests proving that model-authored PASS text cannot bypass atomic page coverage."""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))

from _common import manifest_lines, sha256_file  # noqa: E402
from evaluate_page_gates import evaluate_page_gates  # noqa: E402
from evaluate_advanced_gates import evaluate_advanced_gates  # noqa: E402


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class DeterministicPageGateTest(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, list[dict]]:
        workspace = root / "phase-02-android-inventory"
        static = workspace / "static-analysis"
        static.mkdir(parents=True)
        write_json(workspace / "environments.json", {
            "environments": [{"env_id": "ENV-001"}], "baseline_env_id": "ENV-001",
        })
        write_csv(workspace / "coverage-ledger.csv", ["feature_id", "applicable_env_ids"], [{
            "feature_id": "FEATURE-ONE", "applicable_env_ids": '["ENV-001"]',
        }])
        write_json(static / "project-index.json", {"schema_version": 1})
        write_json(static / "pages.json", {"schema_version": 1, "pages": [
            {"page_id": "PAGE-MAIN", "symbol": "MainActivity", "candidate_feature_ids": ["FEATURE-ONE"]},
            {"page_id": "PAGE-TINY", "symbol": "TinyDialog", "candidate_feature_ids": ["FEATURE-ONE"]},
        ]})
        write_json(static / "components.json", {"schema_version": 1, "components": [{
            "component_id": "COMP-TINY-BUTTON", "page_id": "PAGE-TINY",
            "resource_id": "tiny_button", "text": "确定", "type": "Button", "attributes": {},
        }]})
        write_json(static / "events.json", {"schema_version": 1, "events": [{
            "event_id": "EVENT-OPEN-TINY", "page_id": "PAGE-MAIN",
        }]})
        write_json(static / "transitions.json", {"schema_version": 1, "transitions": [{
            "transition_id": "TRANSITION-MAIN-TINY", "source_page_id": "PAGE-MAIN",
            "target_page_id": "PAGE-TINY",
        }]})
        write_json(static / "state-candidates.json", {"schema_version": 1, "states": [{
            "state_id": "STATE-MAIN-DEFAULT", "page_id": "PAGE-MAIN",
        }]})
        write_json(static / "runtime-tasks.json", {"schema_version": 1, "tasks": []})
        write_json(static / "advanced-analysis.json", {
            "schema_version": 1, "dynamic_risks": [], "side_effects": [], "scenarios": [],
        })
        (static / "code-map.candidates.csv").write_text("code_ref\n", encoding="utf-8")
        names = sorted([
            "project-index.json", "pages.json", "components.json", "events.json", "transitions.json",
            "state-candidates.json", "runtime-tasks.json", "advanced-analysis.json",
            "code-map.candidates.csv",
        ])
        (static / "manifest.sha256").write_text(manifest_lines(static, names), encoding="utf-8")
        (static / "COMMITTED").write_text(sha256_file(static / "manifest.sha256") + "\n", encoding="utf-8")

        evidence_rows = []
        inventory_rows = []
        for evidence_id, page_id, predecessor, layout, screen in (
            ("EVD-MAIN", "PAGE-MAIN", "", [{"resourceId": "main"}], b"main"),
            ("EVD-TINY", "PAGE-TINY", "EVD-MAIN", [{"resourceId": "tiny_button", "text": "确定"}], b"tiny"),
        ):
            relative = f"evidence/ENV-001/{page_id}/STATE-DEFAULT/{evidence_id}"
            directory = workspace / relative
            directory.mkdir(parents=True)
            write_json(directory / "layout.json", layout)
            (directory / "screenshot.png").write_bytes(screen)
            write_json(directory / "metadata.json", {
                "evidence_id": evidence_id, "page_id": page_id, "env_id": "ENV-001",
                "predecessor_evidence_id": predecessor, "status": "SEALED",
            })
            evidence_rows.append({
                "evidence_id": evidence_id, "relative_path": relative,
                "metadata_sha256": sha256_file(directory / "metadata.json"), "status": "SEALED",
            })
            inventory_rows.append({
                "evidence_id": evidence_id, "row_status": "CAPTURED",
            })
        write_csv(
            workspace / "evidence-index.csv",
            ["evidence_id", "relative_path", "metadata_sha256", "status"], evidence_rows,
        )
        write_csv(workspace / "inventory.csv", ["evidence_id", "row_status"], inventory_rows)

        observations = [
            {"observation_id": "OBS-PAGE-MAIN", "subject_type": "PAGE", "subject_id": "PAGE-MAIN",
             "page_id": "PAGE-MAIN", "env_id": "ENV-001", "before_evidence_id": "",
             "after_evidence_id": "EVD-MAIN", "locator_field": "", "locator_value": "", "locator_occurrence": 0},
            {"observation_id": "OBS-PAGE-TINY", "subject_type": "PAGE", "subject_id": "PAGE-TINY",
             "page_id": "PAGE-TINY", "env_id": "ENV-001", "before_evidence_id": "",
             "after_evidence_id": "EVD-TINY", "locator_field": "", "locator_value": "", "locator_occurrence": 0},
            {"observation_id": "OBS-COMP-TINY", "subject_type": "COMPONENT", "subject_id": "COMP-TINY-BUTTON",
             "page_id": "PAGE-TINY", "env_id": "ENV-001", "before_evidence_id": "",
             "after_evidence_id": "EVD-TINY", "locator_field": "", "locator_value": "", "locator_occurrence": 0},
            {"observation_id": "OBS-EVENT", "subject_type": "EVENT", "subject_id": "EVENT-OPEN-TINY",
             "page_id": "PAGE-MAIN", "env_id": "ENV-001", "before_evidence_id": "EVD-MAIN",
             "after_evidence_id": "EVD-TINY", "locator_field": "", "locator_value": "", "locator_occurrence": 0},
            {"observation_id": "OBS-TRANSITION", "subject_type": "TRANSITION", "subject_id": "TRANSITION-MAIN-TINY",
             "page_id": "PAGE-MAIN", "env_id": "ENV-001", "before_evidence_id": "EVD-MAIN",
             "after_evidence_id": "EVD-TINY", "locator_field": "", "locator_value": "", "locator_occurrence": 0},
            {"observation_id": "OBS-STATE", "subject_type": "STATE", "subject_id": "STATE-MAIN-DEFAULT",
             "page_id": "PAGE-MAIN", "env_id": "ENV-001", "before_evidence_id": "",
             "after_evidence_id": "EVD-MAIN", "locator_field": "", "locator_value": "", "locator_occurrence": 0},
        ]
        return workspace, observations

    def test_missing_tiny_page_cannot_be_hidden_by_claimed_pass(self) -> None:
        with tempfile.TemporaryDirectory(prefix="page-gate-") as temp_name:
            workspace, observations = self.fixture(Path(temp_name))
            observations = [row for row in observations if row["page_id"] != "PAGE-TINY"]
            write_json(workspace / "runtime-observations.json", {
                "schema_version": 1, "claimed_decision": "PASS", "observations": observations,
            })
            report = evaluate_page_gates(workspace)
            self.assertEqual(report["machine_verdict"], "BLOCKED")
            self.assertTrue(any("PAGE-TINY" in error for error in report["errors"]))

    def test_all_atomic_evidence_passes_without_model_verdict(self) -> None:
        with tempfile.TemporaryDirectory(prefix="page-gate-") as temp_name:
            workspace, observations = self.fixture(Path(temp_name))
            write_json(workspace / "runtime-observations.json", {
                "schema_version": 1, "observations": observations,
            })
            report = evaluate_page_gates(workspace)
            self.assertEqual(report["machine_verdict"], "PASS", report["errors"])
            self.assertEqual({row["machine_verdict"] for row in report["pages"]}, {"PAGE_PASS"})

    def test_observation_cannot_contain_pass_field(self) -> None:
        with tempfile.TemporaryDirectory(prefix="page-gate-") as temp_name:
            workspace, observations = self.fixture(Path(temp_name))
            observations[0]["decision"] = "PASS"
            write_json(workspace / "runtime-observations.json", {
                "schema_version": 1, "observations": observations,
            })
            report = evaluate_page_gates(workspace)
            self.assertEqual(report["machine_verdict"], "BLOCKED")
            self.assertTrue(any("forbidden fields" in error for error in report["errors"]))

    def test_model_pass_cannot_hide_missing_side_effect_probe(self) -> None:
        with tempfile.TemporaryDirectory(prefix="advanced-gate-") as temp_name:
            workspace, _ = self.fixture(Path(temp_name))
            static = workspace / "static-analysis"
            write_json(static / "advanced-analysis.json", {
                "schema_version": 1,
                "dynamic_risks": [],
                "side_effects": [{
                    "candidate_id": "EFFECT-DATABASE-ONE", "effect_type": "DATABASE",
                    "page_id": "PAGE-MAIN", "candidate_feature_ids": ["FEATURE-ONE"],
                }],
                "scenarios": [],
            })
            names = sorted([
                "project-index.json", "pages.json", "components.json", "events.json",
                "transitions.json", "state-candidates.json", "runtime-tasks.json",
                "advanced-analysis.json", "code-map.candidates.csv",
            ])
            (static / "manifest.sha256").write_text(manifest_lines(static, names), encoding="utf-8")
            (static / "COMMITTED").write_text(
                sha256_file(static / "manifest.sha256") + "\n", encoding="utf-8"
            )
            write_json(workspace / "advanced-observations.json", {
                "schema_version": 1, "claimed_decision": "PASS", "observations": [],
            })
            write_csv(workspace / "probe-evidence-index.csv", [
                "probe_evidence_id", "candidate_id", "page_id", "env_id",
                "relative_path", "metadata_sha256", "status",
            ], [])
            report = evaluate_advanced_gates(workspace)
            self.assertEqual(report["machine_verdict"], "BLOCKED")
            self.assertEqual(report["required_observations"], 1)
            self.assertTrue(any("forbidden verdict" in error for error in report["errors"]))
            self.assertTrue(any("Missing advanced observation" in error for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()
