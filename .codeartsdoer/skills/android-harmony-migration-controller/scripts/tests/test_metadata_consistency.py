from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
SKILL_ROOT = ROOT / ".codeartsdoer" / "skills"
SKILLS = (
    "android-harmony-migration-controller",
    "android-migration-inventory",
    "harmonyos-migration-scaffold",
    "harmonyos-feature-implementation",
)
MOJIBAKE = ("\ufffd", "閳", "閿", "閵", "娴ｈ", "闂冭")



def yaml_quoted_value(path: Path, key: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf'^\s*{re.escape(key)}:\s*"([^"]*)"\s*$', text, re.MULTILINE)
    if not match:
        raise AssertionError(f"Missing quoted {key}: {path}")
    return match.group(1)


class MetadataConsistencyTest(unittest.TestCase):

    def test_agent_prompts_are_readable_and_match_current_gate_contract(self) -> None:
        required = {
            "android-harmony-migration-controller": ("WAITING_HUMAN_REVIEW",),
            "android-migration-inventory": ("WAITING_HUMAN_REVIEW",),
            "harmonyos-migration-scaffold": ("WAITING_HUMAN_REVIEW",),
            "harmonyos-feature-implementation": (
                "PAGE_WORK_ORDER",
                "CAPABILITY_WORK_ORDER",
                "UI_UNDERSTANDING_AND_CONVERSION_AGENT",
                "UiTest",
            ),
        }
        for name, tokens in required.items():
            root = SKILL_ROOT / name
            for adapter in ("interface.yaml", "openai.yaml"):
                path = root / "agents" / adapter
                text = path.read_text(encoding="utf-8")
                with self.subTest(skill=name, adapter=adapter):
                    self.assertFalse(any(marker in text for marker in MOJIBAKE), text)
                    prompt = yaml_quoted_value(path, "default_prompt")
                    for token in tokens:
                        self.assertIn(token, prompt)

    def test_controller_metadata_is_human_gated_and_excludes_phase56_claims(self) -> None:
        root = SKILL_ROOT / "android-harmony-migration-controller"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("phases-1-4-human-gated", manifest["capability_scope"])
        self.assertNotIn("skill_ir_source", manifest)
        self.assertNotIn("reports", manifest["factory_components"])
        self.assertFalse((root / "reports").exists())
        self.assertFalse((root / "evals").exists())
        self.assertFalse((root / "requirements-ci.txt").exists())

        # Regression guard: Phase 5/6 stays dead. The scripts/ directory must not
        # contain the removed work-order issuers or their shared helper.
        scripts = {path.name for path in (root / "scripts").glob("*.py")}
        for forbidden in (
            "issue_phase5_work_order.py",
            "issue_phase6_work_order.py",
            "_phase56_common.py",
        ):
            self.assertNotIn(forbidden, scripts)


        # Documents must not re-introduce Phase 5/6, Gate 5, or their environments.
        docs = [root / "SKILL.md", *sorted((root / "references").glob("*.md"))]
        banned = re.compile(r"phase5|phase6|phase56|Phase 5|Phase 6|Gate 5|H5ENV|H6ENV")
        for path in docs:
            with self.subTest(doc=path.name):
                self.assertIsNone(banned.search(path.read_text(encoding="utf-8")))

        for required in (
            "references/human-review-gates.md",
            "references/phase-gates.md",
            "scripts/_human_gate.py",
            "scripts/generate_review_summary.py",
            "scripts/record_human_review.py",
            "scripts/tests/test_human_gate.py",
            "scripts/tests/test_human_gate_wiring.py",
        ):
            self.assertTrue((root / required).is_file(), required)

        # The reference map must stay one-to-one with the actual reference files.
        skill_text = (root / "SKILL.md").read_text(encoding="utf-8")
        for path in sorted((root / "references").glob("*.md")):
            self.assertIn(path.name, skill_text, path.name)

    def test_phase4_metadata_uses_page_and_capability_orders_only(self) -> None:
        root = SKILL_ROOT / "harmonyos-feature-implementation"
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("phase-4-page-and-capability-orders", manifest["capability_scope"])
        ir_path = root / "reports" / "skill-ir.json"
        if not ir_path.is_file():
            # The feature skill dropped its factory reports; its live contract is
            # asserted through manifest + SKILL.md below.
            combined = (root / "SKILL.md").read_text(encoding="utf-8")
            self.assertNotIn("feature work order", combined.lower())
            for required in ("PAGE_WORK_ORDER", "CAPABILITY_WORK_ORDER"):
                self.assertIn(required, combined)
            return
        ir = json.loads(ir_path.read_text(encoding="utf-8"))
        combined = json.dumps(ir, ensure_ascii=False)
        self.assertNotIn("feature work order", combined.lower())
        self.assertNotIn("Inspector", combined)
        self.assertNotIn("arkui_inspector", combined)
        self.assertIn("UI_UNDERSTANDING_AND_CONVERSION_AGENT", combined)
        self.assertIn("arkts-page-plan", combined)
        self.assertIn("UiTest", combined)
        scripts = {item.replace("\\", "/") for item in ir["resources"]["scripts"]}
        assets = {item.replace("\\", "/") for item in ir["resources"]["assets"]}
        self.assertNotIn("scripts/issue_feature_work_order.py", scripts)
        for required in (
            # 原独立签发 CLI（页面/能力各一个入口）已删除，页面与能力
            # 工单统一由 scripts/stage4_work_orders.py 签发。
            "scripts/stage4_work_orders.py",
            "scripts/tests/test_stage4_work_orders.py",
        ):
            self.assertIn(required, scripts)
        for required in (
            "assets/page-work-order.template.json",
            "assets/page-work-order-registry.template.csv",
            "assets/capability-work-order.template.json",
            "assets/capability-work-order-registry.template.csv",
        ):
            self.assertIn(required, assets)


if __name__ == "__main__":
    unittest.main()
