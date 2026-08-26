# Single-task, phase-gated execution contract

The user issues one complete migration task. The system keeps context, evidence, work orders and rework records in the same task, but never crosses a human review point automatically into the next phase.

## Execution order

1. Phase 1 freezes source, APK, scope, accounts, data and the run environment, then computes the machine Gate.
2. After a machine Gate PASS, generate a compact review package; status becomes `WAITING_HUMAN_REVIEW`.
3. A person chooses approve, return, approve an explicit deviation, or take over manually.
4. Only the `APPROVED` or `APPROVED_DEVIATION` decision bound to the current Gate may authorize the next phase work order.
5. Phases 2, 3 and 4 repeat the same flow. The original task stays continuable; the user does not need to restart.

Inside Phase 2, static analysis, runtime traversal, evidence binding and coverage computation remain automatic; no human page enumeration is inserted. A person reviews the page map, omission risks and low-confidence items only after the Phase 2 machine result is formed.

## Automatic rework

On a machine Gate failure, route the failure back by origin: missing/contradictory Android facts go back to Phase 2, wrong Harmony base or carrier to Phase 3, ArkUI implementation or evidence errors stay in Phase 4. Each migration unit allows one initial verification and at most two automatic repairs; further failure enters `MANUAL_TAKEOVER`.

A model may analyze, implement, verify and propose fixes, but may not approve a phase, accept a deviation, or rewrite a failure into `PASS`. The human approval entry must be held by a Web login state or a human-controlled terminal.

## External blockers

When an account, verification code, private service permission, signing material, SDK, DevEco/Hvigor/HDC, usable emulator or unique baseline is missing, keep the existing progress and list everything missing at once. Never bypass a blocker by shrinking scope, faking a CLI, replacing a page carrier or hand-writing evidence.

