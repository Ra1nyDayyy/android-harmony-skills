# Output Blind A/B Review Pack

This packet hides whether each variant came from the baseline or the skill-guided output. Use the separate answer key only after review.

- Pairs: `5`
- Seed: `yao-output-eval-blind-v1`
- Answer key separate: `True`

## Case: inventory-file-backed

Prompt: Close Phase 2 for the fixture.

Rubric:
- `coverage` (1.0): Preserves uncovered obligations.

### Variant A

The main page was captured, so Phase 2 passes.

### Variant B

Phase 2 is BLOCKED. Keep reflection in advanced-analysis.json, clipboard writes as side-effect probes, and the hidden error state in runtime-tasks.json; default-state evidence cannot grant PAGE_PASS for missing atomic subjects.

## Case: inventory-static-runtime

Prompt: How should one page be analyzed?

Rubric:
- `two-lens` (1.0): Requires static and runtime correlation.

### Variant A

Commit static Page, Component, Event, Transition, and State candidates with file:line provenance; then bind each atomic subject to Android CLI runtime evidence on every required ENV-ID before deterministic page-gate evaluation.

### Variant B

Read its XML and summarize the visible controls.

## Case: inventory-model-pass

Prompt: The analysis model says a small dialog is complete. Accept it?

Rubric:
- `anti-self-pass` (1.0): Prevents model-authored pass.

### Variant A

No. The model may propose bindings but may not grant PASS. Every dialog component, event, transition, and state needs hash-bound evidence; evaluate_page_gates.py alone computes PAGE_PASS.

### Variant B

Yes, the model is confident and the dialog is minor.

## Case: inventory-near-neighbor

Prompt: Implement the discovered Android screens in ArkUI now.

Rubric:
- `boundary` (1.0): Avoids implementation scope.

### Variant A

Create ArkUI pages directly from the inventory.

### Variant B

Stop at the Phase 2 boundary. Do not implement HarmonyOS code; return the closed inventory to $android-harmony-migration-controller for gated Phase 3 dispatch.

## Case: inventory-unreachable

Prompt: A special account state cannot be reached automatically. Mark the page complete.

Rubric:
- `missing-evidence` (1.0): Fails closed on unreachable state.

### Variant A

Mark it complete and note the limitation.

### Variant B

Do not mark it complete. Preserve the scenario as a blocking runtime task with owner, environment, confidence, and missing evidence; no prose waiver may reduce the committed coverage denominator.
