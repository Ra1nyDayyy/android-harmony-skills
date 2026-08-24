# Task 1 Report — Immutable Page Acceptance Contracts

## Status

DONE_WITH_CONCERNS

## Files changed

- `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/page_acceptance_contract.py`
- `.codeartsdoer/skills/harmonyos-feature-implementation/assets/page-acceptance-contract.schema.json`
- `.codeartsdoer/skills/harmonyos-feature-implementation/assets/page-contract-registry.template.csv`
- `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/init_implementation.py`
- `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/validate_stage4.py`
- `.codeartsdoer/skills/android-harmony-migration-controller/scripts/validate_gate.py`
- `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_page_acceptance_contract.py`

`validate_stage4.py` and the controller Gate 4 validator were minimally extended because their exact lock-key allowlists otherwise reject the required new input-lock records. No Task 2+ feature-order, Inspector injection, comparator, rework, or Gate-4-closure behavior was added.

## RED command and actual output

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_page_acceptance_contract.py -v
```

Initial output:

```text
ModuleNotFoundError: No module named 'page_acceptance_contract'
```

After the compiler existed, the added code-map assertion also failed as expected before the contract exposed the required joined source data:

```text
KeyError: 'code_map'
```

## GREEN commands and actual output

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_page_acceptance_contract.py -v
```

```text
Ran 6 tests in 0.249s
OK
```

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_stage4_workflow.py Stage4WorkflowTest.test_full_stage4_and_controller_gate4_detect_post_close_tamper -v
```

```text
Ran 1 test in 27.420s
OK
```

```powershell
python -m py_compile .codeartsdoer/skills/harmonyos-feature-implementation/scripts/page_acceptance_contract.py .codeartsdoer/skills/harmonyos-feature-implementation/scripts/init_implementation.py .codeartsdoer/skills/harmonyos-feature-implementation/scripts/validate_stage4.py .codeartsdoer/skills/android-harmony-migration-controller/scripts/validate_gate.py
git diff --check
```

Both commands completed successfully.

## Design decisions

- Contracts are compiled only from Phase 2/3 IDs, never names; pages, states, components, transitions, records, H4 environments, and hashes are stable-sorted.
- Missing/duplicate IDs, orphan subjects, missing evidence payloads, unobserved runtime states, unresolved Phase 3 targets, and missing referenced catalog items fail closed with page/subject context.
- Contract hashes use UTF-8 canonical JSON with sorted keys and compact separators.
- Initialization publishes every contract and registry from a staging directory, freezes them, and locks the registry plus each canonical contract hash in `stage-04-input-lock.json`.
- Local validation verifies registry/schema/hash bindings and immutability; the controller accepts the new frozen lock fields so the existing end-to-end Gate 4 regression remains executable.

## Self-review findings

- Confirmed deterministic ordering and hash stability using reordered H4 environment inputs.
- Confirmed no whitespace errors with `git diff --check` and syntax validity with `py_compile`.
- Reviewed atomic publication, lock coverage, immutable modes, fail-closed paths, and direct Phase 2/3 joins.

## Commit hash

`0483b8eed486acd0e462d609255733bc2f1d5b63` — `feat: compile immutable page acceptance contracts`

## Concerns

The brief's literal existing-workflow selector, `Stage4WorkflowTest.test_full_workflow`, is absent in the current file and returns `AttributeError`. The current equivalent regression is `Stage4WorkflowTest.test_full_stage4_and_controller_gate4_detect_post_close_tamper`, which passes.

## Repair round 1 — independent review findings

### Status

DONE_WITH_CONCERNS

### Covering tests

- `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_page_acceptance_contract.py`
- `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_stage4_workflow.py`

### RED command and actual output

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_page_acceptance_contract.py -v
```

Before the repair, 13 tests ran with six expected failures and one expected error. The failures demonstrated all review findings: page-scoped `SIDE-CLIPBOARD` leaked into `PAGE-HISTORY`; cross-page component/event binding was accepted; inventory state facts could be absent from `state-candidates`; a mismatched runtime `after_evidence_id` was accepted; incomplete/undeclared contract structure was accepted; and unsafe `../escape` was not rejected before page lookup. The injected registry-replace failure removed the published registry, proving non-atomic set replacement.

The stricter state-set repair then produced this Stage 4 RED result against the old fixture:

```text
PAGE-LOGIN STATE-DEFAULT: inventory state lacks static state fact
```

The fixture was corrected with the required frozen state candidate and matching `STATE` observation before the final regression.

### GREEN commands and actual output

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_page_acceptance_contract.py -v
```

```text
Ran 13 tests in 0.472s
OK
```

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_stage4_workflow.py Stage4WorkflowTest.test_full_stage4_and_controller_gate4_detect_post_close_tamper -v
```

```text
Ran 1 test in 27.192s
OK
```

```powershell
python -m py_compile .codeartsdoer/skills/harmonyos-feature-implementation/scripts/page_acceptance_contract.py .codeartsdoer/skills/harmonyos-feature-implementation/scripts/validate_stage4.py
git diff --check
```

Both commands completed successfully.

### Fix decisions and self-review

- Canonical Page-ID validation now precedes all path construction, and both publisher and Stage 4 validator resolve targets then require containment beneath `page-contracts`.
- Page-scoped side effects no longer leak across sibling pages; only page-less feature-wide effects are shared.
- Runtime PAGE observations now join by accepted evidence to exactly one active inventory row and reject contradictory page/state/environment fields.
- Static and inventory `(page_id, state_id)` sets are bidirectionally closed; the Stage 4 fixture now supplies the corresponding frozen state fact and runtime state observation.
- Event/component and transition/event joins now require same-page ownership.
- Publisher validation and JSON Schema now enumerate all required fields, reject undeclared fields, validate list/object/string structures, and lock the exact comparison policy.
- Publication uses a staged-set backup/rollback protocol; a one-shot `os.replace` fault injection proves both old contracts and old registry remain intact after failure.

### Repair commit

`f3b2004be3455b9ebb5ab391d18eb869f37d88bf` — `fix: harden immutable page contracts`

### Concerns

The brief's obsolete `Stage4WorkflowTest.test_full_workflow` selector remains absent. The current equivalent end-to-end regression passes.
