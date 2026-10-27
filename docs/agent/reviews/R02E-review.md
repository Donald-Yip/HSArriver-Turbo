# R02E Stage 1 Independent Review

## Verdict

**APPROVED**

- Branch: `feature/R02E-test-cleanup`
- Review base: `0cf215d`
- Implementation commits: `a990515`, `cf4ab44`, `3f1b95f`
- Reviewed head: `3f1b95f`
- Reviewer scope: read-only test/commit audit; no tests, production code, task book, board, handoff, or report files were edited.

## Findings

### Critical

None.

### Important

None.

### Minor

1. `tests/test_screen_regions.py::CalibrationTargetTests.test_interface_recognition_boxes_match_battle_entry` imports frame builders from `tests.test_battle_entry`. The imported functions are pure synthetic-frame helpers, so this does not invalidate the behavior gate, but it creates avoidable cross-test-module coupling. Moving those builders to `tests/helpers/` would be a cleaner follow-up; it is not required for approval.
2. `tests/test_web_prepare_flow.py::OverlayVerificationTests.test_verification_skips_unknown_statuses` no longer uses `Thread.assert_not_called()`, and it verifies the observable control state remains unchanged. It would not detect a no-op background thread being started for an unknown status, but no user-visible regression is exposed by that scenario. This is a non-blocking strength limitation, not a reason to reject.

## Scope And Identity Verification

Whole-tree AST comparison from `0cf215d` to `3f1b95f` found exactly three removed methods, zero additions, and five changed method bodies:

Removed, all approved:

- `tests/test_web_hot_control.py::HotChannelTests.test_delay_effect_scope_table` (A01)
- `tests/test_draw_extra_delay_settings.py::ConfigTests.test_key_is_part_of_the_user_delay_whitelist` (A02)
- `tests/application/test_dto.py::DtoContractTests.test_invalid_confidence_and_status_are_rejected` (M01)

Changed, all approved replacements or merge targets:

- `tests/test_screen_regions.py::CalibrationTargetTests.test_interface_recognition_boxes_match_battle_entry` (R06)
- `tests/test_roi_config_frame.py::RecommendationConfigIntegrationTests.test_the_rank_box_falls_back_to_the_code_default` (R07)
- `tests/application/test_dto.py::DtoContractTests.test_observations_are_immutable_and_serializable` (M01 merge target)
- `tests/test_web_logs_wiring.py::LogTrimTests.test_small_runtime_log_is_left_alone` (R08)
- `tests/test_web_prepare_flow.py::OverlayVerificationTests.test_verification_skips_unknown_statuses` (R09)

All 34 deferred B3/B4/B6-B9 candidate identities are still present with AST-equivalent bodies versus `0cf215d`. No unapproved test identity changed, and no production file changed.

## Requirement Checks

### A01/A02 public equivalent gates

A01 and A02 are absent. The replacement coverage is real public behavior:

- `tests.test_hsctl.CliDelayAndScreenshotContractTests.test_delays_show_exposes_every_supported_key_and_effect` exercises `hsctl delays show` and checks every public delay key/effect.
- `tests.test_hsctl.CliDelayAndScreenshotContractTests.test_delays_set_accepts_every_supported_key` exercises `hsctl delays set` through the HTTP request body.
- `tests.test_draw_extra_delay_settings.WebSettingsTests.test_status_exposes_the_draw_delay` and the adjacent Web settings tests exercise the public Web delay API and validation.

### R06 behavior replacement

R06 no longer compares module constants. It builds fixed main-menu and deck-select frames from the calibration target boxes and calls `battle_entry.identify`, asserting `main_menu` and `deck_select` states. It retains the minimum-size assertion. This is a meaningful behavioral replacement; the only concern is the non-blocking cross-test-module helper import noted above.

### R07 save/load behavior

R07 checks the code-default rank box, writes a client-relative saved rank box to `ui_config.json`, constructs a new `RecommendationConfig`, and verifies the saved value is reloaded. It no longer references `_USER_BOX_KEYS`.

### M01 coverage preservation

The removed M01 method's confidence boundary cases are present in `test_observations_are_immutable_and_serializable`:

- `GameObservation(..., 1.1)` raises `ValueError`.
- `RecommendationObservation(..., -0.1, ...)` raises `ValueError`.

The `max_turns=0` case remains in `test_max_turns_requires_an_exact_positive_int`, alongside the exact-positive-int checks. No confidence or max_turns coverage was dropped.

### R08/R09 mock-only replacement

R08 now creates a real temporary runtime log, calls `_log`, and checks preserved original content, appended content, bounded size, and absence of the temporary replacement file. R09 no longer patches `threading.Thread`; it checks `CTRL.prepared` and the preserved automation-thread reference. Neither test now relies on a mock-call-only assertion.

### Suite membership and counts

Independent AST count at HEAD: 1,523 top-level/default methods, 1,685 discoverable methods, and 3 native opt-in methods. This matches the required stage result. The corrected final targets remain documented as default 1,495, discover 1,657, and opt-in 20.

### Commit scope and rollback

Each implementation commit is independently limited and revertible:

- `a990515` changes B1 test identities plus B1 documentation only.
- `cf4ab44` changes M01/its merge target plus count documentation only.
- `3f1b95f` changes R08/R09 plus stage reporting/status documentation only.

No production module changed in any commit. `git status --short --branch` was clean after verification.

## Verification Commands And Results

All Python commands used `uv`; `PYTHONUTF8=1` was set for Windows console compatibility.

### Focused gates

```powershell
$env:PYTHONUTF8='1'; uv run python -m unittest tests.test_hsctl.CliDelayAndScreenshotContractTests tests.test_draw_extra_delay_settings.WebSettingsTests tests.test_click_safety_checks.ClickSafetyContractTests tests.test_screen_regions.CalibrationTargetTests tests.test_roi_config_frame.RecommendationConfigIntegrationTests
```

Exit code: `0`; `Ran 31 tests`; `OK`.

```powershell
$env:PYTHONUTF8='1'; uv run python -m unittest tests.application.test_dto
```

Exit code: `0`; `Ran 6 tests`; `OK`.

```powershell
$env:PYTHONUTF8='1'; uv run python -m unittest tests.test_web_logs_wiring.LogTrimTests tests.test_web_prepare_flow.OverlayVerificationTests
```

Exit code: `0`; `Ran 9 tests`; `OK`.

### Full gates

```powershell
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py
```

Exit code: `0`; `Ran 1523 tests`; `OK (skipped=1)`.

```powershell
$env:PYTHONUTF8='1'; uv run python -m unittest discover -s tests -t .
```

Exit code: `0`; `Ran 1685 tests`; `OK (skipped=1)`.

```powershell
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py --opt-in
```

Exit code: `0`; `Ran 3 tests`; `OK`; opt-in summary `run=3 passed=3 failures=0 errors=0 skipped=0`.

### Architecture, compile, and diff checks

```powershell
$env:PYTHONUTF8='1'; uv run python -m unittest tests.domain.test_purity tests.application.test_purity
```

Exit code: `0`; `Ran 4 tests`; `OK`.

```powershell
$env:PYTHONUTF8='1'; uv run python -m compileall -q tests
```

Exit code: `0`.

```powershell
git diff --check 0cf215d..HEAD
```

Exit code: `0`.

Whole-tree AST identity comparison and candidate-body comparison both exited `0` with no unexpected deltas.

## Environment Note

`uv run python selfcheck.py --json` initially failed on GBK console encoding and then exited `1` with the JSON mode still unable to render non-ASCII output cleanly. It reported only environment prerequisites, including non-admin execution and an unconfigured Hearthstone log directory. Neither condition affected the headless test gates used for this review.

## Required Corrections

None. The stage-1 test cleanup is approved for integration.
