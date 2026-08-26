---
name: android-harmony-migration-controller
description: Coordinate an auditable Android-to-HarmonyOS migration through Phases 1-4 with frozen inputs, specialist work orders, deterministic machine gates, mandatory human review, and governed rework. Use for one-shot workflow requests or phase governance; do not inspect Android UI or implement HarmonyOS application code in the controller role.
---

# Android to HarmonyOS Migration Controller

Create and govern one migration run. Specialist Skills do the analysis and implementation; this controller freezes inputs, issues work, verifies evidence, pauses for people, and routes rework.

## Non-negotiable contract

- Models never approve, accept deviations, or declare a phase complete.
- A script-authored machine `PASS` is necessary but never sufficient to open the next phase.
- After every Gate, generate the compact review summary and enter `WAITING_HUMAN_REVIEW`.
- Continue only after a current, sealed `APPROVED` or `APPROVED_DEVIATION` decision bound to that Gate report's SHA-256.
- Preserve old work orders, decisions, evidence, and failures. Supersede them with new IDs.
- The controller never edits app source, captures specialist evidence, or invents missing facts.

Read [human-review-gates.md](references/human-review-gates.md) before running a phase transition. The approval command is an external Web/human integration endpoint, not a migration-worker action.

## Inputs

- Android project root and clean Git revision.
- Structurally valid APK and SHA-256.
- Included/excluded scope, target HarmonyOS profile, accounts, seed data, networks, permissions, and frozen Android/Harmony environments.
- Real CodeArts task receipts for assigned production work.

Initialize with `scripts/init_migration.py`, complete `controller/scope.json`, then compute Gate 1 with `scripts/validate_gate.py --phase 1 --write`.
 
**Phase 1 环境前置（必做，先于 phase state machine）：** 屏幕 + SDK 一起冻结，`scripts/preflight_env.py --serial emulator-5554 --width 1080 --height 2400 --density 440 --scope controller/scope.json`。要求：
- 屏幕（screen parity）：Android 模拟器在线且 `wm size/density` 固定为 WxH/dpi（P2 运行时证据以它为准）；离线/不符 → 先解决环境，P1 不放行。Harmony 模拟器（hdc serial）在线且同参数（P4 H4ENV 与截图对比基准）；离线 → 明确记录「Harmony 模拟器不可用」进 scope（P4 的 parity 将 DEFERRED，不得虚构）。
- SDK/工具链（SDK find）：扫描并锁定 Android（ANDROID_HOME/adb/emulator/java）+ Harmony（hdc/DevEcoStudio/node/ohpm/hvigor）路径与版本，全量写入 scope.json `sdk_toolchain` 块；缺失项输出 `[WARN] missing xxx` 先行暴露（P3/P4 构建门禁会据此硬卡，补齐后重跑）。
- 冻结值写入 scope.json（`screen_resolution/screen_density/serial` + `sdk_toolchain`），P2 gmi_runtime（`--screen-size/--screen-density`）、P3/P4 构建与 H4ENV 直接复用，全程不改基准。

**gmi 模式 Gate 2/3/4 判定规则**（gmi 流程下 controller 机器复核）：
- 判别：`phase-02-android-inventory/gmi/phase-2-closure.json` 存在（或 phase-manifest `generator: gmi`）即为 gmi run；`validate_gate.py` 自动走等价门禁（P2: closure PASS + UNMAPPED=0；P3: + stage-03-gate-report PASS），legacy 证据链校验对 gmi run 不再适用。
- 工单签发：gmi run 的 P3/P4 工单可手工签发（照 `issue_phase3/4_work_order.py` 的结构），**work_order_id 一律大写**（后缀取 scope_sha256 前 12 位大写，如 `WO-PHASE-04-F2CF8BB31CF6`），必须符合 `^[A-Z0-9][A-Z0-9._-]{2,95}$`；签完后 registry 记录 `work_order_sha256` 并追加 decision-log。
- adapter 防覆盖：`gmi_phase3_adapter.py --out` 指向**已有 controller 的 run** 时不会写 controller/scope.json、run-manifest.json、registries（只产出 phase-02 正式布局）——绝不可因错误 `--out` 覆盖 run 身份；若已误覆盖，从 `phase-02-android-inventory/controller-scope.snapshot.json` 恢复 scope.json（并按决策日志留痕）。
- 中文路径约束：hvigor 拒绝非 ASCII 工作区路径（00306003 Invalid project path）。若 run 位于中文路径，构建/验证改在 ASCII 载体（如 `/tmp/...`）执行，结果 seal 回正式 run 并在决策日志记录载体差异；原则上 run 路径应全程 ASCII。

## Phase state machine

For each phase:

1. Recompute the canonical machine Gate.
2. On failure, record `AUTO_GATE_FAIL` or `BLOCKED` and route the exact defect.
3. On machine `PASS`, build `review-summary.json`; status becomes `WAITING_HUMAN_REVIEW`.
4. A person chooses `APPROVED`, `REWORK`, `APPROVED_DEVIATION`, or `MANUAL_TAKEOVER`.
5. Only the two approval decisions may authorize the next work order. A rewritten Gate invalidates the old decision.

Phase 2 stays automatic internally; the human checkpoint is after its machine closure, never manual page enumeration.

## Specialist routing

- Phase 1 freezes scope and baselines.
- Phase 2: issue with `scripts/issue_phase2_work_order.py`, then invoke `$android-migration-inventory`. Anchor every sealed Android evidence package with `scripts/anchor_phase2_evidence.py`.
- Phase 3: after approved Gate 2, issue with `scripts/issue_phase3_work_order.py`, then invoke `$harmonyos-migration-scaffold` using its bundled ArkUI Stage template.
- Phase 4: after approved Gate 3, issue with `scripts/issue_phase4_work_order.py`, then invoke `$harmonyos-feature-implementation`. Work is page-owned; shared capabilities use separate orders.

Actor IDs are assignments, not proof. Use `scripts/record_team_execution.py` to bind each real CodeArts task ID, work-order hash, actor, terminal state, and produced artifact hashes. Reused, fabricated, missing, or stale receipts are blocking. Logical specialist roles may share a model service, but a producer cannot perform the external human approval.

## Failure routing

- Missing or contradictory Android fact, page, state, transition, or side effect: return to Phase 2.
- Wrong Harmony module, route, carrier, public shell, contract, or asset landing: return to Phase 3.
- Correct upstream contract but wrong ArkUI implementation or evidence: repair in Phase 4.
- After one initial attempt and two automatic repairs for a unit, enter `MANUAL_TAKEOVER`; do not reset the counter by deleting evidence.

Never translate a failure into `PASS_WITH_GAPS`, `PARTIAL`, or prose completion.

## Outputs and delivery

Each phase produces a canonical Gate report, exception-first review summary, sealed human decision, immutable work order, rework records, and evidence hashes. Run `scripts/audit_delivery.py --through-phase 4` only after approved Gate 4. Delivery is valid only when the audit exits zero; the reported machine verdict and human approval remain separate facts.

## Reference map

- [controller-contract.md](references/controller-contract.md): scope and run layout.
- [phase-gates.md](references/phase-gates.md): deterministic Gate requirements.
- [phase-2-handoff.md](references/phase-2-handoff.md), [phase-3-handoff.md](references/phase-3-handoff.md), [phase-4-handoff.md](references/phase-4-handoff.md): specialist inputs and outputs.
- [human-review-gates.md](references/human-review-gates.md): review UI, decisions, trust boundary, and pause behavior.
- [governed-execution-contract.md](references/governed-execution-contract.md): package and report integrity.
