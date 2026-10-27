# R10 Turn Loop And Action Reconciliation

## Task ID

R10

## Goal

Implement one bounded in-game turn loop that repeatedly reads a normalized TURN recommendation, converts it to a declarative `ActionIntent`, builds a plan through the application `PlanBuilder`, executes it through `PlanExecutor`, and stops after `end_turn`, loss of the local turn, cancellation, ambiguity, or max-action budget.

## Scope

- Add a deterministic parser for the MVP turn instructions:
  - `结束回合`
  - `使用英雄技能`
  - `打出N号位随从|法术|武器|地标|英雄`
  - `操作N号位随从攻击`
  - `操作我方英雄攻击`
  - `操作N号位地标`
  - target lines `目标是对方/敌方N号位...`, `目标是我方/己方N号位...`, and enemy/friendly hero targets
- Resolve slot references against one immutable snapshot in deterministic `zone_pos` order.
- Add optional `ActionIntent.destination` for textual `放置于我方N号位`; otherwise reject/ignore only when no destination is present.
- Add `PlayTurnCommand`, typed statuses/result, `TurnIntentParser`, and `PlayTurnUseCase`.
- Use the injected `PlanBuilder` and default plugin registry, not card-specific branching inside the use case.
- Track one action at a time with the shared `ActionGate`, ledger and journal; never loop after `executed_unconfirmed`.
- Stop after `end_turn`, a post-action snapshot showing it is no longer the local turn, or max action count.
- Preserve the action result and journal evidence in the turn result.

## Non-Goals

- No settlement/result-screen handling, second game, mulligan, OCR/capture/Win32, Web/CLI/MCP, native loop, or dynamic plugin loading.
- No combat AI or hidden card inference.
- No expansion of unsupported parser grammar beyond the listed MVP instructions.
- Target-only/two-phase recommendations are deferred to R13/R14 dispatch integration; R10 accepts action+target in one recommendation.
- No changes to coordinates, delays, configuration, or legacy root modules.

## Inputs

- `RecommendationSourcePort.recommend(snapshot, RecommendationMode.TURN)`.
- `GameStateSourcePort.current()` and `wait_for_revision_change()`.
- Injected `PlanBuilder`, `PlanExecutor`, `ActionGate`, `StopToken`, journal and clock.
- `CardCatalogPort` through bootstrap.

## Outputs

- `src/hslegend/application/use_cases/turn_intent.py`
- `src/hslegend/application/use_cases/turn.py`
- application/bootstrap exports
- focused parser, loop, cancellation, ambiguity, and plugin-integration tests
- updated `docs/interfaces.md`

## Interface Contract

```text
PlayTurnCommand(command_id, max_actions, timeout_seconds)
PlayTurnStatus = completed | ended | unconfirmed | rejected | failed | cancelled | gate_busy
PlayTurnResult(command_id, status, actions, ended_turn, reason_code, evidence_ids, action_records)
```

- Parser returns structured `ActionIntent` or a stable rejection code.
- Entity slots are 1-based in recommendation text and zero-based in intent `choice_index`.
- A recommendation must be TURN mode and belong to the current snapshot.
- `end_turn` plan must use the registered EndTurnPlugin and require `is_my_turn=False`.
- Any rejected/invalid/stale/unsupported action dispatches nothing for that iteration.
- `executed_unconfirmed`, `cancelled`, `gate_busy`, or postcondition failure ends the loop immediately.
- No second action may dispatch before the prior action is confirmed.
- Maximum actions and total timeout are enforced across the whole turn.

## Controller Scope Approval

- `src/hslegend/domain/actions.py` may be extended additively for `destination`.
- Planning/use-case/bootstrap application modules, tests/application and docs are approved.
- The existing generic MulliganPlugin/registry hazard recorded in R09 remains deferred; R10 only uses turn-family plugins.

## Dependencies And Impact

- Depends on R09.
- Additive application/domain/bootstrap behavior; legacy adapters remain unchanged.

## Acceptance

- Parser covers all listed instruction/target forms and fails closed on unknown/ambiguous input.
- Slot resolution is deterministic and validates zone/entity ownership.
- PlanBuilder is used for all turn actions.
- End-turn stops the loop without starting a second turn.
- Cancellation before dispatch produces no dispatch; cancellation after confirmation ends typed as cancelled.
- Two identical recommendations cannot cause two dispatches without a confirmed revision.
- Max-action and total-time budgets are enforced.
- Focused tests, default/discover, architecture, scope and compile gates pass.
- No production/legacy adapter or Web/CLI/MCP file changes.

## Risk And Rollback

- Risk: ambiguous slot targeting. Control: exact grammar, deterministic zone_pos resolution and fail-closed rejection.
- Risk: duplicate clicks. Control: PlanExecutor ledger, revision-gated confirmation, no retry after unconfirmed.
- Risk: turn loop runaway. Control: max actions, total deadline, end-turn stop, local-turn check.
- Rollback: revert the R10 commit; R09 and R08 remain usable.

## Git Requirement

```text
feat(R10): add one-turn action loop
```

## Report Paths

- Implementer: `docs/agent/reports/R10-implementer.md`
- Reviewer: `docs/agent/reviews/R10-review.md`

## Allowed Files

- `src/hslegend/domain/actions.py`
- `src/hslegend/application/planning/__init__.py`
- `src/hslegend/application/planning/builder.py`
- `src/hslegend/application/planning/executor.py`
- `src/hslegend/application/use_cases/turn_intent.py`
- `src/hslegend/application/use_cases/turn.py`
- `src/hslegend/application/use_cases/__init__.py`
- `src/hslegend/application/__init__.py`
- `src/hslegend/bootstrap/container.py`
- `src/hslegend/bootstrap/__init__.py`
- `tests/application/**`
- `tests/domain/**`
- `docs/interfaces.md`
- `docs/tasks/R10-*.md`
- `docs/agent/reports/R10-*.md`
- `docs/agent/reviews/R10-*.md`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/TASK_BOARD.json`
- `docs/agent/HANDOFF.md`
- `TOTAL_TASK.md`

## Forbidden Files

- Any legacy/root production module
- Any adapter under `src/hslegend/adapters/**`
- Web, CLI, MCP, OCR, Win32, coordinates, configs and logs
- `.github/**`, `tools/**`, `pyproject.toml`, `uv.lock`
