# Phase 1-4 闭环（gmi 路径）

本文件定义 gmi 流程下 P1→P4 的完整数据流：每阶段"产什么、下一阶段吃什么、谁校验"。任何一环节缺失即 `CLOSURE-BROKEN`（阻断到被补，不静默）。

## 数据流总览

```text
P1 (scope)
  → phase-1-scope.md / phase-manifest.json      【included_features: 大写 Feature-ID】
        │
        ▼
P2 (gmi 盘点)
  → candidates/ 13表
      inventory.candidates        (页面列表+feature+state)
      page-fields.candidates      (页面字段/顺序/图标)      ★ P4 合同语义
      field-options.candidates    (字段可选值/子项)          ★ P4 合同语义
      navigation-relations.candidates (跳转+返回)            ★ P4 合同语义
      motion.candidates           (动效/行为)               ★ P4 合同语义
      color-palette.candidates    (颜色真值 hex+alpha)      ★ P4 资产
      asset-mapping.candidates    (FILE_ASSET/组件属性)     ★ P4 资产
      third-party-dependencies.candidates                 ★ P4 依赖
      risk-probes.candidates      (动态风险)               ★ P4 风险
      phase-2-completeness.candidates (10类缺口)           P4 已知边界
  → runtime-evidence/  (每页 ui.xml+screenshot+gate+audit-replay)  ★ P4 验证基准
  → coverage/coverage-ledger.csv  (UNMAPPED=0 证明)
        │
        ▼
P3 (adapter + scaffold)
  → <run>-run/phase-02-android-inventory/ (旧契约合成 + gmi 门禁验证)
  → <run>-run/phase-03-harmony-scaffold/ (harmony-project + registries)
        │
        ▼
P4 (实现 + 验证)
  → page_acceptance_contract.py 读：
      phase-02/.../inventory.csv + static-analysis/pages.json (结构)
      + candidates/*.candidates.csv (gmi 语义增强 → 合同 gmi_fields/options/nav/motion)
  → 每页合同 → AG-PAGE 实现 → hvigor 构建 → 鸿蒙模拟器截图
  → compare_screenshot (SSIM, 分辨率一致硬校验) + compare_geometry (bounds 解析)
  → review_parity → DEFERRED-PARITY(原因) 或 通过
```

## 各阶段"必须产/必须验"清单

| 阶段 | 必产 | 必验（否则阻断） |
|---|---|---|
| P1 | phase-manifest.json 的 `included_features`（大写合法） | 非空 + 正则 `^[A-Z0-9][A-Z0-9._-]{2,95}$` |
| P2 | candidates/ 13 表 + runtime-evidence + audit-replay | UNMAPPED=0 + audit DISCREPANCY=0 + closure 哈希 |
| P3 | run/phase-02...+phase-03 契约 + harmony-project | gmi gate verified (init_scaffold) + 首屏构建 |
| P4 | 每页 page-contract + .ets + parity | 截图真源 + SSIM/几何达标 + DEFERRED 带原因 |

## 闭环检查（一键跑）

```text
P1: 存在 phase-manifest.json 且 included_features 非空合法
P2: candidates/ 含 ≥8 语义表 && audit-replay discrepancy=no && coverage GAP=0
P3: run/phase-03-harmony-scaffold/harmony-project 存在 && stage-03-input-lock.json 存在
P4: 每页 contract 含 gmi_fields（非空页）&& parity 记录含 SSIM 或 DEFERRED 原因
```

任一失败 → 定位缺环阶段 → （适配器补或人工）补救后才可进入下一阶段。
本文件与 P3 `input-mapping-contract.md` 的 gmi 适配节、P4 `input-contract.md` 的 gmi 适配节一致。
