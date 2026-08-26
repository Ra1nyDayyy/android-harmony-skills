import { describe, it, expect } from '@ohos/hypium';
import { Component, Driver, ON } from '@kit.TestKit';
import { fileIo } from '@kit.CoreFileKit';
import { abilityDelegatorRegistry } from '@kit.TestKit';
import { UI_TEST_PAGE_PROBES, UiTestPageProbe, UiTestRequiredComponent } from './UiTestPageProbeRegistry';
import { UiTestRunBinding } from './UiTestRunBinding';

const RUN_BINDING_PARAMETER_KEYS = [
  'ui_test_test_hap_sha256',
  'ui_test_final_hap_sha256',
  'ui_test_device_identity_sha256',
  'ui_test_command_sha256'
] as const;

// The host harness computes the four frozen runner hashes before invoking
// `aa test`, then passes them as hypium `-s` parameters (one per key below).
// Compile-time constants cannot carry test_hap_sha256 because it would make
// the artifact reference its own digest; injecting at run time keeps every
// value verifiable against the host-measured file while staying fail-closed:
// any missing or malformed parameter still throws here.
function frozenRunBinding(): UiTestRunBinding {
  const parameters = abilityDelegatorRegistry.getArguments().parameters;
  const values = RUN_BINDING_PARAMETER_KEYS.map((key): string => {
    const raw = parameters ? (parameters as Record<string, Object>)[key] : undefined;
    return typeof raw === 'string' ? raw : '';
  });
  const [test_hap_sha256, final_hap_sha256, device_identity_sha256, command_sha256] = values;
  return {
    test_hap_sha256, final_hap_sha256, device_identity_sha256, command_sha256
  };
}

function requireFrozenHash(value: string, label: string): void {
  if (!/^[0-9a-f]{64}$/.test(value) || /^0+$/.test(value)) {
    throw new Error(`UiTest run binding is not frozen: ${label}`);
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
    trace.push({
      subject_type: 'EVENT', subject_id: action['event_id'], action: 'CLICK',
      component_id: action['component_id'],
      observable_result: 'click dispatched; functional result requires independent assertions'
    });
    for (const transition of probe.declared_transitions) {
      if (transition['event_id'] !== action['event_id']) {
        continue;
      }
      for (const target of transition['target_components']) {
        const matcher = target['locator_strategy'] === 'ID'
          ? ON.id(target['locator_value']) : ON.text(target['locator_value']);
        const targetMatches = await driver.findComponents(matcher);
        if (targetMatches.length !== 1) {
          throw new Error(`Transition target locator is not unique: ${transition['transition_id']}`);
        }
      }
      await driver.pressBack();
      for (const source of probe.required_components) {
        await oneRequiredComponent(driver, source);
      }
      trace.push({
        subject_type: 'TRANSITION', subject_id: transition['transition_id'],
        action: 'CLICK_TARGET_AND_PRESS_BACK',
        observable_result: 'target and returned source components uniquely matched by UiTest'
      });
    }
  }
}

async function captureProbe(probe: UiTestPageProbe): Promise<void> {
  const binding = frozenRunBinding();
  requireFrozenHash(binding.test_hap_sha256, 'test_hap_sha256');
  requireFrozenHash(binding.final_hap_sha256, 'final_hap_sha256');
  requireFrozenHash(binding.device_identity_sha256, 'device_identity_sha256');
  requireFrozenHash(binding.command_sha256, 'command_sha256');
  const driver = Driver.create();
  const root = resultRoot(probe);
  fileIo.mkdirSync(root, true);
  // The frozen external NAVIGATE adapter has already placed the app at target_id.
  // This test never treats a Phase 3 target ID as an ArkUI router URL.
  const operationTrace: Array<Object> = [];
  const componentSnapshots: Array<Object> = [];
  for (const required of probe.required_components) {
    const component = await oneRequiredComponent(driver, required);
    componentSnapshots.push({
      component_id: required.component_id,
      type: await component.getType(),
      text: await component.getText(),
      bounds: await component.getBounds(),
      visible: true,
      visibility_basis: 'UNIQUE_MATCH_AND_VALID_BOUNDS',
      enabled: await component.isEnabled(),
      clickable: await component.isClickable(),
      locator_strategy: required.locator_strategy,
      locator_value: required.locator_value,
      match_count: 1
    });
  }
  const screenshotPath = `${root}/ui-test-snapshot.png`;
  const resultPath = `${root}/ui-test-snapshot.json`;
  const screenshotCaptured = await driver.screenCap(screenshotPath);
  if (!screenshotCaptured) {
    throw new Error('UiTest screenshot capture failed');
  }
  writeJson(resultPath, { probe_id: probe.probe_id, components: componentSnapshots });
  await executeDeclaredActions(driver, probe, operationTrace);
  writeJson(`${root}/ui-test-snapshot-operation-trace.json`, operationTrace);
  writeJson(`${root}/ui-test-snapshot-metadata.json`, {
    probe_id: probe.probe_id, page_id: probe.page_id, state_id: probe.state_id,
    screenshot_path: 'ui-test-snapshot.png', result_path: 'ui-test-snapshot.json',
    test_hap_sha256: binding.test_hap_sha256,
    final_hap_sha256: binding.final_hap_sha256,
    device_identity_sha256: binding.device_identity_sha256,
    command_sha256: binding.command_sha256
  });
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
