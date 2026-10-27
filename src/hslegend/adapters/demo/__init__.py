"""Deterministic one-game demo adapters with no external side effects."""

from __future__ import annotations

from dataclasses import dataclass

from hslegend.application.dto import (
    DispatchResult,
    GameObservation,
    JournalAppendResult,
    LobbyPhase,
    LobbyState,
    RecommendationMode,
    RecommendationObservation,
)
from hslegend.application.ports import (
    ActionDispatchPort,
    CardCatalogPort,
    GameStateSourcePort,
    LobbyStatePort,
    RecommendationSourcePort,
    RunJournalPort,
)
from hslegend.domain import (
    CardDefinition,
    ChoiceView,
    EntityRef,
    GameSnapshot,
    PlanStep,
    PlayerView,
)


def _snapshot(
    *,
    snapshot_id: str,
    revision: int,
    turn_number: int,
    is_my_turn: bool,
    is_end: bool,
    with_mulligan: bool = False,
) -> GameSnapshot:
    friendly_hero = EntityRef(1, "demo-hero-friendly", "friendly", "hero", 0)
    opponent_hero = EntityRef(2, "demo-hero-opponent", "opponent", "hero", 0)
    hand = tuple(
        EntityRef(100 + index, f"demo-hand-{index}", "friendly", "hand", index)
        for index in range(1, 4)
    )
    choices = (
        ChoiceView(
            choice_id="demo-mulligan",
            options=(),
            choice_type="mulligan",
            min_choices=0,
            max_choices=3,
        ),
    ) if with_mulligan else ()
    return GameSnapshot(
        schema_version=1,
        snapshot_id=snapshot_id,
        log_revision=revision,
        game_generation=42,
        observed_at=float(revision),
        turn_number=turn_number,
        is_my_turn=is_my_turn,
        is_end=is_end,
        my_player=PlayerView(
            player_id=10,
            side="friendly",
            hero=friendly_hero,
            mana=1 if turn_number else 0,
            hand=hand,
        ),
        opponent=PlayerView(
            player_id=20,
            side="opponent",
            hero=opponent_hero,
        ),
        choices=choices,
    )


class DemoLobbyState(LobbyStatePort):
    """A lobby phase that advances only when a semantic dispatch occurs."""

    def __init__(self) -> None:
        self.phase = LobbyPhase.MAIN_MENU
        self.calls: list[float] = []

    def get_state(self, *, timeout_seconds: float) -> LobbyState:
        self.calls.append(timeout_seconds)
        return LobbyState(
            phase=self.phase,
            observed_at=float(len(self.calls)),
            confidence=1.0,
            evidence_id=f"lobby-{self.phase.value}",
        )

    def transition(self, phase: LobbyPhase) -> None:
        self.phase = phase


class DemoGameState(GameStateSourcePort):
    """A single authoritative snapshot advanced by demo dispatches."""

    def __init__(self, initial: GameSnapshot) -> None:
        self._observation = GameObservation(initial, 1.0)
        self.current_calls = 0
        self.wait_calls: list[tuple[int, float]] = []

    def current(self) -> GameObservation:
        self.current_calls += 1
        return self._observation

    def wait_for_revision_change(
        self,
        after_revision: int,
        timeout_seconds: float,
    ) -> GameObservation | None:
        self.wait_calls.append((after_revision, timeout_seconds))
        if self._observation.snapshot.log_revision <= after_revision:
            return None
        return self._observation

    def replace(self, snapshot: GameSnapshot) -> None:
        self._observation = GameObservation(snapshot, 1.0)


class DemoRecommendationSource(RecommendationSourcePort):
    """Stable recommendations for the mulligan and end-turn surfaces."""

    def __init__(self) -> None:
        self.modes: list[RecommendationMode] = []

    def recommend(
        self,
        snapshot: GameSnapshot,
        mode: RecommendationMode,
    ) -> RecommendationObservation:
        self.modes.append(mode)
        if mode is RecommendationMode.MULLIGAN:
            instruction = "保留全部卡牌"
        elif mode is RecommendationMode.TURN:
            instruction = "结束回合"
        else:
            raise ValueError(f"unsupported demo recommendation mode: {mode}")
        return RecommendationObservation(
            mode=mode,
            instruction=instruction,
            confidence=1.0,
            evidence_id=f"recommendation-{mode.value}",
            snapshot_id=snapshot.snapshot_id,
        )


class DemoCardCatalog(CardCatalogPort):
    """The deterministic path needs no card metadata for end-turn/mulligan."""

    def get_card(self, card_id: str) -> CardDefinition | None:
        return None


class DemoJournal(RunJournalPort):
    """In-memory durable journal for the presentation-free demo path."""

    def __init__(self) -> None:
        self.records: list[object] = []

    def append(self, record) -> JournalAppendResult:
        self.records.append(record)
        record_id = f"journal-{len(self.records)}"
        return JournalAppendResult(
            record_id=record_id,
            durable=True,
            evidence_ids=(record_id, *record.evidence_ids),
        )


class DemoActionDispatch(ActionDispatchPort):
    """Apply exactly the five scripted actions and nothing else."""

    def __init__(self, lobby: DemoLobbyState, state: DemoGameState) -> None:
        self._lobby = lobby
        self._state = state
        self.calls: list[PlanStep] = []
        self._mulligan = _snapshot(
            snapshot_id="demo-mulligan-snapshot",
            revision=1,
            turn_number=0,
            is_my_turn=False,
            is_end=False,
            with_mulligan=True,
        )
        self._turn = _snapshot(
            snapshot_id="demo-turn-snapshot",
            revision=2,
            turn_number=1,
            is_my_turn=True,
            is_end=False,
        )
        self._result = _snapshot(
            snapshot_id="demo-result-snapshot",
            revision=3,
            turn_number=1,
            is_my_turn=False,
            is_end=True,
        )

    @property
    def action_types(self) -> tuple[str, ...]:
        return tuple(step.intent.action_type for step in self.calls)

    def dispatch(
        self,
        step: PlanStep,
        snapshot: GameSnapshot,
        *,
        timeout_seconds: float,
    ) -> DispatchResult:
        del snapshot, timeout_seconds
        self.calls.append(step)
        action = step.intent.action_type
        if action == "enter_traditional":
            self._lobby.transition(LobbyPhase.DECK_SELECT)
        elif action == "select_deck":
            self._lobby.transition(LobbyPhase.DECK_SELECT)
        elif action == "start_match":
            self._lobby.transition(LobbyPhase.IN_GAME)
            self._state.replace(self._mulligan)
        elif action == "mulligan_confirm":
            self._lobby.transition(LobbyPhase.IN_GAME)
            self._state.replace(self._turn)
        elif action == "end_turn":
            self._lobby.transition(LobbyPhase.RESULT)
            self._state.replace(self._result)
        else:
            return DispatchResult.failed(
                "unsupported_demo_action",
                f"dispatch-unknown-{len(self.calls)}",
            )
        dispatch_id = f"dispatch-{action}-{len(self.calls)}"
        return DispatchResult.sent(dispatch_id, dispatch_id)


@dataclass(frozen=True, slots=True)
class DemoScenario:
    """All deterministic ports for one fake game."""

    lobby: DemoLobbyState
    state: DemoGameState
    dispatch: DemoActionDispatch
    recommendation: DemoRecommendationSource
    catalog: DemoCardCatalog
    journal: DemoJournal

    @classmethod
    def build(cls) -> "DemoScenario":
        lobby = DemoLobbyState()
        initial = _snapshot(
            snapshot_id="demo-initial-snapshot",
            revision=0,
            turn_number=0,
            is_my_turn=False,
            is_end=False,
        )
        state = DemoGameState(initial)
        return cls(
            lobby=lobby,
            state=state,
            dispatch=DemoActionDispatch(lobby, state),
            recommendation=DemoRecommendationSource(),
            catalog=DemoCardCatalog(),
            journal=DemoJournal(),
        )


__all__ = [
    "DemoActionDispatch",
    "DemoCardCatalog",
    "DemoGameState",
    "DemoJournal",
    "DemoLobbyState",
    "DemoRecommendationSource",
    "DemoScenario",
]
