---
name: android-migration-inventory
description: Automatically build a migration-grade semantic inventory of a frozen Android app using source discovery, Android CLI traversal, source-runtime binding, state evidence, dynamic-risk probes, and deterministic coverage gates. Use before Android-to-HarmonyOS implementation; do not write HarmonyOS code.
---

# Android Migration Inventory

Describe what the Android app actually contains and does. The unit of truth is `Feature-ID + Page-ID + State-ID + ENV-ID + Evidence-ID`, not a screenshot or page name alone.

## Non-negotiable contract

- Models never approve, create `PAGE_PASS`, or declare Phase 2 complete.
- No manual page enumeration or annotation occurs inside Phase 2.
- Every factual claim cites a frozen file/line, sealed runtime evidence, or both; otherwise it is `PENDING_CONFIRMATION`.
- Static discovery gaps, skipped source, parse errors, unsupported UI surfaces, unresolved runtime tasks, and unprobed side effects are blocking.
- Evidence and assets are immutable; recapture or supersede with new IDs.
- MP4 and Android Layout Inspector are not formal evidence.

Phase 2 automation ends at its machine Gate. The human review happens only after the machine Gate, when the controller enters `WAITING_HUMAN_REVIEW`.

## Inputs and initialization

Consume the controller run, frozen `scope.json`, and Phase 2 work order. Initialize with `scripts/init_inventory.py`; then attest frozen accounts, seed, network, permissions, APK, Git revision, Android CLI, device, and emulator using `scripts/attest_environment.py`.

Read [static-page-analysis.md](references/static-page-analysis.md), then run `scripts/analyze_static_pages.py` → `scripts/validate_static_analysis.py` → **`scripts/generate_candidates.py` OR `scripts/gmi.py --project <any> --workspace <out>`** (generic, 0 hardcode, UNMAPPED=0 gate). The gmi path emits **13 candidate tables** (`code-map / business-rules / asset-mapping / inventory / page-fields / third-party-dependencies / field-options / navigation-relations / behavior / risk-probes / color-palette / motion / phase-2-completeness`) plus `coverage/coverage-ledger.csv`. Do not traverse runtime until static validation succeeds; do not start LLM fill until candidates exist.

## Automatic understanding — tool-first, LLM as checker (choice, not essay)

**New workflow (7 steps, tool generates candidates; LLM only checks):**
0 Preflight → 1 Full index → 2 Candidate generation (gmi 引擎 13 表 + `coverage/coverage-ledger.csv` 门禁 UNMAPPED=0) → 3 Sharded fill (LLM) → 4 Evidence capture → 5 Auto-ledger → 6 Gate

- **Sharded fill (weak-model friendly):** Split into 4 independent shards `ListShard / AddShard / UpdateShard / DbShard`. Each shard receives its `candidates/*.csv` slice + 2 few-shot rows (e.g. `BaseViewModel.kt:45 双非空`, `BindingAdapters.kt:56 红黄绿`). Output is **rows, not prose**, with `file:line` that must `grep` pass. Choices are closed: `feature ∈ included_features`, `disposition ∈ IN_SCOPE|NON_VISUAL|OUT_OF_SCOPE`. No open-ended writing.
- **Generic path:** `scripts/gmi.py --project <any Android> --workspace <out> [--features A,B]` auto-derives `features/pages` (no `ListFragment→TODO-LIST` hardcode), full-repo UNMAPPED=0; **call-canvas 模式**：扫描页面 body 内**所有** UI 调用（含自研组件如 `CustomSwitchRow/Section/AppIconOption`），每个调用一行（name+line+label/icon/color/size 从参数与尾 lambda `Text(stringResource())` 摘录），宁多勿漏；也提取 **Preference 树/列表子选项** (`field-options.candidates.csv`)、**跳转/返回** (`navigation-relations.candidates.csv`)、**风险信号** (`risk-probes.candidates.csv`)、**颜色真值** (`color-palette.candidates.csv`: Palette `#hex + alpha`、tokens、渐变序列)、**动效/行为** (`motion.candidates.csv`: `NestedScrollConnection` 滚动折叠、`CustomAnimatedVisibility + blur + runtimeShaderEffect` 虚化、`Animatable`、fade…)、以及 **10 类验收矩阵** (`phase-2-completeness.csv`: 每页 RECORDED/MISSING/N/A)。
- CodeArts 不得把无归属的字段/选项交给 P4 猜测。`page-fields.candidates.csv` 和 `field-options.candidates.csv` 每行必须绑定已知 `page_id`；路径推断不唯一时 P2 闭包直接失败，必须先补映射，禁止空值流入 P4。
- **Fidelity extraction:** Every visual component's `color/textSize/background/src/radius/margin/padding` is pre-extracted as `fidelity_attrs` so LLM only maps, not guesses.
- **1:1 asset trace:** `asset-mapping.candidates` already links `layout attr → resolved @color/@drawable → harmony target hint`, so LLM cannot bulk-tag one asset to 6 features.

The runtime lens consumes every machine-generated task, autonomously navigates the frozen app with Android CLI, captures UI tree, screenshot, foreground package, assertions, before/after effects, and transition diffs, then binds each result back to source IDs. Missing or contradictory bindings remain explicit blockers; never infer them.

**Runtime bridge (gmi_runtime):** `scripts/gmi_runtime.py --project <root> --workspace <out> --package <pkg> --activity MainActivity [--auto] [--grant-perms] [--compare]` starts the app on a live emulator (adb), and captures per-step `ui.xml + screenshot.png + evidence.json`. `--auto`: cascades anchor-matched taps (strings.xml 页→中文锚点) from home through deep pages (BFS), handles permission dialogs automatically, and writes `runtime-gate.csv` (VISITED/NOT_ENTERED) + `route-hints.csv` + `compare.csv`. Each evidence row records `foreground`; a page is only VISITED if foreground is the target package (else EXITED — never a fake visit). **Anti-forgery gate: MUST run `scripts/gmi_audit.py --project <root> --workspace <out> --package <pkg>` after every runtime run.** It replays the evidence directory (no emulator) and re-derives each page state; any recorded status ≠ replayed status → `audit-discrepancies.csv` + exit 1 (gate fails). A page can never be VISITED on the basis of "was clicked" alone — only on evidence (foreground ∈ pkg AND UI features matched).

## Work separation

Use focused logical lenses for code map, runtime state, business rules, data/capabilities, evidence administration, and coverage. They may use the same approved model service, but outputs remain role-owned and independently recomputed. Record the real CodeArts task and artifact receipt required by the controller.

Archive real Android assets with `scripts/archive_assets.py`. Run `scripts/generate_candidates.py` first, then use its **candidates/** as the only source for `scripts/build_inventory.py` sharded builds (one shard per feature). Capture state evidence with `scripts/capture_state.py`; every package must be controller-anchored. Bind runtime subjects with `scripts/record_runtime_observation.py`, and capture advanced probes with `scripts/capture_advanced_probe.py`. `validate_evidence.py` now auto-computes `coverage-ledger` and enforces **code≥80% / asset 1:1 / every rule has file:line**.

## Machine Gate and rework

Run the deterministic page, advanced, evidence, asset, and coverage validators described in [deterministic-page-gates.md](references/deterministic-page-gates.md). The coverage reviewer may diagnose and open rework but cannot convert its opinion into `PASS`. A claim is complete only when every frozen denominator item is accounted for and every applicable environment has reproducible evidence.

Route source/runtime disagreement, missing pages, weak locator binding, dynamic surfaces, special scenarios, and side-effect probe failures through [review-and-rework.md](references/review-and-rework.md). Do not delete a blocker to improve coverage.

Return the closed workspace to the controller. The controller independently recomputes Gate 2, generates the exception-first review summary, and pauses at `WAITING_HUMAN_REVIEW`.

## Reference map

- [inventory-contract.md](references/inventory-contract.md): IDs, rows, and catalogs.
- [static-page-analysis.md](references/static-page-analysis.md): source denominator and runtime backlog.
- [android-cli-procedure.md](references/android-cli-procedure.md) and [evidence-contract.md](references/evidence-contract.md): formal runtime capture.
- [advanced-runtime-analysis.md](references/advanced-runtime-analysis.md): dynamic risks, side effects, and scenarios.
- [deterministic-page-gates.md](references/deterministic-page-gates.md) and [review-and-rework.md](references/review-and-rework.md): closure and failure routing.
