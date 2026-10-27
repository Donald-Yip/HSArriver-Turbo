# R02D Test Portfolio Audit

## Task ID

R02D

## Goal

Re-audit the post-R08 test suite as a behavior portfolio and produce an exact, low-risk cleanup ledger. The audit must identify duplicated, implementation-coupled, over-defensive, nondeterministic, environment-bound, and stale compatibility tests without deleting or modifying tests.

## Scope

- Inventory all current Python test files/classes/methods under `tests/`, including layered tests.
- Classify tests by contract value:
  - user-visible behavior;
  - explicit port/API/data contract;
  - regression protection;
  - architecture invariant;
  - integration/native gate;
  - duplicate or implementation-coupled coverage;
  - over-defensive validation with no external contract;
  - nondeterministic or environment-bound test.
- Identify exact cleanup candidates using `file::Class.method` identities.
- Separate `must-remove`, `merge`, `rewrite`, `move-opt-in`, and `keep`.
- For every candidate, record evidence, replacement/equivalent gate, risk, and dependency.
- Produce a proposed phased cleanup order that preserves behavior coverage at each step.
- Reconcile the outdated R02A counts with the current post-R08 suite.

## Non-Goals

- Do not edit, delete, move, or rename any test.
- Do not change production code, public APIs, CI workflow, or test runner.
- Do not use line count or test count alone as deletion justification.
- Do not propose deletion without a replacement or an explicit non-behavioral rationale.
- Do not classify architecture self-tests or domain/application purity checks as junk merely because they inspect source.

## Inputs

- `docs/testing/TEST_INVENTORY.md`
- `docs/testing/TEST_RATIONALIZATION_PLAN.md`
- `docs/agent/TEST_STRATEGY.md`
- Current `tests/` tree at `develop` after R08.
- Current `tools/run_headless_tests.py` and CI gates.

## Outputs

- `docs/testing/TEST_PORTFOLIO_AUDIT.md`
- `docs/testing/TEST_CLEANUP_PLAN.md`
- Implementer report at `docs/agent/reports/R02D-implementer.md`

## Interface Contract

- Audit identities use repository-relative path plus `Class.method`; no ambiguous method names alone.
- Every current `tests/**/test_*.py` file appears exactly once in a file summary.
- The method total reported by AST must match the current suite; discrepancies must be explained.
- Cleanup candidates must identify the behavior or gate that remains protected after removal/merge.
- The plan must be executable as independent cleanup batches, each with verification and rollback.

## Dependencies And Impact

- Depends on R08.
- Documentation-only impact.
- R02E and R09 depend on this audit.

## Acceptance

- Current test files and method counts are recorded accurately.
- Every `must-remove`, `merge`, or `rewrite` candidate has exact identity and replacement rationale.
- The audit explicitly covers the new `tests/domain`, `tests/application`, `tests/adapters`, and `tests/plugins` suites.
- The cleanup plan defines batches small enough for one reviewable commit each.
- No test or production file changes exist in the final diff.

## Risk And Rollback

- Risk: incomplete audit leaves duplicate coverage hidden.
- Mitigation: file summary completeness check plus AST count reconciliation.
- Rollback: revert the documentation commit; runtime tests are untouched.

## Garbage-Code Check

- Explicitly reject `inspect.getsource`, exact implementation-string assertions, private patch-only tests, mock-call-only wiring tests, duplicate table drift checks, and over-defensive DTO tests unless they protect a documented external contract.
- Preserve architecture AST checks because they enforce dependency direction rather than implementation wording.

## Git Requirement

```text
docs(R02D): audit test portfolio and cleanup candidates
```

## Report Paths

- Implementer: `docs/agent/reports/R02D-implementer.md`
- Reviewer: `docs/agent/reviews/R02D-review.md`

## Allowed Files

- `docs/testing/TEST_PORTFOLIO_AUDIT.md`
- `docs/testing/TEST_CLEANUP_PLAN.md`
- `docs/tasks/R02D-*.md`
- `docs/tasks/R02E-*.md`
- `docs/agent/reports/R02D-implementer.md`
- `docs/agent/reviews/R02D-review.md`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/TASK_BOARD.json`
- `docs/agent/HANDOFF.md`
- `TOTAL_TASK.md`

## Forbidden Files

- Any file under `tests/`
- Any production Python module under `src/` or legacy root modules
- `.github/**`
- `tools/**`
- `pyproject.toml`, `uv.lock`
- Runtime configuration, logs, screenshots, fixtures, and evaluation artifacts
