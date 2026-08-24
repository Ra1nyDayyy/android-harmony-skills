# Security Trust Report

- OK: `True`
- Scanned files: `33`
- Scripts: `12`
- Internal script modules: `2`
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
- Package hash files: `33`
- Package SHA256: `61e04deb9c3db3015796ac6962eed11eb3b221fb768bf1852106bbc87532f060`

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
| scripts\_phase56_common.py | internal-module | True | False | False | False | False | True | True | Imported by Phase 5/6 controller work-order scripts; not a standalone CLI. |
| scripts\_team_execution.py | internal-module | True | False | False | False | False | False | False | Imported by controller receipt recording, phase issuance, and delivery audit scripts. |
| scripts\anchor_phase2_evidence.py | cli | False | True | True | False | False | True | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\audit_delivery.py | cli | False | True | True | False | False | False | True | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\init_migration.py | cli | False | True | True | False | False | True | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\issue_phase2_work_order.py | cli | False | True | True | False | False | True | True | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\issue_phase3_work_order.py | cli | False | True | True | False | False | True | True | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\issue_phase4_work_order.py | cli | False | True | True | False | False | True | True | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\issue_phase5_work_order.py | cli | False | True | True | False | False | False | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\issue_phase6_work_order.py | cli | False | True | True | False | False | False | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\record_team_execution.py | cli | False | True | True | False | False | True | False | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts\validate_gate.py | cli | False | True | True | False | False | True | True | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
