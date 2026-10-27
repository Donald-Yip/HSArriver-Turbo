"""Semantic enter-battle use case with bounded lobby polling."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum

from hslegend.application.dto import (
    DispatchResult,
    DispatchStatus,
    LobbyPhase,
    LobbyState,
)
from hslegend.application.ports import ActionDispatchPort, LobbyStatePort
from hslegend.application.runtime import (
    ActionGate,
    ActionLease,
    ActionLeaseHeld,
    OperationCancelled,
    StopToken,
)
from hslegend.domain import ActionIntent, GameSnapshot, PlanStep, PlayerView
from hslegend.domain.primitives import PrimitiveMixin


Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class LobbyAction(str, Enum):
    """Stable semantic action names understood by action-dispatch adapters."""

    ENTER_TRADITIONAL = "enter_traditional"
    SELECT_DECK = "select_deck"
    START_MATCH = "start_match"


class EnterBattleStatus(str, Enum):
    """Terminal status for the enter-battle slice."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    GATE_BUSY = "gate_busy"


@dataclass(frozen=True, slots=True)
class EnterBattleCommand(PrimitiveMixin):
    """One bounded request to reach deck selection and optionally start queueing."""

    deck_index: int
    start_match: bool = False
    timeout_seconds: float = 30.0
    poll_interval_seconds: float = 0.05
    command_id: str = "enter-battle"

    def __post_init__(self) -> None:
        if type(self.deck_index) is not int:
            raise TypeError("deck_index must be an int")
        if self.deck_index < 0:
            raise ValueError("deck_index must be non-negative")
        if type(self.start_match) is not bool:
            raise TypeError("start_match must be a bool")
        _validate_positive_finite(self.timeout_seconds, "timeout_seconds")
        _validate_positive_finite(
            self.poll_interval_seconds,
            "poll_interval_seconds",
        )
        if not isinstance(self.command_id, str) or not self.command_id.strip():
            raise ValueError("command_id must be non-empty text")


@dataclass(frozen=True, slots=True)
class EnterBattleDispatch(PrimitiveMixin):
    """One semantic action and the transport-level result it produced."""

    action: LobbyAction
    result: DispatchResult

    def __post_init__(self) -> None:
        if not isinstance(self.action, LobbyAction):
            object.__setattr__(self, "action", LobbyAction(self.action))
        if not isinstance(self.result, DispatchResult):
            raise TypeError("result must be a DispatchResult")


@dataclass(frozen=True, slots=True)
class EnterBattleResult(PrimitiveMixin):
    """Typed terminal result; failed actions never continue the sequence."""

    command_id: str
    status: EnterBattleStatus
    reason_code: str
    phase: LobbyPhase
    dispatches: tuple[EnterBattleDispatch, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    elapsed_seconds: float = 0.0
    detail_code: str = ""
    failed_action: LobbyAction | None = None
    release_error: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, EnterBattleStatus):
            object.__setattr__(self, "status", EnterBattleStatus(self.status))
        if not isinstance(self.phase, LobbyPhase):
            object.__setattr__(self, "phase", LobbyPhase(self.phase))
        if self.failed_action is not None and not isinstance(
            self.failed_action,
            LobbyAction,
        ):
            object.__setattr__(
                self,
                "failed_action",
                LobbyAction(self.failed_action),
            )
        object.__setattr__(self, "dispatches", tuple(self.dispatches))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))

    @property
    def ok(self) -> bool:
        return self.status is EnterBattleStatus.COMPLETED


def _validate_positive_finite(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{field_name} must be finite and positive")
    return normalized


def _lobby_snapshot() -> GameSnapshot:
    """Return the immutable non-game context used to route lobby actions.

    Lobby actions are semantic commands, while the existing dispatch port carries
    a ``GameSnapshot`` for game actions. This sentinel deliberately contains no
    inferred battlefield state; it only satisfies the shared transport contract.
    """

    player = PlayerView(player_id=0, side="friendly")
    opponent = PlayerView(player_id=0, side="opponent")
    return GameSnapshot(
        schema_version=1,
        snapshot_id="lobby",
        log_revision=0,
        game_generation=0,
        observed_at=0.0,
        turn_number=0,
        is_my_turn=False,
        is_end=False,
        my_player=player,
        opponent=opponent,
    )


LOBBY_SNAPSHOT = _lobby_snapshot()


def build_lobby_step(
    action: LobbyAction,
    *,
    choice_index: int | None = None,
    timeout_seconds: float = 10.0,
) -> PlanStep:
    """Build one deterministic semantic lobby step."""

    if not isinstance(action, LobbyAction):
        action = LobbyAction(action)
    if choice_index is not None and type(choice_index) is not int:
        raise TypeError("choice_index must be an int or None")
    if action is LobbyAction.SELECT_DECK:
        if choice_index is None:
            raise ValueError("select_deck requires choice_index")
        if choice_index < 0:
            raise ValueError("choice_index must be non-negative")
    elif choice_index is not None:
        raise ValueError(f"{action.value} does not accept choice_index")
    _validate_positive_finite(timeout_seconds, "timeout_seconds")
    return PlanStep(
        step_id=f"lobby-{action.value}",
        intent=ActionIntent(
            action_type=action.value,
            choice_index=choice_index,
            intent_id=f"intent-{action.value}",
        ),
        timeout_seconds=float(timeout_seconds),
    )


class _StopSequence(Exception):
    def __init__(
        self,
        status: EnterBattleStatus,
        reason_code: str,
        *,
        detail_code: str = "",
        failed_action: LobbyAction | None = None,
    ) -> None:
        self.status = status
        self.reason_code = reason_code
        self.detail_code = detail_code
        self.failed_action = failed_action
        super().__init__(reason_code)


class EnterBattleUseCase:
    """Advance from the main menu to deck selection and optional queueing."""

    def __init__(
        self,
        lobby: LobbyStatePort,
        dispatch: ActionDispatchPort,
        action_gate: ActionGate,
        stop_token: StopToken,
        clock: Clock,
        *,
        sleep: Sleeper | None = None,
        lobby_snapshot: GameSnapshot | None = None,
    ) -> None:
        if not callable(getattr(lobby, "get_state", None)):
            raise TypeError("lobby must provide get_state()")
        if not callable(getattr(dispatch, "dispatch", None)):
            raise TypeError("dispatch must provide dispatch()")
        if not isinstance(action_gate, ActionGate):
            raise TypeError("action_gate must be an ActionGate")
        if not isinstance(stop_token, StopToken):
            raise TypeError("stop_token must be a StopToken")
        clock_source = clock
        if not callable(clock_source):
            now = getattr(clock_source, "now", None)
            if not callable(now):
                raise TypeError("clock must be callable or provide now()")
            clock = now
        available_sleep = getattr(clock_source, "sleep", None)
        if sleep is not None and not callable(sleep):
            raise TypeError("sleep must be callable")
        if sleep is None:
            sleep = available_sleep if callable(available_sleep) else time.sleep
        if lobby_snapshot is not None and not isinstance(lobby_snapshot, GameSnapshot):
            raise TypeError("lobby_snapshot must be a GameSnapshot or None")
        self._lobby = lobby
        self._dispatch = dispatch
        self._action_gate = action_gate
        self._stop_token = stop_token
        self._clock = clock
        self._sleep = sleep
        self._lobby_snapshot = lobby_snapshot or LOBBY_SNAPSHOT

    def run(
        self,
        deck_index: int,
        *,
        start_match: bool = False,
        timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 0.05,
        command_id: str = "enter-battle",
    ) -> EnterBattleResult:
        """Convenience wrapper for callers that do not need a command object."""

        return self.execute(
            EnterBattleCommand(
                deck_index=deck_index,
                start_match=start_match,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
                command_id=command_id,
            )
        )

    def execute(self, command: EnterBattleCommand) -> EnterBattleResult:
        """Run the sequence once and return a typed, fail-closed result."""

        if not isinstance(command, EnterBattleCommand):
            raise TypeError("command must be an EnterBattleCommand")

        try:
            started_at = self._now()
        except Exception:
            return EnterBattleResult(
                command_id=command.command_id,
                status=EnterBattleStatus.FAILED,
                reason_code="clock_error",
                phase=LobbyPhase.UNKNOWN,
            )

        try:
            self._stop_token.raise_if_cancelled()
        except OperationCancelled as exc:
            return self._cancelled_result(command, LobbyPhase.UNKNOWN, exc.reason)

        try:
            lease = self._action_gate.acquire(
                "enter-battle",
                command.command_id,
                now=started_at,
            )
        except ActionLeaseHeld:
            return EnterBattleResult(
                command_id=command.command_id,
                status=EnterBattleStatus.GATE_BUSY,
                reason_code="action_gate_busy",
                phase=LobbyPhase.UNKNOWN,
            )
        except Exception:
            return EnterBattleResult(
                command_id=command.command_id,
                status=EnterBattleStatus.FAILED,
                reason_code="action_gate_error",
                phase=LobbyPhase.UNKNOWN,
            )

        deadline = started_at + command.timeout_seconds
        phase = LobbyPhase.UNKNOWN
        dispatches: list[EnterBattleDispatch] = []
        evidence_ids: list[str] = []
        release_error = ""
        try:
            try:
                _state, phase = self._wait_for(
                    LobbyPhase.MAIN_MENU,
                    deadline,
                    command,
                )
                self._dispatch_action(
                    LobbyAction.ENTER_TRADITIONAL,
                    deadline,
                    command,
                    dispatches,
                    evidence_ids,
                )

                _state, phase = self._wait_for(
                    LobbyPhase.DECK_SELECT,
                    deadline,
                    command,
                )
                self._dispatch_action(
                    LobbyAction.SELECT_DECK,
                    deadline,
                    command,
                    dispatches,
                    evidence_ids,
                    choice_index=command.deck_index,
                )

                if command.start_match:
                    self._dispatch_action(
                        LobbyAction.START_MATCH,
                        deadline,
                        command,
                        dispatches,
                        evidence_ids,
                    )

                result = EnterBattleResult(
                    command_id=command.command_id,
                    status=EnterBattleStatus.COMPLETED,
                    reason_code="completed",
                    phase=phase,
                    dispatches=tuple(dispatches),
                    evidence_ids=tuple(evidence_ids),
                    elapsed_seconds=self._elapsed(started_at),
                )
            except OperationCancelled as exc:
                result = EnterBattleResult(
                    command_id=command.command_id,
                    status=EnterBattleStatus.CANCELLED,
                    reason_code="cancelled",
                    phase=phase,
                    dispatches=tuple(dispatches),
                    evidence_ids=tuple(evidence_ids),
                    elapsed_seconds=self._elapsed(started_at),
                    detail_code=exc.reason,
                )
            except _StopSequence as stop:
                result = EnterBattleResult(
                    command_id=command.command_id,
                    status=stop.status,
                    reason_code=stop.reason_code,
                    phase=phase,
                    dispatches=tuple(dispatches),
                    evidence_ids=tuple(evidence_ids),
                    elapsed_seconds=self._elapsed(started_at),
                    detail_code=stop.detail_code,
                    failed_action=stop.failed_action,
                )
        finally:
            release_error = self._release(lease)

        if release_error:
            result = replace(result, release_error=release_error)
        return result

    def _wait_for(
        self,
        expected: LobbyPhase,
        deadline: float,
        command: EnterBattleCommand,
    ) -> tuple[LobbyState, LobbyPhase]:
        while True:
            self._stop_token.raise_if_cancelled()
            now = self._now_or_stop()
            if now >= deadline:
                raise _StopSequence(
                    EnterBattleStatus.TIMED_OUT,
                    "timeout",
                    detail_code=f"timeout_waiting_{expected.value}",
                )

            remaining = deadline - now
            try:
                state = self._lobby.get_state(timeout_seconds=remaining)
            except TimeoutError:
                raise _StopSequence(
                    EnterBattleStatus.TIMED_OUT,
                    "timeout",
                    detail_code=f"timeout_waiting_{expected.value}",
                ) from None
            except OperationCancelled:
                raise
            except Exception:
                raise _StopSequence(
                    EnterBattleStatus.FAILED,
                    "lobby_state_error",
                    detail_code=f"error_waiting_{expected.value}",
                ) from None

            self._stop_token.raise_if_cancelled()
            now = self._now_or_stop()
            if now >= deadline:
                raise _StopSequence(
                    EnterBattleStatus.TIMED_OUT,
                    "timeout",
                    detail_code=f"timeout_waiting_{expected.value}",
                )
            if not isinstance(state, LobbyState):
                raise _StopSequence(
                    EnterBattleStatus.FAILED,
                    "lobby_state_invalid",
                    detail_code=f"invalid_waiting_{expected.value}",
                )
            if state.phase is LobbyPhase.UNKNOWN:
                raise _StopSequence(
                    EnterBattleStatus.FAILED,
                    "lobby_unknown",
                    detail_code=f"unknown_waiting_{expected.value}",
                )
            if state.phase is expected:
                return state, state.phase
            remaining = deadline - now
            try:
                self._sleep(min(command.poll_interval_seconds, remaining))
            except OperationCancelled:
                raise
            except Exception:
                raise _StopSequence(
                    EnterBattleStatus.FAILED,
                    "clock_error",
                    detail_code="sleep_failed",
                ) from None

    def _dispatch_action(
        self,
        action: LobbyAction,
        deadline: float,
        command: EnterBattleCommand,
        dispatches: list[EnterBattleDispatch],
        evidence_ids: list[str],
        *,
        choice_index: int | None = None,
    ) -> None:
        self._stop_token.raise_if_cancelled()
        now = self._now_or_stop()
        if now >= deadline:
            raise _StopSequence(
                EnterBattleStatus.TIMED_OUT,
                "timeout",
                detail_code=f"timeout_before_{action.value}",
                failed_action=action,
            )
        step = build_lobby_step(
            action,
            choice_index=choice_index,
            timeout_seconds=max(deadline - now, 1e-9),
        )
        self._stop_token.raise_if_cancelled()
        remaining = deadline - now
        try:
            result = self._dispatch.dispatch(
                step,
                self._lobby_snapshot,
                timeout_seconds=remaining,
            )
        except TimeoutError:
            raise _StopSequence(
                EnterBattleStatus.TIMED_OUT,
                "timeout",
                detail_code=f"timeout_in_{action.value}",
                failed_action=action,
            ) from None
        except OperationCancelled:
            raise
        except Exception:
            raise _StopSequence(
                EnterBattleStatus.FAILED,
                "dispatch_exception",
                detail_code="dispatch_raised",
                failed_action=action,
            ) from None

        if not isinstance(result, DispatchResult):
            raise _StopSequence(
                EnterBattleStatus.FAILED,
                "dispatch_invalid_result",
                detail_code="dispatch_result_type",
                failed_action=action,
            )
        dispatches.append(EnterBattleDispatch(action, result))
        evidence_ids.extend(
            evidence_id
            for evidence_id in result.evidence_ids
            if evidence_id not in evidence_ids
        )
        try:
            self._stop_token.raise_if_cancelled()
        except OperationCancelled:
            raise _StopSequence(
                EnterBattleStatus.CANCELLED,
                "cancelled",
                detail_code="cancelled_after_dispatch",
                failed_action=action,
            ) from None
        post_dispatch = self._now_or_stop()
        if post_dispatch >= deadline:
            raise _StopSequence(
                EnterBattleStatus.TIMED_OUT,
                "timeout",
                detail_code=f"timeout_after_{action.value}",
                failed_action=action,
            )
        if result.status is DispatchStatus.SENT:
            return
        reason_code = (
            "dispatch_rejected"
            if result.status is DispatchStatus.REJECTED
            else "dispatch_failed"
        )
        raise _StopSequence(
            EnterBattleStatus.FAILED,
            reason_code,
            detail_code=result.reason_code or result.status.value,
            failed_action=action,
        )

    def _now(self) -> float:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("clock must return a number")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise ValueError("clock must return a finite number")
        return normalized

    def _now_or_stop(self) -> float:
        try:
            return self._now()
        except Exception:
            raise _StopSequence(
                EnterBattleStatus.FAILED,
                "clock_error",
                detail_code="clock_read_failed",
            ) from None

    def _elapsed(self, started_at: float) -> float:
        try:
            return max(0.0, self._now() - started_at)
        except Exception:
            return 0.0

    def _release(self, lease: ActionLease) -> str:
        try:
            self._action_gate.release(lease)
        except Exception as exc:
            detail = str(exc).strip()
            return type(exc).__name__ if not detail else f"{type(exc).__name__}: {detail}"
        return ""

    def _cancelled_result(
        self,
        command: EnterBattleCommand,
        phase: LobbyPhase,
        reason: str,
    ) -> EnterBattleResult:
        return EnterBattleResult(
            command_id=command.command_id,
            status=EnterBattleStatus.CANCELLED,
            reason_code="cancelled",
            phase=phase,
            detail_code=reason,
        )


__all__ = [
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
]
