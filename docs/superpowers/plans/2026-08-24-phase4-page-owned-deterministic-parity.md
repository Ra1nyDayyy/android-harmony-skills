# Phase 4 Page-Owned Deterministic Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task by task with the stated review checkpoints.

**Goal:** Rewrite Phase 4 so every frozen Phase 2 page is migrated by one exclusive page owner, shared capabilities are implemented separately, and only deterministic Android-versus-HarmonyOS evidence comparison can produce `PASS`.

**Architecture:** Phase 4 starts by compiling immutable page acceptance contracts from the Phase 2 inventory and Phase 3 scaffold. The controller issues one CodeArts work item per page plus separate capability work items, generates page-scoped UiTest probes under `ohosTest`, executes them through frozen external launch/navigation adapters, and binds the test HAP, final HAP, device, command, page plan, generation manifest, component snapshots, operation traces, screenshots, behavior assertions, and side-effect evidence. A local work-item string is never proof of a real CodeArts platform task; platform receipts provide that identity. Agents may implement, diagnose, and repair, but cannot write acceptance results.

**Tech Stack:** Python 3 standard library for contracts, ledgers, hashing, orchestration, and JSON/CSV validation; Pillow for deterministic PNG decoding; ArkTS UiTest APIs imported from `@kit.TestKit` under `ohosTest`; Hvigor and HDC through frozen executable contracts; `unittest` for regression tests.

**Spec:** [2026-08-24-phase4-page-owned-deterministic-parity-design.md](../specs/2026-08-24-phase4-page-owned-deterministic-parity-design.md)

> **2026-08-25 binding amendment:** Original Tasks 3-4 are superseded by [Phase 4 UiTest and ArkTS Page-Plan Amendment](../specs/2026-08-25-phase4-uitest-page-plan-amendment.md). Phase 4 must use the UiTest API re-exported by `@kit.TestKit`, must compile a conservation-checked `arkts-page-plan.json` before page code, and must not implement the replaced tree-dump bridge design.

## Global Constraints

- Phase 2 frozen artifacts are the only source of Android UI and functional truth. Phase 4 may normalize them into contracts but must not reinterpret or silently reduce them.
- Every distinct `Page-ID` has one distinct page owner ID and one distinct real CodeArts task ID. Queued execution is allowed; task identity reuse is not.
- A page owner owns all states and all repairs for that page. Shared capabilities use separate capability owners and non-overlapping code paths.
- Page owners and capability owners cannot approve their own work. Reviewer identity must differ from every implementation identity in the reviewed unit.
- Only comparison scripts can write `PASS`. Model-authored `PASS`, `MATCH`, `PARTIAL`, `PASS_WITH_GAPS`, `CAN_PROCEED`, or native-design substitutions are rejected.
- A page may run attempt `0` plus repair attempts `1` and `2`. A third repair is rejected before execution.
- Every accepted page evidence package and the integration regression package must reference the same final HAP SHA-256.
- Machine differences are append-only. A human exception can waive named comparison IDs but cannot edit, delete, or supersede the original machine result.
- Missing or contradictory Phase 2/3 facts, unavailable frozen toolchains, and failed comparison infrastructure produce `UPSTREAM_BLOCKED`; they never produce an inferred expected value or substitute pass.
- Existing Phase 4 runs are not upgraded in place. The controller issues a new versioned Phase 4 root order and regenerates page contracts, page orders, capability orders, and evidence while preserving the old run read-only.
- Native HarmonyOS optimizations are allowed only after the same carrier, components, states, actions, outputs, transitions, and side effects remain within the frozen comparison thresholds.
- All multi-file mutations use a staging directory, validate the complete staged set, then atomically rename files into place.
- Every task below begins with a failing test, makes the smallest implementation that passes, reruns the focused suite, and commits before the next task.

## Task 1: Compile Immutable Page Acceptance Contracts

**Files:**

- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/page_acceptance_contract.py`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/page-acceptance-contract.schema.json`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/page-contract-registry.template.csv`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/init_implementation.py`
- Test: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_page_acceptance_contract.py`

**Contract interface:**

```python
def compile_page_contracts(
    phase2_workspace: Path,
    phase3_workspace: Path,
    required_h4env_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    """Return one canonical contract for every distinct non-empty Phase 2 page_id."""

def canonical_contract_sha256(contract: dict[str, object]) -> str:
    """Hash UTF-8 canonical JSON using sorted keys and compact separators."""
```

Each contract must contain `page_id`, `page_name`, `feature_ids`, every `state_id`, ordered component records, source geometry, assets, visible text, interaction bindings, entry conditions, outgoing transitions, business rules, data dependencies, side effects, system capabilities, Android evidence hashes, Phase 3 route/module targets, and required H4 environments. It also contains deterministic comparison policy copied from the frozen scope: geometry `max(2dp, 0.5%)`, application-region SSIM `0.98`, changed-pixel ratio `0.02`, and stricter required-element masks.

**Step 1: Write the failing tests.**

```python
def test_compiles_one_contract_per_page_with_all_states(self):
    contracts = compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))
    self.assertEqual(["PAGE-CALCULATOR", "PAGE-HISTORY"], [c["page_id"] for c in contracts])
    calculator = contracts[0]
    self.assertEqual({"STATE-EMPTY", "STATE-RESULT", "STATE-ERROR"},
                     {s["state_id"] for s in calculator["states"]})
    self.assertIn("TRANS-CALC-HISTORY", {t["transition_id"] for t in calculator["transitions"]})
    self.assertIn("SIDE-CLIPBOARD", {e["side_effect_id"] for e in calculator["side_effects"]})

def test_rejects_page_when_inventory_references_missing_evidence(self):
    remove_android_evidence(self.phase2, "EVID-RESULT")
    with self.assertRaisesRegex(ValueError, "PAGE-CALCULATOR.*EVID-RESULT"):
        compile_page_contracts(self.phase2, self.phase3, ("H4ENV-001",))
```

**Step 2: Run and confirm failure.**

Run:

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_page_acceptance_contract.py -v
```

Expected: import failure because `page_acceptance_contract.py` does not exist.

**Step 3: Implement canonical joins and completeness checks.**

- Join Phase 2 inventory, code map, business rules, data dependencies, system capabilities, assets, evidence index, metadata, and deterministic page records by IDs rather than names.
- Join Phase 3 route/module registries without changing Android semantics.
- Sort pages, states, components, and transitions by stable IDs before hashing.
- Reject orphan references, blank IDs, duplicate IDs, missing screenshots/layout trees, uncovered runtime states, and any page absent from the Phase 3 route map. Record the page as `UPSTREAM_BLOCKED` with the exact missing upstream IDs before returning control to Phase 2 or Phase 3.
- Write contracts under `page-contracts/<page-id>.json` and registry rows through a staging directory.
- Include the contract registry and every contract hash in `stage-04-input-lock.json`.

**Step 4: Run focused tests and the existing initialization test.**

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_page_acceptance_contract.py -v
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_stage4_workflow.py Stage4WorkflowTest.test_full_workflow -v
```

Expected: contract tests pass; legacy workflow may fail only where the next task intentionally replaces feature-level work orders.

**Step 5: Commit.**

```powershell
git add .codeartsdoer/skills/harmonyos-feature-implementation
git commit -m "feat: compile immutable page acceptance contracts"
```

## Task 2: Replace Feature-Level Orders with Page and Capability Orders

**Files:**

- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/stage4_work_orders.py`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/issue_page_work_order.py`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/issue_capability_work_order.py`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/page-work-order.template.json`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/page-work-order-registry.template.csv`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/capability-work-order.template.json`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/capability-work-order-registry.template.csv`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/page-implementation-ledger.template.csv`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/issue_feature_work_order.py`
- Modify: `.codeartsdoer/skills/android-harmony-migration-controller/scripts/issue_phase4_work_order.py`
- Modify: `.codeartsdoer/skills/android-harmony-migration-controller/scripts/_team_execution.py`
- Modify: `.codeartsdoer/skills/android-harmony-migration-controller/scripts/record_team_execution.py`
- Test: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_stage4_work_orders.py`
- Test: `.codeartsdoer/skills/android-harmony-migration-controller/scripts/tests/test_team_execution.py`

**Work-order interface:**

```python
def issue_page_order(workspace: Path, page_id: str, owner_id: str,
                     codearts_task_id: str, code_paths: tuple[str, ...]) -> Path: ...

def issue_capability_order(workspace: Path, capability_id: str, owner_id: str,
                           codearts_task_id: str, consumer_page_ids: tuple[str, ...],
                           code_paths: tuple[str, ...]) -> Path: ...
```

**Step 1: Write failing ownership tests.**

```python
def test_each_page_requires_distinct_owner_and_codearts_task(self):
    issue_page_order(self.ws, "PAGE-A", "owner-a", "TASK-100", ("entry/src/main/ets/pages/A.ets",))
    with self.assertRaisesRegex(ValueError, "owner.*already bound"):
        issue_page_order(self.ws, "PAGE-B", "owner-a", "TASK-101", ("entry/src/main/ets/pages/B.ets",))
    with self.assertRaisesRegex(ValueError, "task.*already bound"):
        issue_page_order(self.ws, "PAGE-B", "owner-b", "TASK-100", ("entry/src/main/ets/pages/B.ets",))

def test_page_and_capability_code_paths_cannot_overlap(self):
    issue_page_order(self.ws, "PAGE-A", "owner-a", "TASK-100", ("entry/src/main/ets/pages/A.ets",))
    with self.assertRaisesRegex(ValueError, "exclusive code path"):
        issue_capability_order(self.ws, "CAP-CALC", "cap-owner", "TASK-200", ("PAGE-A",),
                               ("entry/src/main/ets/pages/A.ets",))
```

**Step 2: Run tests and confirm missing modules/functions.**

**Step 3: Implement issuance and receipts.**

- Page order embeds the page contract path/hash, all state IDs, all parity checks, capability dependencies, exclusive page code paths, exact completion command, owner ID, and real CodeArts task ID.
- Capability order embeds consumer pages, behavior/side-effect contracts, interface files, implementation files, test files, and non-overlapping ownership paths.
- Order registries are append-only; no task or owner reuse across pages.
- Controller refuses to start Phase 4 until page order count equals page contract count and all shared capabilities have orders.
- Team receipts must contain the real task ID, actor ID, order hash, result artifact hash, start/end times, and terminal task state.
- `issue_feature_work_order.py` becomes an explicit migration error explaining that Phase 4 requires page and capability orders; it must not silently translate old feature orders.

**Step 4: Run both focused suites.**

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_stage4_work_orders.py -v
python .codeartsdoer/skills/android-harmony-migration-controller/scripts/tests/test_team_execution.py -v
```

**Step 5: Commit.**

```powershell
git add .codeartsdoer/skills/harmonyos-feature-implementation .codeartsdoer/skills/android-harmony-migration-controller
git commit -m "feat: enforce page-owned Phase 4 work orders"
```

## Task 3: Generate UiTest Snapshot Probes in `ohosTest`

This entire legacy Task 3 is replaced by the binding amendment and the current UiTest snapshot implementation. It is retained only as historical plan context and has no implementation authority.

**Files:**

- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/uitest-snapshot/UiTestSnapshot.test.ets`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/uitest-snapshot/UiTestPageProbeRegistry.ets`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/prepare_inspector_test.py`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/uitest-snapshot/UiTestRunBinding.ets`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/init_implementation.py`
- Test: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_inspector_test_injection.py`

**Step 1: Write the failing injection test.**

```python
def test_injects_inspector_only_into_ohos_test(self):
    result = prepare_inspector_test(self.workspace)
    test_root = self.workspace / "harmony-project/entry/src/ohosTest/ets/test"
    self.assertTrue((test_root / "UiTestSnapshot.test.ets").is_file())
    self.assertTrue((test_root / "UiTestPageProbeRegistry.ets").is_file())
    self.assertFalse(any((self.workspace / "harmony-project/entry/src/main").rglob("*UiTest*")))
    self.assertEqual(result["page_ids"], ["PAGE-CALCULATOR", "PAGE-HISTORY"])
```

**Step 2: Run and confirm failure.**

**Step 3: Implement deterministic test generation.**

- Generate one ArkTS test case per page state from frozen contracts.
- Navigate using the Phase 3 route target and execute the exact action sequence from Phase 2.
- Query frozen components through UiTest `Driver` and stable `ON.id`/unique `ON.text` locators only.
- Save raw tree, normalized tree, operation trace, page/state/environment IDs, capture timestamps, and capture errors to the test result directory.
- Fail the ArkTS test when a required page/state is unreachable, the tree is empty, a required component ID is absent, or any result file cannot be written.
- Emit a generation manifest with source contract hashes and hashes of every injected ArkTS file.
- Ensure the bridge and generated probes exist only under `ohosTest`, never production `main` source.

**Step 4: Run the injection suite.**

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_inspector_test_injection.py -v
```

**Step 5: Commit.**

```powershell
git add .codeartsdoer/skills/harmonyos-feature-implementation
git commit -m "feat: inject UiTest snapshot ohosTest probes"
```

## Task 4: SUPERSEDED — Execute Hash-Bound UiTest Snapshot Evidence

The authoritative Task 4 path uses `UITEST_SNAPSHOT_CAPTURE`, never the legacy instructions below. It builds and seals a test HAP and final HAP, uses a frozen external launch/navigation adapter, runs one frozen State-ID snapshot before any action, isolates actions, rejects transitions without source/action/target/back evidence, and validates `ui-test-snapshot.json`, operation trace, UiTest screenshot, component properties, device identity, command hash, page-plan hash, and generation-manifest hash. The remaining legacy prose in this section is non-authoritative historical context.

**Files:**

- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/run_inspector_capture.py`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/inspector-execution-plan.template.json`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/phase4-environment.template.json`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/_common.py`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/arkui_inspector.py`
- Test: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_inspector_runner.py`

**Execution categories:** `INSPECTOR_TEST_BUILD`, `INSPECTOR_TARGET_INSTALL`, `INSPECTOR_TEST_INSTALL`, `INSPECTOR_TEST_RUN`, and `INSPECTOR_RESULT_PULL`.

**Step 1: Write failing runner tests.**

```python
def test_runner_rejects_result_not_produced_by_injected_test(self):
    write_pulled_result(self.ws, generation_sha256="0" * 64)
    with self.assertRaisesRegex(ValueError, "generation manifest hash"):
        run_inspector_capture(self.ws, self.plan)

def test_runner_requires_all_states_and_one_device_serial(self):
    omit_state(self.plan, "STATE-ERROR")
    with self.assertRaisesRegex(ValueError, "STATE-ERROR"):
        run_inspector_capture(self.ws, self.plan)
```

**Step 2: Run and confirm failure.**

**Step 3: Implement frozen-tool orchestration.**

- Resolve and hash the Hvigor/HDC executables at environment-freeze time.
- Build the application and `ohosTest` target in test mode, install both HAPs, run the generated UiTest probes, and pull only the declared result directory.
- Use `shell=False`, fixed working directories, exact device serial, timeouts, and an argv allowlist. Reject undeclared extra arguments.
- Verify the installed bundle, test bundle, final HAP hash, generation manifest hash, contract hash, and complete page/state result set.
- Preserve raw stdout/stderr and exit codes. External success text alone is never UiTest evidence.
- Keep fake executables only in unit tests; production validation rejects the test-fixture environment marker.

**Step 4: Run focused tests.**

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_inspector_runner.py -v
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_phase4_common.py -v
```

**Step 5: Commit.**

```powershell
git add .codeartsdoer/skills/harmonyos-feature-implementation
git commit -m "feat: run and attest hash-bound UiTest snapshots"
```

## Task 5: Add Deterministic Cross-Platform Comparison Engines

**Files:**

- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/comparison_common.py`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/compare_component_tree.py`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/compare_geometry.py`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/compare_screenshot.py`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/compare_behavior.py`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/compare_migration_unit.py`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/comparison-result.schema.json`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/requirements-ci.txt`
- Test: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_deterministic_comparators.py`

**Comparator interface:**

```python
@dataclass(frozen=True)
class ComparisonResult:
    comparison_id: str
    category: str
    passed: bool
    expected_sha256: str
    actual_sha256: str
    metrics: dict[str, float | int | str]
    differences: tuple[dict[str, object], ...]

def compare_page_state(contract: dict[str, object], evidence_dir: Path,
                       output_dir: Path) -> list[ComparisonResult]: ...
```

**Step 1: Add calculator regression fixtures and failing tests.**

```python
def test_rejects_page_replaced_by_dialog(self):
    results = compare_fixture("calculator-page", "calculator-dialog")
    self.assertFalse(result_for(results, "carrier").passed)

def test_rejects_missing_buttons_and_shifted_geometry(self):
    results = compare_fixture("calculator-page", "calculator-missing-buttons")
    self.assertFalse(result_for(results, "component-tree").passed)
    self.assertFalse(result_for(results, "geometry").passed)

def test_rejects_wrong_result_even_when_external_status_says_pass(self):
    results = compare_fixture("calculator-5-plus-3", "calculator-result-zero")
    self.assertFalse(result_for(results, "behavior").passed)
```

**Step 2: Run and confirm missing comparators.**

**Step 3: Implement comparison rules.**

- Add exactly `Pillow>=10.4,<12` to `requirements-ci.txt`; production comparator imports must fail closed with an installation message if Pillow is unavailable.
- Carrier: page remains a page; dialog/sheet/popup substitution fails unless Phase 2 itself records that carrier.
- Component tree: required component IDs, types, parent-child relationships, order, visibility, enabled state, text, and interaction affordance must match normalized Phase 2 records.
- Geometry: normalize density and application viewport; every required element must be within `max(2dp, 0.5%)` for x/y/width/height.
- Screenshot: use Pillow to crop normalized application regions; compute 8-bit luminance SSIM over deterministic non-overlapping `8x8` windows with standard `C1=(0.01*255)^2` and `C2=(0.03*255)^2`, average the window scores, and compute changed-pixel ratio from the frozen per-channel color threshold. Enforce SSIM `>=0.98` and changed pixels `<=0.02`, then repeat on required-element masks.
- Behavior: evaluate exact Phase 2 expected observables against actual normalized values. The external program cannot supply the pass bit.
- Side effects: compare database/network/clipboard/background/capability traces by contract-defined predicates and redacted payload hashes.
- Navigation: compare source page/state, action, destination page/state, back behavior, and carrier type.
- Write one immutable result per comparison plus a page-state summary. Summary passes only when every required result passes.
- Comparison outputs use `comparisons/<migration-unit-id>/<attempt-id>/` and include `structural-diff.json`, `geometry-diff.json`, `pixel-diff.json`, `behavior-diff.json`, `overlay.png`, `diff.png`, `verdict.json`, `manifest.sha256`, and `COMMITTED`. A pre-existing or hand-edited `verdict.json` invalidates the attempt.

**Step 4: Run comparator tests.**

```powershell
python -m pip install -r .codeartsdoer/skills/harmonyos-feature-implementation/requirements-ci.txt
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_deterministic_comparators.py -v
```

Expected: all fixtures pass their expected accept/reject assertions.

**Step 5: Commit.**

```powershell
git add .codeartsdoer/skills/harmonyos-feature-implementation
git commit -m "feat: compare Phase 4 parity deterministically"
```

## Task 6: Make State Capture Consume Contracts and Comparator Results

**Files:**

- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/capture_state.py`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/_stage4_audit.py`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/state-verification-plan.template.json`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/evidence-index.template.csv`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/page-state-evidence.schema.json`
- Test: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_capture_state_v2.py`

**Step 1: Write failing anti-self-pass tests.**

```python
def test_external_pass_cannot_override_wrong_actual(self):
    self.plan["assertions"][0].update({"expected": "8", "actual": "0", "status": "PASS"})
    completed = capture(self.plan)
    self.assertNotEqual(0, completed.returncode)
    self.assertIn("expected observable mismatch", completed.stderr)

def test_plan_cannot_change_frozen_expected_value(self):
    self.plan["assertions"][0]["expected"] = "0"
    completed = capture(self.plan)
    self.assertNotEqual(0, completed.returncode)
    self.assertIn("contract hash", completed.stderr)
```

**Step 2: Run and observe the existing false-pass behavior.**

**Step 3: Replace assertion trust with contract-derived evaluation.**

- Verification plans may declare actions and capture commands but cannot declare expected values or final status.
- Load expected observables, component requirements, geometry, transitions, and side-effect predicates from the locked page contract.
- Bind the raw state screenshot, UiTest component snapshot, hash-bound operation trace, UiTest screenshot, assertions, test/final HAPs, source snapshot, environment, page plan, generation manifest, and comparator results into one evidence manifest.
- Set evidence status from comparator aggregation only.
- Reject evidence if capture actor equals page owner, capability owner, or reviewer.
- Seal evidence directories after writing and record every file hash in the evidence index.

**Step 4: Run capture and audit suites.**

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_capture_state_v2.py -v
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_stage4_workflow.py -v
```

**Step 5: Commit.**

```powershell
git add .codeartsdoer/skills/harmonyos-feature-implementation
git commit -m "feat: derive Phase 4 evidence verdicts from contracts"
```

## Task 7: Enforce the Three-Attempt Repair State Machine and Human Exceptions

**Files:**

- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/manage_stage4_rework.py`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/record_human_exception.py`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/attempt-ledger.template.csv`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/rework-tickets.template.csv`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/human-exception.schema.json`
- Modify: `.codeartsdoer/skills/android-harmony-migration-controller/assets/phase4-attempt-ledger.template.csv`
- Test: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_stage4_rework_budget.py`

**Allowed transitions:**

```text
NOT_STARTED -> IMPLEMENTING -> VERIFYING
VERIFYING -> PASS
VERIFYING -> REWORK_1 -> VERIFYING
VERIFYING -> REWORK_2 -> VERIFYING
VERIFYING -> UPSTREAM_BLOCKED
VERIFYING -> REVIEW_REQUIRED
REVIEW_REQUIRED -> EXCEPTION_ACCEPTED | REJECTED
```

**Step 1: Write failing state-machine tests.**

```python
def test_third_repair_is_rejected(self):
    record_failed_attempts(self.ws, page="PAGE-A", attempts=(0, 1, 2))
    with self.assertRaisesRegex(ValueError, "repair budget exhausted"):
        open_repair(self.ws, "PAGE-A")

def test_human_exception_cannot_erase_machine_difference(self):
    exception = signed_exception(waived_ids=["CMP-GEOMETRY-1"])
    record_human_exception(self.ws, exception)
    self.assertTrue(machine_result(self.ws, "CMP-GEOMETRY-1")["passed"] is False)
```

**Step 2: Run and confirm current permissive behavior.**

**Step 3: Implement append-only transitions.**

- Reserve attempts atomically per page and environment.
- Route comparator differences into grouped repair tickets: carrier/navigation, missing UI, geometry/visual, behavior/business logic, side effects/capabilities, or infrastructure.
- Bind each repair to the original page owner and the exact failed comparison IDs.
- At attempt exhaustion, create `REVIEW_REQUIRED`; never create a synthetic pass.
- Toolchain, UiTest, frozen navigation-adapter, or comparator execution faults create `UPSTREAM_BLOCKED` with command evidence; they do not consume a business-code repair attempt until infrastructure is restored.
- Human exceptions require reviewer ID, scope, rationale, waived comparison IDs, expiry or permanent flag, risk statement, signature digest, and controller countersignature.
- Reject broad exceptions such as `all`, `page`, or an empty comparison list.

**Step 4: Run the focused tests.**

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_stage4_rework_budget.py -v
```

**Step 5: Commit.**

```powershell
git add .codeartsdoer/skills/harmonyos-feature-implementation .codeartsdoer/skills/android-harmony-migration-controller
git commit -m "feat: cap Phase 4 repair attempts and govern exceptions"
```

## Task 8: Require One Final Build and Cross-Page Integration Regression

**Files:**

- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/run_integration_regression.py`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/assets/integration-regression-plan.template.json`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/run_build.py`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/validate_stage4.py`
- Modify: `.codeartsdoer/skills/android-harmony-migration-controller/scripts/validate_gate.py`
- Modify: `.codeartsdoer/skills/android-harmony-migration-controller/scripts/audit_delivery.py`
- Test: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_final_build_closure.py`
- Test: `.codeartsdoer/skills/android-harmony-migration-controller/scripts/tests/test_gate4_page_closure.py`

**Step 1: Write failing closure tests.**

```python
def test_gate_rejects_page_evidence_from_different_haps(self):
    bind_page_build("PAGE-A", "a" * 64)
    bind_page_build("PAGE-B", "b" * 64)
    self.assertGateFails("same final HAP")

def test_gate_rejects_missing_cross_page_transition(self):
    close_all_pages_but_omit_transition("TRANS-CALC-HISTORY")
    self.assertGateFails("TRANS-CALC-HISTORY")
```

**Step 2: Run and confirm Gate 4 currently lacks these closures.**

**Step 3: Implement final-build convergence.**

- After page and capability integration, create one clean final HAP and freeze its SHA-256.
- Recapture every required page/state/environment using that HAP. Older build evidence remains historical but cannot satisfy Gate 4.
- Run every Phase 2 cross-page transition, back path, shared capability consumer, persistence restart, and declared global state interaction.
- Build an integration package that lists covered transition/capability IDs, evidence hashes, comparator results, and final HAP hash.
- Gate 4 recomputes closure from Phase 2 contracts, page/capability order registries, receipts, final evidence, exceptions, and integration package. It does not trust summary counts.
- Delivery audit reruns Gate 4 read-only and rejects changed files, missing receipts, stale evidence, or a delivery HAP hash different from the accepted final HAP.
- Any integration failure reopens the implicated page or capability order even if that unit previously passed. The regenerated final build then forces all page evidence to be recaptured.

**Step 4: Run both focused suites.**

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_final_build_closure.py -v
python .codeartsdoer/skills/android-harmony-migration-controller/scripts/tests/test_gate4_page_closure.py -v
```

**Step 5: Commit.**

```powershell
git add .codeartsdoer/skills/harmonyos-feature-implementation .codeartsdoer/skills/android-harmony-migration-controller
git commit -m "feat: close Gate 4 on one final integrated build"
```

## Task 9: Remove Legacy Escape Hatches and Rewrite the Skill Contract

**Files:**

- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/SKILL.md`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/references/feature-workflow.md`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/references/roles-and-authority.md`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/references/observable-consistency-contract.md`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/references/ui-test-snapshot-evidence.md`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/references/review-and-rework.md`
- Modify: `.codeartsdoer/skills/android-harmony-migration-controller/SKILL.md`
- Modify: `.codeartsdoer/skills/android-harmony-migration-controller/references/phase-4-handoff.md`
- Modify: `.codeartsdoer/skills/android-harmony-migration-controller/references/continuous-run.md`
- Modify: `README.md`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/evals/trigger_cases.json`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/evals/semantic_config.json`
- Test: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_phase4_contract_text.py`

**Step 1: Write a failing policy scan.**

```python
def test_phase4_contract_contains_no_legacy_acceptance_terms(self):
    texts = read_phase4_runtime_contracts()
    for forbidden in ("PASS_WITH_GAPS", "CAN_PROCEED", "PARTIAL"):
        self.assertNotIn(forbidden, texts)

def test_skill_requires_page_task_per_page(self):
    text = SKILL.read_text(encoding="utf-8")
    self.assertIn("one distinct CodeArts task for every distinct Page-ID", text)
    self.assertIn("only deterministic comparison scripts may write PASS", text)
```

**Step 2: Run and confirm legacy wording is present or new rules are absent.**

**Step 3: Rewrite documentation and routing.**

- Make page contracts, page work orders, capability work orders, UiTest execution, deterministic comparison, repair budget, final build, and Gate 4 the only documented path.
- Clearly state UiTest limits: component lookup and geometry do not prove business semantics; behavior, navigation, return paths, and side effects require separate deterministic evidence.
- Explain that “one agent per page” means one exclusive owner/task identity, not guaranteed simultaneous execution.
- Remove advice permitting visual simplification, carrier substitution, deferred page completion, self-review, or model judgement as acceptance.
- Update one-shot controller prompts so Phase 4 fans out page/capability tasks and converges them before final validation.
- Add positive/negative trigger cases for page-level migration, UiTest verification, dialog substitution, model-authored pass, and exhausted repairs.

**Step 4: Run policy tests and YAO validation.**

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_phase4_contract_text.py -v
python3.11 C:\Users\Rainyday\.codex\skills\yao-meta-skill\scripts\yao.py validate .codeartsdoer/skills/harmonyos-feature-implementation
```

Expected: policy tests pass and YAO produces no critical contract or trigger errors.

**Step 5: Commit.**

```powershell
git add README.md .codeartsdoer/skills/harmonyos-feature-implementation .codeartsdoer/skills/android-harmony-migration-controller
git commit -m "docs: define page-owned deterministic Phase 4 workflow"
```

## Task 10: Full-Chain Verification and Release Readiness

**Files:**

- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_stage4_workflow.py`
- Modify: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/strict_fake_phase4.py`
- Modify: `.codeartsdoer/skills/android-harmony-migration-controller/scripts/tests/test_team_execution.py`
- Modify: `.codeartsdoer/skills/android-harmony-migration-controller/scripts/init_migration.py`
- Modify: `.codeartsdoer/skills/android-migration-inventory/scripts/tests/test_workflow.py`
- Modify: `.codeartsdoer/skills/harmonyos-migration-scaffold/scripts/tests/test_stage3_workflow.py`
- Create: `.codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/fixtures/calculator/README.md`
- Update generated YAO reports under: `.codeartsdoer/skills/harmonyos-feature-implementation/reports/`

**Step 1: Replace the old happy-path fixture with a realistic calculator chain.**

The fixture must contain at least calculator and history pages; empty, result, error, and history states; digit/operator/equal/clear/backspace/history interactions; a `5 + 3 = 8` observable; history persistence; a cross-page transition; representative geometry and PNGs; and negative variants for page-to-dialog substitution, missing buttons, shifted geometry, wrong result, missing history, forged external `PASS`, stale HAP, and a third repair attempt.

Also make the existing Windows baseline portable: render `scope.template.json` through parsed JSON fields rather than raw string replacement of `C:\...` paths, and skip symlink-only security cases when Windows cannot create a symlink while retaining equivalent non-symlink path-containment tests.

**Step 2: Run all production script help smoke tests.**

```powershell
Get-ChildItem .codeartsdoer/skills -Recurse -Filter *.py |
  Where-Object { $_.FullName -notmatch '\\scripts\\tests\\' } |
  ForEach-Object { python -B $_.FullName --help; if ($LASTEXITCODE -ne 0) { throw $_.FullName } }
```

Expected: every production CLI exits `0` for `--help`.

**Step 3: Run the complete Python suite.**

```powershell
python -m unittest discover -s .codeartsdoer/skills/android-harmony-migration-controller/scripts/tests -v
python -m unittest discover -s .codeartsdoer/skills/android-migration-inventory/scripts/tests -v
python -m unittest discover -s .codeartsdoer/skills/harmonyos-migration-scaffold/scripts/tests -v
python -m unittest discover -s .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests -v
```

Expected: all tests pass on Windows without developer-mode symlink privileges.

**Step 4: Run adversarial acceptance checks.**

```powershell
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_deterministic_comparators.py -v
python .codeartsdoer/skills/harmonyos-feature-implementation/scripts/tests/test_stage4_rework_budget.py -v
python .codeartsdoer/skills/android-harmony-migration-controller/scripts/tests/test_gate4_page_closure.py -v
```

Expected: every known false-pass fixture is rejected for the intended reason.

**Step 5: Inspect the diff and scan for unfinished text.**

```powershell
git diff --check
rg -n "TODO|TBD|PASS_WITH_GAPS|CAN_PROCEED|PARTIAL" \
  .codeartsdoer/skills/harmonyos-feature-implementation \
  .codeartsdoer/skills/android-harmony-migration-controller
git status --short
```

Expected: `git diff --check` is clean; the scan has no runtime-contract hits; only intended Phase 4 files are modified.

**Step 6: Run YAO packaging and trust checks, then commit.**

```powershell
python3.11 C:\Users\Rainyday\.codex\skills\yao-meta-skill\scripts\yao.py validate .codeartsdoer/skills/harmonyos-feature-implementation
git add README.md docs .codeartsdoer
git commit -m "test: verify page-owned deterministic Phase 4 end to end"
```

## Final Review Checklist

- Trace every design-spec requirement to at least one test and one enforcing code path.
- Confirm contract schema field types match Python readers, CSV writers, templates, and controller validators.
- Confirm no acceptance status is accepted from an LLM response, external command payload, review template, or CLI flag.
- Confirm all distinct Phase 2 pages have distinct owners and CodeArts task IDs, while shared capabilities have separate orders.
- Confirm UiTest probes are generated only into `ohosTest`, executed, isolated per frozen state/action, and cryptographically bound to pulled evidence.
- Confirm carrier, tree, geometry, screenshot, behavior, side-effect, navigation, and final-build checks all fail closed.
- Confirm attempt `0`, repair `1`, and repair `2` are the only automatic attempts.
- Confirm human exceptions are narrow, separately signed, and preserve original machine differences.
- Confirm Gate 4 recomputes closure and all accepted evidence references one final HAP hash.
- Confirm the full Windows test suite and calculator adversarial regression pass before pushing.
