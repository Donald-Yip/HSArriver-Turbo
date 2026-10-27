# R05 Plan Builder, Validator, Executor And Ledger

## Task ID

R05

## Goal

Implement the application-level declarative planning and one-action execution pipeline with idempotency and evidence records.

## Scope

- Add `PlanPlugin`/`PlanContext` contract and static `PluginRegistry`.
- Add default plugins for mulligan, play card, targeted card, attack, hero power, location, and end turn.
- Add `PlanBuilder`, `PlanValidator`, `ExecutionLedger`, and `PlanExecutor`.
- Integrate `ActionGate`, `ActionDispatchPort`, `GameStateSourcePort`, `RunJournalPort`, StopToken, and domain postconditions.
- Add deterministic tests/fakes for plan selection, conflicts, validation, execution, ambiguous result, and no duplicate replay.
- Stabilize `WindowMoveTracker.stop()` shutdown cleanup so the window tracker regression is deterministic.

## Non-Goals

- No concrete legacy adapters.
- No OCR/capture/Power.log/click implementation.
- No one-game use-case state machine.
- No runtime hot loading or dynamic plugin discovery.

## Allowed Files

- `window_tracker.py` (minimal deterministic shutdown fix only)
- `src/hslegend/application/planning/**`
- `src/hslegend/plugins/**`
- `src/hslegend/application/runtime.py` only if minimal integration fix
- `tests/application/**`
- `tests/plugins/**`
- `tests/test_window_tracker.py`
- `docs/interfaces.md`
- `docs/architecture/**`
- `docs/tasks/R05-*.md`
- `docs/agent/**`
- `TOTAL_TASK.md`

## Forbidden Files

- Legacy root modules not listed under Allowed Files.
- Adapters, bootstrap, entrypoints.
- Domain files except reviewer-approved bug fix.
- Coordinates, delays, card data.

## Acceptance

- Plugin resolution is explicit and deterministic: exact card > capability/family > default.
- Ambiguous plugin matches fail closed.
- Plugins return declarative ActionPlan only and cannot perform I/O.
- PlanValidator rejects stale/invalid plans using domain rules and supports
  current-snapshot validation immediately before each step.
- PlanExecutor dispatches each approved step through ActionDispatchPort.
- PlanExecutor enforces the plan-level total timeout and each per-step timeout
  with an injected monotonic clock.
- ExecutionLedger marks executed_unconfirmed and prevents immediate replay; an
  in-flight NOT_EXECUTED attempt rejects a second begin.
- ActionGate ownership spans ledger completion, record construction, and journal
  append, so a concurrent execution cannot dispatch while finalization is active.
- StopToken and remaining plan budget are rechecked after step validation and
  immediately before dispatch.
- Any exception, timeout, postcondition failure, or cancellation after dispatch
  remains executed_unconfirmed and preserves the original reason.
- Journal receives one ExecutionRecord per attempt; journal failure cannot
  double-complete the ledger or mask its own error.
- Archived/confirmed/failed/cancelled statuses are deterministic.
- Standalone application and plugin test packages configure the `src` import
  path without external `PYTHONPATH`.
- `WindowMoveTracker.stop()` synchronously unhooks and clears the current hook;
  the hook thread never double-unhooks a hook already cleared by stop.
- Headless, architecture, scope, domain/application gates remain green.

## Risk And Rollback

- Risk: over-general plugin framework before real card cases.
- Mitigation: small static registry and only MVP action families.
- Rollback: delete planning/plugins/tests; restore `window_tracker.py` and `tests/test_window_tracker.py`; R03/R04 remain intact.

## Git Requirement

```text
feat(R05): add validated plan execution pipeline
```

## Report Paths

- Implementer: `docs/agent/reports/R05-implementer.md`
- Reviewer: `docs/agent/reviews/R05-review.md`
