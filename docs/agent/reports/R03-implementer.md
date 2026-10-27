# R03 Implementer Report

## Status

FIX_ROUND_2_COMPLETE — ready for fresh review. No commit or push performed.

## Base / Head / Branch

- Base SHA: `468b5987c682d5eba192554281d0072f7ed75bcf`
- Head SHA: `468b5987c682d5eba192554281d0072f7ed75bcf` (working tree only)
- Branch: `feature/R03-domain-contracts`

## Changed Files

Production:

- `src/hslegend/__init__.py`
- `src/hslegend/domain/__init__.py`
- `src/hslegend/domain/primitives.py`
- `src/hslegend/domain/models.py`
- `src/hslegend/domain/cards.py`
- `src/hslegend/domain/actions.py`
- `src/hslegend/domain/results.py`
- `src/hslegend/domain/rules.py`

Tests:

- `tests/domain/__init__.py`
- `tests/domain/support.py`
- `tests/domain/test_models.py`
- `tests/domain/test_targets.py`
- `tests/domain/test_validation.py`
- `tests/domain/test_postconditions.py`
- `tests/domain/test_results.py`
- `tests/domain/test_purity.py`

Contract/status documentation:

- `docs/agent/HANDOFF.md`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/TASK_BOARD.json`
- `docs/tasks/R03-domain-contracts.md`
- `TOTAL_TASK.md`
- `docs/agent/reports/R03-implementer.md`

Pre-existing controller-owned documentation remains within the allowed R03 scope.
The complete staged task scope is 21 changed paths.

## Fix Round 1

1. `PostconditionEvaluator` defers condition evaluation when revision did not
   advance. Fix round 2 makes only equal revisions
   `unconfirmed/snapshot_unchanged`; regressions fail as
   `failed/snapshot_regressed`.
2. `SUPPORTED_SCHEMA_VERSIONS = frozenset({1})` is defined and exported.
   `PlanRuleValidator` rejects unsupported plan/snapshot schemas and schema
   mismatch fails closed. `PostconditionEvaluator` rejects schema mismatch and
   unsupported schemas.
3. `ActionPlan.game_generation` is now a required `int`, rejects `None`/`bool`,
   and is always compared with `GameSnapshot.game_generation`.
4. Plan and step timeouts use `math.isfinite` and must be greater than zero.
   Tests cover `0`, negative, `NaN`, positive infinity, and negative infinity.
5. Every step card id is checked against the plan intent card and any supplied
   `CardDefinition`; mismatches reject with `step_card_mismatch`.
6. `GameSnapshot` rejects duplicate `entity_id` values across players, zones,
   weapons, secrets, and choice options with `ValueError`.
7. `Condition.expected` is recursively deep-frozen through
   `deep_freeze_primitive`; mappings become sorted tuple pairs, sequences become
   tuples, and arbitrary objects are rejected. Nested source mutation cannot
   affect a condition.
8. `TOTAL_TASK.md` now records R03 current phase, branch, and
   `ready_for_review` status; this report records the actual 21-path scope.

## Fix Round 2

- `PostconditionEvaluator` checks `after.log_revision < before.log_revision`
  first and returns `failed/snapshot_regressed` without evaluating conditions.
- Only `after.log_revision == before.log_revision` returns
  `unconfirmed/snapshot_unchanged`.
- Added a dedicated regression test containing both a false condition and an
  unsupported condition to prove regression fails closed before evaluation.
- Domain test count increased from 37 to 38.

## Interfaces Produced

Exported from `hslegend.domain`:

- `EntityRef`
- `PlayerView`
- `ChoiceView`
- `GameSnapshot`
- `CardCapabilitySet`
- `CardDefinition`
- `ActionIntent`
- `Condition`
- `PlanStep`
- `ActionPlan`
- `ValidationResult`
- `PostconditionResult`
- `ActionVerdict`
- `TargetResolver`
- `PlanRuleValidator`
- `PostconditionEvaluator`
- `SUPPORTED_SCHEMA_VERSIONS`
- `deep_freeze_primitive`
- `to_primitive`
- `canonical_json`

Key behavior:

- All public domain models are frozen, slots-enabled dataclasses; sequence
  inputs are normalized to tuples and condition payloads are recursively frozen.
- `GameSnapshot` provides deterministic entity lookup and rejects duplicate
  entity ids at construction.
- `TargetResolver.resolve` filters by `side`, `zone`, `zone_pos`, and
  `entity_id`; exact targets require matching pinned snapshot identity.
- `PlanRuleValidator` fails closed on schema, generation, snapshot revision,
  timeout, target/source, card identity, empty-plan, duplicate-step, and
  precondition errors.
- `PostconditionEvaluator` judges only immutable before/after snapshots and
  returns unconfirmed before condition evaluation when revision did not advance.
- Every model supports deterministic JSON-compatible primitives and canonical
  compact JSON.

## Interfaces Consumed

- Python standard library only: `dataclasses`, `json`, `enum`, `math`,
  `collections.abc`, and `typing`.
- No application, adapter, bootstrap, plugin, legacy, filesystem, network,
  thread, OCR, Win32, Tk, or external-library imports.

## Verification (Raw Results)

### Environment sync

```text
$ uv sync --locked --all-groups
Resolved 244 packages in 4ms
Audited 77 packages in 8ms
EXIT:0
```

### Compile

```text
$ uv run python -m compileall -q src tests tools
EXIT:0
```

### Architecture

```text
$ uv run python tools/check_architecture.py
[architecture] PASS: 8 files checked
EXIT:0
```

```text
$ uv run python tools/check_architecture.py --self-test
[architecture:self-test] PASS: 16/16 cases
EXIT:0
```

### Domain tests

```text
$ uv run python -m unittest discover -s tests/domain -t . -v
Ran 38 tests in 0.022s
OK
EXIT:0
```

### Headless suite

```text
$ uv run python tools/run_headless_tests.py
Ran 1524 tests in 136.422s
OK (skipped=1)
[headless-tests] headless summary: run=1524 passed=1523 failures=0 errors=0 skipped=1
EXIT:0
```

### Scope

```text
$ uv run python tools/check_task_scope.py --self-test
[scope:self-test] PASS: 8/8 cases
EXIT:0
```

```text
$ uv run python tools/check_task_scope.py --task R03
[scope] PASS: R03 (21 changed files, source: git diff --cached)
EXIT:0
```

The scope check uses the complete staged R03 file set containing 21 changed
paths.

### R03-owned whitespace

```text
$ git diff --check -- src/hslegend tests/domain TOTAL_TASK.md docs/agent/reports/R03-implementer.md
EXIT:0
```

## Scope Check

PASS. Complete task scope is 21 changed paths. No legacy runtime file,
application, adapter, bootstrap, plugin, dependency manifest, or lock file was
changed.

## New Dependencies And Public API

- New runtime dependencies: none.
- New build/test dependencies: none.
- Public product API: unchanged.
- New internal domain API: `SUPPORTED_SCHEMA_VERSIONS` and
  `deep_freeze_primitive`; existing R03 domain interfaces remain intact.

## Known Risks

1. `Condition` intentionally supports a small string vocabulary. Unsupported
   conditions fail closed, so new gameplay conditions require explicit rule and
   test additions.
2. `PlanRuleValidator` evaluates plan-level preconditions plus first-step initial
   preconditions. Later step preconditions belong to ordered execution and must
   be evaluated by the application executor when that step becomes current.
3. Exact target validation includes `side`, `zone`, and `zone_pos` from the pinned
   snapshot. An entity moving between observation and dispatch is rejected as a
   changed target rather than silently retargeted.
4. Confirmation requires an increased authoritative `log_revision`; equal
   revisions return `unconfirmed/snapshot_unchanged`, while regressed revisions
   fail as `snapshot_regressed` without evaluating conditions.
5. Schema version `2+` is intentionally rejected until an explicit contract
   migration adds the version to `SUPPORTED_SCHEMA_VERSIONS` and supplies tests.

## Requested Review Focus

- Confirm equal-versus-regressed revision precedence matches the execution contract.
- Confirm schema whitelist failures use acceptable stable reason codes.
- Review required integer `game_generation` enforcement.
- Review step-card validation against both plan intent and card definition.
- Confirm duplicate entity-id rejection does not reject a valid normalized
  snapshot shape.
- Confirm deep-frozen condition payloads remain usable for all current condition
  kinds.
- Confirm no domain import-time I/O or hidden mutable state.
