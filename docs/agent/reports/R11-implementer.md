# R11 Implementer Report

## Status

FIXES_ADDRESSED / READY_FOR_REVIEW

## Scope

- Added observation-only `FinishBattleUseCase` and immutable command/result DTOs.
- Added bounded lobby polling with injected clock/sleeper.
- Added application/bootstrap exports without dispatch or legacy dependencies.
- Removed redundant confidence validation that could only be reached by mutating a frozen valid `LobbyState`.
- Removed the brittle constructor-introspection test; architecture/import gates and independent review enforce the observation-only boundary.

## Changed Files

- `docs/interfaces.md`
- `src/hslegend/application/use_cases/finish_battle.py`
- `src/hslegend/application/use_cases/__init__.py`
- `src/hslegend/application/__init__.py`
- `src/hslegend/bootstrap/container.py`
- `src/hslegend/bootstrap/__init__.py`
- `tests/application/test_finish_battle.py`
- `docs/agent/reports/R11-implementer.md`

## Behavior

- Immediate/delayed RESULT -> completed.
- StopToken before polls, between polls, or while a port/sleeper call is in flight -> cancelled with the last successful observation preserved.
- The injected monotonic deadline is the only source of `timed_out`.
- Adapter `TimeoutError` before the deadline -> failed with `lobby_state_timeout`; at/after the deadline -> timed_out.
- Generic adapter errors, invalid observations, clock failures, and sleeper failures -> typed failed outcomes with phase/evidence preserved.
- Evidence IDs deduplicated; elapsed time non-negative.
- The use case exposes no dispatch seam and never starts another game.
- Non-RESULT states are polled until the total deadline.

## Verification

- Focused finish-battle tests: 18/18 passed.
- Changed-module compile: passed.
- Architecture check: 39 files checked, no violations or cycles; self-test passed.
- R11 task scope gate: passed with no violations.

## Risks

- The real result screen may require a UI action to dismiss; intentionally deferred to R13, because clicking a generic "start" control could start a second game.
- Result/winner text extraction is deferred.

## Rollback

Revert the R11 commit; R10 remains independent.

## Review Fix Report

- Adapter timeout classification now samples the injected monotonic clock: early adapter timeout is `failed/lobby_state_timeout`; timeout at/after the command deadline is `timed_out/timeout`.
- Cancellation is rechecked after successful and failed lobby reads, invalid observations, clock failures, and sleeper success/failure before classification; in-flight cancellation therefore wins over a failing port or sleeper.
- Added behavior coverage for early/deadline adapter timeout, cancellation masking `TimeoutError`, generic exceptions, invalid state, and sleeper failure, plus sleeper failure preserving phase/evidence. Removed the constructor-introspection test.
- Focused command `uv run python -m unittest tests/application/test_finish_battle.py -v`: `Ran 18 tests in 0.001s`, `OK`.
- `uv run python -m py_compile src/hslegend/application/use_cases/finish_battle.py`: exit 0.
- `uv run python tools/check_architecture.py --json`: `ok: true`, 39 files checked, no violations, no cycles.
- `uv run python tools/check_architecture.py --self-test --json`: `ok: true`, all 16 cases passed.
- `uv run python tools/check_task_scope.py --task R11 --json`: `ok: true`, no violations.
- No dispatch or next-game behavior was added; no commit was created.

## Round 2 Regression Fix

- Pre-poll cancellation now returns a direct `CANCELLED` result with `elapsed_seconds=0.0` before the start clock is sampled, so it performs no clock read and cannot report absolute clock uptime.
- Added `test_cancellation_before_poll_reports_zero_elapsed_without_clock_read` using `FakeClock(now=100.0)`; it asserts `cancelled`, reason `cancelled`, `UNKNOWN` phase, `0.0` elapsed, zero clock calls, and zero lobby reads.
- Updated the interface contract to state that pre-start cancellation returns exactly `0.0` elapsed without reading the clock.
- Focused command `uv run python -m unittest tests/application/test_finish_battle.py -v`: `Ran 19 tests in 0.003s`, `OK`.
- `uv run python -m py_compile src/hslegend/application/use_cases/finish_battle.py`: exit 0.
- `uv run python tools/check_architecture.py --json`: `ok: true`, 39 files checked, no violations, no cycles.
- `uv run python tools/check_architecture.py --self-test --json`: `ok: true`, all 16 cases passed.
- `uv run python tools/check_task_scope.py --task R11 --json`: `ok: true`, no violations.
- No index, review-report, or history changes were made by this regression fix.
