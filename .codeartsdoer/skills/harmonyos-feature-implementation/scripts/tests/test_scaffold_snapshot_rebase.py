from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import init_implementation as init_impl  # noqa: E402


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ScaffoldRebaseTest(unittest.TestCase):
    def _snapshot(self, phase3: Path) -> dict:
        """Build a sealed snapshot with one registry file and one project file."""
        reg = phase3 / "architecture-map.csv"
        page = phase3 / "harmony-project" / "pages" / "Index.ets"
        (phase3 / "harmony-project" / "pages").mkdir(parents=True, exist_ok=True)
        reg.write_bytes(b"OLD-REGISTRY")
        page.write_bytes(b"PAGE-V1")
        entries = [
            {
                "path": "architecture-map.csv",
                "sha256": sha_bytes(b"OLD-REGISTRY"),
                "size": len(b"OLD-REGISTRY"),
            },
            {
                "path": "harmony-project/pages/Index.ets",
                "sha256": sha_bytes(b"PAGE-V1"),
                "size": len(b"PAGE-V1"),
            },
        ]
        canonical = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return {
            "entries": entries,
            "entry_count": len(entries),
            "snapshot_sha256": init_impl.sha256_text(canonical),
            "excluded_generated_parts": [],
        }

    def _workspace(self, tmp: Path) -> Path:
        phase3 = tmp / "p3"
        phase3.mkdir()
        return phase3

    def test_unauthorized_drift_still_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            phase3 = self._workspace(tmp)
            snap = self._snapshot(phase3)
            (phase3 / "architecture-map.csv").write_bytes(b"NEW-REGISTRY")
            with self.assertRaisesRegex(ValueError, "snapshot entry changed"):
                init_impl.verify_phase3_snapshot(phase3, snap)

    def test_authorized_rebase_passes_and_keeps_sealed_digest_view(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            phase3 = self._workspace(tmp)
            snap = self._snapshot(phase3)
            (phase3 / "architecture-map.csv").write_bytes(b"NEW-REGISTRY-BYTES")
            rebase = {
                "architecture-map.csv": {
                    "sealed_sha256": snap["entries"][0]["sha256"],
                    "current_sha256": sha_bytes(b"NEW-REGISTRY-BYTES"),
                    "current_size": len(b"NEW-REGISTRY-BYTES"),
                }
            }
            entries, applied = init_impl.verify_phase3_snapshot(
                phase3, snap, rebase=rebase)
            # Sealed view preserved: entry reports the frozen digest so the
            # canonical/gate cross-binding stays intact.
            self.assertEqual(entries[0]["sha256"], snap["entries"][0]["sha256"])
            self.assertIn("architecture-map.csv", applied)

    def test_rebase_with_wrong_current_binding_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            phase3 = self._workspace(tmp)
            snap = self._snapshot(phase3)
            (phase3 / "architecture-map.csv").write_bytes(b"SOME-OTHER-NEW")
            rebase = {
                "architecture-map.csv": {
                    "sealed_sha256": snap["entries"][0]["sha256"],
                    "current_sha256": sha_bytes(b"PREDICTED-DIFFERENT"),
                    "current_size": len(b"PREDICTED-DIFFERENT"),
                }
            }
            with self.assertRaisesRegex(ValueError, "snapshot entry changed"):
                init_impl.verify_phase3_snapshot(phase3, snap, rebase=rebase)

    def test_project_files_are_not_rebasable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            declarations = [
                {
                    "path": "harmony-project/pages/Index.ets",
                    "sealed_sha256": "a" * 64,
                    "current_sha256": "b" * 64,
                    "current_size": 6,
                }
            ]
            with self.assertRaisesRegex(ValueError, "not rebasable"):
                init_impl.normalize_scaffold_rebase(declarations)

    def test_normalize_requires_frozen_hash_shape(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            declarations = [
                {
                    "path": "architecture-map.csv",
                    "sealed_sha256": "__UNBOUND_SHA256__",
                    "current_sha256": "b" * 64,
                    "current_size": 1,
                }
            ]
            with self.assertRaisesRegex(ValueError, "not a valid frozen hash"):
                init_impl.normalize_scaffold_rebase(declarations)


if __name__ == "__main__":
    unittest.main()