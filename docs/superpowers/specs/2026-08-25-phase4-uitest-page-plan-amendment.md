# Phase 4 UiTest and ArkTS Page-Plan Amendment

This amendment replaces the original Phase 4 Task 3 and Task 4 design. The replaced design is forbidden and must not be implemented.

## Binding decision

Phase 4 uses HarmonyOS UiTest APIs imported from `@kit.TestKit` only. It must not call tree-dump APIs, ship a tree-dump bridge, or treat a model-authored UI description as evidence.

Phase 2 frozen JSON, CSV, page acceptance contracts, and evidence are the only Android truth. Every page work order binds one `ui_understanding_agent_id`. By default it is the exclusive page owner; if explicitly separated, it remains unique to that Page-ID and cannot be reused by another page.

## Mandatory pre-code artifact

Before ArkTS page code is written, the system compiles and validates one immutable `arkts-page-plan.json` for each Page-ID. The plan conserves, without omission or reinterpretation:

- carrier and Phase 3 targets;
- components and stable ArkTS test tags;
- source geometry;
- text and assets;
- every State-ID and entry/action record;
- events, actions, and transitions;
- side effects and capability dependencies;
- Android source references and evidence hashes.

The compiler may use only repository-defined deterministic Android-to-ArkUI type mappings. Unknown component types, missing fields, duplicate or non-unique locators, carrier substitution, deleted records, conflicting targets, and unmapped facts fail before code generation. The page agent cannot edit the frozen source section or conservation hashes.

## UiTest snapshot evidence

Repository-managed tests are generated only under `entry/src/ohosTest`. For every Page-ID multiplied by State-ID, a UiTest case:

1. reaches the frozen state through a hash-bound external launch/deep-link/navigation adapter or a deterministically compiled UiTest entry sequence; a Phase 3 target ID is never assumed to be an ArkUI router URL;
2. finds each required component by a unique stable test tag, with frozen text fallback only when explicitly unique;
3. reads component type, text, bounds, enabled, and clickable state; visible is true only when a unique match with valid bounds exists, because the supported `Component` API has no `isVisible()` method;
4. captures the frozen State-ID screenshot before any action; actions cannot be serially accumulated into that state result;
5. executes at most one isolated action after the state snapshot. A transition passes only after the real source action, unique target-component observation, return action, and unique source-component re-observation; missing event/action/target/back facts block generation;
6. records screenshot and result paths;
7. binds test-package hash, final-HAP hash, device identity hash, and command hash supplied by the frozen runner;
8. writes only `ui-test-snapshot*` evidence names.

Missing pages, states, required components, binding hashes, or non-unique locators fail closed. Probe code never enters `src/main`.

Task 4 is consequently the build/install/run/pull adapter for these UiTest snapshots. It must verify the generated probe manifest, final application HAP, test HAP, device, command, and complete Page-ID/State-ID result set.
