# R17 Recommendation Semantics And Layering

## Task ID

R17

## Goal

Move card semantics and recommendation adaptation into domain/application/adapters while preserving the existing Web/FSM entrypoint and every successful action behavior.

## Base

- Branch: `feature/R17-recommendation-semantics`
- Base SHA: `47b8a36702086a13f0c82b9f63165bde16579eea`

## Scope

- Extend pure domain card semantics and shared name comparison.
- Move recommendation orchestration into the application layer.
- Move card metadata, OCR parsing, recommendation adaptation, evidence, and legacy click action DTOs into adapters.
- Keep root recommendation modules as thin compatibility shims during Web/FSM migration.
- Add a declarative card rule pack for measured facts that cannot be derived from `cards.json`.
- Make `FSM_action.py` compose the new adapter/controller and preserve the production entrypoint.
- Add behavior-compatibility tests, rules audit tooling, architecture checks, and task records.

## Non-Goals

- No Web UI, HTTP, CLI, MCP, configuration, or Power.log contract changes.
- No BGS feature work or new entrypoint cutover.
- No coordinate, delay, threshold, or click-order changes for existing success paths.
- No heuristic or hard-coded card-id fallback.

## Allowed Files

- `FSM_action.py`
- `README.md`
- `config.py`
- `screen_regions.py`
- `manual_controller.py`
- `src/**`
- `rules/**`
- `.gitignore`
- `tests/**`
- `tools/**`
- `docs/interfaces.md`
- `docs/architecture/**`
- `docs/agent/**`
- `docs/tasks/R17-*.md`
- `AGENT_CODE_MAP.md`
- `TOTAL_TASK.md`

## Forbidden Files

- `cards.json`
- `web_ui.py`
- `click.py`
- `log_state.py`
- `pyproject.toml`
- `uv.lock`
- `web/**`
- `assets/**`

## Acceptance

```powershell
uv run python -m compileall -q src tests tools
uv run python tools/check_architecture.py
uv run python tools/check_architecture.py --self-test
uv run python tools/check_card_rules.py --check
uv run python tools/check_task_scope.py --task R17
uv run python tools/run_headless_tests.py
uv run python tools/run_headless_tests.py --opt-in
uv run python tools/run_headless_tests.py --native-live
```

- All existing successful action paths keep their click sequences and identity checks.
- Domain imports only stdlib and `hslegend.domain`.
- Application imports only domain, application ports/DTOs, and stdlib.
- Adapters own filesystem, OCR evidence, legacy action DTOs, and chart translation.
- Missing/conflicting card rules fail closed before the first click.
- `cards.json` remains unchanged.

## Rollback

Each layer extraction has a thin root shim, so the branch can be reverted as one R17 commit without changing external callers.

## Follow-up: Rank OCR

Field logs showed a confident label-only OCR result (`1` line, confidence `1.00`)
but no digit, followed by “按还没上传说处理”. The rank reader discarded the raw
text and did not retry with enhanced image preprocessing. The follow-up keeps the
selection-screen rank contract, preserves raw OCR evidence, retries grayscale and
binary variants, and clarifies that calibration happens on the deck-selection
screen rather than the result screen.

## Follow-up: No Per-Recommendation Circuit Breaker

Field behavior showed that long opponent end-turn animations can leave a stale
recommendation visible after control returns. The former ten-failure circuit
breaker could then suppress all retries for that turn. The same-recommendation
failure counter and `recommendation_stuck` observation state are removed.
Failures always remain `RETRY`; raw OCR evidence remains deduplicated per
unreadable episode. Turn-level deadlines, stop controls, and Hearthstone
liveness checks remain the only mechanisms that can end the loop.
