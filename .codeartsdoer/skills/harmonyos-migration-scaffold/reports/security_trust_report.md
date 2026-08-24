# Security Trust Report

- OK: `True`
- Scanned files: `41`
- Scripts: `6`
- Internal script modules: `1`
- Secret findings: `0`
- Network-capable scripts: `0`
- Network policy covered scripts: `0`
- Network policy missing scripts: `0`
- File-write scripts: `5`
- Permission approvals: `2 / 2`
- Permission approval gaps: `0`
- CLI help smoke checked: `0`
- CLI help smoke failures: `0`
- Interactive scripts: `0`
- Package hash scope: `source-contract-without-generated-reports`
- Package hash files: `41`
- Package SHA256: `f7ed98e672cbe56fba575b3b025b04299efc4237424f707fa116577ae120cb10`

## Failures

- None

## Warnings

- None

## Dependency Evidence

- Files: `requirements-ci.txt`
- Pinned entries: `0`
- Unpinned entries: `0`

## Network Policy

- Policy file: `security/network_policy.json`
- Present: `False`
- Covered scripts: `0`
- Missing scripts: `none`
- Mismatches: `0`

## Permission Governance

- Policy file: `security/permission_policy.json`
- Present: `True`
- Required capabilities: `file_write, subprocess`
- Approved capabilities: `file_write, subprocess`
- Missing approvals: `none`
- Invalid approvals: `none`
- Expired approvals: `none`

## CLI Help Smoke

- Enabled: `False`
- Timeout seconds: `5.0`
- Checked scripts: `0`
- Passed scripts: `0`
- Failed scripts: `none`

## Script Surface

| Script | Interface | Declared | Argparse | Main Guard | Input | Network | File Write | Subprocess | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| scripts\_common.py | internal-module | True | False | False | False | False | True | True | Shared deterministic library imported by Phase 3 scaffold CLIs. |
| scripts\freeze_environment.py | cli | False | True | True | False | False | False | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\init_scaffold.py | cli | False | True | True | False | False | True | True | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\manage_stage3_rework.py | cli | False | True | True | False | False | True | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\run_verification.py | cli | False | True | True | False | False | True | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\validate_stage3.py | cli | False | True | True | False | False | True | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
