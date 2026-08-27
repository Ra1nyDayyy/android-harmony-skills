# 挑战杯 Android→HarmonyOS 四 Skill 闭环审查与最小化修改规划

> 审查日期：2026-08-28 ｜ 审查对象：`.codeartsdoer/skills/` 下 4 个 Skill
> 审查方式：三线独立取证（耗时/契约链/测试验证）+ 交叉验证；**不采信任何文档中的 PASS/CLOSED/已闭环表述**，全部结论以脚本代码 `文件:行号` 与 `runs/MIG-20260827T162215Z-B6CBE3` 真实产物为准。全程只读，未修改任何 Skill 文件。
> 总目标（用户确认）：Phase 1–4 形成闭环 ｜ 压缩总时长 ｜ 不要求 1:1 复现，但需"大部分一致"。

---

## 0. 执行摘要

- **当前未闭环。** 设计文档层面闭环完整，但工程实证：真实运行只走到 Phase 2 的 10%（331/3432 个运行时任务），P3/P4 从未执行（`runs/` 无对应目录、`arkts/` 交付目录为空）。
- **最早断裂点在 Phase 2 执行层**（不是契约层）：静态段 956 个绑定失败→现场修扫描器→全量重扫 2.5h；运行时段双 lane 实际同绑一台模拟器、队列 8:1 失衡中途重拆、实测 22–41 秒/任务，推算总时长 ≈19 小时。
- **即使跑完 Phase 2，前方还有两个必然断裂点**：① P2 人工验收文件在两个 Skill 间四重错位（无生产者）；② controller 的 gmi 门禁读取从未被写入的 `audit_discrepancy` 字段（Gate 2 恒 FAIL）。
- **时间压缩空间已量化**：落实"REQUIRED 100% 上机 + REVIEW 同页合并 + 队列均衡 + SOURCE_ONLY 分类"后，Phase 2 运行时段可从 ~19h 压到 **约 1.5–2.5h**，且不降低门禁阈值语义。
- 自带测试 206 例中 **7 例失败**，根因是同一条跨 Skill 共享 fixture 与升级后 closure 协议的断裂链——P2→P3 的桥在当前代码版本下从未被测试验证过。

---

## 1. 当前是否真正闭环，以及最早断裂的位置

### 1.1 真实运行证据链（markor，run `MIG-20260827T162215Z-B6CBE3`）

| 证据 | 内容 | 含义 |
|---|---|---|
| `run-manifest.json` | `"status": "IN_PROGRESS"` | 运行未完成 |
| `controller/gate-report.json` | phase 1 verdict=PASS（16:23:47Z） | **P1 是通的** |
| `phase-02.../phase-manifest.json` | `"status": "GENERATED"`，含 `generator:"gmi"`、26 个 gmi 页面、gmi_counts 13 表 | 静态+候选阶段已完成 |
| `SCANNER-PATCH-NOTES.md` | 首次 `validate_static_analysis.py` FAIL（956 个 REQUIRED 任务缺 Page-ID）→ 现场修 `analyze_static_pages.py` 3 处 → 删 `static-analysis/` 全量 recapture（22:17→00:47，约 2.5h） | 静态段曾返工一轮 |
| `runtime-evidence/runtime-queue-a.json/-b.json` + `-a2/-b2` | A=3052 / B=380（8:1 失衡），后又出现 A2=1418 / B2=1419 均衡重拆 | **队列中途重拆、前功部分作废** |
| `runtime-evidence/lane-*/lane-meta.json` | **lane A 与 lane B 的 `device_serial` 均为 `emulator-5554`** | 双 lane 并行实际未生效（两队列串行抢同一台设备） |
| lane 目录时间戳 | 00:59 冻结 → 02:19 最后产出，lane-a 215 项、lane-b 116 项（≈10%） | 实测 22.3s/任务（A）、41.4s（B） |
| `runtime-evidence/` | 无 `runtime-gate.csv`、无 `audit-replay.csv` | `gmi_audit.py` 从未运行到合并 |
| 全 phase-02 目录 | 无 `phase-2-closure.json`（顶层与 `gmi/` 子目录均无） | `gmi_closure.py` 从未闭合 |
| `validate_gate.py --phase 2` 实跑（只读） | **FAIL**：closure missing、gate missing、runtime-gate.csv missing、audit-replay.csv missing 等 | Gate 2 不通 |
| `init_scaffold.py` 判定分支 | `BLOCKED - gmi-gate-incomplete`（缺 gate 输入） | P3 被正确阻断（防御有效，但链断了） |
| `arkts/` | 空目录（仅 .DS_Store） | P4 从未交付 |

### 1.2 判定结论

- **P1→P2 契约链：通**。工单字段、scope 哈希、ownership 深比较逐一对上（`issue_phase2_work_order.py:171-204` ↔ `init_inventory.py:114-144`）。
- **P2 内部：断在执行层**（上述时间/并行/重拆问题），且 `gmi_audit`→`gmi_closure`→人工验收→adapter 这段桥**从未在真实运行中走通过**。
- **P2→P3→P4 契约链：文件名/列名/哈希绑定主链严丝合缝**（13 表名 `gmi_generate.py:875-887` ↔ `validate_gate.py:4979-4987` 逐一对应；P3 closure ↔ P4 `verify_closed_phases` 复验一致），但存在 3 个隐藏断点（见 §2 的 B1/B2/B3），其中 B1/B2 会在 P2 完成后立刻引爆。
- **最早断裂位置（一句话）**：Phase 2 运行时段——16:29 初始化，8.5h 后才冻结队列，双 lane 同绑一台模拟器，完成率 10% 即停滞；就算硬跑完，`audit_discrepancy` 字段错位会让 controller 的 Gate 2 无条件 FAIL。

---

## 2. 最重要的问题及代码依据（按影响排序）

**P0-1｜Phase 2 时间黑洞：全量任务上机 + 失衡 + 固定 sleep**
- 3432 个任务 **SOURCE_ONLY=0**（全上机）：RUNTIME_UI 2494 / RUNTIME_EFFECT 938；REQUIRED 1151 / REVIEW 2281。其中 2281 个 `VERIFY_STATE_BRANCH`（全 REVIEW 档）的 trigger 是 `AUTO_SATISFY_CONDITION`，被 `gmi_runtime.py:733` 排除点击——实际就是"导航 + 两次几乎相同的快照"，66% 的机时花在重复快照上。
- 根因 1：`analyze_static_pages.py:36-45` 的 `TASK_VERIFICATION` 没有任何 `SOURCE_ONLY` 映射，而文档 `static-page-analysis.md:39` 承诺"纯源码可证为 SOURCE_ONLY"——**承诺未实现**。
- 根因 2：`gmi_runtime.py:538-609` 队列分配 journey=page 粒度，exclusive 任务（SCENARIO/SIDE_EFFECT 共 938 个）的 journey 全压 lane A（:561-562）→ A=3052/B=380。
- 根因 3：30 处固定 `time.sleep(0.8–6.0s)`（`gmi_runtime.py:120–1193`），每次 snapshot 8+ 个 adb 子进程调用（:196-237），`wm size/density` 每任务重复查询 6 次（:222-228）。
- 实测公式验证：lane A 22.3s/任务 × 3052 ≈ 18.9h；理论下限（均衡+合并后）≈3.6h。

**P0-2｜双 lane 同绑一台模拟器**
- `runtime-evidence/lane-a/lane-meta.json` 与 `lane-b/lane-meta.json` 的 `device_serial` 都是 `emulator-5554`。违背 SKILL.md"single ADB writer per device"契约：两队列互相干扰 app 状态、页面身份校验失败率上升（lane-b 56/116 UNRECOGNIZED），且并行度实际=1。
- 代码无启动校验：`gmi_runtime.py` 不检查"两 lane serial 必须不同"。

**P0-3｜B1：P2 人工验收文件四重错位（无生产者）**
- `gmi_phase3_adapter.py:325-341` 期望 `<workspace>/human-review/phase-2-acceptance.json`，要求 `decision=="ACCEPTED"` 且 `closure_sha256`。
- controller 侧 `record_human_review.py:82-101` 实际写 `controller/human-reviews/phase-0N/<review_id>.json`，decision ∈ {APPROVED, REWORK, APPROVED_DEVIATION, MANUAL_TAKEOVER}（`_human_gate.py:12`，**无 ACCEPTED**），哈希字段是 `gate_report_sha256`（绑定 gate-report 而非 closure）。
- 目录名、文件名、decision 值域、哈希绑定对象**全部错位**；全仓无任何脚本写 adapter 期望的那个文件 → 人工审核完成后 adapter 依然拒绝，P3 永远开不了门。

**P0-4｜B2：Gate 2 恒 FAIL 的字段错位**
- `validate_gate.py:5055`：`gate.get("audit_discrepancy") != 0` → 报错。
- 生产者 `gmi_closure.py:185-199` 的 gate 只写 `audit_passed`（布尔），全仓唯一写 `audit_discrepancy` 的是测试 fixture（`test_gmi_gates.py:42`）。→ gmi 模式下 controller 的 Gate 2 **结构性恒 FAIL**。

**P1-1｜集成测试断裂链（7/206 失败）**
- scaffold 4 失败 + feature-implementation 1 失败 + inventory 2 失败，同一根因：共享 fixture `gmi_phase2_fixture.py:477` 调 `gmi_closure.py` 被新版规则拒绝（要求 dual-lane、static-analysis 先行、REVIEW≤10%，fixture 还是旧协议）。另有 `test_workflow.py` 失败于 fake_android 与 `validate_gate` 的 analyzer 可用性协议不匹配。
- 含义：**P2→P3 桥在当前版本从未被任何测试跑通过**。

**P1-2｜B3：通用应用 carrier 不可判定**
- `gmi_phase3_adapter.py:271-289` `infer_page_kind` 对不以 activity/fragment/dialog/screen/page/view/sheet/configure/picker/popup 结尾的类名返回 COMPOSABLE；COMPOSABLE 不在 `init_scaffold.py:64-69` 的 ROUTABLE/NON_ROUTE 两个集合中 → `gmi_mapping_type:250-262` 抛 `carrier-undecidable` BLOCKED。markor 类名全带后缀所以没踩，换一个命名风格的应用（如 `HomeCompose`）P3 直接死。

**P1-3｜假 VERIFIED 风险（真实性）**
- `_judge_task`（`gmi_runtime.py:612-623`）对 `sym=="MainActivity"` **无条件 VERIFIED**；markor 补丁 3 的 launcher 兜底（`analyze_static_pages.py:839-860`）把零/多引用主体一律绑 launcher 页 → 任意应用中共享 utils 的 event/state 任务可能在主页快照上自动 VERIFIED。audit 重放同规则（`gmi_audit.py:57` `not anchors_defined → VERIFIED`），**防伪审计与采集同源，发现不了弱证据**。
- Compose 应用硬编码文案不在 strings.xml → `anchors_defined=False` → 包内即 VERIFIED：门禁数量可达、质量失真。

**P2 级**（不阻断但需处理）：死代码（§5）、文档失实（`input-contract.md:53` 仍称 compare_screenshot 参与运行时复核；P4 `SKILL.md:21` 写 `audit-discrepancies.csv` 实为 `audit-replay.csv`）、`ProjectSkillStatus.txt` 全 true 与 7 个失败用例不符、UNMAPPED=0 无任何测试覆盖、permission_policy 引用不存在的 `reports/security_trust_report.md`、scaffold policy JSON 带 BOM。

---

## 3. 推荐的简单目标闭环

**目标画像**（对齐用户三个要求：闭环、快、大部分一致）：

```
P1 冻结（分钟级，现状已 PASS）
 └→ P2：静态 100% 全量（保持）+ REQUIRED 1151 任务 100% 上机
        + REVIEW 同页合并抽验（≤10% 上机）+ 双 lane 真·均衡
        → 运行时段目标 1.5–2.5h（原 ~19h）
 └→ P2.5 人工审核：record_human_review 直接产出 adapter 可读的 acceptance（修 B1）
 └→ P3 脚手架一次成型（修 B3 后对任意命名风格应用可用）
 └→ P4：核心 REQUIRED 页（markor 约 8–10 页）全证据深验（UiTest+断言+哈希链）
        + 其余页"轻证"（结构化对比：组件树/文案集合 + 截图抽检）
        —— 落实"大部分一致"：核心页组件/文案/行为 100% 对齐，
           全页结构对比 ≥80%，几何逐像素从门禁降为抽样工具
 └→ 交付：publish_harmony_project → arkts/（空目录约束保持）
```

**不变式**（不降门禁语义，靠任务分层而非降阈值）：
- 静态 100%、REQUIRED 100%、证据哈希/身份错误 0 —— 原样保留；
- UI/功能 ≥90%、REVIEW 未验证 ≤10% —— 原样保留，但 UI 分母中 REVIEW 档通过"同页合并快照"满足（一页一次快照可服务同页全部 STATE_BRANCH 的身份判定，每任务仍独立 gate 行与证据引用，`gmi_audit.py:118-131` 天然支持）；
- 保留：针对性插桩、有限状态探索、Skill 级变异验证（`test_minimal_phase2.py` 的 16 个用例就是其载体，不新增流程）。

---

## 4. 文件级最小修改规划

> 原则：只改行为必需处；不重构；不统一同名模板；参数可配置而非硬编码。

### A 组｜闭环断链修复（必做，约半天）

| # | 文件 | 位置 | 改动 | 依据 |
|---|---|---|---|---|
| A1 | `android-harmony-migration-controller/scripts/validate_gate.py` | :5055 | `gate.get("audit_discrepancy") != 0` → `gate.get("audit_passed") is not True`（或让 gmi_closure 增写数值型 `audit_discrepancy`，二选一，推荐前者） | P0-4/B2 |
| A2 | `android-harmony-migration-controller/scripts/record_human_review.py` | 新增分支 | `--phase 2 --acceptance` 受控双写：在写既有 `controller/human-reviews/...` 的同时，产出 `<run>/phase-02-android-inventory/human-review/phase-2-acceptance.json`，`decision` 做 APPROVED→ACCEPTED 映射，`closure_sha256` 由 phase-2-closure.json 现算 | P0-3/B1 |
| A3 | `harmonyos-migration-scaffold/scripts/init_scaffold.py` | :250-262 | `gmi_mapping_type` 对 `COMPOSABLE` 不再抛异常：归入 ROUTABLE（按 SCREEN 处理）并记录 `kind=COMPOSABLE→SCREEN` 决策行 | P1-2/B3 |
| A4 | `harmonyos-migration-scaffold/scripts/tests/gmi_phase2_fixture.py` | :477 附近 | fixture 对齐新版 closure 协议：补 `static-analysis/runtime-tasks.json`、lane 队列文件、REVIEW 抽样数据（一次性修好，7 个失败测试应转绿） | P1-1 |

### B 组｜时间压缩（核心诉求，约 1 天）

| # | 文件 | 位置 | 改动 | 预期收益 |
|---|---|---|---|---|
| B1 | `android-migration-inventory/scripts/gmi_runtime.py` | :538-609 `split_queues` | exclusive 约束降到**任务级**（仅 SCENARIO/SIDE_EFFECT 本身独占 lane A），journey 不再按整页捆绑；其余贪心均衡 | 3052/380 → ≈1716/1716，wall 19h→≈10.5h |
| B2 | 同上 | :707-764 `run_lane` | 同页 STATE_BRANCH 合并执行：同 page_id 任务共享一次 navigate+before 快照，逐任务独立判定与落盘 | 上机任务 3432→≈1200，再省 ≈60% 机时（与 B1 叠加后运行时段 ≈1.5–2.5h） |
| B3 | `android-migration-inventory/scripts/analyze_static_pages.py` | :36-45 `TASK_VERIFICATION` | 落实 SOURCE_ONLY：编译期常量分支/纯内部实现判 SOURCE_ONLY（`gmi_closure.py:117` 已按分母排除，语义合规） | 直接减少上机分母 |
| B4 | `gmi_runtime.py` | :196-237 `snapshot` | ① `wm size/density` 改 lane 级一次性缓存（省 6 次 adb/任务）；② `uiautomator dump+pull` 两步改 `exec-out cat` 一步（模式已存在于 :255） | ≈2–4s/任务 |
| B5 | `gmi_runtime.py` | :120,:193 等启动/常驻 sleep | 冷启动 6.0s 保留，轮询类 1.2–1.6s 降 0.5s 并加条件等待（poll-for-idle），全部走参数可调，不硬编码 markor 时序 | ≈2–5s/任务 |
| B6 | `analyze_static_pages.py` | main :652-1064 | 增量 recapture：按 mtime/sha 文件级缓存，validate FAIL 修复后只重扫受影响文件 | 重扫 2.5h→分钟级 |
| B7 | `gmi_runtime.py` | `run_lane` 启动处 | 校验两 lane `device_serial` 必须不同且都在线，相同即拒绝启动并明示 | 消除 P0-2，并行度 1→2 |

### C 组｜通用化与真实性（约半天）

| # | 文件 | 位置 | 改动 | 依据 |
|---|---|---|---|---|
| C1 | `gmi_runtime.py` | :612-623 `_judge_task` | 删除 MainActivity 无条件 VERIFIED：至少要求 foreground==包名且命中页面身份特征（无特征时降为 UNRECOGNIZED 而非 VERIFIED） | 假 VERIFIED |
| C2 | `analyze_static_pages.py` | :839-860 `resolve_host_page_id` | launcher 兜底改为显式 `PENDING_HOST_BLOCK`（宁阻塞、不假绑）；阻塞项进人工审核清单 | 假 VERIFIED/错绑 |
| C3 | `analyze_static_pages.py` | :346-357, :374-379 | 删除前一样例应用的 `ListFragment/AddFragment/UpdateFragment/tododatabase` 映射残留（幽灵页风险） | 通用性 |
| C4 | `gmi_runtime.py` | :418 | `owners∈{1,2,3,6}` markor 校准魔法数字 → CLI 参数 `--max-shared-owners`（默认 6，文档注明按应用校准） | 通用性 |
| C5 | `gmi_phase3_adapter.py` | :243-268 `find_application_id` | 最终兜底 `com.example.todo`（:268）改为报 BLOCKED（要求人工填 application_id），不做静默错绑 | 通用性 |

### D 组｜文档与状态修正（随代码同步，约 2 小时）

- `harmonyos-feature-implementation/references/input-contract.md:53`：compare_screenshot 描述改为 `_stage4_audit` 内联校验（若 §5 决定删除 compare_* 则必须改）。
- `harmonyos-feature-implementation/SKILL.md:21`：`audit-discrepancies.csv` → `audit-replay.csv`。
- `android-migration-inventory/references/static-page-analysis.md:39`：SOURCE_ONLY 承诺——B3 落地后即与实现一致，无需改文。
- `ProjectSkillStatus.txt`：改为按实测维护（当前 4 个 true 与 7 个失败用例不符）。
- 两个 permission_policy 中 `reports/security_trust_report.md` 引用删除或补文件；scaffold policy 去 BOM。

---

## 5. 应删除、合并或保留的内容

**建议删除**（确认无独立价值后）：
- `android-migration-inventory/scripts/gmi_probe.py` —— 零引用，且模块级硬编码 `PKG="net.gsantner.markor"`（:39），是 markor 专用探针混入通用 Skill 的实证。
- `harmonyos-feature-implementation/scripts/issue_page_work_order.py`、`issue_capability_work_order.py` —— 无 import、无文档引用，职能已被 `stage4_work_orders.py` 取代。
- `compare_behavior/compare_component_tree/compare_geometry/compare_screenshot/compare_migration_unit.py`（5 个）—— 生产链零引用，比对功能已由 `_stage4_audit.py` 内联（`validate_stage4.py:20-43` import 清单可证）；唯一引用方是测试 `test_deterministic_comparators.py`。删除时同步改该测试与 input-contract.md:53。（若不想动测试，可整体归档到 `scripts/legacy/`）

**建议合并/归位**：
- `test_minimal_phase2.py` 从 `scripts/` 根移入 `scripts/tests/` —— 它不是死代码：16 个用例真实覆盖双 lane 分配、双设备校准、checkpoint/resume、audit 防伪、closure 阈值、mutation 变异（`test_minimal_phase2.py:1-9` 自述为方案自检），是"Skill 级变异验证"的载体，**应保留并纳入常规测试运行**。

**明确保留（各有消费方，勿强行统一）**：
- 同名模板不同构：`evidence-index.template.csv`（inventory 7 列 runtime 索引 vs P4 19 列 parity 证据链，分属不同目录，各自消费方所读列均在）；`attempt-ledger`（controller 与 P4 表头逐字一致）；`rework-log.csv`(17 列) 与 `rework-tickets.csv`(22 列) 双账本并存，消费关系已核实无读不到的列。
- `gmi_audit` 全量哈希重算与身份重放：分钟级成本，是防伪核心，保留全量（REVIEW 身份重放可选抽样，优先级最低）。
- 门禁阈值本身（静态100/UI≥90/功能≥90/REQUIRED100/REVIEW≤10）：不降，时间问题用任务分层解决。

---

## 6. 最小测试与验收方案

**第一层：单元/集成回归（改完即跑，全绿为基线）**
```
cd <每个 skill>/scripts && PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests
```
- 基线目标：206 用例全过（A4 修复后 scaffold 4 个、inventory 2 个、feature-implementation 1 个应转绿）；pytest 未安装，unittest 等效（本次实测已验证可行）。
- 新增用例（最小集，写进现有测试文件）：
  1. B1：split 后两 lane 任务数差 ≤5%（用 markor 3432 任务样本 fixture）；
  2. B7：两 lane 同 serial 拒绝启动；
  3. A1：gate 只含 audit_passed=True 时 validate_gate --phase 2 判 PASS（对 gmi 布局）；
  4. A2：record_human_review --phase 2 --acceptance 产出的文件能被 gmi_phase3_adapter 接受（哈希匹配闭环）；
  5. C1：无身份特征的 MainActivity 快照判 UNRECOGNIZED 而非 VERIFIED。

**第二层：markor 实测验收（一次全量 run）**
- P2 运行时段（队列冻结→gmi_closure）≤2.5h；双 lane serial 不同；REQUIRED 完成率 100%；audit discrepancy=0；`validate_gate --phase 2` PASS（A1/A2 修复后）。
- P3：init_scaffold 通过、构建+冒烟证据齐全、`stage-03-closure-manifest` 封闭。
- P4：核心 REQUIRED 页全 ACCEPTED；全页结构对比 ≥80% 一致（"大部分一致"的机器定义）；publish 到 `arkts/` 成功。
- 端到端（P1→P4）≤ 1 个工作日。

**第三层：通用性冒烟（非 markor 应用，一个小型 Compose/Jetpack 样例即可）**
- 不 BLOCK 在 carrier 判定（A3）；application_id 不落 com.example.todo（C5）；无 strings.xml 锚点页不产生假 VERIFIED（C1）。

---

## 7. 推荐实施顺序和重大问题停止条件

**顺序**（每批完成后跑第一层测试再进下一批）：
1. **第 1 批·断链**（半天）：A1 → A2 → A4 → 验证 Gate2 链路 → A3。
2. **第 2 批·提速**（1 天）：B7（先堵住同 serial）→ B1 → B2 → B3 → B4 → B5 → B6。改完先在 markor 上跑 30 分钟抽样（两 lane 各 50 任务）核对速率与 UNRECOGNIZED 率，再放全量。
3. **第 3 批·通用与真实**（半天）：C1 → C2 → C3 → C5 → C4。
4. **第 4 批·清理**（半天）：§5 删除项 + D 组文档。
5. **第 5 批·全量验收**：markor 端到端 + 通用样例冒烟（§6 第二/三层）。

**停止条件**（出现即停，回退该步，不带病前进）：
- B2 同页合并后 UNRECOGNIZED 率 >20%（页面身份误判）→ 回退为按状态组分批执行；
- A1/A2 修完 Gate 2 仍 FAIL（冒出新字段错位）→ 停，重新审计 closure→adapter→gate 全链字段；
- 双设备校准持续失败（两台模拟器无法达成一致性）→ 降级单 lane + 扩大 SOURCE_ONLY 分类（时间换可靠性），不得伪造校准记录；
- markor 全量重跑后 REQUIRED 完成率 <90% → 检查 C1/C2 是否引入新阻塞（宁可显式阻塞也不假绑）；
- 任一批测试基线由绿转红且 30 分钟内无法定位 → 回退该批改动。

---

## 8. 挑战杯现场推荐演示路径

**总策略**：现场只演示"可压缩到 15–20 分钟的切片 + 完整证据链"，完整 19h→2.5h 的 run 以 `runs/` 目录与录屏作证。突出差异化卖点：**防伪审计重放、人工审核哈希绑定、双 lane 并行、变异验证**——这些是别的迁移方案没有的。

1. **P1 冻结（3 分钟，真实操作）**：`init_migration` + `preflight_env` 双机校准 + `validate_gate --phase 1` PASS —— 展示"冻结"理念（scope 哈希、APK 哈希、屏幕基线）。
2. **P2 静态（5 分钟，快进+实物）**：放 `gmi_scan` 13 表生成录屏 30 秒；现场打开 `candidates/` 的 inventory/page-fields/behavior 表和 `SCANNER-PATCH-NOTES.md`——**把当初 956 个绑定失败→修扫描器→recapture 的过程当卖点讲**（真实性叙事：机器发现问题、人修规则、全量留痕）。
3. **P2 运行时（4 分钟，真机同屏）**：两台 Android 模拟器并排，现场续跑 checkpoint 恢复 2–3 个核心 journey（笔记创建→编辑→保存→杀进程→重启恢复），展示 lane 队列、每任务快照、`VERIFIED` 判定条件（foreground+身份特征）。
4. **P2.5 人工审核（2 分钟）**：`record_human_review --phase 2 --acceptance` → 展示 closure_sha256 绑定与"改一个字节即失效"（当场篡改演示最直观）。
5. **P3 脚手架（3 分钟）**：`init_scaffold` → hvigor 命令行构建 → 装模拟器 → 路由冒烟截图墙（每页一张 PNG 证据）。
6. **P4 对比（4 分钟，高潮）**：Android 左、Harmony 右的对比卡片——核心页放"组件/文案 100% 对齐+UiTest 断言"，普通页放"结构对比 ≥80%+截图抽检"，如实讲"大部分一致"的目标与边界（诚实反而加分）。
7. **交付（1 分钟）**：`publish_harmony_project --target arkts/` → 现场打开 DevEco 运行。
8. **兜底**：完整 run 的 `runs/` 目录树 + gate-report 时间线 + `audit-replay.csv`（0 差异）打印页；断网/设备故障时切录屏。

---

## 附录 A｜通用场景（非 markor 应用）泛化结论

- **耗时公式**：`T_static ≈ c₁·F·R`（F=源文件数，R≈4–5 遍全读，markor ~500 文件实测含返工 2.5h）；`T_runtime ≈ max(N_A,N_B)·t̄`，`t̄ = n_snap×(t_dump+t_pull×2+t_cap+t_sys) + n_tap×(stay+1.5) + p_exit×T_btf`（markor 代入：理论 16–28s vs 实测 22.3/41.4s，吻合）。换应用只需代入任务数与快照步数。
- **UI 范式覆盖**：XML/ViewBinding/通用 R.layout ✅；Compose 有扫描路径但根识别只认 `*Screen` 命名（`analyze_static_pages.py:363`，**缺口**）；Navigation Component ✅（:285-330）；纯代码 Dialog ✅（补丁 2）；WebView 壳半缺口（只生成 DYNAMIC_SURFACE 风险任务，内容无法锚点匹配）；**Flutter add-to-app：全仓无检测（缺口）**。
- **门禁可达性边界**：强账号/验证码应用 REQUIRED 100% 永不闭合（无环境适配器即 BLOCKED，属"如实阻塞"但 Phase 2 停摆）；REVIEW≤10% 只兜数量不兜真假（C1/C2 修复前）。
- **13 表非空**：实为字节级检查（`validate_gate.py:5058-5061` st_size==0 才拒），仅表头不阻塞，且 completeness 有 N/A 豁免（`gmi_generate.py:565-584`）——无动画/无设置页应用**不会**被系统性空表误阻塞。
- **markor 三补丁普适性**：补丁 1（通用 R.layout）与补丁 2（Dialog 关键字）普适，应进主干；补丁 3（launcher 兜底）特化风险高，按 C2 收紧。
- **硬编码残留**：`analyze_static_pages.py:346-379`（ListFragment/todo demo 映射，逻辑级）、`gmi_runtime.py:418`（markor 校准魔法数字）、`gmi_runtime.py:960-991`（markor 特定导航特判，--auto 诊断路径）、`gmi_probe.py:39`（net.gsantner.markor）。Skill 代码无 ListFragment→TODO-LIST 功能映射（SKILL.md:42 声明属实）。

## 附录 B｜关键证据索引

- 运行产物：`runs/MIG-20260827T162215Z-B6CBE3/{run-manifest.json, controller/gate-report.json, phase-02-android-inventory/{phase-manifest.json, SCANNER-PATCH-NOTES.md, runtime-evidence/*}}`
- 断链三处：`gmi_phase3_adapter.py:325-341`｜`validate_gate.py:5055`｜`gmi_phase3_adapter.py:271-289`+`init_scaffold.py:250-262`
- 时间结构：`gmi_runtime.py:{32,196-237,538-609,612-623,707-764}`｜`analyze_static_pages.py:{36-45,343,652-1064,839-860}`｜`gmi_closure.py:{77-89,117,155-162,185-207}`
- 契约主链 PASS 证据：`gmi_generate.py:875-887` ↔ `validate_gate.py:4979-4987`（13 表同名）；`validate_stage3.py:1763-1772` ↔ `init_implementation.py:264-302`；`page_acceptance_contract.py:136-163` ↔ `gmi_generate.py:901-923`（列名全在）
- 测试实测：controller 39/39 过；inventory 29/31；scaffold 1/5；feature-implementation 130/131（unittest 运行，2026-08-28）

---

## 附录 C｜Phase 2 修复实施记录（2026-08-28 已完成）

> 备份：`/tmp/skills-backup-20260828-024354`（改前全量副本，可随时 diff/回退）。实施按 §4/§7 规划分三线并行 + 回归收尾，全部改动通过终验。

### C.1 已落地的改动

| 批次 | 文件 | 内容 |
|---|---|---|
| A1 | controller/scripts/validate_gate.py:5051-5062 | gmi 门禁改判 `audit_passed`（主判定），数值型 `audit_discrepancy≠0` 仍报错（兼容旧布局）；Gate 2 字段断链闭合 |
| A2 | controller/scripts/record_human_review.py | `--phase 2` 批准时双写 `<p2>/human-review/phase-2-acceptance.json`（decision=ACCEPTED、closure_sha256 现算绑定、reviewer_id/accepted_at 对齐 adapter 读取字段）；closure 非 READY_FOR_HUMAN_REVIEW 拒写、acceptance 已存在拒写、写失败回滚主记录；legacy 路径零影响 |
| B1/B2/B4/B5/B7/C1/C4 | inventory/scripts/gmi_runtime.py（341 行 diff） | split_queues 任务级均衡（exclusive 全 A + 页组贪心，60 任务实测 30/30）；同页 STATE_BRANCH 合并快照（≤50/组，shared_source 复制证据、UNRECOGNIZED 单独重试、组首 EXITED 整组降级）；wm 进程级缓存 + `exec-out uiautomator dump /dev/tty` 一步法（失败自动回退两步）；`--wait-scale`（默认 0.5，冷启动 6.0 固定，btf 下限 2.0）；双 lane 同 serial 拒绝启动（REFUSING TO START）；`_judge_task` 删除 MainActivity/无锚点无条件 VERIFIED（必须特征命中）；`--max-shared-owners` 参数化（默认 6） |
| B3/B6/C2/C3 | inventory/scripts/analyze_static_pages.py + validate_static_analysis.py | VERIFY_STATE_BRANCH 保守编译期常量判定（字面量/全大写常量标识符及其比较，11 接受/13 拒绝边界验证，宁可少判）→ SOURCE_ONLY；`--incremental` 文件级缓存（含 action_map/analyzer 指纹失效，增量与全量产物一致）；resolve_host_page_id 歧义改 `PENDING_HOST` 显式阻塞 + needs_human_resolution 清单，validate 对 PENDING_HOST 降 WARNING（普通缺失仍 FAIL）；删除 ListFragment/todo 样例残留映射 |
| C5 | inventory/scripts/gmi_phase3_adapter.py | application_id 三级推断失败改 SystemExit（提示人工补充），删除 `com.example.todo` 兜底 |
| A4 | scaffold/scripts/tests/gmi_phase2_fixture.py（重写 640 行） | 对齐新 closure 协议全链：runtime-tasks.json（REQUIRED+REVIEW 抽样）→ 真实 split-queues → 双 lane 证据 → 真实 gmi_audit → 真实 gmi_closure → acceptance 哈希绑定 → adapter → advanced-gate 哈希链重绑；连带修复 inventory tests/test_gmi_phase3_adapter.py（closure 构造）、test_workflow.py 与 scaffold test_stage3_workflow.py（fake 脚本执行位幂等 chmod） |

### C.2 新增回归用例（5 项，均在现有测试文件）

B1 均衡（60 任务断言 30/30+exclusive 全 A+页内连续）、B7 同 serial 拒绝启动、A1 audit_passed 门禁（含反例）、A2 acceptance↔adapter 哈希闭环、C1 无特征 MainActivity 判 UNRECOGNIZED（3 子用例）。

### C.3 终验结果（leader 独立复跑，PYTHONDONTWRITEBYTECODE=1 unittest）

| Skill | 结果 |
|---|---|
| controller | **41/41 OK**（基线 39 + A1/A2） |
| inventory tests/ | **15/15 OK**（原 1 fail 1 error 全部转绿） |
| inventory test_minimal_phase2.py | **21/21 OK**（基线 16 + 新增 5） |
| scaffold | **5/5 OK**（原 4 fail 全部转绿） |
| feature-implementation | **134/134 OK**（test_stage4_workflow 转绿） |

**合计 216 用例全绿**；无新建文件、无 git 操作、生产脚本改动严格限于授权清单。

### C.4 遗留问题（生产脚本间协议不一致，已用 fixture 桥接，建议后续主线修复）

1. `gmi_phase3_adapter.py:641` 仍按页级 `status=="VISITED"` 判定 ACCEPTED 页，而 gmi_audit 合并产出 task 级 VERIFIED——建议 adapter 改为聚合 task 级状态，即可删除 fixture 的页级 VISITED 桥接行。
2. `gmi_phase3_adapter.py:834-838` advanced-gate 硬编码 `decision_source=HUMAN_REVIEW_ACCEPTANCE`，与 validate_stage3/validate_gate 要求的 `DETERMINISTIC_ADVANCED_RUNTIME_AND_PROBE_GATE` 冲突——建议透传 evaluate_advanced_gates 真实产物。
3. fake_harmony/fake_android 执行位不属于内容 diff，跨机器可能丢失——消费方测试已内置幂等 chmod 兜底。

### C.5 下一步（真实环境验证）

代码侧修复与测试回归已完成。剩余验证依赖真机环境，按 §6 第二层执行：起两台模拟器（不同 serial）→ markor 新 run（B3/B6 生效后静态段应 ≈1h 内，运行段按新参数预估 2-3h）→ 观察 UNRECOGNIZED 率（>20% 触发 §7 停止条件）→ gmi_audit/closure → record_human_review --phase 2 → validate_gate --phase 2 首次真实 PASS → P3 开门。

---

## 附录 D｜Phase 3/4 优化与全面加固实施记录（2026-08-28 第二轮）

> 延续附录 C 的方法：四批并行实施（P3 链路修复 / P4 分层 / 清理 / 僵尸运行防护），全部通过终验。备份仍为 `/tmp/skills-backup-20260828-024354`。附录 C.4 的遗留 1/2 已在本轮主线修复（不再是桥接）。

### D.1 P3 链路修复（任务 13）

| 项 | 文件:位置 | 内容 |
|---|---|---|
| A3 COMPOSABLE 载体 | scaffold/init_scaffold.py:69-75,247-285,291-336,349-357 | COMPOSABLE 按 SCREEN 语义归入可路由载体（不再 carrier-undecidable 阻断）；决策痕迹记入 route-registry 与 architecture-map 的 notes；混合 kind 时 NON_ROUTE 优先级不变 |
| 遗留1 页级 VISITED 口径 | inventory/gmi_phase3_adapter.py:638-664 | accepted_syms 改 task 级聚合：该页全部 task 均 VERIFIED 且≥1 行才接受；兼容旧页级 VISITED 行 |
| 遗留2 advanced-gate | gmi_phase3_adapter.py:829-868 | decision_source 优先透传 evaluate_advanced_gates 真实产物，否则确定性默认 DETERMINISTIC_ADVANCED_RUNTIME_AND_PROBE_GATE（required==received）；closure verdict 同步 |
| fixture 桥接移除 | scaffold/tests/gmi_phase2_fixture.py | 删除 _append_page_gate_rows 与 _finalize_deterministic_advanced_gate 桥接 |

### D.2 P4 分层验证（任务 15）——「大部分一致」落地

**tier 推导**：a) 工单显式 phase4_verification_tiers（入 input_lock 哈希）＞ b) P2 runtime-tasks.json 该页 REQUIRED 任务>0→CORE 否则 LITE ＞ c) 默认 CORE。旧合同默认 CORE 完全兼容。

| 环节 | 分层行为 |
|---|---|
| 合同/registry | verification_tier 入可选键与 registry 新列；apply/derive 函数；值域 fail-closed |
| 工单 | LITE 页 required_parity_checks 六类裁为 [COMPONENT_TREE,SCREENSHOT,BEHAVIOR]；工单带 tier 参与重放校验 |
| 采集 | LITE 命令序列 10→6 类（保 DEVICE/CLEAN_INSTALL/LAUNCH/NAVIGATE/SCREENSHOT/UITEST）；evidence-index 行落 tier 列 |
| 审计 | LITE_EVIDENCE_SEQUENCE；内联 _lite_component_overlap（蓝本自已删 compare_component_tree 备份）：一致率 1-\|diff\|/max(1,\|expected\|)≥0.8；哈希绑定/三类断言/截图分辨率全保留 |
| 复核 | CORE 须全视觉元素；LITE 允许非空子集；review JSON 落 tier |
| controller | :4151 提取 _reviewed_visual_ids_are_acceptable 分层；逐元素几何按页 tier 分层；HEVD 重放读 evidence-index tier 列（缺省 CORE）；hrev_keys 兼容；gmi 等价校验未动（天然兼容） |

时间收益：P4 机器时间压缩比 ≈0.45-0.6（省 40-55%）；「大部分一致」= 组件树一致率 ≥80% + 截图 + 三类断言 PASS。

### D.3 清理（任务 16）

- 删 9 文件：compare_* 5 脚本 + comparison_common + test_deterministic_comparators.py（18 用例整体依赖被删模块）+ calculator fixtures ×2。
- input-contract.md:53 改述 _stage4_audit 内联校验；SKILL.md:21 audit-discrepancies→audit-replay；ProjectSkillStatus.txt 按实测重写。
- 白名单 policy 实测无 BOM 无错误引用（未动）。**白名单外遗留**：governed-execution-contract.md:3 同类错名、controller policy 引用不存在的 reports/security_trust_report.md。

### D.4 僵尸运行防护（任务 18，用户发现的盲区）

盲区：模拟器画面冻结但 adb 活着 → dump/截图照常返回冻结页、任务照常"完成"、agent 看似正常推进，直到 closure 才拦截（整轮白跑）；冻结在目标页还产生格式合法的"静止 VERIFIED"，审计与采集同源无法识别。

| 机制 | 触发 | 动作 |
|---|---|---|
| 跨页静止检测 | 连续 S=6（--stale-streak≥3）个真拍快照 ui+png 哈希与前一个相同且涉及 ≥2 页（同页/B2 共享不误报；VERIFIED 不清零） | 半程 WARNING；达阈值 exit 3 + FROZEN_DEVICE_SUSPECTED（agent 可读指令：重启模拟器→resume） |
| 连续异常熔断 | 连续 F=15（--fail-streak≥5）个任务终态 UNRECOGNIZED/EXITED/ERROR | exit 4 + DEVICE_UNRESPONSIVE_SUSPECTED |

仅影响"是否继续跑"，不改判定/证据语义；fuse-state.json 原子写记录原因，resume 打印并清除；checkpoint 全程保留。落地位置 gmi_runtime.py:719-734（签名）、807-819（resume）、990-1108（主循环双机制）、1147-1158（CLI）。

### D.5 终验（leader 独立复跑）

| 套件 | 用例数 | 结果 |
|---|---|---|
| controller | 42（41+1 :4151 分支） | **OK** |
| inventory tests/ | 15 | **OK** |
| inventory test_minimal_phase2.py | 25（21+4 防护） | **OK** |
| scaffold | 5 | **OK** |
| feature-implementation | 127（116+11 分层） | **OK** |

**合计 214 用例全绿**（feature 134 → 删 18 死代码用例 → 116 → 加 11 分层用例）。

### D.6 遗留与下一步

1. D.3 两处白名单外文档/引用问题待下轮（已由附录 E 处理完毕）。
2. 真机验证（C.5）流程不变，且长跑已具备冻结防护。
3. LITE 首次真机运行后按实际一致率微调 --stale-streak/--fail-streak 与 LITE_COMPONENT_OVERLAP_MIN。

---

## 附录 E｜设置增强与外部审核遗留闭环（2026-08-28 第三轮）

> 触发：外部 agent 只读审核（214 全绿复核 + A/B/C/D 组逐项行级取证）+ 用户确认"设置功能极大程度实现"需求。审核结论经 leader 实测裁决：大部分采纳，两处纠偏（"ProjectSkillStatus 零改动"为误报，mtime 04:18 已更新；B5 :120 冷启动为设计内保留非缺陷）。

### E.1 设置三列增强（保证设置类大功能"极大程度实现"的静态基础）

| 改动 | 文件:位置 | 内容 |
|---|---|---|
| category 上下文跟踪 | inventory/gmi_scan.py:912-921 | scan_preference_xml 跟踪最近 PreferenceCategory 祖先 title → node["category"]（无则空串）；defaultValue/summary 原解析保留 |
| 三列产出 | inventory/gmi_generate.py:645-655 | make_pref_concat_rows 行加 default_value/summary/category（资源引用保持 @string/xxx 原样） |
| 非 preference 源 | gmi_generate.py:353-361,366-375 | XML comps 与 compose 行三列空串 |
| 表头 | gmi_generate.py:901-904 | page-fields.candidates.csv 追加三列 |

兼容性核实（工程师逐点取证）：全部消费方（gmi_runtime:64-68、gmi_closure:53-58、gmi_phase3_adapter:44-48、两 _common、page_acceptance_contract:824-828、controller/validate_gate:508-513、anchor_phase2_evidence:45-48）均为 csv.DictReader **按列名读取**，加列零错位；无列集精确断言；B6 缓存指纹为自文件 sha256（本轮 :295/298 修改自动失效旧缓存），gmi 生成链无缓存无缺列风险。测试：test_gmi_candidate_bindings.py +2 用例（4 组断言：带属性 preference 三列正确/缺属性空串/无 category 包裹空串/非 preference 源空串）。

时间影响：静态段毫秒级（三条 regex），不产生新运行时任务，Phase 2 总时长不变（2.5-4h 机器时间）。

### E.2 外部审核确认的遗留清理（全部完成）

| 项 | 处理 |
|---|---|
| 删 gmi_probe.py + issue_page/capability_work_order.py | 先同步改造两处消费方（feature test_stage4_workflow.py CLI 调用改 stage4_work_orders.issue_page_order 库调用；controller test_metadata_consistency.py required 列表移除）再删；grep 零残留（含 pyc） |
| controller policy 幽灵引用 | 实测 2 处（审核报 1 处）evidence 引用 reports/security_trust_report.md 全删，JSON 合法 |
| scaffold policy BOM | efbbbf 已去（utf-8-sig 读→utf-8 写，语义等价验证 old==new） |
| 文档幽灵文件名 | audit-discrepancies.csv（gmi_audit 从未产出，真实为 audit-replay.csv 的 discrepancy 列）全 skills 6 处清零：phase-2/3/4-handoff、phase-gates ×2、input-contract ×2 |
| 样例方法名残迹 | analyze_static_pages.py:295/298 删 navigateToAddFragment 等五个前样例名（onClick/onLongClick 保留），零残留 |
| B5 两处游离 sleep | gmi_runtime.py:841 stay+1.0→stay+wait_secs(1.0)；:1380（审核所述 :192 实为此处）args.stay+1.5→args.stay+wait_secs(WAIT_TAP_SETTLE_BASE)；:120 冷启动按设计保留；**poll-for-idle 明确不做**（wait-scale 已取大头，避免增复杂度） |
| 测试载体说明 | feature SKILL.md Build and evidence 章节补 test_minimal_phase2.py 必跑说明 |
| 状态记录 | ProjectSkillStatus.txt 增补本轮变更记录 |

### E.3 终验（leader 独立复跑）

controller 42 ✅｜inventory tests 17（15+2）✅｜minimal 25 ✅｜scaffold 5 ✅｜feature 127 ✅ —— **合计 216 用例全绿**；audit-discrepancies/死文件/幽灵引用全 skills grep 归零。

### E.4 当前总体状态

三轮实施后：Phase 1-4 全链代码侧就绪（断链修复 + 提速 8-10 倍 + 通用化 + 分层验证 + 冻结防护 + 设置三列），216 用例回归网，唯一待办为真机 markor 全链 run（C.5 流程）。外部审核与本报告结论已互相印证闭环。
