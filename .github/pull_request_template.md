## Task ID

<!-- Example: R02B -->

## Task Book

<!-- Path under docs/tasks/ and link to the reviewed task. -->

## Why

<!-- User-visible or architectural problem, root cause, and expected outcome. -->

## Scope

<!-- Summarize allowed files and call out anything intentionally not changed. -->

## Interfaces

<!-- Ports, commands, schemas, HTTP/CLI/MCP contracts, or "none". -->

## Tests (Raw Results)

<!-- List each command, exit code, and pass/fail/skip counts. Do not paste only "tests pass". -->

```text
command:
exit code:
result:
```

## Architecture Impact

<!-- Dependency direction, layers, import cycles, and why no new violation exists. -->

## Breaking Change

<!-- Yes/No. If yes, include migration or compatibility details. -->

## Risk / Rollback

<!-- Main risks, monitoring, and the exact revert/rollback path. -->

## Checklist

- [ ] Task ID and task book are present.
- [ ] Branch is based on the approved base commit.
- [ ] `uv sync --locked --all-groups` succeeds.
- [ ] `uv run python -m compileall -q src tests tools` succeeds.
- [ ] `uv run python tools/check_architecture.py` succeeds.
- [ ] `uv run python tools/check_task_scope.py --task <TASK>` succeeds.
- [ ] Headless tests ran; native/Node/OCR/real-game tests were not used as default CI evidence.
- [ ] No production coordinates, delays, thresholds, or click sequences changed unless explicitly in scope.
- [ ] No tests under `tests/**` changed unless the task book allows it.
- [ ] Report, review, risk, and rollback are documented.
- [ ] No commit/push/merge is included unless authorized by the controller.
