# Roles and authority

All six Phase 3 actor IDs are frozen in the controller-issued work order and copied into `phase-manifest.json` and `stage-03-input-lock.json`. They must be distinct from each other and from the frozen Phase 1/2 actors. A command-line `--created-by`, `--executed-by`, `--mapped-by`, or `--reviewer` value is not authority by itself; it must equal the assigned ID below.

Every role is a distinct actual CodeArts worker task. After completion, the controller records the platform task ID and hashes of role-owned artifacts. A role string, command-line actor value, or model-authored narrative is not an execution receipt. One platform task ID cannot satisfy two roles.

## HarmonyOS architecture lead

- Owns `HENV-ID` selection, module placement, dependency policy, naming, architecture decisions, and every Phase 2 asset's target module/path/symbol plan.
- Resolves placement conflicts against the frozen scope and Phase 2 input lock.
- Confirms the responsible role on every rework ticket.
- Cannot waive the Phase 3 gate or review work the lead authored.
- Is the only role allowed to freeze a HENV and initialize the Phase 3 workspace from the registered work order.

## Toolchain and scaffold agent

- Preflights command-line tools, SDK, required devices, bundle identity, and signing references.
- Creates the project, modules, products, and build configuration.
- Makes the selected source snapshot clean-build, install, and launch.
- Executes the frozen emulator screenshot command and seals the resulting `HSCREEN-ID` packages after the navigation agent positions each shell.
- Uses secure external secret storage. It may reference signing configuration but must not copy secrets into the project or reports.

## Navigation and page-shell agent

- Creates app entry, route registry, independently navigable page shells, non-route visual-surface shells, back behavior, and smoke tests.
- Positions every route or visual-surface shell on the assigned frozen emulator and supplies the exact target identity for screenshot capture.
- Preserves the original navigation kind. It must not turn a dialog, sheet, tab body, or embedded surface into a fake route.
- Does not add business controls, data, state behavior, or ViewModels.

## Public UI agent

- Creates generic color, typography, spacing, theme, page-container, loading-shell, empty-shell, error-shell, and responsive-rule artifacts.
- Does not recreate any concrete Android business page.
- Common loading, empty, and error shells remain unmounted from business placeholders in Phase 3.

## Capability-contract agent

- Creates compileable interface/type/error definitions for every seeded nonvisual requirement.
- Does not add concrete adapters, SDK calls, network/storage access, or fixed return data.

## Architecture acceptance agent

- Is the sole final reviewer.
- Must use the exact `architecture_acceptance_agent_id` frozen in the work order and therefore differs from every creator, mapper, and verification executor.
- Verifies; it never edits project files, registries, mappings, evidence, or tickets.
- Visually opens every sealed PNG; hashes, metadata, and command success alone are insufficient.
- Uses `manage_stage3_rework.py` to open or close tickets and rechecks the new immutable verification package after the original responsible agent fixes the issue.

## Fixed rework routing

| `problem_type` values | Responsible role |
| --- | --- |
| `ARCHITECTURE`, `PLACEMENT`, `ASSET`, `DEPENDENCY`, `INPUT` | HarmonyOS architecture lead |
| `TOOLCHAIN`, `BUILD`, `DEVICE`, `BUNDLE`, `SIGNING`, `INSTALL`, `LAUNCH`, `ARTIFACT`, `SCREENSHOT` | Toolchain and scaffold agent |
| `NAVIGATION`, `ROUTE`, `SURFACE`, `MAPPING`, `SMOKE` | Navigation and page-shell agent |
| `PUBLIC_UI`, `RESPONSIVE`, `THEME` | Public UI agent |
| `CAPABILITY`, `CONTRACT` | Capability-contract agent |

The problem type, not a free-form assignee, selects the frozen owner. The manager writes the same ticket into local `rework-tickets.csv` and controller `rework-log.csv`. Flow: acceptance agent opens ticket → architecture lead confirms the deterministic owner → original role corrects it → frozen toolchain agent produces a newer sealed PASS `HVER-ID` → acceptance agent closes it with that HVER. Every open ticket blocks PASS, regardless of severity. A closed workspace rejects further ticket changes.
