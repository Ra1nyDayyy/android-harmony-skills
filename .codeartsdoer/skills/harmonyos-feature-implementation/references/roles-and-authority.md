# Roles and authority

Every role below is a separately dispatched CodeArts worker task. Each worker returns a unique platform task ID and hash-bound role-owned artifacts. Distinct actor strings without distinct execution receipts are invalid, and a worker may not review, verify, or accept work it implemented.

## Phase 4 implementation lead

Freezes feature work orders, dependencies, and code ownership, and confirms every rework open/close operation. The verification executor creates HBUILDs, the parity acceptance agent selects the final per-environment build set, and problem type determines rework routing. The lead may arbitrate implementation placement but cannot change frozen Android facts or approve observable parity deviations.

## Feature owner

One owner is accountable for one `Feature-ID`. It integrates UI, business/data, native capabilities, assets, and tests. It cannot accept its own feature.

## HarmonyOS UI agent

Implements every frozen Page/State presentation and interaction. It follows the accepted Phase 3 public-UI records, uses registered assets, cites real source files and symbols, and cannot invent business rules or silently replace Android-owned visuals.

## Business and data agent

Implements validation, state machines, API calls, authentication/session behavior, persistence, cache, offline handling, failures, retries, and data results. It cannot use fake production data or alter architecture landing points.

## Native-capability agent

Implements real HarmonyOS adapters for permissions, notifications, files, camera, location, sharing, background work, WebView, SDKs, and other capability contracts. No-op and fixed-return adapters are prohibited.

## Visual-asset agent

Is the only role that migrates registered Android visual files. It byte-copies compatible assets, records deterministic conversions, and never redraws or regenerates an existing asset.

## Emulator verification executor

Runs the exact sealed build on the frozen emulator, prepares the source environment profile, executes the state journey, captures assertions/UI tree/PNG, and seals evidence. It cannot implement or accept the reviewed feature.

## Parity acceptance agent

Is the sole final reviewer. It visually opens every screenshot, checks functional/data assertions, verifies asset provenance and nativeization decisions, opens or closes rework, and never edits code, claims, or evidence.

The implementation lead, asset agent, emulator verification executor, and parity acceptance agent IDs are distinct and frozen by the controller work order. For each feature, its owner, UI agent, business/data agent, and native-capability agent are also mutually distinct and cannot reuse any of those four governance IDs.
