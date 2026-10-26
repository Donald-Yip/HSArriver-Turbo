# R02C4 Implementer Report

## Status

APPROVED_FOR_COMMIT (working tree only; no commit or push).

## Git

- Branch: `feature/R02C4-native-node`
- Base SHA: `7a8ccf8`
- Head SHA: `7a8ccf8` (no commit)
- Scope task: R02C4

## Changed Files

Implementer-authored:

- `tests/native/README.md`
- `tests/native/test_process_enumeration.py`
- `tests/native/test_surrender_frontend.py`
- `tests/frontend_surrender_roundtrip.js` (caller reference updated only)
- `tests/test_surrender_frontend.py` (deleted after moving its test)
- `tests/test_hearthstone_liveness.py` (two real-enumeration methods removed; two fake methods retained)
- `tools/run_headless_tests.py`
- `docs/tasks/R02C4-native-node.md`
- `docs/testing/CI_GATES.md`
- `docs/testing/TEST_INVENTORY.md`
- `docs/testing/TEST_RATIONALIZATION_PLAN.md`
- `docs/agent/reports/R02C4-implementer.md`
- `TOTAL_TASK.md`

`docs/tasks/R02C4-native-node.md` now lists `tests/test_hearthstone_liveness.py`
under Allowed Files. The original task book required moving two methods out of that
file but omitted the source file itself; no production file was added to scope.

Pre-existing controller edits to `docs/agent/HANDOFF.md`,
`docs/agent/TASK_BOARD.md`, and `docs/agent/TASK_BOARD.json` were not authored by
this implementer and are not part of the implementer change set.

## Interfaces

- Consumed: `src.safety.hearthstone_liveness.list_process_names`,
  `web/index.html`, and the existing `tests/frontend_surrender_roundtrip.js` fake-DOM harness.
- Produced: a default-runner test-selection contract (`tests/test_*.py` only), an
  opt-in module layout under `tests/native/`, and the optional `HSL_NODE` environment
  variable for explicit Node executable selection.
- Production behavior changes: none.
- Public product API/config/log changes: none. `HSL_NODE` is test-only environment configuration.
- New dependencies: none.

## Move Mapping

| Original test | New location / result |
|---|---|
| `tests.test_surrender_frontend.FrontendRoundTripTests.test_nested_config_survives_the_form` | `tests.native.test_surrender_frontend.FrontendRoundTripTests.test_nested_config_survives_the_form` |
| `tests.test_hearthstone_liveness.NativeProcessTests.test_can_enumerate_processes` | `tests.native.test_process_enumeration.NativeProcessTests.test_can_enumerate_processes` |
| `tests.test_hearthstone_liveness.NativeProcessTests.test_finds_this_python_process` | `tests.native.test_process_enumeration.NativeProcessTests.test_finds_this_python_process` |
| `tests.test_hearthstone_liveness.NativeProcessTests.test_returns_bool_for_unknown_process` | Retained at the original path; fake headless behavior unchanged. |
| `tests.test_hearthstone_liveness.NativeProcessTests.test_unreadable_process_list_is_unknown` | Retained at the original path; fake headless behavior unchanged. |

## Behavior Coverage

- Frontend coverage is unchanged: nested AND/OR/NOT round-trip, fields that the form
  cannot represent, deck-level fields, collapsed-state persistence, save-under-collapse,
  and rendered group/arrow/summary assertions all remain in the JS harness.
- The Python Node invocation now uses `encoding="utf-8"` with strict errors, so the
  previous GBK `UnicodeDecodeError` cannot recur.
- Node resolution checks `HSL_NODE`, then `node`/`node.exe` on `PATH`, then standard
  Node.js locations derived from `ProgramFiles`, `ProgramFiles(x86)`, `ProgramW6432`,
  and `LOCALAPPDATA`. No user-specific path or drive-letter path remains.
- `tests/native` deliberately has no `__init__.py`, so ordinary recursive discovery from
  `tests` does not collect it. The headless runner additionally enumerates only
  top-level `tests/test_*.py` modules and therefore does not depend on that packaging detail.
- The real ctypes enumeration behavior remains runnable in the opt-in native module;
  the two injected-fake process checks remain in the default suite.

## Original Verification

Baseline before edits:

```powershell
uv run python -m unittest tests.test_surrender_frontend.FrontendRoundTripTests.test_nested_config_survives_the_form tests.test_hearthstone_liveness.NativeProcessTests -v
# exit 1; Ran 5 tests; FAILED (failures=1)
# UnicodeDecodeError: 'gbk' codec can't decode byte 0x94 in position 8
# NativeProcessTests: 4 tests passed
```

## Fresh Verification

```powershell
$env:PYTHONIOENCODING='utf-8'; uv run python selfcheck.py --json
# exit 1; environment blockers only: not elevated and Hearthstone Logs directory not configured
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

uv run python tools/check_task_scope.py --self-test
# exit 0; [scope:self-test] PASS: 8/8 cases
```

```text
uv run python -m unittest tests.test_hearthstone_liveness.NativeProcessTests -v
exit 0; Ran 2 tests; OK

uv run python -m unittest tests.native.test_process_enumeration tests.native.test_surrender_frontend -v
exit 0; Ran 3 tests; OK
```

Explicit Node configuration smoke:

```powershell
$env:HSL_NODE=(Get-Command node).Source
uv run python -m unittest tests.native.test_surrender_frontend -v
# HSL_NODE=<detected-node>; exit 0; Ran 1 test; OK
```

Default discovery probe:

```text
default_discovered=1523
native_discovered=0
```

```text
uv run python tools/run_headless_tests.py
exit 0; [headless-tests] summary: run=1523 passed=1522 failures=0 errors=0 skipped=1
```

Scope task gate result is recorded below after running it against a temporary index
containing only the implementer-authored files.

## Scope Check

```text
uv run python tools/check_task_scope.py --task R02C4 --json
exit 0; ok=true; changed_files=13; violations=[]

changed files:
TOTAL_TASK.md
docs/agent/reports/R02C4-implementer.md
docs/tasks/R02C4-native-node.md
docs/testing/CI_GATES.md
docs/testing/TEST_INVENTORY.md
docs/testing/TEST_RATIONALIZATION_PLAN.md
tests/native/README.md
tests/native/test_process_enumeration.py
tests/native/test_surrender_frontend.py
tests/frontend_surrender_roundtrip.js
tests/test_hearthstone_liveness.py
tests/test_surrender_frontend.py
tools/run_headless_tests.py
```

The index was populated with only the implementer files for the check and then
unstaged again. No production file changed and no forbidden file changed. No commit
or push was made.

`git diff --check` passes for the implementer file set. A full-worktree check still
reports pre-existing trailing whitespace in controller-owned
`docs/agent/HANDOFF.md`, `docs/agent/TASK_BOARD.md`, and
`docs/agent/TASK_BOARD.json`; those files were not edited by this implementer.

## Risks And Review Focus

- Confirm `tests/native` stays non-package, and that adding `__init__.py` would not
  silently re-enable default collection by raw `unittest discover` (the custom headless
  runner remains safe either way).
- Confirm moving only the two real enumeration methods left both injected-fake process
  checks in the default suite.
- Confirm the Node test retains every original round-trip/collapse/render assertion and
  that `HSL_NODE` failure is explicit rather than a silent skip.
- The task-book source-file addition is intentional and limited to the test file whose
  two methods the acceptance criteria require moving.
- Rollback: restore the moved tests to their original paths and restore the previous
  exclusion/filtering runner; no production rollback is required.
