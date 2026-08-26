from __future__ import annotations

import csv
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import validate_gate  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_csv(path: Path, fields: list[str], rows: list[dict[str, str]] = []) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def seal(report: Path, closed: Path) -> None:
    closed.write_text(hashlib.sha256(report.read_bytes()).hexdigest(), encoding="utf-8")


class GmiGateTest(unittest.TestCase):
    def test_phase2_accepts_adapter_root_layout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gmi-root-gate-") as temp_name:
            run = Path(temp_name)
            p2 = run / "phase-02-android-inventory"
            write_json(p2 / "closure-report.json", {
                "generated_by": "gmi-phase3-adapter",
                "gmi_closure": {"unmapped": 0, "audit_discrepancy": 0},
            })
            for name in validate_gate.GMI_REQUIRED_CANDIDATES:
                write_csv(run / "candidates" / name, ["id"])
            (run / "candidates" / "manifest.sha256").write_text("fixture", encoding="utf-8")
            write_csv(run / "coverage" / "coverage-ledger.csv", ["status", "disposition"])
            write_csv(run / "runtime-evidence" / "runtime-gate.csv", ["status"])
            write_csv(run / "runtime-evidence" / "audit-replay.csv", ["discrepancy"])

            errors, _ = validate_gate._validate_gmi_equivalent(run, 2)
            self.assertEqual([], errors)

    def test_phase4_rejects_forged_pass_without_real_implementation_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gmi-gate-") as temp_name:
            run = Path(temp_name)
            p2 = run / "phase-02-android-inventory"
            gmi = p2 / "gmi"
            write_json(gmi / "phase-2-closure.json", {"gate": {"unmapped": 0, "audit_discrepancy": 0}})
            for name in validate_gate.GMI_REQUIRED_CANDIDATES:
                rows = [{"page_id": "PAGE-ONE", "event": "click", "action": "open"}] if name == "behavior.candidates.csv" else []
                write_csv(gmi / "candidates" / name, list(rows[0]) if rows else ["id"], rows)
            (gmi / "candidates" / "manifest.sha256").write_text("fixture", encoding="utf-8")
            write_csv(gmi / "coverage" / "coverage-ledger.csv", ["file", "category", "disposition", "status"], [{"file": "Main.kt", "category": "code", "disposition": "MAPPED", "status": "COVERED"}])
            write_csv(gmi / "runtime-evidence" / "runtime-gate.csv", ["page_id", "symbol", "status", "evidence"])
            write_csv(gmi / "runtime-evidence" / "audit-replay.csv", ["page_id", "symbol", "replayed", "recorded", "discrepancy", "note"])
            write_csv(p2 / "inventory.csv", ["inventory_id", "page_id"], [{"inventory_id": "INV-ONE", "page_id": "PAGE-ONE"}])
            write_csv(p2 / "evidence-index.csv", ["evidence_id", "status"], [{"evidence_id": "EVD-ONE", "status": "ACCEPTED"}])

            p3 = run / "phase-03-harmony-scaffold"
            write_json(p3 / "stage-03-gate-report.json", {"phase": 3, "verdict": "PASS"})
            seal(p3 / "stage-03-gate-report.json", p3 / "CLOSED")
            (p3 / "stage-03-closure-manifest.sha256").write_text("fixture", encoding="utf-8")
            write_csv(p3 / "architecture-map.csv", ["inventory_id"], [{"inventory_id": "INV-ONE"}])
            write_csv(p3 / "module-registry.csv", ["harmony_module_id", "status"], [{"harmony_module_id": "MODULE-ONE", "status": "READY"}])

            errors, _ = validate_gate._validate_gmi_equivalent(run, 4)
            self.assertTrue(any("stage-04" in error for error in errors))

            p4 = run / "phase-04-harmony-implementation"
            write_json(p4 / "stage-04-gate-report.json", {"phase": 4, "verdict": "PASS"})
            seal(p4 / "stage-04-gate-report.json", p4 / "CLOSED")
            (p4 / "stage-04-closure-manifest.sha256").write_text("fixture", encoding="utf-8")
            write_csv(p4 / "page-contract-registry.csv", ["page_id", "relative_path"], [{"page_id": "PAGE-ONE", "relative_path": "page-contracts/PAGE-ONE.json"}])
            write_json(p4 / "page-contracts" / "PAGE-ONE.json", {"page_id": "PAGE-ONE", "components": [{"component_id": "COMP-ONE"}], "behavior_bindings": [{"event": "click", "action": "open"}]})

            errors, _ = validate_gate._validate_gmi_equivalent(run, 4)
            self.assertTrue(any("implementation ledger" in error for error in errors))
            self.assertTrue(any("sealed evidence" in error for error in errors))
            self.assertTrue(any("acceptance ledger" in error for error in errors))

    def test_detects_codearts_route_id_back_shell(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gmi-shell-") as temp_name:
            source = Path(temp_name) / "page.ets"
            source.write_text(
                "@Entry @Component struct Demo { build() { Column() { "
                "Text('ROUTE-DEMO') Text('PAGE-DEMO') Button('Back') } } }",
                encoding="utf-8",
            )
            self.assertIn("Route-ID/Page-ID", validate_gate._gmi_placeholder_page(source, "PAGE-DEMO"))


if __name__ == "__main__":
    unittest.main()
