# R02F Test Cluster Reduction

## Task ID

R02F

## Goal

Reduce duplicated, implementation-coupled, and low-signal tests in four
behavior clusters while keeping every safety contract that guards real
mouse/keyboard input.

The user-facing trigger: the default headless gate had grown from the R02E
target of 1,495 to 1,826-1,851 tests because each bug fix added a new
regression file. The reduction is quality-first: no fixed numeric target.

## Scope

Four independently revertible batches:

- B1 surrender cluster: `tests/test_surrender_rules.py`,
  `tests/test_surrender_integration.py`, `tests/test_hsctl_surrender.py`,
  `tests/test_web_ui_surrender.py`.
- B2 display/DPI cluster.
- B3 target-choice cluster.
- B4 human-like timing cluster.
- Low-risk leftovers: no-assert/constant assertions, merging the small
  local-strategy regression files, removing empty `tests/native` and
  `tests/tooling` directories, and refreshing stale test-count docs.

Deletion criteria (user-approved):

1. Duplicate coverage: the same behavior is already gatekept at a higher
   level; keep the higher-level test and merge the low-level copies.
2. Private-attribute-only assertions that do not protect user-visible
   behavior.
3. No assertions, or assertions that only re-state constants/HTML strings.
4. Prefer merging sibling tests into one subTest matrix over deleting them.

Red lines (user-approved): click safety, `battle_entry`, `hs_restart`, and
surrender rule normalization keep at least one test each. Rules-level
surrender coverage is not to be dismantled.

## Current Increment

Both waves are executed. Wave 1: surrender 109 -> 85, display/DPI
226 -> 166, target-choice 183 -> 129, human-like 96 -> 31. Wave 2:
OCR 78 -> 53, web 157 -> 108, hsctl 93 -> 62, battle/rank/restart
111 -> 75, overlay 121 -> 85, infra 345 -> 234, game/log 146 -> 82.

The default headless gate went from 1,850 to 1,451 runnable tests
(555 removed; the resident R18J work added ~156 new tests in parallel),
with 0 failures / 0 errors.

## Non-Goals

- No production behavior changes.
- No change to `tools/run_headless_tests.py`, CI workflows, or gate policy.
- No deletion of fail-closed, ambiguity, cancellation, deadline, safe-click,
  or real user/API contract coverage.
- No test moved to a new framework or opt-in gate.

## Verification

Each batch must be verified with the default headless runner and must not
add a single failure relative to the recorded pre-batch baseline:

```powershell
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py
```

B1 pre-batch baseline: 1,850 run / 3 known screen-size dependent failures
(local 1920x1080 DPI assertions, unrelated to the surrender cluster).
Batch results after B1: 1,826 run / 0 failures / 0 errors.
Final gate after both waves: 1,451 run / 0 failures / 0 errors (86s).

## Allowed Files

- `tests/test_surrender_*.py`
- `tests/test_hsctl_surrender.py`
- `tests/test_web_ui_surrender.py`
- `tests/test_display_mapping.py`
- `tests/test_calibrate_tool.py`
- `tests/test_screen_regions.py`
- `tests/test_overlay_dpi_scaling.py`
- `tests/test_dpi_is_not_a_gate.py`
- `tests/test_desktop_capture_scaled_roi.py`
- `tests/test_validator_client_size.py`
- `tests/test_choose_one.py`
- `tests/test_choose_skill.py`
- `tests/test_assemble_choice.py`
- `tests/test_four_option_choices.py`
- `tests/test_timeline_choice.py`
- `tests/test_discover_with_target.py`
- `tests/test_select_target_after_choice.py`
- `tests/test_hand_or_board_targets.py`
- `tests/test_targeted_battlecries.py`
- `tests/test_friendly_hand_target_battlecries.py`
- `tests/test_starship_launch.py`
- `tests/test_targeted_locations.py`
- `tests/test_human_like.py`
- `tests/test_human_like_*.py`
- `tests/test_hsctl.py`
- `tests/test_rank_stop.py`
- `tests/test_battle_*.py`
- `tests/test_hs_restart.py`
- `tests/test_web_*.py`
- `tests/test_overlay_*.py`
- `tests/test_region_overlay_button.py`
- `tests/test_ocr_*.py`
- `tests/test_mulligan_*.py`
- `tests/test_hand_*.py`
- `tests/test_hearthstone_liveness.py`
- `tests/test_board_rewind_positions.py`
- `tests/test_attack_target_name_fallback.py`
- `tests/test_secondary_hero_power.py`
- `tests/test_mode_click.py`
- `tests/test_player_name_match.py`
- `tests/test_power_log_hand_entries.py`
- `tests/test_log_state_entity_names.py`
- `tests/test_turn_scan_heartbeat.py`
- `tests/test_recommendation_*.py`
- `tests/test_start_latency.py`
- `tests/test_strategy_*.py`
- `tests/test_env_provision.py`
- `tests/test_selfcheck*.py`
- `tests/test_feedback.py`
- `tests/test_action_feed.py`
- `tests/test_joint_helpers.py`
- `tests/test_adapted_description.py`
- `tests/test_turn_start_readiness.py`
- `tests/test_draw_extra_delay*.py`
- `tests/test_cancel_manual_flow.py`
- `tests/test_memory_watch.py`
- `tests/test_script_logs.py`
- `tests/test_window_tracker.py`
- `tests/test_update_check.py`
- `tests/test_release_tool.py`
- `tests/test_ci_gates.py`
- `tests/test_uv_environment.py`
- `tests/test_version.py`
- `tests/test_card_catalog.py`
- `tests/test_config_strategy.py`
- `tests/test_overlay_human_like_status.py`
- `tests/test_strategy_*.py`
- `tests/test_*strategy*.py`
- `docs/tasks/R02F-*.md`
- `docs/testing/*.md`
- `docs/FEATURES.md`

## Forbidden Files

- Any production module under `src/` or any legacy root module
- Any test file not listed above
- `pyproject.toml`, `uv.lock`
- Runtime configs, logs, screenshots, and Hearthstone interactions
