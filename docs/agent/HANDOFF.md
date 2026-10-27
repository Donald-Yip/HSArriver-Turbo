# HANDOFF

## Current Phase

R02E - test cleanup stage 1 (B1/B2/B5).

## Current Task

R02E (`in_progress`)

## Scope

- B1: remove two private table-drift tests and rewrite two in place.
- B2: merge one duplicate DTO boundary method.
- B5: rewrite two mock-only tests in place.
- Deferred: B3/B4/B6-B9.

## Expected Counts

- Default: `1,525 -> 1,522`.
- Opt-in: `3 -> 3`.

## Exact Next Action

1. Implement B1 with replacement evidence and commit.
2. Implement B2 and commit.
3. Implement B5 and commit.
4. Independent reviewer validates all three commits and count deltas.
