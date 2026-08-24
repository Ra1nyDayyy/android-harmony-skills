# Governed execution contract

- `input_files: file-backed fixture`: Android project, frozen APK, scope, environments, specialist work orders, canonical stage reports, and worker receipts.
- `owner`: Android-Harmony Migration Maintainers.
- `review cadence`: every release and after any gate, work-order, role, or completion-claim change.
- `output contract`: one immutable migration run whose canonical Gate 1-4 reports and `audit_delivery.py` verdict are `PASS`, or one explicit `BLOCKED` result naming the failed check, evidence path, owner, and repair entry.
- `rollback boundary`: never rewrite source baselines, sealed evidence, old work orders, or closed reports. Revert only package-source changes through version control; supersede run artifacts with new IDs.

## Claim boundary

Skill governance reports prove package structure and regression coverage only. They never prove that an app migrated, built, ran, or preserved behavior. Only canonical migration scripts may grant a phase verdict. Provider telemetry, real CodeArts task authentication, independent blind review, and real-device runs remain `missing evidence` until their own artifacts exist.

## Routing boundary

Own full-workflow orchestration, phase transition, rework routing, and delivery audit. Hand Phase 2-only discovery to `$android-migration-inventory`, Phase 3-only scaffold work to `$harmonyos-migration-scaffold`, and authorized Phase 4 implementation to `$harmonyos-feature-implementation`. Never perform their specialist work under the controller identity.
