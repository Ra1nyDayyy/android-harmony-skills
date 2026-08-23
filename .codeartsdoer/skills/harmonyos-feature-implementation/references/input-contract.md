# Phase 4 input contract

Phase 4 consumes only passing, hash-locked artifacts.

## Phase 1

Lock controller scope, Gate 3 controller report, included/excluded Feature-IDs, Android environments, account roles, seed reset references, network profiles, locale, theme, timezone, permissions, and target device classes.

## Phase 2

Lock the closure report, exact closure manifest, `CLOSED`, `inventory.csv`, evidence index, every complete evidence package referenced by an active `REVIEWED` inventory row, `asset-inventory.csv`, and both `asset-package/manifest.sha256` and `COMMITTED`. `SUPERSEDED` inventory rows are not active.

The asset inventory must provide `asset_id`, source and archived paths, SHA-256, file type, applicable Feature/Page/State IDs, and review status. Phase 4 copies each archived byte to `inputs/phase2-assets/files/<Asset-ID>/<basename>` and records its source/snapshot paths, hash, and size. `DIRECT_COPY` reads only that local frozen copy; a mutable Android worktree or later upstream lookup is not a valid byte source.

## Phase 3

Lock the active Phase 3 work order, `stage-03-input-lock.json`, `stage-03-gate-report.json`, exact closure manifest, `CLOSED`, the accepted source snapshot, HENV registry and every work-order-listed environment, and the architecture/module/route/surface/public-UI/capability/asset registries.

Phase 4 copies only project files named by the accepted snapshot. It never modifies Phase 3. All controller-listed small inputs are copied to `inputs/upstream/`; each input-lock record binds canonical source path, local snapshot path, SHA-256, and size.

Each Android evidence copy is stored at `inputs/android-evidence/<Evidence-ID>` with its manifest, metadata, PNG, layout-tree, canonical whole-package digest, total size, and file count in `stage-04-input-lock.json`. The copied package is read-only and is the only Android side used by final parity review.

## Composite Phase 4 environment

Each `H4ENV-ID` binds one Phase 1 Android environment profile to one frozen Phase 3 HarmonyOS emulator:

```text
H4ENV-ID = source ENV-ID + base HENV-ID + HDEVICE-ID + exact serial + exact Bundle + comparison policy
```

The selected device must be a required screenshot emulator. Account, role, seed, reset reference, network profile/conditions/toggle, locale, theme, font scale, timezone, permissions, and orientation must all be nonempty frozen business inputs. Device/toolchain/application identity comes from Phase 3. Root `category_contracts` must cover exactly all 14 Phase 4 categories and bind canonical executable paths/hashes, required argv tokens, success markers, and error markers.

Any changed input or environment creates a new ID. Missing asset handoff, missing Gate PASS, or upstream hash drift is `BLOCKED`, never inferred.
