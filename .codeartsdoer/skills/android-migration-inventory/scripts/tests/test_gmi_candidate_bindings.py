from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import gmi_generate  # noqa: E402
import gmi_scan  # noqa: E402


class GmiCandidateBindingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pages = [
            {"page_id": "PAGE-SETTINGS", "symbol": "SettingsActivity"},
            {"page_id": "PAGE-ACTION-BUTTON", "symbol": "ActionButtonSettingsActivity"},
        ]

    def test_binds_preference_file_to_unique_page_symbol(self) -> None:
        self.assertEqual(
            "PAGE-ACTION-BUTTON",
            gmi_generate._page_for_source_hint(
                "app/src/main/res/xml/preferences_action_button_settings.xml:3", self.pages
            ),
        )

    def test_ambiguous_generic_preference_remains_unbound(self) -> None:
        self.assertEqual("", gmi_generate._page_for_source_hint("preferences.xml:1", self.pages))

    def test_when_branch_keeps_source_file_for_page_binding(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gmi-when-") as temp_name:
            source = Path(temp_name) / "ActionButtonSettingsActivity.kt"
            source.write_text(
                "fun render(destination: Destination) { when(destination) { "
                "Destination.One -> one(), Destination.Two -> two(), } }",
                encoding="utf-8",
            )
            branches, sources, _ = gmi_scan.scan_when_branches([{
                "category": "source", "abs": str(source),
                "rel": "src/ActionButtonSettingsActivity.kt",
            }])
            self.assertEqual(["Destination.One", "Destination.Two"], branches["destination"])
            self.assertTrue(sources["destination"].startswith("src/ActionButtonSettingsActivity.kt:"))


if __name__ == "__main__":
    unittest.main()
