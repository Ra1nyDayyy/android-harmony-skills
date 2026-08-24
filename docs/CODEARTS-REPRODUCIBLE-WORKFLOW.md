# CodeArts 可复现的 Android → HarmonyOS 四阶段工作流

本流程不依赖 Web 管理页面。一次 CodeArts 任务保存全流程上下文，但每个 Phase 的机器 Gate 后必须暂停，由操作者在终端完成审核记录，再让原任务继续。

## 1. 开工前准备

必须准备：

- 可正常构建的 Android Git 项目，工作树干净；
- 与源码对应、可安装运行的 APK；
- Android CLI、Android 模拟器或设备；
- DevEco/HarmonyOS SDK、Hvigor/HDC、可用鸿蒙模拟器；
- 测试账号、种子数据、权限、网络场景和签名环境；
- 本仓库四个 Skill 已整体复制到目标项目 `.codeartsdoer/skills/`。

Phase 3 固定使用 `harmonyos-migration-scaffold/assets/arkui-stage-template`，不要再提供另一套鸿蒙模板。

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

只在机器 Gate 为 `PASS`、摘要状态为 `WAITING_HUMAN_REVIEW` 且关键异常已经看过后，记录决定：

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

其余决定：

- `REWORK`：退回当前阶段修复；
- `APPROVED_DEVIATION --deviation "差异ID: 原因"`：只批准明确、可追踪的偏差，不会删除机器差异；
- `MANUAL_TAKEOVER`：停止自动修复，转人工修改。

审批后回到同一个 CodeArts 任务发送：

```text
我已完成当前 Phase 的人工审核并生成审批记录。请校验审批与当前 Gate 的哈希绑定；有效则继续下一阶段，无效则只报告准确原因，不要重建或伪造审批。
```

Gate 一旦重新写入，旧审批自动过期，需要重新生成摘要和审批。

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

只有最终审计退出码为零，且当前 Gate 4 存在有效人工批准，才能标记 Phase 4 已接受。不要使用 `PASS_WITH_GAPS`、`PARTIAL` 或“后续再补”替代真实结果。

## 6. 无网站模式的信任边界

当前流程用 SHA-256 检测审批记录是否被修改或过期，但本地文件本身不能证明操作者身份。暂不做网站时，`record_human_review.py` 必须由你在人工控制的终端执行，迁移 Agent 只能提示命令，不能代替你调用。比赛演示时保留终端操作记录；以后接入 Web/SSO 后，再由服务端持有审批入口和身份审计。
