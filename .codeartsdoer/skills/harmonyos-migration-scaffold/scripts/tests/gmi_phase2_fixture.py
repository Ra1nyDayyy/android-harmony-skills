#!/usr/bin/env python3
"""Build one real, closed gmi Phase 2 workspace and its Phase 3 run directory.

The fixture drives the real gmi chain from the android-migration-inventory
skill (dual-lane protocol): it hand-writes a minimal gmi workspace (candidates
13 tables + coverage + page-level evidence sources + static-analysis
runtime-tasks.json), runs the real `gmi_runtime.py --split-queues` to freeze
runtime-queue-a/b.json, hand-writes both lane evidence directories (as if two
emulator lanes had executed every frozen task to VERIFIED), runs the real
gmi_audit.py merge (hash/identity/queue audit -> audit-result.json +
task-level runtime-gate.csv; gmi_phase3_adapter then aggregates the task rows
per page — all VERIFIED -> ACCEPTED), runs gmi_closure.py for the
READY_FOR_HUMAN_REVIEW certificate, records the human-review acceptance bound
to the closure hash, presets the run-level controller identity files, and
finally lets gmi_phase3_adapter.py synthesize the canonical
`phase-02-android-inventory/` layout (including the deterministic
advanced-gate report).
"""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
SCAFFOLD_SKILL = HERE.parents[1]
BUNDLE = SCAFFOLD_SKILL.parent
INVENTORY_SKILL = BUNDLE / "android-migration-inventory"

sys.path.insert(0, str(HERE))
import fake_harmony  # noqa: E402  (write_png reuse only)
sys.path.insert(0, str(INVENTORY_SKILL / "scripts"))
import gmi_runtime as gmi_rt  # noqa: E402  (lane CSV field contract only)

PKG = "com.example.fixture"
LANE_SERIALS = {"A": "fixture-lane-a", "B": "fixture-lane-b"}
LANE_SCREEN = {"resolution": "1080x2400", "density": "440"}

CANDIDATE_TABLES = (
    "code-map.candidates.full.csv",
    "business-rules.candidates.csv",
    "asset-mapping.candidates.csv",
    "inventory.candidates.csv",
    "page-fields.candidates.csv",
    "third-party-dependencies.candidates.csv",
    "field-options.candidates.csv",
    "navigation-relations.candidates.csv",
    "behavior.candidates.csv",
    "risk-probes.candidates.csv",
    "color-palette.candidates.csv",
    "motion.candidates.csv",
    "phase-2-completeness.csv",
)

CANDIDATE_HEADERS = {
    "code-map.candidates.full.csv": [
        "candidate_id", "code_ref", "file_path", "line", "symbol", "snippet",
        "suggested_feature", "suggested_page", "choices_feature", "choices_disposition",
    ],
    "business-rules.candidates.csv": [
        "candidate_id", "source_ref", "page_id", "condition", "outcome_hint",
        "example_rule", "feature_hint",
    ],
    "asset-mapping.candidates.csv": [
        "candidate_id", "layout", "component_id", "type", "resource_id",
        "android_attr", "resolved_value", "fidelity_key", "category12",
        "harmony_target_hint", "page_id", "choices_hint",
    ],
    "inventory.candidates.csv": [
        "candidate_id", "feature_id", "page_id", "state_id", "state_expression",
        "env_id", "entry_condition", "expected_observable", "source_ref",
    ],
    "page-fields.candidates.csv": [
        "page_id", "page_symbol", "order_index", "field_id", "field_type",
        "field_label", "icon_resource", "layout_ref", "source_ref",
    ],
    "third-party-dependencies.candidates.csv": [
        "candidate_id", "source_ref", "group", "artifact", "version",
        "resolution", "scope", "condition", "example_rule", "feature_hint",
    ],
    "field-options.candidates.csv": [
        "candidate_id", "page_id", "group", "group_key", "option_label",
        "option_type", "sub_option", "sub_option_index", "ref_key",
        "default_value", "summary", "source_ref",
    ],
    "navigation-relations.candidates.csv": [
        "candidate_id", "from_page_id", "from_page_symbol", "trigger", "action",
        "to_page_id", "relation_type", "source_ref",
    ],
    "behavior.candidates.csv": [
        "candidate_id", "page_id", "page_symbol", "event", "action", "params",
        "data_target", "side_effect", "source_ref",
    ],
    "risk-probes.candidates.csv": [
        "candidate_id", "probe_id", "category", "severity", "file", "line",
        "signal", "count", "page_id", "harmony_hint",
    ],
    "color-palette.candidates.csv": [
        "candidate_id", "color_name", "hex", "alpha", "kind", "file", "line",
    ],
    "motion.candidates.csv": [
        "candidate_id", "page_id", "page_symbol", "motion_type", "signal",
        "file", "line",
    ],
    "phase-2-completeness.csv": [
        "page_id", "page_symbol", "check_category", "check_key", "status", "hint",
    ],
}

OWNERSHIP = {
    "architecture_lead_id": "architecture-lead-1",
    "toolchain_agent_id": "toolchain-agent-1",
    "navigation_agent_id": "navigation-agent-1",
    "public_ui_agent_id": "public-ui-agent-1",
    "capability_contract_agent_id": "capability-agent-1",
    "architecture_acceptance_agent_id": "architecture-acceptance-1",
}


def run(*args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(args), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    if completed.returncode != expect:
        raise AssertionError(
            f"Expected exit {expect}, got {completed.returncode}\nCOMMAND: {args}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ui_xml(symbol: str) -> str:
    node = (
        f'<node index="0" text="{symbol}" content-desc="{symbol}" '
        'class="android.widget.TextView" '
        'resource-id="com.example.fixture:id/title" package="com.example.fixture" '
        'checkable="false" clickable="false" bounds="[16,48;304,96]"/>'
    )
    return (
        '<hierarchy rotation="0">'
        + node * 4
        + "</hierarchy>\n"
    )


def _android_project(root: Path) -> Path:
    project = root / "android-project"
    (project / "app" / "src" / "main" / "res" / "drawable").mkdir(parents=True)
    (project / "app" / "src" / "main" / "AndroidManifest.xml").write_text(
        '<manifest package="com.example.fixture"><application /></manifest>\n',
        encoding="utf-8",
    )
    (project / "app" / "build.gradle").write_text(
        'apply plugin: "com.android.application"\n', encoding="utf-8"
    )
    (project / "settings.gradle").write_text(
        'include ":app"\n', encoding="utf-8"
    )
    (project / "app" / "Login.kt").write_text(
        "fun renderLogin() = Unit\n", encoding="utf-8"
    )
    (project / "app" / "src" / "main" / "res" / "drawable" / "login_logo.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
        '<circle cx="16" cy="16" r="14" fill="#3367D6"/></svg>\n',
        encoding="utf-8",
    )
    run("git", "init", "-q", str(project))
    run("git", "-C", str(project), "config", "user.email", "fixture@example.invalid")
    run("git", "-C", str(project), "config", "user.name", "Fixture")
    run("git", "-C", str(project), "add", ".")
    run("git", "-C", str(project), "commit", "-q", "-m", "fixture baseline")
    apk = root / "fixture" / "fixture.apk"
    apk.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(apk, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("AndroidManifest.xml", '<manifest package="com.example.fixture"/>\n')
        archive.writestr("classes.dex", b"fixture-dex")
    return project


def _android_revision(project: Path) -> str:
    return run("git", "-C", str(project), "rev-parse", "HEAD").stdout.strip()


def _gmi_workspace(root: Path, project: Path, pages: list[dict[str, Any]], with_asset: bool) -> Path:
    ws = root / "gmi-ws"
    cands = ws / "candidates"
    coverage = ws / "coverage"
    runtime = ws / "runtime-evidence"
    for directory in (cands, coverage, runtime):
        directory.mkdir(parents=True, exist_ok=True)

    (ws / "phase-manifest.json").write_text(
        json.dumps({
            "phase": 2,
            "generator": "gmi",
            "android_project_root": str(project.resolve()),
            "included_features": ["FEATURE-AUTH"],
        }, indent=2) + "\n",
        encoding="utf-8",
    )

    asset_rows: list[dict[str, Any]] = []
    if with_asset:
        asset_rows.append({
            "candidate_id": "CAND-ASSET-001",
            "layout": "activity_login",
            "component_id": "login_logo",
            "type": "FILE_ASSET",
            "resource_id": "app/src/main/res/drawable/login_logo.svg",
            "android_attr": "src",
            "resolved_value": sha256(project / "app" / "src" / "main" / "res" / "drawable" / "login_logo.svg"),
            "fidelity_key": "vector-image",
            "category12": "ICON",
            "harmony_target_hint": "media",
            "page_id": pages[0]["page_id"],
            "choices_hint": "",
        })

    inventory_rows = [
        {
            "candidate_id": f"CAND-INV-{index + 1:03d}",
            "feature_id": "FEATURE-AUTH",
            "page_id": page["page_id"],
            "state_id": f"STATE-{page['page_id']}-DEFAULT",
            "state_expression": "DEFAULT",
            "env_id": "ENV-001",
            "entry_condition": f"App launched / navigate to {page['symbol']}",
            "expected_observable": f"{page['symbol']} displayed",
            "source_ref": "app/Login.kt:1",
        }
        for index, page in enumerate(pages)
    ]
    completeness_rows = [
        {
            "page_id": page["page_id"],
            "page_symbol": page["symbol"],
            "check_category": "ui_fields",
            "check_key": "fields",
            "status": "OK",
            "hint": "",
        }
        for page in pages
    ]
    page_fields_rows = [
        {
            "page_id": page["page_id"],
            "page_symbol": page["symbol"],
            "order_index": "1",
            "field_id": "title",
            "field_type": "TextView",
            "field_label": page["symbol"],
            "icon_resource": "",
            "layout_ref": "activity_login",
            "source_ref": "app/Login.kt:1",
        }
        for page in pages if page.get("visited")
    ]

    table_rows = {
        "asset-mapping.candidates.csv": asset_rows,
        "inventory.candidates.csv": inventory_rows,
        "phase-2-completeness.csv": completeness_rows,
        "page-fields.candidates.csv": page_fields_rows,
    }
    for name in CANDIDATE_TABLES:
        write_rows(cands / name, CANDIDATE_HEADERS[name], table_rows.get(name, []))
    (cands / "manifest.sha256").write_text(
        "".join(f"{sha256(cands / name)}  {name}\n" for name in sorted(CANDIDATE_TABLES)),
        encoding="utf-8",
    )

    write_rows(
        coverage / "coverage-ledger.csv",
        ["file", "category", "disposition", "status", "covering_candidates"],
        [
            {
                "file": "app/Login.kt",
                "category": "code",
                "disposition": "MAPPED",
                "status": "COVERED",
                "covering_candidates": "CAND-INV-001",
            }
        ],
    )

    # 页级证据源目录：gmi_phase3_adapter 的 evidence 导出（6b）按
    # `runtime-evidence/<有 ui.xml 的目录>` 定位每页截图源；仅 visited 页生成，
    # 未访问页保持 PENDING_RUNTIME_VERIFY（诚实策略：未访问 != 已验证）。
    for page in pages:
        if page.get("visited"):
            evidence_dir = runtime / page["symbol"]
            evidence_dir.mkdir(parents=True, exist_ok=True)
            ui_path = evidence_dir / "ui.xml"
            ui_path.write_text(ui_xml(page["symbol"]), encoding="utf-8")
            fake_harmony.write_png(evidence_dir / "screenshot.png", 320, 640)

    # 双 lane 协议：static-analysis/runtime-tasks.json（gmi_runtime --split-queues、
    # gmi_audit、gmi_closure 的任务口径）。每页一个 REQUIRED 默认态任务 +
    # 一个 REVIEW 级 VERIFY_STATE_BRANCH 抽样任务（保证任意页面组合下
    # closure 的 REVIEW 分母非零且 unverified=0% <= 10%；双 lane 证据由
    # _write_lane_evidence 统一模拟为 VERIFIED）。
    runtime_tasks: list[dict[str, Any]] = []
    for page in pages:
        runtime_tasks.append({
            "task_id": f"RTASK-{page['page_id']}",
            "task_type": "VERIFY_PAGE_DEFAULT_STATE",
            "page_id": page["page_id"],
            "symbol": page["symbol"],
            "trigger": "AUTO_LAUNCH_OR_ROUTE",
            "source_refs": ["app/Login.kt:1"],
            "verification_mode": "RUNTIME_UI",
            "review_tier": "REQUIRED",
        })
        runtime_tasks.append({
            "task_id": f"RTASK-{page['page_id']}-BR1",
            "task_type": "VERIFY_STATE_BRANCH",
            "page_id": page["page_id"],
            "symbol": page["symbol"],
            "trigger": "AUTO_LAUNCH_OR_ROUTE",
            "source_refs": ["app/Login.kt:2"],
            "verification_mode": "RUNTIME_UI",
            "review_tier": "REVIEW",
        })
    static_dir = ws / "static-analysis"
    static_dir.mkdir(parents=True, exist_ok=True)
    (static_dir / "runtime-tasks.json").write_text(
        json.dumps({"schema_version": 1, "tasks": runtime_tasks}, ensure_ascii=False),
        encoding="utf-8",
    )
    return ws


def _preset_run_controller(run_dir: Path, project: Path) -> None:
    controller = run_dir / "controller"
    (controller / "work-orders").mkdir(parents=True, exist_ok=True)
    (controller / "scope.json").write_text(
        json.dumps({
            "run_id": "MIG-STAGE3-GMI",
            "project_id": "PRJ-FIXTURE",
            "project_name": "Fixture",
            "migration_scope": {
                "included_features": ["FEATURE-AUTH"],
                "excluded_features": [],
                "parity_dimensions": ["visual", "functional", "asset", "navigation"],
            },
            "ownership": {
                "migration_controller_id": "fixture-controller",
                "inventory_lead_id": "inventory-lead-gmi",
                "code_map_agent_id": "gmi-codemap",
                "business_rule_agent_id": "gmi-bizrule",
                "data_dependency_agent_id": "gmi-datadep",
                "evidence_administrator_id": "gmi-evidence",
                "coverage_checker_id": "gmi-coverage",
                "runtime_state_agent_ids": ["gmi-runtime"],
            },
            "tool_policy": {
                "runtime_ui_tool": "android-cli",
                "layout_inspector_allowed": False,
                "apk_analyzer_bin": str(HERE / "fake_harmony.py"),
            },
            "pending_confirmations": [],
            "android": {
                "application_id": "com.example.fixture",
                "project_root": str(project.resolve()),
                "source_revision": _android_revision(project),
                "source_revision_kind": "git-commit",
                "apk_path": str((project.parent / "fixture" / "fixture.apk").resolve()),
                "apk_sha256": sha256(project.parent / "fixture" / "fixture.apk"),
                "app_version": "1.0.0",
                "app_build": "1",
                "build_variant": "debug",
            },
            "target": {
                "platform": "HarmonyOS NEXT",
                "sdk_or_api_target": "fixture-api",
                "device_classes": ["phone"],
            },
            "environments": [{
                "env_id": "ENV-001",
                "is_baseline": True,
                "account_id": "ACCOUNT-FIXTURE",
                "account_role": "user",
                "seed_data_id": "SEED-FIXTURE",
                "seed_reset_ref": "SEED-RESET-FIXTURE",
                "network_profile": "normal",
                "network_conditions_ref": "NETCOND-FIXTURE",
                "network_toggle_available": True,
                "emulator_model": "Fixture Emulator",
                "device_serial": "fixture-001",
                "resolution": "320x640",
                "density_dpi": 420,
                "android_api_level": 34,
                "locale": "zh-CN",
                "theme": "light",
                "font_scale": 1.0,
                "timezone": "Asia/Shanghai",
                "permissions_profile": "fresh-install",
                "orientation": "portrait",
            }],
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "run-manifest.json").write_text(
        json.dumps({
            "run_id": "MIG-STAGE3-GMI",
            "project_id": "PRJ-FIXTURE",
            "project_root": str(project.resolve()),
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    (controller / "gate-report.json").write_text(
        json.dumps({"phase": 2, "verdict": "PASS", "generated_by": "fixture"}) + "\n",
        encoding="utf-8",
    )
    (controller / "evidence-anchor-registry.csv").write_text("evidence_id\n", encoding="utf-8")
    (controller / "decision-log.csv").write_text(
        "decision_id,phase,decision,decided_by,reason,evidence_refs,decided_at\n",
        encoding="utf-8",
    )
    (controller / "rework-log.csv").write_text(
        "rework_id,created_at,phase,record_id,feature_id,page_id,state_id,env_id,evidence_id,"
        "gate_rule,reason,assigned_to,completion_condition,status,resolved_at,"
        "resolution_evidence_id,reviewed_by\n",
        encoding="utf-8",
    )
    (controller / "phase4-attempt-ledger.csv").write_text(
        (SCAFFOLD_SKILL.parent / "android-harmony-migration-controller" / "assets"
         / "phase4-attempt-ledger.template.csv").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    order_relative = "work-orders/PHASE3-GMI-FIXTURE.json"
    (controller / "work-orders" / "PHASE3-GMI-FIXTURE.json").write_text(
        json.dumps({
            "work_order_id": "PHASE3-GMI-FIXTURE",
            "phase": 3,
            "status": "OPEN",
            "ownership": OWNERSHIP,
            "generated_by": "fixture",
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    (controller / "work-order-registry.csv").write_text(
        "work_order_id,phase,relative_path,scope_sha256,work_order_sha256,issued_at,issued_by,status\n"
        "PHASE3-GMI-FIXTURE,3,controller/{0},{1},{2},2026-08-25T00:00:00Z,fixture-controller,OPEN\n".format(
            order_relative,
            sha256(controller / "scope.json"),
            sha256(controller / "work-orders" / "PHASE3-GMI-FIXTURE.json"),
        ),
        encoding="utf-8",
    )
    (controller / "task-ledger.csv").write_text(
        "task_id,phase,task,owner,status,depends_on,updated_at,notes\n"
        "TASK-PHASE-01,1,Freeze migration scope and baselines,fixture-controller,PASS,,2026-08-25T00:00:00Z,gmi fixture scope\n"
        "TASK-PHASE-02,2,Produce Android state inventory and evidence chain,inventory-lead-gmi,PASS,TASK-PHASE-01,2026-08-25T00:00:00Z,gmi closure\n"
        "TASK-PHASE-03,3,Create and independently accept HarmonyOS scaffold,architecture-lead-1,NOT_STARTED,TASK-PHASE-02,,\n"
        "TASK-PHASE-04,4,Implement and accept HarmonyOS feature parity,implementation-lead-4,NOT_STARTED,TASK-PHASE-03,,\n",
        encoding="utf-8",
    )


def _write_lane_evidence(ws: Path) -> None:
    """Hand-write both lane evidence directories as if the frozen queues had
    been executed to VERIFIED on two distinct serials (real gmi_audit.py then
    re-plays every hash/identity/queue check over these artifacts)."""
    ev = ws / "runtime-evidence"
    for lane in ("A", "B"):
        queue_path = ev / f"runtime-queue-{lane.lower()}.json"
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        tasks = queue.get("tasks", [])
        serial = LANE_SERIALS[lane]
        lane_dir = ev / f"lane-{lane.lower()}"
        lane_dir.mkdir(parents=True, exist_ok=True)
        (lane_dir / "lane-meta.json").write_text(
            json.dumps({
                "schema_version": 1,
                "lane": lane,
                "device_serial": serial,
                "queue_file": f"runtime-evidence/runtime-queue-{lane.lower()}.json",
                "queue_tasks": [t["task_id"] for t in tasks],
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        evidence_rows: list[dict[str, Any]] = []
        gate_rows: list[dict[str, Any]] = []
        for task in tasks:
            symbol = str(task.get("symbol", ""))
            for tag in ("before", "after"):
                d = lane_dir / str(task["task_id"]) / tag
                d.mkdir(parents=True, exist_ok=True)
                (d / "ui.xml").write_text(ui_xml(symbol), encoding="utf-8")
                fake_harmony.write_png(d / "screenshot.png", 320, 640)
                evidence_rows.append({
                    "task_id": task["task_id"],
                    "page_id": task.get("page_id", ""),
                    "tag": tag,
                    "foreground": f"{PKG}/{PKG}.{symbol}",
                    "ui_sha256": sha256(d / "ui.xml"),
                    "png_sha256": sha256(d / "screenshot.png"),
                    "screen_resolution": LANE_SCREEN["resolution"],
                    "screen_density": LANE_SCREEN["density"],
                    "capture_slot": lane,
                    "device_serial": serial,
                })
            gate_rows.append({
                "task_id": task["task_id"],
                "journey_id": f"JOURNEY-{task.get('page_id', '')}",
                "page_id": task.get("page_id", ""),
                "symbol": symbol,
                "status": "VERIFIED",
                "evidence": f"lane-{lane.lower()}/{task['task_id']}/after/ui.xml",
                "capture_slot": lane,
                "device_serial": serial,
                "verification_mode": task.get("verification_mode", "RUNTIME_UI"),
                "review_tier": task.get("review_tier", "REQUIRED"),
            })
        write_rows(lane_dir / "lane-evidence-index.csv", gmi_rt.LANE_EVIDENCE_FIELDS, evidence_rows)
        write_rows(lane_dir / "lane-runtime-gate.csv", gmi_rt.LANE_GATE_FIELDS, gate_rows)



def build_gmi_run(
    root: Path, pages: list[dict[str, Any]], *, with_asset: bool = True
) -> tuple[Path, Path]:
    """Return (run_dir, gmi_workspace) with a frozen gmi Phase 2 closure."""
    project = _android_project(root)
    ws = _gmi_workspace(root, project, pages, with_asset)
    run(
        sys.executable, str(INVENTORY_SKILL / "scripts" / "gmi_runtime.py"),
        "--workspace", str(ws), "--split-queues",
    )
    _write_lane_evidence(ws)
    run(
        sys.executable, str(INVENTORY_SKILL / "scripts" / "gmi_audit.py"),
        "--project", str(project), "--workspace", str(ws), "--package", PKG,
    )
    # 桥接已移除（P3 链路修复）：不再追加页级 VISITED 行——adapter 现按
    # task 级聚合（全部 VERIFIED）判定 visited 页 ACCEPTED；adapter 亦直接
    # 落盘确定性的 advanced-gate decision_source，无需后处理重绑哈希链。
    run(
        sys.executable, str(INVENTORY_SKILL / "scripts" / "gmi_closure.py"),
        "--workspace", str(ws),
    )
    acceptance_dir = ws / "human-review"
    acceptance_dir.mkdir(parents=True, exist_ok=True)
    (acceptance_dir / "phase-2-acceptance.json").write_text(
        json.dumps({
            "decision": "ACCEPTED",
            "reviewer_id": "fixture-human-reviewer",
            "accepted_at": "2026-08-28T00:00:00Z",
            "closure_sha256": sha256(ws / "phase-2-closure.json"),
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    run_dir = root / "run"
    _preset_run_controller(run_dir, project)
    run(
        sys.executable, str(INVENTORY_SKILL / "scripts" / "gmi_phase3_adapter.py"),
        "--workspace", str(ws), "--out", str(run_dir),
    )

    return run_dir, ws


LOGIN_PAGE = {"page_id": "PAGE-LOGIN-A1", "symbol": "LoginActivity", "kind": "ACTIVITY", "visited": True}
DIALOG_PAGE = {"page_id": "PAGE-FILTER-B2", "symbol": "FilterDialog", "kind": "DIALOG", "visited": False}
AMBIGUOUS_PAGE = {"page_id": "PAGE-HELP-C3", "symbol": "HelpComposable", "kind": "COMPOSABLE", "visited": False}