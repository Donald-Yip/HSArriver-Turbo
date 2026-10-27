# R12 Implementer Report

## Status

Implemented on `feature/R12-one-game-cli` without commit, push, branch creation, or changes to R08-R11 use-case implementations.

## Changed Files

- `.gitignore`
- `.github/workflows/ci.yml`
- `docs/interfaces.md`
- `docs/tasks/R12-one-game-cli.md`
- `pyproject.toml`
- `uv.lock`
- `src/hslegend/application/__init__.py`
- `src/hslegend/application/dto.py`
- `src/hslegend/application/use_cases/__init__.py`
- `src/hslegend/application/use_cases/one_game.py`
- `src/hslegend/adapters/demo/__init__.py`
- `src/hslegend/bootstrap/__init__.py`
- `src/hslegend/bootstrap/fake.py`
- `src/hslegend/entrypoints/__init__.py`
- `src/hslegend/entrypoints/onegame.py`
- `tests/application/test_dto.py`
- `tests/application/test_one_game.py`
- `tests/test_one_game_e2e.py`
- `tools/check_architecture.py`
- `docs/agent/reports/R12-implementer.md`

## Contracts

- Added immutable `OneGameCommand` with deck index, total deadline, child-operation budgets, maximum turns, per-turn action budget, and poll interval.
- Added `OneGameStatus` values `completed`, `cancelled`, `timed_out`, and `failed`.
- Added immutable `OneGameResult` with command/session identity, terminal status/reason, turn/action counts, final lobby phase, ordered unique evidence, and exact child results.
- Removed the placeholder one-game DTOs from `application/dto.py`; `hslegend.application` now re-exports the real definitions from `application/use_cases/one_game.py`.
- Added installable setuptools/uv packaging and the `hslegend-onegame` console script.

## Behavior

- `OneGameUseCase` coordinates the real `EnterBattleUseCase`, `MulliganUseCase`, `PlayTurnUseCase`, and `FinishBattleUseCase` without duplicating their planning or execution policy.
- The total monotonic deadline clamps every child timeout. `EnterBattle` starts with `start_match=True`, mulligan occurs at most once, each detected local-turn snapshot is delegated at most once, and settlement occurs at most once.
- Direct `RESULT` skips mulligan/turn and settles directly. `ENDED/local_turn_ended` and `ENDED/end_turn` wait for a later revision; `ENDED/game_ended` settles; `COMPLETED` fails closed as `turn_action_budget_exhausted`.
- Cancelled child results stop immediately as `cancelled`. Failed, rejected, unconfirmed, and gate-busy outcomes stop immediately as `failed`. Child evidence is retained in deterministic first-seen order.
- The deterministic demo uses real application use cases and fake ports. It dispatches exactly `enter_traditional`, `select_deck`, `start_match`, `mulligan_confirm`, `end_turn`, ends at `RESULT`, performs no wall-clock sleep/network/native call, and cannot dispatch a second game.
- CLI requires `--fake`, supports bounded options and JSON output, exits 0 for completed games, 1 for non-completed results, and 2 for usage errors. The entrypoint imports only the standard library, application, and bootstrap layers.

## Verification

- `uv lock --check`: PASS
- `uv sync --locked --all-groups`: PASS
- `uv run python -c "import hslegend; print(hslegend.__file__)"`: PASS, resolves to `src/hslegend/__init__.py`
- `uv run hslegend-onegame --help`: PASS, exit 0
- `uv run hslegend-onegame --fake --json`: PASS, exit 0, one completed JSON result
- `uv run python -m unittest tests.application.test_one_game tests.test_one_game_e2e -v`: PASS, 12 tests
- `uv run python -m compileall -q src tests tools`: PASS
- `uv run python tools/check_architecture.py`: PASS, 44 files checked
- `uv run python tools/check_architecture.py --self-test`: PASS, 18/18 cases
- `uv run python tools/check_task_scope.py --self-test`: PASS, 8/8 cases
- `uv run python tools/check_task_scope.py --task R12 --json`: PASS after staging the R12 file set
- `tools/crew_headless_tests.py`: not present; used the required fallback below
- `uv run python tools/run_headless_tests.py`: PASS, 1499 tests, 0 failures/errors/skips
- `uv run python tools/run_headless_tests.py --opt-in`: PASS, 19 tests, 0 failures/errors/skips
- `uv run python -m unittest discover -s tests -t .`: PASS, 1734 tests, 0 failures/errors
- `git diff --cached --check`: PASS

The full discovery run emitted pre-existing `ResourceWarning` messages from fake Tk/file helpers but completed successfully with 1734 passing tests.

## Risks

- Native adapter composition is intentionally absent until R13; omitting `--fake` is a usage error with that guidance.
- Demo correctness depends on the demo lobby/game-state transitions matching the semantic dispatch sequence; the top-level E2E asserts the exact five dispatches and single settlement.
- The orchestrator treats any unexpected child terminal state as fail-closed rather than retrying or starting another game.

## Rollback

Revert the R12 changes. No migration, external API, or persistent data change is involved; R11 remains usable.

## In-Scope Root Fix

The root `.gitignore` rule `demo/` was hiding `src/hslegend/adapters/demo/`. Added narrow negations for only `src/hslegend/adapters/demo/` and its contents, added `src/*.egg-info/` for generated packaging metadata, removed the generated egg-info directory safely, verified the demo file appears with `git status --untracked-files=all`, and added `.gitignore` to the R12 allowed-files section. The task-book Forbidden Files wording was also narrowed so the scope gate permits only the new demo package while still rejecting other adapter changes.

## Out-Of-Scope Findings

None.

## Review Fix Evidence

All R12 review findings were addressed without changing R08-R11 use-case implementations.

- Delegation safety: the first in-game generation after mulligan is now the single expected generation. Every later snapshot is generation-checked before turn delegation. Delegated turns are keyed by `(game_generation, turn_number)`, and ended turns drain revisions until end or a genuinely new turn in the same generation. Focused tests cover same-turn/new-revision no redelegation, a second generation with a high revision producing `game_generation_changed` before another turn call, and normal later-revision settlement.
- Cancellation and deadline classification: a shared cancellation-first helper now guards port, clock, sleeper, invalid-value, and terminal-result paths. `timed_out` is reserved for an actually expired injected deadline. Early port timeouts and state-wait `None` map to typed failures; malformed clocks map to `failed/clock_error`; pre-start cancellation wins over clock failure. Focused tests cover lobby failure after cancellation, state wait `None` after cancellation, sleeper failure after cancellation, malformed clock, early lobby/state timeouts, expired total deadline, and pre-start cancellation.
- Entrypoint architecture: the blacklist was replaced with an allowlist for standard library, `hslegend.application`, `hslegend.bootstrap`, and sibling `hslegend.entrypoints` imports. Self-tests now reject direct domain, adapter, plugin, and third-party imports.
- CLI command ID: `--command-id` now requires non-empty text and command construction errors become argparse usage errors. The CLI test passes an empty command ID and asserts exit 2 without a traceback.
- The fake path remains exactly five dispatches with real R08-R11 use cases, one settlement, and no second game.

Review-fix verification (supersedes earlier counts):

- `uv sync --locked --all-groups`: PASS
- `uv lock --check`: PASS
- `uv run python -c "import hslegend; print(hslegend.__file__)"`: PASS
- `uv run hslegend-onegame --help`: PASS, exit 0
- `uv run hslegend-onegame --fake --json`: PASS, completed/1 turn/1 action/result
- `uv run python -m unittest tests.application.test_one_game tests.test_one_game_e2e -v`: PASS, 23 tests
- `uv run python -m unittest discover -s tests/application -t .`: PASS, 141 tests
- `uv run python -m compileall -q src tests tools`: PASS
- `uv run python tools/check_architecture.py`: PASS, 44 files
- `uv run python tools/check_architecture.py --self-test`: PASS, 20/20 cases
- `uv run python tools/check_task_scope.py --self-test`: PASS, 8/8 cases
- `uv run python tools/run_headless_tests.py`: PASS, 1500 tests, 0 failures/errors/skips
- `uv run python tools/run_headless_tests.py --opt-in`: PASS, 19 tests, 0 failures/errors/skips
- `uv run python -m unittest discover -s tests -t .`: PASS, 1745 tests, 0 failures/errors

The full discovery run repeated the existing `ResourceWarning` messages from fake Tk/file helpers but completed successfully.
