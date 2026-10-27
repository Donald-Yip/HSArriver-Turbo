"""Observation-only settlement use case for one completed game."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from hslegend.application.dto import LobbyPhase, LobbyState
from hslegend.application.ports import LobbyStatePort
from hslegend.application.runtime import OperationCancelled, StopToken
from hslegend.domain.primitives import PrimitiveMixin


Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class FinishBattleStatus(str, Enum):
    """Terminal status for the settlement observation slice."""

    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FinishBattleCommand(PrimitiveMixin):
    """One bounded request to observe the authoritative result screen."""

    command_id: str
    timeout_seconds: float = 120.0
    poll_interval_seconds: float = 0.25

    def __post_init__(self) -> None:
        if not isinstance(self.command_id, str) or not self.command_id.strip():
            raise ValueError("command_id must be non-empty text")
        _validate_positive_finite(self.timeout_seconds, "timeout_seconds")
        _validate_positive_finite(
            self.poll_interval_seconds,
            "poll_interval_seconds",
        )


@dataclass(frozen=True, slots=True)
class FinishBattleResult(PrimitiveMixin):
    """Typed settlement outcome; no action is dispatched by this slice."""

    command_id: str
    status: FinishBattleStatus
    phase: LobbyPhase
    reason_code: str
    evidence_ids: tuple[str, ...] = ()
    elapsed_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.status, FinishBattleStatus):
            object.__setattr__(self, "status", FinishBattleStatus(self.status))
        if not isinstance(self.phase, LobbyPhase):
            object.__setattr__(self, "phase", LobbyPhase(self.phase))
        object.__setattr__(self, "evidence_ids", _unique_evidence(self.evidence_ids))
        object.__setattr__(
            self,
            "elapsed_seconds",
            _non_negative_finite(self.elapsed_seconds, "elapsed_seconds"),
        )

    @property
    def ok(self) -> bool:
        return self.status is FinishBattleStatus.COMPLETED


def _validate_positive_finite(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise ValueError(f"{field_name} must be finite and positive")
    return normalized


def _non_negative_finite(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return normalized


def _unique_evidence(evidence_ids: object) -> tuple[str, ...]:
    unique: list[str] = []
    for evidence_id in tuple(evidence_ids):  # type: ignore[arg-type]
        if evidence_id and evidence_id not in unique:
            unique.append(evidence_id)
    return tuple(unique)


class FinishBattleUseCase:
    """Poll lobby state until RESULT without any input side effect."""

    def __init__(
        self,
        lobby: LobbyStatePort,
        stop_token: StopToken,
        clock: Clock,
        *,
        sleep: Sleeper | None = None,
    ) -> None:
        if not callable(getattr(lobby, "get_state", None)):
            raise TypeError("lobby must provide get_state()")
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
        self._lobby = lobby
        self._stop_token = stop_token
        self._clock = clock
        self._sleep = sleep

    def execute(self, command: FinishBattleCommand) -> FinishBattleResult:
        """Observe at most one game until RESULT or a bounded terminal stop."""

        if not isinstance(command, FinishBattleCommand):
            raise TypeError("command must be a FinishBattleCommand")

        try:
            self._stop_token.raise_if_cancelled()
        except OperationCancelled:
            return FinishBattleResult(
                command_id=command.command_id,
                status=FinishBattleStatus.CANCELLED,
                phase=LobbyPhase.UNKNOWN,
                reason_code="cancelled",
                elapsed_seconds=0.0,
            )

        try:
            started_at = self._now()
        except Exception:
            try:
                self._stop_token.raise_if_cancelled()
            except OperationCancelled:
                return FinishBattleResult(
                    command_id=command.command_id,
                    status=FinishBattleStatus.CANCELLED,
                    phase=LobbyPhase.UNKNOWN,
                    reason_code="cancelled",
                    elapsed_seconds=0.0,
                )
            return FinishBattleResult(
                command_id=command.command_id,
                status=FinishBattleStatus.FAILED,
                phase=LobbyPhase.UNKNOWN,
                reason_code="clock_error",
            )

        deadline = started_at + command.timeout_seconds
        phase = LobbyPhase.UNKNOWN
        evidence_ids: list[str] = []

        while True:
            cancelled = self._cancelled_result(
                command,
                phase,
                evidence_ids,
                started_at,
            )
            if cancelled is not None:
                return cancelled

            try:
                now = self._now()
            except Exception:
                cancelled = self._cancelled_result(
                    command,
                    phase,
                    evidence_ids,
                    started_at,
                )
                if cancelled is not None:
                    return cancelled
                return self._result(
                    command,
                    FinishBattleStatus.FAILED,
                    "clock_error",
                    phase,
                    evidence_ids,
                    started_at,
                )
            if now >= deadline:
                cancelled = self._cancelled_result(
                    command,
                    phase,
                    evidence_ids,
                    started_at,
                )
                if cancelled is not None:
                    return cancelled
                return self._result(
                    command,
                    FinishBattleStatus.TIMED_OUT,
                    "timeout",
                    phase,
                    evidence_ids,
                    started_at,
                )

            cancelled = self._cancelled_result(
                command,
                phase,
                evidence_ids,
                started_at,
            )
            if cancelled is not None:
                return cancelled
            remaining = deadline - now
            try:
                state = self._lobby.get_state(timeout_seconds=remaining)
            except TimeoutError:
                cancelled = self._cancelled_result(
                    command,
                    phase,
                    evidence_ids,
                    started_at,
                )
                if cancelled is not None:
                    return cancelled
                try:
                    now = self._now()
                except Exception:
                    cancelled = self._cancelled_result(
                        command,
                        phase,
                        evidence_ids,
                        started_at,
                    )
                    if cancelled is not None:
                        return cancelled
                    return self._result(
                        command,
                        FinishBattleStatus.FAILED,
                        "clock_error",
                        phase,
                        evidence_ids,
                        started_at,
                    )
                cancelled = self._cancelled_result(
                    command,
                    phase,
                    evidence_ids,
                    started_at,
                )
                if cancelled is not None:
                    return cancelled
                if now >= deadline:
                    return self._result(
                        command,
                        FinishBattleStatus.TIMED_OUT,
                        "timeout",
                        phase,
                        evidence_ids,
                        started_at,
                    )
                return self._result(
                    command,
                    FinishBattleStatus.FAILED,
                    "lobby_state_timeout",
                    phase,
                    evidence_ids,
                    started_at,
                )
            except OperationCancelled:
                return self._result(
                    command,
                    FinishBattleStatus.CANCELLED,
                    "cancelled",
                    phase,
                    evidence_ids,
                    started_at,
                )
            except Exception:
                cancelled = self._cancelled_result(
                    command,
                    phase,
                    evidence_ids,
                    started_at,
                )
                if cancelled is not None:
                    return cancelled
                return self._result(
                    command,
                    FinishBattleStatus.FAILED,
                    "lobby_state_error",
                    phase,
                    evidence_ids,
                    started_at,
                )

            if isinstance(state, LobbyState):
                phase = state.phase
                if state.evidence_id:
                    evidence_ids = list(
                        _unique_evidence((*evidence_ids, state.evidence_id))
                    )

            cancelled = self._cancelled_result(
                command,
                phase,
                evidence_ids,
                started_at,
            )
            if cancelled is not None:
                return cancelled
            if not isinstance(state, LobbyState):
                return self._result(
                    command,
                    FinishBattleStatus.FAILED,
                    "lobby_state_invalid",
                    phase,
                    evidence_ids,
                    started_at,
                )

            try:
                now = self._now()
            except Exception:
                cancelled = self._cancelled_result(
                    command,
                    phase,
                    evidence_ids,
                    started_at,
                )
                if cancelled is not None:
                    return cancelled
                return self._result(
                    command,
                    FinishBattleStatus.FAILED,
                    "clock_error",
                    phase,
                    evidence_ids,
                    started_at,
                )
            cancelled = self._cancelled_result(
                command,
                phase,
                evidence_ids,
                started_at,
            )
            if cancelled is not None:
                return cancelled
            if now >= deadline:
                return self._result(
                    command,
                    FinishBattleStatus.TIMED_OUT,
                    "timeout",
                    phase,
                    evidence_ids,
                    started_at,
                )
            if state.phase is LobbyPhase.RESULT:
                cancelled = self._cancelled_result(
                    command,
                    phase,
                    evidence_ids,
                    started_at,
                )
                if cancelled is not None:
                    return cancelled
                return self._result(
                    command,
                    FinishBattleStatus.COMPLETED,
                    "completed",
                    phase,
                    evidence_ids,
                    started_at,
                )

            cancelled = self._cancelled_result(
                command,
                phase,
                evidence_ids,
                started_at,
            )
            if cancelled is not None:
                return cancelled
            remaining = deadline - now
            try:
                self._sleep(min(command.poll_interval_seconds, remaining))
            except OperationCancelled:
                return self._result(
                    command,
                    FinishBattleStatus.CANCELLED,
                    "cancelled",
                    phase,
                    evidence_ids,
                    started_at,
                )
            except Exception:
                cancelled = self._cancelled_result(
                    command,
                    phase,
                    evidence_ids,
                    started_at,
                )
                if cancelled is not None:
                    return cancelled
                return self._result(
                    command,
                    FinishBattleStatus.FAILED,
                    "sleep_error",
                    phase,
                    evidence_ids,
                    started_at,
                )

    def _cancelled_result(
        self,
        command: FinishBattleCommand,
        phase: LobbyPhase,
        evidence_ids: list[str],
        started_at: float,
    ) -> FinishBattleResult | None:
        try:
            self._stop_token.raise_if_cancelled()
        except OperationCancelled:
            return self._result(
                command,
                FinishBattleStatus.CANCELLED,
                "cancelled",
                phase,
                evidence_ids,
                started_at,
            )
        return None

    def _now(self) -> float:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("clock must return a number")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise ValueError("clock must return a finite number")
        return normalized

    def _elapsed(self, started_at: float) -> float:
        try:
            return max(0.0, self._now() - started_at)
        except Exception:
            return 0.0

    def _result(
        self,
        command: FinishBattleCommand,
        status: FinishBattleStatus,
        reason_code: str,
        phase: LobbyPhase,
        evidence_ids: list[str],
        started_at: float,
    ) -> FinishBattleResult:
        return FinishBattleResult(
            command_id=command.command_id,
            status=status,
            phase=phase,
            reason_code=reason_code,
            evidence_ids=tuple(evidence_ids),
            elapsed_seconds=self._elapsed(started_at),
        )


__all__ = [
    "Clock",
    "FinishBattleCommand",
    "FinishBattleResult",
    "FinishBattleStatus",
    "FinishBattleUseCase",
    "Sleeper",
]
