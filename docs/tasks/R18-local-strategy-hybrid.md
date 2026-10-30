# R18 Local Strategy And Box Hybrid

## Task ID

R18

## Goal

Port the Joint0.1 local deck-strategy engine (`ai/` + `search/`) into the current
R17 layered repository and wire it as an opt-in decision source next to the
existing box-AI flow:

- `hsbox` (default): the current OCR recommendation path, unchanged.
- Local deck: `search.Search` over `StrategyState`, executed through the existing
  adapter/controller (no recommendation-panel OCR required).
- Joint mode: read the box first step, complete the turn with local search, and
  keep the box unless the local line scores 20% higher.
- Local mulligan: the deck strategy's `choose()` drives the existing mulligan
  click/confirm flow.

## Scope

- Add `ai/` (deck strategy registry, shared card rules, simulated draw cards,
  three deck strategies) and `search/` (priority-queue search over `AiState`).
- Add strategy / joint / max-search settings to `ui_config.json` and `config.py`.
- Add the application port `StrategyPlannerPort` and the legacy adapter that
  wraps `search` (anti-corruption package only).
- Extend `RecommendationFlow` with box/local/joint branches while reusing the
  pending-confirmation logic; keep the box path byte-compatible.
- Extend `ProposedAction`/`ActionKind` with the fields the local engine needs.
- Wire `FSM_action.py`, `MulliganFlow.run_local`, the web strategy card and the
  status payload.
- Add fixtures, tests, task/plan/interface docs and README/SKILL updates.

## Non-Goals

- Annotation tooling (`src/annotation/**`, `web/annotation.html`, annotation
  APIs and `annotation_quarantine/`). Joint mode uses the minimal serializer in
  `search/serialize.py` and in-memory stats only.
- `ml/` value network. The RL deck folder runs rules-only; `HSAI_VALUE_CKPT` is
  never required.
- New deck strategies (`脏牧`/`龟法`). New decks are added later by creating an
  `ai/<deck>/` folder per `ai/新建卡组说明.txt`.
- No `cards.json`, `uv.lock` or `pyproject.toml` changes.
- No changes to existing click coordinates or delay thresholds.

## Allowed Files

- `ai/**`
- `search/**`
- `config.py`
- `FSM_action.py`
- `strategy.py`
- `strategy_entity.py`
- `log_state.py`
- `json_op.py`
- `src/flow/mulligan_flow.py`
- `src/hslegend/domain/**`
- `src/hslegend/application/**`
- `src/hslegend/adapters/**`
- `web_ui.py`
- `web/index.html`
- `tests/**`
- `tools/check_task_scope.py` (non-ASCII path quoting fix for `ai/` folders)
- `docs/tasks/R18-*.md`
- `docs/plans/**`
- `docs/interfaces.md`
- `docs/architecture/**`
- `docs/agent/**`
- `README.md`
- `SKILL.md`
- `AGENT_CODE_MAP.md`
- `.gitignore`

## Forbidden Files

- `cards.json`
- `pyproject.toml`
- `uv.lock`
- `assets/**`
- `src/annotation/**`
- `annotation_quarantine/**`
- `src/play_control.py`
- `manual_controller.py`, `click.py` (expected unchanged; raise the task scope if
  an execution gap is proven)

## Acceptance

```powershell
uv sync --locked --all-groups
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/check_architecture.py --self-test
uv run python tools/check_card_rules.py --check
uv run python tools/check_task_scope.py --task R18
uv run python tools/check_task_scope.py --self-test
uv run python tools/run_headless_tests.py
uv run python tools/run_headless_tests.py --opt-in
```

- Default `strategy=hsbox` keeps the box path and every existing test unchanged.
- Selecting a local deck runs the search path, logs `[推荐] ... 最佳评分`, and
  drives the local mulligan without the recommendation panel.
- Joint mode logs `[联合]` and appends one summary line to `ai.txt` per game.
- The zoo turn-4 fixture resolves to `play_card TIME_025` (hand slot 6).
- `Ctrl+Q` aborts a running search in under a second; the search budget is
  bounded by `max_search` and `search_time_limit_seconds`.
- Architecture checks stay green: the application layer does not import root
  packages; only `adapters/legacy` does.

## Rollback

- `strategy` defaults to `hsbox`; removing the key from `ui_config.json` returns
  to the box path.
- The ported stack is inert unless a local strategy is selected.
- Revert the R18 commits as a unit; no data or schema migration is involved.
