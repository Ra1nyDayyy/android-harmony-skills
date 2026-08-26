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
- 判别：`phase-02-android-inventory/gmi/phase-2-closure.json` 存在（或 phase-manifest `generator: gmi`）即为 gmi run。Gate 2 重算 13 表、closure、UNMAPPED 与 audit；Gate 3 叠加架构映射和 stage-03 seal；Gate 4 叠加全页合同、行为绑定、非空组件和 stage-04 seal。不得用 Gate 3 代替 Gate 4。CodeArts 模式下 Gate 4 还必须独立重算全页 `ACCEPTED` 实现、ArkTS 代码路径、非占位页、parity、封存模拟器/UiTest 证据、验收记录、截图唯一性和完整 closure manifest，禁止只信 PASS 报告。
- 工单签发：只用 `issue_phase3_work_order.py` / `issue_phase4_work_order.py`；它们会重算 gmi Gate 并生成大写 ID。禁止手工改 gate-report 或手工组工单。
- adapter 防覆盖：`gmi_phase3_adapter.py --out` 指向已有 run 时保留 controller 身份与真实 static-analysis，将旧 Page-ID 对齐到 gmi Page-ID；缺组件时只可从已验收 UI 树或 page-fields 确定性合成。
- 路径约束：hvigor 拒绝非 ASCII 工作区（00306003）；`preflight_env.py` 必须在 P1 直接阻断，禁止中途搬运 seal 或改写路径哈希。

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
