export interface UiTestRequiredComponent {
  component_id: string;
  source_type: string;
  arkts_type: string;
  locator_strategy: string;
  locator_value: string;
  expected_text: string;
}

export interface UiTestLocator {
  component_id: string;
  locator_strategy: string;
  locator_value: string;
}

export interface UiTestTransition {
  transition_id: string;
  event_id: string;
  source_page_id: string;
  target_page_id: string;
  target_components: Array<UiTestLocator>;
  return_action: string;
}

export interface UiTestPageProbe {
  probe_id: string;
  page_id: string;
  state_id: string;
  target_id: string;
  entry_condition: string;
  action_summary: string;
  required_components: Array<UiTestRequiredComponent>;
  declared_actions: Array<Record<string, string>>;
  declared_transitions: Array<UiTestTransition>;
  result_directory: string;
}

export const UI_TEST_PAGE_PROBES: Array<UiTestPageProbe> = __UI_TEST_PAGE_PROBES__;
