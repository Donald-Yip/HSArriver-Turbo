# R09 Independent Review — Scoped Re-review

## Verdict

**APPROVED**

All prior blocking findings are addressed in the staged tree. The mulligan use case remains within the intended architecture, and the newly added failure-path metadata is carried through immutable records rather than mutable executor state. Remaining items are non-blocking documentation/cleanup notes.

## Findings

### Critical

None.

### Important

None.

### Minor, non-blocking

1. `docs/interfaces.md:210` still says a journal exception “propagates unchanged,” while `src/hslegend/application/planning/executor.py:138-155` now wraps non-ledger journal failures in `PlanExecutorError`. Update the sentence before commit for contract accuracy.
2. `docs/tasks/R09-mulligan.md:47-58` still shows the pre-rework `MulliganResult` shape and omits the additive journal metadata fields implemented at `src/hslegend/application/use_cases/mulligan.py:104-108`. The broader `docs/interfaces.md:63` is updated, so this is documentation consistency only.
3. `src/hslegend/application/use_cases/mulligan.py:7,46` retains unused `time`/`Clock` definitions. Harmless cleanup if touched again.

No required functional correction is identified.

## Prior Findings Resolution

### Confirmation postcondition enforcement

Resolved. `src/hslegend/application/planning/executor.py:251-266` now fails closed with `confirmation_postcondition_required` before dispatch when `confirmation_required=True` and the final step/plan has no confirming condition. The mulligan plan sets the flag at `src/hslegend/application/use_cases/mulligan.py:235-244`, and `tests/application/test_planning.py:225-246` proves zero dispatch for the missing-postcondition case.

### Release failure on exception paths

Resolved. `src/hslegend/application/planning/executor.py:138-155` captures the primary exception, releases the gate in `finally`, attaches `release_error` to ledger errors, and wraps other executor failures in `PlanExecutorError`. `src/hslegend/application/use_cases/mulligan.py:445-480` maps that release evidence into typed results. Both paths are covered by `tests/application/test_mulligan.py:447-530`.

### Scope approval

Resolved. `docs/tasks/R09-mulligan.md:101-104` records the controller’s explicit approval for `application/dto.py` and the R14 plugin-registry follow-up. The focused scope gate passes with 13 staged files.

### Journal metadata and no record-ID fallback

Resolved. `ExecutionRecord` exposes `journal_record_id`, `journal_durable`, `journal_duplicate`, `journal_evidence_ids`, and `release_error` at `src/hslegend/application/dto.py:227-235`. `MulliganUseCase` now returns the actual journal ID without falling back to the execution record ID (`src/hslegend/application/use_cases/mulligan.py:490-505`).

### Empty and ambiguous instructions

Resolved in the suite at `tests/application/test_mulligan.py:374-375`; both are asserted as typed rejections with no dispatch.

### Plugin/registry conflict

Not a blocker for R09. `src/hslegend/plugins/registry.py:51-54` still maps mulligan families to the generic one-step `MulliganPlugin` (`src/hslegend/plugins/defaults.py:52-61`), but R09 does not route through `PlanBuilder`/the default registry. The required R14 reconciliation is recorded at `docs/tasks/R09-mulligan.md:104`.

## Spec Coverage

1. Instruction parsing and legacy 3/5-card bounds: PASS.
2. Stale snapshot/recommendation, wrong mode, missing surface, empty/ambiguous input, gate contention, and cancellation dispatch nothing: PASS.
3. Keep-all dispatches only confirm; replacements are sorted and followed by confirm: PASS.
4. Non-waiting replaces do not wait; final confirmation waits once; confirmation postconditions cannot be omitted when required: PASS.
5. `executed_unconfirmed` remains terminal and immediate replay is ledger-blocked: PASS.
6. ExecutionRecord metadata is immutable and returned without mutable executor side channels: PASS.
7. Minimal use-case dependencies and one consistent bootstrap executor: PASS.
8. Journal/evidence semantics and release-failure handling: PASS for the reviewed paths.
9. Declarative action types and adapter/application/domain boundaries: PASS.
10. Existing plugin conflict is a documented R14 integration concern, not an R09 blocker: PASS.

## Architecture And Dependency Check

PASS. The use case imports application ports/planning contracts and domain declarations only. No adapter, legacy root, OCR, Win32, Power.log, Web/CLI/MCP, or coordinate dependency was introduced. Architecture: 36 files checked, self-test 16/16.

## Test Adequacy

PASS. Coverage now includes the mandatory confirmation failure, no intermediate revision wait, release failures on ledger and journal exception paths, empty/mixed instructions, sorted replacement dispatch, journal evidence, and no automatic replay.

## Verification Evidence

All commands used `uv`:

- `uv sync --locked --all-groups`: exit 0.
- `uv run python -m unittest tests.application.test_mulligan tests.application.test_planning tests.application.test_dto`: exit 0; 47 tests passed.
- `uv run python tools/run_headless_tests.py`: exit 0; `run=1495 passed=1495 failures=0 errors=0 skipped=0`.
- `uv run python -m unittest discover -s tests -t .`: exit 0; 1675 tests passed.
- `uv run python tools/run_headless_tests.py --opt-in`: exit 0; `run=19 passed=19 failures=0 errors=0 skipped=0`.
- `uv run python -m compileall -q src tests tools`: exit 0.
- `uv run python tools/check_architecture.py`: PASS, 36 files checked.
- `uv run python tools/check_architecture.py --self-test`: PASS, 16/16.
- `uv run python tools/check_task_scope.py --task R09`: PASS, 13 changed files.
- `uv run python tools/check_task_scope.py --self-test`: PASS, 8/8.

Self-check still reports the machine-environment blockers outside this task: no administrator elevation and no configured Hearthstone Logs directory. Full discover reports two pre-existing `log/calibrate_roi.log` ResourceWarnings but passes.

## Scope Drift Check

PASS. The staged task book allows the changed implementation/test/docs files, and the DTO expansion has controller approval. No production file outside the amended R09 packet changed.

## Rollback Adequacy

PASS. The slice remains additive and isolated; reverting R09 leaves R08 intact. The plugin-registry reconciliation is explicitly deferred to R14.
