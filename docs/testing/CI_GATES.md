# CI And Headless Test Gates

R02B adds two enforceable repository gates and one Windows headless CI job.

## Local Required Checks

```powershell
uv sync --locked --all-groups
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/check_architecture.py --self-test
uv run python tools/check_task_scope.py --task R02B
uv run python tools/check_task_scope.py --self-test
uv run python tools/run_headless_tests.py
```

`check_architecture.py` uses only the standard library AST. It scans `src/hslegend` and
fails on:

- root legacy-module imports from any layer, including `bootstrap` and `entrypoints`;
- application imports of adapters, plugins, bootstrap, entrypoints, or legacy;
- adapter imports of use cases, bootstrap, or entrypoints;
- plugin imports of adapters, runtime, filesystem, network, subprocess, threading, or
  other IO/input backends;
- plugin calls to `open`, `input`, `exec`, `eval`, `compile`, or `__import__`;
- import cycles in the new package.

If `src/hslegend` does not exist yet, the current check passes.

False-positive boundary: this is a static AST/name check, not a Python evaluator.
Dynamic imports, imports selected through containers or `getattr`, generated code,
aliases, and full lexical shadowing cannot be resolved exactly. Plugin built-in calls
are ignored when the same name is bound at module scope (for example `def open(...)`),
but a local function parameter or more complex shadowing may still be reported. Keep
plugin code explicit and review any such finding instead of suppressing the gate.

The plugin backend list includes direct file, Win32, image, and input modules such as
`tempfile`, `pathlib`, `shutil`, `win32file`, `win32gui`, `win32api`, `cv2`, `PIL`,
`pynput`, `keyboard`, `pyautogui`, `mss`, and `pyscreeze`. Pure computation modules
such as `math`, `dataclasses`, and `numpy` remain allowed unless a future plugin
contract explicitly says otherwise.

`check_task_scope.py` reads `Allowed Files` and `Forbidden Files` from
`docs/tasks/<TASK>.md`. Its default diff source is `git diff --cached --name-only`.
When `--base-ref REF` is present, it runs `git diff REF...HEAD` and never falls back to
the staged index if that command fails. The glob converter treats `**/` as zero or more
directories, so `**/*.py` matches both `a.py` and `nested/a.py`.

## CI Defaults

The workflow runs on `windows-latest` and uses uv only for dependencies. It runs
compile, architecture, scope, and headless unit tests. It does not run native Windows
smoke tests, Node frontend tests, real OCR inference, screen capture, or a real
Hearthstone game.

Until R02C moves the legacy cases, `tools/run_headless_tests.py` explicitly excludes:

- `tests.test_surrender_frontend` because it invokes Node.
- `tests.test_hearthstone_liveness.NativeProcessTests.test_can_enumerate_processes`.
- `tests.test_hearthstone_liveness.NativeProcessTests.test_finds_this_python_process`.

The exclusions are visible with:

```powershell
uv run python tools/run_headless_tests.py --list-exclusions
```

## Scope In CI

The gate fails closed. It resolves a task ID from, in order:

1. `workflow_dispatch.inputs.task_id`.
2. A branch name containing an `Rxx` token such as `feature/R02B-ci-gates`.
3. The latest commit subject containing `(Rxx)`.

If none matches, the workflow fails before running the scope check. It does not skip
the scope step.

The scope base is selected without a staged-index fallback:

- pull request: `github.event.pull_request.base.sha`;
- push: `github.event.before` (or `HEAD^` for a zero before SHA);
- workflow dispatch: `HEAD^`.

The resolved base is always passed to `tools/check_task_scope.py --base-ref`.
