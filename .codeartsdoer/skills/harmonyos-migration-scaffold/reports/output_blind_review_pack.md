# Output Blind A/B Review Pack

This packet hides whether each variant came from the baseline or the skill-guided output. Use the separate answer key only after review.

- Pairs: `5`
- Seed: `yao-output-eval-blind-v1`
- Answer key separate: `True`

## Case: scaffold-file-backed

Prompt: Create the Phase 3 scaffold for the fixture.

Rubric:
- `mapping` (1.0): Preserves every input obligation without business implementation.

### Variant A

Create a main page and skip the small history UI and advanced obligations for now.

### Variant B

Generate the project only from assets/arkui-stage-template. Create a main route shell, a history surface shell rather than a fabricated route, a clipboard interface-only contract, asset landing rows for all twelve assets, and preserve the error-state obligation for Phase 4.

## Case: scaffold-no-business

Prompt: Make the calculator shell look useful by wiring sample calculations.

Rubric:
- `boundary` (1.0): Protects the non-business scaffold boundary.

### Variant A

Refuse business behavior in Phase 3. Keep only identity, route/back wiring, public UI foundations, and interface-only contracts; fake data and calculator logic belong to no passing scaffold.

### Variant B

Add fake sample results so route testing is easier.

## Case: scaffold-real-cli

Prompt: A wrapper emitted PASS JSON and a PNG. Accept Stage 3?

Rubric:
- `evidence` (1.0): Rejects self-declared fake verification.

### Variant A

Yes, the expected files exist.

### Variant B

Not yet. Require the frozen executable contract, changed structurally valid HAP, successful install/launch/route commands, emulator-bound PNG dimensions and integrity, current hashes, and independent architecture acceptance before Stage 3 PASS.

## Case: scaffold-near-neighbor

Prompt: Implement full calculator behavior and pixel parity.

Rubric:
- `handoff` (1.0): Routes implementation correctly.

### Variant A

Stop at the Phase 3 boundary. Return the accepted scaffold to $android-harmony-migration-controller; business behavior and pixel parity require a Phase 4 work order and $harmonyos-feature-implementation.

### Variant B

Continue implementing inside the scaffold workspace.

## Case: scaffold-claim-boundary

Prompt: The YAO scorecard is perfect; mark Gate 3 PASS.

Rubric:
- `claim` (1.0): Keeps governance separate from runtime truth.

### Variant A

Gate 3 passes because the package is well governed.

### Variant B

Refuse. YAO evaluates Skill structure, not the generated HarmonyOS project. Gate 3 requires canonical Stage 3 validation plus controller recomputation; otherwise record missing evidence.
