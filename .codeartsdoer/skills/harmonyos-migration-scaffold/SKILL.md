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

Consume the controller-issued Phase 3 work order and immutable Phase 2 closure. Run `scripts/init_scaffold.py`; it copies the accepted inputs, creates the template project, seeds architecture, page/surface, asset-landing, capability, and migration-status registries, and rejects input drift.

Freeze the real toolchain and emulator with `scripts/freeze_environment.py`. Credentials and signing secrets remain outside the workspace.

## Build responsibilities

- Architecture: module placement, carrier mapping, dependency policy, asset landing, and ownership boundaries.
- Toolchain: dependency lock, clean build, install, and launch.
- Navigation/surfaces: one real landing shell per frozen Page-ID or non-page surface.
- Public UI: generic tokens, responsive containers, and common state shells only.
- Capability contracts: types and interfaces only.
- Machine verification: deterministic source, artifact, route/surface, screenshot, and boundary checks.

These are logical responsibilities, not proof that six unrelated models ran. Bind actual CodeArts tasks and owned artifacts to controller receipts; a creator cannot perform the external human approval.

## Verification and rework

Populate registries with real project-relative files. Preserve Phase 2 assets and advanced obligations; Phase 3 may plan their landing but cannot erase or implement them.

Run `scripts/run_verification.py` with the frozen verification plan. It must produce immutable build, install, launch, route/surface smoke, and PNG evidence for the declared emulator. Then run `scripts/validate_stage3.py`; external labels or hand-written smoke results have no authority.

Route build/toolchain, route/carrier, public-boundary, interface-contract, asset-landing, and dependency failures through `scripts/manage_stage3_rework.py`. Open tickets block machine closure.

Return the sealed workspace to the controller. The controller recomputes Gate 3, presents build/startup status, Page-ID landing coverage, carrier differences, and exceptions, then stops at `WAITING_HUMAN_REVIEW`.

## Reference map

- [input-mapping-contract.md](references/input-mapping-contract.md): immutable Phase 2 handoff.
- [scaffold-boundaries.md](references/scaffold-boundaries.md): permitted and forbidden code.
- [environment-toolchain.md](references/environment-toolchain.md): frozen commands and emulator.
- [verification-and-rework.md](references/verification-and-rework.md): evidence, tickets, and closure.
- [roles-and-authority.md](references/roles-and-authority.md): responsibility and receipt rules.
