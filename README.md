# Android → HarmonyOS 迁移工作流 Skills

这套仓库不是“一键把任何 Android 项目自动翻译成鸿蒙”的代码转换器，而是一套可审计的迁移工作流：把已经探索过的人工迁移过程固化为“理解 → 基座 → 实现 → 验证 → 返工”，并在每个阶段保留人工审核点。

## 四个阶段

| 阶段 | Skill | 主要产物 |
|---|---|---|
| Phase 1 | `android-harmony-migration-controller` | 冻结范围、源码/APK、环境、工单、机器 Gate 和人工审核记录 |
| Phase 2 | `android-migration-inventory` | Android 页面、组件、状态、功能、跳转、副作用、异常场景和证据链 |
| Phase 3 | `harmonyos-migration-scaffold` | 可构建、安装、启动的 ArkUI Stage 基座及页面/载体落点 |
| Phase 4 | `harmonyos-feature-implementation` | 按 Page-ID 实现 UI 和功能，运行一致性比较、有限返修和验收 |

四个 Skill 需要整包使用。控制器只负责治理，不替代专业 Skill 分析页面，也不直接编写业务代码。

## 工作方式

用户发一次完整任务即可，但系统不会连续越过审核点：

```text
使用 $android-harmony-migration-controller 迁移当前 Android 项目。
在同一个任务中执行 Phase 1-4；每个阶段先完成机器验证，再生成简明审核包并停在 WAITING_HUMAN_REVIEW。
我批准后再进入下一阶段；失败项按来源自动退回，单个页面最多自动修复两次。
Phase 3 使用 Skill 内置 ArkUI Stage 模板。Phase 4 必须以 Phase 2 冻结的页面合同为唯一 UI 和功能依据，一页一个负责人，共享能力另行分工。
```

每阶段的人工默认只看核心覆盖率、红黄异常、关键页面样本和证据链接，不需要逐行阅读所有 CSV、JSON 和日志。原始材料仍可展开核查。

## 防止“没做完却说完成”

- 模型无权批准、接受偏差或声明阶段完成。
- 下一阶段工单必须同时满足：当前机器 Gate 通过，且存在绑定该 Gate SHA-256 的人工批准。
- Gate 被重新计算后，旧批准自动失效。
- `REWORK` 和 `MANUAL_TAKEOVER` 不能签发下一阶段工单。
- Phase 4 的模型文字 `MATCH` 没有权威；一致性必须由 Android/Harmony 证据的机器比较产生。
- 构建成功只说明能编译，不能代替页面、功能、跳转、副作用和视觉一致性验证。

## 核心边界

- Phase 2 内部保持自动化，不要求人工枚举页面。扫描跳过、XML 解析错误、未知 UI 面和未触发场景必须显式进入阻断或运行任务，不能静默消失。
- Phase 3 只建基座，不提前填业务。页面、Dialog、Sheet 等载体必须与 Android 语义一致。
- Phase 4 一 Page-ID 一工单、一负责人；共享计算、网络、存储、剪贴板等能力使用独立工单和代码边界。
- 每个页面/能力允许一次初始验证和最多两次自动修复，之后转人工处理。
- 本地哈希封存证明记录未被修改或过期，但不能证明操作者身份。正式 Web 系统必须登录鉴权，并由服务端持有人工审批入口，不能把它暴露给迁移 Agent。

## 安装

把仓库中 `.codeartsdoer/skills/` 下的四个目录整体复制到目标项目的 `.codeartsdoer/skills/`：

```bash
git clone https://github.com/Ra1nyDayyy/android-harmony-skills.git
cp -r android-harmony-skills/.codeartsdoer/skills/* /path/to/project/.codeartsdoer/skills/
```

Phase 3 已包含 `assets/arkui-stage-template`，无需另行提供模板路径。真实迁移仍需要 Android CLI、DevEco/Hvigor/HDC、HarmonyOS SDK、可用模拟器以及项目所需账号和签名环境。

## 当前能力边界

仓库当前覆盖 Phase 1-4 的治理与专用流程。它能显著降低遗漏、假完成和无证据修改，但不能保证静态扫描发现反射、服务端下发、WebView 内部和特殊账号下的全部内容，也不能保证模型第一次就正确实现所有 ArkUI 代码。这些剩余风险必须通过运行时任务、机器差异、有限返修和人在回路共同控制。

最终交付前运行：

```bash
python3 .codeartsdoer/skills/android-harmony-migration-controller/scripts/audit_delivery.py \
  --run-dir <migration-run> --through-phase 4
```

命令退出为零只代表机器审计通过；同时还必须存在当前 Gate 4 的有效人工批准，系统才可标记 Phase 4 已接受。
