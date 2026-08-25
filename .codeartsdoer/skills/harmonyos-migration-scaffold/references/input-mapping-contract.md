# Input and mapping contract

## Controller work order first

Phase 3 may start only from a controller-issued and controller-registered Phase 3 work order. The work order freezes six distinct IDs: architecture lead, toolchain agent, navigation agent, public UI agent, capability-contract agent, and architecture-acceptance agent. `init_scaffold.py` requires `--work-order` and rejects an architecture lead that differs from that frozen assignment.

The initializer runs the controller's Phase 2 gate validator again **without `--write`**. It then proves that Phase 2 is `CLOSED`, its closure manifest still covers the exact package, and its `CLOSED` marker still binds the closure report. A cached `PASS` string alone is not an input.

## Immutable Phase 2 input

Phase 3 creates `stage-03-input-lock.json` containing canonical paths, immutable snapshot paths, SHA-256 values, row counts, IDs, work-order identity, and frozen ownership for:

- controller scope;
- the copied Phase 2 controller-gate snapshot (its `path` deliberately does not point at the live gate, which becomes Gate 3 later);
- Phase 2 closure report, closure manifest, `CLOSED`, and Phase 2 manifest;
- Phase 2 `inventory.csv`, acceptance registry, and evidence index;
- Phase 2 `asset-inventory.csv`, asset-package manifest and `COMMITTED` marker;
- each real archived asset file, by canonical Phase 2 path and SHA-256;
- Phase 2 evidence-anchor snapshot and the matching controller anchor registry;
- data-dependency, system-capability, and third-party-dependency catalogs;
- the committed advanced analysis, advanced observations, deterministic advanced gate report, and probe-evidence index;
- the registered Phase 3 work order.

All of these small input records are copied byte-for-byte into `inputs/` for later review. Except for the live controller gate, canonical Phase 2 files remain the authoritative read-only source paths; their copies are snapshots, not editable replacements. The initializer never edits, normalizes, or rewrites Phase 2 inventory or evidence.

Every active Phase 2 inventory row must already be `REVIEWED` by the frozen coverage checker. The evidence index and acceptance registry must exactly cover those active rows with `ACCEPTED`, and the Phase 2 anchor snapshot must exactly equal the controller-owned Phase 2 anchor rows. Superseded history remains in the locked source file but does not create a Phase 3 shell.

Every active, non-superseded Phase 2 row receives a deterministic `Source-Row-Key`:

```text
SROW-<SHA256(feature_id|page_id|state_id|env_id|evidence_id)[0:20]>
```

The Phase 3 gate recomputes the key and all input hashes. Any drift is `BLOCKED`.

## ArkUI template provenance

Phase 3 uses the bundled `assets/arkui-stage-template` as the only scaffold source. Initialization copies it into a staging workspace, excludes caches and machine-local files, replaces the frozen application identity, rejects unresolved tokens, and then atomically installs it as `harmony-project/`.

`inputs/arkui-stage-template.manifest.sha256` records every source-template file. `template-generation.json` records the template ID, manifest digest, generated-file count, required Stage files, bundle name, application name, vendor, log tag, and initial project-manifest digest. The Phase 3 validator and controller Gate 3 recheck this provenance and the required project files.

## Advanced Phase 2 handoff

`advanced-obligations.json` must be one-to-one with the frozen dynamic-risk, side-effect, and scenario IDs:

- dynamic risks become `PHASE4_DYNAMIC_SURFACE` obligations;
- side effects become `PHASE3_CAPABILITY_CONTRACT` obligations and seed one interface requirement for every included Feature-ID;
- special scenarios become `PHASE4_SCENARIO_TEST` obligations.

The scaffold does not implement these behaviors. It preserves them so later phases cannot lose WebView/dynamic content, database/network/background effects, permissions, special accounts, abnormal data, or extreme states.

## Asset landing mapping

Initialization validates the complete Phase 2 asset package and seeds exactly one `asset-registry.csv` row per real `Asset-ID`. `NONE_FOUND` never creates a registry row. The source identity columns—archive path, hash, type, Feature/Page/State arrays—remain byte-for-byte equal to `asset-inventory.csv`.

The architecture lead completes each row with a real target module, a safe project-relative resource path below that module's `src/main/resources/`, a module-unique resource symbol, and one allowed plan/decision pair:

- `DIRECT_COPY` / `COPY_UNCHANGED`
- `FORMAT_CONVERSION` / `CONVERT_FORMAT`
- `RECREATE_FROM_PUBLIC_UI` / `RECREATE_LATER`

Set `created_by` to the frozen architecture lead and `status` to `READY`. Phase 3 records the landing decision only; copying, converting, or recreating the asset belongs to implementation work under a later order.

## Visual mapping

Exactly one `architecture-map.csv` row must exist for every active frozen inventory row:

```text
Source-Row-Key
→ Mapping-Type
+ Harmony-Module-ID
+ Route-ID or Surface-Shell-ID
+ Page-Shell-ID
+ actual source path
+ one or more Screenshot-IDs
+ Verification-ID
```

Allowed visual mapping types:

- `ROUTE_PAGE`: the original surface was independently navigable; use a real route.
- `VISUAL_SURFACE`: dialog, sheet, tab body, embedded surface, or other non-route visual surface; use a real surface shell.
- `EXCLUDED_BY_SCOPE`: allowed only when the Feature-ID was already excluded in Phase 1.

Multiple states of the same page may point to the same route or surface shell, but every active source row must remain present. Superseded Phase 2 history stays hash-locked in the input snapshot and is not silently converted into a current shell.

Every visual mapping row references the sealed `HSCREEN-ID` values that prove its real shell on every emulator marked `screenshot_required`. Multiple state rows may reuse one screenshot only when they map to the same Page-Shell-ID and route/surface target. Nonvisual capability rows never receive screenshots.

## Nonvisual mapping

Initialization resolves every Phase 2 `data_dependency_refs`, `system_capability_refs`, and `third_party_dependency_refs` value against its locked catalog before seeding `capability-contracts.csv`. An explicit verified `NONE_FOUND` sentinel proves that the category was checked; it **never** becomes a fake HarmonyOS capability requirement. The initializer also seeds a scope-sourced requirement for an included Feature-ID that has no active visual inventory row.

Each requirement maps to:

```text
Capability-Requirement-ID
→ Harmony-Module-ID
+ Capability-Contract-ID
+ contract symbol
+ actual interface file
+ source inventory keys or scope Feature-ID
```

Do not fabricate a page, route, screenshot, or Page-ID for nonvisual work.

## Migration status

`migration-status.csv` contains a separate row for every visual source key and capability requirement. It never changes Phase 2.

Allowed states:

- `NOT_STARTED`
- `SHELL_CREATED_PENDING_IMPLEMENTATION`
- `CONTRACT_CREATED_PENDING_IMPLEMENTATION`
- `BLOCKED`
- `EXCLUDED_BY_SCOPE`

Gate `PASS` permits only `SHELL_CREATED_PENDING_IMPLEMENTATION`, `CONTRACT_CREATED_PENDING_IMPLEMENTATION`, and Phase-1-authorized exclusions.


---

# gmi Phase 2 适配（默认路径）

当 Phase 2 由 gmi（`scripts/gmi.py | gmi_runtime.py | gmi_audit.py`）产生时，
本节覆盖上方"Controller work order first"与"Immutable Phase 2 input"两节的旧工件要求；
旧流程（controller + inventory.csv REVIEWED 链）仍受上方契约约束，gmi 流程走本节。

## 门禁前置（替代 controller Gate + inventory.csv REVIEWED 链）

gmi 流程必须满足以下四项，任一不满足即 `BLOCKED - gmi-gate-incomplete`：

1. `coverage/coverage-ledger.csv`：`UNMAPPED=0`（`GAP 0`）。
2. `candidates/phase-2-completeness.csv`：所有 `MISSING` 均已逐页列出并有 `hint`，
   不存在静默缺失（`N/A` 仅限无 UI 的纯容器页，须在报告中注明）。
3. `runtime-evidence/audit-replay.csv`：`discrepancy=no` 全量（0 伪造）；
   `runtime-gate.csv` 的 `VISITED` 状态与审计重放完全一致。
4. `phase-2-report.md` 或等价产物说明：明确列出
   `P / VISITED / NOT_ENTERED(+reason) / risk-probes 高危 TOP`。

满足后可生成 `phase-2-closure.json`（结构见下节）作为 Phase 3 输入，替代旧
`CLOSED + closure-manifest.json`。

## 工件映射（gmi 12 表 -> 本契约字段）

| 旧契约字段 | gmi 来源 |
|---|---|
| `inventory.csv` 活跃行 | `candidates/inventory.candidates.csv` + `page-fields.candidates.csv`（每页合成一行，feature/page/state 三列合并） |
| Source-Row-Key | 保持 `SROW-<SHA256(feature_id|page_id|state_id|env_id)[0:20]>`；env 缺省用 `ENV-001` |
| `asset-inventory.csv` | `candidates/asset-mapping.candidates.csv`（`type=FILE_ASSET` 行即资产；`sha256` 取自 `resolved_value`，与 `candidates.json` 中对应计数核对） |
| evidence index / acceptance registry | `runtime-evidence/evidence-index.csv`（ui/png 双哈希）与 `runtime-evidence/runtime-gate.csv`（`VISITED`=ACCEPTED，`NOT_ENTERED` 须注明 reason） |
| evidence packages | `runtime-evidence/<page_id>/ui.xml + screenshot.png`；Evidence-ID 用该目录名 |
| `advanced-analysis.json` / 动态风险 | `candidates/risk-probes.candidates.csv`（含 severity + harmony_hint）；高危 TOP 引用其 `severity=高` 子集 |
| data/system/third-party catalogs | `candidates/third-party-dependencies.candidates.csv`；data/system 类以 `RISK-` probe 与 `phase-2-completeness` 为参考 |

## 判定变化

- 不再要求每个活跃行 `REVIEWED`（gmi 无 REVIEWED 状态机）；以
  "**audit 0 discrepancy + UNMAPPED=0 + completeness 无隐瞒**" 等价替代。
- `architecture-map.csv` 可视映射、`migration-status.csv` 状态校验、
  `HSCREEN-ID` 截图要求等**全部不变**——它们只依赖 Source-Row-Key 与页面/状态/环境 ID
  （gmi 的 page_id 已按 `PAGE-<SYMBOL>-<hash>` 生成，直接可用）。
- 截图证明从实时抓取改为引用 `runtime-evidence/` 已有证据；若某 `screenshot_required`
  页面在 runtime 证据中为 `NOT_ENTERED`，提交 `BLOCKED - missing-runtime-evidence`，
  除非该页面在 phase-2-report 中有明确 `reason`（如需账号/需 Shizuku/需长按），
  此时允许以 "RECREATE_FROM_PUBLIC_UI + 推导路径" 进入实现，但必须在 `architecture-map.csv`
  该行 notes 注明依据。

## phase-2-closure.json 结构（gmi 路径的闭包证明）

```json
{
  "generator": "gmi",
  "workspace": "<out>",
  "package": "<pkg>",
  "closure_at": "<ISO8601>",
  "gate": {
    "unmapped": 0,
    "completeness_missing_total": <N>,
    "audit_discrepancy": 0,
    "visited": <N_V>,
    "pages_total": <P>
  },
  "artifact_hashes": {
    "candidates_dir_sha256": "<...>",
    "coverage_ledger_sha256": "<...>",
    "runtime_evidence_dir_sha256": "<...>"
  }
}
```

`phase-2-closure.json` 生成后即冻结；Phase 3 各脚本读取时校验目录哈希与 gate 字段，
漂移即 `BLOCKED`。
