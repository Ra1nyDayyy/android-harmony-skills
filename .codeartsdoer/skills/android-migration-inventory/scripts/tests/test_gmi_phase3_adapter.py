from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import gmi_phase3_adapter as adapter  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]] = []) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class GmiPhase3AdapterTest(unittest.TestCase):
    def test_preserves_controller_and_rebinds_or_synthesizes_components(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gmi-adapter-") as temp_name:
            root = Path(temp_name)
            workspace = root / "gmi"
            out = root / "run"
            candidates = workspace / "candidates"
            # 新版 adapter 契约：closure 必须处于 READY_FOR_HUMAN_REVIEW，且必须
            # 存在绑定 closure 哈希的人工接受记录（decision=ACCEPTED）才肯运行。
            closure_path = workspace / "phase-2-closure.json"
            write_json(closure_path, {
                "machine_status": "READY_FOR_HUMAN_REVIEW",
                "gate": {"unmapped": 0, "audit_discrepancy": 0},
            })
            write_json(workspace / "human-review" / "phase-2-acceptance.json", {
                "decision": "ACCEPTED",
                "reviewer_id": "adapter-tester",
                "accepted_at": "2026-08-28T00:00:00Z",
                "closure_sha256": hashlib.sha256(closure_path.read_bytes()).hexdigest(),
            })
            (candidates / "manifest.sha256").parent.mkdir(parents=True, exist_ok=True)
            (candidates / "manifest.sha256").write_text("fixture", encoding="utf-8")
            completeness = [
                {"page_id": "PAGE-ALPHA", "page_symbol": "AlphaActivity", "check_category": "UI", "check_key": "alpha", "status": "FOUND", "hint": ""},
                {"page_id": "PAGE-BETA", "page_symbol": "BetaActivity", "check_category": "UI", "check_key": "beta", "status": "FOUND", "hint": ""},
            ]
            write_csv(candidates / "phase-2-completeness.csv", list(completeness[0]), completeness)
            inventory = [
                {"candidate_id": "INV-A", "feature_id": "MAIN", "page_id": "PAGE-ALPHA", "state_id": "DEFAULT", "state_expression": "DEFAULT", "env_id": "ENV-001", "entry_condition": "launch", "expected_observable": "alpha", "source_ref": "AlphaActivity:1"},
                {"candidate_id": "INV-B", "feature_id": "MAIN", "page_id": "PAGE-BETA", "state_id": "DEFAULT", "state_expression": "DEFAULT", "env_id": "ENV-001", "entry_condition": "navigate", "expected_observable": "beta", "source_ref": "BetaActivity:1"},
            ]
            write_csv(candidates / "inventory.candidates.csv", list(inventory[0]), inventory)
            fields = [{"page_id": "PAGE-BETA", "page_symbol": "BetaActivity", "order_index": "1", "field_id": "title", "field_type": "TextView", "field_label": "Beta", "icon_resource": "", "layout_ref": "activity_beta", "source_ref": "activity_beta.xml:1"}]
            write_csv(candidates / "page-fields.candidates.csv", list(fields[0]), fields)
            write_csv(candidates / "behavior.candidates.csv", ["candidate_id", "page_id", "page_symbol", "event", "action", "params", "data_target", "side_effect", "source_ref"])
            write_csv(candidates / "navigation-relations.candidates.csv", ["candidate_id", "from_page_id", "from_page_symbol", "trigger", "action", "to_page_id", "relation_type", "source_ref"])
            for name, header in {
                "asset-mapping.candidates.csv": ["candidate_id", "type", "resource_id", "resolved_value"],
                "risk-probes.candidates.csv": ["candidate_id", "probe_id", "category", "severity", "signal", "page_id"],
                "code-map.candidates.full.csv": ["candidate_id", "code_ref", "file_path", "line", "symbol", "suggested_page"],
                "business-rules.candidates.csv": ["candidate_id", "condition", "outcome_hint", "source_ref"],
                "third-party-dependencies.candidates.csv": ["candidate_id", "group", "artifact", "version", "resolution"],
            }.items():
                write_csv(candidates / name, header)
            write_csv(workspace / "runtime-evidence" / "runtime-gate.csv", ["page_id", "symbol", "status", "evidence"])
            write_csv(workspace / "runtime-evidence" / "audit-replay.csv", ["page_id", "symbol", "replayed", "recorded", "discrepancy", "note"])
            write_csv(workspace / "coverage" / "coverage-ledger.csv", ["file", "category", "disposition", "status", "covering_candidates"])

            scope = {"run_id": "RUN-REAL", "migration_scope": {"included_features": ["MAIN"]}}
            write_json(out / "controller" / "scope.json", scope)
            write_json(out / "run-manifest.json", {"run_id": "RUN-REAL"})
            static = out / "phase-02-android-inventory" / "static-analysis"
            write_json(static / "pages.json", {"pages": [{"page_id": "PAGE-OLD-ALPHA", "symbol": "AlphaActivity"}]})
            write_json(static / "components.json", {"components": [{"component_id": "COMP-REAL", "page_id": "PAGE-OLD-ALPHA", "type": "TextView", "text": "Alpha"}]})
            write_json(static / "events.json", {"events": []})
            write_json(static / "transitions.json", {"transitions": []})

            with patch.object(sys, "argv", ["gmi_phase3_adapter.py", "--workspace", str(workspace), "--out", str(out)]):
                self.assertEqual(0, adapter.main())

            self.assertEqual(scope, json.loads((out / "controller" / "scope.json").read_text(encoding="utf-8")))
            rows = json.loads((static / "components.json").read_text(encoding="utf-8"))["components"]
            by_page = {row["page_id"]: row for row in rows}
            self.assertEqual("COMP-REAL", by_page["PAGE-ALPHA"]["component_id"])
            self.assertEqual("Beta", by_page["PAGE-BETA"]["text"])
            self.assertTrue((out / "runtime-evidence" / "runtime-gate.csv").is_file())


if __name__ == "__main__":
    unittest.main()
