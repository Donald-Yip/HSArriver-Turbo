# R12 Review — Round 2

Reviewed the prior review, updated implementer report, and `R12-review-package-round2.txt`. I did not modify source, tests, index, HEAD, or branch. Scope was limited to the four prior findings and regressions caused by their fixes.

### Prior Finding Disposition

1. **Turn re-entry and game-generation safety — ADDRESSED.** `src/hslegend/application/use_cases/one_game.py:411-443` now captures the first in-game generation, rejects later generation changes before turn delegation, and keys delegation by `(game_generation, turn_number)`. After `end_turn`/`local_turn_ended`, repeated same-turn snapshots are drained through later revisions rather than delegated again. Tests at `tests/application/test_one_game.py:474-557` cover same-turn/new-revision no redelegation and a higher-revision second generation failing before another turn call. No second-game dispatch remains on this path.

2. **Cancellation priority and clock classification — ADDRESSED.** `src/hslegend/application/use_cases/one_game.py:539-582` centralizes cancellation-first checks across deadline, failure, clock, port, sleeper, and `None` wait paths. Cancelled work wins over lobby/state/sleeper failures; non-finite or failing clocks map to `failed/clock_error`; only a genuinely expired deadline maps to `timed_out`. Focused tests at `tests/application/test_one_game.py:560-722` cover the requested races and early timeout behavior.

3. **Entrypoint architecture allowlist — ADDRESSED.** `tools/check_architecture.py:36-40` and `:191-203` replace the blacklist with an allowlist for the standard library, application, bootstrap, and sibling entrypoints. Self-tests at `:479-527` reject adapter, plugin, domain, and third-party imports. I also checked relative paths directly: `..domain` is rejected, while `.onegame` and `..bootstrap` resolve to allowed targets.

4. **CLI command-id usage error — ADDRESSED.** `src/hslegend/entrypoints/onegame.py:44-49` validates non-empty command IDs and `:104-116` converts command-construction validation failures into argparse usage errors. `tests/test_one_game_e2e.py:124-128` verifies exit 2 and no traceback.

### Regression Checks

- The fake E2E still asserts the real R08-R11 path, one mulligan, one turn/action, exactly five dispatches, one settlement, final `RESULT`, and no post-`end_turn` dispatch.
- Focused verification passed: 23 tests in `tests.application.test_one_game` and `tests.test_one_game_e2e`.
- Architecture self-test passed 20/20, including the new allowlist cases.
- `uv run hslegend-onegame --fake --json` returned a completed result with one turn and one action.

### Residual Issues

- No blocking or regression findings within the requested re-review scope.

### Assessment

**Approved.** The prior critical turn-reentry/generation issue, cancellation and clock classification issue, architecture allowlist issue, and CLI usage-error issue are all addressed with focused regression coverage, and the fake one-game path remains behaviorally sound.
