# R02C5 Private-Patch Rewrite And Headless Finalization

## Task ID

R02C5

## Goal

Rewrite the remaining five private-patch tests, remove temporary headless exclusions that are no longer needed, and establish the final zero-failure headless baseline.

## Allowed Files

- `tests/test_friendly_hand_target_battlecries.py`
- `tests/test_hand_or_board_targets.py`
- `tests/test_web_selfcheck_api.py`
- `tools/run_headless_tests.py`
- `docs/testing/**`
- `docs/agent/**`
- `docs/tasks/R02C5-*.md`
- `TOTAL_TASK.md`

## Forbidden Files

- Production APIs/config/log/output.
- Unrelated test cleanup.

## Acceptance

- Five private-patch rewrites use injected public seams or fakes.
- Headless suite: 0 failures, 0 errors, explicit skips only.
- No default test requires Node, native process inspection, real OCR, GUI, admin, or game.
- Inventory decisions match final file state.

## Git Requirement

```text
test(R02C5): finalize deterministic headless baseline
```

## Report Paths

- Implementer: `docs/agent/reports/R02C5-implementer.md`
- Reviewer: `docs/agent/reviews/R02C5-review.md`
