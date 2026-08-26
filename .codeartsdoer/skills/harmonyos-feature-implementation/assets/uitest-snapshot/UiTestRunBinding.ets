export interface UiTestRunBinding {
  test_hap_sha256: string;
  final_hap_sha256: string;
  device_identity_sha256: string;
  command_sha256: string;
}

// Frozen runner hashes are injected at run time by the host harness through
// hypium `-s` parameters (see UiTestSnapshot.test.ets frozenRunBinding()).
// Compile-time constants cannot carry test_hap_sha256 without making the
// artifact reference its own digest, so this file only exports the shape.
export const UI_TEST_RUN_BINDING_KEYS = [
  'ui_test_test_hap_sha256',
  'ui_test_final_hap_sha256',
  'ui_test_device_identity_sha256',
  'ui_test_command_sha256'
] as const;
