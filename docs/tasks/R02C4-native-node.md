# R02C4 Node And Native Test Migration

## Task ID

R02C4

## Goal

Move the Node frontend test and two native process-enumeration tests out of the default suite, with deterministic UTF-8 and no machine-specific paths.

## Allowed Files

- `tests/test_surrender_frontend.py`
- `tests/test_hearthstone_liveness.py`
- `tests/frontend_surrender_roundtrip.js`
- `tests/native/**`
- `tools/run_headless_tests.py`
- `docs/testing/**`
- `docs/agent/**`
- `docs/tasks/R02C4-*.md`
- `TOTAL_TASK.md`

## Forbidden Files

- Production runtime behavior.
- Real Hearthstone behavior.

## Acceptance

- Default suite does not invoke Node or real process enumeration.
- Node test uses UTF-8 and no hardcoded user/node paths.
- Native tests are opt-in and still runnable.
- Headless discovery count remains explicit and documented.

## Git Requirement

```text
test(R02C4): isolate Node and native tests
```

## Report Paths

- Implementer: `docs/agent/reports/R02C4-implementer.md`
- Reviewer: `docs/agent/reviews/R02C4-review.md`
