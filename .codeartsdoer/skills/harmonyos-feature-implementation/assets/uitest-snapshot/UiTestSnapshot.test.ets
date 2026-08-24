import { describe, it, expect } from '@ohos/hypium';
import { Driver, ON, Component } from '@ohos.UiTest';
import { router } from '@kit.ArkUI';
import { fileIo } from '@kit.CoreFileKit';
import { abilityDelegatorRegistry } from '@kit.TestKit';
import { UI_TEST_PAGE_PROBES, UiTestPageProbe, UiTestRequiredComponent } from './UiTestPageProbeRegistry';
import { UI_TEST_RUN_BINDING } from './UiTestRunBinding';

function requireFrozenHash(value: string): void {
  if (!/^[0-9a-f]{64}$/.test(value) || /^0+$/.test(value)) {
    throw new Error('UiTest run binding is not frozen');
  }
}

function resultRoot(probe: UiTestPageProbe): string {
  const context = abilityDelegatorRegistry.getAbilityDelegator().getAppContext();
  return `${context.filesDir}/${probe.result_directory}`;
}

function writeJson(path: string, value: Object): void {
  fileIo.writeTextSync(path, JSON.stringify(value));
}

async function oneRequiredComponent(driver: Driver,
  required: UiTestRequiredComponent): Promise<Component> {
  const matcher = required.locator_strategy === 'ID'
    ? ON.id(required.locator_value) : ON.text(required.locator_value);
  const matches = await driver.findComponents(matcher);
  if (matches.length === 0) {
    throw new Error(`Required component missing: ${required.component_id}`);
  }
  if (matches.length !== 1) {
    throw new Error(`Required component locator is not unique: ${required.component_id}`);
  }
  return matches[0];
}

async function executeDeclaredActions(driver: Driver, probe: UiTestPageProbe,
  trace: Array<Object>): Promise<void> {
  for (const action of probe.declared_actions) {
    if (action['action'] !== 'CLICK') {
      throw new Error(`Unsupported frozen action: ${action['action']}`);
    }
    const matches = await driver.findComponents(ON.id(action['component_id']));
    if (matches.length !== 1) {
      throw new Error(`Frozen action locator is not unique: ${action['component_id']}`);
    }
    await matches[0].click();
    trace.push({ action: 'CLICK', component_id: action['component_id'] });
  }
}

async function captureProbe(probe: UiTestPageProbe): Promise<void> {
  requireFrozenHash(UI_TEST_RUN_BINDING.test_hap_sha256);
  requireFrozenHash(UI_TEST_RUN_BINDING.final_hap_sha256);
  requireFrozenHash(UI_TEST_RUN_BINDING.device_identity_sha256);
  requireFrozenHash(UI_TEST_RUN_BINDING.command_sha256);
  const driver = Driver.create();
  const root = resultRoot(probe);
  fileIo.mkdirSync(root, true);
  const operationTrace: Array<Object> = [{
    action: 'NAVIGATE', target_id: probe.target_id,
    entry_condition: probe.entry_condition, action_summary: probe.action_summary
  }];
  await router.pushUrl({ url: probe.target_id });
  const componentSnapshots: Array<Object> = [];
  for (const required of probe.required_components) {
    const component = await oneRequiredComponent(driver, required);
    componentSnapshots.push({
      component_id: required.component_id,
      type: await component.getType(),
      text: await component.getText(),
      bounds: await component.getBounds(),
      visible: await component.isVisible(),
      enabled: await component.isEnabled(),
      clickable: await component.isClickable(),
      locator_strategy: required.locator_strategy,
      locator_value: required.locator_value
    });
  }
  await executeDeclaredActions(driver, probe, operationTrace);
  const screenshotPath = `${root}/ui-test-snapshot.png`;
  const resultPath = `${root}/ui-test-snapshot.json`;
  await driver.screenCap(screenshotPath);
  writeJson(`${root}/ui-test-snapshot-operation-trace.json`, operationTrace);
  writeJson(`${root}/ui-test-snapshot-metadata.json`, {
    probe_id: probe.probe_id, page_id: probe.page_id, state_id: probe.state_id,
    screenshot_path: screenshotPath, result_path: resultPath,
    test_hap_sha256: UI_TEST_RUN_BINDING.test_hap_sha256,
    final_hap_sha256: UI_TEST_RUN_BINDING.final_hap_sha256,
    device_identity_sha256: UI_TEST_RUN_BINDING.device_identity_sha256,
    command_sha256: UI_TEST_RUN_BINDING.command_sha256
  });
  writeJson(resultPath, { probe_id: probe.probe_id, components: componentSnapshots });
  expect(componentSnapshots.length).assertEqual(probe.required_components.length);
}

export default function uiTestSnapshotSuite(): void {
  describe('ui-test-snapshot', () => {
    for (const probe of UI_TEST_PAGE_PROBES) {
      it(probe.probe_id, 0, async () => {
        await captureProbe(probe);
      });
    }
  });
}
