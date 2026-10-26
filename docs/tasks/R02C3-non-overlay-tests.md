# R02C3 Non-Overlay Source-String Replacement

## Task ID

R02C3

## Goal

Replace the 11 non-overlay source-string and private-table candidates with fake flow/clock/stdout/CLI behavior tests.

## Allowed Files

- `tests/test_fsm_liveness_stop.py`
- `tests/test_human_like_scope.py`
- `tests/test_start_latency.py`
- `tests/test_web_admin_banner.py`
- `tests/test_web_logs_wiring.py`
- `tests/test_version.py`
- `docs/testing/**`
- `docs/agent/**`
- `docs/tasks/R02C3-*.md`
- `TOTAL_TASK.md`

## Forbidden Files

- Production behavior changes except minimal injectable seams explicitly reviewed.
- Public API/config/log changes.

## Acceptance

- 11 Batch 3 candidates removed only after alternative behavior tests pass.
- No source-string assertions remain in those files.
- Headless suite remains green.

## Git Requirement

```text
test(R02C3): replace non-overlay source-string tests
```

## Report Paths

- Implementer: `docs/agent/reports/R02C3-implementer.md`
- Reviewer: `docs/agent/reviews/R02C3-review.md`
