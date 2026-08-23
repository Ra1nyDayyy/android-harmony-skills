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


if __name__ == "__main__":
    unittest.main()
