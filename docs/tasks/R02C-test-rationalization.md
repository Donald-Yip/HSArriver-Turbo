# R02C Headless Test Rationalization

## Task ID

R02C

## Goal

Reduce the legacy suite to a smaller, behavior-driven headless baseline using the approved R02A inventory.

## Scope

- Delete or rewrite approved garbage/duplicate tests.
- Move native-only tests to an explicit opt-in suite.
- Fix deterministic headless failures.
- Ensure every deleted behavior has a passing replacement or approved non-behavioral rationale.
- Keep one final squash commit for R02C.

## Non-Goals

- No production feature changes.
- No CI architecture changes beyond R02B.

## Inputs

- R02A inventory and rationalization plan.
- R02B gates.
- Current `tests/` tree.

## Outputs

- Reduced default headless suite.
- Opt-in native suite.
- Updated inventory decisions and replacement evidence.

## Dependencies And Impact

- Depends on: R02B.
- Impacted modules: tests and testing docs.
- Public API impact: none.

## Allowed Files

- `tests/**`
- `docs/testing/**`
- `docs/tasks/R02C-*.md`
- `docs/agent/**`
- `TOTAL_TASK.md`
- `pyproject.toml`
- `uv.lock`

## Forbidden Files

- Production runtime modules.
- Public API, config, coordinates, delays, or click sequences.

## Acceptance

- Default headless suite: 0 failures, 0 errors.
- No real Windows GUI, mouse, game, OCR model, network, or admin dependency in default suite.
- No source-string implementation tests remain unless explicitly approved as generated-artifact checks.
- Every deletion maps to a passing replacement or approved duplicate decision.
- CI headless gate passes.

## Risk And Rollback

- Risk: accidental coverage loss.
- Rollback: restore R02C test changes and keep R02B gate; inventory provides traceability.

## Git Requirement

```text
test(R02C): rationalize and headless the legacy test suite
```

## Report Paths

- Implementer: `docs/agent/reports/R02C-implementer.md`
- Reviewer: `docs/agent/reviews/R02C-review.md`
