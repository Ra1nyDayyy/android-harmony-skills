# Controller contract

## Authority

The controller owns migration scope, phase order, work orders, cross-phase decisions, rework routing, and gate results. It does not own specialist facts.

The Android inventory lead owns factual arbitration inside Phase 2. The coverage checker is the only role that can attest that the Phase 2 evidence chain is closed.

The Phase 3 architecture lead owns scaffold decisions. The architecture acceptance agent is the only final Phase 3 reviewer. The toolchain, navigation, public UI, and capability-contract agents own only their frozen work-order assignments.

The Phase 4 implementation lead owns implementation coordination. The visual-asset agent owns asset migration, the verification executor alone seals HBUILD/HEVD packages, and the parity acceptance agent alone accepts parity or opens and closes Phase 4 rework.

## Required control records

- `run-manifest.json`: immutable run identity and project path.
- `controller/scope.json`: frozen source, APK, target, scope, tool policy, and environment registry.
- `controller/task-ledger.csv`: phase and owner status.
- `controller/decision-log.csv`: append-only scope and arbitration decisions.
- `controller/rework-log.csv`: append-only rework routing and closure.
- `controller/gate-report.json`: latest machine gate result.
- `controller/work-orders/`: immutable, controller-issued phase assignments bound to the scope digest.
- `controller/evidence-anchor-registry.csv`: controller-owned digest anchor for every sealed Phase 2 Evidence-ID.
- `controller/team-execution-registry.csv` and `controller/team-execution-receipts/`: immutable bindings from every frozen role to a distinct real CodeArts task ID and hash-bound artifacts.
- `controller/work-orders/<Phase-3-Work-Order-ID>.phase-02-gate-report.json`: immutable Gate 2 snapshot used by Phase 3.
- `controller/work-orders/<Phase-4-Work-Order-ID>.phase-03-gate-report.json`: immutable Gate 3 snapshot used by Phase 4.

## Scope invariants

- There is exactly one baseline environment.
- Each environment includes account, seed data, network conditions, emulator model, resolution, density, Android/API version, locale, theme, font scale, timezone, and permissions profile.
- `runtime_ui_tool` is `android-cli` and `layout_inspector_allowed` is `false`.
- Changes receive a new decision ID. Existing evidence and decisions are never rewritten to hide the old baseline.
- The source baseline is an exact clean Git `HEAD`; the APK is a valid APK container whose declared SHA-256 matches its bytes.
- Controller, inventory lead, evidence administrator, and coverage checker IDs are frozen and distinct.
- A Phase 3 work order is issued only from a current, independently rechecked Gate 2 `PASS` by the frozen controller.
- It freezes architecture-lead, toolchain, navigation, public-UI, capability-contract, and architecture-acceptance IDs. All six differ from one another and every Phase 1/2 actor.
- It binds the canonical scope, immutable Gate 2 snapshot, Phase 2 closure report/manifest/marker, inventory, evidence-anchor snapshot, and controller anchor registry by SHA-256.
- Gate 3 trusts the work-order snapshot, not the mutable latest `controller/gate-report.json`, which becomes the Gate 3 report after `--write`.
- The reviewed Phase 2 asset inventory, exact asset-package manifest/marker, and every archived asset are part of the cross-phase evidence chain.
- A Phase 4 work order is issued only from a current Gate 3 `PASS` that also passes a read-only rerun of Gates 1–3.
- It freezes implementation-lead, visual-asset, verification-executor, and parity-acceptance IDs. All four differ from one another and every Phase 1–3 actor.
- It binds the canonical scope, controller-owned Gate 3 snapshot, Phase 2 closure/inventory/evidence/assets, Phase 3 closure/scaffold/registries, every frozen HENV, and the upstream Phase 3 work order by SHA-256.
- Gate 4 trusts the controller-owned Gate 3 snapshot, not the mutable latest gate report. It independently recomputes Phase 4 HBUILD, HEVD, HREV, rework-mirror, source-snapshot, and closure hashes.

## Status values

Use only `NOT_STARTED`, `IN_PROGRESS`, `PENDING_CONFIRMATION`, `REWORK`, `PASS`, or `FAIL` in controller records.

`PASS_WITH_GAPS`, `PARTIAL`, and any locally invented synonym are prohibited gate outcomes. A role name or actor ID without a valid team-execution receipt proves assignment only, not execution.
