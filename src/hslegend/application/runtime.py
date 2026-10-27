"""Deterministic runtime controls shared by future application use cases."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field

from .dto import ConfigSnapshot


class RuntimeControlError(RuntimeError):
    """Base class for application runtime-control failures."""


class OperationCancelled(RuntimeControlError):
    """Raised when a caller observes a cancelled run token."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"operation cancelled: {reason}")


class ActionLeaseError(RuntimeControlError):
    """Base class for action-gate ownership failures."""


class ActionLeaseHeld(ActionLeaseError):
    """Raised when another operation already owns the action gate."""


class NoActiveLease(ActionLeaseError):
    """Raised when releasing a gate that has no active lease."""


class WrongLeaseOwner(ActionLeaseError):
    """Raised when a different owner attempts to release a live lease."""


class StaleActionLease(ActionLeaseError):
    """Raised when an obsolete or forged lease attempts release."""


class StopToken:
    """Thread-safe cooperative cancellation without creating threads/I/O.

    Waiters poll a monotonic deadline. ``cancel`` does not create a wake-up
    thread; cancellation is observed by active waiters within ``poll_interval``
    seconds. The first cancellation reason wins exactly once under concurrent
    calls.
    """

    __slots__ = ("_cancelled", "_lock", "_reason")

    def __init__(self) -> None:
        self._cancelled = False
        self._reason = ""
        self._lock = threading.Lock()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    @property
    def cancelled(self) -> bool:
        return self.is_cancelled()

    def cancel(self, reason: str = "cancelled") -> bool:
        """Cancel the token, returning true only for the first cancellation."""

        with self._lock:
            if self._cancelled:
                return False
            self._reason = reason or "cancelled"
            self._cancelled = True
            return True

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def wait(
        self,
        timeout: float | None = None,
        *,
        poll_interval: float = 0.01,
    ) -> bool:
        """Wait until cancelled or timeout expires.

        Returns ``True`` when cancelled and ``False`` on timeout. A timeout of
        ``None`` waits cooperatively until cancellation.
        """

        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise TypeError("timeout must be a number or None")
            timeout = float(timeout)
            if not math.isfinite(timeout) or timeout < 0.0:
                raise ValueError("timeout must be finite and non-negative")
        if isinstance(poll_interval, bool) or not isinstance(poll_interval, (int, float)):
            raise TypeError("poll_interval must be a number")
        poll_interval = float(poll_interval)
        if not math.isfinite(poll_interval) or poll_interval <= 0.0:
            raise ValueError("poll_interval must be finite and positive")

        if self.is_cancelled():
            return True
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_cancelled():
            if deadline is None:
                time.sleep(poll_interval)
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return False
            time.sleep(min(poll_interval, remaining))
        return True

    def raise_if_cancelled(self) -> None:
        with self._lock:
            if not self._cancelled:
                return
            reason = self._reason
        raise OperationCancelled(reason)


@dataclass(frozen=True, slots=True)
class ActionLease:
    """Exclusive proof of action-gate ownership."""

    owner: str
    operation_id: str
    lease_id: str
    expires_at: float | None = None


class ActionGate:
    """Single-owner action gate with minimal timeout and reap semantics."""

    __slots__ = ("_current", "_lock", "_next_lease_number")

    def __init__(self) -> None:
        self._current: ActionLease | None = None
        self._next_lease_number = 0
        self._lock = threading.RLock()

    def _expire_unlocked(self, now: float) -> bool:
        """Reap an expired lease; the caller must hold ``_lock``."""

        current = self._current
        if current is None or current.expires_at is None:
            return False
        if now < current.expires_at:
            return False
        self._current = None
        return True

    def acquire(
        self,
        owner: str,
        operation_id: str,
        *,
        timeout_seconds: float | None = None,
        now: float | None = None,
    ) -> ActionLease:
        """Acquire the sole lease or fail without waiting."""

        if not owner:
            raise ValueError("owner must not be empty")
        if not operation_id:
            raise ValueError("operation_id must not be empty")
        if timeout_seconds is not None:
            if isinstance(timeout_seconds, bool) or not isinstance(
                timeout_seconds, (int, float)
            ):
                raise TypeError("timeout_seconds must be a number or None")
            timeout_seconds = float(timeout_seconds)
            if not math.isfinite(timeout_seconds) or timeout_seconds <= 0.0:
                raise ValueError("timeout_seconds must be finite and positive")
        if now is not None:
            if isinstance(now, bool) or not isinstance(now, (int, float)):
                raise TypeError("now must be a number")
            now = float(now)
            if not math.isfinite(now):
                raise ValueError("now must be finite")
        else:
            now = time.monotonic()

        with self._lock:
            self._expire_unlocked(now)
            current = self._current
            if current is not None:
                raise ActionLeaseHeld(
                    f"action gate held by {current.owner!r} "
                    f"for {current.operation_id!r}"
                )

            self._next_lease_number += 1
            expires_at = None if timeout_seconds is None else now + timeout_seconds
            lease = ActionLease(
                owner=owner,
                operation_id=operation_id,
                lease_id=f"lease-{self._next_lease_number}",
                expires_at=expires_at,
            )
            self._current = lease
            return lease

    def release(self, lease: ActionLease) -> None:
        """Release the exact live lease; any other lease is rejected."""

        with self._lock:
            current = self._current
            if current is None:
                raise NoActiveLease("action gate has no active lease")
            if lease.owner != current.owner:
                raise WrongLeaseOwner(
                    f"lease owner {lease.owner!r} does not own the action gate"
                )
            if lease != current:
                raise StaleActionLease(
                    "lease is stale or does not match current ownership"
                )
            self._current = None

    def reap_expired(self, now: float) -> bool:
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise TypeError("now must be a number")
        normalized = float(now)
        if not math.isfinite(normalized):
            raise ValueError("now must be finite")
        with self._lock:
            return self._expire_unlocked(normalized)

    def current_lease(self, *, now: float | None = None) -> ActionLease | None:
        with self._lock:
            if now is not None:
                if isinstance(now, bool) or not isinstance(now, (int, float)):
                    raise TypeError("now must be a number")
                normalized = float(now)
                if not math.isfinite(normalized):
                    raise ValueError("now must be finite")
                self._expire_unlocked(normalized)
            return self._current


@dataclass(frozen=True, slots=True)
class RunContext:
    """One run's correlation, cancellation, action ownership, and placeholders."""

    session_id: str
    config: ConfigSnapshot = field(default_factory=ConfigSnapshot)
    stop_token: StopToken = field(default_factory=StopToken)
    action_gate: ActionGate = field(default_factory=ActionGate)
    plugin_registry: tuple[str, ...] = ()
    correlation_id: str = ""

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id must not be empty")
        if not isinstance(self.config, ConfigSnapshot):
            raise TypeError("config must be a ConfigSnapshot")
        if not isinstance(self.stop_token, StopToken):
            raise TypeError("stop_token must be a StopToken")
        if not isinstance(self.action_gate, ActionGate):
            raise TypeError("action_gate must be an ActionGate")
        object.__setattr__(self, "plugin_registry", tuple(self.plugin_registry))
        object.__setattr__(
            self,
            "correlation_id",
            self.correlation_id or self.session_id,
        )

    def raise_if_cancelled(self) -> None:
        self.stop_token.raise_if_cancelled()


__all__ = [
    "ActionGate",
    "ActionLease",
    "ActionLeaseError",
    "ActionLeaseHeld",
    "NoActiveLease",
    "OperationCancelled",
    "RunContext",
    "RuntimeControlError",
    "StaleActionLease",
    "StopToken",
    "WrongLeaseOwner",
]
