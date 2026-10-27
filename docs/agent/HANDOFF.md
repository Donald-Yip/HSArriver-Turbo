# HANDOFF

## Current Phase

R11 complete and independently APPROVED; R12 one-game CLI and fake end-to-end slice is next.

## Completed

- R11 observation-only settlement use case approved after 3 review rounds.
- `timed_out` requires the injected monotonic deadline; early adapter timeout is typed failure.
- Cancellation wins after failed reads, invalid observations, and in-flight sleeper/port failures.
- Pre-poll cancellation reports exactly `0.0` elapsed without reading the clock.
- No dispatch, gate, ledger, or next-game action is reachable from settlement.
- Focused 19/19; default 1,495; discover 1,723; opt-in 19/19; compile, architecture, and scope gates passed.

## Known Root Issue For R12

- The project still has `tool.uv.package = false`, so `uv run python -c "import hslegend"` fails and `uv run hslegend-onegame` cannot exist yet. R12 must make the `src/hslegend` package installable before adding the CLI.

## Exact Next Action

1. Fast-forward `develop` to the R11 commit.
2. Create `feature/R12-one-game-cli` from `develop`.
3. Write the R12 task book and interface contract.
4. Implement the application-level one-game orchestration, installable `src` packaging, fake full-game scenario, and `hslegend-onegame --help` gate.