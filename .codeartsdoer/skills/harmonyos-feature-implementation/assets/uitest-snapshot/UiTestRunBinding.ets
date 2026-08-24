export interface UiTestRunBinding {
  test_hap_sha256: string;
  final_hap_sha256: string;
  device_identity_sha256: string;
  command_sha256: string;
}

// Task 4 replaces these fail-closed sentinels with frozen runner hashes.
export const UI_TEST_RUN_BINDING: UiTestRunBinding = {
  test_hap_sha256: '__UNBOUND_SHA256__',
  final_hap_sha256: '__UNBOUND_SHA256__',
  device_identity_sha256: '__UNBOUND_SHA256__',
  command_sha256: '__UNBOUND_SHA256__'
};
