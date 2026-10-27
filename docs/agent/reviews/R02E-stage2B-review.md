# R02E Stage 2B Independent Review

## Verdict

**APPROVED**

Reviewed base: `d13d30849d39ad5a750e8c8c4f1595deafbcb4df`. Reviewed commits: `5230968` (B9), `69f91c3` (B8), `50f80da` (B7), and `2623de9` (docs).

## Findings

No Critical, Important, or Minor findings.

## Prior Blocker Closure

- PASS: Default manifest contract is import-free. Instrumentation observed `SUBPROCESS_RUN_CALLS=0` and `OPT_IN_IMPORTS=0`.
- PASS: O12 requires exactly Git exit code `1` and surfaces stderr. An invalid `GIT_DIR` produced Git `128` and the test correctly failed with `1`.
- PASS: B9/B8/B7 each reverse-apply cleanly from final HEAD; all three checks exited `0`.
- PASS: B9 adds `tests/frontend_surrender_roundtrip.js` to Allowed Files, and no later commit edits that task book or JavaScript helper.
- PASS: No static MCP source/schema replacement exists; the real stdio MCP smoke remains in required CI opt-in.
- PASS: Gate directories are `tests/opt_in` and `tests/native_live`; ordinary CI invokes only `--opt-in`.

## Spec Coverage

- PASS: Counts are default `1511`, discover `1673`, CI opt-in `16`, native-live `1`.
- PASS: Full default headless gate passed: `1511/1511`, zero failures/errors/skips.
- PASS: Full discover gate passed: `1673/1673`, zero failures/errors.
- PASS: CI opt-in passed: `16/16`, zero skips.
- PASS: Native-live expected failure held: one test, zero passed, zero skips, one failure because no Hearthstone window was available.
- PASS: Required skips fail through `RequiredOptInResult`.
- PASS: Static pyproject/requirements parity remains in the default suite.
- PASS: Shared environment helper contains no test cases or assertions.
- PASS: B3/B4 protected files remain untouched: `B3_B4_PROTECTED_FILES_CHANGED=0`.
- PASS: No production code, lockfile, public API, data, coordinates, or runtime behavior changed.

## Architecture And Dependency Check

No production module, dependency, public interface, runtime configuration, coordinate, or data change. The runner, CI, gate directories, and helpers are test infrastructure only.

## Scope Drift Check

PASS. `uv run python tools/check_task_scope.py --task R02E --base-ref d13d308 --json` exits `0` with `ok: true` and `violations: []`.

## Rollback Adequacy

PASS. B9/B8/B7 reverse checks all exit `0` from final HEAD. `git diff --check d13d308 HEAD` and whole-range reverse validation also exit `0`.

## Verification Evidence

```powershell
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py
```

Exit `0`:

```text
Ran 1511 tests in 89.185s
OK
[headless-tests] headless summary: run=1511 passed=1511 failures=0 errors=0 skipped=0
DEFAULT_HEADLESS_EXIT=0
```

```powershell
$env:PYTHONUTF8='1'; uv run python -m unittest discover -s tests -t .
```

Exit `0`:

```text
Ran 1673 tests in 93.096s
OK
DISCOVER_EXIT=0
```

```powershell
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py --opt-in
```

Exit `0`:

```text
Ran 16 tests in 12.452s
OK
[headless-tests] selected 16 opt-in tests from 6 modules; required skips fail
[headless-tests] opt-in summary: run=16 passed=16 failures=0 errors=0 skipped=0
CI_OPT_IN_EXIT=0
```

```powershell
$env:PYTHONUTF8='1'; uv run python tools/run_headless_tests.py --native-live
```

Expected exit `1` with no Hearthstone window:

```text
FAIL: test_live_window_lookup
AssertionError: required opt-in test skipped: 本机没有运行中的炉石窗口
Ran 1 test
FAILED (failures=1)
[headless-tests] native-live summary: run=1 passed=0 failures=1 errors=0 skipped=0
NATIVE_LIVE_EXIT=1
```

```text
default=1511
discover=1673
ci_opt_in=16
native_live=1
COUNT_EXIT=0
```

```text
RESULT_OK=True
SUBPROCESS_RUN_CALLS=0
OPT_IN_IMPORTS=0
DEFAULT_CONTRACT_ISOLATION_EXIT=0
```

```text
AssertionError: 1 != 128 : git check-ignore must report exactly 1; got 128; stderr=b"fatal: not a git repository..."
FAILED (failures=1)
O12_BAD_GIT_DIR_EXIT=1
```

```text
5230968 REVERSE_FROM_HEAD=0
69f91c3 REVERSE_FROM_HEAD=0
50f80da REVERSE_FROM_HEAD=0
RANGE_DIFF_CHECK=0
RANGE_REVERSE=0
```

```text
CI_NATIVE_LIVE_MENTIONS=0
CI_OPT_IN_MENTIONS=1
B3_B4_PROTECTED_FILES_CHANGED=0
PRODUCTION_OR_LOCK_CHANGE=0
SCOPE_EXIT=0
```

## Gate Documentation Check

```text
docs/testing/TEST_CLEANUP_PLAN.md:
- O02/O03 -> tests/opt_in/test_mcp_server.py
- real stdio smoke is the gate
- no source-structure test

tests/frontend_surrender_roundtrip.js:
- tests/opt_in/test_surrender_frontend.py
```
