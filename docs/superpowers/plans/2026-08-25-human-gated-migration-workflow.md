# Human-Gated Migration Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add mandatory phase-level human approval, exception-first review summaries, and lean four-Skill entry contracts without weakening deterministic verification.

**Architecture:** The controller owns one append-only human-review ledger and one normalized review summary per phase. Specialist Skills keep producing machine evidence; work-order issuance and delivery require a current machine gate plus a human decision bound to that gate hash.

**Tech Stack:** Python standard library, JSON/CSV, unittest, Markdown.

**Spec:** `docs/superpowers/specs/2026-08-25-human-gated-migration-workflow-design.md`

## Global Constraints

- Models never grant PASS, approval, completion, or deviation acceptance.
- Human approval never changes a machine verdict or erases a difference.
- Every approval binds the SHA-256 of the current canonical gate report.
- Phase 2 remains fully automatic internally; its human review happens only after machine closure.
- Existing Phase 1-4 evidence and hash checks remain fail-closed.

---

### Task 1: Human Review Records and Exception-First Summary

**Files:**
- Create: `.codeartsdoer/skills/android-harmony-migration-controller/scripts/_human_review.py`
- Create: `.codeartsdoer/skills/android-harmony-migration-controller/scripts/build_review_summary.py`
- Create: `.codeartsdoer/skills/android-harmony-migration-controller/scripts/record_human_review.py`
- Test: `.codeartsdoer/skills/android-harmony-migration-controller/scripts/tests/test_human_review.py`

**Interfaces:**
- Produces: `require_current_approval(run_dir: Path, phase: int) -> dict`.
- Produces: `controller/human-reviews/phase-0N-review.json` and `phase-0N-review-summary.json`.

- [ ] Write tests rejecting machine FAIL approval, stale gate hashes, overwrite, and malformed decisions; verify RED.
- [ ] Implement canonical report selection, SHA-256 binding, immutable decision records and normalized summaries.
- [ ] Run `python -m unittest ...test_human_review -v` and verify GREEN.
- [ ] Commit the independently testable subsystem.

### Task 2: Enforce Human Approval Between Phases

**Files:**
- Modify: `issue_phase2_work_order.py`
- Modify: `issue_phase3_work_order.py`
- Modify: `issue_phase4_work_order.py`
- Modify: `audit_delivery.py`
- Test: `scripts/tests/test_human_review_handoffs.py`

**Interfaces:**
- Consumes: `require_current_approval` from Task 1.
- Produces: fail-closed Phase 1->2, 2->3, 3->4 and Phase 4->delivery handoffs.

- [ ] Write handoff tests that first fail when approval is missing or stale.
- [ ] Call the shared approval reader immediately after each canonical machine-gate recheck.
- [ ] Verify an approval cannot bypass machine failure and delivery requires Phase 4 approval.
- [ ] Run focused controller tests and commit.

### Task 3: Replace Feature-Level Phase 4 Ownership

**Files:** Use Tasks 2-8 of `2026-08-24-phase4-page-owned-deterministic-parity.md`.

**Interfaces:**
- Consumes: frozen Phase 2 page acceptance contracts.
- Produces: one `PAGE_WORK_ORDER` per Page-ID and separate `SHARED_CAPABILITY_WORK_ORDER` records.

- [ ] Execute the referenced TDD tasks in order, preserving one final HAP and deterministic comparisons.
- [ ] Require every page/state/transition to close or route upstream before Gate 4.
- [ ] Commit and independently review each referenced task.

### Task 4: Make Review Output Human-Scale

**Files:**
- Modify: `build_review_summary.py`
- Create: `assets/review-summary.schema.json`
- Test: `scripts/tests/test_review_summary_views.py`

**Interfaces:**
- Produces exactly: `phase,status,coverage,critical_count,warning_count,top_risks,exceptions,key_samples,evidence_links,recommended_action`.

- [ ] Write fixtures for Phases 1-4 and prove raw ledgers are not copied into the summary.
- [ ] Add phase-specific adapters: scope readiness, Android page coverage, scaffold route/carrier coverage, parity difference coverage.
- [ ] Sort critical/warning items before green samples and cap default samples deterministically.
- [ ] Validate against the schema, run tests and commit.

### Task 5: Slim the Four Skill Entrypoints

**Files:**
- Modify: each of the four `.codeartsdoer/skills/*/SKILL.md`
- Create: `android-harmony-migration-controller/references/human-review-gates.md`
- Test: `android-harmony-migration-controller/scripts/tests/test_skill_contract_text.py`

**Interfaces:**
- Produces: four entrypoints containing purpose, exclusions, inputs, run, outputs, automatic gate, human gate, failure route and a small reference map.

- [ ] Write policy tests for model-no-PASS, WAITING_HUMAN_REVIEW, Page-ID ownership and no automatic cross-phase continuation; verify RED.
- [ ] Move repeated CLI detail to existing runbooks and reference the shared human-gate contract.
- [ ] Repair UTF-8 corruption and keep each entrypoint below the governed context budget.
- [ ] Run policy, trigger, resource-boundary and package validation; commit.

### Task 6: Full Regression and Release Evidence

**Files:**
- Modify only failing fixtures or portability code revealed by the complete suites.
- Update governed reports under the four Skill packages.

- [ ] Run every production Python CLI with `--help`.
- [ ] Run all four unittest suites on Windows.
- [ ] Run page-contract, anti-self-PASS, stale-approval and human-summary adversarial tests.
- [ ] Run YAO validation, resource-boundary, trust and trigger checks for each Skill.
- [ ] Run `git diff --check`, inspect the final diff, and commit release evidence.

