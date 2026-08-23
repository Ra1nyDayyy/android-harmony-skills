# HarmonyOS emulator evidence

Formal evidence is captured only after installing the sealed `HBUILD-ID` artifact on the frozen Phase 3 emulator selected by `H4ENV-ID`.

## Required command sequence

1. Verify the exact emulator selector and device identity.
2. Clean-install the sealed artifact.
3. Reset seed/account state.
4. Apply network profile.
5. Apply permission profile.
6. Launch the app.
7. Navigate by the documented user intent.
8. Run business/data/interaction assertions.
9. Capture a PNG from the emulator.
10. Use the test-only Bridge to capture an ArkUI `UIContext` Inspector tree.

Every device-bound command contains the frozen selector tokens. Preview, design canvas, static render, manually resized image, or a screenshot from another device is invalid.

## Evidence package

Each immutable `evidence/<H4ENV-ID>/<Feature-ID>/<Page-ID>/<State-ID>/<HEVD-ID>/` contains:

- `screenshot.png`;
- `ui-tree.json`;
- `steps.md`;
- `assertions.json`;
- sanitized command logs;
- `metadata.json`;
- `manifest.sha256` and `COMMITTED`.

Metadata binds Phase 1 scope, Android Inventory/Evidence IDs, Phase 3 target, H4ENV/HDEVICE, HBUILD artifact and source snapshot, asset and decision IDs, executor, dimensions, commands, and timestamps.

Screenshot comparison covers existence, source asset, shape, color, geometry, spacing, typography, and state. Functional assertions cover business outcome, interaction, data result, error/offline behavior, permissions, and every frozen semantic obligation. Each assertion declares a deterministic operator such as `EQUALS`, `CONTAINS`, `REGEX`, `JSON_EQUALS`, or `NUMERIC_RANGE`; the capture script recomputes the verdict and rejects a false external `PASS`.

The plan also contains exactly one `ANDROID_EXPECTED_OBSERVABLE` assertion whose expected value and Inventory-ID come directly from the migration-unit contract. Follow [arkui-inspector-evidence.md](arkui-inspector-evidence.md): the external adapter supplies the raw `UIContext` Inspector tree, while the repository derives `root`, `nodes`, geometry, and source-component bindings. Every required event or transition has one `operation_trace` record whose before/after values are also Inspector envelopes. A hand-written node list or self-declared event ID cannot satisfy the gate. Missing controls, wrong page/dialog/sheet shape, lost navigation, or uncovered side effects fail before visual review. A screenshot alone never proves functional parity.

Executables located in a Skill `tests` directory, including `fake_harmony.py`, are rejected for formal evidence. `ANDROID_HARMONY_TEST_FIXTURES=1` exists only for this repository's offline unit tests; never set it during a migration run.
