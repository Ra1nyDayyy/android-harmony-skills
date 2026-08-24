#!/usr/bin/env python3
"""Static contract: Phase 4 guidance requires UiTest, never Inspector APIs."""

from __future__ import annotations

import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[2]
FORBIDDEN = ("Inspector", "getFilteredInspectorTree", "arkui-inspector")


class UiTestSkillContractTest(unittest.TestCase):
    def test_skill_and_every_reference_use_uitest_snapshot_contract(self) -> None:
        paths = [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md"))]
        joined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        for token in FORBIDDEN:
            self.assertNotIn(token, joined)
        self.assertIn("@ohos.UiTest", joined)
        self.assertIn("ui-test-snapshot-evidence.md", (SKILL / "SKILL.md").read_text(encoding="utf-8"))
        self.assertTrue((SKILL / "references" / "ui-test-snapshot-evidence.md").is_file())


if __name__ == "__main__":
    unittest.main()
