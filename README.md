# Android 到 HarmonyOS 迁移 Skill 套件

这套仓库用于治理 Android 到 HarmonyOS NEXT 的迁移过程。四个 Skill 必须整包使用：它们通过冻结输入、阶段工单、证据链和独立门禁连接，不能把其中一个单独当作“自动迁移器”。

## 阶段与职责

| 阶段 | Skill | 主要产出 |
|---|---|---|
| Phase 1 | `android-harmony-migration-controller` | 冻结范围、源码版本、APK、环境、角色和阶段计划 |
| Phase 2 | `android-migration-inventory` | 静态页面语义、运行时状态、组件、事件、跳转、副作用和证据链 |
| Phase 3 | `harmonyos-migration-scaffold` | 可构建、可安装、可启动的 ArkUI Stage 基座及真实路由/承载体 |
| Phase 4 | `harmonyos-feature-implementation` | 按状态实现业务与 UI，执行验证、差异判定、返修和一致性验收 |

总控 Skill 贯穿四个阶段，但不编写业务代码。Phase 2 只负责理解 Android；Phase 3 只建立非业务基座；Phase 4 才允许迁移真实功能。

## 一次指令执行 Phase 1–4

把四个 Skill 安装到项目后，在 CodeArts 中只需发送一次：

```text
使用 $android-harmony-migration-controller 对当前 Android 项目连续执行 Phase 1–4。
自动发现可确定的项目、APK 和环境信息；Phase 3 使用 Skill 内置 ArkUI 模板。
Gate 通过后直接进入下一阶段，不要等待我回复继续；可修复问题自动返工并复验。
只有外部凭据、工具链或模拟器确实缺失时才暂停，并一次列全阻塞项。
```

Phase 3 已内置完整 `assets/arkui-stage-template`，不需要另外填写模板路径。

## Skill OS 治理层

四个 Skill 均按 YAO Skill OS 2.0 的 Governed 模式维护。每个目录包含 `manifest.json`、`agents/interface.yaml`、`reports/skill-ir.json`、触发/输出评测、权限契约、信任报告和 Review Studio 摘要。它们用于约束触发边界、角色权限、回滚范围和完成声明，不替代迁移运行本身的 APK、CLI、模拟器、截图、Inspector、断言或阶段 Gate 证据。

评测中的输出文本是 `file-backed fixture`，只能证明已提交规约能拦截对应回归；真实模型执行、盲审结论、CodeArts 身份认证和真机迁移效果若未采集，统一保留为 `missing evidence`，不得据此宣布应用迁移成功。

## 关键约束

- Phase 2 的页面、组件、事件和跳转必须同时经过静态扫描与运行证据绑定。确定性门禁计算覆盖率，模型不能自行宣布 `PASS`。
- Phase 3 必须保持 Android 的页面承载语义。页面不能擅自改成 Dialog、Sheet 或其他交互载体。
- Phase 4 的迁移单元固定为“功能 × 页面 × 状态 × 环境”。页面全集与状态专属对象分别记录，既防漏项，也避免把错误态控件强塞进默认态。
- 事件和跳转必须附带自动化操作轨迹及前后快照，不能只提交一组“已观察 ID”。
- Android 与 Harmony 的几何位置会按各自冻结分辨率归一化比较；超过容差或视觉语义不同，Gate 4 直接失败。
- Phase 4 的组件树必须来自 ArkUI `UIContext` Inspector。门禁从原始树重算节点、位置、哈希和 Android 组件绑定，不接受模型手写的 `nodes` JSON。
- 每个迁移单元最多执行一次初始验证和两次自动返修。执行前会写入控制器侧哈希链，删除本地失败文件不能重置次数。
- 测试目录中的假 CLI 只用于离线测试脚本逻辑，不能作为真实 DevEco、Hvigor、HDC 或模拟器证据。

## 安装

把 `.codeartsdoer/skills/` 下四个目录整体复制到目标项目的 `.codeartsdoer/skills/`：

```bash
git clone https://github.com/Ra1nyDayyy/android-harmony-skills.git
cp -r android-harmony-skills/.codeartsdoer/skills/* /path/to/project/.codeartsdoer/skills/
```

## 强制分工与最终审计

工单里的角色 ID 只代表分配，不能证明多个 Agent 真实执行。Phase 2、Phase 3、Phase 4 以及每个 Phase 4 功能工单的角色都必须对应独立的 CodeArts 任务，并通过 `record_team_execution.py`登记平台任务 ID 和产物哈希。阶段2回执不完整时不能签发阶段3，阶段3回执不完整时不能签发阶段4。

四阶段完成后必须运行：

```bash
python3 .codeartsdoer/skills/android-harmony-migration-controller/scripts/audit_delivery.py \
  --run-dir <migration-run> --through-phase 4
```

只有命令退出码为0且输出 `verdict: PASS` 才能声明交付完成。`PASS_WITH_GAPS`、`PARTIAL`、`PENDING`、占位业务代码、替代门禁报告或“后续 Agent 再注入”都会被判定为失败。

仓库当前提供可执行的 Phase 1–4 专项流程。后续全应用回归和交付阶段尚缺独立执行 Skill 与真实设备集成测试，因此不能把现有代码描述成完整的 Phase 1–6 自动闭环。
