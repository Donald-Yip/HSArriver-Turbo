# R15 Implementer Report

## Status

IMPLEMENTED / UI RUNNING / READY FOR REVIEW

## Root Cause

The full Web UI existed and retained the pre-refactor feature set. On Chinese Windows, a direct or scheduled-task launch could crash before serving because Python's stdout still used GBK and the non-admin warning contains a Unicode warning symbol.

## Fix

- Reconfigure Web UI stdout/stderr to UTF-8 with replacement errors before any warning is printed.
- Force `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8` for `hsctl serve start` child processes.
- Apply the same environment to elevated launch and the highest-privilege scheduled-task launcher.
- Regenerated the ignored `log/hs_serve_elevated.cmd`.
- Preserved all existing UI logic, controls, endpoints and automation behavior.

## Changed Files

- `web_ui.py`
- `hsctl.py`
- `tests/test_hsctl.py`
- `docs/interfaces.md`
- `docs/agent/reports/R15-implementer.md`

## Live UI Evidence

- URL: `http://127.0.0.1:8765`
- Server status: running, `is_admin: true`
- Environment self-check: 21 passed, 2 warnings
- Full UI rendered and accessible in browser.
- Visible controls include run overview, self-check, configuration, battle control, stop-after-game/immediate stop, schedule, card-deck surrender, rank stop, human-like behavior, delays, calibration, live logs and version information.
- Warnings are the expected non-critical ones: Hearthstone currently obscured and DPI 150%; coordinates remain correctly mapped.

## Verification

- Focused UTF-8 launcher tests: 2/2 passed.
- `compileall web_ui.py hsctl.py`: passed.
- Full headless/discover/architecture/scope/opt-in gates are run by the controller before commit.
- Task Scheduler `HSLegendArriver_serve` last result: 0.
- `/api/status` returned HTTP 200 and confirmed the complete control surface.

## Risks

- UTF-8 console output may render differently in legacy consoles; browser UI is unaffected.
- The scheduled task must remain pointed at the regenerated ignored launcher.
- No automation logic or UI feature was changed.

## Rollback

Revert the R15 code commit and run `uv run python web_ui.py --no-browser` with UTF-8 environment as a temporary fallback. All pre-refactor UI logic remains available.
