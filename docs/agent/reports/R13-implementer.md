# R13 Implementer Report - Phase A

## Status

PHASE A IMPLEMENTED / LIVE EXECUTION PENDING USER AUTHORIZATION

## Coordination Note

Two fresh implementer subagents were attempted for Phase A but the service returned HTTP 429 before either could make changes. The main controller took over implementation under the same R13 task book. No live Hearthstone action was performed.

## Scope

- Added the explicit native `--allow-live` safety gate.
- Added read-only `--preflight --json`.
- Added injectable readiness probes and stable typed reports.
- Preserved `--fake` and help/fake import isolation.
- Did not run the live one-game command.

## Changed Files

- `src/hslegend/entrypoints/onegame.py`
- `src/hslegend/adapters/legacy/__init__.py`
- `src/hslegend/adapters/legacy/factory.py`
- `src/hslegend/adapters/legacy/preflight.py`
- `tests/adapters/legacy/test_preflight.py`
- `tests/adapters/legacy/test_import_isolation.py`
- `tests/test_one_game_e2e.py`
- `docs/agent/reports/R13-implementer.md`

## Contracts

- Native execution without `--allow-live` exits 2 before native runtime import, adapter construction, capture or click.
- `--fake` works without the live gate.
- `--preflight` is read-only and exits 0 only when all required checks pass.
- Preflight reports ordered checks for runtime, config identity, log root, Power.log, Hearthstone/HSAng processes, foreground/input readiness, safe UI phase, admin status, and deck index.
- Probe failures are typed and never become success.
- No probe launches, focuses, clicks, saves screenshots, runs OCR inference, or mutates config/game state.
- The report includes a concrete future command only as a string; it is not executed.

## Real Read-Only Preflight Evidence

Command:

```powershell
uv run hslegend-onegame --preflight --deck-index 0 --json
```

Result: exit 1, no click and no game mutation. Stable blockers reported in this worktree:

- `config_identity_missing`
- `log_root_missing`
- `power_log` blocked by missing log root
- `hearthstone_not_foreground`
- `unsafe_ui_state`
- `admin_required`

Hearthstone and HSAng processes were detected, legacy runtime was found, and deck index 0 mapped successfully. These blockers must be resolved in the elevated live-run context before Phase B.

## Verification

- Focused preflight/import/CLI tests: 33/33 passed.
- Default headless gate: 1,501 passed.
- Full discovery: 1,816 passed.
- Opt-in gate: 19 passed.
- Architecture gate: 54 files and 23/23 self-tests passed.
- `uv run hslegend-onegame --fake --json`: completed.
- Read-only preflight returned the expected non-ready report in this environment: missing BattleTag/log root, non-foreground UI and non-admin process.
- No `--allow-live` command was run.

## Risks

- Preflight itself captures a UI frame only when Hearthstone/HSAng is foreground; it does not save or OCR that frame unless the default UI recognizer computes visual pixel evidence in memory.
- The current worktree has no configured BattleTag/log root, and the agent process is not elevated. Live Phase B cannot start from this exact context.
- Non-editable wheel deployment remains a typed limitation from R13A; source-checkout execution is the accepted R13 scope.

## Rollback

Revert the Phase A changes. The R13A native bridge and R12 fake path remain unchanged.

## Out-Of-Scope Findings

- No native live run has been attempted.
- No result dismissal, rematch, second game, config mutation, coordinate change or new dependency was introduced.

## Review Closure

- Independent review rounds closed with APPROVED after fixing architecture placement, tracker-free UI capture, exact live-command rendering, strict probe/report validation, canonical JSON output, typed CLI reasons, Power.log existence checks, and success evidence.
- Focused Phase A tests: 33/33 passed.
- Full discovery: 1,816/1,816 passed at reviewer checkpoint.
- No live Hearthstone command was run. Phase B remains explicitly gated on user authorization, configured BattleTag/log root, safe foreground state, and an elevated process.
