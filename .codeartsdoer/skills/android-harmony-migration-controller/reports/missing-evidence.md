# Missing evidence

Status: `missing evidence`

- The upstream YAO umbrella CLI was not executed because its evidence store imports Unix-only `fcntl` on this Windows host. Individual cross-platform YAO checks are used instead.
- No provider-backed model execution or token telemetry was collected for these output fixtures.
- Blind A/B packs are generated fixtures; reviewer decisions are pending.
- CodeArts worker task IDs are structurally recorded but are not authenticated against a CodeArts service API by this package.
- Real Android/HarmonyOS device evidence belongs to each migration run and cannot be inferred from Skill governance reports.
