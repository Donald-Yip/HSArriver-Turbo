# R07 Implementer Report

## Status

READY_FOR_REVIEW

## Branch And Base

- Branch: `feature/R07-external-adapters`
- Base SHA: `25a0ce1`
- Head SHA: `25a0ce1` (no commit created)
- Commit/push: not performed

## Changed Files

- `src/hslegend/adapters/hsang/__init__.py`
- `src/hslegend/adapters/powerlog/__init__.py`
- `src/hslegend/adapters/lobby/__init__.py`
- `src/hslegend/adapters/input/__init__.py`
- `tests/adapters/test_external_adapters.py`
- `docs/interfaces.md`
- `docs/tasks/R07-external-adapters.md`
- `docs/agent/reports/R07-implementer.md`
- `docs/agent/HANDOFF.md`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/TASK_BOARD.json`
- `TOTAL_TASK.md`

No legacy runtime, domain, application, plugin, coordinate, delay, OCR model, or
user configuration files were changed by this implementation.

## Fix Round 1

- PowerLog wait now captures the deadline at method entry. It rechecks cancellation
  and deadline after every revision read and after every current snapshot read.
  If cancellation occurs during a read, any newly observed result is suppressed.
- Hsang now revalidates parser-returned `RecommendationObservation` values. Empty
  instruction, evidence ID, or snapshot ID values are rejected; the mode must match;
  and confidence must remain finite and within `[0, 1]`.
- Tests cover slow revision/current reads, cancellation during revision/current
  reads, and malformed parser-returned observations.

## Interfaces Produced

- `HsangRecommendationAdapter`: injected `capture`, `read`, and legacy-compatible
  `parse(ocr, turn_number, log_revision)` callables produce a validated
  `RecommendationObservation`.
- `PowerLogGameStateAdapter`: injected snapshot/revision providers produce
  `GameObservation`; revision wait supports timeout and optional stop-token
  cancellation.
- `LegacyLobbyStateAdapter`: injected recognizer maps deterministic aliases to
  `LobbyState`; unknown aliases return `LobbyPhase.UNKNOWN` with confidence `0.0`.
- `LegacyActionDispatch`: semantic handler mapping routes `PlanStep` values,
  rejects unknown action names before invoking handlers, and converts handler
  exceptions to failed `DispatchResult` values.
- Typed adapter error families are exported by each adapter package.

## Interfaces Consumed

- `RecommendationSourcePort`
- `GameStateSourcePort`
- `LobbyStatePort`
- `ActionDispatchPort`
- `RecommendationObservation`, `GameObservation`, `LobbyState`, `DispatchResult`
- `GameSnapshot`, `PlanStep`, `ActionIntent`

All adapters are injection-only. Real legacy factory methods were intentionally
not added because importing root legacy modules from the new architecture is
forbidden; constructors accept legacy callables at the composition boundary.

## Verification

| Command | Exit | Result |
| --- | ---: | --- |
| `uv sync --locked --all-groups` | 0 | Resolved 244 packages; audited 77 packages |
| `uv run python -m unittest tests.adapters.test_external_adapters -v` | 0 | 28 tests passed |
| `uv run python -m unittest discover -s tests/adapters -t . -p "test_*.py" -v` | 0 | 50 tests passed |
| `uv run python -m unittest discover -s tests/domain -t . -p "test_*.py" -v` | 0 | 38 tests passed |
| `uv run python -m unittest discover -s tests/application -t . -p "test_*.py" -v` | 0 | 44 tests passed |
| `uv run python -m unittest discover -s tests/plugins -t . -p "test_*.py" -v` | 0 | 9 tests passed |
| `uv run python -m compileall -q src tests tools` | 0 | No diagnostics |
| `uv run python tools/check_architecture.py` | 0 | `[architecture] PASS: 30 files checked` |
| `uv run python tools/check_architecture.py --self-test` | 0 | `PASS: 16/16 cases` |
| `uv run python tools/check_task_scope.py --task R07` | 0 | `[scope] PASS: R07 (12 changed files)` |
| `uv run python tools/check_task_scope.py --self-test` | 0 | `PASS: 8/8 cases` |
| `uv run python tools/run_headless_tests.py` | 0 | run=1525 passed=1524 failures=0 errors=0 skipped=1 |

## Scope Check

- The staged task file set is limited to R07 adapter, test, interface, task, and
  agent-state files; `check_task_scope.py --task R07` reports 12 changed files.
- No commit or push was performed.

## New Dependencies And Public API

- New runtime dependencies: none.
- New public adapter classes and typed errors listed above.
- `docs/interfaces.md` documents the four adapter seams and their callable
  contracts.

## Known Risks And Review Focus

- `HsangRecommendationAdapter` uses `min(ocr_confidence, semantic_confidence)` when
  the parser provides no single confidence; when both are absent it falls back to
  read confidence. Confirm this conservative normalization is suitable for R08.
- `PowerLogGameStateAdapter` treats `None` from revision wait as either timeout or
  cancellation, matching the existing port contract. Confirm polling cadence and
  optional stop-token behavior are sufficient for R08 wiring.
- `LegacyLobbyStateAdapter` recognizes a fixed deterministic alias set and fails
  closed for all other strings.
- `LegacyActionDispatch` treats a `None` handler return as legacy success. Confirm
  that all future injected legacy handlers either return their click exception or
  `None` only after successful dispatch.
