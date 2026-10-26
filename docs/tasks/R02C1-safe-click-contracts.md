# R02C1 Safe Click Behavior And CLI Contract Tests

## Task ID

R02C1

## Goal

Replace dangerous fail-open click tests with fail-closed behavior and replace private-table drift tests with observable CLI contracts.

## Scope

- Change unverifiable click-target behavior to refuse clicks.
- Add regression tests for unknown window/point.
- Add CLI contract tests for delay keys/effects and screenshot kinds.
- Delete Batch 1 approved candidates only after replacements pass.

## Behavior Change

Intentional safety behavior change: if the target window cannot be verified as Hearthstone/Battle.net, do not click.

## Non-Goals

- No overlay/UI source-string test deletion.
- No Node/native migration.
- No architecture implementation.

## Inputs

- R02A inventory and Batch 1 plan.
- `click.py` and related tests.

## Outputs

- Fail-closed click implementation and tests.
- CLI black-box contract tests.
- Batch 1 deletion ledger update.

## Allowed Files

- `click.py`
- `tests/test_click_safety_checks.py`
- `tests/test_hsctl.py`
- `tests/test_battlenet_play_button.py`
- `docs/testing/**`
- `docs/tasks/R02C*.md`
- `docs/agent/**`
- `TOTAL_TASK.md`

## Forbidden Files

- `web_ui.py`
- `FSM_action.py`
- `manual_controller.py`
- Coordinates, delays, and card rules.

## Acceptance

- Unknown/unverifiable target => zero click calls.
- CLI contract replacements pass.
- Exactly five Batch 1 candidates are removed.
- Headless suite has 0 failures/errors.
- Architecture/scope checks pass.

## Risk And Rollback

- Risk: legitimate fallback clicks may be rejected.
- Rollback: revert R02C1; old tests and behavior return together.

## Git Requirement

```text
fix(R02C1): fail closed on unverifiable click targets
```

## Report Paths

- Implementer: `docs/agent/reports/R02C1-implementer.md`
- Reviewer: `docs/agent/reviews/R02C1-review.md`
