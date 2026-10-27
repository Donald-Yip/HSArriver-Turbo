# R02E Final Independent Review

## Verdict

**APPROVED**

Reviewed base: `62315f5cd8777bf889e45af69003d7905da7ca88`. Reviewed commits: `a181960` (B4), `f6d890a` (B3), and `a156a1e` (docs).

## Findings

No Critical, Important, or Minor findings.

## Spec Coverage

- PASS: B4 removes exactly M06-M08/R01/A03 and adds one public fake-Tk lifecycle scenario.
- PASS: B4 covers both normal and failed mainloop startup in the same public `log_overlay.start()` scenario. The failure path asserts captured reports, GC, `_STARTED`/`_CLOSING` release, deterministic thread completion, and successful reopen.
- PASS: No `_run` patch or private call-order drill was reintroduced. The scenario executes the real thread target with fake Tk.
- PASS: B3 removes exactly M02-M05/R02/A04/R10-R15 and adds three real-script Node DOM behavior methods.
- PASS: The three B3 methods cover selfcheck collapse behavior, settings render/submit round trips, and overlay/version behavior through `tests/web_ui_behaviors.js`.
- PASS: No source-string substitute remains. Checks for `$("`, `refreshStatus()`, prepared/overlay ternaries, and slicing found no replacement implementation-string assertions in the retained default web tests.
- PASS: Static HTML assertions retained in the five cleaned web test files are user-visible ids, copy, and structure only.
- PASS: Node runtime discovery is shared through `tests/helpers/node_runtime.py` by both frontend opt-in tests. No dependency manifest or lockfile changed.
- PASS: Counts are default `1495`, discover `1657`, CI opt-in `19`, native-live `1`, all real TestCase methods `1677`.
- PASS: Full default, discover, and CI opt-in gates pass.
- PASS: Native-live fail-closed behavior holds when no Hearthstone window is available.
- PASS: B4 and B3 each reverse-apply cleanly from final HEAD.
- PASS: No production, public API, data, coordinate, runtime, dependency, or lockfile changes.
- PASS: Scope gate reports no violations.

## Architecture And Dependency Check

No production module or runtime behavior changed. Added Python and JavaScript files are test infrastructure only; the Node helper uses the standard library and no new package or browser framework was introduced.

## Test Adequacy

The public fake-Tk scenario now preserves both normal and exceptional lifecycle cleanup without private `_run` call-order assertions. The opt-in Node gate executes the real page script and covers all approved replacement behaviors. Suite counts and all required gates match the final ledger.

## Scope Drift Check

PASS. `uv run python tools/check_task_scope.py --task R02E --base-ref 62315f5 --json` exited `0` with `ok: true` and `violations: []`.

## Rollback Adequacy

PASS. `a181960` and `f6d890a` each reverse-apply cleanly from final HEAD. Whole-range whitespace and reverse-patch checks also pass.

## Verification Evidence

```powershell
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py
```

Exit `0`:

```text
Ran 1495 tests in 90.648s
OK
[headless-tests] headless summary: run=1495 passed=1495 failures=0 errors=0 skipped=0
DEFAULT_HEADLESS_EXIT=0
```

```powershell
$env:PYTHONUTF8='1'; uv run python -m unittest discover -s tests -t .
```

Exit `0`:

```text
Ran 1657 tests in 92.146s
OK
DISCOVER_EXIT=0
```

```powershell
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py --opt-in
```

Exit `0`:

```text
Ran 19 tests in 10.105s
OK
[headless-tests] selected 19 opt-in tests from 7 modules; required skips fail
[headless-tests] opt-in summary: run=19 passed=19 failures=0 errors=0 skipped=0
CI_OPT_IN_EXIT=0
```

```powershell
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py --native-live
```

Exit `1` because the current environment had no Hearthstone window:

```text
FAIL: test_live_window_lookup
AssertionError: required opt-in test skipped: 本机没有运行中的炉石窗口
Ran 1 test
FAILED (failures=1)
[headless-tests] native-live summary: run=1 passed=0 failures=1 errors=0 skipped=0
NATIVE_LIVE_EXIT=1
```

Counts:

```text
default=1495
discover=1657
ci_opt_in=19
native_live=1
all_real=1677
COUNT_EXIT=0
```

Focused checks:

```text
tests.test_overlay_tk_lifecycle: Ran 21 tests, OK, B4_FOCUSED_EXIT=0
tests.opt_in.test_web_ui_behaviors + tests.opt_in.test_surrender_frontend: Ran 4 tests, OK, B3_FOCUSED_EXIT=0
```

Rollback, scope, and production checks:

```text
a181960 REVERSE_FROM_HEAD=0
f6d890a REVERSE_FROM_HEAD=0
RANGE_DIFF_CHECK=0
RANGE_REVERSE=0
PRODUCTION_OR_LOCK_CHANGE=0
SCOPE_EXIT=0
```
