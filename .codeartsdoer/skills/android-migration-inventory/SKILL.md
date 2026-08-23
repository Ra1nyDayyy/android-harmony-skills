---
name: android-migration-inventory
description: Automatically build a migration-grade semantic inventory of an existing Android app using frozen-source page/UI/event/navigation discovery, Android CLI runtime traversal, source-runtime binding, state-level records, and an immutable evidence chain. Use before Android-to-HarmonyOS migration when every page, component, state, function, and transition must be discovered with minimal later manual repair; do not use it to implement HarmonyOS code.
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
- Phase 2 performs no human annotation or manual page enumeration. Unresolved facts become automatic runtime tasks with explicit confidence; they are never guessed or silently omitted.
- No agent may grant `PASS`. Agents only bind static subjects to evidence; deterministic scripts compute every `PAGE_PASS` and the final verdict.

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

Environment credentials, seed data, and permissions must already be provisioned by the controller. Phase 2 automation records their readiness before capture; it must not pause for human page analysis:

```bash
python3 scripts/attest_environment.py \
  --workspace <migration-run>/phase-02-android-inventory \
  --env-id <ENV-ID> \
  --inventory-lead-id <inventory-lead-id> \
  --account-ready --seed-ready --network-ready --permissions-ready
```

## Discover the source graph first

Read [references/static-page-analysis.md](references/static-page-analysis.md), then run the deterministic scanner against the frozen source:

```bash
python3 scripts/analyze_static_pages.py \
  --workspace <migration-run>/phase-02-android-inventory \
  --analyzed-by <code-map-agent-id>

python3 scripts/validate_static_analysis.py \
  --workspace <migration-run>/phase-02-android-inventory
```

The scanner creates stable page and component candidates, expands XML layouts and resource values, detects Activity/Fragment/Compose surfaces, extracts event/state/navigation candidates, and emits a machine-consumable runtime backlog. It also inventories reflection, dynamic code, WebView/server-driven UI, non-UI side effects, permissions, abnormal data, and extreme-state scenarios in `advanced-analysis.json`. Do not begin runtime traversal unless validation passes.

Static analysis locates what should exist; it does not claim final pixels or behavior. Runtime automation must confirm every page's default state, resolve open relationships, and promote only source/runtime-correlated candidates from `DISCOVERED` to the formal `VERIFIED` catalogs.

## Run independent automated lenses

The inventory lead dispatches four independent automated lenses:

- Code-map agent: consumes the committed static package and resolves modules, entries, routes, pages, component trees, state candidates, resources, and file/line references.
- Runtime-state agent: consumes `runtime-tasks.json`, autonomously navigates user journeys, and captures observable states on the frozen environment.
- Business-rule agent: entry conditions, validations, roles, feature flags, and transitions.
- Data-dependency agent: APIs, local data, permissions, SDKs, system capabilities, and native libraries.

Read [references/inventory-contract.md](references/inventory-contract.md) before creating claim records.
Complete `coverage-ledger.csv` and the five files under `catalogs/` from machine findings. A final `PASS` requires every included Feature-ID and applicable ENV-ID to be marked `COMPLETE`, every in-scope code state candidate to be linked to a formal state row, and every static runtime task to be resolved or retained as an explicit blocking risk.

The code-map agent also generates `asset-mapping.template.json` content from the frozen Android source and archives the real bytes before building the inventory:

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

## Build and review automatically

Build the normalized inventory from JSON or JSONL claim files:

```bash
python3 scripts/build_inventory.py \
  --workspace <migration-run>/phase-02-android-inventory \
  --claims <claims-file-or-directory>
```

Read [references/review-and-rework.md](references/review-and-rework.md) and [references/deterministic-page-gates.md](references/deterministic-page-gates.md). The runtime-state agent records evidence bindings without a status or decision field. Record one observation for every required page, component, event, transition, state candidate, and applicable environment:

```bash
python3 scripts/record_runtime_observation.py \
  --workspace <workspace> \
  --subject-type <PAGE|COMPONENT|EVENT|TRANSITION|STATE> \
  --subject-id <stable-subject-id> \
  --page-id <Page-ID> \
  --env-id <ENV-ID> \
  --after-evidence <Evidence-ID> \
  [--before-evidence <Evidence-ID>] \
  [--locator-field <text|type|content_description|test_tag> \
   --locator-value <source-derived-value> --locator-occurrence <n>]
```

Run the deterministic page gate before final closure. Missing evidence and ambiguous component bindings are blocking, including on dialogs, menus, widgets, and other small surfaces:

```bash
python3 scripts/evaluate_page_gates.py --workspace <workspace>
```

Read [references/advanced-runtime-analysis.md](references/advanced-runtime-analysis.md). For every dynamic risk, side effect, and generated scenario, bind runtime UI evidence without a verdict. Side effects and scenarios additionally require a sealed before/after probe produced by the frozen adapter:

```bash
python3 scripts/seal_side_effect_probe.py \
  --workspace <workspace> \
  --probe-evidence-id <Probe-Evidence-ID> \
  --candidate-id <Side-Effect-ID-or-Scenario-ID> \
  --page-id <Page-ID> --env-id <ENV-ID> \
  --before <before.json> --after <after.json> \
  --adapter-record <adapter-record.json> \
  --comparator <CHANGED|UNCHANGED|EQUALS_EXPECTED> \
  --produced-by <data-dependency-agent-id> \
  --sealed-by <evidence-administrator-id>

python3 scripts/record_advanced_observation.py \
  --workspace <workspace> \
  --subject-type <DYNAMIC_RISK|SIDE_EFFECT|SCENARIO> \
  --subject-id <Subject-ID> --page-id <Page-ID> --env-id <ENV-ID> \
  --evidence-id <Evidence-ID> [--probe-evidence-id <Probe-Evidence-ID>]

python3 scripts/evaluate_advanced_gates.py --workspace <workspace>
```

The advanced gate recomputes JSON differences and comparator results, verifies the frozen adapter hash and evidence bindings, and blocks closure if any generated candidate/environment pair is absent. Do not add `status`, `confidence`, or model verdict fields to advanced observations.

The coverage-checker agent may inspect screenshots, cross-check findings, and open rework, but cannot turn its own assessment into `PASS`. The later project-wide human audit remains outside Phase 2.

```bash
python3 scripts/manage_recheck.py --workspace <workspace> --action open \
  --reviewer <coverage-checker-id> --rework-id <Rework-ID> ...
```

```bash
python3 scripts/validate_evidence.py \
  --workspace <migration-run>/phase-02-android-inventory \
  --reviewer <coverage-checker-id> \
  --decision AUTO
```

`--decision PASS` is accepted only as a legacy alias for `AUTO` and is ignored as an authority signal. `INCOMPLETE` and `BLOCKED` may stop closure; only the deterministic gates can produce `PASS`.

Return the Phase 2 package to `$android-harmony-migration-controller`. Do not begin HarmonyOS architecture or implementation from this skill.
On `PASS`, inventory and asset rows become `REVIEWED`, active evidence becomes `ACCEPTED`, and `CLOSED` makes capture/build/recheck/archive operations read-only. Later changes invalidate the controller gate through the closure snapshot.
