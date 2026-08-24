# CodeArts 可复现的 Android → HarmonyOS 四阶段工作流

本流程不依赖 Web 管理页面。一次 CodeArts 任务保存全流程上下文，但每个 Phase 的机器 Gate 后必须暂停，由操作者在终端完成审核记录，再让原任务继续。

这里的“一次启动”是指启动同一个 Controller 状态机，不是声称 Spec 模式能凭一条提示词创建多个智能体。若 CodeArts 当前入口支持团队任务分派，由 Controller 按工单分派；若只支持 Spec 单任务，则总任务负责冻结合同和验收，操作者按它生成的工单另建 CodeArts 任务。两种模式消费相同文件、写相同回执、通过相同 Gate，不允许让一个 Spec 任务冒充多个独立执行者。

## 1. 开工前准备

必须准备：

- 可正常构建的 Android Git 项目，工作树干净；
- 与源码对应、可安装运行的 APK；
- Android CLI、Android 模拟器或设备；
- DevEco/HarmonyOS SDK、Hvigor/HDC、可用鸿蒙模拟器；
- 测试账号、种子数据、权限、网络场景和签名环境；
- 本仓库四个 Skill 已整体复制到目标项目 `.codeartsdoer/skills/`。

Phase 3 固定使用 `harmonyos-migration-scaffold/assets/arkui-stage-template`，不要再提供另一套鸿蒙模板。

### 首次建立迁移 Run

在 CodeArts 任务终端执行（Windows 环境把 `python3` 换成 `python`）：

```bash
python3 .codeartsdoer/skills/android-harmony-migration-controller/scripts/init_migration.py \
  --output <保存迁移Run的父目录> \
  --project-root <Android项目目录> \
  --project-name <项目名>
```

需要固定 Run-ID 时再追加 `--run-id <未使用的Run-ID>`。按真实环境补全 `<migration-run>/controller/scope.json`，不要保留模板占位值。Gate 1 `PASS` 后不要再修改它；Gate 1 失败时应先修正事实，再重新计算。然后首次写入 Gate 1：

```bash
python3 .codeartsdoer/skills/android-harmony-migration-controller/scripts/validate_gate.py \
  --run-dir <migration-run> --phase 1 --write
```

命令退出码非零或报告 `FAIL` 时先修复，不得生成批准记录。

## 2. 唯一启动提示词

在 Android 项目的 CodeArts 任务中发送：

```text
使用 $android-harmony-migration-controller 对当前 Android 项目执行完整 Phase 1-4 迁移。

要求：
1. 在同一个任务中保留 Run-ID、工单、证据和返工上下文，但每个 Phase 的机器 Gate 后必须生成 review-summary.json，并停在 WAITING_HUMAN_REVIEW；没有我在终端生成的当前人工审批记录，不得签发下一阶段工单。
2. Phase 2 内部全自动，不要求我枚举页面。必须扫描并运行验证所有页面、组件、状态、功能、跳转、动态风险、非 UI 副作用和特殊场景；解析失败、扫描跳过、低置信和未触发任务不得静默消失。
3. Phase 3 只生成可构建、安装、启动的非业务基座，逐 Page-ID 保持 Android 的页面、Dialog、Sheet、Widget 等载体，不得提前简化或实现业务。
4. Phase 4 以 Phase 2 冻结的 JSON、CSV、页面合同、资源和证据为唯一 UI 与功能设计来源。每个 Page-ID 分配一个不跨页复用的 UI理解与转换Agent：先生成并校验 arkts-page-plan.json，再由该 Agent 把该页转换为 ArkTS。共享计算、存储、网络、剪贴板、后台任务和权限能力使用独立 SHARED_CAPABILITY_WORK_ORDER。
5. Phase 4 禁止使用 UI Inspector、Inspector Bridge 或 getFilteredInspectorTree。验证只使用最终 HAP、HarmonyOS UiTest 自动交互与组件查询、模拟器截图、功能/跳转断言和副作用证据。
6. 模型不得写 MATCH、批准偏差或宣布完成。机器差异失败后自动诊断修复；每个页面或能力只有一次初始验证和最多两次自动修复，之后转 MANUAL_TAKEOVER。
7. 每次暂停时只向我展示：覆盖率、红黄异常、关键页面样本、证据链接、建议动作，以及我需要执行的准确审核命令。不要把全部原始 CSV/JSON 直接堆给我。
```

## 3. 每个阶段统一的人工审核动作

CodeArts 报告机器 Gate 已生成后，先生成或刷新简明审核包：

每个阶段的专属 Skill 完成并封存产物后，Controller 必须先把该阶段的当前机器 Gate 写到唯一规范路径。Phase 1 已在初始化步骤完成；Phase 2-4 使用同一命令，只替换阶段号：

```bash
python3 .codeartsdoer/skills/android-harmony-migration-controller/scripts/validate_gate.py \
  --run-dir <migration-run> --phase <2|3|4> --write
```

确认退出码为零且 `<migration-run>/controller/gate-report.json` 中的 `phase` 与当前阶段一致，再生成摘要。`--input` 是可选补充；省略时摘要会直接从当前 Gate 和已有阶段报告生成，补充文件也不能隐藏机器异常：

```bash
python3 .codeartsdoer/skills/android-harmony-migration-controller/scripts/generate_review_summary.py \
  --run-dir <migration-run> \
  --phase <1|2|3|4> \
  --gate-report <migration-run>/controller/gate-report.json
```

打开：

```text
<migration-run>/controller/review-summaries/phase-0N/review-summary.json
```

只有机器 Gate 为 `PASS`、摘要状态为 `WAITING_HUMAN_REVIEW` 且关键异常已经看过，才可记录 `APPROVED` 或 `APPROVED_DEVIATION`：

```bash
python3 .codeartsdoer/skills/android-harmony-migration-controller/scripts/record_human_review.py \
  --run-dir <migration-run> \
  --phase <1|2|3|4> \
  --gate-report <migration-run>/controller/gate-report.json \
  --review-id HREV-P<阶段>-<日期时间> \
  --reviewer <你的姓名或比赛账号> \
  --decision APPROVED \
  --reason "已核对摘要、关键样本和红黄异常"
```

机器 Gate 失败或人工不接受时可记录：

- `REWORK`：退回当前阶段修复；
- `APPROVED_DEVIATION --deviation "差异ID: 原因"`：只批准明确、可追踪的偏差，不会删除机器差异；
- `MANUAL_TAKEOVER`：停止自动修复，转人工修改。

审批后回到同一个 CodeArts 任务发送：

```text
我已完成当前 Phase 的人工审核并生成审批记录。请校验审批与当前 Gate 的哈希绑定；有效则继续下一阶段，无效则只报告准确原因，不要重建或伪造审批。
```

Gate 一旦重新写入，旧审批自动过期，需要重新生成摘要和审批。

审批完成后不要再次运行带 `--write` 的 Gate 命令。下一阶段工单签发器会执行一次**不写文件的只读重算**；重算失败会拒绝签发，但不会仅因 `checked_at` 改变而让刚完成的审批过期。签发器随后校验审批是否仍绑定当前 `controller/gate-report.json`。

Controller 实际签发命令如下。`--issued-by` 必须等于 `scope.json` 中冻结的 `migration_controller_id`；其余角色 ID 必须来自真实的 CodeArts 任务分派，并在首次签发工单时冻结，不能用临时字符串冒充：

```bash
# Gate 1 人工批准后
python3 .codeartsdoer/skills/android-harmony-migration-controller/scripts/issue_phase2_work_order.py \
  --run-dir <migration-run> --issued-by <migration_controller_id>

# Gate 2 人工批准后
python3 .codeartsdoer/skills/android-harmony-migration-controller/scripts/issue_phase3_work_order.py \
  --run-dir <migration-run> --issued-by <migration_controller_id> \
  --architecture-lead-id <ID> --toolchain-agent-id <ID> \
  --navigation-agent-id <ID> --public-ui-agent-id <ID> \
  --capability-contract-agent-id <ID> --architecture-acceptance-agent-id <ID>

# Gate 3 人工批准后
python3 .codeartsdoer/skills/android-harmony-migration-controller/scripts/issue_phase4_work_order.py \
  --run-dir <migration-run> --issued-by <migration_controller_id> \
  --implementation-lead-id <ID> --visual-asset-agent-id <ID> \
  --verification-executor-id <ID> --parity-acceptance-agent-id <ID>
```

每个被真实分派的 CodeArts 工作任务结束后，都要按其工单角色记录一次不可覆盖的执行回执。一个平台任务 ID 不能冒充多个角色：

```bash
python3 .codeartsdoer/skills/android-harmony-migration-controller/scripts/record_team_execution.py \
  --run-dir <migration-run> --work-order <Run内工单JSON相对路径> \
  --role-key <工单角色字段> --actor-id <冻结Actor-ID> \
  --platform-task-id <真实CodeArts任务ID> \
  --started-at <ISO-8601时间> --ended-at <ISO-8601时间> \
  --terminal-task-state SUCCEEDED \
  --artifact <Run内真实产物相对路径>
```

### Spec 模式的页面任务提示词

当 CodeArts 不能自动创建子 Agent 时，Phase 4 每个 `PAGE_WORK_ORDER` 单独新建一个 CodeArts 任务，并发送下面这段话。不要把多个页面合并进同一任务：

```text
使用 $harmonyos-feature-implementation 执行这个 PAGE_WORK_ORDER：<Run内PAGE_WORK_ORDER相对路径>。
只处理工单绑定的 Page-ID 和允许写入的文件。Phase 2 冻结的页面合同是 UI、状态、功能、跳转和副作用的唯一来源；先生成并校验 arkts-page-plan.json，再转换为 ArkTS。不得删减组件、状态或事件，不得改变 PAGE/DIALOG/SHEET/WIDGET 载体，不得跨页修改共享能力。完成后返回真实 CodeArts task ID、开始/结束时间、终态和产物路径；不要自行写 PASS、MATCH 或人工批准。
```

共享能力使用同样方式，但把工单换成 `SHARED_CAPABILITY_WORK_ORDER`，并明确禁止修改页面所有者的 ArkTS 路径。Controller 收齐全部页面与共享能力回执后才能进入 Phase 4 验证。

Phase 2 的每个已封存证据包还必须由 Controller 写入证据锚点；遗漏锚点会阻止 Gate 2 或最终审计：

```bash
python3 .codeartsdoer/skills/android-harmony-migration-controller/scripts/anchor_phase2_evidence.py \
  --run-dir <migration-run> --evidence-id <已封存Evidence-ID> \
  --anchored-by <migration_controller_id>
```

## 4. 四阶段验收重点

### Phase 1

检查源码 commit、APK 哈希、范围、账号/数据、Android 与鸿蒙环境是否唯一且可用。任何基线不确定都应退回，不要带着模糊输入开工。

### Phase 2

审核页面地图和覆盖率，而不是逐行看源码。重点看：未绑定页面/状态、低置信组件、未触发跳转、反射/动态加载/WebView/服务端下发、数据库/网络/剪贴板/后台任务，以及特殊账号、权限拒绝、异常数据和极端状态。

### Phase 3

检查真实构建、安装、启动证据；逐 Page-ID 核对路由或非路由载体落点；确认没有把页面改成 Dialog/Sheet，也没有提前填入假业务。

### Phase 4

先核对每个页面是否都有独立 `PAGE_WORK_ORDER`、UI 理解与转换 Agent、`arkts-page-plan.json` 和 ArkTS 落点。然后检查最终 HAP 上的 UiTest 交互、组件查询、截图、功能输出、跳转、返回路径和副作用差异。页面合同中的组件、状态、事件、跳转、资产或能力少一项，都不能通过。

## 5. 最终交付

Phase 4 人工批准后运行：

```bash
python3 .codeartsdoer/skills/android-harmony-migration-controller/scripts/audit_delivery.py \
  --run-dir <migration-run> --through-phase 4
```

交付包至少包含：迁移后的 HarmonyOS 项目、最终 HAP 和哈希、四个机器 Gate、四个人工审核记录、Phase 2 页面/状态/功能清单、Phase 4 页面计划与工单、UiTest/截图/功能/副作用证据、机器差异、返工记录和仍需人工处理的限制。

最终审计会只读重算 Gate 1-4，并检查：Gate 1-3 工单中保存的历史 Gate 快照及其人工批准、当前 Gate 4 的人工批准、每阶段恰好一个有效 Controller 工单、真实 CodeArts 执行回执、任务台账 `PASS`、规范产物、哈希和封存状态。它不会重写 Gate，因此不会主动使审批过期。

只有最终审计退出码为零，且四个 Gate 都存在可追踪的有效人工批准，才能标记 Phase 4 已接受。不要使用 `PASS_WITH_GAPS`、`PARTIAL` 或“后续再补”替代真实结果。

## 6. 无网站模式的信任边界

当前流程用 SHA-256 检测审批记录是否被修改或过期，但本地文件本身不能证明操作者身份。暂不做网站时，`record_human_review.py` 必须由你在人工控制的终端执行，迁移 Agent 只能提示命令，不能代替你调用。比赛演示时保留终端操作记录；以后接入 Web/SSO 后，再由服务端持有审批入口和身份审计。
