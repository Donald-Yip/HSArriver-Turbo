# R09 Mulligan Vertical Slice

## Task ID

R09

## Goal

Implement one bounded mulligan use case that consumes a normalized HSAng mulligan recommendation, validates it against one current game snapshot, builds a declarative pinned plan, dispatches all replace actions followed by one confirm, and records a typed terminal outcome with evidence.

## Scope

- Parse `替换N号位卡牌` lines and `保留全部卡牌`.
- Support the legacy hand rules: three cards expose three slots; five cards expose four slots.
- Reject stale recommendations, wrong modes, unsupported hands, missing mulligan surfaces, invalid slots, and empty/ambiguous instructions before dispatch.
- Build one `ActionPlan` with non-waiting replace steps and a final confirming step.
- Extend `PlanStep` with `wait_for_change` so intermediate clicks do not wait for a log revision that only changes after confirmation.
- Use the existing `PlanExecutor`, `ActionGate`, `ExecutionLedger`, and journal.
- Return typed statuses for completed, executed-unconfirmed, rejected, failed, cancelled, gate-busy, and recommendation/source failures.
- Publish the new use case through bootstrap without importing legacy modules.

## Non-Goals

- No OCR, screenshot, Win32, HSAng, Power.log, or legacy adapter changes.
- No turn loop, card plugins beyond mulligan, Web/CLI/MCP, or native Hearthstone test.
- No dynamic repeat after an ambiguous confirmation.
- No changes to external contracts, coordinates, delays, or config files.

## Inputs

- Current `GameSnapshot` from `GameStateSourcePort.current()`.
- `RecommendationObservation` with `RecommendationMode.MULLIGAN`.
- Injected dispatch, journal, action gate, stop token, clock, validator, and ledger.
- Existing domain action and condition contracts.

## Outputs

- `src/hslegend/application/use_cases/mulligan.py`
- Updated use-case exports and bootstrap composition.
- Application tests and plan-executor regression tests.
- Updated `docs/interfaces.md`.

## Interface Contract

```text
MulliganCommand(command_id, timeout_seconds)
MulliganResult(
  command_id,
  status,
  reason_code,
  instruction,
  selected_indexes,
  snapshot_id,
  execution_record,
  journal_record_id,
  evidence_ids,
  release_error,
)
```

- `selected_indexes` are zero-based hand indexes.
- The recommendation must belong to the same `snapshot_id` and use mulligan mode.
- The snapshot must expose a choice whose normalized type is `mulligan` or `mulligan_choice`.
- Replace steps carry the exact hand `EntityRef` and use `wait_for_change=False`.
- The final confirm step uses `wait_for_change=True` and requires `snapshot_changed`.
- A plan is journaled exactly once through `PlanExecutor`.
- `executed_unconfirmed` is terminal for this use case; do not replay automatically.
- Cancellation before first dispatch must produce no dispatch.

## Allowed Files

- `src/hslegend/domain/actions.py`
- `src/hslegend/application/planning/executor.py`
- `src/hslegend/application/use_cases/mulligan.py`
- `src/hslegend/application/use_cases/__init__.py`
- `src/hslegend/application/__init__.py`
- `src/hslegend/bootstrap/container.py`
- `src/hslegend/bootstrap/__init__.py`
- `tests/domain/**`
- `tests/application/fakes.py`
- `tests/application/test_planning.py`
- `tests/application/test_mulligan.py`
- `docs/interfaces.md`
- `docs/tasks/R09-*.md`
- `docs/agent/reports/R09-*.md`
- `docs/agent/reviews/R09-*.md`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/TASK_BOARD.json`
- `docs/agent/HANDOFF.md`
- `TOTAL_TASK.md`

## Forbidden Files

- Any legacy/root production module
- OCR, capture, Win32, Power.log, Web, CLI and MCP modules
- `.github/**`, `tools/**`, `pyproject.toml`, `uv.lock`
- Runtime configs, logs, screenshots and coordinates

## Dependencies And Impact

- Depends on R02E and R08.
- Application/domain/bootstrap only.
- Public additive API; no legacy behavior change.

## Acceptance

- Keep-all selects zero cards and dispatches only confirm.
- Valid replace recommendations dispatch replace indexes in sorted order, then confirm.
- Three-hand and five-hand slot bounds match legacy behavior.
- Invalid instruction, stale snapshot, wrong mode, missing mulligan surface, unsupported hand size, and out-of-range slot dispatch nothing and return typed rejection.
- Stop token and gate contention are typed and dispatch nothing.
- Confirmation revision timeout returns `executed_unconfirmed` without retry.
- Journal receives one execution record with recommendation evidence and dispatch evidence.
- Plan executor tests prove no revision wait occurs between non-wait steps.
- Default/discover tests and architecture/scope gates pass.

## Risk And Rollback

- Risk: mis-clicking before mulligan readiness.
- Control: require mulligan choice surface, exact recommendation snapshot, sorted unique slots, and fail-closed validation.
- Risk: waiting for a revision after each slot.
- Control: explicit `wait_for_change=False`; verify no intermediate wait calls.
- Rollback: revert the R09 commit; R08 remains usable.

## Garbage-Code Check

- Reuse domain conditions and PlanExecutor rather than adding card-specific branches to the use case.
- Do not add source-string or mock-only assertions.
- Keep recommendation parsing pure and deterministic.

## Git Requirement

```text
feat(R09): add mulligan vertical slice
```

## Report Paths

- Implementer: `docs/agent/reports/R09-implementer.md`
- Reviewer: `docs/agent/reviews/R09-review.md`
