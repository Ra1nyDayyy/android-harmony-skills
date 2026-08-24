# Security Trust Report

- OK: `True`
- Scanned files: `43`
- Scripts: `11`
- Internal script modules: `3`
- Secret findings: `0`
- Network-capable scripts: `0`
- Network policy covered scripts: `0`
- Network policy missing scripts: `0`
- File-write scripts: `8`
- Permission approvals: `2 / 2`
- Permission approval gaps: `0`
- CLI help smoke checked: `0`
- CLI help smoke failures: `0`
- Interactive scripts: `0`
- Package hash scope: `source-contract-without-generated-reports`
- Package hash files: `43`
- Package SHA256: `156d2895b1d27c72671c40af48a454543701db3ec3943f656ed1ce64511af028`

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
| scripts\_common.py | internal-module | True | False | False | False | False | True | True | Shared deterministic library imported by Phase 4 implementation CLIs. |
| scripts\_stage4_audit.py | internal-module | True | False | False | False | False | False | False | Read-only audit library imported by the Phase 4 validator. |
| scripts\arkui_inspector.py | internal-module | True | False | False | False | False | False | False | Inspector normalization library imported by Phase 4 evidence and audit scripts. |
| scripts\capture_state.py | cli | False | True | True | False | False | True | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\convert_asset.py | cli | False | True | True | False | False | True | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\init_implementation.py | cli | False | True | True | False | False | True | True | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\issue_feature_work_order.py | cli | False | True | True | False | False | True | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\manage_stage4_rework.py | cli | False | True | True | False | False | False | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\review_parity.py | cli | False | True | True | False | False | True | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\run_build.py | cli | False | True | True | False | False | True | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\validate_stage4.py | cli | False | True | True | False | False | True | True | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
