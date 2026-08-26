from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

from publish_harmony_project import publish  # noqa: E402


class PublishHarmonyProjectTest(unittest.TestCase):
    def test_publishes_only_after_closed_pass_and_rejects_nonempty_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="publish-harmony-") as temp_name:
            root = Path(temp_name)
            workspace = root / "run" / "phase-04-harmony-implementation"
            source = workspace / "harmony-project"
            source.mkdir(parents=True)
            (source / "entry.ets").write_text("@Entry struct App {}", encoding="utf-8")
            (source / "build" / "ignored.bin").parent.mkdir(parents=True)
            (source / "build" / "ignored.bin").write_bytes(b"ignored")
            report = workspace / "stage-04-gate-report.json"
            report.write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
            source_hash = hashlib.sha256((source / "entry.ets").read_bytes()).hexdigest()
            (workspace / "stage-04-closure-manifest.sha256").write_text(
                f"{source_hash}  harmony-project/entry.ets\n", encoding="utf-8"
            )
            (workspace / "CLOSED").write_text(hashlib.sha256(report.read_bytes()).hexdigest(), encoding="utf-8")

            target = root / "arkts"
            self.assertEqual(1, publish(workspace, target))
            self.assertTrue((target / "entry.ets").is_file())
            self.assertFalse((target / "build").exists())
            self.assertTrue((target / ".migration-source.json").is_file())
            with self.assertRaisesRegex(ValueError, "empty directory"):
                publish(workspace, target)


if __name__ == "__main__":
    unittest.main()
