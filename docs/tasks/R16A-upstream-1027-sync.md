# R16A Original Repository 2026.10.27 Sync

## Task ID

R16A

## Goal

Import the original repository's pending `2026.10.27` update into the refactored
workspace without reverting the domain/application/adapters layering, UI parity,
tests, CI gates, or other completed refactor work.

## Inputs

- Refactor branch `feature/R15-ui-parity` at `06a85ce`.
- Original desktop clone at commit `f35c5f3` with pending `2026.10.27` changes.
- Original patch: 18 tracked files plus three new files.

## Scope

- Sync version to `2026.10.27`.
- Add the original 10.27 ancient-god/ability feature:
  - parser support for `USE_ABILITY`;
  - `choose_skill.py` recorded panel ordering and fail-closed lookup;
  - recommendation adapter and click gesture support;
  - recommendation circuit breaker.
- Sync updated `cards.json`, README, CHANGELOG, feature docs and legacy tests.
- Preserve current refactor and UI changes.
- Correct stale hand-target test inventory for the newly added `BE_566` card.
- Verify the merged tree with the original 10.27 tests and the full existing gate.

## Non-Goals

- No architectural rewrite of the original 10.27 changes.
- No modification to `src/hslegend`, domain/application port boundaries, CI rules,
  coordinates, delays, or card logic beyond the original patch.
- No direct push.

## Outputs

- Rebased/synced 10.27 source and tests.
- Version and changelog at 10.27.
- Passing original and refactor tests.
- Sync report and comparison summary.

## Acceptance

```powershell
uv run python -m unittest tests.test_choose_skill tests.test_recommendation_circuit_breaker tests.test_feedback tests.test_human_like tests.test_mulligan_timing tests.test_ocr_target_line_variants tests.test_web_logs_wiring
uv run python tools/run_headless_tests.py
uv run python -m unittest discover -s tests -t .
uv run python tools/run_headless_tests.py --opt-in
uv run python tools/check_architecture.py
uv run python tools/check_architecture.py --self-test
uv run python tools/check_task_scope.py --task R16A --json
git diff --cached --check
```

## Risk And Rollback

- Risk: imported legacy changes conflict with refactor boundaries.
- Control: original patch applied with three-way check; targeted and full suites run.
- Rollback: revert the R16A commit; R15 UI/refactor remains unchanged.

## Allowed Files

- `CHANGELOG.md`
- `README.md`
- `FSM_action.py`
- `cards.json`
- `click.py`
- `docs/FEATURES.md`
- `manual_controller.py`
- `src/flow/recommendation_flow.py`
- `src/game_state/recommendation_adapter.py`
- `src/game_state/choose_skill.py`
- `src/parser/recommendation_parser.py`
- `src/recommendation_models.py`
- `tests/test_choose_skill.py`
- `tests/test_recommendation_circuit_breaker.py`
- `tests/test_feedback.py`
- `tests/test_friendly_hand_target_battlecries.py`
- `tests/test_human_like.py`
- `tests/test_mulligan_timing.py`
- `tests/test_ocr_target_line_variants.py`
- `tests/test_web_logs_wiring.py`
- `version.py`
- `web_ui.py`
- `docs/tasks/R16A-upstream-1027-sync.md`
- `docs/agent/reports/R16A-*.md`
- `docs/agent/reviews/R16A-*.md`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/TASK_BOARD.json`
- `docs/agent/HANDOFF.md`
- `TOTAL_TASK.md`

## Forbidden Files

- `src/hslegend/**`
- `pyproject.toml`
- `uv.lock`
- `.github/**`
- coordinates, timing defaults, and unrelated legacy modules

## Git Requirement

```text
fix(R16A): sync original repository 2026.10.27 update
```

## Report Paths

- Implementer: `docs/agent/reports/R16A-implementer.md`
- Reviewer: `docs/agent/reviews/R16A-review.md`