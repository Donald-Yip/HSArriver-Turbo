# HANDOFF

## Current Phase

R13 native one-game acceptance and cutover is in progress. Implement the live safety gate and read-only preflight before any real click.

## Completed

- R13A anti-corruption bridge is approved and committed at `32a9e04`.
- Native action dispatch covers enter/select/start, mulligan, card, target, attack, skill, location and end-turn.
- Power.log errors invalidate lobby and game-state authority until a successful later revision.
- `--help` and `--fake` remain isolated from native legacy imports.
- R13A gates: focused 53/53; default 1,501; discover 1,793; opt-in 19/19.

## Current Task

- Task book: `docs/tasks/R13-native-cutover.md`.
- Implement `--allow-live` and `--preflight --json` without any live clicks.
- Build and test a read-only readiness report covering runtime/config/log/process/UI/admin/deck.
- Keep `--fake` independent from the live gate.
- Do not perform the native game until the user explicitly authorizes it and supplies the controlled deck index/start state.

## Exact Next Action

1. Dispatch one R13 implementer for Phase A only.
2. Review/commit the safety gate and preflight headlessly.
3. Ask the user for explicit live-run authorization, deck index, and confirmed starting state.
4. Run one bounded native game and record acceptance evidence.