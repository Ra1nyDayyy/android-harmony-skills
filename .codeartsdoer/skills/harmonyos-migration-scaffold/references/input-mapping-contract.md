# Input and mapping contract (gmi Phase 2 closure only)

Phase 3 starts exclusively from the frozen **gmi Phase 2 closure** produced by the
gmi chain (`gmi_generate → gmi_runtime → gmi_audit → gmi_closure → gmi_phase3_adapter`).
The former controller/REVIEWED-chain entry is no longer accepted; `init_scaffold.py`
rejects a run directory that carries no gmi closure certificate.

## gmi gate preconditions (any failure is `BLOCKED - gmi-gate-incomplete`)

`init_scaffold.py` re-verifies the four machine-checkable gate inputs before
anything is copied. Any one of them missing or failing raises and blocks:

1. `coverage/coverage-ledger.csv`: `UNMAPPED=0` — no row may have `status=GAP`.
2. `candidates/phase-2-completeness.csv`: every `MISSING` row must carry a
   per-item `hint` (no silent gaps); `N/A` is allowed only for pure container
   pages without UI and must be noted in the Phase 2 report.
3. `runtime-evidence/audit-replay.csv`: `discrepancy=no` for every row
   (0 fabricated states).
4. `runtime-evidence/runtime-gate.csv`: its `VISITED`/`NOT_ENTERED` statuses must
   agree with the audit replay; a cached PASS string alone is not an input.

`phase-2-closure.json` is then the closure certificate. Once written it is
frozen: Phase 3 scripts re-check the recorded directory hashes and gate fields
on every read; drift is `BLOCKED`. If the `CLOSED` marker exists, it must bind
the closure report hash.

## Phase 3 input layout

`gmi_phase3_adapter.py` synthesizes the canonical `phase-02-android-inventory/`
layout from the gmi workspace (candidates 13 表 + runtime-evidence). The
information source is always gmi; synthesized files are marked
`generated-by=gmi-phase3-adapter`:

| Phase 3 contract file | gmi source |
| --- | --- |
| `inventory.csv` active rows | `candidates/inventory.candidates.csv` + `page-fields.candidates.csv`（每页合成一行，feature/page/state 合并） |
| Source-Row-Key | `SROW-<SHA256(feature_id\|page_id\|state_id\|env_id\|evidence_id)[0:20]>`；env 缺省用 `ENV-001` |
| `asset-inventory.csv` | `candidates/asset-mapping.candidates.csv`（`type=FILE_ASSET` 行；归档实体与哈希实测落盘） |
| evidence index / acceptance registry | `runtime-evidence/evidence-index.csv`（ui/png 双哈希）与 `runtime-evidence/runtime-gate.csv`（`VISITED`=ACCEPTED） |
| evidence packages | `runtime-evidence/<page_id>/ui.xml + screenshot.png`；Evidence-ID 用该目录名 |
| `advanced-analysis.json` / dynamic risks | `candidates/risk-probes.candidates.csv`（含 severity + harmony_hint）；高危 TOP 引用其 `severity=高` 子集 |
| data/system/third-party catalogs | `candidates/third-party-dependencies.candidates.csv`；data/system 类以 `RISK-` probe 与 completeness 为参考，`NONE_FOUND` 为显式 sentinel |

判定口径：不再要求逐行 `REVIEWED` 状态机（gmi 无此状态），以
"audit 0 discrepancy + UNMAPPED=0 + completeness 无隐瞒" 等价替代。
截图证明引用 `runtime-evidence/` 已有证据；`screenshot_required` 页面若为
`NOT_ENTERED`，提交 `BLOCKED - missing-runtime-evidence`，除非 phase-2-report
给出明确 `reason`（需账号/需 Shizuku/需长按），此时允许
"RECREATE_FROM_PUBLIC_UI + 推导路径" 并在 `architecture-map.csv` notes 注明依据。

`stage-03-input-lock.json` freezes canonical paths, SHA-256 values, row counts,
IDs, work-order identity, and frozen ownership for: controller scope, the Phase 2
gate snapshot, closure report, phase manifest, inventory, asset inventory and
package, acceptance/evidence/anchor records, catalogs, advanced analysis, and the
registered Phase 3 work order. Small input records are copied byte-for-byte into
`inputs/`; the initializer never edits, normalizes, or rewrites gmi evidence.

## ArkUI template provenance

Phase 3 uses the bundled `assets/arkui-stage-template` as the only scaffold
source. Initialization copies it into a staging workspace, excludes caches and
machine-local files, replaces the frozen application identity, rejects
unresolved tokens, and then atomically installs it as `harmony-project/`.

`inputs/arkui-stage-template.manifest.sha256` records every source-template
file. `template-generation.json` records the template ID, manifest digest,
generated-file count, required Stage files, bundle name, application name,
vendor, log tag, and initial project-manifest digest. The Phase 3 validator
rechecks this provenance and the required project files.

## Advanced Phase 2 handoff

`advanced-obligations.json` must be one-to-one with the frozen dynamic-risk,
side-effect, and scenario IDs:

- dynamic risks become `PHASE4_DYNAMIC_SURFACE` obligations;
- side effects become `PHASE3_CAPABILITY_CONTRACT` obligations and seed one
  interface requirement for every included Feature-ID;
- special scenarios become `PHASE4_SCENARIO_TEST` obligations.

The scaffold does not implement these behaviors. It preserves them so later
phases cannot lose WebView/dynamic content, database/network/background effects,
permissions, special accounts, abnormal data, or extreme states.

## Asset landing mapping

Initialization validates the complete Phase 2 asset package and seeds exactly
one `asset-registry.csv` row per real `Asset-ID`. `NONE_FOUND` never creates a
registry row. The source identity columns—archive path, hash, type,
Feature/Page/State arrays—remain byte-for-byte equal to the gmi asset inventory.

The architecture lead completes each row with a real target module, a safe
project-relative resource path below that module's `src/main/resources/`, a
module-unique resource symbol, and one allowed plan/decision pair:

- `DIRECT_COPY` / `COPY_UNCHANGED`
- `FORMAT_CONVERSION` / `CONVERT_FORMAT`
- `RECREATE_FROM_PUBLIC_UI` / `RECREATE_LATER`

Set `created_by` to the frozen architecture lead and `status` to `READY`.
Phase 3 records the landing decision only; copying, converting, or recreating
the asset belongs to implementation work under a later order.

## Visual mapping and carrier decision

Exactly one `architecture-map.csv` row must exist for every active frozen
inventory row:

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

`mapping_type` is decided from the Phase 2 carrier kinds
(`static-analysis/pages.json` `kinds`, derived from the page symbol):

- `ROUTE_PAGE`: only proven routable carriers (`ACTIVITY`/`SCREEN`/`PAGE`/`VIEW`)
  — the original surface was independently navigable; use a real route.
- `VISUAL_SURFACE`: dialog, bottom sheet, overlay, widget, popup, picker,
  embedded, or tab-body carriers — use a real surface shell
  (`surface_kind` carries the detected carrier).
- `EXCLUDED_BY_SCOPE`: allowed only when the Feature-ID was already excluded in
  Phase 1.

An unknown, empty, or ambiguous carrier is never silently defaulted:
initialization fails with `BLOCKED - carrier-undecidable`, naming the page and
the missing carrier field. `ROUTE_PAGE` rows never set `surface_shell_id` and
`VISUAL_SURFACE` rows never set `route_id`.

Multiple states of the same page may point to the same route or surface shell,
but every active source row must remain present. Superseded history stays
hash-locked in the input snapshot and is not silently converted into a current
shell. Every visual mapping row references the sealed `HSCREEN-ID` values that
prove its real shell on every emulator marked `screenshot_required`. Multiple
state rows may reuse one screenshot only when they map to the same
Page-Shell-ID and route/surface target. Nonvisual capability rows never receive
screenshots.

## Nonvisual mapping

Initialization resolves every Phase 2 `data_dependency_refs`,
`system_capability_refs`, and `third_party_dependency_refs` value against its
locked catalog before seeding `capability-contracts.csv`. An explicit verified
`NONE_FOUND` sentinel proves that the category was checked; it **never** becomes
a fake HarmonyOS capability requirement. The initializer also seeds a
scope-sourced requirement for an included Feature-ID that has no active visual
inventory row.

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

`migration-status.csv` contains a separate row for every visual source key and
capability requirement. It never changes Phase 2.

Allowed states:

- `NOT_STARTED`
- `SHELL_CREATED_PENDING_IMPLEMENTATION`
- `CONTRACT_CREATED_PENDING_IMPLEMENTATION`
- `BLOCKED`
- `EXCLUDED_BY_SCOPE`

Gate `PASS` permits only `SHELL_CREATED_PENDING_IMPLEMENTATION`,
`CONTRACT_CREATED_PENDING_IMPLEMENTATION`, and Phase-1-authorized exclusions.

## phase-2-closure.json structure

```json
{
  "generator": "gmi_closure",
  "workspace": "<out>",
  "closure_at": "<ISO8601>",
  "gate": {
    "unmapped": 0,
    "completeness_missing_total": 0,
    "audit_discrepancy": 0,
    "visited": 1,
    "pages_total": 1
  },
  "artifact_hashes": {
    "candidates_dir_sha256": "<...>",
    "coverage_ledger_sha256": "<...>",
    "runtime_evidence_dir_sha256": "<...>"
  }
}
```
