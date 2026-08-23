#!/usr/bin/env python3
"""End-to-end positive and fail-closed tests for convert_asset.py."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "convert_asset.py"
ASSET_ID = "ASSET-LOGO-001"
CONTRACT_ID = "ACONV-SVG-PNG-001"
ASSET_AGENT = "phase4-visual-assets"
LEAD = "phase4-implementation-lead"
TARGET_RELATIVE = "entry/src/main/resources/base/media/logo.png"
CSV_FIELDS = [
    "asset_id",
    "source_path",
    "archive_relative_path",
    "source_sha256",
    "file_type",
    "feature_ids",
    "page_ids",
    "state_ids",
    "target_module_id",
    "target_resource_path",
    "target_resource_symbol",
    "migration_mode",
    "target_sha256",
    "conversion_command",
    "conversion_record_id",
    "conversion_record_sha256",
    "verification_evidence_id",
    "nativeization_decision_id",
    "migrated_by",
    "status",
    "notes",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ConversionFixture:
    def __init__(self, root: Path, mode: str = "ok") -> None:
        self.workspace = root / "phase-04-harmony-implementation"
        for relative in (
            "asset-conversions",
            "attempts",
            ".locks",
            ".staging",
            "harmony-project",
            f"inputs/phase2-assets/files/{ASSET_ID}",
        ):
            (self.workspace / relative).mkdir(parents=True, exist_ok=True)

        self.source = (
            self.workspace / "inputs" / "phase2-assets" / "files" / ASSET_ID / "logo.svg"
        ).resolve()
        self.source.write_text("<svg><path d='M0 0h1v1z'/></svg>\n", encoding="utf-8")
        self.source_sha = sha256_file(self.source)
        self.target = self.workspace / "harmony-project" / TARGET_RELATIVE

        self.fake = root / "fake_asset_converter.py"
        self.fake.write_text(
            """#!/usr/bin/env python3
import argparse
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--mode', required=True)
p.add_argument('--source', required=True)
p.add_argument('--target', required=True)
a = p.parse_args()
Path(a.target).write_bytes(b'converted:' + Path(a.source).read_bytes())
if a.mode == 'ok':
    print('CONVERSION_OK')
elif a.mode == 'no-marker':
    print('conversion completed')
elif a.mode == 'error-zero':
    print('Error: converter reported failure')
""",
            encoding="utf-8",
        )
        self.fake.chmod(0o755)

        executable = Path(sys.executable).resolve()
        self.contracts = {
            "schema_version": "1.0",
            "created_at": "2026-08-23T00:00:00Z",
            "locked_by": LEAD,
            "contracts": [
                {
                    "contract_id": CONTRACT_ID,
                    "source_extensions": [".svg"],
                    "target_extensions": [".png"],
                    "resolved_executable": str(executable),
                    "executable_sha256": sha256_file(executable),
                    "argv_template": [
                        str(executable),
                        str(self.fake.resolve()),
                        "--mode",
                        mode,
                        "--source",
                        "{SOURCE}",
                        "--target",
                        "{TARGET}",
                    ],
                    "required_argv_tokens": [
                        str(self.fake.resolve()), "--mode", mode, "--source", "--target"
                    ],
                    "success_output_contains": ["CONVERSION_OK"],
                    "error_output_contains": ["Error:", "FAILED"],
                }
            ],
        }
        self.contract_path = self.workspace / "asset-conversion-contracts.json"
        atomic_json(self.contract_path, self.contracts)

        self.input_lock = {
            "ownership": {
                "implementation_lead_id": LEAD,
                "visual_asset_agent_id": ASSET_AGENT,
                "verification_executor_id": "phase4-verification",
                "parity_acceptance_agent_id": "phase4-acceptance",
            },
            "phase2_asset_files": [
                {
                    "asset_id": ASSET_ID,
                    "source_path": str((root / "upstream" / "logo.svg").resolve()),
                    "snapshot_path": str(self.source),
                    "sha256": self.source_sha,
                    "size": self.source.stat().st_size,
                }
            ],
            "asset_conversion_contracts_sha256": sha256_file(self.contract_path),
        }
        self.input_lock_path = self.workspace / "stage-04-input-lock.json"
        atomic_json(self.input_lock_path, self.input_lock)
        atomic_json(
            self.workspace / "phase-manifest.json",
            {
                "phase": 4,
                "status": "IN_PROGRESS",
                "ownership": self.input_lock["ownership"],
                "input_lock_sha256": sha256_file(self.input_lock_path),
            },
        )
        self.write_asset_row()

    def write_asset_row(self) -> None:
        row = {field: "" for field in CSV_FIELDS}
        row.update(
            {
                "asset_id": ASSET_ID,
                "source_path": "app/src/main/res/drawable/logo.svg",
                "archive_relative_path": f"asset-package/files/{ASSET_ID}/logo.svg",
                "source_sha256": self.source_sha,
                "file_type": "SVG",
                "feature_ids": '["FEATURE-AUTH"]',
                "page_ids": '["PAGE-LOGIN"]',
                "state_ids": '["STATE-DEFAULT"]',
                "target_module_id": "HMOD-ENTRY",
                "target_resource_path": TARGET_RELATIVE,
                "target_resource_symbol": "LOGO",
                "migration_mode": "FORMAT_CONVERSION",
                "status": "PLANNED",
            }
        )
        with (self.workspace / "asset-migration.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerow(row)

    def rewrite_contracts_and_relock(self) -> None:
        atomic_json(self.contract_path, self.contracts)
        self.input_lock["asset_conversion_contracts_sha256"] = sha256_file(self.contract_path)
        atomic_json(self.input_lock_path, self.input_lock)
        manifest = json.loads(
            (self.workspace / "phase-manifest.json").read_text(encoding="utf-8")
        )
        manifest["input_lock_sha256"] = sha256_file(self.input_lock_path)
        atomic_json(self.workspace / "phase-manifest.json", manifest)

    def run(
        self,
        conversion_id: str = "CONV-LOGO-001",
        actor: str = ASSET_AGENT,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--workspace",
                str(self.workspace),
                "--conversion-id",
                conversion_id,
                "--asset-id",
                ASSET_ID,
                "--contract-id",
                CONTRACT_ID,
                "--executed-by",
                actor,
                "--timeout",
                "10",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )

    def row(self) -> dict[str, str]:
        with (self.workspace / "asset-migration.csv").open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            return next(csv.DictReader(handle))


class AssetConversionTests(unittest.TestCase):
    def fixture(self, root: Path, mode: str = "ok") -> ConversionFixture:
        return ConversionFixture(root, mode)

    def assert_failed_cleanly(
        self, fixture: ConversionFixture, result: subprocess.CompletedProcess[str], conversion_id: str
    ) -> None:
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((fixture.workspace / "asset-conversions" / conversion_id).exists())
        self.assertFalse(fixture.target.exists())
        self.assertEqual(fixture.row()["status"], "PLANNED")
        self.assertTrue((fixture.workspace / "attempts" / f"ATT-{conversion_id}.json").is_file())
        self.assertEqual(list((fixture.workspace / ".staging").iterdir()), [])

    def test_success_seals_output_and_atomically_updates_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            fixture = self.fixture(Path(temp_name))
            result = fixture.run()
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            package = fixture.workspace / "asset-conversions" / "CONV-LOGO-001"
            expected = {
                "metadata.json",
                "output/logo.png",
                "logs/stdout.log",
                "logs/stderr.log",
                "manifest.sha256",
                "COMMITTED",
            }
            actual = {
                path.relative_to(package).as_posix()
                for path in package.rglob("*")
                if path.is_file()
            }
            self.assertEqual(actual, expected)
            metadata = json.loads((package / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["schema_version"], 1)
            self.assertEqual(metadata["executed_by"], ASSET_AGENT)
            self.assertEqual(metadata["command"]["command_verdict"], "PASS")
            self.assertEqual(metadata["command"]["success_output_matches"], ["CONVERSION_OK"])
            manifest_entries = {}
            for line in (package / "manifest.sha256").read_text(encoding="utf-8").splitlines():
                digest, relative = line.split("  ", 1)
                manifest_entries[relative] = digest
            self.assertEqual(
                set(manifest_entries),
                {"metadata.json", "output/logo.png", "logs/stdout.log", "logs/stderr.log"},
            )
            for relative, digest in manifest_entries.items():
                self.assertEqual(sha256_file(package / relative), digest)
            manifest_sha = sha256_file(package / "manifest.sha256")
            self.assertTrue(
                (package / "COMMITTED").read_text(encoding="utf-8").startswith(
                    f"CONV-LOGO-001 PASS manifest_sha256={manifest_sha} committed_at="
                )
            )
            self.assertTrue(fixture.target.is_file())
            self.assertEqual(sha256_file(fixture.target), metadata["target"]["sha256"])
            row = fixture.row()
            self.assertEqual(row["status"], "CONVERSION_VERIFIED")
            self.assertEqual(row["conversion_record_id"], "CONV-LOGO-001")
            self.assertEqual(row["conversion_record_sha256"], sha256_file(package / "metadata.json"))
            self.assertEqual(row["verification_evidence_id"], "")
            self.assertEqual(row["migrated_by"], ASSET_AGENT)
            self.assertEqual(json.loads(row["conversion_command"]), metadata["command"]["argv"])
            self.assertFalse(package.stat().st_mode & 0o222)
            for path in package.rglob("*"):
                self.assertFalse(path.stat().st_mode & 0o222)

    def test_missing_success_marker_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            fixture = self.fixture(Path(temp_name), "no-marker")
            result = fixture.run("CONV-NOMARKER-001")
            self.assert_failed_cleanly(fixture, result, "CONV-NOMARKER-001")

    def test_exit_zero_with_error_text_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            fixture = self.fixture(Path(temp_name), "error-zero")
            result = fixture.run("CONV-ERRORZERO-001")
            self.assert_failed_cleanly(fixture, result, "CONV-ERRORZERO-001")

    def test_old_target_is_never_reused_or_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            fixture = self.fixture(Path(temp_name))
            fixture.target.parent.mkdir(parents=True)
            fixture.target.write_bytes(b"old-target")
            result = fixture.run("CONV-OLDTARGET-001")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(fixture.target.read_bytes(), b"old-target")
            self.assertFalse(
                (fixture.workspace / "asset-conversions" / "CONV-OLDTARGET-001").exists()
            )
            self.assertEqual(fixture.row()["status"], "PLANNED")
            self.assertTrue(
                (fixture.workspace / "attempts" / "ATT-CONV-OLDTARGET-001.json").is_file()
            )

    def test_wrong_actor_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            fixture = self.fixture(Path(temp_name))
            result = fixture.run("CONV-ACTOR-001", "some-other-agent")
            self.assert_failed_cleanly(fixture, result, "CONV-ACTOR-001")

    def test_closed_workspace_is_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            fixture = self.fixture(Path(temp_name))
            (fixture.workspace / "CLOSED").write_text("sealed\n", encoding="utf-8")
            result = fixture.run("CONV-CLOSED-001")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(fixture.target.exists())
            self.assertFalse(
                (fixture.workspace / "asset-conversions" / "CONV-CLOSED-001").exists()
            )
            self.assertFalse(
                (fixture.workspace / "attempts" / "ATT-CONV-CLOSED-001.json").exists()
            )
            self.assertEqual(fixture.row()["status"], "PLANNED")

    def test_modified_contract_and_source_hash_each_fail_closed(self) -> None:
        for case in ("contract", "source"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp_name:
                fixture = self.fixture(Path(temp_name))
                conversion_id = f"CONV-TAMPER-{case.upper()}"
                if case == "contract":
                    fixture.contracts["contracts"][0]["success_output_contains"] = [
                        "CONVERSION_OK", "EXTRA_MARKER"
                    ]
                    atomic_json(fixture.contract_path, fixture.contracts)
                else:
                    fixture.source.write_text("tampered source\n", encoding="utf-8")
                result = fixture.run(conversion_id)
                self.assert_failed_cleanly(fixture, result, conversion_id)

    def test_conversion_id_cannot_be_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            fixture = self.fixture(Path(temp_name))
            first = fixture.run("CONV-DUPLICATE-001")
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            package = fixture.workspace / "asset-conversions" / "CONV-DUPLICATE-001"
            metadata_sha = sha256_file(package / "metadata.json")
            target_sha = sha256_file(fixture.target)
            second = fixture.run("CONV-DUPLICATE-001")
            self.assertNotEqual(second.returncode, 0)
            self.assertEqual(sha256_file(package / "metadata.json"), metadata_sha)
            self.assertEqual(sha256_file(fixture.target), target_sha)
            self.assertEqual(fixture.row()["conversion_record_id"], "CONV-DUPLICATE-001")


if __name__ == "__main__":
    unittest.main()
