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
                "gmi_closure": {"unmapped": 0, "audit_passed": True, "audit_discrepancy": 0},
            })
            for name in validate_gate.GMI_REQUIRED_CANDIDATES:
                write_csv(run / "candidates" / name, ["id"])
            (run / "candidates" / "manifest.sha256").write_text("fixture", encoding="utf-8")
            write_csv(run / "coverage" / "coverage-ledger.csv", ["status", "disposition"])
            write_csv(run / "runtime-evidence" / "runtime-gate.csv", ["status"])
            write_csv(run / "runtime-evidence" / "audit-replay.csv", ["discrepancy"])

            errors, _ = validate_gate._validate_gmi_equivalent(run, 2)
            self.assertEqual([], errors)

    def test_phase2_audit_gate_uses_audit_passed_only(self) -> None:
        """A1：新版 gmi_closure 只产出布尔 audit_passed（无 audit_discrepancy 字段）；
        validate --phase 2 必须以 audit_passed 为主判定，不得因缺少数值型
        audit_discrepancy 报 audit 类错误；audit_passed 非 True 时仍须阻断。"""
        with tempfile.TemporaryDirectory(prefix="gmi-audit-passed-") as temp_name:
            run = Path(temp_name)
            p2 = run / "phase-02-android-inventory"

            def build_closure(gate: dict) -> None:
                write_json(p2 / "closure-report.json", {
                    "generated_by": "gmi-phase3-adapter",
                    "gmi_closure": gate,
                })
                for name in validate_gate.GMI_REQUIRED_CANDIDATES:
                    write_csv(run / "candidates" / name, ["id"])
                (run / "candidates" / "manifest.sha256").write_text("fixture", encoding="utf-8")
                write_csv(run / "coverage" / "coverage-ledger.csv", ["status", "disposition"])
                write_csv(run / "runtime-evidence" / "runtime-gate.csv", ["status"])
                write_csv(run / "runtime-evidence" / "audit-replay.csv", ["discrepancy"])

            # 只有 audit_passed=True，完全没有 audit_discrepancy 字段
            build_closure({"unmapped": 0, "audit_passed": True})
            errors, _ = validate_gate._validate_gmi_equivalent(run, 2)
            self.assertEqual(
                [], [e for e in errors if "audit" in e.lower()],
                f"no audit-class error is allowed when audit_passed=True: {errors}",
            )
            self.assertEqual([], errors)

            # 反向对照：audit_passed=False 必须产生 audit 类错误
            build_closure({"unmapped": 0, "audit_passed": False})
            errors, _ = validate_gate._validate_gmi_equivalent(run, 2)
            self.assertTrue(
                any("audit_passed" in e for e in errors),
                f"audit_passed=False must be blocked: {errors}",
            )

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

    def test_reviewed_visual_ids_branch_by_verification_tier(self) -> None:
        """P4 分层验证（validate_phase4 :4151 分支的判定核）：

        - LITE：非空抽样子集通过、全集亦通过、空集与未知元素被拒；
        - CORE：必须精确等于全集（子集被拒）。
        """
        universe = {"VEL-A", "VEL-B", "VEL-C"}
        accept = validate_gate.reviewed_visual_ids_are_acceptable
        # LITE 分支
        self.assertTrue(accept(["VEL-A"], universe, "LITE"))
        self.assertTrue(accept(["VEL-A", "VEL-C"], universe, "LITE"))
        self.assertTrue(accept(["VEL-A", "VEL-B", "VEL-C"], universe, "LITE"))
        self.assertFalse(accept([], universe, "LITE"))            # 空抽样被拒
        self.assertFalse(accept(["VEL-GHOST"], universe, "LITE"))  # 超出全集被拒
        self.assertFalse(accept("VEL-A", universe, "LITE"))        # 非列表被拒
        # CORE 分支（默认，与旧行为一致）
        self.assertTrue(accept(["VEL-A", "VEL-B", "VEL-C"], universe, "CORE"))
        self.assertFalse(accept(["VEL-A"], universe, "CORE"))      # 子集被拒
        self.assertFalse(accept(["VEL-A", "VEL-B", "VEL-C", "VEL-GHOST"], universe, "CORE"))
        self.assertFalse(accept([], universe, "CORE"))


if __name__ == "__main__":
    unittest.main()
