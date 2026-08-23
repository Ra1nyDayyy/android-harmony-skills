# Scaffold boundaries

## Module truth

Every module registry row cites a real module directory and build configuration inside `harmony-project/`. Record its layer, feature IDs, and declared module dependencies. `dependency-policy.json` defines allowed layer edges and forbidden placeholder/contract tokens.

The Phase 3 gate verifies ID validity, file existence, allowed dependency directions, and absence of cycles. The acceptance agent separately attests that the registry matches actual build configuration and imports.

## Asset landing plans

`asset-registry.csv` must exactly cover the real Phase 2 assets and preserve their archive paths, hashes, types, and Feature/Page/State associations. Each `READY` row is created by the frozen architecture lead and points to an existing registered module plus a safe future path below that module's `src/main/resources/`. Target paths are unique, and resource symbols are unique within a module.

The registry is architectural placement only. Do not add copied assets, conversions, recreations, visual-token maps, or an asset policy during Phase 3; Phase 4 consumes the frozen public-UI foundation and defines its own implementation policy.

## Page and surface shells

A page shell contains only:

- a blank content area;
- a page-level navigation bar only when the Android page actually had one;
- literal Feature-ID, Page-ID, Page-Shell-ID, and Route-ID or Surface-Shell-ID metadata;
- minimum route registration, opening, and back behavior needed by smoke tests.

It must not contain business components, ViewModels, domain models, requests, persistence, fake/mock data, business state, timers, business validation, or business buttons.

`ROUTE_PAGE` requires real route registration, runtime smoke success, and a matching emulator screenshot. `VISUAL_SURFACE` requires a real component/surface file, instantiation smoke success, and a screenshot from a test-only harness without creating a fake production route. A documentation-only mapping is invalid.

## Public UI

`public-ui-registry.csv` must cover color, typography, spacing, theme, page container, loading shell, empty shell, error shell, and responsive rules. Every row cites a real file. The loading, empty, and error shell symbols must not appear in business page/surface shell files during Phase 3.

## Capability contracts

Every seeded capability requirement must have one real contract file and symbol. Contracts may contain interface/type/enum/error declarations. They may not contain concrete adapter classes, I/O, SDK calls, network/storage operations, fake data, or implemented business methods.

If compilation would otherwise require an implementation, defer wiring; do not create a no-op production adapter and call it an interface.
