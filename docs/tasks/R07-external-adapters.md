# R07 External-System Adapters

## Task ID

R07

## Goal

Wrap existing HSAng, Power.log, lobby recognition, and click systems behind the application ports without changing their current behavior.

## Scope

- `HsangRecommendationAdapter`: injected capture/read/parse callables -> RecommendationObservation.
- `PowerLogGameStateAdapter`: injected snapshot/revision provider -> GameObservation and revision wait.
- `LegacyLobbyStateAdapter`: injected lobby recognizer -> LobbyState.
- `LegacyActionDispatch`: injected semantic action handlers -> DispatchResult; no direct clicks in application.
- Contract tests with fakes and fixed data; no real game required by default.

## Non-Goals

- No one-game use case/state machine.
- No UI/CLI/web changes.
- No coordinate, delay, OCR model, or click-sequence changes.
- No production legacy refactor beyond adapter seams.

## Allowed Files

- `src/hslegend/adapters/hsang/**`
- `src/hslegend/adapters/powerlog/**`
- `src/hslegend/adapters/lobby/**`
- `src/hslegend/adapters/input/**`
- `tests/adapters/**`
- `docs/interfaces.md`
- `docs/architecture/**`
- `docs/tasks/R07-*.md`
- `docs/agent/**`
- `TOTAL_TASK.md`

## Forbidden Files

- `FSM_action.py`, `web_ui.py`, `manual_controller.py`, `click.py` unless a minimal adapter import is explicitly reviewed.
- Domain/application/plugins except approved bug fix.
- User configuration, card data, OCR models.

## Acceptance

- Adapters are constructible with injected functions and do not require real Hearthstone in tests.
- Legacy errors become typed port errors/results.
- Recommendation observations carry instruction, confidence, evidence ID, and snapshot ID.
- Game observations preserve snapshot/revision semantics and wait cancellation.
- Lobby mapping is deterministic and unknown states fail closed.
- Action dispatch routes known semantic names through handlers and rejects unknown handlers without clicking.
- Headless/architecture/scope/domain/application/plugins/adapters gates pass.

## Risk And Rollback

- Risk: legacy signatures differ across modules.
- Mitigation: all legacy calls are injected or wrapped behind narrow adapters.
- Rollback: delete R07 adapter packages/tests; R06 data adapters and core layers remain intact.

## Git Requirement

```text
feat(R07): adapt legacy perception and action systems
```

## Report Paths

- Implementer: `docs/agent/reports/R07-implementer.md`
- Reviewer: `docs/agent/reviews/R07-review.md`
