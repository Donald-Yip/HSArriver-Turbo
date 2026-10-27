# R10 Independent Review

## Verdict

**APPROVED — final staged tree confirmed**

Scoped re-review round 2, including the final non-blocking cleanup. The staged R10 implementation satisfies the task book. No critical, important, or minor blocking findings remain.

## Final Cleanup Confirmation

- `_FRIENDLY_SIDES` was removed from `src/hslegend/application/use_cases/turn_intent.py`.
- Weapon and hero target forms now require textual slot `1`; out-of-range singleton slots fail with `target_slot_invalid` (`turn_intent.py:193-210`).
- The out-of-range weapon regression is present in `tests/application/test_turn_intent.py:141-146`.
- The implementer report now consistently records discovery as `1704/1704`.

## Prior Findings Resolution

### C1. Staged implementation and scope evidence: resolved

- The staged index is non-empty: 16 changed files.
- `uv run python tools/check_task_scope.py --task R10` returns `[scope] PASS: R10 (16 changed files, source: git diff --cached)`.
- All staged files are within the R10 allowed set; no adapter, legacy/root production, Web/CLI/MCP, coordinate, configuration, tool, or dependency file is included.

### I1. Stale post-action reconciliation: resolved

`src/hslegend/application/use_cases/turn.py:454-471` rejects a game-generation change and requires `after.log_revision > fresh.log_revision` before another loop iteration. `tests/application/test_turn.py:523-541` confirms exactly one dispatch and one journal record when state remains stale.

### I2. Target kinds and ownership: resolved

`turn_intent.py:17-18` captures the declared target kind. `turn_intent.py:173-270` resolves hero/weapon/board targets from their asserted zones, validates side/zone ownership for indexed collections, and rejects out-of-range singleton slots. Weapon, hero, missing-target, and malformed-ownership regressions are present.

### I3. Cancellation after confirmed `end_turn`: resolved

`turn.py:423-431` checks the stop token before the `end_turn` terminal branch. `tests/application/test_turn.py:356-380` verifies `cancelled`, `ended_turn=False`, one dispatch, and one journal record.

### I4. Default-plugin routing: resolved

`use_location` carries `source.card_id` (`turn_intent.py:161`), and `tests/application/test_turn.py:543-585` exercises attack, hero power, and location through the real default registry. Every action still goes through `PlanBuilder`/`PlanExecutor`; no card-specific loop branching was introduced.

### I5. Two-phase targeting documentation: resolved

`docs/tasks/R10-turn-loop.md:34` and `docs/interfaces.md:286` explicitly defer target-only/two-phase recommendations to R13/R14. R10 safely requires action+target in one recommendation, preserving one plan/one confirmation per action.

## Spec Coverage

- Exact MVP lines, supported card/type forms, destination, unknown/multiple lines, duplicate targets, singleton target slots, and ambiguous `zone_pos`: pass.
- One-based text slots and zero-based `choice_index`/`destination`: pass.
- Deterministic `(zone_pos, entity_id)` ordering and malformed side/zone rejection: pass.
- Target-only/two-phase recommendation: documented R13/R14 integration limitation, not an R10 blocker.
- Every action through `PlanBuilder` and default plugins: pass.
- Stop conditions: end turn, local turn loss, max actions, total deadline, cancellation, gate contention, rejection, failure, unconfirmed, duplicate replay, stale post-action state, and generation change: pass.
- Journaling: one append per completed attempt; turn results retain per-action records and deduplicated evidence.
- Release errors: preserved in the separate `release_error` field, not mixed into evidence IDs.
- Bootstrap: shares one state/clock with builder/executor, uses the static default registry, and imports no adapter or legacy module.
- Rollback: reverting the R10 staged set restores the prior R08/R09 behavior.

## Verification

Final staged-tree checks:

- Focused R10/planning/domain: 55/55 passed.
- Compile: exit 0.
- Architecture: PASS, 38 files.
- Scope: PASS with 16 staged files.
- Staged and unstaged whitespace checks: PASS.

The previously run broad gates remain applicable to this final delta: headless default 1495/1495, opt-in 19/19, full discovery 1704/1704, architecture self-test 16/16, and scope self-test 8/8.

## Minor Notes

None requiring action.

## Architecture / Scope / Rollback

- Dependency direction remains domain <- application <- bootstrap with plugins consumed by application planning.
- No legacy or adapter imports were added to turn/application/bootstrap production code.
- Staged scope is clean and allowed.
- Rollback remains a single staged R10 revert before commit; no unrelated changes are coupled to the implementation.
