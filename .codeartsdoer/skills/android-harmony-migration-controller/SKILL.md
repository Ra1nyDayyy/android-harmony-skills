---
name: android-harmony-migration-controller
description: Coordinate or continuously execute an Android-to-HarmonyOS migration through Phases 1–4 by freezing scope and baselines, invoking specialist Skills, issuing work orders, routing rework, and enforcing gates. Use for one-shot full migration requests as well as migration governance; do not directly inspect Android runtime UI or implement HarmonyOS features inside the controller role.
---

# Android to HarmonyOS Migration Controller

Create one auditable migration run and keep it governed. This skill is the project controller; it does not perform specialist analysis or write application code.

Before execution, read [references/governed-execution-contract.md](references/governed-execution-contract.md). Treat `manifest.json` and `reports/skill-ir.json` as the package contract. Governance reports assess this Skill, not an app migration; only canonical run gates may declare a phase complete.

## Default: run Phase 1–4 continuously

Read [references/continuous-run.md](references/continuous-run.md) when the user asks for a complete migration or the whole workflow. Treat continuous execution as the default: invoke the three specialist Skills and proceed from one passing gate to the next in the same task. Do not wait for the user to say “继续”, and do not ask for a HarmonyOS template path; Phase 3 uses its bundled `assets/arkui-stage-template`.

Pause only for a real external blocker listed in the continuous-run contract. A recoverable build failure, incomplete mapping, parity defect, or failed gate must enter the governed repair loop automatically.

## Actual team execution is mandatory

Frozen actor IDs are assignments, not evidence that workers ran. For every Phase 2, 3, and 4 role, dispatch a distinct CodeArts worker task before that role acts. Never let one worker impersonate several roles by changing an ID string, and never have the controller perform specialist work when delegation is unavailable.

After the assigned worker finishes, record its real platform task ID and hashes of the artifacts it produced or independently reviewed:

```bash
python3 scripts/record_team_execution.py \
  --run-dir <migration-run> \
  --work-order <run-relative-work-order.json> \
  --role-key <frozen-role-key> \
  --actor-id <frozen-actor-id> \
  --platform-task-id <real-CodeArts-task-id> \
  --artifact <run-relative-artifact-file>
```

Repeat this for every frozen role and every Phase 4 feature-role assignment. A fabricated, reused, missing, or hash-stale worker receipt is blocking. Phase 3 issuance verifies all Phase 2 receipts; Phase 4 issuance verifies all Phase 3 receipts; the final audit verifies Phase 2-4 controller roles and all feature roles.

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
- Treat the committed static Page/Component/Event/Transition/State set as the coverage denominator. Models may bind subjects to evidence or force a non-pass outcome, but only deterministic page and evidence gates may grant `PASS`.

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

In continuous mode, issue the Phase 3 work order immediately after this `PASS`; do not return control merely to announce the phase result.

The Phase 2 gate requires `page-gate-report.json` to contain only machine-computed `PAGE_PASS` rows with equal required/received atomic counts. It also requires `advanced-gate-report.json` to cover every discovered dynamic risk, non-UI side effect, and special scenario. Side effects and scenarios must carry reproducible, hash-bound probe evidence. The controller then independently recomputes the closure snapshot. A stale, hand-written, or model-authored `PASS` cannot open the gate.

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

Gate 3 rechecks the registered work order and role separation, all Phase 2 input hashes, the advanced-analysis/probe handoff, ArkUI template provenance, sealed HENV/HVER evidence, the current scaffold/screenshot/artifact hashes, and the complete Phase 3 closure manifest. Any missing dynamic/side-effect/scenario obligation or open Phase 3 controller rework keeps the gate closed.

A Gate 3 `PASS` closes the scaffold phase only. It does not itself authorize implementation.

In continuous mode, the original full-migration request plus the controller-issued Phase 4 work order supplies that authorization. Continue immediately; no second user prompt is required.

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

Do not create alternative reports such as `phase3-gate-report.json`, `phase2-final-report.json`, or a prose `PASS_WITH_GAPS`. Only the canonical script-authored reports have authority. After Gate 4 passes and all worker receipts are recorded, run:

```bash
python3 scripts/audit_delivery.py --run-dir <migration-run> --through-phase 4
```

The one-shot workflow is complete only when this command exits zero and prints `verdict: PASS`. On failure, continue the governed repair loop or report the real blocker; never reinterpret it as non-blocking.

This bundle provides a complete specialist workflow only through Phase 4. Controller records for later phases do not supply a system-regression or delivery specialist Skill. Do not describe a Gate 5/6 result as executable end-to-end capability until those specialist Skills and real-device tests exist.
