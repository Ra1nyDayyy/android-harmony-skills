#!/usr/bin/env python3
"""Record one independent Android-to-Harmony parity review with both evidence chains."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
from pathlib import Path
from typing import Any

from _common import (
    atomic_json,
    csv_fieldnames,
    exclusive_lock,
    load_json,
    png_dimensions,
    read_csv,
    safe_relative_path,
    sha256_file,
    split_multi,
    utc_now,
    validate_actor,
    validate_id,
    verify_manifest,
    write_csv,
)


SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSETS = SKILL_ROOT / "assets"
ACCEPTABLE_RESULTS = {"MATCH", "APPROVED_DIFFERENCE"}
REVIEW_RESULTS = ACCEPTABLE_RESULTS | {"MISMATCH"}


def package_files(directory: Path) -> set[str]:
    return {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and path.name not in {"manifest.sha256", "COMMITTED"}
    }


def package_summary(directory: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in evidence: {path}")
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(directory).as_posix(),
                    "sha256": sha256_file(path),
                    "size": path.stat().st_size,
                }
            )
    canonical = json.dumps(
        entries, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return {
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "size": sum(item["size"] for item in entries),
        "file_count": len(entries),
    }


def committed(directory: Path, expected_id: str) -> None:
    marker = directory / "COMMITTED"
    if not marker.is_file() or not marker.read_text(encoding="utf-8").strip().startswith(
        expected_id
    ):
        raise ValueError(f"Evidence COMMITTED marker does not bind {expected_id}")
    failures = verify_manifest(directory, package_files(directory))
    if failures:
        raise ValueError("Evidence manifest failed: " + "; ".join(failures))
    for path in (directory, *directory.rglob("*")):
        if path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"Sealed evidence is writable: {path}")


def ownership_from(manifest: dict[str, Any]) -> dict[str, str]:
    value = manifest.get("ownership")
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items()}
    legacy = manifest.get("roles")
    if isinstance(legacy, dict):
        return {
            "implementation_lead_id": str(legacy.get("implementation_lead", "")),
            "visual_asset_agent_id": str(legacy.get("asset_agent", "")),
            "verification_executor_id": str(legacy.get("verification_executor", "")),
            "parity_acceptance_agent_id": str(legacy.get("parity_checker", "")),
        }
    return {}


def decision_ids_are_approved(
    workspace: Path,
    parity_id: str,
    reviewer: str,
    decision_ids: set[str],
    controller_id: str,
) -> None:
    if not decision_ids:
        return
    local = {
        row.get("decision_id", ""): row
        for row in read_csv(workspace / "nativeization-decisions.csv")
    }
    controller_rows = read_csv(workspace.parent / "controller" / "decision-log.csv")
    controller = {row.get("decision_id", ""): row for row in controller_rows}
    superseded_controller_ids = {
        row.get("supersedes_id", "") for row in controller_rows if row.get("supersedes_id", "")
    }
    for decision_id in decision_ids:
        local_row = local.get(decision_id)
        if (
            not local_row
            or local_row.get("status") != "APPROVED"
            or local_row.get("approved_by") != reviewer
            or parity_id not in split_multi(local_row.get("affected_parity_ids", ""))
            or local_row.get("decision_class") != "PLATFORM_VISUAL"
        ):
            raise ValueError(
                f"Difference lacks an approved nativeization decision limited to "
                f"PLATFORM_VISUAL: {decision_id}"
            )
        controller_decision_id = local_row.get("controller_decision_id", "")
        controller_row = controller.get(controller_decision_id)
        if (
            not controller_decision_id
            or not controller_row
            or controller_decision_id in superseded_controller_ids
            or controller_row.get("decided_by") != controller_id
            or not str(controller_row.get("decision", "")).strip()
            or not str(controller_row.get("rationale", "")).strip()
        ):
            raise ValueError(f"Nativeization decision lacks real controller approval: {decision_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--parity-id", required=True)
    parser.add_argument("--comparison", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--decision", required=True, choices=("ACCEPTED", "REWORK"))
    parser.add_argument("--attest-opened-both-screenshots", action="store_true")
    parser.add_argument("--attest-functional-results", action="store_true")
    parser.add_argument("--attest-asset-provenance", action="store_true")
    args = parser.parse_args()

    workspace_input = Path(args.workspace).expanduser().absolute()
    if workspace_input.is_symlink():
        parser.error("Workspace must not be a symbolic link")
    workspace = workspace_input.resolve()
    try:
        manifest = load_json(workspace / "phase-manifest.json")
        if manifest.get("phase") != 4 or (workspace / "CLOSED").exists():
            raise ValueError("Phase 4 workspace is missing or CLOSED")
        input_lock = load_json(workspace / "stage-04-input-lock.json")
        if sha256_file(workspace / "stage-04-input-lock.json") != manifest.get(
            "input_lock_sha256"
        ):
            raise ValueError("Phase 4 input lock changed after initialization")
        ownership = ownership_from(manifest)
        reviewer = validate_actor(args.reviewer, "parity acceptance agent")
        if reviewer != ownership.get("parity_acceptance_agent_id"):
            raise ValueError("Only the frozen parity acceptance agent may review parity")
        if reviewer in {
            ownership.get("implementation_lead_id"),
            ownership.get("visual_asset_agent_id"),
            ownership.get("verification_executor_id"),
        }:
            raise ValueError("Parity reviewer conflicts with a Phase 4 creator/executor")
        if not (
            args.attest_opened_both_screenshots
            and args.attest_functional_results
            and args.attest_asset_provenance
        ):
            raise ValueError("A parity review requires all three review attestations")
        parity_id = validate_id(args.parity_id, "Parity-ID")
        parity_rows = read_csv(workspace / "parity-map.csv")
        parity = next((row for row in parity_rows if row.get("parity_id") == parity_id), None)
        if not parity or parity.get("status") not in {"EVIDENCED", "ACCEPTED", "REWORK"}:
            raise ValueError(f"Parity row is not ready for review: {parity_id}")
        harmony_evidence_id = validate_id(
            str(parity.get("harmony_evidence_id", "")), "HEVD-ID"
        )
        android_evidence_id = validate_id(
            str(parity.get("android_evidence_id", "")), "Android Evidence-ID"
        )
        index = next(
            (
                row for row in read_csv(workspace / "evidence-index.csv")
                if row.get("evidence_id") == harmony_evidence_id
                and row.get("parity_id") == parity_id
                and row.get("status") == "SEALED"
            ),
            None,
        )
        if not index:
            raise ValueError("Parity has no active sealed Harmony evidence")
        harmony_dir = safe_relative_path(
            workspace, str(index.get("relative_path", "")), "Harmony evidence package"
        )
        android_dir = safe_relative_path(
            workspace / "inputs" / "android-evidence",
            android_evidence_id,
            "copied Android evidence package",
        )
        committed(harmony_dir, harmony_evidence_id)
        committed(android_dir, android_evidence_id)

        package_records = input_lock.get("android_evidence")
        package_record = next(
            (
                item for item in package_records
                if isinstance(item, dict) and item.get("evidence_id") == android_evidence_id
            ),
            None,
        ) if isinstance(package_records, list) else None
        if not isinstance(package_record, dict):
            raise ValueError("Android evidence is absent from the frozen Phase 4 input lock")
        if Path(str(package_record.get("snapshot_path", ""))).resolve() != android_dir:
            raise ValueError("Android evidence path differs from the frozen Phase 4 input lock")
        summary = package_summary(android_dir)
        if any(package_record.get(field) != summary[field] for field in summary):
            raise ValueError("Android evidence package digest/size/count differs from the input lock")
        for field, path in (
            ("manifest_sha256", android_dir / "manifest.sha256"),
            ("metadata_sha256", android_dir / "metadata.json"),
            ("screenshot_sha256", android_dir / "screenshot.png"),
            ("layout_sha256", android_dir / "layout.json"),
        ):
            if not path.is_file() or sha256_file(path) != package_record.get(field):
                raise ValueError(f"Android evidence {field} differs from the input lock")

        android_screenshot = android_dir / "screenshot.png"
        android_layout = android_dir / "layout.json"
        harmony_screenshot = harmony_dir / "screenshot.png"
        harmony_tree = harmony_dir / "ui-test-snapshot.json"
        harmony_assertions = harmony_dir / "assertions.json"
        for path in (
            android_screenshot, android_layout, harmony_screenshot, harmony_tree,
            harmony_assertions,
        ):
            if not path.is_file():
                raise ValueError(f"Review evidence file is missing: {path}")

        android_metadata = load_json(android_dir / "metadata.json")
        harmony_metadata = load_json(harmony_dir / "metadata.json")
        for field in ("inventory_id", "feature_id", "page_id", "state_id"):
            if str(android_metadata.get(field, "")) != str(parity.get(field, "")):
                raise ValueError(f"Android evidence {field} differs from parity")
            if str(harmony_metadata.get(field, "")) != str(parity.get(field, "")):
                raise ValueError(f"Harmony evidence {field} differs from parity")
        if (
            android_metadata.get("evidence_id") != android_evidence_id
            or harmony_metadata.get("evidence_id") != harmony_evidence_id
            or harmony_metadata.get("parity_id") != parity_id
            or harmony_metadata.get("android_evidence_id") != android_evidence_id
            or harmony_metadata.get("captured_by")
            != ownership.get("verification_executor_id")
        ):
            raise ValueError("Evidence metadata identity or executor differs")
        png_dimensions(android_screenshot)
        png_dimensions(harmony_screenshot)
        android_layout_value = load_json(android_layout)
        harmony_tree_value = load_json(harmony_tree)
        assertion_value = load_json(harmony_assertions)
        if android_layout_value in ({}, [], None):
            raise ValueError("Android layout evidence is empty")
        snapshot_binding = harmony_metadata.get("ui_test_snapshot")
        components = (
            harmony_tree_value.get("components")
            if isinstance(harmony_tree_value, dict) else None
        )
        if (
            not isinstance(snapshot_binding, dict)
            or snapshot_binding.get("path") != "ui-test-snapshot.json"
            or snapshot_binding.get("sha256") != sha256_file(harmony_tree)
            or not isinstance(harmony_tree_value, dict)
            or harmony_tree_value.get("probe_id")
            != f"{parity.get('page_id')}::{parity.get('state_id')}"
            or not isinstance(components, list)
            or not components
        ):
            raise ValueError("Harmony UiTest component snapshot evidence is incomplete")
        generated_assertions = (
            assertion_value.get("assertions") if isinstance(assertion_value, dict) else None
        )
        if not isinstance(generated_assertions, list) or not generated_assertions:
            raise ValueError("Harmony assertion evidence is missing")
        for assertion in generated_assertions:
            if (
                not isinstance(assertion, dict)
                or not str(assertion.get("assertion_id", ""))
                or assertion.get("actual") in (None, "")
                or assertion.get("status") != "PASS"
            ):
                raise ValueError("Harmony assertion evidence is malformed or failing")

        comparison = load_json(Path(args.comparison).expanduser().resolve())
        if not isinstance(comparison, dict) or comparison.get("parity_id") != parity_id:
            raise ValueError("Comparison must be an object for the selected Parity-ID")
        for field in ("visual_result", "functional_result", "asset_result"):
            if comparison.get(field) not in REVIEW_RESULTS:
                raise ValueError(
                    f"Comparison {field} must be MATCH, APPROVED_DIFFERENCE, or MISMATCH"
                )
        if comparison.get("functional_result") != "MATCH" or comparison.get("asset_result") != "MATCH":
            raise ValueError(
                "Functional and asset semantics are non-waivable; only a platform visual offset "
                "may be an approved difference"
            )
        visual_ids = set(split_multi(parity.get("visual_element_ids", "")))
        reviewed_ids = comparison.get("reviewed_visual_element_ids")
        if not isinstance(reviewed_ids, list) or set(reviewed_ids) != visual_ids:
            raise ValueError("Comparison must review exactly every visual element of the parity row")
        differences = comparison.get("differences")
        if not isinstance(differences, list) or any(not isinstance(item, dict) for item in differences):
            raise ValueError("Comparison differences must be an object array")
        declared_difference = any(
            comparison.get(field) in {"APPROVED_DIFFERENCE", "MISMATCH"}
            for field in ("visual_result", "functional_result", "asset_result")
        )
        if declared_difference != bool(differences):
            raise ValueError("APPROVED_DIFFERENCE and the differences array must agree")
        difference_dimensions = {
            str(item.get("dimension", "")).lower() for item in differences
        }
        nonmatch_dimensions = {
            field.removesuffix("_result")
            for field in ("visual_result", "functional_result", "asset_result")
            if comparison.get(field) != "MATCH"
        }
        if difference_dimensions != nonmatch_dimensions:
            raise ValueError("Comparison differences must cover exactly every non-MATCH dimension")
        for item in differences:
            for field in ("dimension", "android_observation", "harmony_observation"):
                if not str(item.get(field, "")).strip():
                    raise ValueError(f"Comparison difference lacks {field}")
            dimension = str(item["dimension"]).lower()
            if dimension not in {"visual", "functional", "asset"}:
                raise ValueError(f"Comparison difference has unknown dimension: {dimension}")
            if comparison.get(f"{dimension}_result") == "APPROVED_DIFFERENCE":
                validate_id(str(item.get("decision_id", "")), "Decision-ID")
            elif item.get("decision_id"):
                validate_id(str(item["decision_id"]), "Decision-ID")
        declared_decisions = set(split_multi(parity.get("nativeization_decision_ids", "")))
        for visual in read_csv(workspace / "visual-elements.csv"):
            if visual.get("parity_id") == parity_id and visual.get("nativeization_decision_id"):
                declared_decisions.add(visual["nativeization_decision_id"])
        approved_difference_decisions = {
            validate_id(str(item["decision_id"]), "Decision-ID")
            for item in differences
            if comparison.get(str(item.get("dimension", "")).lower() + "_result")
            == "APPROVED_DIFFERENCE"
        }
        if approved_difference_decisions - declared_decisions:
            raise ValueError("Comparison cites a decision not bound to this parity row")
        decision_ids_are_approved(
            workspace,
            parity_id,
            reviewer,
            approved_difference_decisions,
            str(load_json(workspace.parent / "controller" / "scope.json").get("ownership", {}).get(
                "migration_controller_id", ""
            )),
        )
        if args.decision == "ACCEPTED" and any(
            comparison.get(field) not in ACCEPTABLE_RESULTS
            for field in ("visual_result", "functional_result", "asset_result")
        ):
            raise ValueError("ACCEPTED comparison has a nonpassing result")
        if args.decision == "REWORK":
            if not differences and not str(comparison.get("notes", "")).strip():
                raise ValueError("REWORK requires an explicit difference or review note")
        elif any(comparison.get(field) == "MISMATCH" for field in (
            "visual_result", "functional_result", "asset_result"
        )):
            raise ValueError("ACCEPTED review cannot contain MISMATCH")
    except (ValueError, OSError, UnicodeDecodeError) as exc:
        parser.error(str(exc))

    reviewed_at = utc_now()
    material = "|".join(
        [parity_id, harmony_evidence_id, sha256_file(Path(args.comparison).expanduser().resolve()), reviewed_at]
    )
    review_id = "HREV-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20].upper()
    review_path = workspace / "reviews" / f"{review_id}.json"
    if review_path.exists():
        parser.error(f"Review-ID already exists: {review_id}")

    review_record = {
        **comparison,
        "reviewed_visual_element_ids": sorted(reviewed_ids),
        "review_id": review_id,
        "inventory_id": parity["inventory_id"],
        "android_evidence_id": android_evidence_id,
        "harmony_evidence_id": harmony_evidence_id,
        "android_manifest_sha256": sha256_file(android_dir / "manifest.sha256"),
        "android_screenshot_sha256": sha256_file(android_screenshot),
        "android_layout_sha256": sha256_file(android_layout),
        "harmony_manifest_sha256": sha256_file(harmony_dir / "manifest.sha256"),
        "harmony_screenshot_sha256": sha256_file(harmony_screenshot),
        "harmony_ui_test_snapshot_sha256": sha256_file(harmony_tree),
        "harmony_assertions_sha256": sha256_file(harmony_assertions),
        "reviewer_id": reviewer,
        "reviewed_at": reviewed_at,
        "decision": args.decision,
        "attestations": {
            "opened_both_screenshots": True,
            "functional_results": True,
            "asset_provenance": True,
        },
    }
    atomic_json(review_path, review_record)
    review_path.chmod(0o444)

    acceptance_path = workspace / "acceptance-ledger.csv"
    parity_path = workspace / "parity-map.csv"
    acceptance_fields = csv_fieldnames(ASSETS / "acceptance-ledger.template.csv")
    parity_fields = csv_fieldnames(ASSETS / "parity-map.template.csv")
    lock = workspace / ".locks" / "phase4-acceptance.lock"
    try:
        with exclusive_lock(lock):
            acceptance_rows = read_csv(acceptance_path)
            parity_rows = read_csv(parity_path)
            current = [
                row for row in acceptance_rows
                if row.get("parity_id") == parity_id and row.get("status") != "SUPERSEDED"
            ]
            if len(current) > 1:
                raise ValueError("Parity has more than one active review")
            supersedes_review_id = current[0].get("review_id", "") if current else ""
            for row in current:
                row["status"] = "SUPERSEDED"
            acceptance_rows.append(
                {
                    "review_id": review_id,
                    "parity_id": parity_id,
                    "inventory_id": parity["inventory_id"],
                    "android_evidence_id": android_evidence_id,
                    "harmony_evidence_id": harmony_evidence_id,
                    "android_manifest_sha256": review_record["android_manifest_sha256"],
                    "android_screenshot_sha256": review_record["android_screenshot_sha256"],
                    "android_layout_sha256": review_record["android_layout_sha256"],
                    "harmony_manifest_sha256": review_record["harmony_manifest_sha256"],
                    "harmony_screenshot_sha256": review_record["harmony_screenshot_sha256"],
                    "harmony_ui_test_snapshot_sha256": review_record["harmony_ui_test_snapshot_sha256"],
                    "harmony_assertions_sha256": review_record["harmony_assertions_sha256"],
                    "comparison_sha256": sha256_file(review_path),
                    "reviewer_id": reviewer,
                    "reviewed_at": reviewed_at,
                    "status": args.decision,
                    "supersedes_review_id": supersedes_review_id,
                    "notes": str(comparison.get("notes", "")),
                }
            )
            target = next(row for row in parity_rows if row.get("parity_id") == parity_id)
            if target.get("harmony_evidence_id") != harmony_evidence_id:
                raise ValueError("Parity evidence changed while the review was in progress")
            target["status"] = args.decision
            old_acceptance = read_csv(acceptance_path)
            write_csv(acceptance_path, acceptance_fields, acceptance_rows)
            try:
                write_csv(parity_path, parity_fields, parity_rows)
            except Exception:
                write_csv(acceptance_path, acceptance_fields, old_acceptance)
                raise
    except Exception as exc:
        try:
            review_path.chmod(0o644)
            review_path.unlink()
        except OSError:
            pass
        parser.error(f"Review registry commit failed; no review was issued: {exc}")

    print(json.dumps({"review_id": review_id, "decision": args.decision}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
