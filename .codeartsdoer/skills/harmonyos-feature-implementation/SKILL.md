---
name: harmonyos-feature-implementation
description: Implement and verify HarmonyOS NEXT pages from frozen Android page contracts and an approved scaffold, using a per-page UI understanding and conversion agent, page-owned work orders, shared capability orders, emulator screenshots, UiTest interaction and component evidence, functional and side-effect assertions, bounded repair, and human review. Use only after approved Gate 3.
---

# HarmonyOS Page Implementation

Translate the frozen Android semantics into ArkUI. Phase 2 page contracts are the only UI and functional design source; Phase 4 may implement them but may not reinterpret or simplify them.

## Non-negotiable contract

- Models never approve, accept differences, write `MATCH`, or declare Phase 4 complete.
- Start only from machine-passing and human-approved Gates 1-3 reached through the gmi artifact chain; inputs remain immutable.
- Carrier, components, geometry, visible text, states, actions, outputs, transitions, assets, data effects, and system effects are non-waivable.
- A page cannot become a Dialog, Sheet, merged page, or simplified substitute unless Phase 2 recorded that carrier or a person approves a named deviation after machine comparison.
- Reuse frozen Android assets. Do not redraw, screenshot-crop, silently replace, or regenerate them.
- No fake data, no-op adapters, placeholder branches, invented APIs, or build-only completion.

## Inputs and initialization

Phase 4 starts only after machine-passing, human-approved Gates 1-3. Its inputs are the **Phase 3 sealed artifacts** (accepted scaffold project, registries, environment set) plus the **gmi Phase 2 handoff**: `runtime-gate.csv` (VISITED=ACCEPTED), `audit-replay.csv` with 0 discrepancies, `phase-2-closure.json`, and the frozen per-page contracts. Frozen Phase 2 page acceptance contracts, the asset package/landing map, and required `H4ENV-ID` configurations complete the input set. Run `scripts/init_implementation.py`; it copies the scaffold, locks every input hash, creates one canonical contract per Page-ID, and seeds parity/evidence registries.

Missing or contradictory Android facts return to Phase 2. Page contracts must retain `gmi_fields`, `gmi_navigation`, `gmi_motion`, and `behavior_bindings`; components come from aligned static facts, accepted runtime UI trees, or deterministic page-field conversion. A non-deferred page with no components is blocking. Wrong module, route, carrier, shell, contract, or asset landing returns to Phase 3. Do not guess around upstream defects.

## Page-owned implementation

Issue one immutable `PAGE_WORK_ORDER` for every Page-ID. Each page has one exclusive owner, one real CodeArts task ID, one page-scoped UI understanding/conversion agent, exclusive code paths, all states, transitions, visual rules, side effects, capability dependencies, and its contract hash. The UI agent may be the page owner but cannot serve another page. Before writing ArkTS, it must consume only the frozen Phase 2 page contract and pass the generated `arkts-page-plan.json` conservation check; missing, simplified, carrier-changed, or unmapped facts fail closed.

Issue separate `SHARED_CAPABILITY_WORK_ORDER` records for reusable calculations, persistence, network, clipboard, files, background work, permissions, and other native capabilities. Page and capability code paths may not overlap. Shared specialists serve pages; they do not redesign them.

`page-contract.behavior_bindings` is mandatory implementation input, not advisory: every interactive component with a binding must implement the real action plus its recorded side effect; empty handlers count as functional defects. When a behavior genuinely cannot be implemented, keep the UI, mark the handler `// DEFERRED-BEHAVIOR: <reason>` with the Android `file:line`, and record a `DEFERRED_BEHAVIOR` ledger row — never silently drop it.

## Parity lifecycle and repair budget

The implementation unit is one `Page-ID`; the acceptance unit is one frozen Android inventory row on one required `H4ENV-ID`.

```text
page-implementation-ledger.csv
NOT_STARTED → (work order issued) → ACCEPTED

parity-map.csv
NOT_STARTED → IMPLEMENTED → EVIDENCED → ACCEPTED
                                       ↘ REWORK
REWORK → EVIDENCED with a new HEVD → ACCEPTED after a new review
```

The default parity relationship is one-to-one: Android Inventory-ID + Android Evidence-ID + H4ENV-ID → Harmony source references, visual elements/assets, HEVD-ID. State merge, split, removal, carrier replacement, missing UI, and extra or compressed user-visible steps are forbidden implementation-agent actions.

The parity chain is closed by three ledgers that Gate 4 re-computes together: `parity-map.csv` (per-state parity rows), `evidence-index.csv` (sealed HEVD packages: supersession chains, hashes, executor), and `acceptance-ledger.csv` (exactly one ACCEPTED HREV per parity row). `rework-tickets.csv` mirrors `controller/rework-log.csv` with the unified 22-column ticket contract.

Each page or shared capability gets attempt 0 plus at most two automatic repairs. Every execution is anchored before commands run, and failed packages stay append-only. When the budget is exhausted, stop changing code and emit a grouped error report. On the next failure, return upstream when the contract is wrong or enter `MANUAL_TAKEOVER` when implementation remains unresolved.

## Build and evidence

Use `scripts/convert_asset.py` only for a frozen conversion contract. Seal the exact final source/HAP per environment with `scripts/run_build.py`.

For every required Page-ID, State-ID, transition, assertion, side effect, and environment, execute the generated UiTest probes imported from `@kit.TestKit` against the installed final HAP. Formal evidence includes command logs, test-HAP and final-HAP hashes, source/build hashes, stable locator queries, component type/text/bounds/visible/enabled/clickable values, action traces, deterministic functional and side-effect assertions, PNG, result path, device identity, metadata, hashes, and `COMMITTED`. Preview images, model summaries, and hand-written component records do not count.

运行 Phase 2 执行层自检：cd scripts && python3 test_minimal_phase2.py（25 用例，覆盖双 lane/审计重放/熔断防护，发布前必跑）

## Machine comparison and repair

The machine comparison binds Android and Harmony evidence hashes and computes carrier, component/type/text, state, transition, assertion/output, side-effect, geometry, and screenshot differences. Model-authored `MATCH` or an acceptance agent's confidence has no authority and cannot override a measured difference.

Run the deterministic Stage 4 validator only after every page and shared capability closes against one final HAP. Open differences, missing states, stale evidence, invented native symbols without build/runtime effect proof, and unmatched assets block the machine Gate.

For CodeArts runs, `INPUT_LOCKED`, a successful build, generated contracts, or a model-written PASS file never counts as implementation completion. Every page ledger row must be `ACCEPTED`, every parity row must have one sealed emulator/UiTest evidence package and one accepted review, and the controller independently rejects Route-ID/Page-ID/Back-only ArkTS shells and reused screenshots. After Gate 4 passes, publish the closed source project with `scripts/publish_harmony_project.py --workspace <run>/phase-04-harmony-implementation --target <requested arkts directory>`. The target must be explicit and empty; temporary ASCII build carriers are never the final delivery directory.

Return the sealed workspace to the controller. It recomputes Gate 4 and shows Android, Harmony, and difference cards plus red/yellow exceptions and sampled green results. Status becomes `WAITING_HUMAN_REVIEW`; human approval never edits the machine result.

## Reference map

- [input-contract.md](references/input-contract.md): gmi Phase 2 handoff, Phase 3 sealed inputs, and initialization.
- [observable-consistency-contract.md](references/observable-consistency-contract.md): required semantics and carrier fidelity.
- [asset-and-visual-parity.md](references/asset-and-visual-parity.md): source-first assets and visual thresholds.
- [ui-test-snapshot-evidence.md](references/ui-test-snapshot-evidence.md) and [emulator-evidence.md](references/emulator-evidence.md): formal execution evidence.
- [review-and-rework.md](references/review-and-rework.md): difference routing and bounded repair.
- [roles-and-authority.md](references/roles-and-authority.md): page-model roles and separation.
- [governed-execution-contract.md](references/governed-execution-contract.md): input/output claims and rollback boundaries.
