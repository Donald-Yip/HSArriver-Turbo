# R02C2 Implementer Report

## Status

APPROVED_FOR_COMMIT (staged implementer files; no commit or push).

## Git

- Branch: `feature/R02C2-overlay-tests`
- Base SHA: `15ab7f8d99b84537275669d8a92676cb5bfb1512`
- Head SHA: `15ab7f8d99b84537275669d8a92676cb5bfb1512` (no commit)
- Scope task: R02C2

## Changed Files

Implementer-authored:

- `tests/helpers/fake_tk.py`
- `tests/helpers/__init__.py`
- `tests/test_overlay_dpi_scaling.py`
- `tests/test_overlay_eye_and_colors.py`
- `tests/test_overlay_human_like_status.py`
- `tests/test_overlay_self_check_hint.py`
- `tests/test_overlay_tk_lifecycle.py`
- `tests/test_overlay_window_slider.py`
- `tests/test_region_overlay_button.py`
- `docs/testing/TEST_INVENTORY.md`
- `docs/testing/TEST_RATIONALIZATION_PLAN.md`
- `docs/agent/reports/R02C2-implementer.md`
- `TOTAL_TASK.md`

Controller-owned status files already modified in the working tree before this task were not edited by this implementer.

## Interfaces

- Consumed: existing `log_overlay._run`, `start`, `_run_overlay_thread`, widget callbacks, `BTN_LAYOUT`, `BTN_SPAN`, `detail_wrap_px`, slider callbacks, and teardown state.
- Produced: `tests.helpers.fake_tk.FakeWidget`, `FakeRoot`, `FakeTkModule`, `FakeEvent`, `OverlayUI`, and `overlay_harness`.
- Production interfaces: unchanged. No production seam was required.
- New dependencies: none.
- Public API change: none.

## Exact Deletion Mapping

The following 19 approved Batch 2 methods were removed. No non-candidate test was deleted.

| # | Removed test | Replacement behavior test |
|---:|---|---|
| 1 | `test_overlay_dpi_scaling.WindowWiringTests.test_run_computes_the_scale_and_pins_tk_font_scaling` | `test_window_run_applies_dpi_scale_to_tk_and_geometry` |
| 2 | `test_overlay_dpi_scaling.WindowWiringTests.test_run_sizes_the_window_through_px` | `test_window_and_minimized_bar_use_scaled_geometry` |
| 3 | `test_overlay_dpi_scaling.WindowWiringTests.test_detail_wrap_uses_the_scaled_window_width` | `test_status_detail_wrap_uses_scaled_window_width` |
| 4 | `test_overlay_eye_and_colors.AccountEyeToggleTests.test_eye_is_rendered_and_clickable` | `test_eye_widget_toggles_visibility_on_click` |
| 5 | `test_overlay_eye_and_colors.LogColorTests.test_renderer_uses_the_tag_function_and_configures_every_tag` | `test_log_renderer_applies_alert_tag_and_every_palette_tag` |
| 6 | `test_overlay_eye_and_colors.ButtonLayoutTests.test_destructive_buttons_guard_themselves` | `test_destructive_buttons_confirm_before_callbacks` |
| 7 | `test_overlay_eye_and_colors.ButtonLayoutTests.test_button_labels_have_no_tofu_glyphs` | `test_rendered_button_labels_are_plain_text` |
| 8 | `test_overlay_eye_and_colors.ButtonLayoutTests.test_window_metrics_and_grid_helpers` | `test_button_grid_matches_layout_contract` |
| 9 | `test_overlay_eye_and_colors.ButtonLayoutTests.test_status_rows_put_name_and_value_next_to_each_other` | `test_status_widgets_pack_left_to_right` |
| 10 | `test_overlay_eye_and_colors.ButtonLayoutTests.test_status_rows_wrap_instead_of_being_clipped` | `test_status_detail_widgets_have_wrap_budget` |
| 11 | `test_overlay_eye_and_colors.ButtonLayoutTests.test_overlay_can_be_minimized_to_a_title_bar` | `test_minimize_event_collapses_and_restores_window` |
| 12 | `test_overlay_human_like_status.BrandHeaderTests.test_brand_name_has_no_subtitle` | `test_rendered_brand_header_has_no_subtitle_widget` |
| 13 | `test_overlay_self_check_hint.SelfCheckButtonTests.test_run_builds_the_button_and_the_info_dialog` | `test_self_check_button_opens_info_dialog_with_version_payload` |
| 14 | `test_overlay_self_check_hint.SelfCheckButtonTests.test_start_accepts_the_version_callback` | `test_start_installs_version_callback_for_self_check` |
| 15 | `test_overlay_tk_lifecycle.RunTeardownSourceTests.test_frame_must_be_released_before_collecting` | `RunTeardownSourceTests.test_wrapper_collects_after_run_returns` |
| 16 | `test_overlay_tk_lifecycle.RunTeardownSourceTests.test_start_threads_the_wrapper_not_run` | `RunTeardownSourceTests.test_start_records_wrapper_as_thread_target` |
| 17 | `test_overlay_window_slider.SliderWiringTests.test_run_builds_a_custom_canvas_slider` | `test_slider_canvas_events_update_value_and_apply_on_release` |
| 18 | `test_overlay_window_slider.SliderWiringTests.test_root_drag_ignores_events_from_the_slider` | `test_root_drag_ignores_slider_events` |
| 19 | `test_region_overlay_button.OverlayButtonTests.test_calibrate_button_exists_and_is_short` | `test_calibrate_button_dispatches_callback` |

Additional source-only checks in `test_overlay_tk_lifecycle.RunTeardownSourceTests` (`test_wraps_run_in_a_finally_block`, `test_collects_before_releasing_started`, `test_gc_is_imported`) retain their original class and test IDs while now checking wrapper behavior. No `inspect.getsource` or source-text assertions remain in the seven target files.

## Fix Round 1 Evidence

- Preserved the class ID `test_overlay_tk_lifecycle.RunTeardownSourceTests` and the exact retained IDs `test_wraps_run_in_a_finally_block`, `test_collects_before_releasing_started`, and `test_gc_is_imported`. Only the two approved lifecycle candidates were removed; replacement behavior tests use new IDs in the same retained class.
- Added dangerous-button confirmed paths: with confirmations false, restart/clear callbacks do not run; with confirmations true, `restart` then `clear` callbacks run in order. Exit-overlay remains a separate callback path.
- Strengthened the eye, DPI, and log-tag assertions as requested: eye box packs right; 144 DPI final detail widget wraplengths are greater than the 96 DPI widgets for all five status rows; every palette tag receives its exact foreground color and `alert` remains bold.
- AST deletion audit against base `15ab7f8` across the seven target files: `removed=19 added=19`. The 19 removed IDs are exactly the Batch 2 candidates in the mapping table; all other original test IDs remain present.

## Replacement Coverage

- DPI: observed `tk scaling`, scaled window geometry, scaled minimized height, and scaled detail wraplength through a recording Tk root.
- Eye/colors/layout: clicked the eye widget and observed right-side placement, visibility/callback/color; rendered an ERROR line and checked every palette tag's actual foreground plus the bold `alert` tag; clicked dangerous buttons through both cancelled and confirmed confirmations; checked plain button labels, grid slots/spans, status-row pack order, scaled final wraplengths, and minimize/restore state.
- Brand/self-check: inspected rendered label text for subtitle absence; clicked the self-check button and observed the injected version callback plus `_info_dialog` arguments.
- Lifecycle: asserted `_run` returns before `gc.collect`, `_STARTED` is still set during collection, and `start()` targets `_run_overlay_thread`.
- Slider/drag: drove press/move/release bindings and observed value text plus resize callback; sent slider-origin drag events to the root and verified geometry did not move.
- Calibration: clicked the rendered `校准` button and observed its callback and grid slot.

## Original Verification

- Before replacement, all 19 candidates were source-text assertions, including `inspect.getsource(log_overlay._run)` and direct source slicing.
- The repository baseline recorded in `docs/testing/TEST_RATIONALIZATION_PLAN.md` was `1520 tests`, 134.726s, 1 failure, 1 skipped. R02C1 later recorded the same headless count as `1523 run / 1522 passed / 0 failures / 0 errors / 1 skipped`.
- Initial selfcheck attempt: `uv run python selfcheck.py --json` exited 1 with `UnicodeEncodeError: 'gbk' codec can't encode character '\u2705'`. The single retry with `PYTHONIOENCODING=utf-8` ran but reported environment blockers only: not elevated and Hearthstone Logs directory not configured. These are outside R02C2.

## Fresh Verification

```powershell
uv run python -m unittest tests.test_overlay_dpi_scaling tests.test_overlay_eye_and_colors tests.test_overlay_human_like_status tests.test_overlay_self_check_hint tests.test_overlay_tk_lifecycle tests.test_overlay_window_slider tests.test_region_overlay_button -v
# exit 0; Ran 155 tests; OK
```

```powershell
uv run python tools/run_headless_tests.py
# exit 0; summary: run=1523 passed=1522 failures=0 errors=0 skipped=1
```

```powershell
uv sync --locked --all-groups
# exit 0; Resolved 244 packages; Audited 77 packages

uv run python -m compileall -q src tests tools
# exit 0; no output

uv run python tools/check_architecture.py
# exit 0; [architecture] PASS: src/hslegend is not present yet

uv run python tools/check_architecture.py --self-test
# exit 0; [architecture:self-test] PASS: 16/16 cases

uv run python tools/check_task_scope.py --task R02C2 --json
# exit 0; ok=true; diff_source="git diff --cached"; violations=[]

uv run python tools/check_task_scope.py --self-test
# exit 0; [scope:self-test] PASS: 8/8 cases
```

## Scope Check

`uv run python tools/check_task_scope.py --task R02C2 --json` exited 0 with `ok=true`, `diff_source="git diff --cached"`, and no violations. The staged file set is:

- `TOTAL_TASK.md`
- `docs/agent/reports/R02C2-implementer.md`
- `docs/testing/TEST_INVENTORY.md`
- `docs/testing/TEST_RATIONALIZATION_PLAN.md`
- `tests/helpers/__init__.py`
- `tests/helpers/fake_tk.py`
- `tests/test_overlay_dpi_scaling.py`
- `tests/test_overlay_eye_and_colors.py`
- `tests/test_overlay_human_like_status.py`
- `tests/test_overlay_self_check_hint.py`
- `tests/test_overlay_tk_lifecycle.py`
- `tests/test_overlay_window_slider.py`
- `tests/test_region_overlay_button.py`

The pre-existing unstaged controller edits to `docs/agent/HANDOFF.md`, `docs/agent/TASK_BOARD.md`, and `docs/agent/TASK_BOARD.json` are not part of this staged implementer set.

## Risks And Review Focus

- The fake Tk harness records widget options and callbacks but does not render pixels or run the event loop. Review should confirm the assertions still represent user-visible layout/copy behavior, not merely implementation wiring.
- Widget identity is inferred from rendered text constants; review should verify duplicate-label cases remain unambiguous.
- `_run` remains a large function. R02C2 intentionally avoids a production refactor; no user-visible behavior changed.
- The lifecycle tests replace source order with wrapper call ordering. Review should confirm this still protects the Tcl cross-thread teardown invariant.
- Rollback: revert R02C2 files to restore the 19 source-string tests and remove the fake Tk harness. No production behavior rollback is required.
