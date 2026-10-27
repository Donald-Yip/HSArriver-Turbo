"""Minimal composition root for the enter-battle vertical slice."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from hslegend.application.planning import (
    ExecutionLedger,
    PlanBuilder,
    PlanExecutor,
    PlanValidator,
)
from hslegend.application.ports import (
    ActionDispatchPort,
    CardCatalogPort,
    GameStateSourcePort,
    LobbyStatePort,
    RecommendationSourcePort,
    RunJournalPort,
)
from hslegend.application.runtime import ActionGate, StopToken
from hslegend.application.use_cases.enter_battle import (
    EnterBattleCommand,
    EnterBattleUseCase,
)
from hslegend.application.use_cases.finish_battle import FinishBattleUseCase
from hslegend.application.use_cases.mulligan import MulliganUseCase
from hslegend.application.use_cases.turn import PlayTurnUseCase, TurnIntentParser
from hslegend.domain import GameSnapshot
from hslegend.plugins import build_default_registry


@dataclass(frozen=True, slots=True)
class Container:
    """Application services wired from injected ports and runtime controls."""

    enter_battle: EnterBattleUseCase
    mulligan: MulliganUseCase | None = None
    turn: PlayTurnUseCase | None = None
    finish_battle: FinishBattleUseCase | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enter_battle, EnterBattleUseCase):
            raise TypeError("enter_battle must be an EnterBattleUseCase")
        if self.mulligan is not None and not isinstance(self.mulligan, MulliganUseCase):
            raise TypeError("mulligan must be a MulliganUseCase or None")
        if self.turn is not None and not isinstance(self.turn, PlayTurnUseCase):
            raise TypeError("turn must be a PlayTurnUseCase or None")
        if self.finish_battle is not None and not isinstance(
            self.finish_battle, FinishBattleUseCase
        ):
            raise TypeError("finish_battle must be a FinishBattleUseCase or None")

    @property
    def enter_battle_use_case(self) -> EnterBattleUseCase:
        """Explicit alias for readers that prefer the full service name."""

        return self.enter_battle

    @property
    def mulligan_use_case(self) -> MulliganUseCase | None:
        """Explicit alias for the optional mulligan service."""

        return self.mulligan

    @property
    def turn_use_case(self) -> PlayTurnUseCase | None:
        """Explicit alias for the optional turn service."""

        return self.turn

    @property
    def finish_battle_use_case(self) -> FinishBattleUseCase | None:
        """Explicit alias for the optional settlement service."""

        return self.finish_battle


def build_container(
    lobby: LobbyStatePort,
    dispatch: ActionDispatchPort,
    *,
    action_gate: ActionGate | None = None,
    stop_token: StopToken | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] | None = None,
    lobby_snapshot: GameSnapshot | None = None,
    mulligan: MulliganUseCase | None = None,
    turn: PlayTurnUseCase | None = None,
    finish_battle: FinishBattleUseCase | None = None,
) -> Container:
    """Build the vertical slice from injected adapters only.

    No legacy module or factory is imported here. Entrypoints are responsible
    for creating real adapters and passing them across this boundary.
    """

    use_case = EnterBattleUseCase(
        lobby=lobby,
        dispatch=dispatch,
        action_gate=action_gate or ActionGate(),
        stop_token=stop_token or StopToken(),
        clock=clock,
        sleep=sleep,
        lobby_snapshot=lobby_snapshot,
    )
    return Container(
        enter_battle=use_case,
        mulligan=mulligan,
        turn=turn,
        finish_battle=finish_battle,
    )


def build_mulligan_use_case(
    recommendation: RecommendationSourcePort,
    state: GameStateSourcePort,
    dispatch: ActionDispatchPort,
    journal: RunJournalPort,
    *,
    card_catalog: CardCatalogPort | None = None,
    validator: PlanValidator | None = None,
    ledger: ExecutionLedger | None = None,
    action_gate: ActionGate | None = None,
    stop_token: StopToken | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> MulliganUseCase:
    """Build the mulligan service from injected ports, without legacy imports."""

    if validator is None:
        if card_catalog is None or not callable(getattr(card_catalog, "get_card", None)):
            raise TypeError("card_catalog must provide get_card() when validator is omitted")
        validator = PlanValidator(card_catalog)
    ledger = ledger or ExecutionLedger()
    executor = PlanExecutor(
        dispatch,
        state,
        journal,
        ledger,
        validator,
        clock=clock,
    )
    return MulliganUseCase(
        recommendation,
        state,
        action_gate or ActionGate(),
        stop_token or StopToken(),
        executor,
    )


def build_turn_use_case(
    recommendation: RecommendationSourcePort,
    state: GameStateSourcePort,
    dispatch: ActionDispatchPort,
    journal: RunJournalPort,
    *,
    card_catalog: CardCatalogPort | None = None,
    builder: PlanBuilder | None = None,
    validator: PlanValidator | None = None,
    ledger: ExecutionLedger | None = None,
    action_gate: ActionGate | None = None,
    stop_token: StopToken | None = None,
    clock: Callable[[], float] = time.monotonic,
    parser: TurnIntentParser | None = None,
) -> PlayTurnUseCase:
    """Build the bounded turn service from injected ports and default plugins."""

    if builder is None:
        if card_catalog is None or not callable(getattr(card_catalog, "get_card", None)):
            raise TypeError("card_catalog must provide get_card() when builder is omitted")
        builder = PlanBuilder(build_default_registry(), card_catalog)
    if validator is None:
        if card_catalog is None or not callable(getattr(card_catalog, "get_card", None)):
            raise TypeError("card_catalog must provide get_card() when validator is omitted")
        validator = PlanValidator(card_catalog)
    ledger = ledger or ExecutionLedger()
    executor = PlanExecutor(
        dispatch,
        state,
        journal,
        ledger,
        validator,
        clock=clock,
    )
    return PlayTurnUseCase(
        recommendation,
        state,
        builder,
        executor,
        action_gate or ActionGate(),
        stop_token or StopToken(),
        clock=clock,
        parser=parser,
    )


def build_play_turn_use_case(*args, **kwargs) -> PlayTurnUseCase:
    """Explicit-name alias for callers that prefer the full use-case name."""

    return build_turn_use_case(*args, **kwargs)


def build_finish_battle_use_case(
    lobby: LobbyStatePort,
    *,
    stop_token: StopToken | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] | None = None,
) -> FinishBattleUseCase:
    """Build the observation-only settlement service."""

    return FinishBattleUseCase(
        lobby,
        stop_token or StopToken(),
        clock,
        sleep=sleep,
    )


def build_enter_battle_use_case(
    lobby: LobbyStatePort,
    dispatch: ActionDispatchPort,
    *,
    action_gate: ActionGate | None = None,
    stop_token: StopToken | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] | None = None,
    lobby_snapshot: GameSnapshot | None = None,
) -> EnterBattleUseCase:
    """Compatibility helper returning the use case directly."""

    return build_container(
        lobby=lobby,
        dispatch=dispatch,
        action_gate=action_gate,
        stop_token=stop_token,
        clock=clock,
        sleep=sleep,
        lobby_snapshot=lobby_snapshot,
    ).enter_battle


__all__ = [
    "Container",
    "build_container",
    "build_enter_battle_use_case",
    "build_finish_battle_use_case",
    "build_mulligan_use_case",
    "build_play_turn_use_case",
    "build_turn_use_case",
    "EnterBattleCommand",
]
