---
name: android-harmony-migration-controller
description: Coordinate an auditable Android-to-HarmonyOS migration through Phases 1-4 with frozen inputs, specialist work orders, deterministic machine gates, mandatory human review, and governed rework. Use for one-shot workflow requests or phase governance; do not inspect Android UI or implement HarmonyOS application code in the controller role.
---

# Android to HarmonyOS Migration Controller

Create and govern one migration run. Specialist Skills do the analysis and implementation; this controller freezes inputs, issues work, verifies evidence, pauses for people, and routes rework.

## Non-negotiable contract

- Models never approve, accept deviations, or declare a phase complete.
- A script-authored machine `PASS` is necessary but never sufficient to open the next phase.
- After every Gate, generate the compact review summary and enter `WAITING_HUMAN_REVIEW`.
- Continue only after a current, sealed `APPROVED` or `APPROVED_DEVIATION` decision bound to that Gate report's SHA-256.
- Preserve old work orders, decisions, evidence, and failures. Supersede them with new IDs.
- The controller never edits app source, captures specialist evidence, or invents missing facts.

Read [human-review-gates.md](references/human-review-gates.md) before running a phase transition. The approval command is an external Web/human integration endpoint, not a migration-worker action.

## Inputs

- Android project root and clean Git revision.
- Structurally valid APK and SHA-256.
- Included/excluded scope, target HarmonyOS profile, accounts, seed data, networks, permissions, and frozen Android/Harmony environments.
- Real CodeArts task receipts for assigned production work.

Initialize with `scripts/init_migration.py`, complete `controller/scope.json`, then compute Gate 1 with `scripts/validate_gate.py --phase 1 --write`.

**Phase 1 environment preflight (mandatory, before the phase state machine):** freeze screen + SDK together with `scripts/preflight_env.py --serial <ANDROID_SERIAL> --harmony-serial <HARMONY_SERIAL> --harmony-config <HARMONY_CONFIG_INI> --width <WIDTH> --height <HEIGHT> --density <DPI> --scope controller/scope.json`. Fill each placeholder from your live environment, never copy a stale value: `<ANDROID_SERIAL>` (e.g. emulator-5554; take the value printed by `adb devices`), `<HARMONY_SERIAL>` (e.g. 127.0.0.1:5557; take the value printed by `hdc list targets`), `<HARMONY_CONFIG_INI>` (the real config.ini path under the Harmony emulator deployment directory; leave empty and note it if absent), `<WIDTH>x<HEIGHT>@<DPI>` (one resolution shared by both sides, e.g. 1080x2400@440). Requirements:
- Screen parity: the Android emulator must be online with `wm size/density` fixed to WxH/dpi (runtime evidence in P2 follows it); offline/mismatch -> fix the environment first, P1 does not pass. The Harmony emulator (hdc serial) must be online with the same parameters (baseline for P4 H4ENV screenshot comparison); offline -> explicitly record "Harmony emulator unavailable" in scope (P4 parity becomes DEFERRED, never fabricate).
- SDK/toolchain probe: scan and freeze Android (ANDROID_HOME/adb/emulator/java) + Harmony (hdc/DevEcoStudio/node/ohpm/hvigor) paths and versions, write them fully into the `sdk_toolchain` block of scope.json; print `[WARN] missing xxx` for absent items (P3/P4 build gates hard-block on them; re-run after installing).
- Frozen values are written to scope.json (`screen_resolution/screen_density/serial` + `sdk_toolchain`); P2 gmi runtime (`--screen-size/--screen-density`, falls back to scope frozen values), P3/P4 builds and H4ENV reuse them directly, never changing the baseline.

**GMI-mode rules:** Phase 2 is the gmi sole path (`$android-migration-inventory`); a run is a GMI run when `phase-02-android-inventory/gmi/phase-2-closure.json` exists or the phase manifest says `generator=gmi`, and the controller itself recomputes Gate 2/3/4 from the gmi artifact chain — the full machine conditions are stated once in [phase-gates.md](references/phase-gates.md), never trust a specialist PASS report alone.

- Work-order issuance: GMI work flows only through `scripts/issue_phase3_work_order.py` / `scripts/issue_phase4_work_order.py` (they recompute the GMI Gate and generate uppercase IDs); `scripts/issue_phase2_work_order.py` is kept as an authorization record only — the Phase 2 data input is always the gmi artifact chain. Never hand-edit gate reports or hand-assemble work orders.
- Adapter no-clobber: `android-migration-inventory/scripts/gmi_phase3_adapter.py --out` pointing at an existing run keeps the controller identity and real static-analysis, aligning old Page-IDs to GMI Page-IDs; when components are missing, synthesize them only deterministically from accepted UI trees or page-fields.
- Path constraint: hvigor rejects non-ASCII workspaces (00306003); `preflight_env.py` must hard-block at P1, never move seals mid-run or rewrite path hashes.

## Phase state machine

For each phase:

1. Recompute the canonical machine Gate.
2. On failure, record `AUTO_GATE_FAIL` or `BLOCKED` and route the exact defect.
3. On machine `PASS`, build `review-summary.json`; status becomes `WAITING_HUMAN_REVIEW`.
4. A person chooses `APPROVED`, `REWORK`, `APPROVED_DEVIATION`, or `MANUAL_TAKEOVER`.
5. Only the two approval decisions may authorize the next work order. A rewritten Gate invalidates the old decision.

The run stays continuable inside one task; the human checkpoint for Phase 2 comes after its automatic gmi closure, never manual page enumeration.

## Specialist routing

- Phase 1 freezes scope and baselines.
- Phase 2: issue the authorization record with `scripts/issue_phase2_work_order.py` (authorization record only), then invoke `$android-migration-inventory` in gmi mode. Anchor every sealed Android evidence package with `scripts/anchor_phase2_evidence.py`.
- Emulator resource checkpoint (controller duty): after the Phase 2 runtime segment ends (both lane queues drained, gmi_audit started, or a fuse trip is confirmed parked), verify with `adb devices` that no still-online Android emulator is being kept idle through audit/closure/human review/Phase 3/Phase 4 — if any is, tell the user to shut it down. Conversely, before Phase 4 no HarmonyOS emulator may be started. The controller re-checks this at every gate transition, not only once.
- Phase 3: after approved Gate 2, issue with `scripts/issue_phase3_work_order.py`, then invoke `$harmonyos-migration-scaffold` using its bundled ArkUI Stage template.
- Phase 4: after approved Gate 3, issue with `scripts/issue_phase4_work_order.py`, then invoke `$harmonyos-feature-implementation`. Work is page-owned (`PAGE_WORK_ORDER`); shared capabilities use separate `SHARED_CAPABILITY_WORK_ORDER`s.

Actor IDs are assignments, not proof. Use `scripts/record_team_execution.py` to bind each real CodeArts task ID, work-order hash, actor, terminal state, and produced artifact hashes. Reused, fabricated, missing, or stale receipts are blocking. Logical specialist roles may share a model service, but a producer cannot perform the external human approval.

## Failure routing

- Route defects by origin: missing or contradictory Android facts return to Phase 2, wrong Harmony module/route/carrier/contract landing returns to Phase 3, ArkUI implementation or evidence repairs stay in Phase 4; each unit allows one initial attempt plus two automatic repairs, then `MANUAL_TAKEOVER` (details in [phase-gates.md](references/phase-gates.md)).
- External blockers (missing account, verification code, private service permission, signing material, SDK, DevEco/Hvigor/HDC, usable emulator, or unique baseline): keep existing progress and list everything missing at once; never bypass by shrinking scope, faking a CLI, replacing a page carrier, or hand-writing evidence.
- Never translate a failure into `PASS_WITH_GAPS`, `PARTIAL`, or prose completion.

## Outputs and delivery

Each phase produces a canonical Gate report, exception-first review summary, sealed human decision, immutable work order, rework records, and evidence hashes. Run `scripts/audit_delivery.py --through-phase 4` only after approved Gate 4. Delivery is valid only when the audit exits zero; the reported machine verdict and human approval remain separate facts.

## Reference map

- [controller-contract.md](references/controller-contract.md): authority, run layout, and scope invariants.
- [phase-gates.md](references/phase-gates.md): the single authoritative Gate 1-4 machine conditions and the P1->P4 gmi data-flow closure.
- [phase-2-handoff.md](references/phase-2-handoff.md), [phase-3-handoff.md](references/phase-3-handoff.md), [phase-4-handoff.md](references/phase-4-handoff.md): specialist inputs and outputs per phase.
- [human-review-gates.md](references/human-review-gates.md): review UI, decisions, trust boundary, and pause behavior.
- [governed-execution-contract.md](references/governed-execution-contract.md): package and report integrity.
