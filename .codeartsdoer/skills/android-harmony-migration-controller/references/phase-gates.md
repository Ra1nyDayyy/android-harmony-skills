# Phase gates

This file is the single authoritative source for every Gate 1-4 machine condition in this workflow. Handoff documents list artifacts only and never restate gate conditions. The controller recomputes each gate itself with `scripts/validate_gate.py --phase <1-4>`; a specialist-authored PASS is never sufficient.

## gmi data-flow closure (Phase 1 -> Phase 4)

- Phase 1 freezes scope, environments, screen parity, and the SDK/toolchain baseline (`controller/scope.json`).
- Phase 2 (gmi sole path, `$android-migration-inventory`) produces `phase-02-android-inventory/gmi/`: `candidates/` with the 13 candidate tables plus manifest, `coverage/coverage-ledger.csv`, `runtime-evidence/` (`runtime-gate.csv`, `audit-replay.csv` with zero discrepancy rows), `phase-2-closure.json`, and `phase-manifest.json` with `generator=gmi`.
- Phase 3 scaffold consumes the gmi Phase 2 closure (Gate 2 recheck), lands modules/routes/surfaces/public UI/asset registry, and seals with `stage-03-closure-manifest.sha256` + `CLOSED`.
- Phase 4 consumes the sealed Phase 3 outputs plus the gmi handoff; work is page-owned through `PAGE_WORK_ORDER` plus `SHARED_CAPABILITY_WORK_ORDER` for shared capabilities; each inventory Page-ID gets one page contract (components + behavior bindings), then seals with `stage-04-closure-manifest.sha256` + `CLOSED`.
- Every phase adds a machine gate plus a human review checkpoint (see human-review-gates.md). Gate conditions below are stated exactly once, here.

## Phase 1: control baseline

Pass only when:

- Android project root exists, is at the exact declared clean Git commit, and has no untracked changes.
- The APK is structurally valid and its declared SHA-256 matches; app version, build, application ID, and build variant are frozen.
- Included scope is non-empty and exclusions are explicit.
- HarmonyOS target is explicit.
- Every environment has the required account, seed data, network, emulator, screen, API, locale, theme, timezone, and permission fields.
- Exactly one `ENV-ID` is the baseline.
- Android CLI is mandatory and Layout Inspector is prohibited.
- Every frozen controller and Phase 2 actor ID is valid and distinct.
- No pending confirmation remains and an immutable Phase 2 work order is issued only after this gate passes.

## Phase 2: Android inventory (gmi sole path)

For a gmi run (detected by `phase-02-android-inventory/gmi/phase-2-closure.json` or `phase-manifest.json` `generator=gmi`) pass only when:

- Phase 1 still passes.
- All 13 candidate tables exist non-empty under `gmi/candidates/` and are covered by the candidates manifest.
- `coverage/coverage-ledger.csv` has no `GAP`/`UNMAPPED` row.
- `runtime-evidence/runtime-gate.csv` exists and `audit-replay.csv` shows zero discrepancies (discrepancy column all "no").
- `phase-2-closure.json` reports `unmapped=0` and `audit_discrepancy=0`.

After the gmi machine closure the controller recomputes Gate 2 itself; the Phase 2 work order is an authorization record only — the Phase 2 data input is always the gmi artifact chain. The human checkpoint follows the machine closure, never manual page enumeration. If the user changes scope, freeze a new scope decision and a new work order before rerunning.

## Phase 3: HarmonyOS project scaffold

Pass only when:

- Phase 1 and Phase 2 still satisfy their gates.
- A uniquely registered, immutable Phase 3 work order was issued by the frozen controller from Gate 2 `PASS`.
- Its six actor IDs are valid, mutually distinct, and different from all Phase 1/2 actors; actual creators, executor, lead, and reviewer match those assignments.
- `stage-03-input-lock.json` still matches the work order, frozen controller scope, Gate 2 snapshot, gmi Phase 2 closure chain (closure, candidates manifest, coverage ledger, runtime evidence), evidence-anchor records, and the dependency/capability catalogs.
- The HENV freezes all nine category-specific executable paths and hashes, required argument tokens, success markers, and error markers; the selected HVER contains the actually executed preflight.
- The Phase 3 gate report says `PASS` and was issued by the frozen architecture acceptance agent.
- The report identifies one frozen `HENV-ID`, one sealed passing `HVER-ID`, the reviewed source snapshot, and built artifact hashes.
- The HVER `manifest.sha256` exactly covers its package, `COMMITTED` identifies that passing HVER, and every recorded log and evidence hash still matches.
- Architecture-map row count equals the frozen inventory row count.
- `asset-registry.csv` exactly covers the Phase 2 asset inventory with frozen hashes, scope links, safe module-local targets, unique symbols, explicit migration decisions, and READY status.
- Every in-scope feature has a module landing, and every visual/nonvisual requirement has a real shell or contract landing.
- Clean build creates or changes a structurally valid HAP; installation, launch, and route/surface smoke checks pass on all required devices.
- Every route/surface smoke result was generated by its recorded command into a new output path and exactly binds the frozen serial, bundle, route/surface, page, and shell.
- Every route or visual-surface shell has sealed, structurally valid PNG evidence from every screenshot-required frozen emulator, and the acceptance agent has visually reviewed it.
- Current scaffold files, screenshots, and build artifacts still match the sealed snapshot and HVER hashes.
- No local or controller Phase 3 rework remains open, and no business implementation is present.
- `stage-03-closure-manifest.sha256` exactly covers the complete closed workspace, and `CLOSED` binds the final Stage 3 report.

Phase 3 does not authorize business implementation by itself; it only opens the next phase for a separately issued work order. For a gmi run, Gate 3 additionally requires complete inventory-to-architecture mappings, READY modules, a passing Stage 3 report, and a valid Stage 3 `CLOSED` binding. Gate 2 alone is never an equivalent Gate 3.

## Phase 4: HarmonyOS page parity implementation

Pass only when:

- Phases 1-3 still pass under read-only controller revalidation.
- A uniquely registered Phase 4 work order was issued by the frozen controller from Gate 3 `PASS`; its four actors are mutually distinct and do not reuse a Phase 1-3 actor. Page work uses `PAGE_WORK_ORDER`; shared capabilities use `SHARED_CAPABILITY_WORK_ORDER`.
- `stage-04-input-lock.json` and its copied snapshots exactly match the work order, scope, immutable Gate 3 snapshot, gmi Phase 2 closure chain, Phase 3 closure/scaffold/registries, upstream Phase 3 work order, and every frozen HENV.
- Each active Android inventory row has one parity row for every required mapped H4ENV.
- Each required H4ENV has exactly one final read-only passing HBUILD from the frozen verification executor, built from the exact current source snapshot.
- Each parity row cites a unique read-only sealed HEVD from that H4ENV's final HBUILD. PNG, UI tree, live assertions, command records, emulator identity, artifact, source, and input-lock hashes all agree.
- Each parity row has exactly one active `ACCEPTED` HREV from the frozen parity acceptance agent. The review recomputes both the copied Android evidence hashes and HarmonyOS HEVD hashes.
- Asset migration exactly covers the Phase 2/3 asset chain, and capability implementation exactly covers Phase 3 contracts with final HEVD references.
- Local and controller Phase 4 rework ledgers contain the same closed tickets and fields; any non-closed ticket blocks the gate.
- The final report contains both `verdict: PASS` and `final_verdict: PASS`, identifies the frozen reviewer, work order, input lock, builds, source snapshot, artifact hashes, and exact counts.
- `stage-04-closure-manifest.sha256` exactly covers the closed workspace except the report, manifest, marker, locks/staging, caches, and generated project output; `CLOSED` contains the final report SHA-256.

For a gmi run, Gate 4 additionally requires one page contract per inventory Page-ID, non-empty components for every non-deferred page, retained behavior bindings wherever behavior candidates exist, no remaining `PENDING_RUNTIME_VERIFY`, a passing Stage 4 report, and a valid Stage 4 `CLOSED` binding. For CodeArts output, the controller also recomputes exact `ACCEPTED` page implementation coverage, canonical ArkTS paths, placeholder-shell rejection, parity coverage, sealed emulator/UiTest packages, one accepted review per parity row, screenshot uniqueness, and the complete closure manifest. A model-authored PASS or a passing Stage 3 report cannot satisfy Gate 4.
