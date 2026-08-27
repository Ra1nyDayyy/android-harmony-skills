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

    def test_preference_fields_carry_default_summary_category(self) -> None:
        """三列增强：preference 源收录 defaultValue/summary/category（原样字符串）。"""
        with tempfile.TemporaryDirectory(prefix="gmi-pref-") as temp_name:
            pref = Path(temp_name) / "preferences_action_button_settings.xml"
            pref.write_text(
                '<PreferenceScreen xmlns:android="http://schemas.android.com/apk/res/android">\n'
                '  <PreferenceCategory android:title="@string/cat_general">\n'
                '    <SwitchPreferenceCompat android:key="sync_data" android:title="Sync"\n'
                '        android:summary="@string/sync_summary" android:defaultValue="true" />\n'
                '    <CheckBoxPreference android:key="legacy_mode" android:title="Legacy" />\n'
                '  </PreferenceCategory>\n'
                '  <Preference android:key="about_entry" android:title="About" />\n'
                "</PreferenceScreen>\n",
                encoding="utf-8",
            )
            files = [{
                "category": "resource", "abs": str(pref),
                "rel": "app/src/main/res/xml/preferences_action_button_settings.xml",
            }]
            pref_rows = gmi_scan.scan_preference_xml(files)
            field_rows = gmi_generate.make_pref_concat_rows(pref_rows, self.pages)
            by_field = {row["field_id"]: row for row in field_rows}
            # 断言 1：带属性的 preference -> defaultValue/summary/category 三列原样收录
            # （资源引用保持 @string/xxx 形式，category 取所在 PreferenceCategory 的 title）。
            self.assertEqual(
                {"default_value": "true", "summary": "@string/sync_summary", "category": "@string/cat_general"},
                {k: by_field["sync_data"][k] for k in ("default_value", "summary", "category")},
            )
            # 断言 2：缺 defaultValue/summary 的 preference -> 两列空串；category 仍来自祖先分组。
            self.assertEqual(
                {"default_value": "", "summary": "", "category": "@string/cat_general"},
                {k: by_field["legacy_mode"][k] for k in ("default_value", "summary", "category")},
            )
            # 断言 3：不在任何 PreferenceCategory 内的 preference -> category 为空串。
            self.assertEqual(
                {"default_value": "", "summary": "", "category": ""},
                {k: by_field["about_entry"][k] for k in ("default_value", "summary", "category")},
            )

    def test_non_preference_field_rows_default_summary_category_empty(self) -> None:
        """兼容性：非 preference 源（layout XML / compose）三列记空串。"""
        comps = [{
            "page_id": "PAGE-SETTINGS", "type": "android.widget.EditText",
            "resource_id": "et_name", "text": "Name", "doc_order": 1,
            "attributes": {}, "layout": "activity_settings",
            "source_ref": "res/layout/activity_settings.xml:5",
        }]
        compose_nodes = [{
            "kind": "field", "page_id": "PAGE-SETTINGS", "resource_id": "",
            "order": 1, "type": "compose:TextField", "text": "Label",
            "attributes": {}, "layout": "SettingsScreen.kt",
            "source_ref": "src/SettingsScreen.kt:9",
        }]
        for row in gmi_generate.make_page_field_rows(comps, self.pages, compose_nodes):
            with self.subTest(field_id=row["field_id"]):
                self.assertEqual(
                    {"default_value": "", "summary": "", "category": ""},
                    {k: row[k] for k in ("default_value", "summary", "category")},
                )


if __name__ == "__main__":
    unittest.main()
