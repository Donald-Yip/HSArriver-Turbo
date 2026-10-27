"""Power.log-backed game state adapter with injected providers."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from hslegend.application import GameObservation
from hslegend.domain import SUPPORTED_SCHEMA_VERSIONS, GameSnapshot


class PowerLogAdapterError(RuntimeError):
    """Typed failure raised by the Power.log adapter."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        message = reason_code if not detail else f"{reason_code}: {detail}"
        super().__init__(message)


class PowerLogReadError(PowerLogAdapterError):
    """The snapshot or revision provider failed."""


class PowerLogValidationError(PowerLogAdapterError):
    """A provider returned an invalid snapshot or revision."""


class PowerLogWaitError(PowerLogAdapterError):
    """Revision waiting failed internally."""


def _valid_int(value: Any, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise PowerLogValidationError(
            f"invalid_{field_name}",
            f"{field_name} must be a non-negative integer",
        )
    return value


def _valid_timeout(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PowerLogValidationError("invalid_timeout", "timeout must be numeric")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout < 0.0:
        raise PowerLogValidationError(
            "invalid_timeout",
            "timeout must be finite and non-negative",
        )
    return timeout


class PowerLogGameStateAdapter:
    """Adapt injected Power.log snapshot and revision providers to the port."""

    def __init__(
        self,
        snapshot_provider: Callable[[], GameSnapshot | GameObservation],
        revision_provider: Callable[[], int],
        *,
        confidence_provider: Callable[[], float] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep_func: Callable[[float], None] = time.sleep,
        poll_interval_seconds: float = 0.01,
        stop_token: Any = None,
    ) -> None:
        for name, callable_value in (
            ("snapshot_provider", snapshot_provider),
            ("revision_provider", revision_provider),
            ("clock", clock),
            ("sleep_func", sleep_func),
        ):
            if not callable(callable_value):
                raise TypeError(f"{name} must be callable")
        if confidence_provider is not None and not callable(confidence_provider):
            raise TypeError("confidence_provider must be callable")
        poll_interval = float(poll_interval_seconds)
        if not math.isfinite(poll_interval) or poll_interval <= 0.0:
            raise ValueError("poll_interval_seconds must be finite and positive")
        self._snapshot_provider = snapshot_provider
        self._revision_provider = revision_provider
        self._confidence_provider = confidence_provider
        self._clock = clock
        self._sleep_func = sleep_func
        self._poll_interval = poll_interval
        self._stop_token = stop_token

    def current(self) -> GameObservation:
        try:
            raw = self._snapshot_provider()
        except Exception as exc:
            raise PowerLogReadError("snapshot_failed", str(exc)) from exc
        return self._to_observation(raw)

    def wait_for_revision_change(
        self,
        after_revision: int,
        timeout_seconds: float,
    ) -> GameObservation | None:
        after_revision = _valid_int(after_revision, "after_revision")
        timeout = _valid_timeout(timeout_seconds)
        deadline = self._clock() + timeout
        if self._should_stop_wait(deadline):
            return None

        while True:
            revision = self._read_revision()
            if self._should_stop_wait(deadline):
                return None
            if revision > after_revision:
                observation = self.current()
                if self._should_stop_wait(deadline):
                    return None
                if observation.snapshot.log_revision > after_revision:
                    return observation

            remaining = deadline - self._clock()
            if remaining <= 0.0 or self._cancelled():
                return None
            try:
                self._sleep_func(min(self._poll_interval, remaining))
            except Exception as exc:
                raise PowerLogWaitError("wait_failed", str(exc)) from exc
            if self._should_stop_wait(deadline):
                return None

    def _read_revision(self) -> int:
        try:
            revision = self._revision_provider()
        except Exception as exc:
            raise PowerLogReadError("revision_failed", str(exc)) from exc
        return _valid_int(revision, "revision")

    def _to_observation(self, raw: GameSnapshot | GameObservation) -> GameObservation:
        if isinstance(raw, GameObservation):
            self._validate_snapshot(raw.snapshot)
            return raw
        if not isinstance(raw, GameSnapshot):
            raise PowerLogValidationError(
                "invalid_snapshot",
                "snapshot_provider must return GameSnapshot or GameObservation",
            )
        self._validate_snapshot(raw)
        confidence = 1.0
        if self._confidence_provider is not None:
            try:
                confidence = self._confidence_provider()
            except Exception as exc:
                raise PowerLogReadError("confidence_failed", str(exc)) from exc
        try:
            return GameObservation(raw, confidence)
        except (TypeError, ValueError) as exc:
            raise PowerLogValidationError("invalid_confidence", str(exc)) from exc

    @staticmethod
    def _validate_snapshot(snapshot: GameSnapshot) -> None:
        if snapshot.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            raise PowerLogValidationError(
                "unsupported_schema",
                f"unsupported schema version: {snapshot.schema_version}",
            )
        if not isinstance(snapshot.snapshot_id, str) or not snapshot.snapshot_id.strip():
            raise PowerLogValidationError(
                "invalid_snapshot_id",
                "snapshot_id must be non-empty text",
            )
        _valid_int(snapshot.log_revision, "log_revision")
        _valid_int(snapshot.game_generation, "game_generation")
        if (
            isinstance(snapshot.observed_at, bool)
            or not isinstance(snapshot.observed_at, (int, float))
            or not math.isfinite(float(snapshot.observed_at))
            or float(snapshot.observed_at) < 0.0
        ):
            raise PowerLogValidationError(
                "invalid_observed_at",
                "observed_at must be finite and non-negative",
            )

    def _should_stop_wait(self, deadline: float) -> bool:
        if self._cancelled():
            return True
        return self._clock() >= deadline

    def _cancelled(self) -> bool:
        if self._stop_token is None:
            return False
        checker = getattr(self._stop_token, "is_cancelled", None)
        if not callable(checker):
            raise PowerLogWaitError(
                "invalid_stop_token",
                "stop_token must expose is_cancelled()",
            )
        try:
            return bool(checker())
        except Exception as exc:
            raise PowerLogWaitError("cancel_check_failed", str(exc)) from exc


__all__ = [
    "PowerLogAdapterError",
    "PowerLogGameStateAdapter",
    "PowerLogReadError",
    "PowerLogValidationError",
    "PowerLogWaitError",
]
