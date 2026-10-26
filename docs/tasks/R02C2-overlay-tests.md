# R02C2 Overlay Source-String Test Replacement

## Task ID

R02C2

## Goal

Replace overlay source-string tests with observable fake-widget/callback behavior and delete the 19 approved Batch 2 candidates.

## Allowed Files

- `tests/test_overlay_*.py`
- `tests/test_region_overlay_button.py`
- `tests/helpers/**` if introduced
- `docs/testing/**`
- `docs/agent/**`
- `docs/tasks/R02C2-*.md`
- `TOTAL_TASK.md`

## Forbidden Files

- Production overlay code unless a minimal test seam is necessary and explicitly reviewed.
- Face/API/config/log behavior changes.

## Acceptance

- 19 Batch 2 source-string candidates removed only after equivalent widget/callback tests pass.
- No `inspect.getsource` or source-text assertions remain in those files.
- Headless suite remains green.

## Risk And Rollback

- Risk: losing user-visible layout/copy coverage.
- Rollback: restore replaced tests and test seam.

## Git Requirement

```text
test(R02C2): replace overlay source-string tests with behavior
```

## Report Paths

- Implementer: `docs/agent/reports/R02C2-implementer.md`
- Reviewer: `docs/agent/reviews/R02C2-review.md`
