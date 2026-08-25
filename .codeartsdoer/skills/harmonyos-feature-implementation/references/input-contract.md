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

**Fixed screen parity (required):** `H4ENV.screen_resolution` and `H4ENV.screen_density` must be **byte-identical** to the frozen Android environment profile (`ENV-ID.resolution / density`) used by the same Page-ID during Phase 2 evidence capture. Phase 4 initialization rejects any H4ENV whose resolution/density differs; `compare_screenshot.py` re-verifies at image load and raises `BLOCKED` on mismatch (no resize fallback — resizing would mask real geometry drift). Changing either side creates a new ID pair.

Any changed input or environment creates a new ID. Missing asset handoff, missing Gate PASS, or upstream hash drift is `BLOCKED`, never inferred.


## gmi Phase 2 适配（默认路径）

当 Phase 2 由 gmi 流程产生时，上方 Phase 2/3 段的旧工件按要求映射为：

| 旧工件 | gmi 来源 |
|---|---|
| `inventory.csv`（REVIEWED 行） | `candidates/inventory.candidates.csv` + `page-fields.candidates.csv`（页面级合成行；缺失 REVIEWED 状态以"审计+覆盖"等价替代） |
| `asset-inventory.csv` + `asset-package/manifest.sha256` + `COMMITTED` | `candidates/asset-mapping.candidates.csv`（`FILE_ASSET` 行）+ `candidates/manifest.sha256`（覆盖 12 表，含 asset 表哈希） |
| evidence index / acceptance registry | `runtime-evidence/evidence-index.csv` + `runtime-gate.csv`（VISITED=ACCEPTED；NOT_ENTERED 须有 reason） |
| evidence packages（PNG+layout-tree） | `runtime-evidence/<page_id>/ui.xml` + `screenshot.png` |
| closure report / `CLOSED` | `phase-2-closure.json`（gmi 闭包，含 gate 字段与 artifact_hashes，见 P3 input-mapping-contract.md 适配节） |
| `advanced-analysis.json` / 动态风险 | `candidates/risk-probes.candidates.csv` |

**Phase 4 前置校验**（取代旧 REVIEWED 链）：`audit-replay.csv` 全 `discrepancy=no` +
`coverage-ledger.csv` `GAP 0` + `phase-2-closure.json` 哈希闭环；任一失败即 `BLOCKED`，
不得以旧流程工件替代（旧流程兼容路径仍有效，但 gmi 工程一律走本节）。

Android 侧证据复制：`runtime-evidence/<page_id>/` 为只读冻结；`inputs/phase2-assets/files/<Asset-ID>/`
由 `asset-mapping.candidates.csv` 的 `FILE_ASSET` 行驱动（`resolved_value`=sha256，`resource_id`=源路径）。
`DIRECT_COPY` 只读本地冻结副本，比照旧契约。
