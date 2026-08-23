# Review and rework

## Final review

The parity acceptance agent verifies:

- every frozen Android parity row has one accepted HEVD on every required composite environment;
- every HEVD uses the selected final HBUILD and exact current source snapshot;
- emulator PNG, UI tree, assertions, steps, logs, metadata, and hashes agree;
- UI and functional results match the cited Android evidence;
- source assets are reused and every exception is approved;
- all capability requirements have real adapters and evidence;
- there are no placeholders, fake production data, no-op adapters, pending decisions, or open rework;
- implementers/executors are separated from the final reviewer.

## Routing

The first three rows are upstream blockers: stop the current Phase 4 and return through the controller. They are not accepted as local `manage_stage4_rework.py` problem types. The remaining rows are local Phase 4 routes.

| Problem | Return to |
| --- | --- |
| Scope, account, seed, environment conflict | Phase 1 controller |
| Android state, rule, dependency, evidence, or asset fact | Phase 2 inventory lead |
| Module, route, surface, public UI, contract, asset landing | Phase 3 architecture lead |
| Feature integration or source | Phase 4 feature owner |
| UI, visual, interaction | Phase 4 UI agent |
| Business, data, state | Phase 4 business/data agent |
| Native capability or permission | Phase 4 native-capability agent |
| Asset copy/conversion/provenance | Phase 4 visual-asset agent |
| Build, install, device, environment, screenshot, UI tree, assertion, evidence | Phase 4 verification executor |

Only the frozen parity acceptance agent opens or closes a Phase 4 ticket, and the implementation lead must confirm both operations. The script writes the local ticket and controller mirror as one double-ledger update; any status other than `CLOSED` blocks Phase 4. A close requires a newer, read-only, sealed PASS `HBUILD-ID` and a newer read-only `HEVD-ID` captured by the frozen verification executor. Never edit sealed evidence.

`review_parity.py --decision REWORK` and ticket creation are two explicit actions. Open the ticket after the failed review:

```bash
python3 scripts/manage_stage4_rework.py \
  --workspace <phase-04-workspace> --action open \
  --reviewer <parity-acceptance-agent-id> \
  --confirmed-by <implementation-lead-id> \
  --ticket-id <new-ticket-id> --feature-id <Feature-ID> \
  --problem-type <fixed-problem-type> \
  --parity-or-record-id <PAR-ID-or-record-ID> \
  --failed-evidence-id <failed-HEVD-ID> \
  --severity <CRITICAL|HIGH|MEDIUM|LOW> \
  --reason <observed-problem> \
  --completion-condition <measurable-pass-condition>
```

Supported problem types are `FEATURE`, `INTEGRATION`, `SOURCE`, `UI`, `VISUAL`, `INTERACTION`, `BUSINESS`, `DATA`, `STATE`, `NATIVE`, `CAPABILITY`, `PERMISSION`, `ASSET`, `PROVENANCE`, `CONVERSION`, `BUILD`, `INSTALL`, `DEVICE`, `ENVIRONMENT`, `SCREENSHOT`, `UI_TREE`, `ASSERTION`, and `EVIDENCE`. The type fixes the responsible role; free-form routing is rejected.

After correction, create a new HBUILD and HEVD, then close:

```bash
python3 scripts/manage_stage4_rework.py \
  --workspace <phase-04-workspace> --action close \
  --reviewer <parity-acceptance-agent-id> \
  --confirmed-by <implementation-lead-id> \
  --ticket-id <ticket-id> \
  --resolution-build-id <new-HBUILD-ID> \
  --resolution-evidence-id <new-HEVD-ID>
```

`BUILD`, `INSTALL`, `DEVICE`, and `ENVIRONMENT` tickets require the build argument explicitly; other types may derive it from the new HEVD. Review the parity again after closing. The new accepted HREV supersedes the earlier failed review.

## Closure

`PASS` creates an acceptance ledger, gate report, full workspace snapshot manifest, and `CLOSED`. Any later file change makes the snapshot invalid. The closed workspace rejects new builds and captures.
