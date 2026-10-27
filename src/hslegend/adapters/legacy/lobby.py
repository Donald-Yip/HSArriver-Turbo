"""Authoritative lobby state layered over Power.log and UI recognition."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from hslegend.application import LobbyPhase, LobbyState, LobbyStatePort

from .powerlog import LegacyPowerLogError, LegacyPowerLogPump


class LegacyLobbyAdapterError(RuntimeError):
    """Typed failure while combining authoritative and UI lobby evidence."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        message = reason_code if not detail else f"{reason_code}: {detail}"
        super().__init__(message)


class AuthoritativeLobbyStateAdapter:
    """Return RESULT/IN_GAME from Power.log before consulting UI recognition."""

    def __init__(
        self,
        pump: LegacyPowerLogPump,
        ui_recognizer: LobbyStatePort,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(pump, LegacyPowerLogPump):
            raise TypeError("pump must be a LegacyPowerLogPump")
        if not callable(getattr(ui_recognizer, "get_state", None)):
            raise TypeError("ui_recognizer must provide get_state()")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._pump = pump
        self._ui = ui_recognizer
        self._clock = clock

    def get_state(self, *, timeout_seconds: float) -> LobbyState:
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ):
            raise LegacyLobbyAdapterError(
                "invalid_timeout", "timeout_seconds must be numeric"
            )
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0.0:
            raise LegacyLobbyAdapterError(
                "invalid_timeout", "timeout_seconds must be finite and positive"
            )

        try:
            state = self._pump.pump()
        except LegacyPowerLogError as exc:
            raise LegacyLobbyAdapterError(exc.reason_code, exc.detail) from exc
        observed_at = self._now()

        if not self._pump.last_error and bool(getattr(state, "available", False)):
            try:
                complete = bool(getattr(state, "is_end"))
            except Exception as exc:
                raise LegacyLobbyAdapterError(
                    "invalid_power_state", str(exc)
                ) from exc
            phase = LobbyPhase.RESULT if complete else LobbyPhase.IN_GAME
            revision = int(getattr(state, "revision", 0) or 0)
            generation = int(getattr(state, "game_generation", 0) or 0)
            return LobbyState(
                phase=phase,
                observed_at=observed_at,
                confidence=1.0,
                evidence_id=f"powerlog-lobby:{generation}:{revision}:{phase.value}",
            )

        try:
            return self._ui.get_state(timeout_seconds=timeout)
        except TimeoutError:
            raise
        except Exception as exc:
            raise LegacyLobbyAdapterError("ui_recognition_failed", str(exc)) from exc

    def _now(self) -> float:
        try:
            value = self._clock()
        except Exception as exc:
            raise LegacyLobbyAdapterError("clock_failed", str(exc)) from exc
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise LegacyLobbyAdapterError("invalid_clock", "clock must return a number")
        timestamp = float(value)
        if not math.isfinite(timestamp) or timestamp < 0.0:
            raise LegacyLobbyAdapterError(
                "invalid_clock", "clock must return finite non-negative seconds"
            )
        return timestamp


__all__ = ["AuthoritativeLobbyStateAdapter", "LegacyLobbyAdapterError"]
