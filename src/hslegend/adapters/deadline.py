"""Bounded execution for synchronous adapter callbacks."""

from __future__ import annotations

import math
import queue
import threading
from collections.abc import Callable
from typing import Any, TypeVar


T = TypeVar("T")


def run_with_deadline(
    callback: Callable[[], T],
    *,
    timeout_seconds: float,
) -> T:
    """Run one callback on a daemon worker and wait at most for the deadline.

    A callback that ignores cooperative timeout signalling cannot be interrupted
    safely from another thread. The worker is therefore daemonized and abandoned
    on timeout; callers must treat a timeout as an unknown/unconfirmed outcome
    and must not retry automatically while the callback may still be running.
    """

    if not callable(callback):
        raise TypeError("callback must be callable")
    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds,
        (int, float),
    ):
        raise TypeError("timeout_seconds must be a number")
    timeout = float(timeout_seconds)
    if not math.isfinite(timeout) or timeout <= 0.0:
        raise ValueError("timeout_seconds must be finite and positive")

    outcomes: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            outcome: tuple[bool, Any] = (True, callback())
        except BaseException as exc:  # Preserve worker failures for the caller.
            outcome = (False, exc)
        try:
            outcomes.put_nowait(outcome)
        except queue.Full:
            return

    thread = threading.Thread(
        target=worker,
        name="hslegend-adapter-deadline",
        daemon=True,
    )
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        raise TimeoutError(f"adapter callback exceeded {timeout:g}s")

    try:
        succeeded, value = outcomes.get_nowait()
    except queue.Empty as exc:
        raise RuntimeError("adapter callback exited without a result") from exc
    if succeeded:
        return value
    raise value
