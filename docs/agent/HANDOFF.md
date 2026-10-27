# HANDOFF

## Current Phase

R02E stage 2B: B9/B8/B7 environment test isolation.

## Current Task

R02E (`in_progress`)

## Approved

- Stage 1 B1/B2/B5.
- Stage 2A B6, final commit `1ebd8fc`.

## Stage 2B Targets

- B9: O12 to CI opt-in tooling.
- B8: O04-O11/O14 to CI opt-in tooling; one static manifest default method.
- B7: O02/O03/O13 to CI opt-in; O01 to native-live; one static MCP schema default method.
- intermediate/final default 1,511, discover 1,673, CI opt-in 16, native-live 1.
- live-window test is never part of ordinary CI.

## Exact Next Action

1. Implement B9 and test the split runner modes.
2. Implement B8, retaining static manifest coverage.
3. Implement B7, retaining static MCP schema and separate native-live smoke.
4. Run full gates and request independent stage 2B review.
