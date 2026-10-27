"""Application use cases."""

from .finish_battle import (
    FinishBattleCommand,
    FinishBattleResult,
    FinishBattleStatus,
    FinishBattleUseCase,
)
from .enter_battle import (
    LOBBY_SNAPSHOT,
    Clock,
    EnterBattleCommand,
    EnterBattleDispatch,
    EnterBattleResult,
    EnterBattleStatus,
    EnterBattleUseCase,
    LobbyAction,
    Sleeper,
    build_lobby_step,
)
from .mulligan import (
    MulliganCommand,
    MulliganInstructionError,
    MulliganResult,
    MulliganStatus,
    MulliganUseCase,
    build_mulligan_plan,
    parse_mulligan_instruction,
    selected_indexes_for_instruction,
    validate_mulligan_selection,
)
from .turn import (
    PlayTurnCommand,
    PlayTurnResult,
    PlayTurnStatus,
    PlayTurnUseCase,
)
from .turn_intent import TurnIntentError, TurnIntentParser, parse_turn_intent

__all__ = [
    "FinishBattleCommand",
    "FinishBattleResult",
    "FinishBattleStatus",
    "FinishBattleUseCase",
    "MulliganCommand",
    "MulliganInstructionError",
    "MulliganResult",
    "MulliganStatus",
    "MulliganUseCase",
    "build_mulligan_plan",
    "parse_mulligan_instruction",
    "selected_indexes_for_instruction",
    "validate_mulligan_selection",
    "LOBBY_SNAPSHOT",
    "Clock",
    "EnterBattleCommand",
    "EnterBattleDispatch",
    "EnterBattleResult",
    "EnterBattleStatus",
    "EnterBattleUseCase",
    "LobbyAction",
    "Sleeper",
    "build_lobby_step",
    "PlayTurnCommand",
    "PlayTurnResult",
    "PlayTurnStatus",
    "PlayTurnUseCase",
    "TurnIntentError",
    "TurnIntentParser",
    "parse_turn_intent",
]

from .one_game import (
    OneGameCommand,
    OneGameResult,
    OneGameStatus,
    OneGameUseCase,
)

__all__ = [
    *__all__,
    "OneGameCommand",
    "OneGameResult",
    "OneGameStatus",
    "OneGameUseCase",
]
