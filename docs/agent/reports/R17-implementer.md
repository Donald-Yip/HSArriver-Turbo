# R17 implementer report

## Status

Implementation complete; final repository review and commit remain.

## Base

- Branch: `feature/R17-recommendation-semantics`
- Base SHA: `47b8a36702086a13f0c82b9f63165bde16579eea`

## Changed Areas

- Pure domain card semantics: target scope, target card types, choose-one children, ability panels, shared name normalization.
- Application recommendation contracts and ports.
- Adapter-owned card catalog/rule loading, rule audit tool, legacy action DTOs, evidence DTOs, card queries and recommendation adapter.
- Root recommendation modules remain thin compatibility facades.
- `FSM_action.py` now composes `RecommendationAdapter()` and manual controller uses unified card queries.
- Added focused domain/application/adapter contract tests.

## Verification

- Compile: pass.
- Architecture: pass, 66 files; self-test 23/23.
- Card rules: pass, 36390 cards, 1 rule override, 1 ability panel, 130 choose-one cards, 26 hand-target cards.
- Focused recommendation compatibility: 178/178 passed.
- Additional flow/validator compatibility: 203/203 passed.
- Default headless: 1558/1558 passed after the final flow/parser migration.
- Opt-in: 20/20 passed.
- Native live: blocked because no running Hearthstone window; required live test fails closed (`test_live_window_lookup`).

## Notes

- Root `src/game_state`, `src/parser`, `src/flow`, and `manual_controller` imports remain compatibility facades; the production recommendation path in `FSM_action.py` composes application/adapters directly.
- The versioned rule pack is `rules/card_rules.json`; `.gitignore` explicitly unignores that file while keeping other JSON artifacts ignored.

## Risks

- Native live and dedicated-deck live matrix still require an authorized running game.
- Git worktree metadata path is currently not resolving through shell invocations; direct Git validation needs restoration before commit.
