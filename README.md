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

## 关键约束

- Phase 2 的页面、组件、事件和跳转必须同时经过静态扫描与运行证据绑定。确定性门禁计算覆盖率，模型不能自行宣布 `PASS`。
- Phase 3 必须保持 Android 的页面承载语义。页面不能擅自改成 Dialog、Sheet 或其他交互载体。
- Phase 4 的迁移单元固定为“功能 × 页面 × 状态 × 环境”。页面全集与状态专属对象分别记录，既防漏项，也避免把错误态控件强塞进默认态。
- 事件和跳转必须附带自动化操作轨迹及前后快照，不能只提交一组“已观察 ID”。
- Android 与 Harmony 的几何位置会按各自冻结分辨率归一化比较；超过容差或视觉语义不同，Gate 4 直接失败。
- 每个迁移单元最多执行一次初始验证和两次自动返修。执行前会写入控制器侧哈希链，删除本地失败文件不能重置次数。
- 测试目录中的假 CLI 只用于离线测试脚本逻辑，不能作为真实 DevEco、Hvigor、HDC 或模拟器证据。

## 安装

把 `.codeartsdoer/skills/` 下四个目录整体复制到目标项目的 `.codeartsdoer/skills/`：

```bash
git clone https://github.com/Ra1nyDayyy/android-harmony-skills.git
cp -r android-harmony-skills/.codeartsdoer/skills/* /path/to/project/.codeartsdoer/skills/
```

仓库当前提供可执行的 Phase 1–4 专项流程。后续全应用回归和交付阶段尚缺独立执行 Skill 与真实设备集成测试，因此不能把现有代码描述成完整的 Phase 1–6 自动闭环。
