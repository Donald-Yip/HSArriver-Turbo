# R02C5 Implementer Report

## Status

APPROVED_FOR_COMMIT (complete R02C5 file set staged; no commit or push).

## Git

- Branch: `feature/R02C5-headless-final`
- Base SHA: `aee522e5bbf63e24d6d3a33a4fc13c684dadf321`
- Head SHA: `aee522e` (no commit)
- Scope task: R02C5

## Fix Round 1

- Sanitized remaining machine-specific executable examples:
  `docs/testing/TEST_RATIONALIZATION_PLAN.md`, `tests/__init__.py`, and
  `tests/native/README.md` now use `<python>` / `<path-to-node>` placeholders.
- Added `tests/test_ci_gates.py` with a committed regression that proves
  `RequiredOptInResult` converts a required skip into a failure. The default
  headless runner collects it automatically.
- Updated `TOTAL_TASK.md` current phase, branch, scope command, opt-in gate command,
  task status, and next action to R02C5.
- Updated HANDOFF and TASK_BOARD state to `ready_for_review`.
- Normalized line endings and removed trailing blank/whitespace lines from
  `TOTAL_TASK.md`, `docs/agent/HANDOFF.md`, `docs/agent/TASK_BOARD.md`,
  `docs/agent/TASK_BOARD.json`, and `docs/tasks/R02C5-headless-final.md`.
- The complete R02C5 file set, including this report, is staged for reproducible
  scope evidence.

## Changed Files

- `TOTAL_TASK.md`
- `docs/agent/HANDOFF.md`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/TASK_BOARD.json`
- `docs/agent/reports/R02A-implementer.md`
- `docs/agent/reports/R02C5-implementer.md`
- `docs/tasks/R02C5-headless-final.md`
- `docs/testing/CI_GATES.md`
- `docs/testing/TEST_INVENTORY.md`
- `docs/testing/TEST_RATIONALIZATION_PLAN.md`
- `tests/__init__.py`
- `tests/native/README.md`
- `tests/test_ci_gates.py`
- `tests/test_friendly_hand_target_battlecries.py`
- `tests/test_hand_or_board_targets.py`
- `tests/test_web_selfcheck_api.py`
- `tests/test_window_tracker.py`
- `tools/run_headless_tests.py`

## Interfaces

- Production behavior changes: none.
- Public product API/config/log/output changes: none.
- Test-only gate: `tools/run_headless_tests.py --opt-in` runs the two Node/native
  modules and converts a required `skipTest` into a failure.
- Regression coverage: `RequiredOptInResultTests.test_required_skip_is_a_failure`
  asserts one failure, zero skips, unsuccessful result, and the required-skip reason.
- Test seams used: public `Path.open`, public module object
  `web_ui.selfcheck.run_checks`, injected `WindowMoveTracker` hook/clock/reader
  callables, and public `wait_ready()`.
- New dependencies: none.

## Private Patch Before / After

| Method | Before | After |
|---|---|---|
| `HandTargetCardDataTests.test_unreadable_card_data_falls_back_to_the_legacy_cards` | patch `src.game_state.hand_target._metadata_hand_target_card_ids` | fail public `Path.open`, reload module to clear metadata caches, assert public `is_friendly_hand_target_card()` fallback |
| `HandOrBoardCardDataTests.test_unreadable_card_data_falls_back_to_the_legacy_card` | patch `src.game_state.hand_target._metadata_hand_or_board_card_ids` | same public `Path.open` seam; assert `hand_or_board_target_card_ids()` returns the legacy set |
| `SelfcheckCacheTests.test_result_is_cached_until_forced` | patch runner and `_log_selfcheck`; inspect call count | `FakeSelfcheckRunner` at public `web_ui.selfcheck.run_checks`; assert two calls, cached identity, forced rerun |
| `SelfcheckCacheTests.test_status_carries_the_summary_after_a_run` | patch runner and `_log_selfcheck` | fake runner; assert checked/ok/failed/python status summary |
| `SelfcheckCacheTests.test_api_selfcheck_wraps_the_result` | patch runner and `_log_selfcheck` | fake runner; assert API wrapper and one runner call |

No test patches an underscore-prefixed production helper for these five methods.

## Behavior Coverage

- Hand-target metadata failure still keeps `CATA_490` and `CATA_563`, rejects an
  unrelated card, and preserves the ambiguous `CATA_161` legacy fallback.
- Selfcheck cache still returns the same result without rerunning, `force=True`
  runs again, status summaries preserve failure/python fields, `/api/selfcheck`
  GET/POST and the API wrapper preserve their result schema, and a crashed runner
  still returns a conclusion.
- Window tracking async tests synchronize hook attachment, failure attempts,
  unhooks, and rehook generations with `threading.Condition`; the non-blocking
  `wait_ready()` check uses `threading.Event`; the event test waits for attachment
  and `wait_ready()` before firing.
- `test_window_tracker.py` contains no `time.sleep`, no custom `wait_for` polling
  loop, and no wall-clock elapsed-time assertion for the six rewritten cases.
- `test_ci_gates.py` keeps the explicit opt-in skip-to-failure contract in the
  default headless suite.
- The default headless runner still selects only top-level `tests/test_*.py`; no
  Node/native module is imported by default.
- The sole default-suite skip is explicit and environment-gated:
  `DisplayMappingSamplingTests.test_live_window_lookup` skips when no live
  Hearthstone window exists. It is not used to hide Node/native failures.

## Raw Verification

```powershell
uv sync --locked --all-groups
```

exit 0

```text
Resolved 244 packages in 1ms
Audited 77 packages in 4ms
```

```powershell
$env:PYTHONIOENCODING='utf-8'
uv run python -m unittest tests.test_friendly_hand_target_battlecries tests.test_hand_or_board_targets tests.test_web_selfcheck_api tests.test_window_tracker tests.test_ci_gates -v
```

exit 0

```text
Ran 73 tests in 2.741s
OK
```

```powershell
$env:PYTHONIOENCODING='utf-8'
for ($i=1; $i -le 3; $i++) { uv run python -m unittest tests.test_window_tracker -q }
```

exit 0; each iteration: `Ran 16 tests ... OK`; final marker
`window-tracker-repeat=3/3-pass`.

```powershell
uv run python tools/run_headless_tests.py --opt-in
```

exit 0

```text
[headless-tests] selected 3 opt-in tests from 2 modules; required skips fail
[headless-tests] opt-in summary: run=3 passed=3 failures=0 errors=0 skipped=0
```

```powershell
$env:PYTHONIOENCODING='utf-8'
uv run python tools/run_headless_tests.py
```

exit 0

```text
Ran 1524 tests in 138.692s
OK (skipped=1)
[headless-tests] headless summary: run=1524 passed=1523 failures=0 errors=0 skipped=1
```

```powershell
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/check_architecture.py --self-test
uv run python tools/check_task_scope.py --self-test
```

exit 0

```text
[architecture] PASS: src/hslegend is not present yet
[architecture:self-test] PASS: 16/16 cases
[scope:self-test] PASS: 8/8 cases
```

Sanitization check: user-directory, drive-letter Node, and conda executable examples
produced no matches in the three reviewed files; `<python>` and `<path-to-node>`
placeholders are present in all applicable examples.

## Scope Check

The complete R02C5 file set is staged, including this report.

```powershell
uv run python tools/check_task_scope.py --task R02C5 --json
```

exit 0

```json
{
  "ok": true,
  "task": "R02C5",
  "task_book": "docs/tasks/R02C5-headless-final.md",
  "diff_source": "git diff --cached",
  "changed_files": [
    "TOTAL_TASK.md",
    "docs/agent/HANDOFF.md",
    "docs/agent/TASK_BOARD.json",
    "docs/agent/TASK_BOARD.md",
    "docs/agent/reports/R02A-implementer.md",
    "docs/agent/reports/R02C5-implementer.md",
    "docs/tasks/R02C5-headless-final.md",
    "docs/testing/CI_GATES.md",
    "docs/testing/TEST_INVENTORY.md",
    "docs/testing/TEST_RATIONALIZATION_PLAN.md",
    "tests/__init__.py",
    "tests/native/README.md",
    "tests/test_ci_gates.py",
    "tests/test_friendly_hand_target_battlecries.py",
    "tests/test_hand_or_board_targets.py",
    "tests/test_web_selfcheck_api.py",
    "tests/test_window_tracker.py",
    "tools/run_headless_tests.py"
  ],
  "violations": []
}
```

No commit or push was made.

## Risks And Review Focus

- Confirm the hand-target fallback tests genuinely exercise the public file-read seam
  and do not depend on earlier cache state; module reload plus cleanup covers both.
- Confirm `--opt-in` is the only gate that converts skips to failures and
  `test_ci_gates.py` protects that behavior in the default suite.
- Confirm the staged index contains the complete file set above.
- Review the `Condition` predicates for attach/unhook/rehook races; three consecutive
  focused runs passed in this round.
- Rollback is test/tooling/docs only; no production rollback is required.
