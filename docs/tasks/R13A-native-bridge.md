# R13A Native Adapter Anti-Corruption Bridge

## Task ID

R13A

## Goal

Add one isolated `adapters/legacy` anti-corruption bridge that turns stable
legacy lobby, Power.log, HSAng OCR, click, config and card APIs into the existing
application ports, then wire those ports through bootstrap and the default CLI
without changing domain/application contracts.

This task is offline-only. It must not click, launch, restart, or control
Hearthstone. The first controlled native game remains R13.

## Why This Is Split From R13

The integration analysis found three hard blockers:

- Power.log exposes mutable `LogState`, not `GameSnapshot`.
- Legacy UI recognition has no authoritative `RESULT` phase.
- Importing root legacy modules from arbitrary `src/hslegend` files violates the
  architecture gate.

The root fix is an adapter-local anti-corruption layer, not imports in
application/bootstrap/entrypoints and not a god factory. R13A builds and tests
that layer headlessly. R13 then performs the real one-game smoke and cutover.

## Scope

- Add `hslegend.adapters.legacy` as the only package allowed to import stable
  root legacy modules and `src.*` legacy packages.
- Add a pumped Power.log source plus `LogState -> GameSnapshot` mapper.
- Add a native lobby adapter that uses the Power.log game state for authoritative
  `RESULT`/`IN_GAME` and injected UI recognition for menu/deck/matching.
- Add a stable HSAng bridge that preserves multi-frame OCR stability while
  implementing the existing capture/read/parse contract.
- Add a declarative `PlanStep/ActionIntent -> legacy click primitive` dispatcher
  for the existing MVP action families.
- Add a native port bundle and bootstrap factory using the real application
  use cases.
- Let the CLI select native composition by default and keep `--fake` as the
  deterministic headless path.
- Narrow the architecture gate so root legacy imports are legal only inside
  `hslegend.adapters.legacy`; all other layers remain forbidden.
- Add deterministic offline adapter/bridge/composition tests.

## Non-Goals

- No real clicks, launches, game actions, screenshots, OCR inference, or admin
  operations in this task.
- No native live smoke and no `main` cutover; R13 owns those.
- No changes to R08-R12 use-case behavior.
- No result dismissal/rematch/start-second-game path.
- No Web, overlay, scheduler, concede, watchdog, MCP, or legacy FSM behavior.
- No changes to coordinates, delays, thresholds, click sequences, external
  HTTP/CLI/MCP contracts, or persistent data formats.
- No new dependencies, async, threads beyond the existing deadline helper, or
  dynamic plugin discovery.
- No direct import of `FSM_action`, `manual_controller`, or `web_ui` internals.

## Inputs

- Branch `feature/R13A-native-bridge`, based on R12 commit `8981413`.
- Existing R06/R07 adapters and application ports.
- Stable legacy surfaces identified by the R13 integration map.
- Recorded/synthetic Power.log and OCR fixtures; no live game required.

## Outputs

- `src/hslegend/adapters/legacy/**` anti-corruption modules.
- `src/hslegend/bootstrap/native.py` and exports.
- Native default CLI path with fake override.
- Focused contract tests under `tests/adapters/legacy/**` and top-level CLI
  composition tests.
- Updated architecture gate and interface documentation.
- Implementer report and independent review.

## Port Bundle Contract

```text
NativePorts(
  config_repository,
  card_catalog,
  recommendation_mulligan,
  recommendation_turn,
  game_state,
  lobby_state,
  action_dispatch,
  journal,
  artifact_store,
)
```

`NativePorts` contains ports only. It must not contain application use cases,
bootstrap objects, plugins, or entrypoints.

```text
build_native_ports(stop_token, clock, sleep) -> NativePorts
build_native_one_game() -> OneGameUseCase
```

- `build_native_ports` lives in the legacy adapter package and is the only native
  factory allowed to assemble root/src legacy callables.
- `build_native_one_game` lives in bootstrap, imports only adapter factory
  surfaces, and constructs the real R08-R11 use cases plus `OneGameUseCase`.
- Tests may inject fake legacy callbacks into the same bridge classes without
  importing live game modules.

## Power.log Contract

The native game-state adapter pumps pending legacy log containers and maps the
current `LogState` to one immutable `GameSnapshot`.

Required mapping:

- `snapshot_id` is stable for `(session_id, game_generation, revision)`.
- `log_revision = state.revision`.
- `game_generation = state.game_generation`.
- `turn_number = state.game_num_turns_in_play`.
- `is_my_turn = state.is_my_turn`.
- `is_end = state.is_end`.
- Players are identified from the legacy player-id/controller mapping.
- Hero, hand, board, deck, weapon and secrets preserve entity id/card id/side/
  zone/zone position and deterministic zone-position order.
- Mana, max mana, health and armor use existing Power.log tags without changing
  their meaning.
- A mulligan surface is represented as a `ChoiceView` whose `choice_type` is
  `mulligan` when the legacy STEP identifies mulligan.
- General/discover choices preserve option order and source entity when known.
- Missing/inconsistent player/entity data fails closed with a typed adapter
  error; it must not invent a usable snapshot.
- `revision_provider()` returns the pumped legacy revision so
  `PowerLogGameStateAdapter` can wait for a real later revision.

## Lobby Contract

- `RESULT` wins whenever the authoritative Power.log state is complete.
- `IN_GAME` wins while an active game entity exists.
- Otherwise an injected UI recognizer supplies `MAIN_MENU`, `DECK_SELECT`,
  `MATCHING`, or `UNKNOWN`.
- Contradictory complete-game plus non-result UI must still return `RESULT`.
- No click is performed by lobby recognition.

## HSAng Contract

- Preserve stable OCR behavior by allowing the bridge to capture/re-read multiple
  frames inside one recommendation request.
- Return the existing `GameSnapshot` id and a deterministic frame/evidence id.
- `RecommendationParser.parse(ocr, turn_number, log_revision)` remains the
  semantic parser.
- Mulligan and turn use separate ROI suppliers.
- Missing OCR/model/frame data is a typed adapter error and never a guessed
  instruction.

## Action Dispatch Contract

The injected click facade maps only existing MVP action families:

```text
mulligan_replace, mulligan_keep, mulligan_confirm,
play_card, targeted_card, attack, hero_power,
play_location, location, use_location, end_turn
```

- Entity indexes/counts are derived only from the pinned `GameSnapshot`.
- Non-targeted play, targeted play, minion placement, spell use, hero power,
  location use, minion/hero attacks and end-turn map to existing click
  primitives.
- Unknown/unsupported actions return a rejected `DispatchResult`; no fallback
  blind click.
- All click operations are injected and fully asserted in headless tests.
- The module never catches a click failure as success.

## CLI Contract

```text
uv run hslegend-onegame --fake      # deterministic demo
uv run hslegend-onegame             # native composition
uv run hslegend-onegame --help
```

- Default native composition may build adapters but must not click until the
  one-game use case observes the required state and dispatches a validated plan.
- CLI tests inject fake native/fake builders and must not touch the live game.
- Exit behavior remains 0 completed, 1 non-completed, 2 usage error.

## Inputs And Fixtures

- Synthetic `LogState`/entity fixtures for snapshot mapping.
- A scripted log-container pump for revision/wait tests.
- Fake stable reader frames for HSAng tests.
- Fake click facade recording every primitive call.
- Fake UI recognizer and complete-game `LogState` for lobby tests.
- No machine-specific paths; any new path must be injected or derived from the
  existing legacy config module.

## Allowed Files

- `src/hslegend/adapters/legacy/**`
- `src/hslegend/bootstrap/native.py`
- `src/hslegend/bootstrap/__init__.py`
- `src/hslegend/bootstrap/container.py`
- `src/hslegend/entrypoints/onegame.py`
- `src/hslegend/entrypoints/__init__.py`
- `tests/adapters/__init__.py`
- `tests/adapters/legacy/**`
- `tests/test_one_game_e2e.py`
- `tools/check_architecture.py`
- `docs/interfaces.md`
- `docs/architecture/layers.md`
- `docs/agent/DECISIONS.md`
- `docs/tasks/R13A-native-bridge.md`
- `docs/agent/reports/R13A-*.md`
- `docs/agent/reviews/R13A-*.md`
- `docs/agent/TASK_BOARD.md`
- `docs/agent/TASK_BOARD.json`
- `docs/agent/HANDOFF.md`
- `TOTAL_TASK.md`
- `README.md`

## Forbidden Files

- All legacy root production modules and non-hslegend source packages.
- Existing generic adapters outside the new anti-corruption package unless a
  contract bug is explicitly reported first.
- R08-R12 use-case implementations, domain models, plugins, pyproject, uv.lock,
  CI workflows, native-live tests, coordinates/configs/logs/screenshots.
- Imports or edits of the legacy god modules named FSM_action, manual_controller,
  and web_ui.

## Acceptance

```powershell
uv lock --check
uv sync --locked --all-groups
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/check_architecture.py --self-test
uv run python tools/check_task_scope.py --task R13A --json
uv run python tools/check_task_scope.py --self-test
uv run python tools/run_headless_tests.py
uv run python -m unittest discover -s tests -t .
uv run python tools/run_headless_tests.py --opt-in
uv run hslegend-onegame --help
uv run hslegend-onegame --fake --json
git diff --cached --check
```

Behavioral acceptance:

- Synthetic Power.log state maps to a valid `GameSnapshot` with ordered zones,
  choices, revision and generation.
- Revision waiting observes only a strictly later revision in the same game.
- Complete log state wins over UI and yields `RESULT`.
- Stable HSAng bridge preserves the parser input and typed evidence.
- Every supported action family maps to the expected fake click primitive and
  unsupported actions click nothing.
- Native bootstrap constructs the real R08-R12 object graph using port doubles
  without importing legacy modules outside `adapters/legacy`.
- Architecture self-tests prove legacy imports are allowed only in the anti-
  corruption package.
- `--fake` remains deterministic and unchanged; no live game/click occurs.

## Risk And Rollback

- Risk: Power.log mapping changes action indices.
- Control: fixture tests assert exact entity/zone positions and dispatcher calls.
- Risk: stable OCR bridge changes recommendation behavior.
- Control: injected frame sequences and fixed parser inputs; no real inference.
- Risk: legacy imports leak upward.
- Control: module-scoped architecture gate and self-tests.
- Risk: native composition clicks before state is authoritative.
- Control: no direct clicks in bootstrap; planner/executor/gate remain the only
  dispatch path and all tests use fake click facades.
- Rollback: revert R13A; R12 fake CLI and adapters remain intact.

## Git Requirement

```text
feat(R13A): bridge native systems through adapter ports
```

## Report Paths

- Implementer: `docs/agent/reports/R13A-implementer.md`
- Reviewer: `docs/agent/reviews/R13A-review.md`