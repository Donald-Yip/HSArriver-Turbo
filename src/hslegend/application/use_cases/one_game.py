"""One-game coordination over the existing R08-R11 use cases."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum

from hslegend.application.dto import GameObservation, LobbyPhase, LobbyState
from hslegend.application.ports import GameStateSourcePort, LobbyStatePort
from hslegend.application.runtime import OperationCancelled, StopToken
from hslegend.domain.primitives import PrimitiveMixin

from .enter_battle import (
    EnterBattleCommand,
    EnterBattleResult,
    EnterBattleStatus,
    EnterBattleUseCase,
)
from .finish_battle import (
    FinishBattleCommand,
    FinishBattleResult,
    FinishBattleStatus,
    FinishBattleUseCase,
)
from .mulligan import (
    MulliganCommand,
    MulliganResult,
    MulliganStatus,
    MulliganUseCase,
)
from .turn import (
    PlayTurnCommand,
    PlayTurnResult,
    PlayTurnStatus,
    PlayTurnUseCase,
)

Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class OneGameStatus(str, Enum):
    """Terminal status of exactly one game command."""

    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


def _positive_int(value: int, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int")
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")
    return value


def _non_negative_int(value: int, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an int")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _positive_finite(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{field_name} must be finite and positive")
    return normalized


def _unique_evidence(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class OneGameCommand(PrimitiveMixin):
    """One bounded request to play and settle exactly one game."""

    command_id: str
    deck_index: int = 0
    max_turns: int = 20
    timeout_seconds: float = 900.0
    enter_battle_timeout_seconds: float = 60.0
    mulligan_timeout_seconds: float = 20.0
    turn_timeout_seconds: float = 90.0
    max_actions_per_turn: int = 10
    poll_interval_seconds: float = 0.25

    def __post_init__(self) -> None:
        if not isinstance(self.command_id, str) or not self.command_id.strip():
            raise ValueError("command_id must be non-empty text")
        _non_negative_int(self.deck_index, "deck_index")
        _positive_int(self.max_turns, "max_turns")
        _positive_int(self.max_actions_per_turn, "max_actions_per_turn")
        for field_name in (
            "timeout_seconds",
            "enter_battle_timeout_seconds",
            "mulligan_timeout_seconds",
            "turn_timeout_seconds",
            "poll_interval_seconds",
        ):
            object.__setattr__(
                self,
                field_name,
                _positive_finite(getattr(self, field_name), field_name),
            )


@dataclass(frozen=True, slots=True)
class OneGameResult(PrimitiveMixin):
    """Terminal one-game result with exact child results and ordered evidence."""

    command_id: str
    session_id: str
    status: OneGameStatus
    reason_code: str = ""
    turns_played: int = 0
    actions_played: int = 0
    last_lobby_phase: LobbyPhase = LobbyPhase.UNKNOWN
    evidence_ids: tuple[str, ...] = ()
    enter_battle: EnterBattleResult | None = None
    mulligan: MulliganResult | None = None
    turn_results: tuple[PlayTurnResult, ...] = ()
    finish_battle: FinishBattleResult | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.command_id, str) or not self.command_id.strip():
            raise ValueError("command_id must be non-empty text")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("session_id must be non-empty text")
        if not isinstance(self.status, OneGameStatus):
            object.__setattr__(self, "status", OneGameStatus(self.status))
        if not isinstance(self.last_lobby_phase, LobbyPhase):
            object.__setattr__(
                self,
                "last_lobby_phase",
                LobbyPhase(self.last_lobby_phase),
            )
        _non_negative_int(self.turns_played, "turns_played")
        _non_negative_int(self.actions_played, "actions_played")
        object.__setattr__(
            self,
            "evidence_ids",
            _unique_evidence(self.evidence_ids),
        )
        object.__setattr__(self, "turn_results", tuple(self.turn_results))
        if self.enter_battle is not None and not isinstance(
            self.enter_battle,
            EnterBattleResult,
        ):
            raise TypeError("enter_battle must be an EnterBattleResult or None")
        if self.mulligan is not None and not isinstance(
            self.mulligan,
            MulliganResult,
        ):
            raise TypeError("mulligan must be a MulliganResult or None")
        if any(
            not isinstance(result, PlayTurnResult)
            for result in self.turn_results
        ):
            raise TypeError("turn_results must contain PlayTurnResult values")
        if self.finish_battle is not None and not isinstance(
            self.finish_battle,
            FinishBattleResult,
        ):
            raise TypeError(
                "finish_battle must be a FinishBattleResult or None"
            )


@dataclass(slots=True)
class _RunProgress:
    evidence_ids: list[str] = field(default_factory=list)
    last_lobby_phase: LobbyPhase = LobbyPhase.UNKNOWN
    enter_battle: EnterBattleResult | None = None
    mulligan: MulliganResult | None = None
    turn_results: list[PlayTurnResult] = field(default_factory=list)
    finish_battle: FinishBattleResult | None = None
    turns_played: int = 0
    actions_played: int = 0


class _StopFlow(Exception):
    def __init__(self, status: OneGameStatus, reason_code: str) -> None:
        self.status = status
        self.reason_code = reason_code
        super().__init__(reason_code)


class OneGameUseCase:
    """Coordinate exactly one game without duplicating child policy."""

    def __init__(
        self,
        enter_battle: EnterBattleUseCase,
        mulligan: MulliganUseCase,
        turn: PlayTurnUseCase,
        finish_battle: FinishBattleUseCase,
        lobby: LobbyStatePort,
        state: GameStateSourcePort,
        stop_token: StopToken | None = None,
        *,
        clock: Clock | None = None,
        sleep: Sleeper | None = None,
    ) -> None:
        if not isinstance(enter_battle, EnterBattleUseCase):
            raise TypeError("enter_battle must be an EnterBattleUseCase")
        if not isinstance(mulligan, MulliganUseCase):
            raise TypeError("mulligan must be a MulliganUseCase")
        if not isinstance(turn, PlayTurnUseCase):
            raise TypeError("turn must be a PlayTurnUseCase")
        if not isinstance(finish_battle, FinishBattleUseCase):
            raise TypeError("finish_battle must be a FinishBattleUseCase")
        if not callable(getattr(lobby, "get_state", None)):
            raise TypeError("lobby must provide get_state()")
        if not callable(getattr(state, "current", None)) or not callable(
            getattr(state, "wait_for_revision_change", None)
        ):
            raise TypeError(
                "state must provide current() and wait_for_revision_change()"
            )
        token = stop_token or StopToken()
        if not isinstance(token, StopToken):
            raise TypeError("stop_token must be a StopToken or None")
        clock_source = clock or time.monotonic
        if not callable(clock_source):
            raise TypeError("clock must be callable")
        if sleep is not None and not callable(sleep):
            raise TypeError("sleep must be callable")
        self._enter_battle = enter_battle
        self._mulligan = mulligan
        self._turn = turn
        self._finish_battle = finish_battle
        self._lobby = lobby
        self._state = state
        self._stop_token = token
        self._clock = clock_source
        self._sleep = sleep or time.sleep

    def execute(self, command: OneGameCommand) -> OneGameResult:
        """Run the bounded one-game flow and never start a second game."""

        if not isinstance(command, OneGameCommand):
            raise TypeError("command must be a OneGameCommand")

        progress = _RunProgress()
        try:
            self._raise_if_cancelled()
            started_at = self._now()
            deadline = started_at + command.timeout_seconds
            self._execute_flow(command, progress, deadline)
        except _StopFlow as stop:
            return self._result(command, stop.status, stop.reason_code, progress)
        except OperationCancelled as exc:
            return self._result(
                command,
                OneGameStatus.CANCELLED,
                exc.reason or "cancelled",
                progress,
            )
        except Exception:
            try:
                self._raise_if_cancelled()
            except _StopFlow as stop:
                return self._result(
                    command,
                    stop.status,
                    stop.reason_code,
                    progress,
                )
            return self._result(
                command,
                OneGameStatus.FAILED,
                "orchestration_error",
                progress,
            )
        return self._result(
            command,
            OneGameStatus.COMPLETED,
            "completed",
            progress,
        )

    def _execute_flow(
        self,
        command: OneGameCommand,
        progress: _RunProgress,
        deadline: float,
    ) -> None:
        remaining = self._remaining(deadline)
        enter = self._enter_battle.execute(
            EnterBattleCommand(
                deck_index=command.deck_index,
                start_match=True,
                timeout_seconds=min(
                    command.enter_battle_timeout_seconds,
                    remaining,
                ),
                poll_interval_seconds=command.poll_interval_seconds,
                command_id=f"{command.command_id}:enter-battle",
            )
        )
        progress.enter_battle = enter
        progress.last_lobby_phase = enter.phase
        self._extend_evidence(progress, enter.evidence_ids)
        self._raise_if_cancelled()
        if enter.status is EnterBattleStatus.CANCELLED:
            raise _StopFlow(
                OneGameStatus.CANCELLED,
                enter.reason_code or "cancelled",
            )
        if enter.status is EnterBattleStatus.TIMED_OUT:
            self._timeout_or_failure(deadline, "enter_battle_timeout")
        if enter.status is not EnterBattleStatus.COMPLETED:
            self._fail(enter.reason_code or "enter_battle_failed")

        if enter.phase is LobbyPhase.RESULT:
            self._settle(command, progress, deadline)
            return

        phase = self._wait_for_phase(
            command,
            progress,
            deadline,
            {LobbyPhase.IN_GAME, LobbyPhase.RESULT},
        )
        if phase is LobbyPhase.RESULT:
            self._settle(command, progress, deadline)
            return

        remaining = self._remaining(deadline)
        mulligan = self._mulligan.execute(
            MulliganCommand(
                command_id=f"{command.command_id}:mulligan",
                timeout_seconds=min(
                    command.mulligan_timeout_seconds,
                    remaining,
                ),
            )
        )
        progress.mulligan = mulligan
        self._extend_evidence(progress, mulligan.evidence_ids)
        self._raise_if_cancelled()
        if mulligan.status is MulliganStatus.CANCELLED:
            raise _StopFlow(OneGameStatus.CANCELLED, mulligan.reason_code or "cancelled")
        if mulligan.status is not MulliganStatus.COMPLETED:
            self._fail(mulligan.reason_code or "mulligan_failed")

        self._play_until_game_end(command, progress, deadline)
        self._settle(command, progress, deadline)

    def _wait_for_phase(
        self,
        command: OneGameCommand,
        progress: _RunProgress,
        deadline: float,
        accepted: set[LobbyPhase],
    ) -> LobbyPhase:
        while True:
            self._check_running(deadline)
            remaining = self._remaining(deadline)
            try:
                state = self._lobby.get_state(timeout_seconds=remaining)
            except TimeoutError:
                self._timeout_or_failure(deadline, "lobby_state_timeout")
            except OperationCancelled as exc:
                raise _StopFlow(
                    OneGameStatus.CANCELLED,
                    exc.reason or "cancelled",
                ) from None
            except Exception:
                self._fail("lobby_state_error")
            if not isinstance(state, LobbyState):
                self._fail("lobby_state_invalid")
            progress.last_lobby_phase = state.phase
            self._extend_evidence(progress, (state.evidence_id,))
            if state.phase in accepted:
                return state.phase
            remaining = self._remaining(deadline)
            try:
                self._sleep(min(command.poll_interval_seconds, remaining))
            except OperationCancelled as exc:
                raise _StopFlow(
                    OneGameStatus.CANCELLED,
                    exc.reason or "cancelled",
                ) from None
            except Exception:
                self._fail("sleep_error")

    def _play_until_game_end(
        self,
        command: OneGameCommand,
        progress: _RunProgress,
        deadline: float,
    ) -> None:
        observation = self._current_observation(progress, deadline)
        expected_generation = observation.snapshot.game_generation
        delegated: set[tuple[int, int]] = set()
        while True:
            snapshot = observation.snapshot
            if snapshot.game_generation != expected_generation:
                self._fail("game_generation_changed")
            if snapshot.is_end:
                return

            turn_key = (snapshot.game_generation, snapshot.turn_number)
            if not snapshot.is_my_turn or turn_key in delegated:
                observation = self._wait_for_revision(
                    progress,
                    snapshot.log_revision,
                    expected_generation,
                    deadline,
                )
                continue
            if progress.turns_played >= command.max_turns:
                self._fail("max_turns_reached")

            remaining = self._remaining(deadline)
            turn = self._turn.execute(
                PlayTurnCommand(
                    command_id=f"{command.command_id}:turn:{progress.turns_played + 1}",
                    max_actions=command.max_actions_per_turn,
                    timeout_seconds=min(command.turn_timeout_seconds, remaining),
                )
            )
            progress.turns_played += 1
            progress.actions_played += turn.actions
            progress.turn_results.append(turn)
            delegated.add(turn_key)
            self._extend_evidence(progress, turn.evidence_ids)
            self._raise_if_cancelled()
            if turn.status is PlayTurnStatus.CANCELLED:
                raise _StopFlow(OneGameStatus.CANCELLED, turn.reason_code or "cancelled")
            if turn.status is PlayTurnStatus.COMPLETED:
                self._fail("turn_action_budget_exhausted")
            if turn.status is not PlayTurnStatus.ENDED:
                self._fail(turn.reason_code or "turn_failed")
            if turn.reason_code == "game_ended":
                return
            if turn.reason_code not in {"local_turn_ended", "end_turn"}:
                self._fail(turn.reason_code or "turn_ended")

            observation = self._current_observation(progress, deadline)

    def _settle(
        self,
        command: OneGameCommand,
        progress: _RunProgress,
        deadline: float,
    ) -> None:
        self._check_running(deadline)
        remaining = self._remaining(deadline)
        finish = self._finish_battle.execute(
            FinishBattleCommand(
                command_id=f"{command.command_id}:finish-battle",
                timeout_seconds=remaining,
                poll_interval_seconds=command.poll_interval_seconds,
            )
        )
        progress.finish_battle = finish
        progress.last_lobby_phase = finish.phase
        self._extend_evidence(progress, finish.evidence_ids)
        self._raise_if_cancelled()
        if finish.status is FinishBattleStatus.CANCELLED:
            raise _StopFlow(OneGameStatus.CANCELLED, finish.reason_code or "cancelled")
        if finish.status is FinishBattleStatus.TIMED_OUT:
            self._timeout_or_failure(deadline, "finish_battle_timeout")
        if finish.status is not FinishBattleStatus.COMPLETED:
            self._fail(finish.reason_code or "finish_battle_failed")

    def _current_observation(
        self,
        progress: _RunProgress,
        deadline: float,
    ) -> GameObservation:
        self._check_running(deadline)
        try:
            observation = self._state.current()
        except OperationCancelled as exc:
            raise _StopFlow(
                OneGameStatus.CANCELLED,
                exc.reason or "cancelled",
            ) from None
        except Exception:
            self._fail("game_state_error")
        if not isinstance(observation, GameObservation):
            self._fail("game_state_invalid")
        self._extend_evidence(progress, (observation.snapshot.snapshot_id,))
        return observation

    def _wait_for_revision(
        self,
        progress: _RunProgress,
        after_revision: int,
        game_generation: int,
        deadline: float,
    ) -> GameObservation:
        self._check_running(deadline)
        remaining = self._remaining(deadline)
        try:
            observation = self._state.wait_for_revision_change(
                after_revision,
                remaining,
            )
        except TimeoutError:
            self._timeout_or_failure(deadline, "game_state_timeout")
        except OperationCancelled as exc:
            raise _StopFlow(
                OneGameStatus.CANCELLED,
                exc.reason or "cancelled",
            ) from None
        except Exception:
            self._fail("game_state_error")
        if observation is None:
            self._timeout_or_failure(deadline, "game_state_timeout")
        if not isinstance(observation, GameObservation):
            self._fail("game_state_invalid")
        if observation.snapshot.log_revision <= after_revision:
            self._fail("stale_game_state")
        if observation.snapshot.game_generation != game_generation:
            self._fail("game_generation_changed")
        self._extend_evidence(progress, (observation.snapshot.snapshot_id,))
        return observation

    def _check_running(self, deadline: float) -> None:
        self._raise_if_cancelled()
        self._remaining(deadline)

    def _raise_if_cancelled(self) -> None:
        if self._stop_token.is_cancelled():
            raise _StopFlow(
                OneGameStatus.CANCELLED,
                self._stop_token.reason or "cancelled",
            )

    def _now(self) -> float:
        try:
            value = self._clock()
        except OperationCancelled as exc:
            raise _StopFlow(
                OneGameStatus.CANCELLED,
                exc.reason or "cancelled",
            ) from None
        except Exception:
            self._fail("clock_error")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self._fail("clock_error")
        normalized = float(value)
        if not math.isfinite(normalized):
            self._fail("clock_error")
        return normalized

    def _timeout_or_failure(self, deadline: float, reason_code: str) -> None:
        self._raise_if_cancelled()
        now = self._now()
        if now >= deadline:
            raise _StopFlow(OneGameStatus.TIMED_OUT, "total_timeout")
        self._fail(reason_code)

    def _fail(self, reason_code: str) -> None:
        self._raise_if_cancelled()
        raise _StopFlow(OneGameStatus.FAILED, reason_code)

    def _remaining(self, deadline: float) -> float:
        self._raise_if_cancelled()
        remaining = deadline - self._now()
        if remaining <= 0.0:
            raise _StopFlow(OneGameStatus.TIMED_OUT, "total_timeout")
        return remaining

    def _extend_evidence(
        self,
        progress: _RunProgress,
        evidence_ids: Iterable[str],
    ) -> None:
        progress.evidence_ids.extend(evidence_ids)

    def _result(
        self,
        command: OneGameCommand,
        status: OneGameStatus,
        reason_code: str,
        progress: _RunProgress,
    ) -> OneGameResult:
        return OneGameResult(
            command_id=command.command_id,
            session_id=command.command_id,
            status=status,
            reason_code=reason_code,
            turns_played=progress.turns_played,
            actions_played=progress.actions_played,
            last_lobby_phase=progress.last_lobby_phase,
            evidence_ids=tuple(progress.evidence_ids),
            enter_battle=progress.enter_battle,
            mulligan=progress.mulligan,
            turn_results=tuple(progress.turn_results),
            finish_battle=progress.finish_battle,
        )


__all__ = [
    "Clock",
    "OneGameCommand",
    "OneGameResult",
    "OneGameStatus",
    "OneGameUseCase",
    "Sleeper",
]
