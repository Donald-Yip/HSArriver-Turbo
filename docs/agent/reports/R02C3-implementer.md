# R02C3 Implementer Report

## Status

APPROVED_FOR_COMMIT (staged implementer files; no commit or push).

## Git

- Branch: `feature/R02C3-non-overlay-tests`
- Base SHA: `55d1be9eea0d5fd7ce0513034499f60a3db86617`
- Head SHA: `55d1be9eea0d5fd7ce0513034499f60a3db86617` (no commit)
- Scope task: R02C3

## Changed Files

Implementer-authored:

- `tests/test_fsm_liveness_stop.py`
- `tests/test_human_like_scope.py`
- `tests/test_start_latency.py`
- `tests/test_web_admin_banner.py`
- `tests/test_web_logs_wiring.py`
- `tests/test_version.py`
- `docs/testing/TEST_INVENTORY.md`
- `docs/agent/reports/R02C3-implementer.md`
- `TOTAL_TASK.md` (reviewer-requested stale phase/branch/next-action correction only)

`TOTAL_TASK.md` was updated only for the reviewer-requested R02C2 -> R02C3
phase/branch/next-action correction. Other pre-existing controller edits to
`docs/agent/HANDOFF.md`, `docs/agent/TASK_BOARD.json`, and
`docs/agent/TASK_BOARD.md` were not authored by this implementer.

## Interfaces

- Consumed: existing `FSM_action` loop exits and recommendation-flow initialization,
  `log_overlay` widget/callback wiring, `web_ui.main`, public `hsctl.py --version`, and
  existing `tests.helpers.fake_tk.overlay_harness`.
- Produced: behavior tests only; no new production interface.
- Production behavior changes: none.
- Public API/config/log changes: none.
- New dependencies: none.

## Fix Round 1

Reviewer-requested corrections:

- `test_start_latency` no longer asserts exact sleep lists. `_FakeClock` advances
  `monotonic()` for every fake `sleep()`; tests assert measured elapsed time and
  per-wait ceilings derived from behavior: each wait is below 2s, `MainMenuAction`
  finishes below 1s, and `ChoosingHeroAction` finishes below 2s while retaining a
  bounded 0.9-1.1s post-click wait.
- `test_human_like_scope` now wraps every global mutated by
  `initialize_recommendation_automation` in patch contexts, including config, both
  captures, parser/readers/validator, and both flow globals, so the original values
  are restored after the test.
- `TOTAL_TASK.md` now points at R02C3, `feature/R02C3-non-overlay-tests`, base
  `55d1be9`, and the correct next action instead of stale R02C2 state.

## Exact Deletion Mapping

Exactly the 11 approved Batch 3 methods were removed. No other test method was deleted.

| # | Removed test | Replacement behavior test |
|---:|---|---|
| 1 | `test_fsm_liveness_stop.LoopWiringTests.test_main_loop_and_battling_call_the_check` | `LoopWiringTests.test_main_loop_and_battling_call_the_check_once_before_stopping` |
| 2 | `test_human_like_scope.MulliganScopeTests.test_fsm_does_not_wire_the_human_like_hook_into_mulligan` | `MulliganScopeTests.test_fsm_wires_the_human_like_hook_only_into_recommendation_flow` |
| 3 | `test_start_latency.NoFixedSleepTests.test_main_menu_action_does_not_wait_three_seconds` | `NoFixedSleepTests.test_main_menu_action_uses_one_frame_clock_without_long_wait` |
| 4 | `test_start_latency.NoFixedSleepTests.test_choosing_hero_action_does_not_wait_two_seconds` | `NoFixedSleepTests.test_choosing_hero_action_uses_one_frame_clock_without_long_wait` |
| 5 | `test_start_latency.NoFixedSleepTests.test_both_entry_states_log_a_milestone` | `NoFixedSleepTests.test_entry_actions_emit_start_milestones` |
| 6 | `test_start_latency.OverlayStartingFeedbackTests.test_overlay_renders_starting_text` | `OverlayStartingFeedbackTests.test_overlay_renders_starting_button_state` |
| 7 | `test_start_latency.OverlayStartingFeedbackTests.test_start_accepts_the_is_starting_callback` | `OverlayStartingFeedbackTests.test_start_installs_the_is_starting_callback` |
| 8 | `test_version.SurfaceTests.test_overlay_draws_version_label` | `SurfaceTests.test_overlay_renders_version_text_next_to_brand` |
| 9 | `test_version.SurfaceTests.test_cli_version_flag_uses_module` | `SurfaceTests.test_cli_version_flag_prints_module_label` |
| 10 | `test_web_admin_banner.AdminWarningBannerTests.test_main_prints_the_banner_only_for_non_admins` | `AdminWarningBannerTests.test_main_outputs_the_banner_only_for_non_admins` |
| 11 | `test_web_logs_wiring.OverlayWiringTests.test_run_uses_the_log_row_and_confirms_before_clearing` | `OverlayWiringTests.test_run_renders_log_row_and_clears_only_after_confirmation` |

## AST Removed/Added Audit

Compared each target file at base `55d1be9` with the working tree using Python AST,
counting only functions whose names start with `test`. Result: `removed=11`, `added=11`.

| File | Tests before -> after | Removed | Added |
|---|---:|---:|---:|
| `tests/test_fsm_liveness_stop.py` | 8 -> 8 | 1 | 1 |
| `tests/test_human_like_scope.py` | 11 -> 11 | 1 | 1 |
| `tests/test_start_latency.py` | 15 -> 15 | 5 | 5 |
| `tests/test_web_admin_banner.py` | 7 -> 7 | 1 | 1 |
| `tests/test_web_logs_wiring.py` | 32 -> 32 | 1 | 1 |
| `tests/test_version.py` | 12 -> 12 | 2 | 2 |

All removed IDs are listed in the exact mapping above. No non-candidate test ID moved.

## Replacement Coverage

- Liveness: stub `check_hearthstone_liveness` flips the quitting flag; both
  `AutoHS_automata` and `Battling` are observed calling it exactly once before exit.
- Human-like scope: `initialize_recommendation_automation` is driven with fake flow
  constructors; `MulliganFlow` receives no `post_action_pause`, while
  `RecommendationFlow` receives `_human_like_post_action_pause`.
- Start latency: fake monotonic sleep advances a clock; `MainMenuAction` stays below
  1s and `ChoosingHeroAction` stays below 2s, every individual wait is below 2s,
  and the 0.9-1.1s post-click interval remains covered without exact call-shape
  assertions; milestone labels are captured.
- Starting feedback: the recording Tk tree renders a disabled `启动中…` button, and
  `start(is_starting=...)` installs the callback used by that widget state.
- Version: the recording overlay renders the version label, and a real public
  `hsctl.py --version` subprocess prints `hsctl <version.version_label()>`.
- Admin banner: `web_ui.main()` is run with a fake server/threads and captured stdout;
  the banner appears for non-admin and not for admin.
- Logs wiring: the recording overlay renders the log row and both buttons; opening
  logs invokes its callback, clearing is blocked on confirmation cancellation, then
  invokes the clear callback after confirmation.

## Original Selfcheck Note

`uv run python selfcheck.py --json` first exited 1 with a GBK
`UnicodeEncodeError`. The single retry with `PYTHONIOENCODING=utf-8` completed but
reported environment blockers only: process is not elevated, and the Hearthstone
Logs directory is not configured. These are outside R02C3 and do not affect the
headless test gates.

## Fresh Verification

```powershell
uv sync --locked --all-groups
# exit 0; Resolved 244 packages; Audited 77 packages

uv run python -m compileall -q src tests tools
# exit 0; no output

uv run python tools/check_architecture.py
# exit 0; [architecture] PASS: src/hslegend is not present yet

uv run python tools/check_architecture.py --self-test
# exit 0; [architecture:self-test] PASS: 16/16 cases
```

```powershell
uv run python -m unittest tests.test_fsm_liveness_stop tests.test_human_like_scope tests.test_start_latency tests.test_web_admin_banner tests.test_web_logs_wiring tests.test_version -v
# exit 0; Ran 85 tests; OK
```

```text
uv run python tools/run_headless_tests.py
exit 0; [headless-tests] summary: run=1523 passed=1522 failures=0 errors=0 skipped=1
```

```powershell
uv run python tools/check_task_scope.py --task R02C3 --json
# exit 0; ok=true; diff_source="git diff --cached"; violations=[]

uv run python tools/check_task_scope.py --self-test
# exit 0; [scope:self-test] PASS: 8/8 cases
```

One repeated headless run hit the pre-existing flaky `test_window_tracker.TrackerTests.test_event_updates_timestamp_and_count` (`hook 还没挂上`) and exited 1; the single immediate retry passed with `run=1523 passed=1522 failures=0 errors=0 skipped=1`. No R02C3 test failed.

## Scope Check

The final scope gate includes the staged implementer files, the reviewer-requested
`TOTAL_TASK.md` update, and pre-existing staged/unstaged controller edits. It reports
`ok=true` and no violations. No production file changed. No forbidden file changed.
No commit or push was made.

## Risks And Review Focus

- The Tk tests use the existing recording widget harness, so they verify rendered
  text/state and callbacks rather than pixels.
- `ChoosingHeroAction` still intentionally waits 1s after dispatching match; the test
  protects removal of the old 2s pre-read wait without asserting a zero-delay change.
- `web_ui.main()` is exercised with its blocking loop terminated by `KeyboardInterrupt`;
  the test isolates server/thread side effects and only asserts console-banner behavior.
- Rollback: restore the six test files to base `55d1be9`; no production rollback is
  required.
