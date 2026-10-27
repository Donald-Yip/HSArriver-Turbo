# R02E Stage 1 Implementer Report

## Status

IMPLEMENTED / READY FOR INDEPENDENT REVIEW

## Scope

Executed only the approved low-risk cleanup batches B1, B2, and B5 from `docs/testing/TEST_PORTFOLIO_AUDIT.md`.

## Commits

- `a990515` `test(R02E-B1): remove private table drift checks`
- `cf4ab44` `test(R02E-B2): merge duplicate DTO boundary assertions`
- B5 commit is created after final verification.

## B1

Changed:

- Removed A01 `test_delay_effect_scope_table`.
- Removed A02 `test_key_is_part_of_the_user_delay_whitelist`.
- Rewrote R06 as fixed-frame `screen_regions -> battle_entry.identify` behavior.
- Rewrote R07 as default-coordinate plus save/load roundtrip behavior.

Verification:

- Public equivalent gate: `Ran 31 tests`, `OK`.
- `HotChannelTests` 19 -> 18; `ConfigTests` 3 -> 2.
- Full headless: `Ran 1523 tests`, `OK (skipped=1)`.

## B2

Changed:

- Merged invalid confidence assertions into the observation immutability/serialization test.
- Reused the existing exact-positive-int max_turns test.
- Removed the duplicate M01 method without weakening the contract.

Verification:

- `Ran 6 tests`, `OK`.
- Default headless remains `1523` because `tests/application` is not collected by the default top-level runner.
- Full discover: `Ran 1685 tests`, `OK (skipped=1)`.

## Count Correction

The first ledger assumed every candidate belonged to the default runner. R02E execution proved that application/domain/adapter/plugin tests affect `discover`, not `default`. The corrected targets are:

- default: `1,525 -> 1,495`
- discover: `1,688 -> 1,657`
- opt-in: `3 -> 20`

Stage 1 target is default `1,523`, discover `1,685`, opt-in `3`.

## B5

Changed:

- R08 now proves no trim using a real temp file: original content remains, the new line is appended, size stays below the trim threshold, and no `.tmp` file remains.
- R09 now proves unknown overlay status leaves `CTRL.prepared` and the automation-thread reference unchanged.

Verification:

- Focused `LogTrimTests` plus `OverlayVerificationTests`: `Ran 9 tests`, `OK`.
- Method count unchanged.

## B6

Changed:

- R04/R05 now use Event-gated callbacks.
- Verified timeout via `TimeoutError`, then released the callback and joined the actual deadline worker; no `Event.wait(timeout)` or elapsed-time assertion remains.
- Removed `time.sleep(0.3)` and wall-clock `<0.15` assertions.

Verification:

- Focused adapter gate: `Ran 15 tests`, `OK`.
- Three consecutive focused runs: `OK`; independent review APPROVED.
- Adapter tests are discoverable-only; default headless method count is unchanged.

## Stage 2B Implementation

- B9 `cf83d1f`: import-free gate manifests, `tests/opt_in`/`tests/native_live`, and exact O12 exit-code validation.
- B8 `a19745e`: environment checks moved to CI opt-in; static manifest parity retained.
- B7 `a3eca78`: live-window check moved to native-live; MCP and Windows semantics moved to CI opt-in.
- B9/B8/B7 reverse patch checks each exit `0`, proving independent rollback from final HEAD.
- Current verified boundaries: default 1,511; discover 1,673; CI opt-in 16; native-live 1.

## Final B4/B3

- B4 `a181960`: one public fake-Tk lifecycle scenario covers normal and failed-mainloop cleanup/reopen and replaces five private patch tests.
- B3 `1135959`: three real-script Node DOM behavior methods replaced twelve implementation-string tests.
- Full default: 1,495 tests pass.
- Full discover: 1,657 tests pass.
- CI opt-in: 19/19 pass.
- Native-live: one required real-window test, intentionally excluded from CI.

## Known Risks

- R06 now imports frame helpers from another test module. This is test-only coupling; a later helper move may reduce it.
- B3/B4/B6-B9 remain deferred.
- The old R02D approval recorded the superseded arithmetic; the R02E decision log and cleanup plan contain the corrected three-suite accounting.

## Rollback

Each batch is a separate revertable commit. No production code was changed.
