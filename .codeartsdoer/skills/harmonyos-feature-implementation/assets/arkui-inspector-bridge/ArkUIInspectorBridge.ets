import { UIContext } from '@kit.ArkUI';

const BRIDGE_CONTRACT: string = 'arkui-inspector-bridge-v1';

/** Test-only bridge. Keep it under ohosTest; never compile it into a release module. */
export function captureArkUIInspectorTree(uiContext: UIContext,
  filters: Array<string> = []): string {
  const rawTreeText: string = uiContext.getFilteredInspectorTree(filters);
  const rawTree: Object = JSON.parse(rawTreeText) as Object;
  return JSON.stringify({
    schema_version: 1,
    source: 'ARKUI_UI_CONTEXT',
    api: 'getFilteredInspectorTree',
    capture_mode: 'OHOS_TEST_BRIDGE',
    bridge_contract: BRIDGE_CONTRACT,
    raw_tree: rawTree
  });
}

export function captureArkUIInspectorSubtree(uiContext: UIContext, id: string,
  depth: number, filters: Array<string> = []): string {
  const rawTreeText: string = uiContext.getFilteredInspectorTreeById(id, depth, filters);
  const rawTree: Object = JSON.parse(rawTreeText) as Object;
  return JSON.stringify({
    schema_version: 1,
    source: 'ARKUI_UI_CONTEXT',
    api: 'getFilteredInspectorTreeById',
    capture_mode: 'OHOS_TEST_BRIDGE',
    bridge_contract: BRIDGE_CONTRACT,
    raw_tree: rawTree
  });
}
