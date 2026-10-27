"""Legacy lobby recognizer adapter with fail-closed phase mapping."""

from __future__ import annotations

import inspect
import math
import time
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Any

from hslegend.application import LobbyPhase, LobbyState

from ..deadline import run_with_deadline


class LobbyStateAdapterError(RuntimeError):
    """Typed failure raised while recognizing or normalizing lobby state."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        message = reason_code if not detail else f"{reason_code}: {detail}"
        super().__init__(message)


_PHASE_ALIASES = {
    "main_menu": LobbyPhase.MAIN_MENU,
    "mainmenu": LobbyPhase.MAIN_MENU,
    "main menu": LobbyPhase.MAIN_MENU,
    "menu": LobbyPhase.MAIN_MENU,
    "主界面": LobbyPhase.MAIN_MENU,
    "主菜单": LobbyPhase.MAIN_MENU,
    "deck_select": LobbyPhase.DECK_SELECT,
    "deckselect": LobbyPhase.DECK_SELECT,
    "deck select": LobbyPhase.DECK_SELECT,
    "deck_selection": LobbyPhase.DECK_SELECT,
    "选择卡组": LobbyPhase.DECK_SELECT,
    "matching": LobbyPhase.MATCHING,
    "matchmaking": LobbyPhase.MATCHING,
    "queue": LobbyPhase.MATCHING,
    "匹配中": LobbyPhase.MATCHING,
    "in_game": LobbyPhase.IN_GAME,
    "ingame": LobbyPhase.IN_GAME,
    "in game": LobbyPhase.IN_GAME,
    "game": LobbyPhase.IN_GAME,
    "battle": LobbyPhase.IN_GAME,
    "对局中": LobbyPhase.IN_GAME,
    "result": LobbyPhase.RESULT,
    "results": LobbyPhase.RESULT,
    "game_over": LobbyPhase.RESULT,
    "finished": LobbyPhase.RESULT,
    "结算": LobbyPhase.RESULT,
    "unknown": LobbyPhase.UNKNOWN,
}


def _timeout_seconds(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LobbyStateAdapterError(
            "invalid_timeout_seconds",
            "timeout_seconds must be a number",
        )
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0.0:
        raise LobbyStateAdapterError(
            "invalid_timeout_seconds",
            "timeout_seconds must be finite and positive",
        )
    return normalized


def _accepts_timeout_seconds(callback: Callable[..., Any]) -> bool:
    try:
        parameters = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        return False
    parameter = parameters.get("timeout_seconds")
    if parameter is not None:
        return parameter.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    return any(
        item.kind is inspect.Parameter.VAR_KEYWORD
        for item in parameters.values()
    )


def _field(source: Any, name: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _first_field(source: Any, names: tuple[str, ...]) -> Any:
    for name in names:
        value = _field(source, name)
        if value is not None:
            return value
    return None


def _normalize_phase(value: Any) -> LobbyPhase:
    if isinstance(value, LobbyPhase):
        return value
    if isinstance(value, Enum):
        value = value.value
    if not isinstance(value, str):
        return LobbyPhase.UNKNOWN
    key = value.strip().lower().replace("-", "_")
    return _PHASE_ALIASES.get(key, LobbyPhase.UNKNOWN)


def _confidence(value: Any, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LobbyStateAdapterError("invalid_confidence", "confidence must be numeric")
    confidence = float(value)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise LobbyStateAdapterError(
            "invalid_confidence",
            "confidence must be finite and between 0 and 1",
        )
    return confidence


def _observed_at(value: Any, clock: Callable[[], float]) -> float:
    if value is None:
        try:
            value = clock()
        except Exception as exc:
            raise LobbyStateAdapterError("clock_failed", str(exc)) from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LobbyStateAdapterError("invalid_observed_at", "observed_at must be numeric")
    observed = float(value)
    if not math.isfinite(observed) or observed < 0.0:
        raise LobbyStateAdapterError(
            "invalid_observed_at",
            "observed_at must be finite and non-negative",
        )
    return observed


class LegacyLobbyStateAdapter:
    """Normalize an injected legacy lobby recognizer to ``LobbyState``."""

    def __init__(
        self,
        recognizer: Callable[..., Any],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(recognizer):
            raise TypeError("recognizer must be callable")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._recognizer = recognizer
        self._clock = clock

    def get_state(self, *, timeout_seconds: float) -> LobbyState:
        timeout_seconds = _timeout_seconds(timeout_seconds)
        try:
            if _accepts_timeout_seconds(self._recognizer):
                raw = run_with_deadline(
                    lambda: self._recognizer(
                        timeout_seconds=timeout_seconds,
                    ),
                    timeout_seconds=timeout_seconds,
                )
            else:
                raw = run_with_deadline(
                    self._recognizer,
                    timeout_seconds=timeout_seconds,
                )
        except TimeoutError:
            raise
        except Exception as exc:
            raise LobbyStateAdapterError("recognition_failed", str(exc)) from exc

        if isinstance(raw, LobbyState):
            return raw
        phase_value = _first_field(raw, ("phase", "state", "screen", "kind"))
        if phase_value is None and isinstance(raw, (str, LobbyPhase, Enum)):
            phase_value = raw
        phase = _normalize_phase(phase_value)
        confidence = _confidence(
            _field(raw, "confidence") if raw is not None else None,
            1.0 if phase is not LobbyPhase.UNKNOWN else 0.0,
        )
        evidence_id = _first_field(raw, ("evidence_id", "evidence", "frame_id"))
        if evidence_id is None:
            evidence_id = ""
        if not isinstance(evidence_id, str):
            raise LobbyStateAdapterError("invalid_evidence_id", "evidence_id must be text")
        observed_at = _observed_at(
            _field(raw, "observed_at") if raw is not None else None,
            self._clock,
        )
        return LobbyState(
            phase=phase,
            observed_at=observed_at,
            confidence=confidence,
            evidence_id=evidence_id,
        )


__all__ = ["LegacyLobbyStateAdapter", "LobbyStateAdapterError"]
