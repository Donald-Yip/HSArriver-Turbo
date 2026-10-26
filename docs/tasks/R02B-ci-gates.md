# R02B Architecture, Scope, CI And PR Gates

## Task ID

R02B

## Goal

Add enforceable architecture/scope gates and Windows headless CI before test deletion.

## Scope

- Add `tools/check_architecture.py` using standard-library AST.
- Add `tools/check_task_scope.py` driven by task books.
- Add `.github/workflows/ci.yml`.
- Add PR template and required-check documentation.
- Ensure CI uses uv only and default tests are headless.

## Non-Goals

- No test rationalization/deletion.
- No production domain/application/adapters implementation.

## Inputs

- R02A inventory.
- `docs/architecture/dependency-rules.md`
- `docs/agent/TEST_STRATEGY.md`
- `pyproject.toml`, `uv.lock`.

## Outputs

- Architecture and scope tools.
- CI workflow and PR template.
- Updated interface/CI docs.

## Dependencies And Impact

- Depends on: R02A.
- Impacted modules: tools, CI, documentation.
- Public API impact: none.

## Allowed Files

- `tools/**`
- `.github/**`
- `docs/testing/**`
- `docs/tasks/R02B-*.md`
- `docs/agent/**`
- `TOTAL_TASK.md`
- `README.md`
- `docs/GIT.md`

## Forbidden Files

- `tests/**`
- Production runtime modules.

## Acceptance

- Architecture checker returns 0 for current compliant tree and nonzero for a fixture violation.
- Scope checker validates changed files against a task book.
- CI YAML parses and uses uv only.
- PR template includes task, scope, interfaces, tests, risk, rollback, and checklist.

## Risk And Rollback

- Risk: CI/runtime platform mismatch.
- Rollback: revert R02B; R02A inventory remains valid.

## Git Requirement

```text
ci(R02B): add architecture, scope, and Windows CI gates
```

## Report Paths

- Implementer: `docs/agent/reports/R02B-implementer.md`
- Reviewer: `docs/agent/reviews/R02B-review.md`
