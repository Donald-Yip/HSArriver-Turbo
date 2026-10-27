"""Minimal composition root for the enter-battle vertical slice."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from hslegend.application.ports import ActionDispatchPort, LobbyStatePort
from hslegend.application.runtime import ActionGate, StopToken
from hslegend.application.use_cases.enter_battle import (
    EnterBattleCommand,
    EnterBattleUseCase,
)
from hslegend.domain import GameSnapshot


@dataclass(frozen=True, slots=True)
class Container:
    """Application services wired from injected ports and runtime controls."""

    enter_battle: EnterBattleUseCase

    def __post_init__(self) -> None:
        if not isinstance(self.enter_battle, EnterBattleUseCase):
            raise TypeError("enter_battle must be an EnterBattleUseCase")

    @property
    def enter_battle_use_case(self) -> EnterBattleUseCase:
        """Explicit alias for readers that prefer the full service name."""

        return self.enter_battle


def build_container(
    lobby: LobbyStatePort,
    dispatch: ActionDispatchPort,
    *,
    action_gate: ActionGate | None = None,
    stop_token: StopToken | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] | None = None,
    lobby_snapshot: GameSnapshot | None = None,
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
    return Container(enter_battle=use_case)


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
    "EnterBattleCommand",
]
