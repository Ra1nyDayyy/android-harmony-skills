# Advanced runtime analysis contract

## What this layer covers

Static page analysis now records three kinds of facts that ordinary UI traversal tends to miss:

- dynamic surfaces: reflection, dynamic code, feature flags, dynamic routes, WebView, and server-driven UI;
- non-UI side effects: database, preferences, files, clipboard, network, background work, notifications, and permissions;
- special scenarios: first run and returning user, empty/populated/boundary data, network failure and timeout, permission denial, missing dynamic targets, process restart, and related variants.

The scanner produces candidates, not conclusions. Each candidate is multiplied by its applicable frozen environments and becomes part of the Phase 2 denominator.

## Evidence rule

Every advanced candidate needs ordinary runtime evidence bound to a known Page-ID and ENV-ID. This proves which visible state accompanied the operation.

Side effects and scenarios also need a sealed probe package. The probe adapter records its absolute executable path, SHA-256, argv, return code, stdout, and stderr. The package contains canonical JSON snapshots from before and after the action. Depending on the declared comparator, the gate independently recomputes that the state changed, remained unchanged, or equals an expected snapshot.

This supports database queries, preference dumps, clipboard reads, captured request/response summaries, work-state queries, permission state, server fixtures, account fixtures, and similar machine-readable observations. Secrets and raw credentials must not appear in snapshots or command logs.

## Automatic special-scenario setup

Scenario setup is the responsibility of an environment adapter, not a language model. The adapter may reset app data, load a test database, switch a local mock server profile, revoke a permission, select a prepared test account, advance a clock, or restart the app process. It must then export before/after state as JSON and leave a successful, hash-bound command record.

If a required account, fixture, permission control, server profile, or device capability is unavailable, the candidate remains `BLOCKED`. Phase 2 does not ask a person to enumerate or approve the missing page.

## Trust boundary

The model may explain a candidate or propose a traversal action. It cannot grant `PASS`. `advanced-observations.json` accepts only identity and evidence references; verdict, confidence, and free-form approval fields are rejected. The gate verifies exact coverage, evidence lifecycles, page/environment identity, package hashes, adapter hashes, reproducible JSON differences, and comparator results.

This mechanism greatly improves observable coverage, but it does not make arbitrary opaque server behavior mathematically discoverable. Coverage is bounded by the frozen source, provisioned scenarios, available adapters, accounts, permissions, and runtime environments. Any unresolved candidate blocks the phase instead of being silently omitted.
