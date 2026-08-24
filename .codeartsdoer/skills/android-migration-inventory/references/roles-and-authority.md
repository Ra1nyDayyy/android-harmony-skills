# Roles and authority

Each heading below represents a separately dispatched worker task, not a persona label that one worker may switch between. Every assigned actor returns its real CodeArts task ID and at least one hashable role-owned artifact to the controller. Reused task IDs, missing receipts, or one worker acting under multiple actor IDs block the Phase 3 handoff.

## Android inventory lead

- Accept the controller work order, assign exclusive feature/page ranges, and freeze the environment registry.
- Arbitrate factual conflicts. When two environments conflict unexpectedly, the frozen baseline `ENV-ID` is authoritative; mark the other result `PENDING_CONFIRMATION` and route rework.
- Create a new `ENV-ID` for any changed environment. Never edit an environment after capture.
- Cannot close the evidence chain.

## Code-map agent

Map modules, build variants, activities, fragments, Compose destinations, routes, deep links, services, receivers, providers, resources, and state candidates to file/line references. Archive every real in-scope asset from the frozen project through `archive_assets.py`; do not invent a file for `NONE_FOUND`. Label unreachable or apparently dead code as a candidate, not a fact, until runtime corroboration.

## Runtime-state agent

Use Android CLI and the assigned device to execute exact journeys. Treat every observable state separately: default, loading, empty, success, error, offline, permission denied, unauthenticated, role-specific, and feature-flagged states where applicable.

The runtime-state agent navigates but does not issue evidence IDs or declare closure.

## Business-rule agent

Document entry conditions, validation, calculations, roles, feature flags, retries, duplicate actions, failure handling, and state transitions. Cite code/tests and link the applicable state IDs.

## Data-dependency agent

Document backend APIs, authentication/session handling, databases, preferences, files, caches, data migrations, permissions, notifications, background work, camera, location, sharing, WebView, third-party SDKs, and native libraries. Purely non-visual capabilities belong in dependency catalogs, not fabricated page records.

## Evidence administrator

- Sole role authorized to invoke formal capture and seal an `Evidence-ID`.
- Maintains paths, hashes, metadata, and the evidence index.
- Cannot decide whether evidence proves the claim.
- Its actor ID is frozen by the controller; formal capture rejects any other issuer ID.

## Coverage checker

- Sole final reviewer of content coverage and evidence-chain closure.
- Cannot edit source claims or sealed evidence.
- Opens rework and re-runs closure validation after the responsible agent fixes the source material.
- Reviews the one-to-one asset inventory/package/reference chain before assets receive `REVIEWED`.
- Its actor ID is frozen by the controller. The validator rejects blank, substituted, lead, controller, issuer, or collector identities.
