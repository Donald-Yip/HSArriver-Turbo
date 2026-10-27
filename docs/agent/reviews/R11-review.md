# R11 Review

## Scoped Re-Review (Round 3)

This round is limited to the Round 2 `elapsed_seconds` regression and regressions caused by its fix.

### Round 2 Regression Status

**ADDRESSED.** `src/hslegend/application/use_cases/finish_battle.py:139-148` now handles pre-poll cancellation directly and returns `CANCELLED` with `elapsed_seconds=0.0` before sampling the start clock. It performs no `_now()`, `_result()`, or lobby read on that path.

`tests/application/test_finish_battle.py:199-220` verifies the exact regression case with `FakeClock(now=100.0)`: status `cancelled`, reason `cancelled`, phase `UNKNOWN`, `elapsed_seconds == 0.0`, zero clock calls, and zero lobby reads. The interface documentation at `docs/interfaces.md:111-112` now states the same contract.

### Regression Check

No regression was found from the fix. The change is limited to the pre-poll cancellation branch and its test/documentation. The subsequent start-clock and bounded-polling flow is unchanged; a cancellation observed after the start clock still reports elapsed time through the normal path. The focused finish-battle suite passed locally: 19 tests, `OK`.

The earlier timeout classification, cancellation precedence, sleeper failure, observation-only boundary, and bootstrap/export findings remain addressed.

### Final Assessment

**Approved.** The pre-poll cancellation elapsed-time regression is fixed, behavior is covered by a focused test, and the scoped review found no regression from the correction.
