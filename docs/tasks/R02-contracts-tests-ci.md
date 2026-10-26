# R02 Contract, Golden Replay, CI And Test Rationalization

## Task ID

R02

## Goal

Freeze the compatibility surface, establish headless golden replay and CI gates, and reduce the legacy test suite to behavior-driven tests.

## Scope

- Inventory and classify all existing tests.
- Preserve behavior and contract coverage; remove junk or duplicate tests.
- Create `docs/testing/TEST_INVENTORY.md`.
- Add golden Power.log, OCR, and frame fixtures.
- Add architecture and task-scope checks.
- Add `.github/workflows/ci.yml`, PR template, and required check documentation.
- Make the default test suite headless.

## Non-Goals

- No production architecture implementation.
- No game logic changes.
- No native test automation beyond opt-in scaffolding.

## Inputs

- `docs/agent/TEST_STRATEGY.md`
- `docs/interfaces.md`
- Existing `tests/` tree.
- Existing 41 API, 34 CLI, and 21 MCP contracts.

## Outputs

- Test inventory and cleanup decisions.
- Replacement/contract/golden tests.
- `tools/check_architecture.py`.
- `tools/check_task_scope.py`.
- CI and PR templates.
- A deterministic default test command.

## Interfaces

- Consumes: current public behavior and repository contracts.
- Produces: regression gate used by all later tasks.

## Dependencies And Impact

- Depends on: R01.
- Impacted modules: tests, tools, CI, documentation.
- Public API impact: none.

## Allowed Files

- `tests/**`
- `tools/**`
- `.github/**`
- `docs/testing/**`
- `docs/interfaces.md`
- `docs/tasks/R02-*.md`
- `README.md`
- `SKILL.md`
- `AGENTS.md`

## Forbidden Files

- Production modules under `src/hslegend/**` (not created yet).
- Legacy behavior changes.
- Coordinates, thresholds, delays, templates, or click sequences.

## Acceptance

```powershell
uv sync --locked --all-groups
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/check_task_scope.py --task R02
uv run python -m unittest discover -s tests -t .
```

- Default suite has 0 failures and 0 errors.
- No default test requires native Windows GUI, real game, OCR inference, admin, or network.
- Every removed test maps to a passing replacement test or an explicit non-behavioral duplicate decision approved by the Controller.
- CI uses uv only.

## Risk And Rollback

- Risk: deleting a test that protects a real behavior.
- Rollback: restore tests from the R02 commit and keep the previous CI gate.
- Every deletion must have evidence in the inventory.

## Garbage-Code Check

- Delete source-string and duplicated-table tests.
- Register any unrelated production garbage as a follow-up task.

## Git Requirement

```text
test(R02): rationalize tests and add headless contract gates
```

## Report Paths

- Implementer: `docs/agent/reports/R02-implementer.md`
- Reviewer: `docs/agent/reviews/R02-review.md`

> Superseded by R02A, R02B, and R02C. This umbrella task is retained for traceability only.
