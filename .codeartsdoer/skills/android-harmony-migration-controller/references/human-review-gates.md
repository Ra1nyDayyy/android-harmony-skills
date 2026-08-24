# Human review gates

Every phase has two separate decisions:

1. A deterministic script writes the canonical machine Gate report.
2. The system builds an exception-first `review-summary.json` and enters
   `WAITING_HUMAN_REVIEW`.
3. A person chooses `APPROVED`, `REWORK`, `APPROVED_DEVIATION`, or
   `MANUAL_TAKEOVER`.
4. The controller records that choice against the SHA-256 of the current Gate
   report. A later Gate rewrite makes the old decision stale.

Machine `FAIL` or `BLOCKED` cannot be approved. `REWORK` and
`MANUAL_TAKEOVER` never authorize the next work order. An approved deviation
does not delete or rename a machine difference; it names the accepted exception
and remains visible in delivery evidence.

Phase 2 remains automatic while it discovers and verifies the Android app. The
human checkpoint happens after its machine Gate, not while pages are being
enumerated.

## What the reviewer sees

The default view contains coverage counts, critical and warning exceptions,
the highest-risk findings, a small set of key page samples, and links to sealed
evidence. Raw CSV, JSON, command logs, screenshots, and Inspector trees remain
available on demand; they are not the default review surface.

Phase-specific views emphasize:

- Phase 1: source/APK/environment/scope readiness.
- Phase 2: page map, uncovered states, low-confidence bindings, dynamic risks,
  side effects, and exceptional scenarios.
- Phase 3: build/install/launch, Page-ID route or surface landing, carrier type,
  and scaffold-boundary violations.
- Phase 4: Android/Harmony/difference cards for every Page-ID, plus component,
  state, transition, side-effect, and capability discrepancies.

## Trust boundary

Migration workers and model reviewers have no approval authority. They may
produce evidence, diagnosis, suggested rework, and review summaries only. The
`record_human_review.py` command is an integration endpoint for the external Web
review service or a human-controlled terminal; do not expose that endpoint as a
worker action. In a deployed system, the Web service must authenticate the
reviewer and retain its own audit log. The repository seal detects stale or
modified records but is not, by itself, proof of a person's identity.
