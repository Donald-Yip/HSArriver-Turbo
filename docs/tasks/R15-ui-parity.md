# R15 Web UI Parity And Startup Reliability

## Task ID

R15

## Goal

Keep the existing full-featured HSLegendArriver Web control UI as the user's
primary operating surface, preserve its pre-refactor behavior, and make startup
reliable on Chinese Windows and through the existing elevated scheduled task.

## Scope

- Preserve the current `web_ui.py` + `web/index.html` feature set and HTTP/CLI
  contracts.
- Fix the UTF-8 startup failure that crashes on the non-admin warning under GBK.
- Force UTF-8 for the web console child process from `hsctl serve start`,
  elevated launch, and the scheduled-task launcher.
- Regenerate/verify the ignored task launcher and ensure `serve start --via-task`
  can start the complete Web UI at highest privilege.
- Verify the UI is reachable at `/api/status` and the main controls remain
  available through the existing UI.
- Add focused regression tests for stdio configuration and launcher environment.
- Keep the native/refactored one-game paths additive and out of the default UI
  workflow until explicitly integrated later.

## Non-Goals

- No new Web framework, dependency, frontend redesign, or changed user workflow.
- No removal of existing controls, API endpoints, scheduled tasks, overlay,
  manual control, CLI, MCP, or automation behavior.
- No change to coordinates, delays, thresholds, card logic, config schema,
  Power.log parsing, or click behavior.
- No integration of the new one-game architecture into the Web UI in this task.
- No direct push or production deployment.

## Inputs

- Existing `web_ui.py`, `web/index.html`, `hsctl.py`.
- Current UI behavior and tests.
- Existing `HSLegendArriver_serve` scheduled task.
- Chinese Windows console encoding constraints.

## Outputs

- UTF-8-safe Web UI startup.
- UTF-8-safe direct, elevated, and scheduled-task launch paths.
- Regenerated ignored `log/hs_serve_elevated.cmd`.
- Regression tests and UI smoke evidence.
- Updated interface/handoff/task records.

## Interfaces

- `python web_ui.py [--no-browser]` behavior remains compatible.
- `uv run python hsctl.py serve start --browser` remains the primary start command.
- `uv run python hsctl.py serve start --via-task` uses the existing highest-
  privilege task and must launch one Web UI instance.
- `/api/status`, existing control endpoints, and `web/index.html` remain the
  operating interface.

## Allowed Files

- `web_ui.py`
- `hsctl.py`
- `web/index.html` only if a user-visible startup/error message must be corrected
- `tests/test_hsctl.py`
- `tests/opt_in/test_web_ui_behaviors.py`
- `tests/opt_in/test_environment.py` only for encoding regression support
- `tools/run_headless_tests.py` only if test discovery must be corrected
- `docs/interfaces.md`
- `docs/tasks/R15-ui-parity.md`
- `docs/agent/reports/R15-*.md`
- `docs/agent/reviews/R15-*.md`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/TASK_BOARD.json`
- `docs/agent/HANDOFF.md`
- `TOTAL_TASK.md`
- `README.md`

## Forbidden Files

- `FSM_action.py`, `manual_controller.py`, `src/hslegend/**`, domain/application/
  adapter logic, `pyproject.toml`, `uv.lock`, CI workflows, coordinates/delays,
  cards.json, config data, logs, and screenshots.
- Any change that reduces the existing UI feature set or changes its workflow.

## Acceptance

```powershell
uv lock --check
uv sync --locked --all-groups
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/check_architecture.py --self-test
uv run python tools/check_task_scope.py --task R15 --json
uv run python tools/check_task_scope.py --self-test
uv run python tools/run_headless_tests.py
uv run python -m unittest discover -s tests -t .
uv run python tools/run_headless_tests.py --opt-in
git diff --cached --check
```

Behavioral acceptance:

- No GBK/UnicodeEncodeError during startup on the Chinese Windows environment.
- Non-admin UI starts cleanly and displays the existing admin warning.
- Highest-privilege scheduled-task startup launches exactly one UI instance.
- `/api/status` returns HTTP 200 and the browser opens the existing UI.
- Existing interface options and buttons remain present and callable.

## Risk And Rollback

- Risk: changing stdio encoding affects console logs.
- Control: use UTF-8 with replacement errors and deterministic tests.
- Risk: stale scheduled-task launcher path.
- Control: regenerate the ignored launcher and verify task action/result.
- Risk: duplicate UI servers.
- Control: retain the existing idempotent probe/port/PID behavior.
- Rollback: revert the R15 commit; the UI and automation logic otherwise remain
  unchanged.

## Git Requirement

```text
fix(R15): restore reliable full-feature web UI startup
```

## Report Paths

- Implementer: `docs/agent/reports/R15-implementer.md`
- Reviewer: `docs/agent/reviews/R15-review.md`