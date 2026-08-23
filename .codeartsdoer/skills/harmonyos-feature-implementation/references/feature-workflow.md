# Feature workflow

The implementation unit is one `Feature-ID`; the acceptance unit is one frozen Android inventory row on one required `H4ENV-ID`.

## Governed lifecycle

The two ledgers have separate enforced states:

```text
implementation-ledger.csv
NOT_STARTED → INPUT_LOCKED → ACCEPTED

parity-map.csv
NOT_STARTED → IMPLEMENTED → EVIDENCED → ACCEPTED
                                      ↘ REWORK
REWORK → EVIDENCED with a new HEVD → ACCEPTED after a new review
```

UI, business/data, native-capability, and integration completion are proven by their registries, real source references, assertions, and evidence; they are not extra ledger status strings. An upstream contradiction blocks this Phase 4 run and returns through the controller to Phase 1, 2, or 3 instead of inventing a local state or local ticket type.

## Feature work order

A work order is issued only by the frozen implementation lead. Every included Feature-ID has exactly one active work order. It automatically binds the exact input-lock/manifest hashes, Feature-ID, source inventory rows, parity IDs, target modules, route/surface targets, required H4ENV-IDs, asset IDs, capability requirements/contracts, four feature actors, exclusive code paths, and completion conditions. Its file is read-only and its hash is registered.

The feature owner, UI agent, business/data agent, and native-capability agent must be four different IDs and must not reuse the implementation lead, global visual-asset agent, verification executor, or parity acceptance agent. Every exclusive path must already exist under `harmony-project/`, contain no symlink/generated directory, and overlap no active feature work order.

The feature owner may integrate shared code only through a recorded ownership decision. A required architecture change returns to Phase 3; it is not hidden inside feature implementation.

Every `harmony_source_refs` value is a real project-relative `path:line` reference. The file must exist below the feature's exclusive code paths, the line must exist, and visual/capability symbols must be present in their cited files.

## Parity record

The default relationship is one-to-one:

```text
Android Inventory-ID
+ Android Evidence-ID
+ H4ENV-ID
→ Harmony source references
+ visual elements and assets
+ HEVD-ID
```

State merge, split, removal, carrier replacement, missing UI, and extra or compressed user-visible steps are forbidden implementation-agent actions. A native implementation may change internals only. Functional, navigation, data, asset, and side-effect mismatches cannot be approved away; only a platform-imposed visual offset may enter the dual-approved `PLATFORM_VISUAL` decision path.

Before editing, read the parity row's immutable `migration-unit-contracts.json` record. Treat its component, event, transition, expected-observable, business-rule, data, capability, dependency, and advanced-obligation IDs as the minimum implementation checklist. Do not mark a feature complete while any migration unit lacks a runtime proof for one of these IDs.

The first failed execution starts an automatic repair loop. Rebuild and recapture after each repair. The gate permits no more than two automatic repair attempts for one migration unit; on exhaustion, stop autonomous edits and produce a grouped error report instead of accumulating unrelated changes.
