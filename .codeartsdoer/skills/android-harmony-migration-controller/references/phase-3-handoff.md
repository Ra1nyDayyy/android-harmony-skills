# Phase 3 handoff

Dispatch `$harmonyos-migration-scaffold` only after the frozen controller issues a registered Phase 3 work order from a current, independently rechecked Gate 2 `PASS`.

The work order freezes six actor IDs:

- architecture lead;
- toolchain agent;
- navigation agent;
- public UI agent;
- capability-contract agent;
- architecture acceptance agent.

They must be mutually distinct and must not reuse any frozen Phase 1/2 actor ID. The architecture lead owns the Phase 3 task-ledger row; only the acceptance agent can issue the final Stage 3 report.

All six assignments are distinct real CodeArts worker tasks. Record their unique platform task IDs and hash-bound artifacts after Stage 3 closes. Phase 4 work-order issuance rejects an incomplete receipt set; actor names alone are insufficient.

The Phase 3 input lock must identify and hash:

- controller scope;
- registered Phase 3 work order and its digest;
- controller-owned immutable Phase 2 gate snapshot;
- Phase 2 closure report, closure manifest, and `CLOSED` marker;
- Phase 2 phase manifest, `inventory.csv`, acceptance registry, evidence index, active row count, and deterministic source-row keys;
- Phase 2 `asset-inventory.csv`, asset-package manifest/marker, and every real archived asset;
- Phase 2 evidence-anchor snapshot and the controller-owned anchor registry;
- Phase 2 data-dependency, system-capability, and third-party-dependency catalogs.

Every item above is bound by SHA-256. Stage 3 copies the Gate 2 snapshot into `inputs/phase-02-gate-report.json`; it must not later treat the mutable latest controller gate report as its Gate 2 evidence.

The HarmonyOS architecture lead owns module and target placement. The architecture acceptance agent is the only final reviewer and must be separate from creators, mappers, status updaters, and verification executors.

Required invariants:

- changed HarmonyOS environments receive a new `HENV-ID`;
- changed project or registries receive a new `HVER-ID`;
- the HENV freezes nine category-specific executable hashes, argument tokens, success markers, and error markers; the actually executed preflight belongs to the HVER;
- visual rows map to real routes or non-route visual-surface shells without changing Phase 2;
- nonvisual requirements map to interface-only contracts without fabricated pages;
- all mappings cite files inside the real HarmonyOS project and runtime smoke evidence;
- each smoke command generates a new direct JSON result bound to the frozen serial, bundle, target, page, and shell; pre-existing result files are invalid;
- every real route or visual-surface shell is opened on a frozen emulator and cites sealed PNG screenshot evidence;
- `asset-registry.csv` maps every real Phase 2 Asset-ID one-to-one to a safe module-local target path and unique symbol, with an explicit READY migration decision;
- clean build produces or changes a structurally valid HAP, and the HVER seals immutable copies of HAP and fully validated PNG files;
- signing secrets never enter the project or reports;
- no business implementation is present;
- no Phase 3 rework ticket remains open;
- the passing workspace is sealed by `stage-03-closure-manifest.sha256` and `CLOSED`;
- controller Gate 3 remains closed until the independent Phase 3 report is `PASS`.

The Phase 3 closure manifest covers the complete workspace except `stage-03-gate-report.json`, `stage-03-closure-manifest.sha256`, and `CLOSED`. `CLOSED` contains the SHA-256 of the final Stage 3 report. Controller Gate 3 recomputes the entire file set and every hash.

Phase 3 rework changes only through `manage_stage3_rework.py`. The frozen architecture acceptance agent opens or closes it, the architecture lead confirms deterministic routing, and closing requires a newer sealed PASS HVER from the frozen toolchain agent. The manager mirrors every ticket into controller `rework-log.csv`; either side being open or inconsistent blocks Gate 3.
