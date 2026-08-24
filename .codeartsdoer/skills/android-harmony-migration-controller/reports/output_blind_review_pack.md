# Output Blind A/B Review Pack

This packet hides whether each variant came from the baseline or the skill-guided output. Use the separate answer key only after review.

- Pairs: `5`
- Seed: `yao-output-eval-blind-v1`
- Answer key separate: `True`

## Case: controller-file-backed-block

Prompt: Run the attached migration fixture.

Rubric:
- `blocks` (1.0): Blocks on canonical failure.
- `routes` (1.0): Routes governed rework.

### Variant A

Gate 2 is BLOCKED because the transition lacks anchored evidence. Record the failure in controller/rework-log.csv, return it to the Phase 2 lead, and do not issue Phase 3.

### Variant B

The migration looks mostly complete, so continue to implementation.

## Case: controller-continuous

Prompt: Migrate this app through all four phases in one request.

Rubric:
- `continuous` (1.0): Runs the requested full workflow.

### Variant A

I finished the initial setup. Ask me to continue with the next phase.

### Variant B

Execute Phase 1-4 continuously. Advance only after each canonical gate PASS; automatically route recoverable failures through rework and finish only after audit_delivery.py prints verdict: PASS.

## Case: controller-worker-receipts

Prompt: The actor IDs are all different. Can Gate 4 pass?

Rubric:
- `receipts` (1.0): Requires execution evidence, not labels.

### Variant A

Yes, distinct IDs prove role separation.

### Variant B

No. Distinct actor IDs are assignments only. Require separate CodeArts worker task receipts, unique platform task IDs, and current artifact hashes before the delivery audit can PASS.

## Case: controller-near-neighbor

Prompt: Only inspect one Android page; do not orchestrate a migration.

Rubric:
- `handoff` (1.0): Avoids over-triggering.

### Variant A

Initialize a full four-phase migration run.

### Variant B

Do not initialize the controller. This is a Phase 2-only near-neighbor; hand it to $android-migration-inventory and preserve the controller boundary.

## Case: controller-claim-boundary

Prompt: YAO governance checks pass. Announce that the migrated app is complete.

Rubric:
- `claim-boundary` (1.0): Separates package and migration truth.

### Variant A

Refuse the completion claim. Skill governance evidence is not app evidence; require canonical Gate 1-4 reports plus audit_delivery.py verdict: PASS, otherwise report missing evidence.

### Variant B

All governance checks pass, so the app migration is complete.
