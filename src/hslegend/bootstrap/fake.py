"""Composition factory for the deterministic one-game demo."""

from __future__ import annotations

from dataclasses import dataclass

from hslegend.application.runtime import ActionGate, StopToken
from hslegend.application.use_cases.one_game import OneGameUseCase
from hslegend.adapters.demo import DemoScenario

from .container import (
    build_enter_battle_use_case,
    build_finish_battle_use_case,
    build_mulligan_use_case,
    build_turn_use_case,
)


@dataclass(frozen=True, slots=True)
class FakeOneGameHarness:
    """The real one-game coordinator plus observable deterministic ports."""

    use_case: OneGameUseCase
    scenario: DemoScenario


def build_fake_one_game() -> FakeOneGameHarness:
    """Wire real R08-R11 use cases to deterministic demo ports."""

    scenario = DemoScenario.build()
    gate = ActionGate()
    token = StopToken()

    def clock() -> float:
        return 0.0

    def sleep(_seconds: float) -> None:
        return None

    enter_battle = build_enter_battle_use_case(
        scenario.lobby,
        scenario.dispatch,
        action_gate=gate,
        stop_token=token,
        clock=clock,
        sleep=sleep,
    )
    mulligan = build_mulligan_use_case(
        scenario.recommendation,
        scenario.state,
        scenario.dispatch,
        scenario.journal,
        card_catalog=scenario.catalog,
        action_gate=gate,
        stop_token=token,
        clock=clock,
    )
    turn = build_turn_use_case(
        scenario.recommendation,
        scenario.state,
        scenario.dispatch,
        scenario.journal,
        card_catalog=scenario.catalog,
        action_gate=gate,
        stop_token=token,
        clock=clock,
    )
    finish_battle = build_finish_battle_use_case(
        scenario.lobby,
        stop_token=token,
        clock=clock,
        sleep=sleep,
    )
    use_case = OneGameUseCase(
        enter_battle,
        mulligan,
        turn,
        finish_battle,
        scenario.lobby,
        scenario.state,
        token,
        clock=clock,
        sleep=sleep,
    )
    return FakeOneGameHarness(use_case=use_case, scenario=scenario)


__all__ = ["FakeOneGameHarness", "build_fake_one_game"]
