# R10 Implementer Report

## Status

APPROVED_FOR_COMMIT; not yet committed at report time.

- Branch: `feature/R10-turn-loop`
- Base SHA: `b95d61880f0b3520d123a696250344d67edb9b1e`
- Head SHA: same as base (`b95d61880f0b3520d123a696250344d67edb9b1e`); changes are uncommitted.

## Scope Completed

- Added zero-based `ActionIntent.destination` with exact `int | None` validation.
- Added pure deterministic `TurnIntentParser` for the R10 grammar.
- Added `PlayTurnCommand`, `PlayTurnStatus`, `PlayTurnResult`, and bounded `PlayTurnUseCase`.
- Wired application/bootstrap exports and `build_turn_use_case` / `build_play_turn_use_case` using the static default plugin registry.
- Documented the R10 interfaces in `docs/interfaces.md`.
- Added parser, destination, plugin-integration, loop, cancellation, ambiguity, stale, gate, budget, and journal tests.

## Changed Files

- `docs/interfaces.md`
- `src/hslegend/application/__init__.py`
- `src/hslegend/application/use_cases/__init__.py`
- `src/hslegend/application/use_cases/turn.py`
- `src/hslegend/application/use_cases/turn_intent.py`
- `src/hslegend/bootstrap/__init__.py`
- `src/hslegend/bootstrap/container.py`
- `src/hslegend/domain/actions.py`
- `tests/application/test_turn.py`
- `tests/application/test_turn_intent.py`
- `tests/domain/test_actions.py`

## Interfaces Produced / Consumed

- Consumed: `RecommendationSourcePort`, `GameStateSourcePort`, `ActionDispatchPort`, `RunJournalPort`, `CardCatalogPort`, `PlanBuilder`, `PlanExecutor`, `ActionGate`, `StopToken`, `ExecutionLedger`, and `PlanValidator`.
- Produced: `ActionIntent.destination`, `TurnIntentParser`, `TurnIntentError`, `PlayTurnCommand`, `PlayTurnResult`, `PlayTurnStatus`, `PlayTurnUseCase`, `build_turn_use_case`, and `build_play_turn_use_case`.
- `PlayTurnResult.actions` is the count of executor attempts recorded in the turn; `action_records` preserves each `ExecutionRecord`.
- Deadline expiry before the next dispatch maps to `failed` / `turn_timeout`; reaching `max_actions` maps to `completed` / `max_actions_reached`; confirmed end turn maps to `ended` / `end_turn` with `ended_turn=True`.

## Interface Decisions

- Text slots are one-based and resolved from the immutable snapshot by `(zone_pos, entity_id)`; duplicate `zone_pos` fails closed as `ambiguous_slot`.
- `choice_index` and `destination` are zero-based. Destination permits `N <= board_size + 1` so a card can be placed after the final board entity.
- Friendly and enemy slot targets resolve only from the asserted board/weapon/hero zone with side/zone ownership validation. Friendly hand-target specialization remains outside the R10 MVP grammar.
- Destination is accepted only on `play_card`; target is required for attacks and prohibited for end turn.
- The use case performs no card-family branching. Plugin conflicts and missing plugins are rejected before dispatch, preserving the R09 registry policy.
- A confirmed action is followed only after `current()` proves a strictly later revision in the same game generation; stale post-action state and generation changes stop the loop. Cancellation after confirmation takes precedence over an end-turn result.

## Verification

All commands were run with `uv` from the repository root.

| Command | Result |
| --- | --- |
| `uv sync --locked --all-groups` | exit 0; resolved 244 packages, audited 77 |
| `uv run python -m unittest tests.application.test_turn tests.application.test_turn_intent tests.application.test_planning tests.domain.test_actions -q` | exit 0; 55 tests, 55 passed |
| `uv run python -m unittest tests.application.test_turn tests.application.test_turn_intent tests.application.test_planning tests.application.test_mulligan tests.domain.test_actions tests.domain.test_validation tests.domain.test_models tests.plugins.test_registry tests.plugins.test_default_plugins -q` | exit 0; 132 tests, 132 passed |
| `$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py` | exit 0; 1495 tests, 1495 passed, 0 failed, 0 errors, 0 skipped |
| `$env:PYTHONUTF8='1'; uv run python -m unittest discover -s tests -t .` | exit 0; 1704 tests, 1704 passed |
| `$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py --opt-in` | exit 0; 19 tests, 19 passed, 0 failed, 0 errors, 0 skipped |
| `uv run python -m compileall -q src tests tools` | exit 0; no output |
| `uv run python tools/check_architecture.py` | exit 0; PASS, 38 files checked |
| `uv run python tools/check_architecture.py --self-test` | exit 0; PASS, 16/16 cases |
| `uv run python tools/check_task_scope.py --self-test` | exit 0; PASS, 8/8 cases |
| `git diff --check` | exit 0; no whitespace errors |

The full discovery run emitted pre-existing `ResourceWarning` messages for unclosed calibration log files in tests; the run still completed successfully with 1704/1704 passing.

## Scope Check

The complete R10 change set is staged before final review; the controller records the real non-empty scope result.

## Review Fix Round 1

- Staged the complete R10 implementation before re-review.
- Added strict post-action reconciliation by game generation and later log revision, preventing a second dispatch on stale state.
- Captured target kind and enforced actual board/weapon/hero zones plus side/zone ownership.
- Moved cancellation-after-confirmation ahead of the end-turn terminal branch.
- Added default-plugin routing regressions for attack, hero power, and location.
- Documented target-only/two-phase recommendation deferral to R13/R14.

## Risks

- The R10 parser does not resolve friendly hand targets for target lines; target-only/two-phase recommendations are deferred to R13/R14 dispatch integration with an explicit hand-vs-board rule.
- Target suffixes are enforced against board/weapon/hero zones, and every indexed collection is checked for side/zone ownership before sorting.
- If the default registry cannot uniquely select a plugin for a turn action, the use case rejects with `plugin_conflict` rather than adding turn-specific branching.
- Deadline expiry is represented as typed failure (`turn_timeout`) because the task contract has no separate timed-out turn status.
- No live adapter/OCR/CLI/Web/MCP integration was attempted, per scope.

## Rollback

Revert the R10 commit. R09 planning/executor and R08 interfaces remain unchanged and usable independently.
