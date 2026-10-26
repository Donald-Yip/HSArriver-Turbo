# R02A Test Inventory And Classification

## Task ID

R02A

## Goal

Inventory every existing test and classify it as keep, rewrite, move-to-native, or candidate-delete before deleting anything.

## Scope

- Produce `docs/testing/TEST_INVENTORY.md`.
- Record test file, test count, layer, purpose, decision, replacement, and follow-up task.
- Identify source-string, implementation-detail, duplicated-contract, real-environment, hardcoded-path, and dangerous-behavior tests.
- Produce a deterministic headless test plan and a list of R02C deletion candidates.

## Non-Goals

- No test deletion.
- No CI workflow changes.
- No production code changes.
- No architecture checker implementation.

## Inputs

- `docs/agent/TEST_STRATEGY.md`
- Current `tests/` tree
- `TOTAL_TASK.md`
- `docs/tasks/R02-contracts-tests-ci.md`

## Outputs

- `docs/testing/TEST_INVENTORY.md`
- `docs/testing/TEST_RATIONALIZATION_PLAN.md`

## Interfaces

- Consumes: existing test suite.
- Produces: reviewed behavior-to-test mapping for R02B/R02C.

## Dependencies And Impact

- Depends on: R01.
- Impacted modules: test documentation only.
- Public API impact: none.

## Allowed Files

- `docs/testing/**`
- `docs/tasks/R02A-*.md`
- `docs/agent/reports/R02A-implementer.md`
- `docs/agent/reviews/R02A-review.md`
- `TOTAL_TASK.md`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/TASK_BOARD.json`
- `docs/agent/HANDOFF.md`

## Forbidden Files

- Any file under `tests/`
- Any production Python module
- `.github/**`
- Runtime configuration, logs, or fixtures

## Acceptance

- Every existing test file appears exactly once in the inventory.
- Every test method is classified or grouped with an explicit file-level reason when method-level mapping is impractically large.
- No deletion happens.
- Every candidate deletion has a replacement or a reviewer-approved non-behavioral rationale.
- Inventory totals match actual `unittest` discovery counts.

## Risk And Rollback

- Risk: incomplete classification could remove coverage later.
- Rollback: revert documentation commit; tests are untouched.

## Garbage-Code Check

- Identify garbage tests but do not delete them in this task.
- Record each deletion candidate in the rationalization plan.

## Git Requirement

```text
test(R02A): inventory and classify legacy test suite
```

## Report Paths

- Implementer: `docs/agent/reports/R02A-implementer.md`
- Reviewer: `docs/agent/reviews/R02A-review.md`
