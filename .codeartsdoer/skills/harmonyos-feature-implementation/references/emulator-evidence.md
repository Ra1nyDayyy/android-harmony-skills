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
10. Capture a nonempty UI/semantics tree.

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

Screenshot comparison covers existence, source asset, shape, color, geometry, spacing, typography, and state. Functional assertions cover business outcome, interaction, data result when applicable, error/offline behavior, and permissions when applicable. A screenshot alone never proves functional parity.
