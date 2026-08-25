# Environment and toolchain contract

## Immutable environment layout

Each environment is stored once:

```text
environments/
├── henv-registry.csv
└── <HENV-ID>/
    └── harmony-environment.json

verification/
└── <HVER-ID>/
    └── deveco-preflight-report.json
```

Changing any frozen field requires a new `HENV-ID`. Never edit or replace an existing environment file. The HENV freezes what must be run; it does not claim that the tools or devices passed. The executed `TOOLCHAIN`, `DEVICE`, `BUNDLE_CHECK`, and `SIGNING_CHECK` records are summarized inside that run's immutable HVER as `deveco-preflight-report.json`.

Only the architecture lead ID frozen in the controller's Phase 3 work order may create and freeze a HENV. `freeze_environment.py` rejects a `CLOSED` Phase 3 workspace and writes the environment JSON read-only after hashing it.

The environment must identify:

- DevEco Studio, HarmonyOS SDK/API, compatible API, build tool, package manager, runtime, host OS, and host architecture;
- bundle name, product, build mode, and dependency-lock hash;
- target device classes plus a baseline device and every required device, including model, serial, OS/API, and resolution;
- signing configuration reference, certificate alias/fingerprint/expiry, and external secret-storage reference;
- creator and timestamp;
- a category contract for each of the nine required verification categories.

Exactly one baseline device is required, and Phase 3 requires that baseline to be a frozen emulator with `required: true` and `screenshot_required: true`. Additional devices may also require screenshots. Every screenshot-required device must be a required emulator with frozen model, serial, OS/API, and resolution. A missing required device or emulator is `BLOCKED`.

**Screen parity with Phase 2 (required):** the baseline device's `resolution` and `density` must be **identical** to the frozen Android Phase 2 evidence device used for the same Page-IDs (Phase 2 `environment-contract` resolution/density and each evidence row's resolution). Phase 3 initialization rejects a baseline whose resolution/density differs from the Android evidence; any mismatch is `BLOCKED - screen-parity` and must be fixed by re-fixing the emulator, never by rescaling screenshots.

## Secrets

Environment JSON and project files must not contain password, passphrase, token, secret, credential material, private-key text, or raw signing-key contents. Keep secret values in an authorized external store and record only a reference. Command output is sanitized before it is sealed.

## Command-line evidence

Formal verification must run command arrays without a shell. The verification plan must cover:

- `TOOLCHAIN`
- `DEVICE`
- `BUNDLE_CHECK`
- `SIGNING_CHECK`
- `CLEAN_BUILD`
- `INSTALL`
- `LAUNCH`
- `ROUTE_SMOKE`
- `SCREENSHOT_CAPTURE`

`toolchain.category_contracts` covers **exactly** those nine categories. Each contract freezes:

- the executable supplied in `executable`, resolved at freeze time to `resolved_executable`;
- the SHA-256 of that exact executable file;
- required argument tokens;
- stable success-output markers;
- error-output markers that invalidate the result even when a command exits with code 0.

The executable must exist, be executable, and not be a symbolic link. `allowed_executables` is derived from the nine resolved contracts; it is not an independent allow-list. Required argument tokens are exact argument-array elements, and every frozen success marker must appear in command output. A listed error marker fails the command even when its exit code is 0. A verifier may not relabel an arbitrary allowed program as `CLEAN_BUILD`, `INSTALL`, `LAUNCH`, or any other category.

Commands execute in the fixed category order. Toolchain, signing, and clean build occur once; device, bundle, install, launch, and route/surface smoke cover every required device. Device-bound commands must carry the exact frozen serial in `argv`. Screenshot capture occurs after the cited smoke command on the same screenshot-required emulator.

Each command records its exact argument array, working directory, timestamps, exit code, sanitized stdout/stderr, executable hash, contract tokens, and output-marker matches. The screenshot output path must be new and present exactly in `argv`. Its result must be a complete emulator-frame PNG whose chunk CRCs, compressed image data, and dimensions match the frozen resolution; orientation reversal is allowed. Desktop crops, editor screenshots, DevEco panels, pre-existing files, and manually copied PNGs are supporting context only, never formal proof.

“Bundle conflict free” must name its verification scope. `LOCAL_DEVICE` proves only local installation identity. `PLATFORM_REGISTRY` requires an authorized platform response. Do not claim global uniqueness from a local check.
