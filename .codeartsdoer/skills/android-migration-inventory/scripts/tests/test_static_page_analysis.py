#!/usr/bin/env python3
"""Offline tests for deterministic Android static page discovery."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SKILL = HERE.parents[1]


class StaticPageAnalysisTest(unittest.TestCase):
    def _run_analyzer(self, workspace: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run([
            sys.executable, str(SKILL / "scripts" / "analyze_static_pages.py"),
            "--workspace", str(workspace), "--analyzed-by", "code-map-agent-1",
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def _run_validator(self, workspace: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run([
            sys.executable, str(SKILL / "scripts" / "validate_static_analysis.py"),
            "--workspace", str(workspace),
        ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def test_xml_activity_event_state_and_transition_are_discovered(self) -> None:
        with tempfile.TemporaryDirectory(prefix="android-static-analysis-") as temp_name:
            root = Path(temp_name)
            project = root / "project"
            workspace = root / "phase-02-android-inventory"
            layout = project / "app" / "src" / "main" / "res" / "layout"
            values = project / "app" / "src" / "main" / "res" / "values"
            source = project / "app" / "src" / "main" / "java" / "demo"
            layout.mkdir(parents=True)
            values.mkdir(parents=True)
            source.mkdir(parents=True)
            workspace.mkdir()
            (project / "app" / "src" / "main" / "AndroidManifest.xml").write_text(
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="demo">'
                '<uses-permission android:name="android.permission.CAMERA"/>'
                '<application><activity android:name=".LoginActivity"><intent-filter>'
                '<action android:name="android.intent.action.MAIN"/>'
                '<category android:name="android.intent.category.LAUNCHER"/>'
                '</intent-filter></activity></application></manifest>', encoding="utf-8",
            )
            (values / "strings.xml").write_text(
                '<resources><string name="login">登录</string></resources>', encoding="utf-8",
            )
            (layout / "activity_login.xml").write_text(
                '<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android" '
                'android:layout_width="match_parent" android:layout_height="match_parent">'
                '<Button android:id="@+id/loginButton" android:layout_width="match_parent" '
                'android:layout_height="48dp" android:text="@string/login"/>'
                '</LinearLayout>', encoding="utf-8",
            )
            (source / "LoginActivity.kt").write_text(
                'class LoginActivity : AppCompatActivity() {\n'
                ' fun show() { setContentView(R.layout.activity_login)\n'
                ' loginButton.setOnClickListener { if (ready) startActivity(Intent(this, HomeActivity::class.java)) }\n'
                ' fun hidden() { Class.forName("demo.Plugin"); WebView(this).loadUrl("https://example.test") }\n'
                ' fun effects(p: SharedPreferences, c: ClipboardManager) { p.edit(); c.setPrimaryClip(clip); '
                'WorkManager.getInstance(this); requestPermissions(arrayOf("CAMERA"), 1); OkHttpClient() }\n'
                '}\n', encoding="utf-8",
            )
            (workspace / "phase-manifest.json").write_text(json.dumps({
                "phase": 2, "status": "IN_PROGRESS", "android_project_root": str(project),
                "source_revision": "abc123", "included_features": ["FEATURE-AUTH"],
                "ownership": {"code_map_agent_id": "code-map-agent-1"},
            }), encoding="utf-8")
            analyzer = subprocess.run([
                sys.executable, str(SKILL / "scripts" / "analyze_static_pages.py"),
                "--workspace", str(workspace), "--analyzed-by", "code-map-agent-1",
            ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            self.assertEqual(analyzer.returncode, 0, analyzer.stderr)
            pages = json.loads((workspace / "static-analysis" / "pages.json").read_text(encoding="utf-8"))["pages"]
            components = json.loads((workspace / "static-analysis" / "components.json").read_text(encoding="utf-8"))["components"]
            events = json.loads((workspace / "static-analysis" / "events.json").read_text(encoding="utf-8"))["events"]
            transitions = json.loads((workspace / "static-analysis" / "transitions.json").read_text(encoding="utf-8"))["transitions"]
            states = json.loads((workspace / "static-analysis" / "state-candidates.json").read_text(encoding="utf-8"))["states"]
            advanced = json.loads((workspace / "static-analysis" / "advanced-analysis.json").read_text(encoding="utf-8"))
            self.assertEqual([page["symbol"] for page in pages], ["LoginActivity"])
            self.assertTrue(any(row["resource_id"] == "loginButton" and row["text"] == "登录" for row in components))
            self.assertTrue(any(row["component_symbol"] == "loginButton" for row in events))
            self.assertTrue(any(row["target_symbol"] == "HomeActivity" for row in transitions))
            self.assertTrue(any(row["expression"] == "ready" for row in states))
            self.assertTrue({"REFLECTION", "WEBVIEW"}.issubset(
                {row["risk_type"] for row in advanced["dynamic_risks"]}
            ))
            self.assertTrue({"PREFERENCES", "CLIPBOARD", "BACKGROUND", "PERMISSION", "NETWORK"}.issubset(
                {row["effect_type"] for row in advanced["side_effects"]}
            ))
            self.assertTrue({"REMOTE_ERROR", "PERMISSION_DENIED", "NETWORK_OFFLINE"}.issubset(
                {row["scenario_type"] for row in advanced["scenarios"]}
            ))
            validator = subprocess.run([
                sys.executable, str(SKILL / "scripts" / "validate_static_analysis.py"),
                "--workspace", str(workspace),
            ], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            self.assertEqual(validator.returncode, 0, validator.stdout + validator.stderr)

    def test_discovery_gaps_are_accounted_for_and_cannot_silently_pass(self) -> None:
        with tempfile.TemporaryDirectory(prefix="android-static-gap-") as temp_name:
            root = Path(temp_name)
            project = root / "project"
            workspace = root / "phase-02-android-inventory"
            layout = project / "app" / "src" / "main" / "res" / "layout"
            source = project / "app" / "src" / "main" / "java" / "demo"
            layout.mkdir(parents=True)
            source.mkdir(parents=True)
            workspace.mkdir()
            (project / "app" / "src" / "main" / "AndroidManifest.xml").write_text(
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="demo">'
                '<application><activity android:name=".MainActivity"/></application></manifest>',
                encoding="utf-8",
            )
            (layout / "activity_main.xml").write_text(
                '<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android" '
                'android:layout_width="match_parent" android:layout_height="match_parent"/>',
                encoding="utf-8",
            )
            (layout / "broken.xml").write_text("<LinearLayout>", encoding="utf-8")
            (source / "MainActivity.kt").write_text(
                'class MainActivity : Activity() { fun show() { setContentView(R.layout.activity_main) } }',
                encoding="utf-8",
            )
            (source / "HugeScreen.kt").write_text("x" * (2 * 1024 * 1024 + 1), encoding="utf-8")
            (workspace / "phase-manifest.json").write_text(json.dumps({
                "phase": 2, "status": "IN_PROGRESS", "android_project_root": str(project),
                "source_revision": "abc123", "included_features": ["FEATURE-A"],
                "ownership": {"code_map_agent_id": "code-map-agent-1"},
            }), encoding="utf-8")

            analyzer = self._run_analyzer(workspace)
            self.assertEqual(analyzer.returncode, 0, analyzer.stderr)
            index = json.loads(
                (workspace / "static-analysis" / "project-index.json").read_text(encoding="utf-8")
            )
            ledger = index["source_scan"]
            self.assertEqual(ledger["discovered_count"], 2)
            self.assertEqual(ledger["parsed_count"], 1)
            self.assertEqual(ledger["skipped_count"], 1)
            self.assertEqual(ledger["skipped"][0]["reason"], "FILE_TOO_LARGE")
            tasks = json.loads(
                (workspace / "static-analysis" / "runtime-tasks.json").read_text(encoding="utf-8")
            )["tasks"]
            blocking = [row for row in tasks if row.get("blocking_discovery_gap")]
            self.assertEqual({row["task_type"] for row in blocking}, {"XML_PARSE_ERROR", "SOURCE_SCAN_SKIPPED"})
            self.assertTrue(all(row.get("subject_id") for row in blocking))

            validator = self._run_validator(workspace)
            self.assertNotEqual(validator.returncode, 0, validator.stdout + validator.stderr)
            self.assertIn("blocking discovery gap", validator.stdout)

    def test_nonstandard_compose_page_name_is_not_silently_ignored(self) -> None:
        with tempfile.TemporaryDirectory(prefix="android-static-compose-") as temp_name:
            root = Path(temp_name)
            project = root / "project"
            workspace = root / "phase-02-android-inventory"
            source = project / "app" / "src" / "main" / "java" / "demo"
            source.mkdir(parents=True)
            workspace.mkdir()
            (project / "app" / "src" / "main" / "AndroidManifest.xml").write_text(
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="demo">'
                '<application/></manifest>', encoding="utf-8",
            )
            (source / "Settings.kt").write_text(
                '@Composable\nfun SettingsContent() { Column { Text("Settings") } }\n',
                encoding="utf-8",
            )
            (workspace / "phase-manifest.json").write_text(json.dumps({
                "phase": 2, "status": "IN_PROGRESS", "android_project_root": str(project),
                "source_revision": "abc123", "included_features": ["FEATURE-SETTINGS"],
                "ownership": {"code_map_agent_id": "code-map-agent-1"},
            }), encoding="utf-8")

            analyzer = self._run_analyzer(workspace)
            self.assertEqual(analyzer.returncode, 0, analyzer.stderr)
            pages = json.loads(
                (workspace / "static-analysis" / "pages.json").read_text(encoding="utf-8")
            )["pages"]
            self.assertIn("SettingsContent", {row["symbol"] for row in pages})


if __name__ == "__main__":
    unittest.main()
