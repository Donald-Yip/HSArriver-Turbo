# HANDOFF

## Current Phase

R13 Phase A is complete and independently APPROVED. Phase B, the controlled native one-game run, is blocked pending explicit user authorization and a ready live environment.

## Completed

- Native CLI requires `--allow-live` before importing/building the native graph.
- `--preflight --json` is read-only, tracker-free, exact-command aware, and fail-closed on malformed reports/probes.
- `--fake`, help, and native-without-consent remain isolated from legacy imports.
- Phase A gates: focused 33/33; default 1,501; discover 1,816; opt-in 19/19; architecture 54 files and 23/23 self-tests.
- No live Hearthstone command, click, screenshot save, OCR inference, or game mutation was performed.

## Live Readiness Blockers

The current worktree preflight reports:

- BattleTag identity is not configured.
- Log root/Power.log are not configured in this worktree.
- Hearthstone is not foreground/input-ready.
- The agent process is not elevated.
- Hearthstone and HSAng processes are present; legacy runtime and deck slot mapping work.

## Exact Next Action

Before Phase B, the user must explicitly authorize one native game and provide:

1. The controlled deck index.
2. A configured BattleTag and existing Hearthstone log root for the elevated process.
3. Hearthstone and HSAng running, with Hearthstone at a confirmed safe starting state.
4. An elevated terminal that will execute only the bounded `--allow-live` command.

Until then, keep R13 in progress and do not run a command that can click.