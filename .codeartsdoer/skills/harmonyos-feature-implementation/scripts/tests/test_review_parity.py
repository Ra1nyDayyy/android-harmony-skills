#!/usr/bin/env python3
"""Minimal end-to-end coverage for the independent Phase 4 parity review."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
SKILL_ROOT = SCRIPTS.parent
ASSETS = SKILL_ROOT / "assets"
CONTROLLER_ASSETS = SKILL_ROOT.parent / "android-harmony-migration-controller" / "assets"
SCRIPT = SCRIPTS / "review_parity.py"

PARITY_ID = "PAR-REVIEW-001"
INVENTORY_ID = "INV-STATE-001"
FEATURE_ID = "FEATURE-AUTH"
PAGE_ID = "PAGE-LOGIN"
STATE_ID = "STATE-DEFAULT"
VISUAL_ID = "VEL-PAGE-ROOT-001"
ANDROID_EVIDENCE_ID = "EVD-ANDROID-001"
HARMONY_EVIDENCE_ID = "HEVD-HARMONY-001"
REVIEWER = "phase4-parity-acceptance"
EXECUTOR = "phase4-verification-executor"
CONTROLLER = "migration-controller"
DECISION_ID = "NDEC-VISUAL-001"
CONTROLLER_DECISION_ID = "CTRL-DEC-VISUAL-001"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def template_fields(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle))


def write_rows(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def valid_png(red: int = 10, width: int = 4, height: int = 3) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanlines = (b"\x00" + bytes((red, 20, 30)) * width) * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", header)
        + png_chunk(b"IDAT", zlib.compress(scanlines))
        + png_chunk(b"IEND", b"")
    )


def manifest_text(directory: Path) -> str:
    files = sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.name not in {"manifest.sha256", "COMMITTED"}
    )
    return "".join(
        f"{sha256_file(path)}  {path.relative_to(directory).as_posix()}\n" for path in files
    )


def make_writable(directory: Path) -> None:
    if not directory.exists():
        return
    directory.chmod(0o755)
    for path in directory.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)


def make_read_only(directory: Path) -> None:
    for path in sorted(directory.rglob("*"), reverse=True):
        path.chmod(0o555 if path.is_dir() else 0o444)
    directory.chmod(0o555)


def seal_package(directory: Path, evidence_id: str, harmony: bool) -> None:
    make_writable(directory)
    (directory / "manifest.sha256").write_text(manifest_text(directory), encoding="utf-8")
    if harmony:
        digest = sha256_file(directory / "manifest.sha256")
        marker = f"{evidence_id} SEALED manifest_sha256={digest} committed_at=2026-08-23T00:00:00Z\n"
    else:
        marker = f"{evidence_id}\n"
    (directory / "COMMITTED").write_text(marker, encoding="utf-8")
    make_read_only(directory)


def package_facts(directory: Path) -> tuple[str, int, int]:
    entries: list[dict[str, object]] = []
    for path in sorted(directory.rglob("*")):
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(directory).as_posix(),
                    "sha256": sha256_file(path),
                    "size": path.stat().st_size,
                }
            )
    canonical = json.dumps(entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest(), sum(int(item["size"]) for item in entries), len(entries)


def workspace_snapshot(workspace: Path) -> dict[str, tuple[str, int, int]]:
    return {
        path.relative_to(workspace).as_posix(): (
            sha256_file(path),
            path.stat().st_mode & 0o777,
            path.stat().st_size,
        )
        for path in sorted(workspace.rglob("*"))
        if path.is_file()
    }


class ReviewFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.workspace = root / "phase-04-harmony-implementation"
        self.controller = root / "controller"
        self.android_source = (
            root
            / "phase-02-android-inventory"
            / "evidence"
            / "ENV-001"
            / FEATURE_ID
            / PAGE_ID
            / STATE_ID
            / ANDROID_EVIDENCE_ID
        )
        self.android = self.workspace / "inputs" / "android-evidence" / ANDROID_EVIDENCE_ID
        self.harmony_relative = (
            Path("evidence") / "H4ENV-001" / FEATURE_ID / PAGE_ID / STATE_ID / HARMONY_EVIDENCE_ID
        )
        self.harmony = self.workspace / self.harmony_relative
        self.comparison = root / "comparison.json"
        for relative in ("reviews", ".locks"):
            (self.workspace / relative).mkdir(parents=True, exist_ok=True)
        self.controller.mkdir(parents=True, exist_ok=True)
        self._create_android_package()
        self.android.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(self.android_source, self.android)
        self._create_harmony_package()
        self._write_registries()
        write_json(
            self.controller / "scope.json",
            {"ownership": {"migration_controller_id": CONTROLLER}},
        )
        write_rows(
            self.controller / "decision-log.csv",
            template_fields(CONTROLLER_ASSETS / "decision-log.template.csv"),
            [],
        )
        self.input_lock = {"android_evidence": [self.android_record()]}
        self.input_lock_path = self.workspace / "stage-04-input-lock.json"
        self.relock()
        self.write_comparison()

    def cleanup_permissions(self) -> None:
        make_writable(self.android_source)
        make_writable(self.android)
        make_writable(self.harmony)
        for review in (self.workspace / "reviews").glob("*.json"):
            review.chmod(0o644)

    def _create_android_package(self) -> None:
        self.android_source.mkdir(parents=True)
        (self.android_source / "screenshot.png").write_bytes(valid_png())
        write_json(self.android_source / "layout.json", {"root": {"class": "android.view.View"}})
        write_json(
            self.android_source / "metadata.json",
            {
                "evidence_id": ANDROID_EVIDENCE_ID,
                "inventory_id": INVENTORY_ID,
                "feature_id": FEATURE_ID,
                "page_id": PAGE_ID,
                "state_id": STATE_ID,
                "status": "SEALED",
            },
        )
        (self.android_source / "steps.md").write_text("1. Open the login state.\n", encoding="utf-8")
        seal_package(self.android_source, ANDROID_EVIDENCE_ID, harmony=False)

    def _create_harmony_package(self) -> None:
        self.harmony.mkdir(parents=True)
        (self.harmony / "screenshot.png").write_bytes(valid_png())
        write_json(
            self.harmony / "ui-test-snapshot.json",
            {
                "probe_id": f"{PAGE_ID}::{STATE_ID}",
                "components": [
                    {
                        "component_id": "COMP-LOGIN-ROOT",
                        "type": "Column",
                        "text": "Login",
                        "bounds": {"left": 0, "top": 0, "right": 4, "bottom": 3},
                        "visible": True,
                        "enabled": True,
                        "clickable": False,
                        "locator_strategy": "ID",
                        "locator_value": "COMP-LOGIN-ROOT",
                        "match_count": 1,
                    }
                ],
            },
        )
        write_json(
            self.harmony / "assertions.json",
            {
                "assertions": [
                    {
                        "assertion_id": "ASSERT-VISUAL-001",
                        "kind": "VISUAL_STATE",
                        "expected": "visible",
                        "actual": "visible",
                        "status": "PASS",
                    }
                ]
            },
        )
        write_json(
            self.harmony / "metadata.json",
            {
                "evidence_id": HARMONY_EVIDENCE_ID,
                "parity_id": PARITY_ID,
                "android_evidence_id": ANDROID_EVIDENCE_ID,
                "inventory_id": INVENTORY_ID,
                "feature_id": FEATURE_ID,
                "page_id": PAGE_ID,
                "state_id": STATE_ID,
                "captured_by": EXECUTOR,
                "ui_test_snapshot": {
                    "path": "ui-test-snapshot.json",
                    "sha256": sha256_file(self.harmony / "ui-test-snapshot.json"),
                },
            },
        )
        (self.harmony / "steps.md").write_text("1. Open the login state.\n", encoding="utf-8")
        (self.harmony / "logs").mkdir()
        (self.harmony / "logs" / "run.log").write_text("verified\n", encoding="utf-8")
        seal_package(self.harmony, HARMONY_EVIDENCE_ID, harmony=True)

    def _write_registries(self) -> None:
        self.parity_row: dict[str, object] = {
            "parity_id": PARITY_ID,
            "inventory_id": INVENTORY_ID,
            "feature_id": FEATURE_ID,
            "page_id": PAGE_ID,
            "state_id": STATE_ID,
            "source_env_id": "ENV-001",
            "android_evidence_id": ANDROID_EVIDENCE_ID,
            "h4env_id": "H4ENV-001",
            "source_row_key": "SROW-REVIEW-001",
            "harmony_module_id": "HMOD-ENTRY",
            "target_kind": "ROUTE_PAGE",
            "target_id": "HROUTE-LOGIN",
            "harmony_source_refs": '["entry/src/main/ets/pages/Login.ets:1"]',
            "visual_element_ids": json.dumps([VISUAL_ID], separators=(",", ":")),
            "asset_ids": "[]",
            "nativeization_decision_ids": "[]",
            "harmony_evidence_id": HARMONY_EVIDENCE_ID,
            "implemented_by": "phase4-feature-owner",
            "status": "EVIDENCED",
        }
        self.write_parity()
        write_rows(
            self.workspace / "evidence-index.csv",
            template_fields(ASSETS / "evidence-index.template.csv"),
            [
                {
                    "evidence_id": HARMONY_EVIDENCE_ID,
                    "parity_id": PARITY_ID,
                    "inventory_id": INVENTORY_ID,
                    "feature_id": FEATURE_ID,
                    "page_id": PAGE_ID,
                    "state_id": STATE_ID,
                    "h4env_id": "H4ENV-001",
                    "hbuild_id": "HBUILD-001",
                    "android_evidence_id": ANDROID_EVIDENCE_ID,
                    "relative_path": self.harmony_relative.as_posix(),
                    "captured_by": EXECUTOR,
                    "status": "SEALED",
                }
            ],
        )
        write_rows(
            self.workspace / "visual-elements.csv",
            template_fields(ASSETS / "visual-elements.template.csv"),
            [
                {
                    "visual_element_id": VISUAL_ID,
                    "parity_id": PARITY_ID,
                    "element_kind": "PAGE_ROOT",
                    "android_evidence_id": ANDROID_EVIDENCE_ID,
                    "status": "IMPLEMENTED",
                    "implemented_by": "phase4-ui-agent",
                }
            ],
        )
        write_rows(
            self.workspace / "nativeization-decisions.csv",
            template_fields(ASSETS / "nativeization-decisions.template.csv"),
            [],
        )
        write_rows(
            self.workspace / "acceptance-ledger.csv",
            template_fields(ASSETS / "acceptance-ledger.template.csv"),
            [],
        )

    def write_parity(self) -> None:
        write_rows(
            self.workspace / "parity-map.csv",
            template_fields(ASSETS / "parity-map.template.csv"),
            [self.parity_row],
        )

    def android_record(self) -> dict[str, object]:
        package_sha, size, count = package_facts(self.android)
        return {
            "evidence_id": ANDROID_EVIDENCE_ID,
            "inventory_id": INVENTORY_ID,
            "source_path": str(self.android_source.resolve()),
            "snapshot_path": str(self.android.resolve()),
            "manifest_sha256": sha256_file(self.android / "manifest.sha256"),
            "metadata_sha256": sha256_file(self.android / "metadata.json"),
            "screenshot_sha256": sha256_file(self.android / "screenshot.png"),
            "layout_sha256": sha256_file(self.android / "layout.json"),
            "sha256": package_sha,
            "size": size,
            "file_count": count,
        }

    def relock(self) -> None:
        write_json(self.input_lock_path, self.input_lock)
        write_json(
            self.workspace / "phase-manifest.json",
            {
                "phase": 4,
                "status": "IN_PROGRESS",
                "ownership": {
                    "implementation_lead_id": "phase4-implementation-lead",
                    "visual_asset_agent_id": "phase4-visual-assets",
                    "verification_executor_id": EXECUTOR,
                    "parity_acceptance_agent_id": REVIEWER,
                },
                "input_lock_sha256": sha256_file(self.input_lock_path),
            },
        )

    def write_comparison(
        self,
        *,
        visual_result: str = "MATCH",
        functional_result: str = "MATCH",
        asset_result: str = "MATCH",
        reviewed_ids: list[str] | None = None,
        differences: list[dict[str, str]] | None = None,
    ) -> None:
        write_json(
            self.comparison,
            {
                "parity_id": PARITY_ID,
                "visual_result": visual_result,
                "functional_result": functional_result,
                "asset_result": asset_result,
                "reviewed_visual_element_ids": [VISUAL_ID] if reviewed_ids is None else reviewed_ids,
                "differences": [] if differences is None else differences,
                "notes": "",
            },
        )

    def add_approved_difference(self, *, bound_to_parity: bool, controller_approved: bool) -> None:
        self.parity_row["nativeization_decision_ids"] = json.dumps([DECISION_ID], separators=(",", ":"))
        self.write_parity()
        write_rows(
            self.workspace / "nativeization-decisions.csv",
            template_fields(ASSETS / "nativeization-decisions.template.csv"),
            [
                {
                    "decision_id": DECISION_ID,
                    "decision_class": "PLATFORM_VISUAL",
                    "feature_id": FEATURE_ID,
                    "page_id": PAGE_ID,
                    "state_id": STATE_ID,
                    "android_behavior": "four-pixel root",
                    "android_evidence_id": ANDROID_EVIDENCE_ID,
                    "harmony_behavior": "platform inset",
                    "reason": "documented platform difference",
                    "invariants": '["business result unchanged"]',
                    "affected_parity_ids": json.dumps(
                        [PARITY_ID if bound_to_parity else "PAR-OTHER-001"], separators=(",", ":")
                    ),
                    "controller_decision_id": CONTROLLER_DECISION_ID,
                    "approved_by": REVIEWER,
                    "approved_at": "2026-08-23T00:00:00Z",
                    "status": "APPROVED",
                }
            ],
        )
        controller_rows: list[dict[str, object]] = []
        if controller_approved:
            controller_rows.append(
                {
                    "decision_id": CONTROLLER_DECISION_ID,
                    "created_at": "2026-08-23T00:00:00Z",
                    "decision_type": "PARITY_EXCEPTION",
                    "scope": PARITY_ID,
                    "decision": "ALLOW_DOCUMENTED_DIFFERENCE",
                    "rationale": "Frozen platform behavior differs while invariants remain",
                    "decided_by": CONTROLLER,
                    "supersedes_id": "",
                }
            )
        write_rows(
            self.controller / "decision-log.csv",
            template_fields(CONTROLLER_ASSETS / "decision-log.template.csv"),
            controller_rows,
        )
        self.write_comparison(
            visual_result="APPROVED_DIFFERENCE",
            differences=[
                {
                    "dimension": "visual",
                    "android_observation": "content begins at y=0",
                    "harmony_observation": "content begins below platform inset",
                    "decision_id": DECISION_ID,
                }
            ],
        )

    def run(
        self,
        *,
        reviewer: str = REVIEWER,
        decision: str = "ACCEPTED",
        attestations: set[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        selected = {"screenshots", "functional", "assets"} if attestations is None else attestations
        argv = [
            sys.executable,
            str(SCRIPT),
            "--workspace",
            str(self.workspace),
            "--parity-id",
            PARITY_ID,
            "--comparison",
            str(self.comparison),
            "--reviewer",
            reviewer,
            "--decision",
            decision,
        ]
        if "screenshots" in selected:
            argv.append("--attest-opened-both-screenshots")
        if "functional" in selected:
            argv.append("--attest-functional-results")
        if "assets" in selected:
            argv.append("--attest-asset-provenance")
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )


class ReviewParityTests(unittest.TestCase):
    def fixture(self, root: Path) -> ReviewFixture:
        return ReviewFixture(root)

    def assert_rejected_without_write(self, fixture: ReviewFixture, expected: str = "") -> None:
        before = workspace_snapshot(fixture.workspace)
        result = fixture.run()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        if expected:
            self.assertIn(expected, result.stderr)
        self.assertEqual(workspace_snapshot(fixture.workspace), before)
        self.assertEqual(list((fixture.workspace / "reviews").glob("*.json")), [])

    def test_accepted_review_is_read_only_and_hashes_both_evidence_chains(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            fixture = self.fixture(Path(temp_name))
            try:
                result = fixture.run()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                output = json.loads(result.stdout)
                review_path = fixture.workspace / "reviews" / f"{output['review_id']}.json"
                self.assertTrue(review_path.is_file())
                self.assertFalse(review_path.stat().st_mode & 0o222)
                review = json.loads(review_path.read_text(encoding="utf-8"))
                expected_hashes = {
                    "android_manifest_sha256": sha256_file(fixture.android / "manifest.sha256"),
                    "android_screenshot_sha256": sha256_file(fixture.android / "screenshot.png"),
                    "android_layout_sha256": sha256_file(fixture.android / "layout.json"),
                    "harmony_manifest_sha256": sha256_file(fixture.harmony / "manifest.sha256"),
                    "harmony_screenshot_sha256": sha256_file(fixture.harmony / "screenshot.png"),
                    "harmony_ui_test_snapshot_sha256": sha256_file(fixture.harmony / "ui-test-snapshot.json"),
                    "harmony_assertions_sha256": sha256_file(fixture.harmony / "assertions.json"),
                }
                for field, expected in expected_hashes.items():
                    self.assertEqual(review[field], expected, field)
                acceptance = read_rows(fixture.workspace / "acceptance-ledger.csv")
                self.assertEqual(len(acceptance), 1)
                self.assertEqual(acceptance[0]["status"], "ACCEPTED")
                self.assertEqual(acceptance[0]["comparison_sha256"], sha256_file(review_path))
                self.assertEqual(read_rows(fixture.workspace / "parity-map.csv")[0]["status"], "ACCEPTED")
            finally:
                fixture.cleanup_permissions()

    def test_wrong_reviewer_is_rejected_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            fixture = self.fixture(Path(temp_name))
            try:
                before = workspace_snapshot(fixture.workspace)
                result = fixture.run(reviewer="wrong-reviewer")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Only the frozen parity acceptance agent", result.stderr)
                self.assertEqual(workspace_snapshot(fixture.workspace), before)
            finally:
                fixture.cleanup_permissions()

    def test_each_missing_attestation_is_rejected_without_writes(self) -> None:
        for missing in ("screenshots", "functional", "assets"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temp_name:
                fixture = self.fixture(Path(temp_name))
                try:
                    before = workspace_snapshot(fixture.workspace)
                    result = fixture.run(attestations={"screenshots", "functional", "assets"} - {missing})
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("all three review attestations", result.stderr)
                    self.assertEqual(workspace_snapshot(fixture.workspace), before)
                finally:
                    fixture.cleanup_permissions()

    def test_android_package_digest_and_screenshot_tampering_are_rejected(self) -> None:
        for case in ("package-digest", "screenshot"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp_name:
                fixture = self.fixture(Path(temp_name))
                try:
                    record = fixture.input_lock["android_evidence"][0]
                    if case == "package-digest":
                        record["sha256"] = "0" * 64
                    else:
                        old_screenshot_sha = record["screenshot_sha256"]
                        make_writable(fixture.android)
                        (fixture.android / "screenshot.png").write_bytes(valid_png(red=200))
                        seal_package(fixture.android, ANDROID_EVIDENCE_ID, harmony=False)
                        record.update(fixture.android_record())
                        record["screenshot_sha256"] = old_screenshot_sha
                    fixture.relock()
                    self.assert_rejected_without_write(fixture, "differs from the input lock")
                finally:
                    fixture.cleanup_permissions()

    def test_comparison_must_cover_every_visual_element(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            fixture = self.fixture(Path(temp_name))
            try:
                fixture.write_comparison(reviewed_ids=[])
                self.assert_rejected_without_write(fixture, "exactly every visual element")
            finally:
                fixture.cleanup_permissions()

    def test_mismatch_cannot_be_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            fixture = self.fixture(Path(temp_name))
            try:
                fixture.write_comparison(
                    visual_result="MISMATCH",
                    differences=[
                        {
                            "dimension": "visual",
                            "android_observation": "dark icon",
                            "harmony_observation": "white glyph",
                        }
                    ],
                )
                self.assert_rejected_without_write(fixture, "nonpassing result")
            finally:
                fixture.cleanup_permissions()

    def test_approved_difference_requires_current_parity_and_controller_approval(self) -> None:
        for case, bound, controller_approved, expected in (
            ("wrong-parity", False, True, "approved nativeization decision"),
            ("no-controller-approval", True, False, "real controller approval"),
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp_name:
                fixture = self.fixture(Path(temp_name))
                try:
                    fixture.add_approved_difference(
                        bound_to_parity=bound,
                        controller_approved=controller_approved,
                    )
                    self.assert_rejected_without_write(fixture, expected)
                finally:
                    fixture.cleanup_permissions()

    def test_failing_harmony_assertion_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            fixture = self.fixture(Path(temp_name))
            try:
                make_writable(fixture.harmony)
                value = json.loads((fixture.harmony / "assertions.json").read_text(encoding="utf-8"))
                value["assertions"][0]["status"] = "FAIL"
                write_json(fixture.harmony / "assertions.json", value)
                seal_package(fixture.harmony, HARMONY_EVIDENCE_ID, harmony=True)
                self.assert_rejected_without_write(fixture, "malformed or failing")
            finally:
                fixture.cleanup_permissions()

    def test_closed_workspace_is_rejected_with_zero_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            fixture = self.fixture(Path(temp_name))
            try:
                (fixture.workspace / "CLOSED").write_text("sealed\n", encoding="utf-8")
                before = workspace_snapshot(fixture.workspace)
                result = fixture.run()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("workspace is missing or CLOSED", result.stderr)
                self.assertEqual(workspace_snapshot(fixture.workspace), before)
                self.assertEqual(list((fixture.workspace / "reviews").glob("*.json")), [])
            finally:
                fixture.cleanup_permissions()


if __name__ == "__main__":
    unittest.main()
