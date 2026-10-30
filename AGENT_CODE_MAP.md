# HSLegendArriver Refactored - Agent Code Map

This directory is a standalone copy of the project. It has no `.git`, logs,
or temporary screenshots. Its own `.venv` was created locally by `uv sync`.
The original source worktree is:

```text
C:\Users\DonaldYip\.codex\worktrees\d248\HearthstoneLegendArriver-main
```

The copy was taken from branch `feature/R16A-upstream-sync-1027` after the
2026-10-27 upstream synchronization. Treat this document as a navigation aid,
not as a replacement for the task books and interface documents.

## 1. First files to read

Read in this order before changing code:

```text
AGENTS.md
CORE_PHILOSOPHY.md
TOTAL_TASK.md
docs/interfaces.md
docs/architecture/layers.md
docs/architecture/dependency-rules.md
docs/agent/HANDBOOK.md
docs/agent/TASK_BOARD.md
```

Do not read `FSM_action.py`, `web_ui.py`, or a large test file end to end just
to gain context. Use the map below, the interface documents, and targeted
search first.

## 2. Runtime and tooling

Use `uv` only:

```powershell
uv sync --locked --all-groups
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/check_architecture.py --self-test
uv run python tools/run_headless_tests.py
uv run hslegend-onegame --help
```

The live Web console requires an elevated PowerShell on Windows:

```powershell
uv run python web_ui.py
```

The copied folder is not registered as a scheduled task. A scheduled task from
the original worktree must not be assumed to point at this copy. If a task is
required, register it again from this directory with:

```powershell
uv run python hsctl.py serve install-task
uv run python hsctl.py serve start --via-task --wait 120
```

## 3. Top-level map

```text
web_ui.py                 Full Web control plane and legacy automation glue.
hsctl.py                  CLI client for the Web API and local service lifecycle.
main.py                   Legacy startup entry point.
selfcheck.py              Environment and runtime self-diagnosis.
FSM_action.py             Legacy traditional-match state machine. Do not use for BGS.
manual_controller.py      Legacy orchestration/control loop.
log_state.py              Legacy traditional-match Power.log state model.
power_log.py              Power.log discovery helpers.
battle_entry.py           Traditional-mode UI recognition and entry flow.
click.py                  Win32 mouse/keyboard primitives.
get_screen.py             Screen capture and window geometry.
screen_regions.py         Coordinate/ROI definitions and mapping.
display_mapping.py        Client/screen coordinate conversion.
config.py                 Legacy configuration and local paths.
ui_config.json            Local UI coordinates and timing calibration.
cards.json                10 MB card catalog. Use a catalog service; do not parse
                           it at every UI refresh or read it manually in full.
web/index.html            Existing Web console page.
assets/                   Templates, images, and UI resources.
constants/                Legacy constants.
ai/                       Local deck-strategy engine (optional decision source):
                          deck_strategy.py auto-discovers ai/<deck>/ folders;
                          card_rules.py/sim_cards.py are shared simulation facts;
                          任务牧/动物园术/强化学习动物园术 are deck strategies.
search/                   Priority-queue turn search over AiState/AiAction:
                          search.py (Search/aiSearch/doAction/rankState),
                          ai_state.py, ai_action.py, serialize.py (joint mode).
docs/                     Architecture, interfaces, tasks, CI, agent reports.
tools/                    Architecture, scope, test, release, and diagnostics tools.
tests/                    Legacy and layered test suites.
```

## 4. New layered package: src/hslegend

Dependency direction:

```text
domain <- application <- adapters
              ^
           plugins
bootstrap -> application + adapters + plugins
entrypoints -> bootstrap + application commands
```

### 4.1 Domain

```text
src/hslegend/domain/models.py       GameSnapshot, EntityRef, PlayerView, ChoiceView.
src/hslegend/domain/actions.py      ActionIntent, PlanStep, ActionPlan.
src/hslegend/domain/cards.py        CardDefinition and CardCapabilitySet.
src/hslegend/domain/rules.py        Pure planning/precondition rules.
src/hslegend/domain/results.py      Validation and postcondition results.
src/hslegend/domain/primitives.py   Shared value primitives.
```

Rules:

- No filesystem, network, Win32, OCR, Tk, legacy root imports, or threads.
- Keep game semantics here, not click coordinates or timing.

### 4.2 Application

```text
src/hslegend/application/dto.py              Frozen application DTOs.
src/hslegend/application/runtime.py          StopToken, ActionGate, runtime controls.
src/hslegend/application/ports/__init__.py   Port protocols.
src/hslegend/application/use_cases/enter_battle.py
src/hslegend/application/use_cases/mulligan.py
src/hslegend/application/use_cases/turn.py
src/hslegend/application/use_cases/finish_battle.py
src/hslegend/application/use_cases/one_game.py
src/hslegend/application/planning/builder.py
src/hslegend/application/planning/validator.py
src/hslegend/application/planning/executor.py
src/hslegend/application/planning/ledger.py
src/hslegend/application/planning/registry.py
```

Rules:

- Application imports domain and application ports only.
- It must not import adapters, bootstrap, entrypoints, `web_ui`, `FSM_action`,
  `manual_controller`, or `click`.
- Side effects are behind ports.

Current use-case flow:

```text
one_game.py
  -> enter_battle.py
  -> mulligan.py
  -> turn.py
  -> finish_battle.py
```

### 4.3 Ports

The supported port contracts are documented in `docs/interfaces.md`. The main
ports are:

```text
ConfigRepositoryPort
CardCatalogPort
RecommendationSourcePort
GameStateSourcePort
LobbyStatePort
ActionDispatchPort
RunJournalPort
ArtifactStorePort
```

Use these interfaces when adding tests or adapters. Do not connect a new
adapter directly to a use case by importing its concrete class.

### 4.4 Adapters

```text
src/hslegend/adapters/cards/       Card catalog implementation.
src/hslegend/adapters/config/      Config repository implementation.
src/hslegend/adapters/hsang/       Stable HSAng OCR capture/read/parse adapter.
src/hslegend/adapters/input/       Action dispatch abstractions.
src/hslegend/adapters/journal/     JSONL run journal.
src/hslegend/adapters/artifacts/   Run artifact storage.
src/hslegend/adapters/lobby/       Lobby state implementations.
src/hslegend/adapters/powerlog/    Power.log-facing implementations.
src/hslegend/adapters/deadline.py  Deadline/timeout adapter.
src/hslegend/adapters/demo/        Deterministic fake adapters.
src/hslegend/adapters/legacy/      Anti-corruption bridge to legacy systems.
```

Important legacy boundary:

- Only `src/hslegend/adapters/legacy/**` may import stable root/legacy systems.
- `factory.py` is the native adapter bundle factory.
- `powerlog.py` maps Power.log state into the traditional GameSnapshot.
- `hsang.py` wraps stable HSAng recommendation OCR.
- `input.py` maps declarative plan steps to legacy click operations.
- `preflight.py` performs read-only readiness checks.
- `lobby.py` layers authoritative Power.log state over UI recognition.
- `strategy_planner.py` is the only bridge from the application layer to the
  root `search/` + `ai/` local deck-strategy stack (implements
  `StrategyPlannerPort`: plan / joint_plan / describe / identity_key).

Do not add battlegrounds logic to the existing legacy `log_state.py` merely to
avoid creating a new adapter. A new BGS feature belongs in a separate bounded
context and a BGS-specific state projector.

### 4.5 Plugins

```text
src/hslegend/plugins/defaults.py   Default static plan plugins.
src/hslegend/plugins/registry.py   Plugin registration and lookup.
```

Plugins depend on domain and narrow plugin contracts only. They return
declarative plans and must not click, read files, use the network, create
threads, or import adapters/bootstrap/runtime.

### 4.6 Bootstrap and entrypoints

```text
src/hslegend/bootstrap/container.py  Application composition root.
src/hslegend/bootstrap/fake.py       Deterministic fake composition.
src/hslegend/bootstrap/native.py     Native Windows/Hearthstone composition.
src/hslegend/entrypoints/onegame.py  `hslegend-onegame` command.
```

Bootstrap is the only place that wires concrete adapters to use cases.
Entrypoints translate commands and render results; they do not import concrete
adapters, plugins, or legacy modules.

## 5. Legacy traditional-match flow

The existing traditional-match automation is still the production Web path:

```text
Web UI / hsctl
  -> web_ui.py
  -> manual_controller.py
  -> FSM_action.py
  -> battle_entry.py / log_state.py / click.py / src/parser / src/ocr
```

Key caution:

- `FSM_action.py` is not a battlegrounds engine.
- `log_state.py` is not a BGS state model; it assumes a traditional match.
- `web_ui.py` is the control plane. Prefer API-level changes over rewriting
  its 180 KB body.
- HSAng recommendations are OCR text from a separate panel; they are not the
  same thing as Power.log state.

## 6. Tests and gates

```text
tests/domain/        Pure domain tests.
tests/application/   Fake-port use-case tests; no real desktop required.
tests/adapters/      Adapter contract tests and fixed fixtures.
tests/plugins/       Plugin and registry tests.
tests/opt_in/        Deterministic tests that are not in the default suite.
tests/native_live/   Real Windows/Hearthstone/OCR tests; explicit opt-in only.
tests/test_*.py      Legacy behavior/regression tests.
tests/helpers/       Fake Tk, Node runtime, environment helpers.
tools/run_headless_tests.py
tools/check_architecture.py
tools/check_task_scope.py
```

Default verification:

```powershell
uv sync --locked --all-groups
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/check_architecture.py --self-test
uv run python tools/run_headless_tests.py
```

Native/real-window verification is explicit only:

```powershell
uv run python tools/run_headless_tests.py --opt-in
```

Do not make default tests require Hearthstone, HSAng, PaddleOCR, a real window,
administrator rights, a network connection, or a particular desktop layout.

## 7. Documentation map

```text
docs/interfaces.md                    Supported cross-layer contracts.
docs/architecture/layers.md           Layer responsibilities and dependency rules.
docs/architecture/dependency-rules.md Forbidden imports and verification.
docs/agent/HANDBOOK.md                Agent handoff and workflow rules.
docs/agent/TASK_BOARD.md              Human-readable task state.
docs/agent/TASK_BOARD.json            Machine-readable task state.
docs/agent/HANDOFF.md                 Current task, SHA, tests, next action.
docs/agent/DECISIONS.md               Architecture decisions and rollback notes.
docs/agent/reports/                   Implementer reports.
docs/agent/reviews/                   Independent reviewer reports.
docs/tasks/                           Individual task books.
docs/testing/                         Test inventory, cleanup, and CI gates.
docs/plans/                           Historical refactor plans.
```

Before implementing a task, read its task book and update the task board/handoff.
After implementation, update interfaces, comments, task book, and verification
evidence together.

## 8. Agent boundaries and common failure modes

- Do not infer a module from its name alone; read its public interface first.
- Do not put BGS behavior into the traditional `FSM_action.py` or `log_state.py`.
- Do not make a domain module import a concrete adapter to save wiring code.
- Do not use OCR to infer state when Power.log provides the event.
- Do not use `cards.json` as a per-frame live lookup without caching.
- Do not assume the box `HSAR` replay format is public or compatible with
  `.hsreplay`; the local `HSAR` container is private binary data.
- Do not run native tests or mouse automation without explicit authorization.
- Do not use pip, conda, standalone venv, or system Python. Use `uv` only.
- Do not commit generated caches, logs, screenshots, machine paths, or secrets.

## 9. Current state in this copy

```text
Source branch: feature/R16A-upstream-sync-1027
Current upstream sync: 2026-10-27
Traditional Web UI: preserved in web_ui.py and web/index.html
New layered path: src/hslegend
Battlegrounds implementation: not present yet
Battlegrounds design direction: separate domain/application/adapters context
```

For a new BGS observer, create a new isolated implementation rather than
modifying the traditional state machine. The expected live-state source is
Power.log; the expected first UI is a read-only Tkinter observer or a separate
read-only Web page.


