# Phase 4 input contract

Phase 4 consumes only passing, hash-locked artifacts: the **Phase 3 sealed outputs** plus the **gmi Phase 2 handoff**. There is no alternative entry path; a missing or drifted input is `BLOCKED`, never inferred.

## gmi Phase 2 handoff (the only Phase 2 source)

| Input | gmi source |
|---|---|
| Page list / scope grouping | `candidates/inventory.candidates.csv` + `page-fields.candidates.csv` (page-level composite rows) |
| Asset inventory + package | `candidates/asset-mapping.candidates.csv` (`FILE_ASSET` rows) + `candidates/manifest.sha256` |
| Runtime gate | `runtime-evidence/runtime-gate.csv` (every required page VISITED=ACCEPTED; NOT_ENTERED must carry a reason) |
| Audit closure | `runtime-evidence/audit-replay.csv` shows zero discrepancies (discrepancy column all "no") |
| Phase 2 closure | `phase-2-closure.json` (gate fields + artifact hashes) |
| Evidence index / packages | `runtime-evidence/evidence-index.csv` + `runtime-evidence/<page_id>/ui.xml` + `screenshot.png` |
| Dynamic risks | `candidates/risk-probes.candidates.csv` |
| 10-category gap matrix | `candidates/phase-2-completeness.csv` (per-page `MISSING` is a known boundary; never silently filled) |

**Phase 4 pre-checks (all must pass before initialization):** `runtime-evidence/runtime-gate.csv` VISITED=ACCEPTED for every required page, `runtime-evidence/audit-replay.csv` zero discrepancies (any discrepancy "YES" row blocks), `phase-2-closure.json` hash closure. Any failure is `BLOCKED`; no pre-gmi artifact set may substitute.

Android-side evidence copies are read-only frozen: `runtime-evidence/<page_id>/` is copied under `inputs/android-evidence/<Evidence-ID>` with manifest, metadata, PNG, layout-tree, canonical digest, size, and file count recorded in `stage-04-input-lock.json`. Honest pending pages listed by the gmi recovery report keep a `PENDING_RUNTIME_VERIFY` record without a snapshot; unlisted non-ACCEPTED rows are rejected.

## Semantic page-contract inputs (consumed by `page_acceptance_contract.py`)

| Semantic input | gmi source | Contract field |
|---|---|---|
| Page fields (order/type/label/icon) | `candidates/page-fields.candidates.csv` | `gmi_fields` |
| Field/option children | `candidates/field-options.candidates.csv` | `gmi_options` |
| Navigation + back | `candidates/navigation-relations.candidates.csv` | `gmi_navigation` |
| Motion | `candidates/motion.candidates.csv` | `gmi_motion` |
| Events/behavior | `candidates/behavior.candidates.csv` | `behavior_bindings` (verifiable interaction binding) |
| Color truth | `candidates/color-palette.candidates.csv` | contract asset colors (hex+alpha) |

## Phase 3 sealed outputs

Lock the active Phase 3 work order, `stage-03-input-lock.json`, `stage-03-gate-report.json`, exact closure manifest, `CLOSED`, the accepted source snapshot, HENV registry and every work-order-listed environment, and the architecture/module/route/surface/public-UI/capability/asset registries.

Phase 4 copies only project files named by the accepted snapshot. It never modifies Phase 3. All controller-listed small inputs are copied to `inputs/upstream/`; each input-lock record binds canonical source path, local snapshot path, SHA-256, and size.

## Frozen asset landing

`inputs/phase2-assets/files/<Asset-ID>/<basename>` is driven by the `FILE_ASSET` rows of `asset-mapping.candidates.csv` (`resolved_value`=sha256, `resource_id`=source path); each archived byte is recorded with source/snapshot paths, hash, and size. `DIRECT_COPY` reads only that local frozen copy; a mutable Android worktree or later upstream lookup is not a valid byte source.

## Composite Phase 4 environment

Each `H4ENV-ID` binds one Phase 1 Android environment profile to one frozen Phase 3 HarmonyOS emulator:

```text
H4ENV-ID = source ENV-ID + base HENV-ID + HDEVICE-ID + exact serial + exact Bundle + comparison policy
```

The selected device must be a required screenshot emulator. Account, role, seed, reset reference, network profile/conditions/toggle, locale, theme, font scale, timezone, permissions, and orientation must all be nonempty frozen business inputs. Device/toolchain/application identity comes from Phase 3. Root `category_contracts` must cover exactly all 14 Phase 4 categories and bind canonical executable paths/hashes, required argv tokens, success markers, and error markers.

**Fixed screen parity (required):** `H4ENV.screen_resolution` and `H4ENV.screen_density` must be **byte-identical** to the frozen Android environment profile (`ENV-ID.resolution / density`) used by the same Page-ID during Phase 2 evidence capture. Phase 4 initialization rejects any H4ENV whose resolution/density differs; `_stage4_audit.py` re-verifies inline during audit (the HEVD screenshot-resolution equality check plus `geometry_matches` bounds checks) and raises `BLOCKED` on mismatch (no resize fallback — resizing would mask real geometry drift). Changing either side creates a new ID pair.

Any changed input or environment creates a new ID. Missing asset handoff, missing Gate PASS, or upstream hash drift is `BLOCKED`, never inferred.
