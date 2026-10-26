# Task Template

## Task ID

Rxx

## Goal

One sentence describing the observable result.

## Scope

- What must change.

## Non-Goals

- What must not change.

## Inputs

- Task book and interface documents.
- Base SHA and branch.

## Outputs

- Files/contracts/tests produced.

## Interfaces

- Consumes:
- Produces:

## Dependencies And Impact

- Depends on:
- Impacted modules:
- Public API impact:

## Allowed Files

- Exact paths.

## Forbidden Files

- Exact paths.

## Acceptance

```powershell
uv sync
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python -m unittest discover -s tests -t .
```

## Risk And Rollback

- Risk:
- Rollback:

## Garbage-Code Check

- In-scope garbage to refactor:
- Out-of-scope discoveries to register:

## Git Requirement

One final squash commit after independent review and CI. Canonical format:

```text
<type>(Rxx): <summary>
```

## Report Paths

- Implementer: `docs/agent/reports/Rxx-implementer.md`
- Reviewer: `docs/agent/reviews/Rxx-review.md`
