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
- `battle_record.py`
- `feedback.py`
- `README.md`
- `config.py`
- `calibrate_roi.py`
- `click.py`
- `screen_regions.py`
- `strategy.py`
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

## Follow-up: Attack Target Name Recovery

A 2026-10-29 failure bundle showed a valid attack recommendation where OCR
retained `操作5号位随从攻击`, the source card name, and the target name
`萨格拉斯`, but dropped the middle `目标是对方英雄` line. The parser now keeps
the trailing target name when at least source and target names are present.
The adapter resolves it only when exactly one enemy hero/minion name matches;
zero or multiple matches still fail closed.

## Follow-up: Secondary Hero Power And Confirmation

`JAIL_446hp` (“吸血鬼之吻”) is declared in the rule pack as the fixed
`secondary` hero-power slot; no ordering heuristic is used. Its calibrated
client-relative button point defaults to `(1235, 730)` and can be overridden in
the `battle_click.hero_power_secondary` section of `ui_config.json`.

Dispatched recommendations now enter a pending confirmation state when the
immediate result is not observable. The same recommendation is never replayed
while pending. Ctrl+Q uses the shared shutdown event and is checked between
coordinate clicks and cancellable delays.

## Follow-up: Bounded Pending Confirmation

Field evidence on 2026-10-30: an attack click sent while the client was still
animating the previous attack was dropped by the client (`You clicked on
something that is not being handled by InputManager`), so `Power.log` never
showed the attack. The pending state then waited forever, re-reading the same
recommendation and logging the same lines every 0.3s until the turn ended.

Pending confirmation is now bounded: if the same recommendation is still on
screen three seconds after the pending state starts and the action's
postcondition is not observable, the consumption mark is discarded and the
action is re-dispatched (at most two retries). This is safe because the click
was demonstrably not processed; a click that did land leaves `Power.log` or
panel evidence within that window. After the retry budget is spent the flow
keeps waiting for the panel update without re-clicking.

The repeated log lines are also deduplicated: the recommendation is printed
once per pending episode, and `FSM_action` stops reporting `my_turn` while a
pending action exists so `awaiting_action_confirmation` keeps its diagnostic
dedupe.


## Follow-up: Rewind, Assembly, And Feedback Noise

- Environment self-check diagnostics classify as informational and never create
  feedback incidents.
- Feedback incidents are recorded only while in a player/opponent turn or on the
  post-game screen; pre-game and mulligan events are ignored.
- Cards with Rewind metadata wait three seconds after being played before the
  next target or option is selected.
- Duplicate board positions left by a rewind timeline keep the newer live entity.
- Assembly hints `左二中一/左一中二/右二中一/右一中二` map to existing
  four-choice positions `2/1/4/3`.
