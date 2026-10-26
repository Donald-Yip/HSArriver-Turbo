# R00 Governance And Branch Baseline

## Task ID

R00

## Goal

Create a durable controller operating system for the refactor so that work survives context resets and cannot drift silently.

## Scope

- Save the approved plan and layered architecture.
- Create `TOTAL_TASK.md`, `CORE_PHILOSOPHY.md`, `docs/interfaces.md`.
- Create handbook, task board, handoff, decisions, reports, and reviews directories.
- Normalize remotes and establish `develop` plus `feature/R00-governance`.
- Document the uv-only, PR, CI, review, and one-commit-per-task rules.

## Non-Goals

- No production code changes.
- No uv lock generation or dependency migration.
- No CI workflow implementation.
- No branch protection changes.
- No push or PR creation.

## Inputs

- Approved consolidated refactor plan.
- Current repository state at the R00 base commit.

## Outputs

- Governance and architecture documents.
- Machine-readable and human-readable task board.
- Initial handoff and decision records.
- This task book.

## Interfaces

- Consumes: existing repository conventions and approved refactor plan.
- Produces: durable task/state/handoff contracts used by all later tasks.

## Dependencies And Impact

- Depends on: none.
- Impacted modules: documentation and agent workflow only.
- Public API impact: none.

## Allowed Files

- `.gitignore`\n- `CORE_PHILOSOPHY.md`
- `TOTAL_TASK.md`
- `docs/interfaces.md`
- `docs/plans/*`
- `docs/architecture/*`
- `docs/tasks/*`
- `docs/agent/*`

## Forbidden Files

- All production `.py` files.
- `.github/*`
- Configuration and test fixtures.
- Git remotes after initialization except local remote naming.

## Acceptance

- All governance files exist and contain the required fields.
- Task board is valid JSON and matches the current branch.
- Handoff contains the base SHA, the pending-commit policy, current task, and next action. The final commit must be discoverable by `git log --grep "(R00)"`.
- No production files changed.
- `git diff --cached --check` passes after staging the R00 files.

## Risk And Rollback

- Risk: low; documentation only.
- Rollback: revert the R00 commit; no runtime or user data impact.

## Garbage-Code Check

- In-scope: remove contradictory or duplicate governance wording when found.
- Out-of-scope: record legacy code duplication for a later task.

## Git Requirement

```text
chore(R00): establish agent workflow and refactor baseline
```

## Report Paths

- Implementer: `docs/agent/reports/R00-implementer.md`
- Reviewer: `docs/agent/reviews/R00-review.md`
