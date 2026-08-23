# Phase 1–4 连续执行契约

当用户要求“完整迁移”“执行整个工作流”或“一次跑完 Phase 1–4”时，进入连续模式。一次请求即授权总控在用户指定的 Android 工程和迁移输出目录内完成四个阶段；不要在阶段边界询问是否继续。

## 自动准备

优先使用用户给出的路径和环境。缺省值按下面的顺序确定：

1. Android 工程：当前工作区中唯一包含 Gradle Android 工程标志的 Git 根目录。
2. 项目名、application ID、版本、构建变体和源码提交：从 Gradle、Manifest、Git 和 APK 中读取。
3. APK：使用用户指定文件；否则使用与 application ID 匹配的最新本地构建产物；仍不存在时，在有 Gradle Wrapper 和可用 SDK 的情况下构建 debug APK。
4. 迁移输出：使用 Android Git 根目录之外的同级 `android-harmony-migration-output`，不得污染冻结源码。
5. 功能范围：让 Android 盘点 Skill 先做只读预发现，再把发现到的全部应用功能冻结为 included scope；不要凭名称猜测，也不要擅自缩小范围。
6. 角色：按 Run-ID 生成稳定且互不重复的角色 ID，并把真实执行记录写入对应台账。不要为了通过门禁复用同一 ID。
7. ArkUI 工程：只使用 `$harmonyos-migration-scaffold/assets/arkui-stage-template`。不再要求用户提供模板路径。
8. Android 与 HarmonyOS 环境：从已连接 CLI 设备和项目工具链读取并冻结。不得用测试目录中的假 CLI 代替。

账号、验证码、私有服务权限、签名材料或不存在的模拟器无法安全推断。只有这些事实确实阻断当前门禁时才向用户提问，并一次列全缺失项。可自动修复的构建、代码、映射、证据或一致性问题不属于提问理由。

## 连续状态机

严格执行下面的状态机，同一任务内自动衔接：

1. 初始化迁移目录，完成冻结范围和环境，写入并通过 Gate 1。
2. 立即签发 Phase 2 工单，调用 `$android-migration-inventory` 完成静态发现、运行遍历、证据绑定和确定性验收；锚定每份证据，通过 Gate 2。
3. 立即签发 Phase 3 工单，调用 `$harmonyos-migration-scaffold` 从内置模板创建基座，完成构建、安装、启动、路由/承载体验证，通过 Gate 3。
4. 立即签发 Phase 4 工单，调用 `$harmonyos-feature-implementation` 按迁移单元实现 UI、功能和副作用，执行构建、模拟器、ArkUI Inspector、功能与视觉一致性验证，通过 Gate 4。
5. 输出一次最终报告，列出四个 Gate、迁移工程、构建产物、证据、已关闭返工和仍存在的外部限制。

每个 Gate 都是强制边界。Gate 失败时读取机器报告，按归属自动开返工、修复并复验；Phase 4 每个迁移单元不得超过一次初始验证加两次自动修复。不能通过修改报告、伪造 `PASS`、减少 scope、跳过状态或更换 UI 承载体继续执行。

## 允许暂停的条件

仅在以下情况下暂停整个连续任务：

- 缺少无法自动生成的凭据、特殊账号、验证码或私有服务访问权；
- Android/HarmonyOS SDK、DevEco/Hvigor/HDC 或所需模拟器不存在且当前环境不允许安装或启动；
- 源码、APK、应用 ID 或冻结环境相互矛盾，无法确定唯一基线；
- 自动修复预算已耗尽；
- 下一步需要超出用户原始迁移范围的外部写入或发布操作。

暂停时保留现有工件，报告当前 Gate、失败证据、已尝试动作和继续所需的最小输入。不要要求用户重新开始整个流程。
