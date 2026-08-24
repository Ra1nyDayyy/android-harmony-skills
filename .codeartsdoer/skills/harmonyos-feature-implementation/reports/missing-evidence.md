# Missing evidence

Status: `missing evidence`

- The YAO umbrella CLI is blocked on Windows by its Unix-only `fcntl` dependency; individual cross-platform checks are used.
- Output fixtures are not provider-backed model executions and have no observed token/latency telemetry; blind-review decisions remain pending.
- CodeArts worker identities are not authenticated through an external API.
- Real DevEco builds, emulator installs, ArkUI Inspector trees, screenshots, input actions, assertions, side-effect probes, and independent parity reviews remain per-run obligations and cannot be inferred from governance evidence.
