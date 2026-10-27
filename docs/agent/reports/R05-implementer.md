# R05 Implementer Report

## Status

FIX_ROUND_2_COMPLETE - ready for re-review. No commit or push performed.

## Base / Head / Branch

- Base SHA: `637639e255b784c83dddaa970a72946de56ceed0`
- HEAD SHA: `637639e255b784c83dddaa970a72946de56ceed0` (implementation is uncommitted)
- Branch: `feature/R05-plan-execution`

## Review Findings Addressed

1. The `ActionGate` lease now spans `ExecutionLedger.begin()`, execution,
   ledger completion, record construction, and journal append; release occurs
   only in the outer `finally`. Concurrent execution cannot dispatch while the
   first attempt is finalizing.
2. `ExecutionLedger.begin()` rejects a second begin while the latest attempt is
   still `NOT_EXECUTED` with `InFlightAttemptError`. Deterministic concurrent
   tests block ledger completion and journal append in turn and confirm the
   second execute cannot dispatch.
3. StopToken and remaining plan budget are rechecked immediately after step
   validation and again immediately before dispatch. Tests cancel the token
   during validation and expire the total budget during validation.
4. `WindowMoveTracker.stop()` now synchronously claims, clears, and unhooks the
   current hook. The hook thread claims before unhooking, so stop and the thread
   cannot double-unhook the same hook. The regression was run 30 times without a
   failure.
5. The R05 task book allows `window_tracker.py` and
   `tests/test_window_tracker.py`; controller state is current for fix round 2.

## Changed Files

- `TOTAL_TASK.md`
- `docs/agent/HANDOFF.md`
- `docs/agent/TASK_BOARD.json`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/reports/R05-implementer.md`
- `docs/interfaces.md`
- `docs/tasks/R05-plan-execution.md`
- `src/hslegend/application/planning/__init__.py`
- `src/hslegend/application/planning/builder.py`
- `src/hslegend/application/planning/contracts.py`
- `src/hslegend/application/planning/executor.py`
- `src/hslegend/application/planning/ledger.py`
- `src/hslegend/application/planning/registry.py`
- `src/hslegend/application/planning/validator.py`
- `src/hslegend/plugins/__init__.py`
- `src/hslegend/plugins/defaults.py`
- `src/hslegend/plugins/registry.py`
- `tests/application/test_planning.py`
- `tests/plugins/__init__.py`
- `tests/plugins/test_default_plugins.py`
- `tests/plugins/test_registry.py`
- `tests/test_window_tracker.py`
- `window_tracker.py`

## Interfaces Produced

- `PlanContext` and runtime-checkable `PlanPlugin`.
- Static `PluginRegistry` with exact card > capability/family > default
  resolution and fail-closed ambiguity.
- Default plugins for mulligan, plain play, targeted play, attack, hero power,
  location, and end turn.
- `PlanBuilder` using `CardCatalogPort`.
- `PlanValidator.validate()`, `validate_plan()`, and `validate_step()` backed by
  the domain `PlanRuleValidator`.
- `ExecutionLedger` with immutable attempts, in-flight attempt rejection,
  attempt limits, archived action blocking, confirmed-action blocking, and
  evidence-gated retry for `executed_unconfirmed`.
- `PlanExecutor` integrating `ActionGate`, `ActionDispatchPort`,
  `GameStateSourcePort`, `PostconditionEvaluator`, `RunJournalPort`, and
  `StopToken`, with gate ownership across finalization, total/per-step timeout
  enforcement, and validation-to-dispatch race guards.

## Interfaces Consumed

- Domain: `ActionIntent`, `ActionPlan`, `PlanStep`, `Condition`,
  `CardDefinition`, `CardCapabilitySet`, `GameSnapshot`, `PlanRuleValidator`,
  `PostconditionEvaluator`.
- Application ports: `CardCatalogPort`, `ActionDispatchPort`,
  `GameStateSourcePort`, `RunJournalPort`.
- Application runtime/DTOs: `ActionGate`, `ActionLeaseHeld`,
  `OperationCancelled`, `StopToken`, `DispatchStatus`, `ExecutionRecord`,
  `ExecutionStatus`.

## Verification

| Command | Exit | Raw result |
|---|---:|---|
| `uv sync --locked --all-groups` | 0 | `Resolved 244 packages in 3ms`; `Audited 77 packages in 4ms` |
| `uv run python -m compileall -q src tests tools window_tracker.py` | 0 | No output |
| `uv run python tools/check_architecture.py` | 0 | `[architecture] PASS: 22 files checked` |
| `uv run python tools/check_architecture.py --self-test` | 0 | `[architecture:self-test] PASS: 16/16 cases` |
| `uv run python -m unittest discover -s tests/domain -t . -q` | 0 | `Ran 38 tests`; `OK` |
| `uv run python -m unittest discover -s tests/application -t . -q` | 0 | `Ran 44 tests`; `OK` |
| `uv run python -m unittest discover -s tests/plugins -t . -q` | 0 | `Ran 9 tests`; `OK` |
| `uv run python -m unittest tests.test_window_tracker -q` x30 | 0 | `17 tests` per run; `window-tracker-iterations=30 failures=0` |
| `uv run python tools/run_headless_tests.py` | 0 | `run=1525 passed=1524 failures=0 errors=0 skipped=1` |
| `uv run python tools/check_task_scope.py --self-test` | 0 | `[scope:self-test] PASS: 8/8 cases` |
| `uv run python tools/check_task_scope.py --task R05` | 0 | [scope] PASS: R05 (23 changed files, source: git diff --cached) |

Acceptance-specific coverage:

- Plugin priority, conflict, default, and missing-card cases remain covered.
- Stale plans are rejected before dispatch.
- Successful plans dispatch once, wait for a later revision, evaluate
  postconditions, and journal one confirmed record.
- Dispatch exceptions and post-dispatch failures remain
  `executed_unconfirmed` with the original reason.
- Journal failure propagates after exactly one ledger completion.
- StopToken and plan-budget changes during step validation stop execution before
  dispatch; changes after dispatch remain `executed_unconfirmed`.
- A blocked ledger completion leaves the attempt `NOT_EXECUTED`; a second begin
  is rejected and a concurrent execute cannot dispatch.
- A blocked journal append keeps the gate held; a concurrent execute cannot
  dispatch.
- Confirmed actions never replay; unconfirmed replay requires explicit
  authorization; attempt limits and archived actions remain deterministic.
- `WindowMoveTracker.stop()` unhooks synchronously exactly once, including after
  the hook thread reaches its finally block.

## Scope Check

Implementation and staged controller state are limited to R05 allowed paths:
planning, plugins, application/plugin tests, the reviewer-approved
`window_tracker.py` cleanup, `tests/test_window_tracker.py`, interfaces, the R05
task book, agent/controller state, and `TOTAL_TASK.md`. No domain, adapter,
bootstrap, entrypoint, legacy-root, coordinate, delay, or card-data file was
changed beyond the approved window tracker shutdown cleanup.

## New Dependencies / Public API

- New dependencies: none.
- Public API additions are confined to `hslegend.application.planning`,
  `hslegend.plugins`, and the minimal `WindowMoveTracker.stop()` cleanup.
- Existing HTTP/CLI/MCP/config/log behavior is unchanged.

## Known Risks / Review Focus

- The gate is intentionally held through journaling, increasing serialization
  across finalization.
- A concurrent execute while the gate is held raises `ActionLeaseHeld` before a
  new ledger attempt is created.
- Review focus: gate/finalization ordering, in-flight ledger rejection,
  validation-to-dispatch race closure, and window hook ownership cleanup.
