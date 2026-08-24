# Phase 2 handoff

## Work order sent to Android inventory

Pass the migration run directory, frozen `controller/scope.json`, and the immutable JSON work order created by `issue_phase2_work_order.py`. The work order includes:

- Project and run IDs.
- Android project root, source revision, installable APK, app version, build, application ID, and build variant.
- Included and excluded feature scope.
- HarmonyOS target version and target device classes.
- Accounts and roles.
- Environment registry with seed data, network profiles, network toggle availability, emulator model, resolution, density, Android/API version, locale, theme, font scale, timezone, and permissions.
- Required tool: Android CLI.
- Prohibited tool: Layout Inspector.
- Frozen IDs for the inventory lead, evidence administrator, and sole coverage checker.

Do not dispatch Phase 2 when Phase 1 is not `PASS`.

Dispatch every ownership entry as a distinct real CodeArts task. After deterministic closure, record one immutable team-execution receipt per assigned actor. Phase 3 work-order issuance rejects missing receipts, duplicate platform task IDs, actor/role mismatches, and changed artifact hashes.

## Package returned by Android inventory

Expect these files below `phase-02-android-inventory/`:

- `environments.json`
- `coverage-ledger.csv`
- `catalogs/`
- `inventory.csv`
- `asset-inventory.csv`
- `asset-package/manifest.sha256`
- `asset-package/COMMITTED`
- `static-analysis/`（页面、组件、事件、跳转、状态候选和高级风险）
- `runtime-observations.json`
- `page-gate-report.json`
- `advanced-observations.json`
- `probe-evidence-index.csv`
- `advanced-gate-report.json`
- `evidence-anchors.snapshot.csv`
- `evidence-index.csv`
- `acceptance-registry.csv`
- `evidence/`
- `rechecks.csv`
- `closure-report.json`
- `closure-manifest.sha256`
- `CLOSED`

The inventory formula is fixed:

> One row = one `Feature-ID` x one `Page-ID` x one `State-ID` x one `ENV-ID` x one `Evidence-ID`.

The closure report must name the coverage checker, match the baseline environment in `scope.json`, and state that the evidence chain is closed.
The controller recomputes the closure manifest and rejects any package changed after review.

`page-gate-report.json` 和 `advanced-gate-report.json` 必须由确定性脚本计算为 `PASS`。模型给出的总结、置信度或“看起来正确”不能代替门禁结果。每个静态页面对象必须关联真实运行证据；事件和跳转还必须同时提供操作前、操作后的证据。

Each active inventory row has a nonempty JSON `asset_ids` array: exactly `["NONE_FOUND"]` or real Asset-IDs. Each real asset is reviewed, linked back to at least one feature/page/state row, archived below `asset-package/files/<Asset-ID>/`, listed exactly once in the asset-package manifest, and sealed by `COMMITTED`.
