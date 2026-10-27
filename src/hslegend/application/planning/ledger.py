"""Attempt ledger that prevents unsafe immediate replay."""

from __future__ import annotations

from dataclasses import dataclass, replace

from hslegend.application.dto import ExecutionStatus
from hslegend.domain import ActionPlan

from .contracts import PlanContext


class LedgerError(RuntimeError):
    """Base class for execution-ledger policy failures."""

    def __init__(self, action_key: str, message: str) -> None:
        self.action_key = action_key
        super().__init__(message)


class ImmediateReplayBlocked(LedgerError):
    """Raised when replay would duplicate an unconfirmed or confirmed action."""


class InFlightAttemptError(LedgerError):
    """Raised when another execution attempt is still in flight."""


class AttemptLimitExceeded(LedgerError):
    """Raised when an action has consumed its controlled attempt budget."""


class ArchivedActionError(LedgerError):
    """Raised when an archived action is selected for execution."""


class UnknownAttemptError(LedgerError):
    """Raised when completing an attempt not owned by this ledger."""


@dataclass(frozen=True, slots=True)
class LedgerAttempt:
    """One ledger attempt; NOT_EXECUTED means in flight."""

    action_key: str
    attempt_number: int
    status: ExecutionStatus = ExecutionStatus.NOT_EXECUTED
    retry_authorized: bool = False
    retry_evidence_id: str = ""
    retry_reason: str = ""


@dataclass(slots=True)
class _History:
    attempts: list[LedgerAttempt]
    archived: bool = False
    retry_authorized: bool = False
    retry_evidence_id: str = ""
    retry_reason: str = ""


class ExecutionLedger:
    """Track attempts per stable action key with deterministic retry policy.

    A confirmed action is never replayed.  An unconfirmed action requires an
    explicit ``authorize_retry`` call before another attempt.  Other failed
    attempts may be retried until ``max_attempts`` is exhausted.
    """

    def __init__(self, *, max_attempts: int = 3) -> None:
        if type(max_attempts) is not int:
            raise TypeError("max_attempts must be an int")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._max_attempts = max_attempts
        self._histories: dict[str, _History] = {}

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    def begin(self, action_key: str) -> LedgerAttempt:
        history = self._history(action_key)
        if history.archived:
            raise ArchivedActionError(action_key, f"action is archived: {action_key}")
        if len(history.attempts) >= self._max_attempts:
            raise AttemptLimitExceeded(
                action_key,
                f"attempt limit {self._max_attempts} reached for {action_key}",
            )

        if history.attempts:
            last = history.attempts[-1]
            if last.status == ExecutionStatus.NOT_EXECUTED:
                raise InFlightAttemptError(
                    action_key, f"action attempt is already in flight: {action_key}"
                )
            if last.status == ExecutionStatus.EXECUTED_CONFIRMED:
                raise ImmediateReplayBlocked(
                    action_key, f"action already confirmed: {action_key}"
                )
            if (
                last.status == ExecutionStatus.EXECUTED_UNCONFIRMED
                and not history.retry_authorized
            ):
                raise ImmediateReplayBlocked(
                    action_key,
                    f"unconfirmed action requires explicit retry authorization: {action_key}",
                )

        attempt = LedgerAttempt(
            action_key=action_key,
            attempt_number=len(history.attempts) + 1,
            retry_authorized=history.retry_authorized,
            retry_evidence_id=history.retry_evidence_id,
            retry_reason=history.retry_reason,
        )
        history.attempts.append(attempt)
        history.retry_authorized = False
        history.retry_evidence_id = ""
        history.retry_reason = ""
        return attempt

    def complete(
        self, attempt: LedgerAttempt, status: ExecutionStatus
    ) -> LedgerAttempt:
        if not isinstance(status, ExecutionStatus):
            status = ExecutionStatus(status)
        history = self._history(attempt.action_key)
        index = attempt.attempt_number - 1
        if index < 0 or index >= len(history.attempts):
            raise UnknownAttemptError(
                attempt.action_key, "attempt was not issued by this ledger"
            )
        current = history.attempts[index]
        if current != attempt or current.status != ExecutionStatus.NOT_EXECUTED:
            raise UnknownAttemptError(
                attempt.action_key, "attempt is unknown or already completed"
            )
        completed = replace(current, status=status)
        history.attempts[index] = completed
        return completed

    def authorize_retry(
        self, action_key: str, *, evidence_id: str, reason: str = ""
    ) -> LedgerAttempt:
        if not evidence_id:
            raise ValueError("evidence_id must not be empty")
        history = self._history(action_key)
        if not history.attempts:
            raise LedgerError(action_key, f"no attempts recorded for {action_key}")
        last = history.attempts[-1]
        if last.status != ExecutionStatus.EXECUTED_UNCONFIRMED:
            raise LedgerError(
                action_key,
                f"retry authorization is only valid for executed_unconfirmed, got {last.status.value}",
            )
        if history.retry_authorized:
            return last
        history.retry_authorized = True
        history.retry_evidence_id = evidence_id
        history.retry_reason = reason
        updated = replace(
            last,
            retry_authorized=True,
            retry_evidence_id=evidence_id,
            retry_reason=reason,
        )
        history.attempts[-1] = updated
        return updated

    def archive(self, action_key: str) -> None:
        history = self._history(action_key)
        history.archived = True

    def is_archived(self, action_key: str) -> bool:
        history = self._histories.get(action_key)
        return bool(history and history.archived)

    def attempts(self, action_key: str) -> tuple[LedgerAttempt, ...]:
        history = self._histories.get(action_key)
        return tuple(history.attempts) if history else ()

    def last_attempt(self, action_key: str) -> LedgerAttempt | None:
        attempts = self.attempts(action_key)
        return attempts[-1] if attempts else None

    def _history(self, action_key: str) -> _History:
        if not isinstance(action_key, str) or not action_key:
            raise ValueError("action_key must not be empty")
        return self._histories.setdefault(action_key, _History([]))


def action_key_for_plan(plan: ActionPlan) -> str:
    """Return a stable replay key that also isolates game generations."""

    if not isinstance(plan, ActionPlan):
        raise TypeError("plan must be an ActionPlan")
    intent_id = plan.intent.intent_id or (
        plan.steps[0].intent.intent_id if plan.steps else ""
    )
    suffix = intent_id or plan.plan_id
    return f"{plan.game_generation}:{suffix}"


__all__ = [
    "ArchivedActionError",
    "AttemptLimitExceeded",
    "ExecutionLedger",
    "ImmediateReplayBlocked",
    "InFlightAttemptError",
    "LedgerAttempt",
    "LedgerError",
    "UnknownAttemptError",
    "action_key_for_plan",
]
