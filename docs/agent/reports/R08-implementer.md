# R08 Implementer Report

## Status

APPROVED_FOR_COMMIT

## Branch And Base

- Branch: `feature/R08-enter-battle`
- Base SHA: `082255e`
- Head SHA: `082255e` (no commit created)
- Commit/push: not performed

## Changed Files

- `src/hslegend/application/use_cases/__init__.py`
- `src/hslegend/application/use_cases/enter_battle.py`
- `src/hslegend/application/ports/__init__.py`
- `src/hslegend/application/planning/executor.py`
- `src/hslegend/adapters/lobby/__init__.py`
- `src/hslegend/adapters/input/__init__.py`
- `src/hslegend/bootstrap/__init__.py`
- `src/hslegend/bootstrap/container.py`
- `tests/application/fakes.py`
- `tests/application/test_enter_battle.py`
- `tests/application/test_planning.py`
- `tests/application/test_ports.py`
- `tests/adapters/test_external_adapters.py`
- `docs/interfaces.md`
- `docs/tasks/R08-enter-battle.md`
- `docs/agent/HANDOFF.md`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/TASK_BOARD.json`
- `TOTAL_TASK.md`
- `docs/agent/reports/R08-implementer.md`

No legacy root module, real-adapter factory/entrypoint wiring, domain model,
coordinate, delay, OCR, card-data, UI, or CLI behavior was changed.

## Fix Round 1

1. Deadline-aware ports
   - `LobbyStatePort.get_state(*, timeout_seconds)` and
     `ActionDispatchPort.dispatch(..., *, timeout_seconds)` now require the
     caller's remaining budget.
   - `EnterBattleUseCase` passes the remaining total deadline to every lobby read
     and dispatch. `TimeoutError` becomes a terminal `TIMED_OUT` result with no
     retry or later dispatch.
   - `PlanExecutor` passes its remaining plan budget to dispatch.
   - `LegacyLobbyStateAdapter` and `LegacyActionDispatch` forward the budget to
     timeout-aware injected callables and propagate `TimeoutError`; legacy
     two-argument callbacks remain supported.
   - Application fakes, adapter tests, and enter-battle fakes accept and record
     timeout budgets. Blocking fakes raise `TimeoutError` and are covered by
     tests.
2. Post-dispatch cancellation
   - The dispatch and its evidence are appended before cancellation is observed.
   - A `SENT` result followed by cancellation returns `CANCELLED` with
     `detail_code="cancelled_after_dispatch"`, the affected `failed_action`, and
     the retained dispatch/evidence.
3. Release failures
   - `EnterBattleResult.release_error` records the release exception type/message.
   - The primary status and reason are preserved; a completed sequence stays
     `COMPLETED` while exposing release failure.
4. State and scope
   - Current phase/branch now point to R08 on `feature/R08-enter-battle`.
   - R08 task scope explicitly allows the approved port/executor/R07-adapter
     timeout fix and related tests.

## Base Implementation

- Added `EnterBattleUseCase` with the fixed semantic sequence:
  `MAIN_MENU -> enter_traditional -> DECK_SELECT -> select_deck(index) ->
  optional start_match -> typed result`.
- Added declarative `LobbyAction` and `build_lobby_step()` values. `select_deck`
  carries the requested index in `ActionIntent.choice_index`.
- `UNKNOWN` lobby observations fail immediately with `lobby_unknown`; they never
  trigger a guessed click.
- Dispatch rejection, failure, exception, invalid output, and action-gate
  contention return typed results and stop the sequence.
- Cancellation is checked before gate acquisition, before/after lobby reads,
  immediately before dispatch, and after dispatch. No later action is dispatched
  after cancellation.
- The action gate lease covers the whole sequence and is released on all paths.
- Added `bootstrap.build_container()` and `build_enter_battle_use_case()` as the
  minimal composition root. They import application contracts/use case only and
  accept injected adapters/runtime controls; no legacy factory is imported.

## Verification

| Command | Exit | Result |
| --- | ---: | --- |
| `uv sync --locked --all-groups` | 0 | Resolved 244 packages; audited 77 packages |
| `uv run python -m unittest tests.application.test_enter_battle -v` | 0 | 16 tests passed |
| `uv run python -m unittest discover -s tests/domain -t . -p "test_*.py" -q` | 0 | 38 tests passed |
| `uv run python -m unittest discover -s tests/adapters -t . -p "test_*.py" -q` | 0 | 52 tests passed |
| `uv run python -m unittest discover -s tests/application -t . -p "test_*.py" -q` | 0 | 60 tests passed |
| `uv run python -m unittest discover -s tests/plugins -t . -p "test_*.py" -q` | 0 | 9 tests passed |
| `uv run python -m compileall -q src tests tools` | 0 | No diagnostics |
| `uv run python tools/check_architecture.py` | 0 | `[architecture] PASS: 34 files checked` |
| `uv run python tools/check_architecture.py --self-test` | 0 | `PASS: 16/16 cases` |
| `uv run python tools/run_headless_tests.py` | 0 | run=1525 passed=1524 failures=0 errors=0 skipped=1 |
| `uv run python tools/check_task_scope.py --task R08 --json` | 0 | `ok: true`; 20 staged files, zero violations |
| `uv run python tools/check_task_scope.py --self-test --json` | 0 | `ok: true`; 8/8 cases |

## Scope Check

All R08 implementation, adapter timeout-fix, test, interface, task, and agent
state files are staged explicitly. The final scope and whitespace checks are run
after this report is added. No commit or push was performed.

## New Dependencies And Public API

- New runtime dependencies: none.
- New application API: `EnterBattleUseCase`, `EnterBattleCommand`,
  `EnterBattleResult`, `EnterBattleStatus`, `EnterBattleDispatch`, `LobbyAction`,
  and `build_lobby_step()`.
- New bootstrap API: `Container`, `build_container()`, and
  `build_enter_battle_use_case()`.
- Timeout-breaking port API: `LobbyStatePort.get_state(timeout_seconds=...)` and
  `ActionDispatchPort.dispatch(..., timeout_seconds=...)`.

## Known Risks And Review Focus

- `start_match` completion means the semantic action was dispatched successfully;
  the use case deliberately does not claim `MATCHING`/`IN_GAME` without another
  lobby observation.
- Lobby actions use an immutable zero-game `LOBBY_SNAPSHOT` only because the
  existing shared `ActionDispatchPort` requires a `GameSnapshot`. It contains no
  inferred battlefield state.
- Forwarding timeout support is structural: timeout-aware injected callables
  receive the budget, while legacy callables remain callable for compatibility.
  The port contract, not a background worker, owns the timeout boundary.
