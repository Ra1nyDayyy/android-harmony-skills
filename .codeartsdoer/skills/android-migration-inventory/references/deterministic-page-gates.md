# Deterministic page gates

## Authority boundary

Agents may discover subjects, navigate the app, capture evidence, and record evidence bindings. They must not write a pass/fail field into `runtime-observations.json`. Unknown fields, including `status`, `decision`, `pass`, and `confidence`, invalidate the observation package.

`validate_evidence.py` computes the final verdict. A requested `PASS` is a legacy alias for `AUTO`; it cannot override a failed or missing atomic check. A reviewer may force `INCOMPLETE` or `BLOCKED`, but may not force `PASS`.

## Atomic coverage

The committed static package defines the denominator. For every applicable `ENV-ID`, require exactly one evidence-bound observation for each:

- `Page-ID`, including dialogs, sheets, menus, widgets, and other observable surfaces;
- `Component-ID`;
- `Event-ID`;
- `Transition-ID`;
- conditional `State-ID` candidate.

Never calculate coverage only from the observations that an agent submitted. A missing small page is therefore a missing required key, not an absent row that can be ignored.

## Machine checks

- Page: require active inventory evidence for the same page and environment and a non-empty runtime layout.
- Component with Android resource ID: locate that exact ID in the evidence `layout.json`.
- Component without resource ID: require an exact source-derived text, type, content description, or test tag plus an occurrence number. Do not allow two static components to reuse one runtime node.
- Event: require before/after evidence in the same environment, a sealed predecessor link, and an observable layout or screenshot change.
- Transition: apply the event checks and require the after evidence to identify the statically expected target `Page-ID`.
- State candidate: require active evidence for the same page and environment. Keep the state blocked until its condition has been automatically exercised.

Every referenced Evidence-ID must also belong to an active inventory row. Evidence integrity, controller anchors, immutable package checks, and rework closure remain mandatory in addition to these page gates.

## Verdict rule

```text
PAGE_PASS = every required atomic observation for Page-ID is present and machine-valid

PASS = every Page-ID is PAGE_PASS
       and no static subject is unresolved
       and no observation is extra or duplicated
       and the remaining Phase 2 evidence/catalog/rework gates pass
```

Any missing, ambiguous, mismatched, stale, or unverifiable item produces `BLOCKED` in `page-gate-report.json`. The final closure validator reports `INCOMPLETE` unless a reviewer explicitly selects the stricter `BLOCKED` decision.
