export interface UiTestRequiredComponent {
  component_id: string;
  source_type: string;
  arkts_type: string;
  locator_strategy: string;
  locator_value: string;
  expected_text: string;
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
  result_directory: string;
}

export const UI_TEST_PAGE_PROBES: Array<UiTestPageProbe> = __UI_TEST_PAGE_PROBES__;
