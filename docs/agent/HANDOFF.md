# HANDOFF

## Current Phase

R12 one-game application flow, installable package, and fake CLI are in progress.

## Completed

- R11 independently approved and merged to local `develop` at `2426366`.
- Settlement is observation-only and cannot dispatch or start a second game.
- R11 gates: focused 19/19; default 1,495; discover 1,723; opt-in 19/19.

## Current Task

- Task book: `docs/tasks/R12-one-game-cli.md`.
- Branch: `feature/R12-one-game-cli`.
- Implement the real `OneGameUseCase` over R08-R11 services.
- Make `src/hslegend` installable, add `hslegend-onegame --fake`, demo adapters,
  architecture entrypoint rules, CI CLI smoke, and a deterministic full-game E2E.
- Root issue: `tool.uv.package = false` currently prevents package imports and
  console-script generation.
- Do not wire native Hearthstone adapters or start a second game.

## Exact Next Action

1. Dispatch one R12 implementer with the task book and interface contract.
2. Review focused diff and tests as controller.
3. Dispatch a fresh independent reviewer.
4. Fix findings, run all gates, and create the single R12 feature commit.