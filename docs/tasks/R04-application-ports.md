# R04 Application Ports And Runtime Controls

## Task ID

R04

## Goal

Define the minimum application-layer contracts and deterministic runtime controls needed by later planning and execution tasks.

## Scope

- Add `src/hslegend/application/ports/**` protocol contracts.
- Add application command/result DTOs.
- Add `StopToken`, `ActionGate`, and `RunContext`.
- Add test fakes under `tests/application/**`.
- Add tests for cancellation, lease mutual exclusion, DTO serialization, and port contract behavior.

## Non-Goals

- No concrete adapters.
- No power-log/OCR/click implementation.
- No use-case workflows yet.
- No plugin registry or executor yet.

## Allowed Files

- `src/hslegend/application/**`
- `tests/application/**`
- `docs/interfaces.md`
- `docs/architecture/**`
- `docs/tasks/R04-*.md`
- `docs/agent/**`
- `TOTAL_TASK.md`

## Forbidden Files

- Legacy root modules.
- `src/hslegend/domain/**` except pure bug fixes with reviewer approval.
- `src/hslegend/adapters/**`, `bootstrap/**`, `plugins/**`.

## Acceptance

- Application imports only domain, stdlib, and application modules.
- StopToken cancellation is idempotent and wakes waiters.
- ActionGate enforces one lease and rejects wrong-owner release.
- All port protocols use domain/application types, not legacy objects.
- Fakes are deterministic and headless.
- Domain/headless/architecture/scope gates remain green.

## Risk And Rollback

- Risk: ports may over-specify before adapters exist.
- Mitigation: only include operations required by the MVP one-game flow.
- Rollback: delete the application package and tests, revert `docs/interfaces.md`, and remove the R04 implementer report; domain remains unchanged.

## Git Requirement

```text
feat(R04): define application ports and runtime controls
```

## Report Paths

- Implementer: `docs/agent/reports/R04-implementer.md`
- Reviewer: `docs/agent/reviews/R04-review.md`
