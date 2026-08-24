from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[5]
SKILLS = {
    name: ROOT / ".codeartsdoer" / "skills" / name / "SKILL.md"
    for name in (
        "android-harmony-migration-controller",
        "android-migration-inventory",
        "harmonyos-migration-scaffold",
        "harmonyos-feature-implementation",
    )
}


class SkillContractTextTest(unittest.TestCase):
    def text(self, name: str) -> str:
        return SKILLS[name].read_text(encoding="utf-8")

    def test_all_entrypoints_are_lean_and_end_at_human_review(self) -> None:
        for name, path in SKILLS.items():
            with self.subTest(skill=name):
                text = path.read_text(encoding="utf-8")
                nonblank = [line for line in text.splitlines() if line.strip()]
                self.assertLessEqual(len(nonblank), 85)
                self.assertIn("WAITING_HUMAN_REVIEW", text)
                self.assertIn("Models never approve", text)

    def test_controller_cannot_continue_automatically_across_phase_gates(self) -> None:
        text = self.text("android-harmony-migration-controller")
        self.assertIn("human-review-gates.md", text)
        self.assertIn("APPROVED_DEVIATION", text)
        self.assertNotIn("Continue immediately", text)
        self.assertNotIn("issue the Phase 3 work order immediately", text)

    def test_phase2_stays_automatic_internally_but_requires_final_human_review(self) -> None:
        text = self.text("android-migration-inventory")
        self.assertIn("No manual page enumeration", text)
        self.assertIn("human review happens only after the machine Gate", text)

    def test_phase4_is_page_owned_and_model_match_has_no_authority(self) -> None:
        text = self.text("harmonyos-feature-implementation")
        self.assertIn("PAGE_WORK_ORDER", text)
        self.assertIn("one exclusive owner", text)
        self.assertIn("Model-authored `MATCH`", text)
        self.assertIn("machine comparison", text)


if __name__ == "__main__":
    unittest.main()
