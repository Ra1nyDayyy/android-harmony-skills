# Verification, evidence, and rework

## Verification package

Each `verification/<HVER-ID>/` is immutable and contains:

- `metadata.json`;
- `scaffold-snapshot-manifest.json`;
- `deveco-preflight-report.json` for the actually executed toolchain/device/bundle/signing checks;
- sanitized command logs under `logs/`;
- `route-results.json` and `surface-results.json`;
- `screenshot-index.csv`;
- one immutable `screenshots/<HSCREEN-ID>/` package per captured shell;
- `artifact-manifest.json` plus immutable HAP copies under `artifacts/`;
- `manifest.sha256` and `COMMITTED`.

The source snapshot excludes generated build/cache directories but includes project source, module build configuration, lockfiles, the scaffold registries, the input lock, and the selected HENV. It deliberately excludes `rework-tickets.csv`, because the governed open → correction HVER → close lifecycle must be able to advance after a failed HVER. The rework ledger is instead checked against its controller mirror and is protected by the final Phase 3 closure. A verification ID applies only to its scaffold snapshot, one registered work order, and one `HENV-ID`. The frozen toolchain agent is the only valid `executed_by` value.

The runner creates and seals a package for both PASS and FAIL. `manifest.sha256` must exactly cover all package files other than itself and `COMMITTED`, and every path in the finished HVER is read-only. Never edit a failed HVER into a passing one; correct the project and issue a new ID.

No MP4 is required or accepted as formal evidence.

Each screenshot package contains `screenshot.png`, `metadata.json`, `manifest.sha256`, and `COMMITTED`. Metadata binds the PNG to HENV-ID, HVER-ID, HDEVICE-ID and serial, bundle, route/surface target, Feature-ID values, Page-ID, Page-Shell-ID, its preceding smoke command, capture command and executable hash, executor, timestamp, dimensions, and PNG hash. The runner validates the complete PNG stream: signature, chunks, CRC, IEND, decompression length, and frozen resolution.

Every unique route or surface shell must have one screenshot on every HENV device marked `screenshot_required`. Multiple Phase 2 state rows may cite the same `HSCREEN-ID` only when the states share the same Phase 3 shell and target. Screenshots are not created for capability-only mappings.

## Generated route and surface result records

`ROUTE_SMOKE` does not accept an already prepared result file. Its `result_output_path` must be a new, unique path below `harmony-project/` and must appear exactly in the command `argv`. The smoke command generates one direct JSON object; the runner immediately verifies and seals it. Wrapper objects such as `{ "results": [...] }` are rejected.

A generated route result must exactly bind `route_id`, `page_id`, `page_shell_id`, `device_id`, frozen device serial, frozen bundle name, and `status: PASS`. A surface result uses `surface_shell_id` with the same identity fields. Smoke coverage is required for every registered route/surface target on every required device. Results without the generating command, real registry and source files, or corresponding screenshot evidence do not count.

## Build artifact proof

The verification plan declares relative `.hap` paths. `CLEAN_BUILD` must create the HAP or change its recorded pre-run file state. The runner opens the result as a ZIP, rejects corrupt or unsafe members, and requires a Harmony module configuration (`module.json` or `config.json`). The same bytes must remain present through later commands before an immutable copy is sealed under `artifacts/`; an arbitrary pre-existing file or a renamed text file cannot pass.

## Rework

`rework-tickets.csv` records ticket ID, severity, problem type, source/mapping ID, failed HVER, responsible role and agent, architecture-lead confirmation, status, correction HVER, opener, and closer. Only `manage_stage3_rework.py` may update it. The script simultaneously mirrors the same ticket into controller `rework-log.csv` under an exclusive lock.

Only the frozen architecture acceptance agent opens or closes a ticket. The problem type deterministically routes it to a frozen role, and the architecture lead must confirm that owner. Closing requires a different, newer, read-only PASS HVER produced by the frozen toolchain agent. **No ticket of any severity may remain open at PASS.** A route, mapping, contract, dependency, build, install, launch, smoke, artifact, or screenshot failure also prevents PASS even when no ticket has yet been opened.

## Final gate

The gate report identifies:

- the exact input-lock hash;
- HENV-ID and HVER-ID;
- source snapshot and artifact hashes;
- reviewer role and ID;
- row, route, surface, contract, module, and status counts;
- all attestations, errors, warnings, and open tickets.

The acceptance agent visually opens every PNG and attests that it shows the expected emulator, blank placeholder boundary, original page-level navigation bar when applicable, Feature-ID/Page-ID identity, and correct route or surface shell. File hashes and dimensions do not replace this visual review.

The final reviewer must equal the work-order's frozen architecture acceptance ID and cannot be any creator, mapper, status updater, environment owner, or verification executor. A later fix requires a new `HVER-ID`; never edit the reviewed package.

On PASS, `validate_stage3.py` writes `stage-03-closure-manifest.sha256`, writes `CLOSED` with the SHA-256 of the final Stage 3 report, and removes write permission from the complete workspace. The closure manifest covers everything except the mutable final report, the closure manifest itself, and `CLOSED`. The controller then recomputes that complete file set and every hash at Gate 3; the Stage 3 report alone is not the controller gate.
