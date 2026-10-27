# HANDOFF

## Current Phase

R13A native anti-corruption bridge is in progress. This task is fully headless;
it must not click, launch, or control Hearthstone.

## Completed

- R12 is independently approved, committed at `8981413`, and merged to local `develop`.
- The fake one-game flow uses the real R08-R11 services and the installable `hslegend-onegame` CLI.
- R12 gates: focused 23/23; default 1,500; discover 1,745; opt-in 19/19.

## Current Task

- Task book: `docs/tasks/R13A-native-bridge.md`.
- Branch: `feature/R13A-native-bridge`.
- Only `hslegend.adapters.legacy` may import stable root/src legacy systems.
- Build Power.log -> GameSnapshot mapping, authoritative lobby result, stable HSAng bridge, and PlanStep -> click dispatch with injected fakes.
- Native bootstrap receives a `NativePorts` bundle and constructs the real R08-R12 use cases.
- No live game action or main cutover in this task.

## Exact Next Action

1. Dispatch one R13A implementer with the task book and anti-corruption boundary.
2. Verify the diff and headless evidence as controller.
3. Dispatch a fresh independent reviewer.
4. Fix findings, run all gates, and create the single R13A feature commit.