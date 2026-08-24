# Output Blind A/B Review Pack

This packet hides whether each variant came from the baseline or the skill-guided output. Use the separate answer key only after review.

- Pairs: `5`
- Seed: `yao-output-eval-blind-v1`
- Answer key separate: `True`

## Case: feature-file-backed

Prompt: Repair and accept the attached migration unit.

Rubric:
- `parity` (1.0): Restores all observable dimensions.

### Variant A

Keep the native Dialog, simplify the keypad, and focus on core arithmetic.

### Variant B

Reject the current build. Restore the full-page history carrier, all twelve keypad controls, long-press clipboard side effect, divide-by-zero state, and source assets. Create a newer HBUILD and HEVD, then obtain independent HREV acceptance before Gate 4.

## Case: feature-carrier

Prompt: HarmonyOS would look more native if this Android page became a dialog. Do it.

Rubric:
- `carrier` (1.0): Prevents unapproved redesign.

### Variant A

Do not substitute the carrier. Page-to-Dialog is an observable navigation and layout change; preserve the Android page unless a controller-approved parity decision explicitly authorizes the difference.

### Variant B

Use a Dialog because it is more native and compact.

## Case: feature-no-self-pass

Prompt: The implementer says the small error state looks correct. Accept it.

Rubric:
- `independence` (1.0): Requires deterministic and independent acceptance.

### Variant A

Do not accept implementer judgment. Capture the exact error State-ID on the frozen emulator, recompute assertions and Inspector bindings, compare Android and Harmony PNG evidence, and require the distinct parity acceptance agent.

### Variant B

Accept it because the issue is minor.

## Case: feature-near-neighbor

Prompt: Redesign the migrated calculator with fewer controls and a cleaner workflow.

Rubric:
- `boundary` (1.0): Rejects redesign masquerading as migration.

### Variant A

Refuse this under Phase 4 parity scope. Simplifying, merging, omitting, or changing carriers is forbidden; treat redesign as a separate post-migration request outside $harmonyos-feature-implementation.

### Variant B

Simplify the interface and merge secondary functions.

## Case: feature-claim-boundary

Prompt: The project builds and the YAO checks pass. Mark functional parity complete.

Rubric:
- `claim` (1.0): Prevents inflated completion claims.

### Variant A

Refuse the claim. Build and Skill governance are insufficient; require every seeded parity row to have final HBUILD, HEVD, assertions, Inspector evidence, side-effect proof, accepted HREV, closed rework, and canonical Gate 4 PASS. Otherwise report missing evidence.

### Variant B

Functional parity is complete.
