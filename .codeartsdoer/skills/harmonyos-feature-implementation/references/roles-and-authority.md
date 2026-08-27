# Roles and authority

Every role below is a separately dispatched CodeArts worker task. Each worker returns a unique platform task ID and hash-bound role-owned artifacts. Distinct actor strings without distinct execution receipts are invalid, and a worker may not review, verify, or accept work it implemented.

## Phase 4 implementation lead

Freezes page and shared-capability work orders, dependencies, and code ownership, and confirms every rework open/close operation. The verification executor creates HBUILDs, the acceptance agent selects the final per-environment build set, and problem type determines rework routing. The lead may arbitrate implementation placement but cannot change frozen Android facts or approve observable parity deviations.

## Page owner

One owner is accountable for one `Page-ID`. It integrates the page UI, in-page business/data behavior, tests, and asset usage, and consumes shared capabilities through their frozen contracts. The page owner may also act as that page's UI understanding/conversion agent; it cannot serve another page and cannot accept its own page.

## Per-page UI understanding and conversion agent

Implements every frozen Page/State presentation and interaction for exactly one page. It follows the accepted Phase 3 public-UI records, uses registered assets, cites real source files and symbols, and cannot invent business rules or silently replace Android-owned visuals. It reads only the frozen Phase 2 page contract and the generated `arkts-page-plan.json`.

## Shared capability specialists

Implement reusable calculations, persistence, network, clipboard, files, background work, permissions, and other native capabilities under `SHARED_CAPABILITY_WORK_ORDER` records. Real HarmonyOS adapters only — no-op and fixed-return adapters are prohibited. Specialists serve pages through frozen interface contracts; they do not redesign pages.

## Visual-asset agent

Is the only role that migrates registered Android visual files. It byte-copies compatible assets, records deterministic conversions, and never redraws or regenerates an existing asset.

## Emulator verification executor

Runs the exact sealed build on the frozen emulator, prepares the source environment profile, executes the state journey, captures assertions/UI tree/PNG, and seals evidence. It cannot implement or accept the reviewed page.

## Parity acceptance agent

Is the sole final reviewer. It visually opens every screenshot, checks functional/data assertions, verifies asset provenance and nativeization decisions, opens or closes rework, and never edits code, claims, or evidence.

The implementation lead, visual-asset agent, emulator verification executor, and parity acceptance agent IDs are distinct and frozen by the controller work order. Implementer, executor, and acceptance actors never overlap: a page owner or capability specialist can neither execute the verification nor accept the parity it produced.
