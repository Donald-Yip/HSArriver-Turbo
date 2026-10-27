# HANDOFF

## Current Phase

R13A native anti-corruption bridge is complete and independently APPROVED. R13 now owns the controlled live one-game smoke and cutover.

## Completed

- R13A is committed work in progress pending final commit on `feature/R13A-native-bridge`.
- `hslegend.adapters.legacy` is the only package importing stable root/src legacy systems.
- Power.log pump failure invalidates both lobby and game-state authority; the next successful INFO revision restores it.
- EnterBattle, mulligan, turn and action families dispatch through injected legacy-click callbacks, never a global `click` alias.
- HSAng keeps the legacy no-header, normalized-text, multi-frame stability behavior with separate mulligan/turn captures.
- `NativePorts` contains ports only; bootstrap-native constructs the real R08-R12 graph.
- `--help` and `--fake` stay isolated from native imports.
- R13A gates: focused 53/53; default 1,501; discover 1,793; opt-in 19/19; architecture 53 files and 23/23 self-tests.

## Known Constraints

- Native composition works from a source checkout. A non-editable wheel cannot contain the legacy root runtime; it now fails typed and supports `HSLEGEND_LEGACY_ROOT`. R13 must decide whether to package legacy or document source-checkout deployment.
- No live game click, screenshot, OCR inference, or native smoke has been run for the new graph.

## Exact Next Action

1. Fast-forward `develop` to R13A and create `feature/R13-native-cutover`.
2. Write the R13 task book with explicit live-game authorization and stop conditions.
3. Run a controlled native one-game smoke only with user approval.
4. Capture Power.log/action evidence, fix only root integration defects, and decide main cutover.