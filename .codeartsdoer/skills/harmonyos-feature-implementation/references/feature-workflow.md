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

State merge, split, removal, or an extra user-visible step requires an approved nativeization decision. The parity checker rejects undocumented remapping.
