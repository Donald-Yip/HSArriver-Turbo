# R12 One-Game Application Flow And CLI

## Task ID

R12

## Goal

Add an installable, headless one-game application flow that executes the existing
enter-battle, mulligan, turn, and settlement use cases through deterministic fake
ports, exposes it as `uv run hslegend-onegame --fake`, and proves a complete game
stops after exactly one settlement.

## Scope

- Make `src/hslegend` an installable package with a console script.
- Replace the placeholder `OneGameCommand/Result/Status` DTOs with the real
  application-level one-game contract and re-export the names from
  `hslegend.application`.
- Add a thin `OneGameUseCase` orchestrator over the existing R08-R11 use cases.
- Add deterministic demo adapters that drive the real use cases, not a fake
  orchestrator.
- Add a CLI entrypoint with `--fake`, bounded game options, JSON output, and
  explicit exit codes.
- Add a bootstrap factory for the fake one-game composition.
- Add a top-level headless end-to-end test from main menu to result.
- Extend the static architecture gate so entrypoints cannot import adapters,
  plugins, or legacy/root modules.
- Add CLI/import/help/fake-smoke gates to CI.

## Non-Goals

- No real Hearthstone, HSAng, Power.log, OCR, Win32, mouse, screenshot, config,
  or legacy adapter wiring; R13 owns native cutover.
- No Web, MCP, overlay, scheduler, watchdog, surrender, rewards, rematch, or
  second-game behavior.
- No OCR/result text extraction.
- No dynamic plugin discovery, hot reload, async rewrite, new dependency, or
  database.
- No changes to coordinates, delays, thresholds, or click sequences.
- No edits to the existing HTTP/CLI/MCP external contracts.

## Inputs

- Task book and `docs/interfaces.md`.
- Branch `feature/R12-one-game-cli`, based on R11 commit `2426366`.
- Existing R08-R11 application services and ports.
- Existing headless fake-port patterns.

## Outputs

- Installable `src/hslegend` package and `hslegend-onegame` console script.
- `OneGameCommand`, `OneGameStatus`, `OneGameResult`, and `OneGameUseCase`.
- Deterministic demo adapters and fake bootstrap factory.
- CLI entrypoint.
- One comprehensive top-level fake end-to-end module plus focused contract tests.
- Updated interfaces, architecture rules, CI, task state, report, and review.

## Interface Contract

```text
OneGameCommand(
  command_id,
  deck_index=0,
  max_turns=20,
  timeout_seconds=900,
  enter_battle_timeout_seconds=60,
  mulligan_timeout_seconds=20,
  turn_timeout_seconds=90,
  max_actions_per_turn=10,
  poll_interval_seconds=0.25,
)

OneGameStatus = completed | cancelled | timed_out | failed

OneGameResult(
  command_id,
  session_id,
  status,
  reason_code,
  turns_played,
  actions_played,
  last_lobby_phase,
  evidence_ids,
  enter_battle,
  mulligan,
  turn_results,
  finish_battle,
)
```

- The total deadline is monotonic and bounds all child use-case budgets.
- `EnterBattle` runs once with `start_match=True` and the requested deck index.
- The flow waits for `LobbyPhase.IN_GAME` before mulligan; if `RESULT` appears
  first, it skips directly to settlement.
- Mulligan runs at most once.
- A local turn is delegated exactly once per detected local-turn snapshot.
- `PlayTurnStatus.ENDED/local_turn_ended` waits for a later state revision.
- `PlayTurnStatus.ENDED/game_ended` transitions to settlement.
- `PlayTurnStatus.COMPLETED` is fail-closed as
  `turn_action_budget_exhausted`; one turn slice cannot silently continue.
- Settlement runs at most once and only after game end/direct RESULT.
- Any cancelled child result stops immediately with `cancelled`.
- Any failed/rejected/unconfirmed/gate-busy child result stops immediately with
  `failed` and never retries or starts another game.
- `completed` requires `FinishBattleStatus.COMPLETED`.
- Evidence IDs preserve deterministic order and de-duplicate within the result.

## CLI Contract

```text
uv run hslegend-onegame --help
uv run hslegend-onegame --fake --json
uv run hslegend-onegame --fake --deck-index 0 --max-turns 20 --timeout-seconds 900
```

- R12 requires `--fake`; omitting it exits 2 with an actionable message that
  native adapters arrive in R13.
- Completed fake game exits 0; any non-completed result exits 1; CLI usage errors
  exit 2.
- `--json` writes one machine-readable object to stdout and no logs.
- The CLI must not import adapters, plugins, or legacy modules directly; it calls
  a bootstrap factory.
- The fake scenario is deterministic, uses no wall-clock sleeps or network, and
  dispatches exactly:
  `enter_traditional`, `select_deck`, `start_match`, `mulligan_confirm`,
  `end_turn`.
- The fake scenario ends at `LobbyPhase.RESULT` and must not dispatch anything
  after `end_turn`.

## Garbage-Code Check

- Remove the unused placeholder one-game DTOs from `application/dto.py`; the
  real immutable contract lives with the one-game use case but remains exported
  from `hslegend.application`.
- Do not keep duplicate demo/test orchestrators or duplicate scenario tables.
- Reuse existing ports and use cases; do not clone their state machines.
- Do not add tests for impossible frozen-dataclass mutations, source strings,
  private call sequences, or implementation detail.

## Allowed Files

- `pyproject.toml`
- `uv.lock`
- `.github/workflows/ci.yml`
- `README.md`
- `src/hslegend/application/**`
- `src/hslegend/bootstrap/**`
- `src/hslegend/adapters/demo/**`
- `src/hslegend/entrypoints/**`
- `tests/application/test_dto.py`
- `tests/application/test_one_game.py`
- `tests/test_one_game_e2e.py`
- `tools/check_architecture.py`
- `docs/interfaces.md`
- `docs/architecture/layers.md`
- `docs/tasks/R12-*.md`
- `docs/agent/reports/R12-*.md`
- `docs/agent/reviews/R12-*.md`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/TASK_BOARD.json`
- `docs/agent/HANDOFF.md`
- `TOTAL_TASK.md`

## Forbidden Files

- All legacy/root production modules except documentation explicitly listed.
- Existing adapter implementations under `src/hslegend/adapters/**` other than
  the new `demo/**` package.
- OCR, Win32, coordinates, configs, logs, screenshots, and native-live tests.
- Existing R08-R11 application use-case implementations.
- Existing non-R12 tests outside the explicit list.

## Acceptance

```powershell
uv lock --check
uv sync --locked --all-groups
uv run python -c "import hslegend; print(hslegend.__file__)"
uv run hslegend-onegame --help
uv run hslegend-onegame --fake --json
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/check_architecture.py --self-test
uv run python tools/check_task_scope.py --task R12 --json
uv run python tools/check_task_scope.py --self-test
uv run python tools/run_headless_tests.py
uv run python -m unittest discover -s tests -t .
uv run python tools/run_headless_tests.py --opt-in
git diff --cached --check
```

Behavioral acceptance:

- A fake run executes the real R08-R11 use cases through fake ports.
- The observed lobby sequence is main menu -> deck select -> in game -> result.
- Exactly one mulligan, one local turn slice, and one settlement occur.
- The only five dispatches are the sequence declared above.
- The result is `completed`, one turn and one action are reported, and evidence
  includes the recommendation, snapshot, dispatch, and result evidence.
- A cancelled or failed child result prevents all later child calls.
- CLI JSON contains command ID, status, reason, turns, actions, final phase, and
  evidence IDs.
- No wall-clock sleep, network, native API, or legacy import is used by the fake
  path.

## Risk And Rollback

- Risk: packaging changes break test imports or shadow the repository's root
  `click.py`.
- Control: console script uses stdlib argument parsing; run import and CLI gates
  from both direct and test contexts.
- Risk: the orchestrator duplicates child use-case policy or retries actions.
- Control: orchestrate only typed child results; stop on the first unsafe result.
- Risk: fake adapters bypass the real flow and prove nothing.
- Control: wire real R08-R11 use cases and assert their execution evidence.
- Rollback: revert the R12 commit; R11 remains usable and no external API or data
  migration is involved.

## Git Requirement

```text
feat(R12): add one-game application flow and fake CLI
```

## Report Paths

- Implementer: `docs/agent/reports/R12-implementer.md`
- Reviewer: `docs/agent/reviews/R12-review.md`