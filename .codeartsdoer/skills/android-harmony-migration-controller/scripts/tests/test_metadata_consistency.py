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


def frontmatter_description(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
    if not match:
        raise AssertionError(f"Missing frontmatter description: {path}")
    return match.group(1).strip()


def yaml_quoted_value(path: Path, key: str) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf'^\s*{re.escape(key)}:\s*"([^"]*)"\s*$', text, re.MULTILINE)
    if not match:
        raise AssertionError(f"Missing quoted {key}: {path}")
    return match.group(1)


class MetadataConsistencyTest(unittest.TestCase):
    def test_skill_ir_is_valid_and_matches_frontmatter_and_real_resources(self) -> None:
        for name in SKILLS:
            with self.subTest(skill=name):
                root = SKILL_ROOT / name
                ir = json.loads((root / "reports" / "skill-ir.json").read_text(encoding="utf-8"))
                description = frontmatter_description(root / "SKILL.md")
                self.assertEqual(name, ir["name"])
                self.assertEqual(description, ir["job_to_be_done"])
                self.assertEqual(description, ir["trigger_surface"]["description"])
                for group in ("references", "scripts", "assets", "reports"):
                    for relative in ir["resources"][group]:
                        self.assertTrue((root / Path(relative.replace("\\", "/"))).is_file(), relative)

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
        ir = json.loads((root / "reports" / "skill-ir.json").read_text(encoding="utf-8"))
        combined = json.dumps(ir, ensure_ascii=False)
        self.assertNotIn("continuously execute", combined)
        self.assertNotRegex(combined, r"issue_phase[56]|_phase56|Phase [56]")
        flattened = {item.replace("\\", "/") for values in ir["resources"].values() for item in values}
        for required in (
            "references/human-review-gates.md",
            "scripts/_human_gate.py",
            "scripts/generate_review_summary.py",
            "scripts/record_human_review.py",
            "scripts/tests/test_human_gate.py",
            "scripts/tests/test_human_gate_wiring.py",
        ):
            self.assertIn(required, flattened)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("phases-1-4-human-gated", manifest["capability_scope"])

    def test_phase4_metadata_uses_page_and_capability_orders_only(self) -> None:
        root = SKILL_ROOT / "harmonyos-feature-implementation"
        ir = json.loads((root / "reports" / "skill-ir.json").read_text(encoding="utf-8"))
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
            "scripts/issue_page_work_order.py",
            "scripts/issue_capability_work_order.py",
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
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("phase-4-page-and-capability-orders", manifest["capability_scope"])


if __name__ == "__main__":
    unittest.main()
