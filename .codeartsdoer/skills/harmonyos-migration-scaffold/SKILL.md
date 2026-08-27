---
name: harmonyos-migration-scaffold
description: Build and verify a non-business HarmonyOS NEXT scaffold from an approved Android inventory using the bundled ArkUI Stage template, real routes or surfaces, public UI foundations, interface-only capability contracts, command-line build evidence, and a mandatory human checkpoint. Use after approved Gate 2; do not implement business behavior.
---

# HarmonyOS Migration Scaffold

Create a runnable landing place for every frozen Android page without prematurely implementing the app.

## Non-negotiable contract

- Models never approve, accept deviations, or declare Phase 3 complete.
- Start only from machine-passing and human-approved Gates 1-2 with hash-locked Phase 2 inputs.
- Use the bundled `assets/arkui-stage-template`; do not substitute a sample project.
- Preserve each Android surface carrier: page, Dialog, Sheet, overlay, widget, or external surface. Do not invent a route for a non-routable surface.
- Create modules, routes/surface shells, public tokens/containers, and interface-only capability contracts. No ViewModels, business state, requests, persistence, fake data, real adapters, or business buttons.
- Build/install/launch/smoke evidence must come from frozen command-line tools and the emulator, not previews or claims.

## Inputs and initialization

The only accepted input is **gmi Phase 2 closure** (`phase-2-closure.json` + `candidates/` 13 表 + `runtime-evidence/`（evidence-index / runtime-gate / audit-replay） + `coverage/coverage-ledger.csv`，由 gmi_phase3_adapter 合成为 `phase-02-android-inventory/` 布局 — see [input-mapping-contract.md](references/input-mapping-contract.md)). Run `scripts/init_scaffold.py`; it verifies the gmi gate (audit 0 discrepancy, UNMAPPED=0, no silent MISSING, runtime-gate consistent — any missing gate artifact is `BLOCKED`), copies the accepted inputs, creates the template project, seeds architecture, page/surface, asset-landing, capability, and migration-status registries, and rejects input drift. mapping_type is decided from the Phase 2 carrier kinds; a non-routable carrier (dialog/sheet/overlay/widget) never becomes a route.

Freeze the real toolchain and emulator with `scripts/freeze_environment.py`. Credentials and signing secrets remain outside the workspace.

## Build responsibilities

- Architecture: module placement, carrier mapping, dependency policy, asset landing, and ownership boundaries.
- Toolchain: dependency lock, clean build, install, and launch.
- Navigation/surfaces: one real landing shell per frozen Page-ID or non-page surface.
- Public UI: generic tokens, responsive containers, and common state shells only.
- Capability contracts: types and interfaces only.
- Machine verification: deterministic source, artifact, route/surface, screenshot, and boundary checks.

These are logical responsibilities, not proof that six unrelated models ran. Every duty is owned by a named agent ID; one worker may reuse a single platform task across roles, but a creator can never perform the external human approval, and the acceptance agent must differ from every creator it reviews.

## Verification and rework

Populate registries with real project-relative files. Preserve Phase 2 assets and advanced obligations; Phase 3 may plan their landing but cannot erase or implement them.

Run `scripts/run_verification.py` with the frozen verification plan. It must produce immutable build, install, launch, route/surface smoke, and PNG evidence for the declared emulator. Then run `scripts/validate_stage3.py`; external labels or hand-written smoke results have no authority.

Route build/toolchain, route/carrier, public-boundary, interface-contract, asset-landing, and dependency failures through `scripts/manage_stage3_rework.py`. Open tickets block machine closure.

Gate 3 closes Phase 3: `validate_stage3.py` recomputes the sealed snapshot, presents build/startup status, Page-ID landing coverage, carrier differences, and exceptions, then stops at `WAITING_HUMAN_REVIEW`. Only the external human review may approve Gate 3.

## Reference map

- [input-mapping-contract.md](references/input-mapping-contract.md): immutable gmi Phase 2 handoff (13 tables + closure).
- [scaffold-boundaries.md](references/scaffold-boundaries.md): permitted and forbidden code.
- [environment-toolchain.md](references/environment-toolchain.md): frozen commands and emulator.
- [verification-and-rework.md](references/verification-and-rework.md): evidence, tickets, and closure.
- [roles-and-authority.md](references/roles-and-authority.md): responsibility and receipt rules.
- [governed-execution-contract.md](references/governed-execution-contract.md): claim, routing, and rollback boundaries.
