"""Composition root for the native R08-R12 one-game graph."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from hslegend.adapters.legacy.ports import NativePorts
from hslegend.application.runtime import ActionGate, StopToken
from hslegend.application.use_cases.one_game import OneGameUseCase

from .container import (
    build_enter_battle_use_case,
    build_finish_battle_use_case,
    build_mulligan_use_case,
    build_turn_use_case,
)


def build_native_one_game(
    *,
    ports_builder: Callable[
        [StopToken, Callable[[], float], Callable[[float], None]], NativePorts
    ]
    | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> OneGameUseCase:
    """Wire adapter ports into the exact same use-case graph as the fake path."""

    if ports_builder is None:
        # Importing the factory imports no legacy module; the factory itself
        # loads the source-tree runtime only when it is actually called.
        from hslegend.adapters.legacy.factory import build_native_ports

        ports_builder = build_native_ports
    if not callable(ports_builder):
        raise TypeError("ports_builder must be callable")
    if not callable(clock):
        raise TypeError("clock must be callable")
    if not callable(sleep):
        raise TypeError("sleep must be callable")

    token = StopToken()
    gate = ActionGate()
    ports: Any = ports_builder(token, clock, sleep)
    if not isinstance(ports, NativePorts):
        raise TypeError("ports_builder must return NativePorts")

    enter_battle = build_enter_battle_use_case(
        ports.lobby_state,
        ports.action_dispatch,
        action_gate=gate,
        stop_token=token,
        clock=clock,
        sleep=sleep,
    )
    mulligan = build_mulligan_use_case(
        ports.recommendation_mulligan,
        ports.game_state,
        ports.action_dispatch,
        ports.journal,
        card_catalog=ports.card_catalog,
        action_gate=gate,
        stop_token=token,
        clock=clock,
    )
    turn = build_turn_use_case(
        ports.recommendation_turn,
        ports.game_state,
        ports.action_dispatch,
        ports.journal,
        card_catalog=ports.card_catalog,
        action_gate=gate,
        stop_token=token,
        clock=clock,
    )
    finish_battle = build_finish_battle_use_case(
        ports.lobby_state,
        stop_token=token,
        clock=clock,
        sleep=sleep,
    )
    return OneGameUseCase(
        enter_battle,
        mulligan,
        turn,
        finish_battle,
        ports.lobby_state,
        ports.game_state,
        token,
        clock=clock,
        sleep=sleep,
    )


__all__ = ["build_native_one_game", "build_native_preflight"]


def build_native_preflight(deck_index: int, *, live_command: str | None = None):
    """Lazily delegate read-only preflight to the legacy adapter package."""

    from hslegend.adapters.legacy.preflight import build_native_preflight as _build

    return _build(deck_index, live_command=live_command)
