# Input and mapping contract

## Controller work order first

Phase 3 may start only from a controller-issued and controller-registered Phase 3 work order. The work order freezes six distinct IDs: architecture lead, toolchain agent, navigation agent, public UI agent, capability-contract agent, and architecture-acceptance agent. `init_scaffold.py` requires `--work-order` and rejects an architecture lead that differs from that frozen assignment.

The initializer runs the controller's Phase 2 gate validator again **without `--write`**. It then proves that Phase 2 is `CLOSED`, its closure manifest still covers the exact package, and its `CLOSED` marker still binds the closure report. A cached `PASS` string alone is not an input.

## Immutable Phase 2 input

Phase 3 creates `stage-03-input-lock.json` containing canonical paths, immutable snapshot paths, SHA-256 values, row counts, IDs, work-order identity, and frozen ownership for:

- controller scope;
- the copied Phase 2 controller-gate snapshot (its `path` deliberately does not point at the live gate, which becomes Gate 3 later);
- Phase 2 closure report, closure manifest, `CLOSED`, and Phase 2 manifest;
- Phase 2 `inventory.csv`, acceptance registry, and evidence index;
- Phase 2 `asset-inventory.csv`, asset-package manifest and `COMMITTED` marker;
- each real archived asset file, by canonical Phase 2 path and SHA-256;
- Phase 2 evidence-anchor snapshot and the matching controller anchor registry;
- data-dependency, system-capability, and third-party-dependency catalogs;
- the committed advanced analysis, advanced observations, deterministic advanced gate report, and probe-evidence index;
- the registered Phase 3 work order.

All of these small input records are copied byte-for-byte into `inputs/` for later review. Except for the live controller gate, canonical Phase 2 files remain the authoritative read-only source paths; their copies are snapshots, not editable replacements. The initializer never edits, normalizes, or rewrites Phase 2 inventory or evidence.

Every active Phase 2 inventory row must already be `REVIEWED` by the frozen coverage checker. The evidence index and acceptance registry must exactly cover those active rows with `ACCEPTED`, and the Phase 2 anchor snapshot must exactly equal the controller-owned Phase 2 anchor rows. Superseded history remains in the locked source file but does not create a Phase 3 shell.

Every active, non-superseded Phase 2 row receives a deterministic `Source-Row-Key`:

```text
SROW-<SHA256(feature_id|page_id|state_id|env_id|evidence_id)[0:20]>
```

The Phase 3 gate recomputes the key and all input hashes. Any drift is `BLOCKED`.

## ArkUI template provenance

Phase 3 uses the bundled `assets/arkui-stage-template` as the only scaffold source. Initialization copies it into a staging workspace, excludes caches and machine-local files, replaces the frozen application identity, rejects unresolved tokens, and then atomically installs it as `harmony-project/`.

`inputs/arkui-stage-template.manifest.sha256` records every source-template file. `template-generation.json` records the template ID, manifest digest, generated-file count, required Stage files, bundle name, application name, vendor, log tag, and initial project-manifest digest. The Phase 3 validator and controller Gate 3 recheck this provenance and the required project files.

## Advanced Phase 2 handoff

`advanced-obligations.json` must be one-to-one with the frozen dynamic-risk, side-effect, and scenario IDs:

- dynamic risks become `PHASE4_DYNAMIC_SURFACE` obligations;
- side effects become `PHASE3_CAPABILITY_CONTRACT` obligations and seed one interface requirement for every included Feature-ID;
- special scenarios become `PHASE4_SCENARIO_TEST` obligations.

The scaffold does not implement these behaviors. It preserves them so later phases cannot lose WebView/dynamic content, database/network/background effects, permissions, special accounts, abnormal data, or extreme states.

## Asset landing mapping

Initialization validates the complete Phase 2 asset package and seeds exactly one `asset-registry.csv` row per real `Asset-ID`. `NONE_FOUND` never creates a registry row. The source identity columns—archive path, hash, type, Feature/Page/State arrays—remain byte-for-byte equal to `asset-inventory.csv`.

The architecture lead completes each row with a real target module, a safe project-relative resource path below that module's `src/main/resources/`, a module-unique resource symbol, and one allowed plan/decision pair:

- `DIRECT_COPY` / `COPY_UNCHANGED`
- `FORMAT_CONVERSION` / `CONVERT_FORMAT`
- `RECREATE_FROM_PUBLIC_UI` / `RECREATE_LATER`

Set `created_by` to the frozen architecture lead and `status` to `READY`. Phase 3 records the landing decision only; copying, converting, or recreating the asset belongs to implementation work under a later order.

## Visual mapping

Exactly one `architecture-map.csv` row must exist for every active frozen inventory row:

```text
Source-Row-Key
→ Mapping-Type
+ Harmony-Module-ID
+ Route-ID or Surface-Shell-ID
+ Page-Shell-ID
+ actual source path
+ one or more Screenshot-IDs
+ Verification-ID
```

Allowed visual mapping types:

- `ROUTE_PAGE`: the original surface was independently navigable; use a real route.
- `VISUAL_SURFACE`: dialog, sheet, tab body, embedded surface, or other non-route visual surface; use a real surface shell.
- `EXCLUDED_BY_SCOPE`: allowed only when the Feature-ID was already excluded in Phase 1.

Multiple states of the same page may point to the same route or surface shell, but every active source row must remain present. Superseded Phase 2 history stays hash-locked in the input snapshot and is not silently converted into a current shell.

Every visual mapping row references the sealed `HSCREEN-ID` values that prove its real shell on every emulator marked `screenshot_required`. Multiple state rows may reuse one screenshot only when they map to the same Page-Shell-ID and route/surface target. Nonvisual capability rows never receive screenshots.

## Nonvisual mapping

Initialization resolves every Phase 2 `data_dependency_refs`, `system_capability_refs`, and `third_party_dependency_refs` value against its locked catalog before seeding `capability-contracts.csv`. An explicit verified `NONE_FOUND` sentinel proves that the category was checked; it **never** becomes a fake HarmonyOS capability requirement. The initializer also seeds a scope-sourced requirement for an included Feature-ID that has no active visual inventory row.

Each requirement maps to:

```text
Capability-Requirement-ID
→ Harmony-Module-ID
+ Capability-Contract-ID
+ contract symbol
+ actual interface file
+ source inventory keys or scope Feature-ID
```

Do not fabricate a page, route, screenshot, or Page-ID for nonvisual work.

## Migration status

`migration-status.csv` contains a separate row for every visual source key and capability requirement. It never changes Phase 2.

Allowed states:

- `NOT_STARTED`
- `SHELL_CREATED_PENDING_IMPLEMENTATION`
- `CONTRACT_CREATED_PENDING_IMPLEMENTATION`
- `BLOCKED`
- `EXCLUDED_BY_SCOPE`

Gate `PASS` permits only `SHELL_CREATED_PENDING_IMPLEMENTATION`, `CONTRACT_CREATED_PENDING_IMPLEMENTATION`, and Phase-1-authorized exclusions.
