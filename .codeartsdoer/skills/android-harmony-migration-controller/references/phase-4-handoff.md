# Phase 4 handoff

Dispatch `$harmonyos-feature-implementation` only after the frozen controller issues a registered Phase 4 work order from a current Gate 3 `PASS` and a successful read-only rerun of Gates 1-3. Gate conditions live in [phase-gates.md](phase-gates.md); this file lists the handoff artifacts only.

The work order freezes four actor IDs:

- implementation lead;
- visual-asset agent;
- verification executor;
- parity acceptance agent.

The four governance assignments are distinct real CodeArts worker tasks. Before delivery, record a unique platform task ID and hash-bound role-owned artifact for every assignment. The final audit rejects a role-name-only team, task-ID reuse, missing receipts, and self-acceptance.

They are mutually distinct and do not reuse a Phase 1-3 actor. The implementation lead owns the Phase 4 task row. Only the verification executor may seal HBUILD or HEVD packages, and only the parity acceptance agent may accept parity or open and close Phase 4 rework.

## Work-order model (page-owned)

Implementation work is page-owned: one `PAGE_WORK_ORDER` per inventory Page-ID, each with one exclusive owner page implementation and one page contract (`page-contracts/<Page-ID>.json`, components plus behavior bindings). Shared capabilities never ride inside a page order; they use a separate `SHARED_CAPABILITY_WORK_ORDER` per capability. The controller tracks both families in the page-model ledgers below.

## Inputs

The work order and `stage-04-input-lock.json` bind, by SHA-256:

- canonical controller scope and the registered Phase 4 work order;
- the controller-owned immutable Gate 3 snapshot and registered upstream Phase 3 work order;
- the gmi Phase 2 handoff: `gmi/phase-2-closure.json`, `phase-manifest.json`, the 13 candidate tables and manifest, `coverage-ledger.csv`, `runtime-evidence/`, `audit-replay.csv`, reviewed inventory rows, evidence index, and every archived asset;
- Phase 3 sealed outputs: input lock, gate report, closure manifest, `CLOSED`, scaffold snapshot, architecture/module/route/surface/public-UI/capability/asset registries, HENV registry, and every frozen HENV.

Every small input is copied below `inputs/upstream/`. Every Android evidence package is copied to `inputs/android-evidence/<Evidence-ID>/`, and every source asset is copied below `inputs/phase2-assets/`. The input lock records canonical source path, snapshot path, SHA-256, and size. Do not read the mutable latest controller gate as Gate 3 evidence.

## Returned package

Expect `phase-04-harmony-implementation/` to contain:

- work-order/input records and frozen H4ENV records;
- the page-model ledgers: `page-implementation-ledger.csv`, `page-work-order-registry.csv`, `capability-work-order-registry.csv`, `parity-map.csv`, `attempt-ledger.csv`, `evidence-index.csv`, `acceptance-ledger.csv`, and `rework-tickets.csv`;
- `page-contracts/<Page-ID>.json`, one per inventory Page-ID, keeping complete page coverage while assigning runtime-observed controls, events, and transitions to their actual state;
- a lead-frozen `asset-conversion-contracts.json` and read-only `asset-conversions/<Conversion-ID>/` packages for every format conversion;
- implemented HarmonyOS project;
- read-only `builds/<HBUILD-ID>/` packages;
- read-only `evidence/.../<HEVD-ID>/` packages;
- immutable `reviews/<HREV-ID>.json` records;
- `stage-04-gate-report.json`;
- `stage-04-closure-manifest.sha256`;
- `CLOSED`.

`attempt-ledger.csv` is mirrored to the controller as a hash chain before every automated execution. Each parity row must cite one final HBUILD-backed HEVD and exactly one accepted HREV that recomputes both Android and Harmony evidence hashes. MP4 is prohibited.

Asset modes are exactly `DIRECT_COPY`, `FORMAT_CONVERSION`, or `RECREATE_FROM_PUBLIC_UI`. A conversion must seal its executable contract, command/result logs, source snapshot, and target bytes. A recreated asset needs a current HEVD for a state it covers, an approved Stage 4 `ASSET_RECREATION` decision, and a live controller decision.

## Rework

Each parity row allows one initial execution and at most two automated repairs. Deleting a local failure package does not restore the budget because the controller mirror is authoritative. Runtime event/transition coverage must come from raw operation traces with before/after snapshots; a self-declared ID list is not evidence. Phase 4 rework changes only through `manage_stage4_rework.py` (owned by `harmonyos-feature-implementation`, in its `scripts/` directory); the controller mirror in `rework-log.csv` stays authoritative and any open or inconsistent ticket blocks Gate 4.
