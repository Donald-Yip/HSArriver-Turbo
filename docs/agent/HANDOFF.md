# HANDOFF

## Current Phase

R12 complete and independently APPROVED; R13 native adapter composition and one-game cutover are next.

## Completed

- R12 added installable `src` packaging and the real `hslegend-onegame` console script.
- `OneGameUseCase` coordinates the actual R08-R11 services for one game, one mulligan, one settlement, and no second game.
- Turn delegation is keyed by `(game_generation, turn_number)` and generation changes fail closed after end-turn.
- Cancellation wins over failed lobby/state/sleeper operations; only an expired monotonic deadline reports `timed_out`.
- Entrypoints now have an allowlist architecture gate: stdlib, application, bootstrap, sibling entrypoints.
- The deterministic fake path performs the exact five dispatches and ends at `RESULT`.
- R12 gates: focused 23/23; default 1,500; discover 1,745; opt-in 19/19; import, CLI help/fake JSON, architecture, scope, compile, and diff checks passed.

## Current Risk

- R13 must compose the existing R06/R07 adapters through legacy root functions without importing legacy modules into application code.
- Native adapter wiring is still absent; `hslegend-onegame` intentionally requires `--fake`.
- The real result screen may need a safe dismissal action; do not reuse an unfiltered start/rematch control.

## Exact Next Action

1. Fast-forward `develop` to R12 and create `feature/R13-native-cutover`.
2. Write the R13 task book and interface contract.
3. Add a bootstrap-only native composition factory using the existing adapters.
4. Keep downstream recommendation/target two-phase work explicit and fail closed.
5. Run a controlled native smoke, then decide the main cutover.