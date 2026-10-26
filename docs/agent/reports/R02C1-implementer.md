# R02C1 Implementer Report

## Status

APPROVED_FOR_COMMIT (uncommitted staged changes; no commit or push).

## Git

- Branch: `feature/R02C1-safe-click-contracts`
- Base SHA: `24a5c91f54b4267336c48b5ddac88611d2aae423`
- Head SHA: `24a5c91f54b4267336c48b5ddac88611d2aae423` (no commit)
- Staged commit candidate: 17 files total.
- Implementer-authored files in the staged commit candidate: 7.
- Controller-owned artifacts staged in the commit candidate: 10.

## Review Findings Addressed

- Unified R02C1 as `ready_for_review` across `TOTAL_TASK.md`, `TASK_BOARD.json`, `TASK_BOARD.md`, and `HANDOFF.md`; aligned the current phase, current branch, next action, gate task ID, review round, and last verified command.
- Added the positive verified-background regression. With another foreground window, both the target and activation points verified, the recorded events are right-click `(1800, 500)`, then left-click `(960, 650)`, and `click_button` returns `True`.
- Corrected this report's staging statement: controller-owned artifacts are present in the staged commit candidate and are distinguished from implementer-authored changes below.

## Behavior Change

- `click_button` fails closed before constructing the mouse controller when the Hearthstone window is unknown, coordinates are invalid, or `point_targets_hearthstone` cannot verify the target point.
- Background-window activation verifies the auxiliary activation point immediately before the physical right click; an unverified activation point aborts with zero mouse events.
- `_point_targets_window` returns `False` when Win32 hit testing returns no window or raises.
- `enter_HS` warns and returns `False` when the Battle.net play point is covered or unverifiable instead of clicking through it.
- Verified foreground Hearthstone clicks, verified background activation-then-target clicks, and verified Battle.net play-button clicks remain supported.

## Original Evidence

- Before this change, `click_button` called `Controller()` and clicked even when `get_HS_hwnd()` returned `0`.
- Before this change, the background path called `point_targets_hearthstone(...) == False` but still clicked the fallback activation point and then the requested point.
- Before this change, `_point_targets_window` returned `True` for both an empty `WindowFromPoint` result and exceptions.
- Before this change, `enter_HS` warned that a covered Battle.net point "可能点空" but still called `click_button`.
- The deleted tests locked those unsafe outcomes as correct behavior: `test_clicks_when_hearthstone_window_is_not_found` and `test_background_window_activates_and_clicks_without_hit_testing`.

## Staged File Ownership

Implementer-authored files (7):

- `click.py`
- `tests/test_click_safety_checks.py`
- `tests/test_battlenet_play_button.py`
- `tests/test_hsctl.py`
- `docs/testing/TEST_RATIONALIZATION_PLAN.md`
- `docs/testing/TEST_INVENTORY.md`
- `docs/agent/reports/R02C1-implementer.md`

Controller-owned artifacts staged in the commit candidate (10):

- `TOTAL_TASK.md`
- `docs/agent/DECISIONS.md`
- `docs/agent/HANDOFF.md`
- `docs/agent/TASK_BOARD.json`
- `docs/agent/TASK_BOARD.md`
- `docs/tasks/R02C1-safe-click-contracts.md`
- `docs/tasks/R02C2-overlay-tests.md`
- `docs/tasks/R02C3-non-overlay-tests.md`
- `docs/tasks/R02C4-native-node.md`
- `docs/tasks/R02C5-headless-final.md`

The status-only edits to controller-owned files in this fix round were made under the reviewer's explicit instruction. No forbidden web/FSM/manual-controller file, coordinate, delay, or card rule was changed.

## Batch 1 Deletions

Removed exactly the five approved candidates:

1. `test_click_safety_checks.ClickSafetyCheckRemovalTests.test_clicks_when_hearthstone_window_is_not_found`
2. `test_click_safety_checks.ClickSafetyCheckRemovalTests.test_background_window_activates_and_clicks_without_hit_testing`
3. `test_hsctl.TableDriftTests.test_delay_keys_match_config_whitelist`
4. `test_hsctl.TableDriftTests.test_delay_effect_table_matches_web_ui`
5. `test_hsctl.TableDriftTests.test_screenshot_kinds_match_web_ui`

Replacements:

- `ClickSafetyContractTests` (6 tests): unknown window, unverified point, unknown coordinates, unverified background activation point, verified background activation-then-target, and verified foreground click.
- `CliDelayAndScreenshotContractTests` (5 tests): all delay keys/effect labels through `delays show`; all accepted delay keys and unknown-key rejection through `delays set`; all public screenshot kinds and unknown-kind rejection through `screenshot`.

## Interfaces

- Consumed: existing `get_HS_hwnd`, `point_targets_hearthstone`, `get_battlenet_hwnd`, `_point_targets_window`, `click_button`, `Ctx.post`, `/api/status`, `/api/delays`, `/api/screenshot`.
- Produced: `click_button` returns `bool` so callers can observe accepted/refused physical dispatch. Existing callers ignore the return value.
- Public HTTP/CLI/config/log contracts unchanged. No new runtime dependency.

## Verification

```powershell
uv run python -m unittest tests.test_click_safety_checks tests.test_battlenet_play_button tests.test_hsctl.CliDelayAndScreenshotContractTests -v
# exit 0; Ran 25 tests; OK

uv run python tools/run_headless_tests.py
# attempt 1 exit 1; ran 1523; failures=1; error was unrelated
# test_window_tracker.TrackerTests.test_ensure_started_is_idempotent
# AssertionError: False is not true: 钩子就绪是异步的，应该只有一个句柄

uv run python tools/run_headless_tests.py
# retry exit 0; summary: run=1523 passed=1522 failures=0 errors=0 skipped=1; OK (skipped=1)

uv run python -m compileall -q src tests tools
# exit 0; no output

uv run python tools/check_architecture.py
# exit 0; [architecture] PASS: src/hslegend is not present yet

uv run python tools/check_architecture.py --self-test
# exit 0; [architecture:self-test] PASS: 16/16 cases

uv run python tools/check_task_scope.py --task R02C1 --json
# exit 0; ok=true, task=R02C1, diff_source=git diff --cached, violations=[]

uv run python tools/check_task_scope.py --self-test
# exit 0; [scope:self-test] PASS: 8/8 cases
```

## Scope Check

The scope gate evaluates all 17 staged files in the commit candidate, including controller-owned artifacts, and reports `ok=true` with `violations=[]`. The first headless attempt exposed an unrelated known flaky `test_window_tracker` timing test; the required single retry passed the complete suite without changing that out-of-scope test.

## Risks And Review Focus

- Safety tradeoff: transient Win32 hit-test failures or any overlay covering the target point now reject the click. Confirm this fail-closed behavior is intentional and that verified foreground, background activation, and Battle.net paths still pass.
- Review `click_button` ordering: no `Controller()` or mouse event may occur before window/coordinate/point validation.
- Review the positive background assertion: activation must be a right click at `(1800, 500)` before the target left click, with no extra mouse events.
- Review that CLI contract tests assert observable stdout/request bodies and do not reintroduce `config._USER_DELAY_KEYS`, `web_ui._delay_effect_scope`, or `web_ui.SCREENSHOT_KINDS` private-table comparisons.
- Rollback remains atomic: revert R02C1 to restore the prior fail-open behavior and deleted acceptance tests together.
