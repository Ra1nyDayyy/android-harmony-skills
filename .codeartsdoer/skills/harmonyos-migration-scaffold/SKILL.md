---
name: harmonyos-migration-scaffold
description: Build and verify a non-business HarmonyOS NEXT project scaffold from a frozen Android migration inventory, with immutable inputs and environments, real module and route landing points, interface-only capability contracts, frozen-emulator PNG evidence, command-line build proof, and an independent Phase 3 gate. Use after the Android inventory phase passes; do not use it to implement business behavior or alter Phase 2 artifacts.
---

# HarmonyOS Migration Scaffold

Create a HarmonyOS NEXT project that builds, installs, launches, and contains a real architectural landing point for every frozen Phase 2 record. Stop before business implementation.

## Hard boundaries

- Phase 1 and Phase 2 must both be `PASS`; Phase 2 inputs are read-only and hash-locked.
- A page shell may contain only identity metadata, an originally existing page-level navigation bar, blank content, and the minimum route/back wiring needed for smoke tests.
- Do not add ViewModels, domain behavior, requests, persistence, fake data, state transitions, timers, business buttons, or real capability adapters.
- A visual surface that was not independently navigable on Android receives a surface shell, not a fabricated route.
- Capability files define types and interfaces only. Real adapters belong to a later phase.
- Formal build, install, launch, and smoke evidence comes from command-line tools. GUI-only claims do not pass.
- Every real route or visual-surface shell must be opened on a frozen HarmonyOS emulator and bound to immutable PNG screenshot evidence. Desktop crops and manually supplied screenshots do not pass.
- Never store passwords, tokens, private keys, passphrases, or signing secrets in the workspace.
- Environments and verification packages are immutable. Issue a new `HENV-ID` or `HVER-ID` instead of overwriting one. Manage rework only through the governed rework script; never delete or reuse a ticket ID.
- The architecture acceptance agent is the only final reviewer and must not be a creator or verification executor.

## Initialize Phase 3

Read [references/input-mapping-contract.md](references/input-mapping-contract.md) and initialize the read-only Phase 2 handoff:

```bash
python3 scripts/init_scaffold.py \
  --run-dir <migration-run> \
  --work-order <controller-issued-phase-3-work-order.json> \
  --architecture-lead <agent-id>
```

Initialization first reruns controller Gate 2 without writing controller state. It verifies the registered work order, all six frozen Phase 3 actors, the complete Phase 2 closure, reviewed inventory, accepted evidence, controller evidence anchors, the committed real-asset package, and the three dependency/capability catalogs. It copies and hashes the small input records, locks every archived asset file by canonical path and hash, seeds one architecture/migration-status row per active inventory record and one `asset-registry.csv` row per real asset, and extracts real capability requirements while ignoring explicit `NONE_FOUND` sentinels. Any input drift blocks the phase.

Create the empty command-line project and dependency lockfile under `harmony-project/`. Then read [references/environment-toolchain.md](references/environment-toolchain.md), complete `assets/harmony-environment.template.json`, and freeze it:

```bash
python3 scripts/freeze_environment.py \
  --workspace <migration-run>/phase-03-harmony-scaffold \
  --config <completed-harmony-environment.json> \
  --frozen-by <architecture-lead-id>
```

Use the same script with a new ID when any environment field changes. Never edit an existing environment directory.

## Divide and build

Read [references/roles-and-authority.md](references/roles-and-authority.md) and [references/scaffold-boundaries.md](references/scaffold-boundaries.md).

- Architecture lead: freezes module placement and dependency policy, registers the safe target module/path/symbol and migration decision for every real Phase 2 asset, arbitrates conflicts, and confirms the manager's deterministic rework owner.
- Toolchain agent: creates the project/modules and makes the frozen target build, install, and launch.
- Navigation agent: creates real route or visual-surface shells and smoke coverage.
- Public UI agent: creates only generic tokens, containers, common state shells, and responsive rules.
- Capability-contract agent: creates interface-only contracts for the seeded requirements.
- Architecture acceptance agent: independently verifies the exact snapshot and alone issues the gate verdict.

Populate the registries with paths to real files inside `harmony-project/`. Documentation-only mappings are invalid. Asset rows are landing plans, not permission to copy or recreate business assets during Phase 3. Keep `migration-status.csv` separate from Phase 2.

## Capture formal verification

Read [references/verification-and-rework.md](references/verification-and-rework.md). Complete `assets/verification-plan.template.json`, including command arrays for toolchain, device, bundle, signing, clean build, install, launch, route smoke, and emulator screenshot capture. Then run:

```bash
python3 scripts/run_verification.py \
  --workspace <migration-run>/phase-03-harmony-scaffold \
  --plan <verification-plan.json>
```

The runner uses argument arrays without a shell, records exit codes and sanitized logs, hashes the source snapshot and built artifact, and seals one `HVER-ID` package. Each screenshot receives an `HSCREEN-ID`, PNG, metadata, file hashes, emulator identity, target route/surface identity, and capture-command reference. A failed command does not create passing evidence.

The HENV freezes a separate executable contract for each of the nine command categories. The HVER—not the HENV directory—contains the executed toolchain/device/bundle/signing preflight report. Every route/surface smoke command must create a new direct JSON result at its declared output path; pre-existing or hand-written result input is rejected. Clean build must create or change a structurally valid HAP, and screenshot capture must create a complete PNG whose CRC, decompressed data, and frozen-emulator dimensions validate. PASS and FAIL HVER packages are both sealed read-only and are never edited.

## Route rework

Only the frozen architecture acceptance agent may open or close Phase 3 rework. The architecture lead confirms the fixed owner, and the manager mirrors the ticket into the controller ledger:

```bash
python3 scripts/manage_stage3_rework.py \
  --workspace <migration-run>/phase-03-harmony-scaffold \
  --action open \
  --reviewer <architecture-acceptance-agent-id> \
  --ticket-id <new-ticket-id> \
  --problem-type <fixed-problem-type> \
  --source-or-mapping-id <record-id> \
  --failed-verification-id <failed-HVER-ID> \
  --severity <CRITICAL|HIGH|MEDIUM|LOW> \
  --reason <reason> \
  --completion-condition <condition> \
  --confirmed-by <architecture-lead-id>
```

Close it only with a newer sealed PASS HVER produced by the frozen toolchain agent:

```bash
python3 scripts/manage_stage3_rework.py \
  --workspace <migration-run>/phase-03-harmony-scaffold \
  --action close \
  --reviewer <architecture-acceptance-agent-id> \
  --ticket-id <ticket-id> \
  --correction-verification-id <new-passing-HVER-ID>
```

Every open ticket blocks PASS, regardless of severity.

## Close Phase 3

The architecture acceptance agent reviews the real module files, one-to-one asset landing registry, route and surface smoke results, public-UI boundary, interface-only contracts, dependency graph, and all open rework tickets. It then runs:

```bash
python3 scripts/validate_stage3.py \
  --workspace <migration-run>/phase-03-harmony-scaffold \
  --henv-id <HENV-ID> \
  --verification-id <HVER-ID> \
  --reviewer <architecture-acceptance-agent-id> \
  --decision PASS \
  --attest-real-file-review \
  --attest-placeholder-boundaries \
  --attest-contract-only \
  --attest-dependency-review \
  --attest-runtime-smoke \
  --attest-screenshot-review
```

On PASS, the validator writes the Phase 3 closure manifest and `CLOSED`, then makes the complete workspace read-only. Return it to `$android-harmony-migration-controller` and run the independent controller gate:

```bash
python3 ../android-harmony-migration-controller/scripts/validate_gate.py \
  --run-dir <migration-run> \
  --phase 3 \
  --write
```

The controller recomputes the registered work order, role separation, locked Phase 2 snapshots, HENV/HVER evidence, current scaffold/artifact/screenshot hashes, mirrored rework state, and complete Phase 3 closure. Enter business implementation only under a later separately approved work order; Gate 3 PASS closes the scaffold phase but does not itself authorize feature work.
