# Test Strategy For The Refactor

## Goal

Replace a large, historically accumulated test suite with a smaller suite that proves behavior, contracts, architecture, and regression risk. Test count is not a success metric.

## Test Layers

### Domain Unit Tests

Pure, fast, no I/O. Cover immutable models, target resolution, plan validation, postconditions, card capabilities, and domain invariants.

### Application Tests

Use fake ports and controllable time. Cover use-case transitions, cancellation, action gating, uncertain execution, plugin resolution, journaling, and failure recovery.

### Adapter Contract Tests

Use fixed frames, OCR fixtures, Power.log fixtures, temporary config directories, and fake Win32 boundaries. Prove translation between external systems and ports.

### Fake End-To-End

Run a complete one-game scenario without Hearthstone, Windows, Tk, Paddle models, real mouse input, or admin privileges.

### Native Smoke

Explicit opt-in only. Run against a controlled Windows environment with Hearthstone and HSAng. Native tests are release/high-risk-path gates, not the default suite.

## Keep

- Tests tied to a user-visible behavior.
- Tests tied to an explicit interface or data contract.
- Tests that reproduce a real regression.
- Tests for cancellation, stale snapshots, ambiguous execution, and fail-closed behavior.
- Architecture and dependency-direction checks.
- Golden parser/OCR/Power.log fixtures when they represent real captured evidence.

## Remove Or Replace

- `inspect.getsource()` and source-string assertions.
- Tests that assert implementation details or private call sequences where observable behavior is available.
- Duplicated assertions that merely protect copied implementation; genuine external API, HTML workflow, or status-schema contracts are retained even if text overlaps.
- Tests requiring real admin rights, Node, screen capture, process enumeration, OCR models, or a running game in the default suite.
- Hardcoded machine paths or credentials.
- Tests that accept dangerous behavior such as clicking unknown coordinates or rebuilding corrupted config.
- Tests that exist only to maintain drift between duplicated tables.

## Migration Rule

1. Inventory every test by layer and purpose.
2. Classify as keep, rewrite, move-to-native, or delete.
3. For each behavior worth preserving, create a passing replacement test before deleting the old one. A deletion rationale alone is insufficient.
4. Run the old and new gates where possible; record intentional coverage changes.
5. Delete obsolete tests in a dedicated task commit after Controller approval of the behavior-to-test mapping, never mixed with unrelated feature work.

## Quality Gates

- No default test may require Windows GUI, real mouse, real Hearthstone, Paddle inference, network, or admin.
- Unit tests should not sleep; component tests use fake time.
- Every new production module landed in the refactor needs a behavioral test or a documented reason why no test is useful.
- Every bug fix requires a regression test that fails without the fix.
- CI must report actual command, exit code, pass/fail/skip counts, and duration.

## Inventory Deliverable

R02 must produce `docs/testing/TEST_INVENTORY.md` containing:

- Test file.
- Test count.
- Layer classification.
- Keep/rewrite/move/delete decision.
- Replacement test or reason for deletion.
- Owner and follow-up task.
