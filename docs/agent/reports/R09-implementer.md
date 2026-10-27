# R09 Implementer Report

## Status

CHANGES_ADDRESSED / READY_FOR_REVIEW

## Branch And Base

- Branch: `feature/R09-mulligan`
- Base SHA: `14f2e31`
- Head SHA: `14f2e31` (no commit created)
- Commit/push: not performed

## Changed Files

- `docs/interfaces.md`
- `src/hslegend/domain/actions.py`
- `src/hslegend/application/dto.py`
- `src/hslegend/application/planning/executor.py`
- `src/hslegend/application/use_cases/__init__.py`
- `src/hslegend/application/use_cases/mulligan.py`
- `src/hslegend/bootstrap/__init__.py`
- `src/hslegend/bootstrap/container.py`
- `tests/application/test_planning.py`
- `tests/application/test_mulligan.py`
- `docs/agent/reports/R09-implementer.md`

No legacy/root module, adapter, OCR, Web, CLI, MCP, coordinate, runtime-config,
or dependency file was changed.

## TDD Evidence

The new behavior tests were written first and observed failing:

- `PlanStep(..., wait_for_change=False)` initially raised
  `TypeError: unexpected keyword argument 'wait_for_change'`.
- The mulligan test module initially failed with
  `ModuleNotFoundError: No module named 'hslegend.application.use_cases.mulligan'`.

After implementation, the focused mulligan/planning/DTO suite passes 47 tests, including no-intermediate-wait, mandatory-confirmation, empty/ambiguous instruction, and exception-release regressions.

## Implementation

- Added `PlanStep.wait_for_change: bool = True` and type validation.
- Updated `PlanExecutor` to dispatch non-waiting steps sequentially without a
  revision wait or postcondition evaluation. A final non-waiting step fails
  closed, and `confirmation_required` plans require a non-empty confirming
  postcondition.
- Added optional `evidence_ids` ingestion so recommendation evidence reaches the
  journal record. `ExecutionRecord` carries journal ID/durability/duplicate/
  evidence and release metadata, avoiding mutable executor side channels.
  Unexpected executor failures use `PlanExecutorError` with release evidence;
  ledger exceptions retain their type and receive a `release_error` attribute.
- Added exact deterministic parsing for `替换N号位卡牌` and `保留全部卡牌`.
- Added legacy selection bounds: 3-card hands expose slots 1-3; 5-card hands
  expose slots 1-4. Valid selection is sorted, unique, and zero-based.
- Added `MulliganUseCase`, `MulliganCommand`, `MulliganResult`,
  `MulliganStatus`, and `MulliganInstructionError`. Stale recommendations,
  changed snapshots, wrong mode, missing mulligan surface, unsupported hand
  sizes, invalid slots, and malformed instructions are rejected before dispatch.
- Keep-all dispatches only confirm. Replacements carry the exact hand
  `EntityRef`, dispatch in sorted order with `wait_for_change=False`, then send
  one confirm step with `snapshot_changed` and `wait_for_change=True`.
- Added bootstrap helpers/exports without legacy imports. `Container` carries an
  optional mulligan service; `build_mulligan_use_case()` wires ports,
  validator, ledger, executor, action gate, and stop token.

## Interface Decisions

- `parse_mulligan_instruction()` returns sorted unique 1-based slots; the empty
  tuple means keep-all. `validate_mulligan_selection()` maps them to zero-based
  hand indexes.
- Mixed keep-all/replace text, empty text, unknown lines, zero slots, and
  unsupported hand sizes fail closed instead of guessing.
- A recommendation tied to another `snapshot_id` is
  `stale_recommendation`; a changed current snapshot between reads is
  `stale_snapshot`.
- A second explicit command after `executed_unconfirmed` is rejected by the
  ledger as `immediate_replay_blocked`; the use case never retries itself.
- Existing port signatures were not changed.

## Verification

| Command | Exit | Result |
| --- | ---: | --- |
| `uv sync --locked --all-groups` | 0 | Resolved 244 packages; audited 77 packages |
| `uv run python -m unittest tests.application.test_mulligan tests.application.test_planning tests.application.test_dto` | 0 | 47 tests passed; 0 failures/errors |
| `uv run python -m unittest tests.application.test_enter_battle tests.application.test_ports tests.application.test_purity` | 0 | 21 tests passed; 0 failures/errors |
| `uv run python -m unittest discover -s tests -t .` | 0 | 1675 tests passed; 0 failures/errors |
| `uv run python tools/run_headless_tests.py` | 0 | run=1495 passed=1495 failures=0 errors=0 skipped=0 |
| `uv run python -m compileall -q src tests tools` | 0 | No diagnostics |
| `uv run python tools/check_architecture.py` | 0 | `[architecture] PASS: 36 files checked` |
| `uv run python tools/check_architecture.py --self-test` | 0 | `PASS: 16/16 cases` |
| `uv run python tools/check_task_scope.py --task R09` | 0 | `PASS: R09`; allowed files only |
| `uv run python tools/check_task_scope.py --self-test` | 0 | `PASS: 8/8 cases` |

The environment self-check was also attempted. It initially hit a Windows GBK
console encoding error, then reran with `PYTHONIOENCODING=utf-8` and reported two
environment blockers outside this task: process not elevated and the Hearthstone
Logs directory not configured.

## Risks And Rollback

- Risk: clicking before mulligan readiness. Control: exact recommendation
  snapshot, fresh-state comparison, mulligan choice-surface requirement, strict
  instruction parsing, and fail-closed slot validation.
- Risk: waiting for a revision after every replacement. Control: regression test
  proves exactly one wait call for a false/true step plan.
- Risk: ambiguous confirmation. Control: `executed_unconfirmed` is terminal;
  immediate replay is ledger-blocked.
- Residual limitation: this slice is not exercised against a live Hearthstone
  client by design. Full discover reported two pre-existing `ResourceWarning`s
  for `log/calibrate_roi.log`; all tests still passed.
- Controller explicitly approved the `application/dto.py` scope expansion because journal/release metadata belongs on the immutable execution record.
- Deferred hazard: the generic `MulliganPlugin` does not build this batch plan; R14 must reconcile or retire it before registry cutover.
- Rollback: revert the R09 changed files. R08 enter-battle remains independent.
