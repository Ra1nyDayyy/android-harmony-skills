# UiTest 页面证据规约

Phase 4 的 UI 运行证据必须由生成在 `ohosTest` 中、从 `@kit.TestKit` 导入的 UiTest 探针采集。探针只读取冻结的 `arkts-page-plan.json`，不允许模型自由补写页面、状态、组件或定位规则，也不允许测试代码进入 `entry/src/main`。

## 生成与执行

1. `scripts/init_implementation.py` 为每个 Page-ID 生成并校验 `arkts-page-plan.json`，逐项守恒载体、组件、几何、文本、资源、状态、事件、跳转、副作用、能力依赖和来源引用。
2. `scripts/prepare_uitest_probe.py` 为每个 Page-ID × State-ID 生成一个探针，并将生成清单、页面计划和文件哈希写入 `ui-test-snapshot-generation-manifest.json`。
3. 页面 ArkTS 必须给冻结组件绑定稳定 test tag；仅在冻结文本唯一时才可使用文本定位。组件缺失、定位结果为零或多于一个均立即失败。
4. 探针在安装最终 HAP 后执行，查询并记录组件的 type、text、bounds、visible、enabled、clickable，按冻结事件执行操作，并保存 `ui-test-snapshot.json`、`ui-test-snapshot.png` 与操作轨迹。
5. 功能、跳转、数据结果和系统副作用由确定性断言共同验证，不能由截图或组件属性替代。

## 运行绑定

每次正式执行必须绑定并校验：测试 HAP SHA-256、最终 HAP SHA-256、设备身份 SHA-256、完整命令 SHA-256、Page-ID、State-ID、结果目录和执行时间。任一字段为空、占位、与构建记录不一致或结果路径越界，证据无效。

## 信任边界

截图只能证明像素结果，组件查询只能证明可观测属性，二者都不能单独证明功能一致。正式结论必须同时具备冻结 Android 证据、页面计划守恒、UiTest 自动交互、功能与副作用断言、构建/设备/命令哈希以及独立人工审核。模型输出的 `PASS` 没有门禁效力。
