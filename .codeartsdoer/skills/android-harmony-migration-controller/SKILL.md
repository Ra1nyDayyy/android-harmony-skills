---
name: android-harmony-migration-controller
description: Coordinate an Android-to-HarmonyOS migration by freezing scope and baselines, issuing phase work orders, arbitrating conflicts, routing rework, and enforcing phase gates. Use when starting or governing a migration; do not use this skill to inspect Android runtime UI or implement HarmonyOS features.
---

# Android to HarmonyOS Migration Controller

Create one auditable migration run and keep it governed. This skill is the project controller; it does not perform specialist analysis or write application code.

## Boundaries

- Freeze scope, source/APK baselines, target platform, environments, ownership, decisions, and gates.
- Never infer missing facts. Record them as `PENDING_CONFIRMATION` with an owner.
- Never collect or alter runtime evidence and never implement Android or HarmonyOS features.
- Preserve old decisions, environments, and evidence. Supersede them with new IDs rather than overwriting them.
- Phase 3 may create and verify architecture, routes/surfaces, public UI foundations, and interface-only
  capability contracts. It may not implement business behavior.
- Phase 4 may implement business parity only under a controller-issued work order after Gate 3 passes.
  The controller still does not edit application code or create specialist evidence.

## Start a migration run

Read [references/controller-contract.md](references/controller-contract.md), then initialize a run:

```bash
python3 scripts/init_migration.py \
  --output <audit-output-root> \
  --project-root <android-project-root> \
  --project-name <project-name>
```

Complete `controller/scope.json`. It must contain an exact clean Git commit, a structurally valid APK and its SHA-256, included and excluded scope, HarmonyOS target, accounts, seed data, network profiles, emulator model and resolution, one baseline environment, and distinct IDs for every frozen controller and Phase 2 role.

Run the Phase 1 gate before dispatching Android inventory work:

```bash
python3 scripts/validate_gate.py --run-dir <migration-run> --phase 1 --write
```

## Dispatch Phase 2

Read [references/phase-2-handoff.md](references/phase-2-handoff.md). Issue the immutable work order after the Phase 1 gate passes:

```bash
python3 scripts/issue_phase2_work_order.py \
  --run-dir <migration-run> \
  --issued-by <frozen-controller-id>
```

Invoke `$android-migration-inventory` with the run directory, frozen `controller/scope.json`, and issued work order.

The Phase 2 runtime policy is fixed:

- Use Android CLI for `describe`, `run`, `layout`, `layout --diff`, and screenshots.
- Do not use Layout Inspector.
- One inventory row equals one feature, one page, one state, one environment, and one evidence ID.

After each successful capture, the migration controller independently anchors the sealed package outside the Phase 2 workspace:

```bash
python3 scripts/anchor_phase2_evidence.py \
  --run-dir <migration-run> \
  --evidence-id <Evidence-ID> \
  --anchored-by <frozen-controller-id>
```

Final review fails if an indexed Evidence-ID has no controller anchor, or if its manifest or metadata no longer matches that anchor.

## Arbitrate and route rework

Record every conflict or scope change in `controller/decision-log.csv`. The Android inventory lead arbitrates inventory facts against the frozen baseline `ENV-ID`; this controller records the ruling and decides whether the phase gate opens.

Return failed Phase 2 work to the Android inventory lead through `controller/rework-log.csv`. Do not bypass the lead by assigning individual inventory workers directly.

## Close Phase 2

Read [references/phase-gates.md](references/phase-gates.md), then run:

```bash
python3 scripts/validate_gate.py --run-dir <migration-run> --phase 2 --write
```

Open the next migration phase only when the gate report says `PASS`. The gate requires the complete included feature set; it does not silently downgrade to a partial release.

The Phase 2 gate independently recomputes the closure snapshot. A stale or hand-written `closure-report.json` cannot open the gate.

## Dispatch and close Phase 3

Read [references/phase-3-handoff.md](references/phase-3-handoff.md). After the current Gate 2 report is `PASS`, freeze six new and mutually distinct Phase 3 actors in a controller-issued work order:

```bash
python3 scripts/issue_phase3_work_order.py \
  --run-dir <migration-run> \
  --issued-by <frozen-controller-id> \
  --architecture-lead-id <actor-id> \
  --toolchain-agent-id <actor-id> \
  --navigation-agent-id <actor-id> \
  --public-ui-agent-id <actor-id> \
  --capability-contract-agent-id <actor-id> \
  --architecture-acceptance-agent-id <actor-id>
```

Invoke `$harmonyos-migration-scaffold` with the migration run, issued work order, and frozen architecture lead. The six Phase 3 actor IDs must also differ from every frozen Phase 1/2 actor.

After the architecture acceptance agent passes and closes Stage 3, independently open Gate 3:

```bash
python3 scripts/validate_gate.py --run-dir <migration-run> --phase 3 --write
```

Gate 3 rechecks the registered work order and role separation, all Phase 2 input hashes, sealed HENV/HVER evidence, the current scaffold/screenshot/artifact hashes, and the complete Phase 3 closure manifest. Any open Phase 3 controller rework keeps the gate closed.

A Gate 3 `PASS` closes the scaffold phase only. It does not itself authorize implementation.

## Dispatch and close Phase 4

Read [references/phase-4-handoff.md](references/phase-4-handoff.md). From a current Gate 3 `PASS`, freeze four new actors that are distinct from one another and every Phase 1–3 actor:

```bash
python3 scripts/issue_phase4_work_order.py \
  --run-dir <migration-run> \
  --issued-by <frozen-controller-id> \
  --implementation-lead-id <actor-id> \
  --visual-asset-agent-id <actor-id> \
  --verification-executor-id <actor-id> \
  --parity-acceptance-agent-id <actor-id>
```

Invoke `$harmonyos-feature-implementation` with the migration run, issued work order, frozen implementation lead, and required H4ENV configurations. Do not let the implementation skill choose or replace the four controller assignments.

After the parity acceptance agent closes Stage 4, independently open Gate 4:

```bash
python3 scripts/validate_gate.py --run-dir <migration-run> --phase 4 --write
```

Gate 4 first revalidates Phases 1–3. It then recomputes the Phase 4 input archive, one final HBUILD per required H4ENV, one sealed HEVD and one accepted HREV per parity row, both rework ledgers, all evidence and artifact hashes, and the complete Phase 4 closure manifest. Any mismatch or open ticket keeps the gate closed.

## Current executable boundary

This controller currently implements work orders and gates only through Phase 4. Do not claim, simulate, or manually write a Gate 5/6 PASS. When planning whole-app regression and delivery acceptance, read the bundle-level `PHASES-4-6-PLAN.md`; implement the specified controller work orders, rework mirrors, and independent gates before those phases are run.
