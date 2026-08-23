---
name: harmonyos-feature-implementation
description: Implement and verify real HarmonyOS NEXT business features from frozen Android inventory and an accepted HarmonyOS scaffold, with source-first visual assets, state-level emulator evidence, native-capability adapters, and independent parity acceptance. Use only after migration Phases 1–3 pass; do not use it for scaffold creation, Android discovery, or release-store submission.
---

# HarmonyOS Feature Implementation

Implement real business behavior in a writable copy of the accepted Phase 3 project. Work by `Feature-ID`; accept by Android `Inventory-ID` and `State-ID`.

## Hard boundaries

- Controller Gates 1–3 and the Phase 3 acceptance report must be `PASS` before initialization.
- Phase 1–3 artifacts are immutable inputs. Copy the Phase 3 project; never develop inside it.
- Every active Android inventory row maps to one HarmonyOS parity row on every required `H4ENV-ID`.
- Phase 4 is a constrained translation, not a redesign. Carrier, components, functions, transitions, data effects, and system effects are non-waivable; an agent may not simplify, merge, omit, or substitute them.
- Formal screenshots come from the frozen HarmonyOS emulator after install, launch, navigation, and assertions. Preview or design-tool images do not count.
- Reuse existing Android SVG, PNG, WebP, JPG, icon, and illustration files. Do not redraw, regenerate, crop from screenshots, or silently substitute a glyph or system symbol.
- Format conversion and native-system substitution require traceable records; observable behavior changes require controller approval.
- No fake data, no-op adapters, placeholder business behavior, or unimplemented production branches may pass.
- The emulator verification executor and parity acceptance agent are distinct from implementers. Only the parity acceptance agent closes Phase 4.
- MP4 is neither required nor accepted as formal evidence.

## Initialize Phase 4

Read [references/input-contract.md](references/input-contract.md), [references/roles-and-authority.md](references/roles-and-authority.md), [references/asset-and-visual-parity.md](references/asset-and-visual-parity.md), and [references/observable-consistency-contract.md](references/observable-consistency-contract.md). Phase 2 must provide a frozen asset inventory/package and Phase 3 must provide its accepted project snapshot and asset landing registry. The controller issues the Phase 4 work order and freezes all four governance roles.

```bash
python3 scripts/init_implementation.py \
  --run-dir <migration-run> \
  --work-order <migration-run>/controller/work-orders/<phase4-work-order>.json \
  --environment-config <completed-phase4-environment.json> \
  --implementation-lead <frozen-implementation-lead-id>
```

Repeat `--environment-config` once for every required source-environment/emulator mapping. When Phase 3 contains `FORMAT_CONVERSION` assets, repeat `--asset-conversion-config <contracts.json>` for every contract file. Initialization reruns Gates 1–3 read-only, verifies both closure manifests and `CLOSED` markers, copies every referenced Android evidence package, every Phase 2 asset byte, the Phase 3 work order, all controller-listed inputs, and only the accepted Phase 3 project snapshot. It freezes exact `H4ENV-ID` command contracts, seeds one parity record per active Android state and mapped environment, and byte-copies every `DIRECT_COPY` asset from the local frozen asset copy.

## Implement one feature

Read [references/feature-workflow.md](references/feature-workflow.md). The implementation lead issues one immutable work order per `Feature-ID`:

```bash
python3 scripts/issue_feature_work_order.py \
  --workspace <migration-run>/phase-04-harmony-implementation \
  --feature-id <Feature-ID> \
  --issued-by <implementation-lead-id> \
  --feature-owner <agent-id> \
  --ui-agent <agent-id> \
  --business-data-agent <agent-id> \
  --native-capability-agent <agent-id> \
  --exclusive-code-path <existing-project-relative-path>
```

Issue exactly one active work order for every included `Feature-ID`; repeat `--exclusive-code-path` when one feature owns multiple existing directories. The feature owner integrates UI, business/data logic, native-capability adapters, and assets. Specialists may work in parallel only after exclusive code ownership is recorded. Every accepted implementation row must bind this work order, and every parity/visual/capability row must cite real `path:line` source references below its exclusive paths.

Implement each immutable `migration-unit-contracts.json` record without changing its observable dimensions. Populate the implementation, parity, visual-element, asset, capability, nativeization, and rework registries. A missing or conflicting Android fact blocks the current Phase 4 and returns through the controller to Phase 2; a wrong module, route, surface, contract, or resource landing similarly returns to Phase 3. Do not disguise an upstream contradiction as local implementation rework.

For a Phase 3 `FORMAT_CONVERSION` asset, execute only the frozen conversion contract:

```bash
python3 scripts/convert_asset.py \
  --workspace <migration-run>/phase-04-harmony-implementation \
  --conversion-id <new-conversion-id> \
  --asset-id <Asset-ID> \
  --contract-id <frozen-contract-id> \
  --executed-by <visual-asset-agent-id>
```

The result is immutable and binds the tool hash, exact command, source bytes, target bytes, logs, and registry update. A recreation requires both a parity-bound `ASSET_RECREATION` decision and controller approval.

## Seal a build

Create one immutable build package for the exact source snapshot:

```bash
python3 scripts/run_build.py \
  --workspace <migration-run>/phase-04-harmony-implementation \
  --plan <completed-build-plan.json>
```

Every required `H4ENV-ID` has exactly one final PASS `HBUILD-ID`. All final state evidence on that environment must reference that build and its exact source snapshot.

## Verify every state on the emulator

Read [references/emulator-evidence.md](references/emulator-evidence.md). For every seeded parity row, run the frozen emulator workflow:

```bash
python3 scripts/capture_state.py \
  --workspace <migration-run>/phase-04-harmony-implementation \
  --plan <completed-state-verification-plan.json>
```

A valid `HEVD-ID` contains command logs, steps, deterministic assertion results, a carrier/component-bound UI tree, raw event/transition operation traces with before/after snapshots, PNG screenshot, build artifact identity, source snapshot identity, metadata, hashes, and `COMMITTED`. An external `PASS` label is ignored unless the computed comparison passes. Before commands run, the execution is appended to both the local and controller hash-chain ledgers. Each migration unit gets one initial execution and at most two automatic repairs; deleting a local failure package cannot reset that budget.

## Close Phase 4

Read [references/review-and-rework.md](references/review-and-rework.md). The parity acceptance agent visually opens every PNG, compares it with the cited Android evidence, verifies functional assertions and asset provenance, then alone runs:

```bash
python3 scripts/review_parity.py \
  --workspace <migration-run>/phase-04-harmony-implementation \
  --parity-id <PAR-ID> \
  --comparison <completed-comparison.json> \
  --reviewer <parity-acceptance-agent-id> \
  --decision ACCEPTED \
  --attest-opened-both-screenshots \
  --attest-functional-results \
  --attest-asset-provenance
```

`review_parity.py --decision REWORK` records the review but does not create the return ticket. The parity acceptance agent must also run `manage_stage4_rework.py --action open`; the script routes the fixed problem type and updates the local/controller ledgers together. After the responsible agent fixes the issue, create a newer sealed PASS `HBUILD-ID` and `HEVD-ID`, close the ticket with `--action close`, then review the parity again as `ACCEPTED`. The implementation lead confirms both ticket operations.

```bash
python3 scripts/validate_stage4.py \
  --workspace <migration-run>/phase-04-harmony-implementation \
  --build-id <final-HBUILD-for-H4ENV-001> \
  --build-id <final-HBUILD-for-H4ENV-002> \
  --reviewer <parity-acceptance-agent-id> \
  --decision PASS \
  --attest-visual-review \
  --attest-functional-parity \
  --attest-asset-provenance \
  --attest-nativeization-review
```

Repeat `--build-id` exactly once per required `H4ENV-ID`; omit extra lines when only one environment is required. Local validation first runs controller Gate 4 read-only, then seals the exact project, registries, builds, emulator evidence, reviews, acceptance ledger, and closure manifest. The controller must still independently write Gate 4:

```bash
python3 android-harmony-migration-controller/scripts/validate_gate.py \
  --run-dir <migration-run> --phase 4 --write
```

Later changes require a new Phase 4 run or a newly issued controller work order; sealed evidence is never edited.
