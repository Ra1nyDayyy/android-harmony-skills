# Observable consistency contract

Phase 4 is a constrained translation, not a redesign. The frozen per-state parity contract set (`migration-unit-contracts.json`) is generated from the frozen Phase 2 inventory/static analysis and the accepted Phase 3 mapping. One contract exists for every Android inventory state on every required Harmony environment.

## Non-waivable dimensions

The implementation agent must preserve all of these dimensions:

- carrier: page, dialog, sheet, popup, or embedded surface;
- target identity and user-visible step count;
- every page-level frozen component, event, and transition somewhere in the page contract;
- every component, event, and transition observed in the current Android state in that state's contract;
- entry condition, action, and expected observable result;
- business rules, data dependencies, system capabilities, third-party dependencies, and advanced runtime/side-effect obligations;
- source-first asset identity and functional meaning.

The agent may not delete, merge, replace, hide, or compress any item merely because a simpler Harmony implementation is available. A full Android page cannot become a dialog; the same rule also rejects any other carrier substitution, missing control, lost branch, changed navigation, altered data result, or omitted side effect.

## Native optimization boundary

Harmony-native APIs and architecture are encouraged behind the observable boundary. They may improve internal state management, lifecycle handling, performance, accessibility, or platform integration only when the frozen external behavior remains unchanged.

Only a platform-imposed visual offset may use `APPROVED_DIFFERENCE`. It requires a `PLATFORM_VISUAL` decision approved by both the parity reviewer and controller. Functional and asset results must remain `MATCH`; approval cannot waive a carrier, component, function, transition, data, or side-effect mismatch.

## Machine enforcement

`capture_state.py` computes assertion verdicts from `actual`, `expected`, and a frozen operator. An external command's `status: PASS` is never authoritative. The state plan must contain the exact Android expected observable bound to its Inventory-ID and must cover all frozen semantic obligation IDs.

The runtime UI tree must report the exact carrier and target and every component required for the current state. Required events and transitions must appear in raw operation traces containing the executed action and before/after snapshots; a self-declared ID array is rejected. Local validation and controller Gate 4 independently recompute both the complete page set and the state-specific set from Phase 2/3 artifacts, so editing the contract and its hash files together does not bypass the gate.

Each page or shared capability gets attempt 0 plus at most two automatic repairs; every execution is controller-anchored before commands run. When the budget is exhausted, stop changing code and emit a grouped error report for the later human-assisted repair stage.
