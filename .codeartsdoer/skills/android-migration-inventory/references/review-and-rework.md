# Review and rework

## Final review

Only the closure validator may grant `PASS`. The coverage checker may request closure or force a non-pass result, and verifies:

- Every inventory row has the five core IDs and one real evidence package.
- Screenshot, layout, steps, metadata, hashes, and environment agree.
- Source, runtime, business-rule, data, permission, SDK, and native findings are cross-linked.
- Every real source asset is copied byte-for-byte into the committed asset package, reviewed, and referenced by the applicable Feature/Page/State rows; `NONE_FOUND` creates no file.
- Evidence references are bidirectional and there are no active orphans.
- The capture tool is Android CLI, no MP4 exists, and Layout Inspector is absent.
- The collector and final reviewer are different roles.
- Coverage ledger and all catalogs close every included feature, environment, and in-scope code state candidate.
- The closure snapshot still matches every file when the controller opens its gate.

The evidence administrator validates identity and integrity; the coverage checker reports sufficiency concerns and routes rework. Neither role can override the deterministic closure verdict.

## Rework routing

| Problem family | Responsible role |
|---|---|
| `SCOPE`, `ENV`, `CONFLICT` | Android inventory lead |
| `CODE`, `ROUTE`, `ENTRY`, `ASSET` | Code-map agent |
| `STATE`, `SCREENSHOT`, `LAYOUT`, `CLI`, `STEPS` | Runtime-state agent |
| `RULE`, `VALIDATION`, `TRANSITION` | Business-rule agent |
| `API`, `DATA`, `SDK`, `PERMISSION`, `NATIVE` | Data-dependency agent |
| `EVID`, `ID`, `HASH`, `INDEX`, `SCHEMA` | Evidence administrator |

The coverage checker uses `manage_recheck.py` to open a rework row. The inventory lead assigns one primary owner when multiple families are involved. The original role fixes the source material; recapture receives a new evidence ID. Only the frozen coverage checker can close the rework, and closure requires an active sealed resolution Evidence-ID.

Final decisions are `PASS`, `INCOMPLETE`, or `BLOCKED`. `PASS` is machine-computed and requires every deterministic page gate, every advanced runtime/probe gate, zero open reworks of any severity, and zero pending confirmations. A model-written attestation is advisory and cannot satisfy a missing atomic observation or probe.
