# Static page analysis contract

## Purpose

Run this analysis before Android runtime traversal. It must discover source-level page, UI, event, state, navigation, resource, and dependency candidates without human annotation. Static findings are candidates until runtime evidence confirms them.

## Discovery boundary

Scan the frozen Android revision and exclude generated/build directories. Cover:

- Gradle modules, source sets, manifests, launcher/deep-link entries, and navigation resources;
- Activity, Fragment, DialogFragment, bottom sheet, Compose destination, XML layout, programmatic view, menu, list-item, and overlay candidates;
- XML layout trees, include/merge relationships, resource references, qualifiers, styles, selectors, and Compose component calls;
- click, long-click, text-change, selection, gesture, menu, lifecycle, permission, and activity-result handlers;
- explicit and navigation-component transitions, route arguments, condition branches, and external surfaces;
- UI state branches, validation conditions, API/local-storage/system-capability calls, and third-party boundaries.

Do not treat a static guess as observed behavior. Reflection, remote configuration, WebView content, dynamic class loading, encrypted routes, server-driven UI, and unresolved dependency injection must create automatic runtime tasks.

## Required package

`scripts/analyze_static_pages.py` creates `static-analysis/` with:

- `project-index.json`: frozen project identity and scan counts;
- `pages.json`: stable Page-IDs, source symbols, page kinds, layouts, entries, feature candidates, and confidence;
- `components.json`: stable Component-IDs, hierarchy, text, size rules, position rules, state properties, event attributes, and source references;
- `events.json`: stable Event-IDs, source pages, event candidates, and handler excerpts;
- `transitions.json`: stable Transition-IDs, source/target Page-IDs, and target symbols/routes;
- `state-candidates.json`: stable State-IDs and branch expressions requiring runtime coverage;
- `runtime-tasks.json`: machine-executable verification backlog. Every task carries two frozen fields:

```json
{
  "verification_mode": "SOURCE_ONLY | RUNTIME_UI | RUNTIME_EFFECT",
  "review_tier": "REQUIRED | REVIEW"
}
```

`SOURCE_ONLY` items are settled by source and never enter an emulator queue. `RUNTIME_UI` items must be confirmed on an emulator (page/popup/control/copy/layout/state). `RUNTIME_EFFECT` items must verify a real outcome (save, delete, navigation, restart recovery, database change). `REQUIRED` tasks must run; `REVIEW` tasks (low-risk or isomorphic duplicates) may stay unverified up to 10% for human review. Default classification: page default states, core events, navigation, persistence, and system side effects are `REQUIRED`; ordinary visual-state branches and duplicate enum-style options are `REVIEW`; purely internal implementation provable from source is `SOURCE_ONLY`. No extra runtime-task CSV and no new semantic IDs are introduced;
- `advanced-analysis.json`: dynamic surface risks, non-UI side-effect candidates, and automatically expanded special scenarios;
- `code-map.candidates.csv`: rows that may be promoted to the formal code map only after source/runtime correlation;
- `manifest.sha256` and `COMMITTED`: immutable package binding.

Every page, event, transition, and state candidate must have a subject-bound runtime task. Components are verified against the page's full runtime UI tree. Every unresolved static relationship must remain an open runtime task; it must never be silently dropped or filled from convention.

## Completion rule

Static location is complete when:

1. every discovered application entry resolves to a page candidate;
2. every page has a source reference and a default-state runtime task;
3. every statically visible interactive component has an event candidate, a known platform-default behavior, or an automatic runtime-resolution task;
4. every transition has a target candidate or an automatic target-resolution task;
5. every conditional UI branch becomes a State-ID candidate;
6. package validation passes and the frozen source revision still matches.

Static completion does not close Phase 2. Runtime traversal consumes the frozen lane queues, binds final geometry and observable behavior, and promotes only evidence-backed rows to `VERIFIED`. `SOURCE_ONLY` tasks leave the emulator denominator; a `PENDING_SOURCE_BINDING` page must never reach the runtime stage (validation rejects REQUIRED tasks without a bound Page-ID and source reference).
