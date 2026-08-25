# Environment contract

An `ENV-ID` is an immutable set of conditions. Normal, offline, and weak-network tests use different environment IDs.
`environments.json` is bound by SHA-256 in the phase manifest and every evidence metadata file. Any byte-level change blocks capture and closure; changed conditions require a new ENV-ID.

Each environment must include:

- App version/build, build variant, source revision, application ID, and APK hash.
- Test account alias and role. Never store passwords, tokens, cookies, or one-time codes.
- Seed-data ID and reset procedure or reference.
- Network profile and whether a controlled offline/weak-network switch is available.
- Emulator/device serial, model, resolution, density, Android/API level, and orientation where relevant.

**Frozen screen parity (required):** the evidence device's `resolution` and `density` are **frozen once and reused for every Page-ID's captures** — they are the canonical screen basis for Phase 3 baseline devices and Phase 4 H4ENV parity. Phase 2 attestation must record exact `wm size`/`wm density` values, and every evidence row (runtime-evidence evidence-index) includes that resolution/density. Any emulator whose resolution/density is later changed invalidates all prior captures unless a new ENV-ID is created; Phase 4 compares screenshots against this exact `WxH/dpi` and raises `BLOCKED` on mismatch (no rescaling).
- Locale, timezone, theme, font scale, and permission profile.
- Seed reset reference, network-condition reference, and orientation.

## Manual readiness attestation

Before formal capture, the controller-frozen inventory lead confirms that the account, seed data, network conditions, and permission state for each applicable frozen environment are ready:

```bash
python3 scripts/attest_environment.py \
  --workspace <migration-run>/phase-02-android-inventory \
  --env-id <ENV-ID> \
  --inventory-lead-id <controller-frozen-inventory-lead-id> \
  --account-ready \
  --seed-ready \
  --network-ready \
  --permissions-ready \
  --notes <optional-non-secret-notes>
```

All four readiness flags are mandatory. The script verifies the current phase, scope, run-manifest, APK, and environment-registry bindings before it writes `environment-attestations/<ENV-ID>.json`. The record includes UTC time, the frozen environment's key values, the four boolean confirmations, notes, and SHA-256 digests for the scope, environment registry, and selected environment.

An ENV-ID may be attested only once. Existing records, symbolic-link paths, non-frozen environments, substituted actor IDs, and a `CLOSED` Phase 2 are rejected. Do not put passwords, tokens, cookies, one-time codes, or other secrets in `--notes`.

Exactly one environment is the baseline. When results conflict unexpectedly:

1. Retain the baseline result as the current migration fact.
2. Mark the non-baseline result `PENDING_CONFIRMATION`.
3. The coverage checker opens rework.
4. The inventory lead decides whether the difference is expected, invalid, or needs a new environment.

Expected differences such as online versus offline UI are separate state rows, not conflicts.
