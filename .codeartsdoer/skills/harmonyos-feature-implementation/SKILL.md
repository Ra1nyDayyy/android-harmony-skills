---
name: harmonyos-feature-implementation
description: Implement and verify HarmonyOS NEXT pages from frozen Android page contracts and an approved scaffold, using a per-page UI understanding and conversion agent, page-owned work orders, shared capability orders, emulator screenshots, UiTest interaction and component evidence, functional and side-effect assertions, bounded repair, and human review. Use only after approved Gate 3.
---

# HarmonyOS Feature Implementation

Translate the frozen Android semantics into ArkUI. Phase 2 page contracts are the only UI and functional design source; Phase 4 may implement them but may not reinterpret or simplify them.

## Non-negotiable contract

- Models never approve, accept differences, write `MATCH`, or declare Phase 4 complete.
- Start only from machine-passing and human-approved Gates 1-3. Inputs remain immutable.
- Carrier, components, geometry, visible text, states, actions, outputs, transitions, assets, data effects, and system effects are non-waivable.
- A page cannot become a Dialog, Sheet, merged page, or simplified substitute unless Phase 2 recorded that carrier or a person approves a named deviation after machine comparison.
- Reuse frozen Android assets. Do not redraw, screenshot-crop, silently replace, or regenerate them.
- No fake data, no-op adapters, placeholder branches, invented APIs, or build-only completion.

## Inputs and initialization

Consume the controller Phase 4 order, frozen Phase 2 page acceptance contracts, accepted Phase 3 project, asset package/landing map, and required `H4ENV-ID` configurations. Run `scripts/init_implementation.py`; it copies the scaffold, locks every input hash, creates one canonical contract per Page-ID, and seeds parity/evidence registries.

Missing or contradictory Android facts return to Phase 2. Wrong module, route, carrier, shell, contract, or asset landing returns to Phase 3. Do not guess around upstream defects.

## Page-owned implementation

Issue one immutable `PAGE_WORK_ORDER` for every Page-ID. Each page has one exclusive owner, one real CodeArts task ID, exclusive code paths, all states, transitions, visual rules, side effects, capability dependencies, and its contract hash. One owner keeps the page through its bounded repairs, preventing context loss and inconsistent partial edits.

Issue separate `SHARED_CAPABILITY_WORK_ORDER` records for reusable calculations, persistence, network, clipboard, files, background work, permissions, and other native capabilities. Page and capability code paths may not overlap. Shared specialists serve pages; they do not redesign them.

## Build and evidence

Use `scripts/convert_asset.py` only for a frozen conversion contract. Seal the exact final source/HAP per environment with `scripts/run_build.py`.

For every required Page-ID, State-ID, transition, assertion, side effect, and environment, run `scripts/capture_state.py` against the installed final HAP. Formal evidence includes command logs, source/build hashes, action trace, deterministic assertions, raw and normalized ArkUI Inspector trees, event/transition snapshots, PNG, device identity, metadata, hashes, and `COMMITTED`. Preview images and hand-written trees do not count.

## Machine comparison and repair

The machine comparison binds Android and Harmony evidence hashes and computes carrier, component/type/text, state, transition, assertion/output, side-effect, geometry, and screenshot differences. Model-authored `MATCH` or an acceptance agent's confidence has no authority and cannot override a measured difference.

Agents may diagnose and repair. Each page or capability gets attempt 0 plus at most two automatic repairs. Keep failed packages append-only. On the next failure, return upstream when the contract is wrong or enter `MANUAL_TAKEOVER` when implementation remains unresolved.

Run the deterministic Stage 4 validator only after every page and shared capability closes against one final HAP. Open differences, missing states, stale evidence, invented native symbols without build/runtime effect proof, and unmatched assets block the machine Gate.

Return the sealed workspace to the controller. It recomputes Gate 4 and shows Android, Harmony, and difference cards plus red/yellow exceptions and sampled green results. Status becomes `WAITING_HUMAN_REVIEW`; human approval never edits the machine result.

## Reference map

- [input-contract.md](references/input-contract.md): frozen inputs and initialization.
- [observable-consistency-contract.md](references/observable-consistency-contract.md): required semantics and carrier fidelity.
- [asset-and-visual-parity.md](references/asset-and-visual-parity.md): source-first assets and visual thresholds.
- [arkui-inspector-evidence.md](references/arkui-inspector-evidence.md) and [emulator-evidence.md](references/emulator-evidence.md): formal execution evidence.
- [review-and-rework.md](references/review-and-rework.md): difference routing and bounded repair.
