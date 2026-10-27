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

## Current Increment

Stage 1 (`B1/B2/B5`) and stage 2A (`B6`) are independently approved.

Stage 2B executes B9, B8, then B7 from `docs/testing/TEST_CLEANUP_PLAN.md`:

- B9: move O12 repository hygiene to an explicit opt-in tooling gate.
- B8: move O04-O11/O14 environment checks to opt-in tooling; retain one static manifest check in default.
- B7: move O02/O03/O13 to CI opt-in and O01 to a separate native-live gate; retain one static MCP schema check in default.
- Required CI opt-in must fail on missing prerequisites; the live-window gate is explicit and never runs in ordinary CI.
- Stage target: default `1,511`, discover `1,673`, CI opt-in `16`, native-live `1`.

B3/B4 remain deferred.

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

## Allowed Files

- `tests/test_web_hot_control.py`
- `tests/test_draw_extra_delay_settings.py`
- `tests/test_screen_regions.py`
- `tests/test_roi_config_frame.py`
- `tests/application/test_dto.py`
- `tests/adapters/test_external_adapters.py`
- `tests/test_web_logs_wiring.py`
- `tests/test_web_prepare_flow.py`
- `tests/test_env_provision.py`
- `tests/test_uv_environment.py`
- `tests/test_selfcheck.py`
- `tests/test_mcp_server.py`
- `tests/test_display_mapping.py`
- `tests/tooling/**`
- `tests/native/**`
- `tests/helpers/**`
- `tools/run_headless_tests.py`
- `.github/workflows/ci.yml`
- `docs/testing/TEST_INVENTORY.md`
- `docs/testing/TEST_RATIONALIZATION_PLAN.md`
- `docs/testing/TEST_PORTFOLIO_AUDIT.md`
- `docs/testing/TEST_CLEANUP_PLAN.md`
- `docs/testing/CI_GATES.md`
- `docs/tasks/R02E-*.md`
- `docs/agent/reports/R02E-*.md`
- `docs/agent/reports/R02D-implementer.md`
- `docs/agent/DECISIONS.md`
- `docs/agent/reviews/R02E-*.md`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/TASK_BOARD.json`
- `docs/agent/HANDOFF.md`
- `TOTAL_TASK.md`

## Forbidden Files

- Any production module under `src/` or legacy root modules
- Any test file not listed above
- `pyproject.toml`, `uv.lock`
- Runtime configs, logs, screenshots, and Hearthstone interactions in default/CI opt-in gates

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
test(R02E-B1): remove private table drift checks
test(R02E-B2): merge duplicate DTO boundary assertions
test(R02E-B5): replace mock-only web test assertions
test(R02E-B6): replace wall-clock timeout assertions
test(R02E-B9): isolate repository hygiene check
test(R02E-B8): isolate environment smoke checks
test(R02E-B7): isolate native and MCP integration checks
```

## Report Paths

- Implementer: `docs/agent/reports/R02E-implementer.md`
- Reviewer: `docs/agent/reviews/R02E-review.md`
