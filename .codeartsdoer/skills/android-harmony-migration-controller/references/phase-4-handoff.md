# Phase 4 handoff

Dispatch `$harmonyos-feature-implementation` only after the frozen controller issues a registered Phase 4 work order from a current Gate 3 `PASS` and a successful read-only rerun of Gates 1–3.

The work order freezes four actor IDs:

- implementation lead;
- visual-asset agent;
- verification executor;
- parity acceptance agent.

The four governance assignments and every feature work order's owner/UI/business-data/native assignments are distinct real CodeArts worker tasks. Before delivery, record a unique platform task ID and hash-bound role-owned artifact for every assignment. The final audit rejects a role-name-only team, task-ID reuse, missing receipts, and self-acceptance.

They are mutually distinct and do not reuse a Phase 1–3 actor. The implementation lead owns the Phase 4 task row. Only the verification executor may seal HBUILD or HEVD packages, and only the parity acceptance agent may accept parity or open and close Phase 4 rework.

## Frozen inputs

The work order and `stage-04-input-lock.json` bind, by SHA-256:

- canonical controller scope and the registered Phase 4 work order;
- the controller-owned immutable Gate 3 snapshot and registered upstream Phase 3 work order;
- Phase 2 closure report, closure manifest, `CLOSED`, reviewed inventory, evidence index, asset inventory, asset package, static page/component/event/transition records, runtime observations, page gate, advanced observations, probe index, advanced gate, and every archived asset;
- Phase 3 input lock, gate report, closure manifest, `CLOSED`, scaffold snapshot, architecture/module/route/surface/public-UI/capability/asset registries, HENV registry, and every frozen HENV.

Every small input is copied below `inputs/upstream/`. Every Android evidence package is copied to `inputs/android-evidence/<Evidence-ID>/`, and every source asset is copied below `inputs/phase2-assets/`. The input lock records canonical source path, snapshot path, SHA-256, and size. Do not read the mutable latest controller gate as Gate 3 evidence.

## Returned package

Expect `phase-04-harmony-implementation/` to contain:

- work-order/input records and frozen H4ENV records;
- implementation, parity, visual, asset, capability, nativeization, evidence, acceptance, and rework ledgers;
- `migration-unit-contracts.json`, which keeps complete page coverage while assigning runtime-observed controls, events, and transitions to their actual state;
- `attempt-ledger.csv`, mirrored to the controller as a hash chain before every automated execution;
- a lead-frozen `asset-conversion-contracts.json` and read-only `asset-conversions/<Conversion-ID>/` packages for every format conversion;
- implemented HarmonyOS project;
- read-only `builds/<HBUILD-ID>/` packages;
- read-only `evidence/.../<HEVD-ID>/` packages;
- immutable `reviews/<HREV-ID>.json` records;
- `stage-04-gate-report.json`;
- `stage-04-closure-manifest.sha256`;
- `CLOSED`.

The final unit remains one feature × one page × one state × one required environment. Each parity row must cite one final HBUILD-backed HEVD and exactly one accepted HREV that recomputes both Android and HarmonyOS evidence hashes. MP4 is prohibited.

Asset modes are exactly `DIRECT_COPY`, `FORMAT_CONVERSION`, or `RECREATE_FROM_PUBLIC_UI`. A conversion must seal its executable contract, command/result logs, source snapshot, and target bytes. A recreated asset needs a current HEVD for a state it covers, an approved Stage 4 `ASSET_RECREATION` decision, and a live controller decision.

Each parity row allows one initial execution and at most two automated repairs. Deleting a local failure package does not restore the budget because the controller mirror is authoritative. Runtime event/transition coverage must come from raw operation traces with before/after snapshots; a self-declared ID list is not evidence. Controller Gate 4 normalizes Android and Harmony coordinates to their frozen viewports and rejects geometry beyond the configured tolerance or differing visual semantics.

The closure manifest uses `<sha256><two spaces><relative path>` lines. It excludes only the final report, closure manifest, `CLOSED`, transient lock/staging/cache files, and generated HarmonyOS project output. `CLOSED` is the SHA-256 of the final Stage 4 report. Controller Gate 4 recomputes all inputs, packages, ledgers, hashes, and the complete closure file set.
