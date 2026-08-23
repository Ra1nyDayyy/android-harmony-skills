# Android CLI procedure

## Mandatory tool policy

Formal runtime evidence must come from Android CLI. Layout Inspector cannot create or supplement formal evidence. If Android CLI is unavailable or incompatible, mark Phase 2 `BLOCKED`.

Preflight records the actual local help and version before relying on syntax:

```text
android --version
android describe --project_dir=<project-root>
android run --help
android layout --help
android screen --help
```

Core operations:

```text
android run --device=<device-serial> ...
android layout --device=<device-serial> --pretty -o=<layout.json>
android layout --device=<device-serial> --diff --pretty -o=<layout-diff.json>
android screen capture --device=<device-serial> -o=<screenshot.png>
```

Before formal capture, run `adb devices -l` and require the assigned serial to appear exactly once with status `device`; use `adb -s <serial> shell dumpsys activity activities` to bind the resumed foreground package to the frozen application ID. Android CLI 1.0 may print `Error:` while returning exit code 0, so treat error output, missing artifacts, and timeouts as failures in addition to nonzero exit codes.

## State procedure

1. Start from the frozen seed data, account, network, permission, and display settings.
2. Run or reset the app using the documented journey.
3. Execute each action exactly as written; use Android CLI layout output to locate state and ADB only to perform required input.
4. For a transition, obtain `layout --diff` immediately after the action, before another layout request consumes the diff state.
5. Obtain a full layout and PNG screenshot.
6. Visually inspect the screenshot before submitting it for formal capture.
7. Record every actual command, device check, source-revision check, result, and timestamp.

For WebView or animation states where layout is incomplete, preserve the incomplete layout result and screenshot, mark the limitation, and open `PENDING_CONFIRMATION`. Do not switch tools silently.
