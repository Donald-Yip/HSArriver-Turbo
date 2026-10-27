# R08 Enter-Battle Use Case And Bootstrap

## Task ID

R08

## Goal

Implement the first end-to-end application slice: main menu -> traditional battle -> deck selection -> optional start queue, with a composition root and deterministic fake tests.

## Scope

- Add `EnterBattleUseCase`.
- Add semantic lobby action plans/steps.
- Add minimal bootstrap container.
- Add tests for happy path, unknown lobby, dispatch rejection/error, blocking
  timeout, cancellation, release-failure reporting, and action gate contention.
- Add deadline-aware `LobbyStatePort` and `ActionDispatchPort` signatures and
  propagate the remaining budget through R07 adapters, executor, fakes, and tests.
- Enforce timeout even for callbacks that ignore cooperative timeout signalling
  through a shared daemon-worker deadline helper with a bounded join.
- Keep legacy runtime behavior untouched; bootstrap accepts injected adapters.

## Non-Goals

- No actual legacy factory/entrypoint wiring yet.
- No battle-turn logic, OCR recommendation flow, or settlement.
- No UI/CLI changes.

## Allowed Files

- `src/hslegend/application/use_cases/**`
- `src/hslegend/bootstrap/**`
- `src/hslegend/application/ports/__init__.py` only for approved timeout-contract fix
- `src/hslegend/application/planning/executor.py` only for approved timeout propagation
- `src/hslegend/adapters/deadline.py` only for the shared bounded-callback helper
- `src/hslegend/adapters/lobby/**` only for approved timeout-contract fix
- `src/hslegend/adapters/input/**` only for approved timeout-contract fix
- `tests/application/**`
- `tests/adapters/**` only for approved timeout-contract tests
- `docs/interfaces.md`
- `docs/architecture/**`
- `docs/tasks/R08-*.md`
- `docs/agent/**`
- `TOTAL_TASK.md`

## Forbidden Files

- Legacy root modules and real-adapter factory/entrypoint wiring.
- Domain models and unrelated application ports.
- Coordinates, delays, OCR, card data.

## Acceptance

- Use case sequence is deterministic and fail-closed.
- No click/dispatch after cancellation.
- Unknown lobby state does not guess.
- Dispatch rejection/exception stops the sequence with typed result.
- ActionGate prevents concurrent operations.
- Timeout is bounded and testable with fake clock; blocking port calls receive
  the remaining budget and raise `TimeoutError` within the budget even when
  injected callbacks ignore timeout signalling.
- A `SENT` dispatch is retained in the result if cancellation arrives afterward.
- Gate release failures are reported without replacing the primary status.
- Composition root constructs use case from injected ports only.
- Headless/architecture/scope/all layer tests pass.

## Risk And Rollback

- Risk: lobby UI timing assumptions leak into application.
- Mitigation: poll through LobbyStatePort with bounded timeout and deterministic clock.
- Rollback: delete R08 use-case/bootstrap/tests; R07 adapters remain.

## Git Requirement

```text
feat(R08): add enter-battle vertical slice
```

## Report Paths

- Implementer: `docs/agent/reports/R08-implementer.md`
- Reviewer: `docs/agent/reviews/R08-review.md`
