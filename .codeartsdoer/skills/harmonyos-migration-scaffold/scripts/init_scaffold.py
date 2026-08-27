#!/usr/bin/env python3
"""Initialize Phase 3 (gmi path only) from a frozen gmi Phase 2 closure.

Input contract (the only accepted entry): the run directory produced by the
gmi Phase 2 chain — `phase-2-closure.json` + `candidates/` 13 tables +
`runtime-evidence/` (evidence-index.csv, runtime-gate.csv, audit-replay.csv)
+ `coverage/coverage-ledger.csv`, synthesized into `phase-02-android-inventory/`
by `gmi_phase3_adapter.py`. The former controller/REVIEWED-chain entry is
rejected.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import re
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from _common import (
    atomic_json,
    atomic_text,
    csv_fieldnames,
    join_multi,
    load_json,
    read_csv,
    sha256_file,
    sha256_text,
    safe_relative_path,
    source_row_key,
    utc_now,
    validate_id,
    write_csv,
)


SKILL_ROOT = Path(__file__).resolve().parents[1]
ASSETS = SKILL_ROOT / "assets"
ARKUI_TEMPLATE = ASSETS / "arkui-stage-template"
PHASE_NAME = "phase-03-harmony-scaffold"
PHASE3_ROLES = (
    "architecture_lead_id",
    "toolchain_agent_id",
    "navigation_agent_id",
    "public_ui_agent_id",
    "capability_contract_agent_id",
    "architecture_acceptance_agent_id",
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TEMPLATE_EXCLUDES = {
    ".git", ".idea", ".hvigor", "oh_modules", "node_modules", "build", "dist",
    ".preview", "local.properties", "oh-package-lock.json5", ".DS_Store",
}
TEMPLATE_REQUIRED = {
    "AppScope/app.json5", "build-profile.json5", "oh-package.json5",
    "entry/build-profile.json5", "entry/src/main/module.json5",
    "entry/src/main/ets/entryability/EntryAbility.ets",
    "entry/src/main/ets/pages/Index.ets",
}
# Carrier kinds that can never become a HarmonyOS route (non-routable surfaces).
NON_ROUTE_CARRIER_KINDS = {
    "DIALOG", "BOTTOM_SHEET", "SHEET", "OVERLAY", "WIDGET", "POPUP", "PICKER",
    "EMBEDDED", "TAB_BODY",
}
# Carrier kinds proven to be independently navigable on Android.
ROUTABLE_CARRIER_KINDS = {"ACTIVITY", "SCREEN", "PAGE", "VIEW"}
# A3 mapping decision: a Jetpack Compose screen carrier is normalized to the
# SCREEN semantics (static analysis proved it independently navigable), so it
# routes like any other routable page. The COMPOSABLE->SCREEN decision trail
# is recorded on the seeded mapping rows (architecture-map/route-registry notes).
COMPOSABLE_CARRIER_KIND = "COMPOSABLE"
COMPOSABLE_SCREEN_KIND = "SCREEN"


def parse_refs(value: str) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return [item.strip() for item in value.split(";") if item.strip()]
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError(f"Inventory reference cell must be an array of strings: {value}")
    return [item for item in parsed if item]


def requirement_id(feature_id: str, source_kind: str, source_ref: str) -> str:
    digest = sha256_text(f"{feature_id}|{source_kind}|{source_ref}")[:20].upper()
    return f"HREQ-{digest}"


def copy_template_csv(temp_dir: Path, source_name: str, target_name: str) -> None:
    shutil.copyfile(ASSETS / source_name, temp_dir / target_name)


def template_files(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"ArkUI Stage template is missing or unsafe: {root}")
    result: list[Path] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in TEMPLATE_EXCLUDES for part in relative.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Symbolic links are prohibited in the ArkUI template: {path}")
        if path.is_file():
            result.append(path)
    names = {path.relative_to(root).as_posix() for path in result}
    missing = TEMPLATE_REQUIRED - names
    if missing:
        raise ValueError(f"ArkUI Stage template lacks required files: {sorted(missing)}")
    return result


def template_manifest(root: Path) -> str:
    return "".join(
        f"{sha256_file(path)}  {path.relative_to(root).as_posix()}\n"
        for path in template_files(root)
    )


def create_arkui_project(template: Path, destination: Path, replacements: dict[str, str]) -> dict[str, Any]:
    destination.mkdir()
    files = template_files(template)
    text_suffixes = {".json", ".json5", ".ets", ".ts", ".txt", ".md", ".csv"}
    for source in files:
        relative = source.relative_to(template)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.suffix.lower() in text_suffixes or source.name == ".gitignore":
            text = source.read_text(encoding="utf-8")
            for token, value in replacements.items():
                text = text.replace(token, value)
            with target.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        else:
            shutil.copyfile(source, target)
    unresolved = []
    for path in destination.rglob("*"):
        if path.is_file() and (path.suffix.lower() in text_suffixes or path.name == ".gitignore"):
            if re.search(r"__[A-Z0-9_]+__", path.read_text(encoding="utf-8")):
                unresolved.append(path.relative_to(destination).as_posix())
    if unresolved:
        raise ValueError(f"Unresolved ArkUI template tokens remain: {unresolved}")
    generated = "".join(
        f"{sha256_file(path)}  {path.relative_to(destination).as_posix()}\n"
        for path in sorted(destination.rglob("*")) if path.is_file()
    )
    return {
        "schema_version": 1,
        "template_id": "ARKUI-STAGE-TEMPLATE-V1",
        "template_manifest_sha256": hashlib.sha256(template_manifest(template).encode("utf-8")).hexdigest(),
        "generated_file_count": len(files),
        "generated_project_manifest_sha256": hashlib.sha256(generated.encode("utf-8")).hexdigest(),
        "required_files": sorted(TEMPLATE_REQUIRED),
        "bundle_name": replacements["__BUNDLE_NAME__"],
        "app_name": replacements["__APP_NAME__"],
        "vendor": replacements["__VENDOR__"],
        "log_tag": replacements["__LOG_TAG__"],
    }


def canonical_input(value: str, label: str) -> Path:
    raw = Path(value).expanduser().absolute()
    if raw.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link: {raw}")
    return raw.resolve()


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def validate_gmi_ownership(work_order: dict[str, Any]) -> dict[str, str]:
    """Six logical Phase 3 responsibilities must be named; IDs may be reused
    by one worker (logical-role policy), but every role key needs an owner."""
    ownership = require_object(work_order.get("ownership"), "Phase 3 work-order ownership")
    normalized: dict[str, str] = {}
    for role in PHASE3_ROLES:
        actor = ownership.get(role)
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError(f"Phase 3 work order is missing ownership.{role}")
        normalized[role] = actor.strip()
    return normalized


def catalog_index(
    path: Path,
    id_field: str,
    sentinel: Callable[[dict[str, str]], bool],
) -> tuple[dict[str, dict[str, str]], set[str]]:
    rows = read_csv(path)
    indexed: dict[str, dict[str, str]] = {}
    sentinels: set[str] = set()
    for row in rows:
        identifier = row.get(id_field, "")
        if not identifier or identifier in indexed:
            raise ValueError(f"Missing or duplicate {id_field} in {path.name}: {identifier!r}")
        indexed[identifier] = row
        if sentinel(row):
            sentinels.add(identifier)
    return indexed, sentinels


def input_record(
    source: Path,
    snapshot: Path | None = None,
    *,
    use_snapshot_as_path: bool = False,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": str(snapshot if use_snapshot_as_path and snapshot is not None else source),
        "sha256": sha256_file(source),
    }
    if snapshot is not None:
        record["snapshot_path"] = str(snapshot)
    if use_snapshot_as_path:
        record["source_path"] = str(source)
    return record


def _sanitize_pid(sym: str) -> str:
    import re as _re2
    return _re2.sub(r"[^A-Z0-9a-z]", "-", sym or "")[:64].strip("-") or "X"


def gmi_carrier_kinds(phase2: Path) -> dict[str, set[str]]:
    """page_id -> carrier kinds, read from the adapter's static-analysis pages.json."""
    pages_path = phase2 / "static-analysis" / "pages.json"
    kinds: dict[str, set[str]] = {}
    if pages_path.is_file():
        pages = require_object(load_json(pages_path), "Phase 2 pages.json").get("pages", [])
        if isinstance(pages, list):
            for page in pages:
                if not isinstance(page, dict):
                    continue
                page_id = str(page.get("page_id", ""))
                raw = page.get("kinds", [])
                if page_id and isinstance(raw, list):
                    kinds[page_id] = {str(item).upper() for item in raw if item}
    return kinds


def gmi_mapping_type(page_id: str, kinds: set[str]) -> str:
    """Decide mapping_type from the Phase 2 carrier evidence; never default silently.

    - dialog/sheet/overlay/widget/... carriers are non-routable -> VISUAL_SURFACE;
    - only proven routable carriers (activity/screen/...) become ROUTE_PAGE;
    - COMPOSABLE is normalized to the SCREEN semantics and treated as routable
      (A3 decision; the COMPOSABLE->SCREEN trail is recorded on the mapping rows
      by the caller via ``composable_screen_decision``);
    - an unknown, empty, or ambiguous carrier is BLOCKED with the missing field named.
    """
    non_route = kinds & NON_ROUTE_CARRIER_KINDS
    if non_route:
        return "VISUAL_SURFACE"
    # A3: COMPOSABLE -> SCREEN normalization before the routability check.
    effective = set(kinds)
    if COMPOSABLE_CARRIER_KIND in effective:
        effective.discard(COMPOSABLE_CARRIER_KIND)
        effective.add(COMPOSABLE_SCREEN_KIND)
    routable = effective & ROUTABLE_CARRIER_KINDS
    if routable and routable == effective:
        return "ROUTE_PAGE"
    detail = f"page_id={page_id}, carrier kinds={sorted(kinds) or 'missing'}"
    raise ValueError(
        "BLOCKED - carrier-undecidable: cannot decide ROUTE_PAGE vs VISUAL_SURFACE "
        f"from Phase 2 static-analysis pages.json kinds ({detail}); "
        "add an explicit carrier kind (ACTIVITY/SCREEN/PAGE/VIEW/COMPOSABLE for "
        "routable, DIALOG/BOTTOM_SHEET/OVERLAY/WIDGET/... for non-routable surfaces)"
    )


def composable_screen_decision(kinds: set[str]) -> str:
    """Decision trail marker for the seeded mapping rows when the A3
    COMPOSABLE->SCREEN normalization fired for this page's carrier kinds."""
    if COMPOSABLE_CARRIER_KIND in (kinds or set()):
        return "gmi auto; carrier kind COMPOSABLE->SCREEN (A3 routable)"
    return "gmi auto"


def _fill_gmi_registries(
    temp_dir: Path,
    architecture_rows: list[dict[str, str]],
    source_rows: list[dict[str, str]],
    carrier_kinds: dict[str, set[str]],
    ownership: dict[str, str],
) -> None:
    """gmi mode: deterministically seed module/route/surface/architecture-map.

    One `entry` module; per inventory page exactly one landing shell side:
    ROUTE_PAGE pages get a route row (no surface), VISUAL_SURFACE pages get a
    surface row (no route). mapping_type comes from the Phase 2 carrier kinds.
    """
    mod_fields = csv_fieldnames(ASSETS / "module-registry.template.csv")
    rt_fields = csv_fieldnames(ASSETS / "route-registry.template.csv")
    sf_fields = csv_fieldnames(ASSETS / "surface-registry.template.csv")

    rows_module = [{
        "harmony_module_id": "ENTRY", "module_name": "entry",
        "layer": "app", "module_path": "entry", "build_config_path": "entry/build-profile.json5",
        "feature_ids": join_multi({row.get("feature_id", "") for row in source_rows}),
        "declared_dependencies": "", "created_by": ownership["toolchain_agent_id"],
        "status": "READY", "notes": "gmi auto",
    }]
    rows_route: list[dict[str, str]] = []
    rows_surface: list[dict[str, str]] = []
    for row in source_rows:
        inv_id = row.get("inventory_id", "")
        page_id = row.get("page_id", "")
        feat = row.get("feature_id", "")
        sym = row.get("page_name", inv_id).replace("INV-", "")
        sym_id = _sanitize_pid(sym).upper()
        rt_id = f"ROUTE-{sym_id}"
        sf_id = f"SURFACE-{sym_id}"
        pshell_id = f"PAGESHELL-{sym_id}"
        mapping_type = gmi_mapping_type(page_id, carrier_kinds.get(page_id, set()))
        # A3 decision trail: when COMPOSABLE was normalized to SCREEN, keep the
        # COMPOSABLE->SCREEN evidence on the seeded mapping rows (this function
        # has no other log/CSV channel; the row notes are the audit trail).
        trail = composable_screen_decision(carrier_kinds.get(page_id, set()))
        if mapping_type == "ROUTE_PAGE":
            rows_route.append({
                "route_id": rt_id, "page_id": page_id,
                "page_shell_id": pshell_id,
                "harmony_module_id": "ENTRY", "route_pattern": sym.lower() + "/index",
                "registry_file": "entry/src/main/ets/pages/" + sym.lower().replace("-", "_") + ".ets",
                "registry_symbol": sym_id, "page_shell_file": "",
                "feature_ids": feat, "created_by": ownership["navigation_agent_id"],
                "status": "READY", "notes": trail,
            })
        else:
            surface_kind = sorted(carrier_kinds.get(page_id, set()) & NON_ROUTE_CARRIER_KINDS)[0]
            rows_surface.append({
                "surface_shell_id": sf_id, "page_id": page_id,
                "page_shell_id": pshell_id,
                "harmony_module_id": "ENTRY", "surface_kind": surface_kind,
                "surface_file": "", "surface_symbol": sym_id,
                "feature_ids": feat, "created_by": ownership["navigation_agent_id"],
                "status": "READY", "notes": trail,
            })
        for ar in architecture_rows:
            if ar.get("inventory_id") == inv_id:
                ar["harmony_module_id"] = "ENTRY"
                ar["route_id"] = rt_id if mapping_type == "ROUTE_PAGE" else ""
                ar["surface_shell_id"] = sf_id if mapping_type == "VISUAL_SURFACE" else ""
                ar["page_shell_id"] = pshell_id
                ar["mapping_type"] = mapping_type
                ar["mapped_by"] = ownership["navigation_agent_id"]
                ar["mapping_status"] = "NOT_STARTED"
                ar["notes"] = "gmi auto seed;" + (
                    " carrier kind COMPOSABLE->SCREEN (A3 routable);"
                    if COMPOSABLE_CARRIER_KIND in carrier_kinds.get(page_id, set())
                    else ""
                ) + " shell/screenshot/verification pending"
                break
    write_csv(temp_dir / "module-registry.csv", mod_fields, rows_module)
    write_csv(temp_dir / "route-registry.csv", rt_fields, rows_route)
    write_csv(temp_dir / "surface-registry.csv", sf_fields, rows_surface)
    write_csv(temp_dir / "architecture-map.csv",
              csv_fieldnames(ASSETS / "architecture-map.template.csv"), architecture_rows)


def is_gmi_phase2(run_dir: Path) -> bool:
    """True only when the run carries a gmi Phase 2 closure certificate."""
    phase2 = run_dir / "phase-02-android-inventory"
    closure = phase2 / "phase-2-closure.json"
    if not closure.exists():
        closure = phase2 / "closure-report.json"
    manifest = phase2 / "phase-manifest.json"
    if manifest.exists():
        try:
            m = load_json(manifest)
            if isinstance(m, dict) and m.get("generator") == "gmi":
                return True
        except ValueError:
            pass
    return closure.exists()


def _read_rows_csv(path: Path) -> list[dict[str, str]]:
    import csv
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def verify_gmi_phase2_gate(run_dir: Path) -> dict:
    """gmi equivalent gate: any of the four gate inputs missing -> BLOCKED.

    Four machine-checkable gate inputs (mirrors gmi_closure.py preconditions):
      1. coverage/coverage-ledger.csv with UNMAPPED=0 (no GAP rows);
      2. candidates/phase-2-completeness.csv with no silent MISSING;
      3. runtime-evidence/audit-replay.csv with 0 discrepancies;
      4. runtime-evidence/runtime-gate.csv consistent with the audit replay.
    Any missing file raises (BLOCKED - gmi-gate-incomplete); a cached PASS
    string alone is not an input.
    """
    p2 = run_dir / "phase-02-android-inventory"

    def locate(name: str) -> Path:
        # run_dir first (adapter copy location), then the parent workspace (gmi source)
        for base in (run_dir, run_dir.parent):
            p = base / name
            if p.exists():
                return p
        return run_dir / name

    audit = locate("runtime-evidence/audit-replay.csv")
    coverage = locate("coverage/coverage-ledger.csv")
    runtime_gate = locate("runtime-evidence/runtime-gate.csv")
    completeness = locate("candidates/phase-2-completeness.csv")

    missing = [
        label for label, path in (
            ("coverage/coverage-ledger.csv", coverage),
            ("candidates/phase-2-completeness.csv", completeness),
            ("runtime-evidence/audit-replay.csv", audit),
            ("runtime-evidence/runtime-gate.csv", runtime_gate),
        ) if not path.is_file()
    ]
    if missing:
        raise ValueError(
            f"BLOCKED - gmi-gate-incomplete: missing gate input(s) {missing}; "
            "all four gmi gate artifacts are mandatory"
        )

    coverage_rows = _read_rows_csv(coverage)
    gaps = [r for r in coverage_rows if str(r.get("status", "")).strip() == "GAP"]
    if gaps:
        raise ValueError(f"gmi coverage has {len(gaps)} GAP files; UNMAPPED>0")

    silent_missing = [
        r for r in _read_rows_csv(completeness)
        if str(r.get("status", "")).strip() == "MISSING" and not str(r.get("hint", "")).strip()
    ]
    if silent_missing:
        raise ValueError(
            f"gmi completeness has {len(silent_missing)} silent MISSING rows (no hint); gate BLOCKED"
        )

    audit_rows = _read_rows_csv(audit)
    bad = [r for r in audit_rows if str(r.get("discrepancy", "")).strip().upper() == "YES"]
    if bad:
        raise ValueError(f"gmi-audit has {len(bad)} discrepancy rows; gate BLOCKED")

    gate_rows = _read_rows_csv(runtime_gate)
    gate_status = {
        (str(r.get("page_id", "")), str(r.get("symbol", ""))): str(r.get("status", ""))
        for r in gate_rows
    }
    for r in audit_rows:
        recorded = str(r.get("recorded", ""))
        expected = gate_status.get((str(r.get("page_id", "")), str(r.get("symbol", ""))))
        if recorded and expected is not None and recorded != expected:
            raise ValueError(
                "gmi runtime-gate.csv VISITED status disagrees with audit replay: "
                f"{r.get('page_id')} recorded={recorded} gate={expected}"
            )

    closure_report = p2 / "phase-2-closure.json"
    if not closure_report.exists():
        closure_report = p2 / "closure-report.json"
    closed = p2 / "CLOSED"
    if closed.exists() and closure_report.is_file():
        import hashlib as _h
        if closed.read_text(encoding="utf-8").strip() != _h.sha256(closure_report.read_bytes()).hexdigest():
            raise ValueError("gmi closure CLOSED marker does not bind closure-report")
    return {
        "mode": "gmi",
        "audit": "clean",
        "unmapped": "GAP=0",
        "completeness": "no-silent-missing",
        "runtime_gate": "consistent",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--work-order", required=True)
    parser.add_argument("--architecture-lead", required=True)
    args = parser.parse_args()

    try:
        run_dir = canonical_input(args.run_dir, "Migration run")
        work_order_path = canonical_input(args.work_order, "Phase 3 work order")
    except ValueError as exc:
        parser.error(str(exc))
    if not run_dir.is_dir():
        parser.error(f"Migration run does not exist: {run_dir}")
    if not is_gmi_phase2(run_dir):
        parser.error(
            "Only gmi Phase 2 closure input is accepted "
            "(phase-2-closure.json / closure-report.json from the gmi chain); "
            "the former controller path was removed"
        )
    work_orders_root = (run_dir / "controller" / "work-orders").resolve()
    try:
        work_order_path.relative_to(work_orders_root)
    except ValueError:
        parser.error(f"Work order must live below: {work_orders_root}")

    scope_input = run_dir / "controller" / "scope.json"
    gate_source_input = run_dir / "controller" / "gate-report.json"
    controller_anchor_input = run_dir / "controller" / "evidence-anchor-registry.csv"
    for label, path in (
        ("controller scope", scope_input),
        ("controller gate", gate_source_input),
        ("controller evidence anchors", controller_anchor_input),
    ):
        if path.is_symlink():
            parser.error(f"{label} must not be a symbolic link: {path}")
    scope_path = scope_input.resolve()
    gate_source_path = gate_source_input.resolve()
    controller_anchor_path = controller_anchor_input.resolve()
    phase2_input = run_dir / "phase-02-android-inventory"
    phase2 = phase2_input.resolve()
    if phase2_input.is_symlink() or phase2.parent != run_dir:
        parser.error("Phase 2 workspace must be the canonical run-owned directory")
    phase2_paths = {
        "closure": phase2 / "closure-report.json",
        "closed": phase2 / "CLOSED",
        "phase_manifest": phase2 / "phase-manifest.json",
        "inventory": phase2 / "inventory.csv",
        "asset_inventory": phase2 / "asset-inventory.csv",
        "asset_manifest": phase2 / "asset-package" / "manifest.sha256",
        "asset_committed": phase2 / "asset-package" / "COMMITTED",
        "acceptance": phase2 / "acceptance-registry.csv",
        "evidence_index": phase2 / "evidence-index.csv",
        "anchor_snapshot": phase2 / "evidence-anchors.snapshot.csv",
        "data_catalog": phase2 / "catalogs" / "data-dependencies.csv",
        "system_catalog": phase2 / "catalogs" / "system-capabilities.csv",
        "third_party_catalog": phase2 / "catalogs" / "third-party-dependencies.csv",
        "advanced_analysis": phase2 / "static-analysis" / "advanced-analysis.json",
        "advanced_observations": phase2 / "advanced-observations.json",
        "advanced_gate": phase2 / "advanced-gate-report.json",
        "probe_index": phase2 / "probe-evidence-index.csv",
    }
    if not phase2_paths["closure"].is_file() and (phase2 / "phase-2-closure.json").is_file():
        phase2_paths["closure"] = phase2 / "phase-2-closure.json"

    try:
        gmi_gate = verify_gmi_phase2_gate(run_dir)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"[init_scaffold] gmi Phase 2 gate verified: {gmi_gate}")

    try:
        run_manifest = require_object(load_json(run_dir / "run-manifest.json"), "run-manifest.json")
        scope = require_object(load_json(scope_path), "controller scope")
        gate = require_object(load_json(gate_source_path), "gate report snapshot")
        work_order = require_object(load_json(work_order_path), "Phase 3 work order")
        closure = require_object(load_json(phase2_paths["closure"]), "Phase 2 closure")
        phase2_manifest = require_object(load_json(phase2_paths["phase_manifest"]), "Phase 2 manifest")
        inventory_all = read_csv(phase2_paths["inventory"])
        acceptance = read_csv(phase2_paths["acceptance"])
        evidence_index = read_csv(phase2_paths["evidence_index"])
        anchor_snapshot = read_csv(phase2_paths["anchor_snapshot"])
        controller_anchors_all = read_csv(controller_anchor_path)
        advanced_analysis = require_object(
            load_json(phase2_paths["advanced_analysis"]), "Phase 2 advanced analysis"
        )
        advanced_observations = require_object(
            load_json(phase2_paths["advanced_observations"]), "Phase 2 advanced observations"
        )
        advanced_gate = require_object(load_json(phase2_paths["advanced_gate"]), "Phase 2 advanced gate")
        probe_index = read_csv(phase2_paths["probe_index"])
    except ValueError as exc:
        parser.error(str(exc))

    if gate.get("phase") != 2 or gate.get("verdict") != "PASS":
        parser.error("A Phase 2 PASS gate snapshot is required")
    try:
        ownership = validate_gmi_ownership(work_order)
    except ValueError as exc:
        parser.error(str(exc))
    if args.architecture_lead != ownership["architecture_lead_id"]:
        parser.error("--architecture-lead must equal the frozen architecture_lead_id")
    work_order_sha256 = sha256_file(work_order_path)
    gate_work_order_snapshot = gate_source_path
    phase2_assets = read_csv(phase2_paths["asset_inventory"])
    phase2_asset_files = [
        {
            "asset_id": row.get("asset_id", ""),
            "source_path": row.get("source_path", ""),
            "archive_path": row.get("archive_path", ""),
            "sha256": row.get("sha256", ""),
            "asset_type": row.get("asset_type", ""),
        }
        for row in phase2_assets
    ]
    controller_anchors = sorted(
        [
            row for row in controller_anchors_all
            if row.get("run_id") in ("", run_manifest.get("run_id")) and row.get("phase") in ("", "2")
        ],
        key=lambda row: row.get("evidence_id", ""),
    )

    catalog_specs = {
        "data_dependency_refs": (
            phase2_paths["data_catalog"], "data_dependency_id", "DATA_DEPENDENCY",
            lambda row: row.get("data_dependency_id") == "NONE_FOUND" or row.get("name") == "NONE_FOUND",
        ),
        "system_capability_refs": (
            phase2_paths["system_catalog"], "system_capability_id", "SYSTEM_CAPABILITY",
            lambda row: row.get("system_capability_id") == "NONE_FOUND" or row.get("name") == "NONE_FOUND",
        ),
        "third_party_dependency_refs": (
            phase2_paths["third_party_catalog"], "third_party_dependency_id", "THIRD_PARTY_DEPENDENCY",
            lambda row: row.get("third_party_dependency_id") == "NONE_FOUND" or row.get("name") == "NONE_FOUND",
        ),
    }
    catalog_indexes: dict[str, tuple[dict[str, dict[str, str]], set[str], str]] = {}
    try:
        for field, (path, id_field, source_kind, sentinel) in catalog_specs.items():
            indexed, sentinels = catalog_index(path, id_field, sentinel)
            catalog_indexes[field] = (indexed, sentinels, source_kind)
    except ValueError as exc:
        parser.error(str(exc))

    migration_scope = scope.get("migration_scope", {})
    included = {str(item) for item in migration_scope.get("included_features", [])}
    excluded = {str(item) for item in migration_scope.get("excluded_features", [])}
    if not included:
        parser.error("Frozen included feature scope is empty")
    for feature_id in included | excluded:
        try:
            validate_id(feature_id, "Feature-ID in controller scope")
        except ValueError as exc:
            parser.error(str(exc))

    active_inventory = [row for row in inventory_all if row.get("row_status") != "SUPERSEDED"]
    if not active_inventory:
        parser.error("Phase 2 inventory is empty")

    source_rows: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    seen_inventory_ids: set[str] = set()
    visual_features: set[str] = set()
    for row in active_inventory:
        try:
            key = source_row_key(row)
            for field in ("inventory_id", "feature_id", "page_id", "state_id", "env_id", "evidence_id"):
                validate_id(row.get(field, ""), field)
        except ValueError as exc:
            parser.error(str(exc))
        if key in seen_keys or row["inventory_id"] in seen_inventory_ids:
            parser.error(f"Duplicate active Phase 2 source row: {row['inventory_id']} / {key}")
        if row["feature_id"] not in included | excluded:
            parser.error(f"Inventory Feature-ID is outside frozen scope: {row['feature_id']}")
        # gmi mode: catalog-refs semantics come from gmi candidates (the adapter
        # writes explicit NONE_FOUND sentinels); hard per-feature catalog binding
        # is deliberately not re-derived here.
        seen_keys.add(key)
        seen_inventory_ids.add(row["inventory_id"])
        visual_features.add(row["feature_id"])
        source_rows.append({**row, "source_row_key": key})

    requirements: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in source_rows:
        if row["feature_id"] in excluded:
            continue
        for field, (_indexed, sentinels, source_kind) in catalog_indexes.items():
            for ref in parse_refs(row.get(field, "")):
                if ref in sentinels:
                    continue
                key = (row["feature_id"], source_kind, ref)
                requirement = requirements.setdefault(
                    key,
                    {
                        "capability_requirement_id": requirement_id(*key),
                        "source_kind": source_kind,
                        "source_feature_id": row["feature_id"],
                        "source_requirement_ref": ref,
                        "source_inventory_row_keys": set(),
                    },
                )
                requirement["source_inventory_row_keys"].add(row["source_row_key"])

    advanced_obligation_rows: list[dict[str, Any]] = []
    advanced_ids: set[str] = set()
    observation_probe_by_subject = {
        str(row.get("subject_id")): str(row.get("probe_evidence_id", ""))
        for row in advanced_observations.get("observations", []) if isinstance(row, dict)
    }
    for subject_type, collection, id_field, handoff_kind in (
        ("DYNAMIC_RISK", "dynamic_risks", "risk_id", "PHASE4_DYNAMIC_SURFACE"),
        ("SIDE_EFFECT", "side_effects", "candidate_id", "PHASE3_CAPABILITY_CONTRACT"),
        ("SCENARIO", "scenarios", "scenario_id", "PHASE4_SCENARIO_TEST"),
    ):
        rows = advanced_analysis.get(collection, [])
        if not isinstance(rows, list):
            parser.error(f"Phase 2 advanced analysis has an invalid {collection} array")
        for item in rows:
            if not isinstance(item, dict):
                parser.error(f"Phase 2 advanced analysis has a non-object {collection} row")
            subject_id = str(item.get(id_field, ""))
            try:
                validate_id(subject_id, f"advanced {id_field}")
            except ValueError as exc:
                parser.error(str(exc))
            if subject_id in advanced_ids:
                parser.error(f"Duplicate Phase 2 advanced subject ID: {subject_id}")
            advanced_ids.add(subject_id)
            candidate_features = sorted({
                str(value) for value in item.get("candidate_feature_ids", [])
                if str(value) in included
            })
            if not candidate_features:
                parser.error(f"Advanced subject has no included Feature-ID: {subject_id}")
            linked_requirements: list[str] = []
            if subject_type == "SIDE_EFFECT":
                for feature_id in candidate_features:
                    key = (feature_id, "ADVANCED_SIDE_EFFECT", subject_id)
                    requirement = requirements.setdefault(
                        key,
                        {
                            "capability_requirement_id": requirement_id(*key),
                            "source_kind": "ADVANCED_SIDE_EFFECT",
                            "source_feature_id": feature_id,
                            "source_requirement_ref": subject_id,
                            "source_inventory_row_keys": set(),
                        },
                    )
                    linked_requirements.append(requirement["capability_requirement_id"])
            advanced_obligation_rows.append({
                "subject_type": subject_type,
                "subject_id": subject_id,
                "page_id": item.get("page_id", ""),
                "candidate_feature_ids": candidate_features,
                "handoff_kind": handoff_kind,
                "capability_requirement_ids": sorted(linked_requirements),
                "probe_evidence_id": observation_probe_by_subject.get(subject_id, ""),
                "status": "LOCKED_FOR_IMPLEMENTATION",
            })

    for feature_id in sorted(included - visual_features):
        key = (feature_id, "SCOPE_FEATURE", feature_id)
        requirements[key] = {
            "capability_requirement_id": requirement_id(*key),
            "source_kind": "SCOPE_FEATURE",
            "source_feature_id": feature_id,
            "source_requirement_ref": feature_id,
            "source_inventory_row_keys": set(),
        }

    android_scope = scope.get("android", {}) if isinstance(scope.get("android"), dict) else {}
    bundle_name = str(android_scope.get("application_id", "")).strip()
    if not re.fullmatch(r"[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+", bundle_name):
        parser.error("Frozen Android application_id cannot seed a valid HarmonyOS bundle name")
    app_name = str(scope.get("project_name") or scope.get("project_id") or bundle_name).strip()
    vendor = "migration"
    log_tag = re.sub(r"[^A-Za-z0-9_.-]", "", app_name)[:31] or "MigrationScaffold"
    template_replacements = {
        "__BUNDLE_NAME__": bundle_name,
        "__APP_NAME__": json.dumps(app_name, ensure_ascii=False)[1:-1],
        "__VENDOR__": vendor,
        "__LOG_TAG__": log_tag,
    }

    phase_dir = run_dir / PHASE_NAME
    if phase_dir.exists():
        # gmi_phase3_adapter pre-creates empty phase-03/04 placeholders; only an
        # empty placeholder may be replaced, never a real workspace.
        if any(phase_dir.iterdir()):
            parser.error(f"Phase 3 workspace already exists; overwrite is prohibited: {phase_dir}")
        shutil.rmtree(phase_dir)
    inputs_dir = phase_dir / "inputs"
    input_snapshots = {
        "controller_scope": inputs_dir / "controller-scope.json",
        "phase2_gate": inputs_dir / "phase-02-gate-report.json",
        "phase2_closure": inputs_dir / "phase-02-closure-report.json",
        "phase2_phase_manifest": inputs_dir / "phase-02-phase-manifest.json",
        "phase2_inventory": inputs_dir / "phase-02-inventory.csv",
        "phase2_asset_inventory": inputs_dir / "phase-02-asset-inventory.csv",
        "phase2_asset_manifest": inputs_dir / "phase-02-asset-package-manifest.sha256",
        "phase2_asset_committed": inputs_dir / "phase-02-asset-package-COMMITTED",
        "phase2_acceptance": inputs_dir / "phase-02-acceptance-registry.csv",
        "phase2_evidence_index": inputs_dir / "phase-02-evidence-index.csv",
        "phase2_anchor_snapshot": inputs_dir / "phase-02-evidence-anchors.snapshot.csv",
        "controller_anchor_registry": inputs_dir / "controller-evidence-anchor-registry.csv",
        "phase3_work_order": inputs_dir / "phase-03-work-order.json",
        "phase2_data_catalog": inputs_dir / "catalogs" / "data-dependencies.csv",
        "phase2_system_catalog": inputs_dir / "catalogs" / "system-capabilities.csv",
        "phase2_third_party_catalog": inputs_dir / "catalogs" / "third-party-dependencies.csv",
        "phase2_advanced_analysis": inputs_dir / "phase-02-advanced-analysis.json",
        "phase2_advanced_observations": inputs_dir / "phase-02-advanced-observations.json",
        "phase2_advanced_gate": inputs_dir / "phase-02-advanced-gate-report.json",
        "phase2_probe_index": inputs_dir / "phase-02-probe-evidence-index.csv",
        "arkui_template_manifest": inputs_dir / "arkui-stage-template.manifest.sha256",
    }
    initialized_at = utc_now()
    input_lock: dict[str, Any] = {
        "run_id": run_manifest.get("run_id"),
        "project_id": run_manifest.get("project_id"),
        "locked_at": initialized_at,
        "locked_by": args.architecture_lead,
        "work_order_id": work_order.get("work_order_id"),
        "work_order_sha256": work_order_sha256,
        "ownership": ownership,
        "phase2_baseline_env_id": closure.get("baseline_env_id"),
        "gmi_gate": gmi_gate,
        "controller_scope": input_record(scope_path, input_snapshots["controller_scope"]),
        "phase2_gate": input_record(
            gate_work_order_snapshot, input_snapshots["phase2_gate"], use_snapshot_as_path=True
        ),
        "phase2_closure": input_record(phase2_paths["closure"], input_snapshots["phase2_closure"]),
        "phase2_phase_manifest": input_record(
            phase2_paths["phase_manifest"], input_snapshots["phase2_phase_manifest"]
        ),
        "phase2_inventory": {
            **input_record(phase2_paths["inventory"], input_snapshots["phase2_inventory"]),
            "row_count": len(inventory_all),
            "active_row_count": len(source_rows),
            "source_row_keys": sorted(seen_keys),
        },
        "phase2_asset_inventory": {
            **input_record(
                phase2_paths["asset_inventory"], input_snapshots["phase2_asset_inventory"]
            ),
            "row_count": len(phase2_assets),
            "asset_ids": [row["asset_id"] for row in phase2_assets],
        },
        "phase2_asset_package_manifest": input_record(
            phase2_paths["asset_manifest"], input_snapshots["phase2_asset_manifest"]
        ),
        "phase2_asset_package_committed": input_record(
            phase2_paths["asset_committed"], input_snapshots["phase2_asset_committed"]
        ),
        "phase2_asset_files": phase2_asset_files,
        "phase2_acceptance": input_record(
            phase2_paths["acceptance"], input_snapshots["phase2_acceptance"]
        ),
        "phase2_evidence_index": input_record(
            phase2_paths["evidence_index"], input_snapshots["phase2_evidence_index"]
        ),
        "phase2_anchor_snapshot": input_record(
            phase2_paths["anchor_snapshot"], input_snapshots["phase2_anchor_snapshot"]
        ),
        "controller_anchor_registry": input_record(
            controller_anchor_path, input_snapshots["controller_anchor_registry"]
        ),
        "phase3_work_order": input_record(work_order_path, input_snapshots["phase3_work_order"]),
        "phase2_catalogs": {
            "data_dependencies": input_record(
                phase2_paths["data_catalog"], input_snapshots["phase2_data_catalog"]
            ),
            "system_capabilities": input_record(
                phase2_paths["system_catalog"], input_snapshots["phase2_system_catalog"]
            ),
            "third_party_dependencies": input_record(
                phase2_paths["third_party_catalog"], input_snapshots["phase2_third_party_catalog"]
            ),
        },
        "phase2_advanced": {
            "analysis": input_record(
                phase2_paths["advanced_analysis"], input_snapshots["phase2_advanced_analysis"]
            ),
            "observations": input_record(
                phase2_paths["advanced_observations"], input_snapshots["phase2_advanced_observations"]
            ),
            "gate": input_record(
                phase2_paths["advanced_gate"], input_snapshots["phase2_advanced_gate"]
            ),
            "probe_index": input_record(
                phase2_paths["probe_index"], input_snapshots["phase2_probe_index"]
            ),
            "dynamic_risk_ids": sorted(
                str(row.get("risk_id")) for row in advanced_analysis.get("dynamic_risks", [])
            ),
            "side_effect_ids": sorted(
                str(row.get("candidate_id")) for row in advanced_analysis.get("side_effects", [])
            ),
            "scenario_ids": sorted(
                str(row.get("scenario_id")) for row in advanced_analysis.get("scenarios", [])
            ),
            "probe_evidence_ids": sorted(
                str(row.get("probe_evidence_id")) for row in probe_index if row.get("probe_evidence_id")
            ),
        },
        "arkui_template": {
            "template_id": "ARKUI-STAGE-TEMPLATE-V1",
            "manifest_path": str(input_snapshots["arkui_template_manifest"]),
            "manifest_sha256": hashlib.sha256(template_manifest(ARKUI_TEMPLATE).encode("utf-8")).hexdigest(),
            "file_count": len(template_files(ARKUI_TEMPLATE)),
        },
        "included_feature_ids": sorted(included),
        "excluded_feature_ids": sorted(excluded),
        "inventory_feature_ids": sorted(visual_features),
        "page_ids": sorted({row["page_id"] for row in source_rows}),
        "state_ids": sorted({row["state_id"] for row in source_rows}),
        "capability_requirement_ids": sorted(
            requirement["capability_requirement_id"] for requirement in requirements.values()
        ),
        "advanced_obligation_ids": sorted(advanced_ids),
    }

    architecture_rows: list[dict[str, str]] = []
    migration_rows: list[dict[str, str]] = []
    for row in sorted(source_rows, key=lambda item: item["source_row_key"]):
        architecture_rows.append(
            {
                "source_row_key": row["source_row_key"], "inventory_id": row["inventory_id"],
                "feature_id": row["feature_id"], "page_id": row["page_id"],
                "state_id": row["state_id"], "env_id": row["env_id"],
                "evidence_id": row["evidence_id"], "mapping_type": "",
                "harmony_module_id": "", "route_id": "", "surface_shell_id": "",
                "page_shell_id": "", "shell_file": "", "screenshot_ids": "",
                "verification_id": "", "mapped_by": "", "mapping_status": "NOT_STARTED",
                "notes": "",
            }
        )
        migration_rows.append(
            {
                "source_kind": "INVENTORY_ROW", "source_key": row["source_row_key"],
                "feature_id": row["feature_id"], "page_id": row["page_id"],
                "state_id": row["state_id"], "target_id": "", "status": "NOT_STARTED",
                "updated_by": ownership["architecture_lead_id"], "updated_at": initialized_at,
                "notes": "",
            }
        )

    capability_rows: list[dict[str, str]] = []
    for requirement in sorted(requirements.values(), key=lambda item: item["capability_requirement_id"]):
        requirement_key = requirement["capability_requirement_id"]
        capability_rows.append(
            {
                "capability_requirement_id": requirement_key,
                "source_kind": requirement["source_kind"],
                "source_feature_id": requirement["source_feature_id"],
                "source_requirement_ref": requirement["source_requirement_ref"],
                "source_inventory_row_keys": join_multi(requirement["source_inventory_row_keys"]),
                "capability_contract_id": "", "harmony_module_id": "", "contract_kind": "",
                "contract_file": "", "contract_symbol": "", "created_by": "",
                "status": "NOT_STARTED", "notes": "",
            }
        )
        migration_rows.append(
            {
                "source_kind": "CAPABILITY_REQUIREMENT", "source_key": requirement_key,
                "feature_id": requirement["source_feature_id"], "page_id": "", "state_id": "",
                "target_id": "", "status": "NOT_STARTED",
                "updated_by": ownership["architecture_lead_id"], "updated_at": initialized_at,
                "notes": "",
            }
        )

    asset_registry_rows = [
        {
            "asset_id": row["asset_id"],
            "phase2_archive_path": row["archive_path"],
            "asset_sha256": row["sha256"],
            "asset_type": row["asset_type"],
            "feature_ids": row["feature_ids"],
            "page_ids": row["page_ids"],
            "state_ids": row["state_ids"],
            "target_module_id": "",
            "target_path": "",
            "target_symbol": "",
            "planned_mode": "",
            "decision": "",
            "created_by": "",
            "status": "NOT_STARTED",
            "notes": "",
        }
        for row in phase2_assets
    ]

    copy_sources = {
        "controller_scope": scope_path,
        "phase2_gate": gate_work_order_snapshot,
        "phase2_closure": phase2_paths["closure"],
        "phase2_phase_manifest": phase2_paths["phase_manifest"],
        "phase2_inventory": phase2_paths["inventory"],
        "phase2_asset_inventory": phase2_paths["asset_inventory"],
        "phase2_asset_manifest": phase2_paths["asset_manifest"],
        "phase2_asset_committed": phase2_paths["asset_committed"],
        "phase2_acceptance": phase2_paths["acceptance"],
        "phase2_evidence_index": phase2_paths["evidence_index"],
        "phase2_anchor_snapshot": phase2_paths["anchor_snapshot"],
        "controller_anchor_registry": controller_anchor_path,
        "phase3_work_order": work_order_path,
        "phase2_data_catalog": phase2_paths["data_catalog"],
        "phase2_system_catalog": phase2_paths["system_catalog"],
        "phase2_third_party_catalog": phase2_paths["third_party_catalog"],
        "phase2_advanced_analysis": phase2_paths["advanced_analysis"],
        "phase2_advanced_observations": phase2_paths["advanced_observations"],
        "phase2_advanced_gate": phase2_paths["advanced_gate"],
        "phase2_probe_index": phase2_paths["probe_index"],
    }
    carrier_kinds = gmi_carrier_kinds(phase2)
    with tempfile.TemporaryDirectory(prefix=f".{PHASE_NAME}-", dir=run_dir) as temp_name:
        temp_dir = Path(temp_name)
        for name in ("inputs", "environments", "verification", "gate-reports"):
            (temp_dir / name).mkdir()
        (temp_dir / "inputs" / "catalogs").mkdir()
        for key, source in copy_sources.items():
            relative = input_snapshots[key].relative_to(phase_dir)
            shutil.copyfile(source, temp_dir / relative)
        source_template_manifest = template_manifest(ARKUI_TEMPLATE)
        template_manifest_snapshot = temp_dir / input_snapshots["arkui_template_manifest"].relative_to(phase_dir)
        atomic_text(template_manifest_snapshot, source_template_manifest)
        project_generation = create_arkui_project(
            ARKUI_TEMPLATE, temp_dir / "harmony-project", template_replacements
        )
        actual_template_manifest_sha256 = sha256_file(template_manifest_snapshot)
        input_lock["arkui_template"]["manifest_sha256"] = actual_template_manifest_sha256
        project_generation["template_manifest_sha256"] = actual_template_manifest_sha256
        atomic_json(temp_dir / "template-generation.json", project_generation)
        atomic_json(temp_dir / "advanced-obligations.json", {
            "schema_version": 1,
            "source_advanced_gate_sha256": sha256_file(phase2_paths["advanced_gate"]),
            "obligations": sorted(advanced_obligation_rows, key=lambda row: row["subject_id"]),
        })
        atomic_json(temp_dir / "stage-03-input-lock.json", input_lock)

        copy_template_csv(temp_dir, "module-registry.template.csv", "module-registry.csv")
        copy_template_csv(temp_dir, "route-registry.template.csv", "route-registry.csv")
        copy_template_csv(temp_dir, "surface-registry.template.csv", "surface-registry.csv")
        copy_template_csv(temp_dir, "public-ui-registry.template.csv", "public-ui-registry.csv")
        copy_template_csv(temp_dir, "architecture-decisions.template.csv", "architecture-decisions.csv")
        copy_template_csv(temp_dir, "rework-tickets.template.csv", "rework-tickets.csv")
        # gmi path: deterministic registry seeding (single `entry` module plus one
        # landing shell side per page, mapping_type decided by the Phase 2 carrier).
        _fill_gmi_registries(temp_dir, architecture_rows, source_rows, carrier_kinds, ownership)
        shutil.copyfile(ASSETS / "dependency-policy.template.json", temp_dir / "dependency-policy.json")
        shutil.copyfile(ASSETS / "henv-registry.template.csv", temp_dir / "environments" / "henv-registry.csv")
        write_csv(
            temp_dir / "capability-contracts.csv",
            csv_fieldnames(ASSETS / "capability-contracts.template.csv"), capability_rows,
        )
        write_csv(
            temp_dir / "migration-status.csv",
            csv_fieldnames(ASSETS / "migration-status.template.csv"), migration_rows,
        )
        write_csv(
            temp_dir / "asset-registry.csv",
            csv_fieldnames(ASSETS / "asset-registry.template.csv"), asset_registry_rows,
        )
        atomic_json(
            temp_dir / "phase-manifest.json",
            {
                "run_id": run_manifest.get("run_id"), "project_id": run_manifest.get("project_id"),
                "phase": 3, "status": "IN_PROGRESS", "initialized_at": initialized_at,
                "architecture_lead": ownership["architecture_lead_id"],
                "ownership": ownership, "work_order_id": work_order.get("work_order_id"),
                "work_order_sha256": work_order_sha256,
                "phase2_input_locked": True, "business_implementation_allowed": False,
                "gui_only_evidence_allowed": False, "mp4_required": False,
                "template_id": project_generation["template_id"],
                "template_manifest_sha256": project_generation["template_manifest_sha256"],
                "template_generation_sha256": sha256_file(temp_dir / "template-generation.json"),
            },
        )
        atomic_json(
            temp_dir / "build-report.json",
            {"status": "NOT_RUN", "verification_id": None, "updated_at": initialized_at},
        )
        atomic_json(
            temp_dir / "stage-03-gate-report.json",
            {"phase": 3, "verdict": "NOT_RUN", "reviewer_role": "architecture-acceptance-agent"},
        )
        temp_dir.rename(phase_dir)

    print(json.dumps({
        "workspace": str(phase_dir), "work_order_id": work_order.get("work_order_id"),
        "inventory_rows": len(source_rows), "capability_requirements": len(capability_rows),
        "assets": len(asset_registry_rows),
        "advanced_obligations": len(advanced_obligation_rows),
        "template_files": project_generation["generated_file_count"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
