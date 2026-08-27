# Understanding validation contract

This document defines how the skill itself proves it can still *understand* an app. It separates everyday Phase 2 thresholds from skill-version qualification tests.

## Everyday Phase 2 thresholds (per run)

The unified machine gate (`gmi_closure.py`) enforces:

| Item | Threshold |
| --- | --- |
| Android static discovery completeness | 100% |
| UI-state runtime verification (`RUNTIME_UI`) | ≥ 90% |
| Externally observable functional verification (`RUNTIME_EFFECT`) | ≥ 90% |
| REQUIRED key functionality | 100% |
| Evidence hash / page identity / device identity errors | 0 |
| Unverified low-risk (`REVIEW`) items | ≤ 10%, itemized for human review |

Rules:

- Unfinished `REQUIRED` tasks block; unfinished `REVIEW` tasks count toward the unverified share and block only past 10%.
- Static discovery gaps, identity errors, corrupted evidence, and contradictory results always block.
- `SOURCE_ONLY` items never inflate runtime coverage; UI and functional coverage are computed and reported separately.
- The machine stage ends at `READY_FOR_HUMAN_REVIEW`; `PASS`/`CLOSED` exist only after human review acceptance with an unchanged closure hash.

## Mutation qualification test (on skill change only)

Mutation tests are **not** part of every application run — they qualify a modified skill version once, before reuse. Minimum coverage:

1. **UI mutation** — change one UI attribute (text/color/visibility). Phase 2 must detect the change (hash/fidelity mismatch) or block because it cannot prove equivalence.
2. **Event/navigation mutation** — change one event handler or navigation target. The runtime task set or replay must expose the behavior difference; never pass unchanged source as verified behavior.
3. **Persistence/system side-effect mutation** — change a persistence or system side effect. The before/after probe comparator must detect the snapshot delta (or block as unprovable).

The skill must fail (or explicitly block) in all three cases when the source has changed. A mutation run that still passes unchanged is a disqualifying result for the skill version.

## Dual-lane allocation & merge test

Qualification also includes one dual-lane test:

- Queue A ∩ Queue B = ∅; Queue A ∪ Queue B = the frozen runnable task set.
- A complete save/restart journey stays inside one lane.
- Two lanes with divergent device configuration are blocked by calibration.
- A lane interrupted mid-queue resumes from its checkpoint without re-executing completed Task-IDs.
- Duplicate Task-IDs or a wrong serial in either lane are caught by the audit.

## Independent blind review

A full independent blind re-analysis of the app is not required for every run. It is triggered when the skill version changes its understanding pipeline, when a migration dispute cannot be settled from recorded evidence, or when the controller explicitly orders a re-baseline. Everyday runs rely on the deterministic gates above plus human review of the exception-first summary.