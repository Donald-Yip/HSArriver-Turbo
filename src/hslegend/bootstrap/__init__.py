"""Composition-root entry points."""

from hslegend.application.use_cases.finish_battle import (
    FinishBattleCommand,
    FinishBattleResult,
    FinishBattleStatus,
    FinishBattleUseCase,
)
from hslegend.application.use_cases.mulligan import (
    MulliganCommand,
    MulliganInstructionError,
    MulliganResult,
    MulliganStatus,
    MulliganUseCase,
)
from hslegend.application.use_cases.turn import (
    PlayTurnCommand,
    PlayTurnResult,
    PlayTurnStatus,
    PlayTurnUseCase,
)
from hslegend.application.use_cases.turn_intent import (
    TurnIntentError,
    TurnIntentParser,
    parse_turn_intent,
)

from .container import (
    Container,
    EnterBattleCommand,
    build_container,
    build_enter_battle_use_case,
    build_finish_battle_use_case,
    build_mulligan_use_case,
    build_play_turn_use_case,
    build_turn_use_case,
)

__all__ = [
    "Container",
    "EnterBattleCommand",
    "FinishBattleCommand",
    "FinishBattleResult",
    "FinishBattleStatus",
    "FinishBattleUseCase",
    "MulliganCommand",
    "MulliganInstructionError",
    "MulliganResult",
    "MulliganStatus",
    "MulliganUseCase",
    "PlayTurnCommand",
    "PlayTurnResult",
    "PlayTurnStatus",
    "PlayTurnUseCase",
    "TurnIntentError",
    "TurnIntentParser",
    "build_container",
    "build_enter_battle_use_case",
    "build_finish_battle_use_case",
    "build_mulligan_use_case",
    "build_play_turn_use_case",
    "build_turn_use_case",
    "parse_turn_intent",
]
