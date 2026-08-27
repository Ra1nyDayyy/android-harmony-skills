# Phase 2 handoff

Phase 2 is the gmi sole path: invoke `$android-migration-inventory` in gmi mode. The work order created by `issue_phase2_work_order.py` is an authorization record only; the Phase 2 data input and output are always the gmi artifact chain, not manual page enumeration.

## gmi artifact chain returned by Android inventory

Expect these artifacts below `phase-02-android-inventory/gmi/`:

- `candidates/` — the 13 candidate tables (code-map, business-rules, asset-mapping, inventory, page-fields, third-party-dependencies, field-options, navigation-relations, behavior, risk-probes, color-palette, motion, phase-2-completeness) plus `manifest.sha256`;
- `coverage/coverage-ledger.csv`;
- `runtime-evidence/` — `runtime-gate.csv` and `audit-replay.csv`;
- `audit-replay.csv`;
- `phase-2-closure.json` (`unmapped=0`, `audit_discrepancy=0`);
- `phase-manifest.json` with `generator=gmi`.

After the gmi run closes automatically, the controller recomputes Gate 2 from this chain (conditions in [phase-gates.md](phase-gates.md)); the human checkpoint follows the machine closure.

## Work order sent to Android inventory

Pass the migration run directory, frozen `controller/scope.json`, and the immutable JSON work order. The work order includes:

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

## Legacy asset chain (pre-gmi runs only)

Legacy runs additionally return `environments.json`, `inventory.csv`, `asset-inventory.csv`, `asset-package/{manifest.sha256,COMMITTED}`, static-analysis records, runtime observations, gate reports, `evidence-index.csv`, `acceptance-registry.csv`, `rechecks.csv`, `closure-report.json`, `closure-manifest.sha256`, and `CLOSED` below `phase-02-android-inventory/`. Anchor every sealed Android evidence package with `scripts/anchor_phase2_evidence.py`. The controller recomputes the closure manifest and rejects any package changed after review. Each active legacy inventory row has a nonempty JSON `asset_ids` array: exactly `["NONE_FOUND"]` or real Asset-IDs, each reviewed, linked, archived below `asset-package/files/<Asset-ID>/`, listed exactly once in the manifest, and sealed by `COMMITTED`.
