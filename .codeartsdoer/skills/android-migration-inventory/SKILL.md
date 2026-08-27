---
name: android-migration-inventory
description: Automatically build a migration-grade semantic inventory of a frozen Android app using source discovery, Android CLI traversal, source-runtime binding, state evidence, dynamic-risk probes, and deterministic coverage gates. Use before Android-to-HarmonyOS implementation; do not write HarmonyOS code.
---

# Android Migration Inventory

Describe what the Android app actually contains and does. The unit of truth is `Feature-ID + Page-ID + State-ID + ENV-ID + Evidence-ID`, not a screenshot or page name alone.

## Execution shape: two identical Android emulators (Phase 2 never boots HarmonyOS)

Phase 2 runs on **two configuration-identical Android emulators only** (capture slots A/B, e.g. `emulator-5554` + `emulator-5556`, same AVD/snapshot, same APK SHA-256, same API/resolution/DPI/locale/theme/font-scale, distinct ADB serials and data disks). The HarmonyOS emulator starts only in Phase 4. Two runtime workers run in parallel, but **each emulator executes its own frozen queue serially** (single ADB writer per device). Slots must pass dual-device calibration before any capture; calibration failure stops both workers. `capture_slot`/`device_serial` record where evidence was collected and never expand the business denominator — A/B are two capture slots of one `ENV-ID`, not two business environments.

## Non-negotiable contract

- Models never approve, create `PAGE_PASS`, or declare Phase 2 complete.
- No manual page enumeration or annotation occurs inside Phase 2.
- Every factual claim cites a frozen file/line, sealed runtime evidence, or both; otherwise it is `PENDING_CONFIRMATION`.
- Static discovery gaps, skipped source, parse errors, unsupported UI surfaces, evidence hash/identity/device errors, and contradictory results always block.
- Unfinished runtime tasks follow the REQUIRED/REVIEW dual gate (see below) instead of a blanket "every branch must run" rule.
- Evidence and assets are immutable; recapture or supersede with new IDs.
- MP4 and Android Layout Inspector are not formal evidence.

Runtime tasks carry two frozen fields: `verification_mode` (`SOURCE_ONLY` never occupies emulator time; `RUNTIME_UI` must confirm pages/popups/controls/copy/layout/state; `RUNTIME_EFFECT` must verify real outcomes such as save/delete/navigation/restart-recovery/database changes) and `review_tier` (`REQUIRED` = core/high-risk/easy-to-break, must run; `REVIEW` = low-risk/isomorphic duplicates, sampled for humans).

Machine-gate thresholds for `READY_FOR_HUMAN_REVIEW`: static discovery 100%; UI-state runtime verification ≥90%; externally observable functional verification ≥90%; REQUIRED 100%; evidence hash / page-identity / device-identity errors 0; unverified REVIEW items ≤10% (each listed for human review with reason and suggested check). The machine stage can only ever emit `READY_FOR_HUMAN_REVIEW` — never `PASS`/`CLOSED`. `PASS`/`CLOSED` are written only after human review acceptance; Phase 3 consumes only a humanly-closed Phase 2 whose closure hash is unchanged.

Phase 2 automation ends at its machine Gate. The human review happens only after the machine Gate, when the controller enters `WAITING_HUMAN_REVIEW`.

## Inputs and initialization

Consume the controller run, frozen `scope.json`, and the controller Gate 2 authorization. Initialize with `scripts/init_inventory.py`; then attest frozen accounts, seed, network, permissions, APK, Git revision, Android CLI, device, and emulator using `scripts/attest_environment.py`.

Read [static-page-analysis.md](references/static-page-analysis.md), then run `scripts/analyze_static_pages.py` → `scripts/validate_static_analysis.py` → `scripts/gmi.py --project <any> --workspace <out>` (the single candidate-generation path; generic, 0 hardcode, UNMAPPED=0 gate). gmi emits **13 candidate tables** (`code-map / business-rules / asset-mapping / inventory / page-fields / third-party-dependencies / field-options / navigation-relations / behavior / risk-probes / color-palette / motion / phase-2-completeness`) plus `coverage/coverage-ledger.csv`. Do not traverse runtime until static validation succeeds; do not start LLM fill until candidates exist.

## Automatic understanding — tool-first, LLM as checker (choice, not essay)

**New workflow (7 steps, tool generates candidates; LLM only checks):**
0 Preflight → 1 Full index → 2 Candidate generation (gmi 引擎 13 表 + `coverage/coverage-ledger.csv` 门禁 UNMAPPED=0) → 3 Sharded fill (LLM) → 4 Evidence capture → 5 Auto-ledger → 6 Gate

- **Sharded fill (weak-model friendly):** Split into 4 independent shards `ListShard / AddShard / UpdateShard / DbShard`. Each shard receives its `candidates/*.csv` slice + 2 few-shot rows (e.g. `BaseViewModel.kt:45 双非空`, `BindingAdapters.kt:56 红黄绿`). Output is **rows, not prose**, with `file:line` that must `grep` pass. Choices are closed: `feature ∈ included_features`, `disposition ∈ IN_SCOPE|NON_VISUAL|OUT_OF_SCOPE`. No open-ended writing.
- **Generic path:** `scripts/gmi.py --project <any Android> --workspace <out> [--features A,B]` auto-derives `features/pages` (no `ListFragment→TODO-LIST` hardcode), full-repo UNMAPPED=0; **call-canvas 模式**：扫描页面 body 内**所有** UI 调用（含自研组件如 `CustomSwitchRow/Section/AppIconOption`），每个调用一行（name+line+label/icon/color/size 从参数与尾 lambda `Text(stringResource())` 摘录），宁多勿漏；也提取 **Preference 树/列表子选项** (`field-options.candidates.csv`)、**跳转/返回** (`navigation-relations.candidates.csv`)、**风险信号** (`risk-probes.candidates.csv`)、**颜色真值** (`color-palette.candidates.csv`: Palette `#hex + alpha`、tokens、渐变序列)、**动效/行为** (`motion.candidates.csv`: `NestedScrollConnection` 滚动折叠、`CustomAnimatedVisibility + blur + runtimeShaderEffect` 虚化、`Animatable`、fade…)、以及 **10 类验收矩阵** (`phase-2-completeness.csv`: 每页 RECORDED/MISSING/N/A)。
- CodeArts 不得把无归属的字段/选项交给 P4 猜测。`page-fields.candidates.csv` 和 `field-options.candidates.csv` 每行必须绑定已知 `page_id`；路径推断不唯一时 P2 闭包直接失败，必须先补映射，禁止空值流入 P4。
- **Fidelity extraction:** Every visual component's `color/textSize/background/src/radius/margin/padding` is pre-extracted as `fidelity_attrs` so LLM only maps, not guesses.
- **1:1 asset trace:** `asset-mapping.candidates` already links `layout attr → resolved @color/@drawable → harmony target hint`, so LLM cannot bulk-tag one asset to 6 features.

The runtime lens consumes every machine-generated task, autonomously navigates the frozen app with Android CLI, captures UI tree, screenshot, foreground package, assertions, before/after effects, and transition diffs, then binds each result back to source IDs. Missing or contradictory bindings remain explicit blockers; never infer them.

**Dual-lane runtime bridge (gmi_runtime):** after static validation reaches 100%, split the frozen runnable tasks (all non-`SOURCE_ONLY`) with `scripts/gmi_runtime.py --workspace <out> --split-queues`, which writes deterministic, hash-frozen `runtime-evidence/runtime-queue-a.json` / `runtime-queue-b.json` plus `runtime-task-set.json` / `queue-freeze.sha256`. Allocation unit is the **journey** (a complete chain like create → edit → save → kill → relaunch → verify never crosses lanes); shared-server/shared-remote-state tasks (scenarios, side-effect probes) run exclusively on lane A; a Task-ID may never appear in both queues. Each worker then runs `scripts/gmi_runtime.py --workspace <out> --queue runtime-evidence/runtime-queue-a.json --slot A --serial emulator-5554 --project <root> --package <pkg>` (lane B analog with `emulator-5556`). Every ADB call is forced `-s <serial>`; lanes execute Task-ID by Task-ID serially, checkpoint/resume on interruption, and write isolated `runtime-evidence/lane-a/` / `lane-b/` outputs. A page/state is `VERIFIED` only when foreground is the target package **and** page-identity features match — clicking an entry, staying inside the package, or a bare screenshot never counts; the legacy blind `--auto` BFS is diagnostic-only and can no longer produce `VERIFIED`.

**Anti-forgery audit & merge (gmi_audit):** MUST run `scripts/gmi_audit.py --project <root> --workspace <out> --package <pkg>` after both lanes finish. It audits each lane independently (recomputes every artifact hash, replays page identity, checks slot/serial declarations), verifies queue disjointness and coverage of the frozen task set, then merges the single canonical `runtime-evidence/evidence-index.csv` / `runtime-gate.csv` / `audit-replay.csv`. Later gates read only these merged files. Duplicate Task-IDs, wrong serials, hash/screen/foreground inconsistencies, or recorded==replayed-but-both-wrong statuses all block.

**Emulator resource discipline:** an Android emulator is only needed while its lane's queue is still draining — once a lane finishes its frozen queue (or the fuse trips and parking is confirmed), its emulator is idle and MUST be reported to the controller as "runtime capture ended, emulator may be shut down". Audit, closure, human review, and all of Phase 3/4 never touch the Android emulators again; keeping them running through those stages wastes host resources. Do not start the HarmonyOS emulator before Phase 4, and never start extra Android emulators beyond the two calibrated slots.

## Work separation

Use focused logical lenses for code map, runtime state, business rules, data/capabilities, evidence administration, and coverage. They may use the same approved model service, but outputs remain role-owned and independently recomputed. Record the real CodeArts task and artifact receipt required by the controller.

Archive real Android assets with `scripts/archive_assets.py`. LLM fill works directly from the gmi **candidates/** tables (one shard per feature). Capture state evidence with `scripts/capture_state.py`; every package must be controller-anchored. Bind runtime subjects with `scripts/record_runtime_observation.py`, and capture advanced probes with `scripts/record_advanced_observation.py`. `validate_evidence.py` now auto-computes `coverage-ledger` and enforces **code≥80% / asset 1:1 / every rule has file:line**.

## Machine Gate and rework

Run the deterministic page, advanced, evidence, asset, and coverage validators described in [deterministic-page-gates.md](references/deterministic-page-gates.md), then `scripts/gmi_closure.py` enforces the unified dual-threshold gate: static 100%, UI ≥90%, functional ≥90%, REQUIRED 100%, REVIEW-unverified ≤10%, zero evidence/identity errors. `MISSING` rows never count as complete even with a hint; `NOT_ENTERED`/`UNRECOGNIZED` on REQUIRED tasks always block; `PAGE-NONE` is never a legal field/option ownership; UI and functional coverage are computed separately and `SOURCE_ONLY` never inflates runtime coverage. The machine stage ends at `READY_FOR_HUMAN_REVIEW`. The coverage reviewer may diagnose and open rework but cannot convert its opinion into `PASS`. Do not delete a blocker to improve coverage.

Route source/runtime disagreement, missing pages, weak locator binding, dynamic surfaces, special scenarios, and side-effect probe failures through [review-and-rework.md](references/review-and-rework.md).

`scripts/gmi_phase3_adapter.py` is a read-only converter: it refuses to run unless the closure is `READY_FOR_HUMAN_REVIEW` **and** a human review acceptance record exists whose recorded closure hash still matches; it never creates `PASS`, an acceptance registry, or `CLOSED` on its own. Return the humanly-closed workspace to the controller. The controller independently recomputes Gate 2, generates the exception-first review summary, and pauses at `WAITING_HUMAN_REVIEW`.

## Reference map

- [inventory-contract.md](references/inventory-contract.md): IDs, rows, and catalogs.
- [static-page-analysis.md](references/static-page-analysis.md): source denominator, runtime backlog, and the `verification_mode`/`review_tier` classification.
- [android-cli-procedure.md](references/android-cli-procedure.md) and [evidence-contract.md](references/evidence-contract.md): formal runtime capture (lane evidence, slot/serial binding).
- [advanced-runtime-analysis.md](references/advanced-runtime-analysis.md): dynamic risks, side effects, bounded scenarios, and on-demand instrumentation.
- [understanding-validation.md](references/understanding-validation.md): 90%/100% dual thresholds, mutation qualification tests, and dual-lane allocation checks.
- [deterministic-page-gates.md](references/deterministic-page-gates.md) and [review-and-rework.md](references/review-and-rework.md): closure and failure routing.
- [environment-contract.md](references/environment-contract.md): frozen environments, attestations, and A/B capture slots.
- [governed-execution-contract.md](references/governed-execution-contract.md): governed inputs and artifact-chain constraints.
- [roles-and-authority.md](references/roles-and-authority.md): role ownership and authority boundaries.
