# ArkUI Inspector 证据规约

Phase 4 的 `ui-tree.json` 必须来自 ArkUI `UIContext` Inspector API。DevEco Studio 的可视化检查器适合人工排查，但正式门禁不驱动 IDE 界面，也不接受普通脚本自行拼出的组件数组。

## 接入方式

1. 将 `assets/arkui-inspector-bridge/ArkUIInspectorBridge.ets` 放入项目的 `ohosTest` 测试代码，不得进入发布模块。
2. 在目标页面完成加载并稳定后，从当前窗口取得 `UIContext`，调用 Bridge 的全树方法；局部事件验证可调用子树方法。
3. 测试适配器把 Bridge 返回的 JSON 写入 `UI_TREE_CAPTURE` 输出，并补充 `raw_tree_sha256`。哈希算法是对 `raw_tree` 进行 UTF-8、键名排序、无多余空格的 JSON 序列化后计算 SHA-256。
4. 同一适配器只能补充 Bundle、设备、目标页面和操作轨迹等运行绑定；不得自行生成 `root`、`nodes`、`bounds` 或 Android 组件绑定。
5. `capture_state.py` 从原始 Inspector 树重新展开节点、计算节点哈希，并按资源 ID 或唯一的“文本+类型”匹配 Android 组件。匹配缺失或不唯一都失败。

每条事件和跳转的 `before_snapshot`、`after_snapshot` 也使用同样的 Inspector envelope，不能换成模型总结、截图描述或自报状态。动画中的瞬时属性、Builder/controller 内部信息以及方法和事件本身不一定能由 Inspector 完整给出，因此事件语义仍需与确定性业务断言、导航结果和副作用证据共同验证。

## 信任边界

该规约能阻止旧式的“随手写一份 nodes JSON 就 PASS”，并把页面结构还原绑定到 ArkUI 官方运行时接口；它不能单凭 JSON 证明采集程序绝对可信。正式环境仍必须冻结测试适配器可执行文件、设备、命令和输出哈希，且由独立验证角色执行。Debug/ohosTest Bridge 不得进入交付包。
