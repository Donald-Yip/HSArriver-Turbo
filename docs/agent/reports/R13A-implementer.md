# R13A Implementer Report

## Status

Implemented on `feature/R13A-native-bridge` without commit, push, branch creation, Hearthstone launch/restart, live clicks, screenshots, OCR inference, or native execution. The work is offline/headless and uses `uv` only.

## Changed Files

- `src/hslegend/adapters/legacy/__init__.py`
- `src/hslegend/adapters/legacy/factory.py`
- `src/hslegend/adapters/legacy/hsang.py`
- `src/hslegend/adapters/legacy/input.py`
- `src/hslegend/adapters/legacy/lobby.py`
- `src/hslegend/adapters/legacy/paths.py`
- `src/hslegend/adapters/legacy/ports.py`
- `src/hslegend/adapters/legacy/powerlog.py`
- `src/hslegend/bootstrap/native.py`
- `src/hslegend/bootstrap/__init__.py`
- `src/hslegend/entrypoints/onegame.py`
- `tests/adapters/legacy/__init__.py`
- `tests/adapters/legacy/test_input.py`
- `tests/adapters/legacy/test_lobby_hsang.py`
- `tests/adapters/legacy/test_native_bootstrap.py`
- `tests/adapters/legacy/test_powerlog.py`
- `tests/test_one_game_e2e.py`
- `tools/check_architecture.py`
- `docs/interfaces.md`
- `docs/architecture/layers.md`
- `docs/agent/reports/R13A-implementer.md`

## Boundary And Contracts

- `hslegend.adapters.legacy` is the only package allowed to import stable root legacy modules or legacy `src.*` packages. `FSM_action`, `manual_controller`, and `web_ui` are forbidden even inside that package.
- `NativePorts` is a validated frozen bundle of nine application ports only. It contains no use cases, bootstrap objects, plugins, or entrypoints.
- `build_native_ports` derives machine-specific paths from legacy `config`, uses existing JSON config/card/journal/artifact adapters, creates one shared Power.log pump, and injects the stable click facade.
- `LegacyPowerLogPump` consumes pending legacy containers into `LogState`. `LegacyLogStateSnapshotMapper` maps revision/generation/turn/turn-owner/end/player/zone/mana/health/armor/choices into immutable `GameSnapshot`, orders zones by `(zone_position, entity_id)`, and raises typed errors on missing identity/ownership/card/entity data.
- The lobby adapter gives complete Power.log state precedence (`RESULT`), then active game state (`IN_GAME`), then injected UI evidence for menu/deck/matching/unknown. It never clicks.
- The HSAng bridge performs one multi-frame stable read through injected reader callbacks, uses separate mulligan/turn ROI suppliers, passes the exact OCR evidence to the existing parser contract, and emits deterministic hash-derived evidence IDs. Unstable/missing/parse/mode failures are typed.
- `LegacyClickActionDispatcher` wraps the existing `LegacyActionDispatch`. It resolves all click indexes from the pinned snapshot, maps the task-book MVP action families to injected click primitives, rejects unknown/stale/unsupported requests before clicking, and lets exceptions become `FAILED`.
- `build_native_one_game` shares one `StopToken` and `ActionGate` across the real EnterBattle/Mulligan/PlayTurn/FinishBattle/OneGame graph, using only adapter factory surfaces.
- The CLI selects native composition by default and `--fake` on request. Builder injection keeps CLI tests entirely offline.

## Fixture Coverage

- Synthetic `LogState` fixtures cover revision, generation, turn number, turn ownership, complete/end state, both players, hero/hand/board/deck/weapon/secrets, mana/health/armor, deterministic board ordering, general discover choices, mulligan choice surface, and missing ownership failure.
- A scripted two-container pump proves revision advancement and `wait_for_revision_change` returning a strictly later same-game revision.
- Lobby fixtures prove complete state wins even with an exploding contradictory UI, active game bypasses UI, and no-game state delegates to UI.
- OCR fixtures prove three frame/ROI reads inside one request, exact parser evidence handoff, deterministic evidence IDs, typed unstable-read failure, and mode mismatch rejection.
- Fake click fixtures assert exact calls for mulligan replace/keep/confirm, play card, targeted card, attack, hero power, all location aliases, end turn, unsupported action, stale entity, and click exception.
- Native bootstrap executes with injected demo port doubles; CLI tests assert default-native and `--fake` builder selection without touching live systems.
- Architecture self-tests prove the ACL accepts root and `src.*` imports only under `adapters.legacy`, rejects them elsewhere, and rejects the three legacy god modules everywhere.

## Verification

- `uv lock --check`: PASS
- `uv sync --locked --all-groups`: PASS
- `uv run python -m compileall -q src tests tools`: PASS
- `uv run python tools/check_architecture.py`: PASS, 53 files checked
- `uv run python tools/check_architecture.py --self-test`: PASS, 23/23 cases
- `uv run python tools/check_task_scope.py --self-test`: PASS, 8/8 cases
- Focused R13A tests: PASS, 24 tests
- `uv run python -m unittest tests.test_one_game_e2e -v`: PASS, 6 tests
- `uv run python tools/run_headless_tests.py`: PASS, 1501 tests, 0 failures/errors/skips
- `uv run python -m unittest discover -s tests -t .`: PASS, 1770 tests, 0 failures/errors
- `uv run python tools/run_headless_tests.py --opt-in`: PASS, 19 tests, 0 failures/errors/skips
- `uv run hslegend-onegame --help`: PASS, exit 0
- `uv run hslegend-onegame --fake --json`: PASS, exit 0, completed/1 turn/1 action/result
- `uv run python tools/check_task_scope.py --task R13A --json`: PASS, 21 staged files, zero violations
- `git diff --cached --check`: PASS

The full discovery run emitted pre-existing `ResourceWarning` messages from fake Tk/file helpers and completed successfully.

## Risks

- Native Power.log mapping is fixture-proven but not live-smoke-proven; R13 owns the first controlled native game.
- The bridge derives the repository root at import time to make stable root modules importable from the installed console script. This is isolated to `adapters.legacy`.
- OCR inference and screenshot capture remain runtime-only; R13A only verifies injected frame/evidence behavior.
- `mulligan_keep` is semantically a no-click action; keeping is represented by not replacing the card.

## Rollback

Revert the R13A file set. The R12 fake path remains intact, and `--fake` continues to exercise the real R08-R11 use-case graph through deterministic ports.

## Out-Of-Scope Findings

- No R08-R12 behavior changes were required.
- No generic adapter contract bug was found.
- No changes were made to domain models, plugins, root legacy files, `pyproject.toml`, `uv.lock`, CI, task board, handoff, total task, review files, decisions, or live-game assets.

## Review Fix Evidence

These review-fix results supersede the initial implementation counts above.

- **Power.log unavailable — ADDRESSED.** `LegacyPowerLogPump` now updates the frozen `last_error` field through `object.__setattr__`, returns the current empty `LogState`, and leaves lobby fallback to the injected UI recognizer. Direct container-error and lobby-fallback tests pass.
- **Pre-click target validation — ADDRESSED.** Action dispatch now resolves and validates source/target identity, side, zone, index/count, destination, and post-placement target adjustment before executing any click plan. Stale targets for `targeted_card`, `play_card` with target, `hero_power`, and location actions reject with zero clicks; the valid insertion-adjustment path is asserted exactly.
- **Lazy native imports and package isolation — ADDRESSED.** The legacy package exports `NativePorts` eagerly but lazy-loads bridge modules; bootstrap lazy-loads the native builder; the CLI imports the native builder only in the native branch; native bootstrap imports the factory only inside its builder call. Root/src imports and `sys.path` insertion now occur in `build_native_ports`, with `HSLEGEND_LEGACY_ROOT`/explicit-root override and typed `legacy_runtime_unavailable` failure. Subprocess tests prove entrypoint/bootstrap import, `--help`, `--fake`, and injected native bootstrap do not load legacy runtime modules.
- **Choice ownership/readiness — ADDRESSED.** General choices require `general_choice_ready` and an absent/local owner; incomplete and opponent-owned choices are omitted. General/power option indexes are normalized through typed validation before sorting and lookup, with malformed-index coverage.
- **HSAng factory defect — ADDRESSED.** Native readers now use no required header beacon, `parser.normalize_action_text`, and a mulligan conclusion key based on parsed replacement slots with normalized-text fallback. The focused factory test injects fake reader/capture/parser objects and performs no OCR inference or live capture.

Review-fix verification:

- Focused R13A tests: PASS, 39 tests
- CLI/E2E tests: PASS, 6 tests
- Full discovery: PASS, 1785 tests, 0 failures/errors
- Headless gate: PASS, 1501 tests, 0 failures/errors/skips
- Opt-in gate: PASS, 19 tests, 0 failures/errors/skips
- Architecture gate: PASS, 53 files
- Architecture self-test: PASS, 23/23 cases
- `uv run hslegend-onegame --help`: PASS, exit 0
- `uv run hslegend-onegame --fake --json`: PASS, exit 0

## Round 2 Review Fix Evidence

- **Native EnterBattle actions — ADDRESSED.** The native dispatcher now registers `enter_traditional`, `select_deck`, and `start_match`. `enter_traditional` captures/identifies through injected `battle_entry`, requires visible `traditional.point`, then clicks through `runtime.click`. `select_deck` validates the zero-based intent index, passes `index + 1` with an injected `runtime.click` callback, and converts mapping failures to typed rejected results. `start_match` requires recognized deck-select evidence before invoking `match_opponent` through `runtime.click.run_hearthstone_action`.
- **Power.log authority on container error — ADDRESSED.** A container error now marks prior state non-authoritative through `last_error`, and lobby recognition falls back to UI even when the retained `LogState` still has an active game entity. A later successful INFO container clears `last_error` and restores authoritative Power.log precedence.
- **Click module binding — ADDRESSED.** Every used `battle_entry` path receives callbacks/actions backed by the injected runtime click facade. The preloaded fake `click` regression test covers enter/select/start plus an in-game action, verifies the preloaded module is untouched, and confirms only the runtime facade is called.
- **Preservation checks — ADDRESSED.** Prior target prevalidation, HSAng no-header/normalizer/stability behavior, choice gating, and lazy help/fake import isolation remain covered and green.

Round-2 verification:

- Focused R13A tests: PASS, 45 tests
- Full discovery: PASS, 1791 tests, 0 failures/errors
- Headless gate: PASS, 1501 tests, 0 failures/errors/skips
- Opt-in gate: PASS, 19 tests, 0 failures/errors/skips
- CLI/E2E tests: PASS, 6 tests
- Architecture gate: PASS, 53 files
- Architecture self-test: PASS, 23/23 cases
- `uv run hslegend-onegame --help`: PASS, exit 0
- `uv run hslegend-onegame --fake --json`: PASS, exit 0

## Round 3 Review Fix Evidence

- **Power.log authoritative method failure — ADDRESSED.** `LegacyLogStateSnapshotMapper.current()` and `.revision()` now inspect `pump.last_error` immediately after pumping and raise typed `LegacyPowerLogError("power_log_unavailable", ...)` instead of returning retained state. Both methods recover normally after the next successful INFO container clears `last_error`.
- **Coverage — ADDRESSED.** Added current/revision error-and-recovery tests using the same scripted error-then-INFO pump. Existing stale-active lobby fallback and successful-container restoration tests remain green.

Round-3 verification:

- Focused R13A tests: PASS, 47 tests
- Full discovery: PASS, 1793 tests, 0 failures/errors
- Headless gate: PASS, 1501 tests, 0 failures/errors/skips
- Opt-in gate: PASS, 19 tests, 0 failures/errors/skips
- Architecture gate: PASS, 53 files
- Architecture self-test: PASS, 23/23 cases
- `uv run hslegend-onegame --help`: PASS, exit 0
- `uv run hslegend-onegame --fake --json`: PASS, exit 0
