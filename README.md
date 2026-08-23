# Android → HarmonyOS 迁移技能集（四件套）

一套覆盖安卓到鸿蒙（HarmonyOS NEXT）**完整迁移生命周期**的 AI Agent 技能包。

> ⚠️ **本仓库中的 4 个 skill 是一个整体，需打包一起使用，不建议单独拆用。**
> 它们对应同一条迁移流水线的不同阶段，彼此之间通过冻结基线、工单下发和阶段门禁（Gate）衔接——上游 skill 的产出物是下游 skill 的强制输入，缺了任何一环，流水线都无法闭环。

## 四个角色，一条流水线

| # | Skill | 角色 | 在流水线中的位置 |
|---|-------|------|------|
| 1 | [android-migration-inventory](.codeartsdoer/skills/android-migration-inventory) | 盘点 Agent | **Phase 1**：对现有 Android 应用做迁移级盘点（CLI 运行时捕获、源码映射、状态级记录、环境冻结、不可变证据链），产出冻结盘点清单 |
| 2 | [harmonyos-migration-scaffold](.codeartsdoer/skills/harmonyos-migration-scaffold) | 脚手架 Agent | **Phase 2–3**：以冻结的盘点清单为唯一输入，构建非业务性质的鸿蒙工程脚手架（真实模块与路由落点、接口级能力契约、冻结模拟器 PNG 证据、命令行构建证明），过独立 Phase 3 门禁 |
| 3 | [harmonyos-feature-implementation](.codeartsdoer/skills/harmonyos-feature-implementation) | 功能实现 Agent | **Phase 4+**：在 Phase 1–3 全部通过后，实现真实业务功能（源码级视觉资产、状态级模拟器证据、原生能力适配器、独立一致性验收） |
| 4 | [android-harmony-migration-controller](.codeartsdoer/skills/android-harmony-migration-controller) | 迁移总管 | **全程**：不写一行业务代码，负责冻结范围与基线、下发阶段工单、仲裁冲突、路由返工、执行阶段门禁，是其余三个 skill 的调度中枢 |

## 协作方式

```
┌─────────────────────────────────────────────────────┐
│        android-harmony-migration-controller          │
│   （迁移总管：全程统筹 / 工单 / 门禁 / 返工路由）      │
└───────┬──────────────────┬──────────────────┬────────┘
        ▼ 下发Phase1工单     ▼ 下发Phase2-3工单   ▼ 下发Phase4+工单
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│    Phase 1    │  │   Phase 2–3   │  │   Phase 4+    │
│ android-      │  │ harmonyos-    │  │ harmonyos-    │
│ migration-    │─▶│ migration-    │─▶│ feature-      │
│ inventory     │  │ scaffold      │  │ implementation│
│ （安卓盘点）    │  │ （鸿蒙脚手架）  │  │ （功能实现）    │
└───────────────┘  └───────────────┘  └───────────────┘
     冻结盘点清单 ─▶    可运行脚手架  ─▶   业务功能闭环
```

关键约束（也是必须整套使用的原因）：

- **阶段前置**：脚手架 skill 必须等盘点 Phase 通过后才能启动；功能实现 skill 只能在 Phase 1–3 全部通过后使用。
- **输入契约**：每个 skill 的 `references/input-contract.md` 定义了它认哪些上游产物，格式不对会被脚本校验拦下。
- **门禁与证据链**：每个阶段结束由总管跑 `validate_gate.py` 类校验，凭证据（截图、构建日志、台账）过关，不过关就返工。

## 使用方法（整包安装）

将本仓库的 `.codeartsdoer/skills/` 目录整体复制到你项目的 `.codeartsdoer/` 下即可：

```bash
git clone https://github.com/Ra1nyDayyy/android-harmony-skills.git
cp -r android-harmony-skills/.codeartsdoer/skills/* /path/to/your/project/.codeartsdoer/skills/
```

实际开工时，从「迁移总管」入口发起即可，它会按阶段依次驱动其余三个 skill：

```
你：发起一次安卓转鸿蒙迁移，范围是 xxx 应用
  → 总管接管：冻结范围 → 派盘点 Agent → 派脚手架 Agent → 派功能实现 Agent → 逐阶段过门禁
```

## 目录结构

```
.codeartsdoer/skills/
├── android-harmony-migration-controller/   # 迁移总管（入口）
├── android-migration-inventory/            # 盘点 Agent
├── harmonyos-migration-scaffold/           # 脚手架 Agent
└── harmonyos-feature-implementation/       # 功能实现 Agent

每个 skill 内含：
├── SKILL.md          # 主指令（触发条件、工作流、边界）
├── references/       # 角色权限、输入契约、阶段交接、验收细则
├── scripts/          # 可执行校验脚本（含 tests/ 自测）
└── assets/           # 工单、台账、模板文件
```
