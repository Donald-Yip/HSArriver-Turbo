# R02E Test Portfolio Cleanup

## Task ID

R02E

## Goal

Execute the reviewed R02D cleanup ledger to reduce maintenance-heavy and low-value tests while preserving all retained behavior contracts and deterministic headless gates.

## Scope

- Remove, merge, rewrite, or move only the exact candidates approved in R02D.
- Add a small static test-quality gate for prohibited test patterns that can be detected without false positives.
- Update the test inventory and CI documentation with the final portfolio.
- Keep each cleanup batch independently verifiable and reversible.

## Non-Goals

- No production behavior changes.
- No arbitrary target based only on test count.
- No deletion of architecture, security, fail-closed, cancellation, stale-state, or ambiguous-execution coverage.
- No new dependency, framework, browser runner, or native requirement.
- No mass directory migration in this task.

## Inputs

- Approved `docs/testing/TEST_CLEANUP_PLAN.md`.
- Exact candidate ledger from R02D.
- Current headless, architecture, scope, and compile gates.

## Outputs

- Cleaned test files.
- Updated `docs/testing/TEST_INVENTORY.md` and test strategy documentation.
- Optional `tools/check_test_quality.py` plus focused self-tests.
- Implementer/reviewer reports.

## Interface Contract

- Every deletion/merge maps to an R02D ledger entry.
- Prohibited-pattern checks run through uv and use AST/text checks only where false positives are controlled.
- The default test suite remains headless, deterministic, and opt-in free.
- CI still runs the full retained headless suite.

## Dependencies And Impact

- Depends on R02D.
- Test and testing-documentation impact only.
- R09 depends on R02E.

## Acceptance

- All approved cleanup batches complete or are explicitly deferred with reasons.
- Default suite has zero failures/errors and only documented explicit skips.
- Test count and runtime reduction are measured, not assumed.
- No retained contract loses its replacement coverage.
- Architecture self-tests and fail-closed behavior tests still pass.
- CI does not silently exclude tests; any exclusion is a reviewed static list with rationale.

## Risk And Rollback

- Risk: deleting a test that protects an undocumented but real contract.
- Mitigation: R02D exact ledger, batch-by-batch replacement evidence, full headless regression.
- Rollback: revert the cleanup commit/batch; the audit preserves the original identities and rationale.

## Garbage-Code Check

- Delete exact implementation-string and private-wiring tests only after a stable observable/structural replacement exists.
- Do not replace one brittle test with several trivial tests.
- Prefer one scenario test over multiple mock-call assertions when behavior is equivalent.

## Git Requirement

```text
test(R02E): rationalize the test portfolio
```

## Report Paths

- Implementer: `docs/agent/reports/R02E-implementer.md`
- Reviewer: `docs/agent/reviews/R02E-review.md`
