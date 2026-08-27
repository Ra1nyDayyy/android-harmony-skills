# Phase 3 handoff

Dispatch `$harmonyos-migration-scaffold` only after the frozen controller issues a registered Phase 3 work order from a current, independently rechecked Gate 2 `PASS`. Gate conditions live in [phase-gates.md](phase-gates.md); this file lists the handoff artifacts only.

The work order freezes six actor IDs:

- architecture lead;
- toolchain agent;
- navigation agent;
- public UI agent;
- capability-contract agent;
- architecture acceptance agent.

They must be mutually distinct and must not reuse any frozen Phase 1/2 actor ID. The architecture lead owns the Phase 3 task-ledger row; only the acceptance agent can issue the final Stage 3 report.

All six assignments are distinct real CodeArts worker tasks. Record their unique platform task IDs and hash-bound artifacts after Stage 3 closes. Phase 4 work-order issuance rejects an incomplete receipt set; actor names alone are insufficient.

## Inputs (gmi artifact chain)

The Phase 3 input lock must identify and hash the gmi Phase 2 closure chain plus the controller records:

- controller scope;
- registered Phase 3 work order and its digest;
- controller-owned immutable Phase 2 gate snapshot;
- `phase-02-android-inventory/gmi/phase-2-closure.json` and `phase-manifest.json` (`generator=gmi`);
- `gmi/candidates/` — the 13 candidate tables and their manifest;
- `gmi/coverage/coverage-ledger.csv`;
- `gmi/runtime-evidence/` — `runtime-gate.csv` and `audit-replay.csv`;
- `gmi/audit-replay.csv`;
- Phase 2 `asset-inventory.csv`, asset-package manifest/marker, and every real archived asset;
- Phase 2 evidence-anchor snapshot and the controller-owned anchor registry;
- Phase 2 data-dependency, system-capability, and third-party-dependency catalogs.

Every item above is bound by SHA-256. Stage 3 copies the Gate 2 snapshot into `inputs/phase-02-gate-report.json`; it must not later treat the mutable latest controller gate report as its Gate 2 evidence.

## Returned package

Expect `phase-03-harmony-scaffold/` to contain the input lock, the sealed HENV/HVER records, module/architecture/route/surface/public-UI/capability/asset registries, the scaffolded HarmonyOS project, smoke results with sealed PNG evidence, `stage-03-gate-report.json`, `stage-03-closure-manifest.sha256`, and `CLOSED`.

## Handoff invariants

- The architecture lead owns module and target placement; the acceptance agent is the only final reviewer and is separate from creators, mappers, status updaters, and verification executors.
- Visual rows map to real routes or non-route visual-surface shells without changing Phase 2; nonvisual requirements map to interface-only contracts without fabricated pages.
- All mappings cite files inside the real HarmonyOS project and runtime smoke evidence; each smoke command generates a new direct JSON result bound to the frozen serial, bundle, target, page, and shell.
- Every real route or visual-surface shell is opened on a frozen emulator and cites sealed PNG screenshot evidence.
- `asset-registry.csv` maps every real Phase 2 Asset-ID one-to-one to a safe module-local target path and unique symbol, with an explicit READY migration decision.
- No business implementation is present; signing secrets never enter the project or reports.

## Rework

Phase 3 rework changes only through `manage_stage3_rework.py` (owned by `harmonyos-migration-scaffold`, in its `scripts/` directory). The frozen architecture acceptance agent opens or closes it, the architecture lead confirms deterministic routing, and closing requires a newer sealed PASS HVER from the frozen toolchain agent. The manager mirrors every ticket into controller `rework-log.csv`; either side being open or inconsistent blocks Gate 3.
