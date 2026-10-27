# HANDOFF

## Current Phase

R15 UI parity is now the active priority: keep the complete pre-refactor Web UI and make its startup reliable on Chinese Windows and through the existing highest-privilege scheduled task.

## Completed

- R12 fake one-game flow and R13A native adapter bridge are approved.
- R13 Phase A live gate/preflight and focus-before-live are committed.
- The existing `web_ui.py` + `web/index.html` remain the full-feature UI.
- Root startup defect found: `web_ui.py` crashes on Chinese Windows because the admin warning uses characters not encodable by GBK stdout.

## Current Task

- Task book: `docs/tasks/R15-ui-parity.md`.
- Branch: `feature/R15-ui-parity`.
- Force UTF-8 for Web UI stdio, `hsctl serve start`, elevated launch and the scheduled-task launcher.
- Regenerate the ignored launcher and verify `/api/status`.
- Preserve every existing UI control and workflow.

## Exact Next Action

1. Implement UTF-8-safe startup without changing UI features.
2. Start the full Web UI through the highest-privilege task.
3. Verify the browser UI and `/api/status`.
4. Run tests, independent review and one R15 commit.
