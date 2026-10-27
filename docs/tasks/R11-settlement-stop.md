# R11 Settlement And One-Game Stop

## Task ID

R11

## Goal

Add a bounded settlement use case that waits for the authoritative `RESULT` lobby phase, records evidence, and terminates the one-game flow without dispatching any action that could start another match.

## Scope

- Add `FinishBattleCommand`, typed `FinishBattleStatus/Result`, and `FinishBattleUseCase`.
- Poll `LobbyStatePort.get_state(timeout_seconds=remaining)` at a bounded interval.
- Complete only when phase is `RESULT`.
- Return typed `cancelled`, `timed_out`, or `failed` for stop/error paths.
- Preserve observed evidence IDs and elapsed time.
- Explicitly perform no input dispatch; no start, continue, rematch, or deck-selection action is allowed in this slice.
- Wire bootstrap/export without legacy imports.

## Non-Goals

- No result text/winner extraction.
- No Web/CLI/MCP changes, next-game orchestration, surrender, rewards, or native interaction.
- No click actions and no action gate/ledger entry because this slice has no dispatch.
- No coordinates, delays, configs, or legacy root imports.

## Inputs

- `LobbyStatePort`.
- `StopToken`.
- Injected monotonic clock and sleeper.
- `FinishBattleCommand(command_id, timeout_seconds, poll_interval_seconds)`.

## Outputs

- `src/hslegend/application/use_cases/finish_battle.py`
- application/bootstrap exports
- focused application tests
- updated `docs/interfaces.md`

## Interface Contract

```text
FinishBattleCommand(command_id, timeout_seconds, poll_interval_seconds)
FinishBattleStatus = completed | cancelled | timed_out | failed
FinishBattleResult(
  command_id,
  status,
  phase,
  reason_code,
  evidence_ids,
  elapsed_seconds,
)
```

- `completed` requires an observed `LobbyPhase.RESULT`.
- `cancelled` is terminal before or between polls.
- `timed_out` means no RESULT before the command deadline.
- Adapter and clock failures are typed without leaking exceptions.
- The use case must never call `ActionDispatchPort` and must never auto-start another game.
- Evidence IDs are unique and include the result-state evidence when present.

## Dependencies And Impact

- Depends on R10.
- Additive application/bootstrap behavior; no external contract change.

## Acceptance

- Immediate RESULT completes.
- Polling reaches RESULT and preserves its evidence ID.
- Timeout and cancellation are typed and bounded.
- Adapter/clock failures are typed.
- The use case exposes no dispatch dependency.
- Focused/default/discover/architecture/scope/compile gates pass.
- No action dispatch or next-game start is possible from this module.

## Risk And Rollback

- Risk: accidentally clicking result screen and starting a second game.
- Control: no dispatch dependency; result observation only.
- Risk: unbounded polling.
- Control: total deadline, bounded interval validation, injected clock/sleeper.
- Rollback: revert the R11 commit; R10 remains usable.

## Git Requirement

```text
feat(R11): finish a single game and stop
```

## Report Paths

- Implementer: `docs/agent/reports/R11-implementer.md`
- Reviewer: `docs/agent/reviews/R11-review.md`

## Allowed Files

- `src/hslegend/application/use_cases/finish_battle.py`
- `src/hslegend/application/use_cases/__init__.py`
- `src/hslegend/application/__init__.py`
- `src/hslegend/bootstrap/container.py`
- `src/hslegend/bootstrap/__init__.py`
- `tests/application/**`
- `docs/interfaces.md`
- `docs/tasks/R11-*.md`
- `docs/agent/reports/R11-*.md`
- `docs/agent/reviews/R11-*.md`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/TASK_BOARD.json`
- `docs/agent/HANDOFF.md`
- `TOTAL_TASK.md`

## Forbidden Files

- Any legacy/root production module
- Any adapter under `src/hslegend/adapters/**`
- Web, CLI, MCP, OCR, Win32, coordinates, configs and logs
- `.github/**`, `tools/**`, `pyproject.toml`, `uv.lock`
