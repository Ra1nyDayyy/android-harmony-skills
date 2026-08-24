---
name: android-migration-inventory
description: Automatically build a migration-grade semantic inventory of a frozen Android app using source discovery, Android CLI traversal, source-runtime binding, state evidence, dynamic-risk probes, and deterministic coverage gates. Use before Android-to-HarmonyOS implementation; do not write HarmonyOS code.
---

# Android Migration Inventory

Describe what the Android app actually contains and does. The unit of truth is `Feature-ID + Page-ID + State-ID + ENV-ID + Evidence-ID`, not a screenshot or page name alone.

## Non-negotiable contract

- Models never approve, create `PAGE_PASS`, or declare Phase 2 complete.
- No manual page enumeration or annotation occurs inside Phase 2.
- Every factual claim cites a frozen file/line, sealed runtime evidence, or both; otherwise it is `PENDING_CONFIRMATION`.
- Static discovery gaps, skipped source, parse errors, unsupported UI surfaces, unresolved runtime tasks, and unprobed side effects are blocking.
- Evidence and assets are immutable; recapture or supersede with new IDs.
- MP4 and Android Layout Inspector are not formal evidence.

Phase 2 automation ends at its machine Gate. The human review happens only after the machine Gate, when the controller enters `WAITING_HUMAN_REVIEW`.

## Inputs and initialization

Consume the controller run, frozen `scope.json`, and Phase 2 work order. Initialize with `scripts/init_inventory.py`; then attest frozen accounts, seed, network, permissions, APK, Git revision, Android CLI, device, and emulator using `scripts/attest_environment.py`.

Read [static-page-analysis.md](references/static-page-analysis.md), then run `scripts/analyze_static_pages.py` followed by `scripts/validate_static_analysis.py`. Do not traverse runtime until static validation succeeds.

## Automatic understanding

The source lens inventories all eligible source files and layouts, records parsed and skipped counts, and discovers:

- Activities, Fragments, Dialogs, Sheets, Compose functions regardless of naming convention, navigation resources, widgets, and custom surfaces.
- Components, hierarchy, text, geometry hints, visibility, enablement, events, states, entry conditions, and transitions.
- Assets, business rules, data dependencies, permissions, SDKs, system capabilities, reflection, dynamic loading, WebView/server-driven UI, background work, persistence, clipboard, files, and network effects.
- Special accounts, denied permissions, offline/error data, empty/loading/error states, orientation, theme, locale, and other required scenarios.

The runtime lens consumes every machine-generated task, autonomously navigates the frozen app with Android CLI, captures UI tree, screenshot, foreground package, assertions, before/after effects, and transition diffs, then binds each result back to source IDs. Missing or contradictory bindings remain explicit blockers; never infer them.

## Work separation

Use focused logical lenses for code map, runtime state, business rules, data/capabilities, evidence administration, and coverage. They may use the same approved model service, but outputs remain role-owned and independently recomputed. Record the real CodeArts task and artifact receipt required by the controller.

Archive real Android assets with `scripts/archive_assets.py`. Capture state evidence with `scripts/capture_state.py`; every package must be controller-anchored. Build normalized rows with `scripts/build_inventory.py`, bind runtime subjects with `scripts/record_runtime_observation.py`, and capture advanced probes with `scripts/capture_advanced_probe.py`.

## Machine Gate and rework

Run the deterministic page, advanced, evidence, asset, and coverage validators described in [deterministic-page-gates.md](references/deterministic-page-gates.md). The coverage reviewer may diagnose and open rework but cannot convert its opinion into `PASS`. A claim is complete only when every frozen denominator item is accounted for and every applicable environment has reproducible evidence.

Route source/runtime disagreement, missing pages, weak locator binding, dynamic surfaces, special scenarios, and side-effect probe failures through [review-and-rework.md](references/review-and-rework.md). Do not delete a blocker to improve coverage.

Return the closed workspace to the controller. The controller independently recomputes Gate 2, generates the exception-first review summary, and pauses at `WAITING_HUMAN_REVIEW`.

## Reference map

- [inventory-contract.md](references/inventory-contract.md): IDs, rows, and catalogs.
- [static-page-analysis.md](references/static-page-analysis.md): source denominator and runtime backlog.
- [android-cli-procedure.md](references/android-cli-procedure.md) and [evidence-contract.md](references/evidence-contract.md): formal runtime capture.
- [advanced-runtime-analysis.md](references/advanced-runtime-analysis.md): dynamic risks, side effects, and scenarios.
- [deterministic-page-gates.md](references/deterministic-page-gates.md) and [review-and-rework.md](references/review-and-rework.md): closure and failure routing.
