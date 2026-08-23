---
name: android-migration-inventory
description: Build a migration-grade inventory of an existing Android app using Android CLI runtime captures, source mapping, state-level records, frozen environments, and an immutable evidence chain. Use before an Android-to-HarmonyOS migration; do not use it to implement the HarmonyOS app or as a generic code-quality review.
---

# Android Migration Inventory

Produce a complete, reproducible description of the Android app before migration work begins. Treat observable states, not pages alone, as the inventory unit.

## Hard rules

- Runtime inspection uses Android CLI. Layout Inspector is prohibited for formal evidence.
- Do not create, accept, or reference MP4 files.
- One inventory row equals one feature, one page, one state, one environment, and one evidence ID.
- Every inventory row explicitly lists real `Asset-ID` values or exactly `["NONE_FOUND"]`.
- Every conclusion cites a file/line, a formal evidence ID, or both. Mark unsupported facts `PENDING_CONFIRMATION`.
- Evidence is immutable. Recapture with a new evidence ID; never overwrite a sealed package.
- The evidence administrator issues and seals evidence. The coverage checker is the only final reviewer.
- A given emulator or device is controlled by only one runtime-state agent at a time.

## Initialize Phase 2

Read [references/roles-and-authority.md](references/roles-and-authority.md) and [references/environment-contract.md](references/environment-contract.md). Phase 1 must already be `PASS`.

```bash
python3 scripts/init_inventory.py \
  --run-dir <migration-run> \
  --scope <migration-run>/controller/scope.json \
  --work-order <migration-run>/controller/work-orders/<work-order>.json \
  --frozen-by <inventory-lead-id>
```

Initialization verifies the current scope/work-order digests, clean Git revision, APK hash, Android CLI version and help output, runs `android describe`, copies the frozen environments, and creates the coverage, catalog, evidence, and inventory registries. If preflight cannot complete, stop Phase 2 as `BLOCKED`; do not fall back to Layout Inspector.

Before capture, the frozen inventory lead confirms the manual parts of each applicable environment:

```bash
python3 scripts/attest_environment.py \
  --workspace <migration-run>/phase-02-android-inventory \
  --env-id <ENV-ID> \
  --inventory-lead-id <inventory-lead-id> \
  --account-ready --seed-ready --network-ready --permissions-ready
```

## Divide the work

The inventory lead dispatches four independent lenses:

- Code-map agent: modules, entries, routes, pages, state candidates, resources, and file/line references.
- Runtime-state agent: user journeys and observable states on the frozen environment.
- Business-rule agent: entry conditions, validations, roles, feature flags, and transitions.
- Data-dependency agent: APIs, local data, permissions, SDKs, system capabilities, and native libraries.

Read [references/inventory-contract.md](references/inventory-contract.md) before creating claim records.
Complete `coverage-ledger.csv` and the five files under `catalogs/`. A final `PASS` requires every included Feature-ID and applicable ENV-ID to be marked `COMPLETE`, with every in-scope code state candidate linked to a formal state row.

The code-map agent also completes a copy of `assets/asset-mapping.template.json` from the frozen Android source and archives the real bytes before building the inventory:

```bash
python3 scripts/archive_assets.py \
  --workspace <migration-run>/phase-02-android-inventory \
  --mapping <asset-mapping.json> \
  --archived-by <code-map-agent-id>
```

This creates `asset-inventory.csv` and the committed `asset-package/`. Use `{"schema_version":1,"assets":[]}` when the audit finds no migratable assets; claim rows then use only the `NONE_FOUND` sentinel. Never create an asset row or file for that sentinel.

## Capture formal evidence

Read [references/android-cli-procedure.md](references/android-cli-procedure.md) and [references/evidence-contract.md](references/evidence-contract.md). The runtime-state agent positions the app at the target state; the evidence administrator performs the formal capture:

```bash
python3 scripts/capture_state.py \
  --workspace <migration-run>/phase-02-android-inventory \
  --inventory-id <Inventory-ID> \
  --feature-id <Feature-ID> \
  --page-id <Page-ID> \
  --state-id <State-ID> \
  --env-id <ENV-ID> \
  --steps <steps.md> \
  --issued-by <evidence-administrator-id> \
  --captured-by <runtime-state-agent-id> \
  --launch
```

Use `--previous-evidence` and `--include-diff` for a state transition. A failed capture creates no valid evidence ID.
Use `--supersedes-evidence` for recapture. The old package remains immutable and its index lifecycle becomes `SUPERSEDED`.
After capture, return every new Evidence-ID to the migration controller for an independent controller-owned digest anchor. Unanchored or later changed evidence cannot pass final review.

## Build and review

Build the normalized inventory from JSON or JSONL claim files:

```bash
python3 scripts/build_inventory.py \
  --workspace <migration-run>/phase-02-android-inventory \
  --claims <claims-file-or-directory>
```

Read [references/review-and-rework.md](references/review-and-rework.md). The coverage checker visually inspects screenshots, cross-checks source and runtime findings, opens rework where needed, then alone runs the final closure check:

```bash
python3 scripts/manage_recheck.py --workspace <workspace> --action open \
  --reviewer <coverage-checker-id> --rework-id <Rework-ID> ...
```

```bash
python3 scripts/validate_evidence.py \
  --workspace <migration-run>/phase-02-android-inventory \
  --reviewer <coverage-checker-id> \
  --decision PASS \
  --attest-visual-review \
  --attest-source-runtime-crosscheck
```

Return the Phase 2 package to `$android-harmony-migration-controller`. Do not begin HarmonyOS architecture or implementation from this skill.
On `PASS`, inventory and asset rows become `REVIEWED`, active evidence becomes `ACCEPTED`, and `CLOSED` makes capture/build/recheck/archive operations read-only. Later changes invalidate the controller gate through the closure snapshot.
