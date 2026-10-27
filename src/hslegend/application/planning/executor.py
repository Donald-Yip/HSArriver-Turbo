"""One-action plan execution with gate, dispatch, wait, and journal evidence."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from hslegend.application.dto import (
    DispatchStatus,
    ExecutionRecord,
    ExecutionStatus,
)
from hslegend.application.ports import (
    ActionDispatchPort,
    GameStateSourcePort,
    RunJournalPort,
)
from hslegend.application.runtime import (
    ActionGate,
    OperationCancelled,
    StopToken,
)
from hslegend.domain import ActionPlan, GameSnapshot, PostconditionEvaluator

from .ledger import ExecutionLedger, action_key_for_plan
from .validator import PlanValidator


Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class _ExecutionOutcome:
    status: ExecutionStatus
    reason_code: str
    before_snapshot_id: str
    after_snapshot_id: str
    step_results: tuple[str, ...]
    evidence_ids: tuple[str, ...]


class PlanExecutor:
    """Execute one validated declarative plan and journal every attempt."""

    def __init__(
        self,
        dispatch_port: ActionDispatchPort,
        state_source: GameStateSourcePort,
        journal: RunJournalPort,
        ledger: ExecutionLedger,
        validator: PlanValidator,
        *,
        postconditions: PostconditionEvaluator | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._dispatch_port = dispatch_port
        self._state_source = state_source
        self._journal = journal
        self._ledger = ledger
        self._validator = validator
        self._postconditions = postconditions or PostconditionEvaluator()
        self._clock = clock or time.monotonic

    @property
    def ledger(self) -> ExecutionLedger:
        return self._ledger

    def execute(
        self,
        plan: ActionPlan,
        *,
        stop_token: StopToken,
        action_gate: ActionGate,
        session_id: str = "",
        correlation_id: str = "",
        game_id: str = "",
    ) -> ExecutionRecord:
        if not isinstance(plan, ActionPlan):
            raise TypeError("plan must be an ActionPlan")
        if not isinstance(stop_token, StopToken):
            raise TypeError("stop_token must be a StopToken")
        if not isinstance(action_gate, ActionGate):
            raise TypeError("action_gate must be an ActionGate")

        action_key = action_key_for_plan(plan)
        started_at = float(self._clock())
        lease = action_gate.acquire(session_id or "plan-executor", action_key)
        try:
            attempt = self._ledger.begin(action_key)
            outcome = self._run_attempt(
                plan,
                stop_token=stop_token,
                started_at=started_at,
            )

            completed = self._ledger.complete(attempt, outcome.status)
            record = ExecutionRecord(
                record_id=f"{action_key}:attempt-{completed.attempt_number}",
                correlation_id=correlation_id or session_id,
                session_id=session_id,
                game_id=game_id or f"generation:{plan.game_generation}",
                intent_id=plan.intent.intent_id,
                plan_id=plan.plan_id,
                action_id=action_key,
                before_snapshot_id=outcome.before_snapshot_id,
                after_snapshot_id=outcome.after_snapshot_id,
                status=outcome.status,
                step_results=outcome.step_results,
                error_code=outcome.reason_code,
                elapsed_seconds=max(0.0, float(self._clock()) - started_at),
                evidence_ids=outcome.evidence_ids,
            )
            self._journal.append(record)
            return record
        finally:
            action_gate.release(lease)

    def _run_attempt(
        self,
        plan: ActionPlan,
        *,
        stop_token: StopToken,
        started_at: float,
    ) -> _ExecutionOutcome:
        evidence_ids: list[str] = []
        step_results: list[str] = []
        before_snapshot_id = plan.snapshot_id
        current_snapshot_id = plan.snapshot_id
        any_sent = False

        def remaining_budget() -> float:
            try:
                budget = float(plan.timeout_seconds)
            except (TypeError, ValueError):
                return math.inf
            if not math.isfinite(budget) or budget <= 0.0:
                return math.inf
            remaining = budget - (float(self._clock()) - started_at)
            return remaining if math.isfinite(remaining) else 0.0

        def outcome(
            status: ExecutionStatus,
            reason_code: str,
            *,
            after_snapshot_id: str | None = None,
            extra_evidence: tuple[str, ...] = (),
        ) -> _ExecutionOutcome:
            return _ExecutionOutcome(
                status=status,
                reason_code=reason_code,
                before_snapshot_id=before_snapshot_id,
                after_snapshot_id=after_snapshot_id or current_snapshot_id,
                step_results=tuple(step_results),
                evidence_ids=_unique((*evidence_ids, *extra_evidence)),
            )

        def cancelled_outcome() -> _ExecutionOutcome:
            return outcome(
                (
                    ExecutionStatus.EXECUTED_UNCONFIRMED
                    if any_sent
                    else ExecutionStatus.CANCELLED
                ),
                stop_token.reason or "cancelled",
            )

        def failed_outcome(reason_code: str) -> _ExecutionOutcome:
            return outcome(
                (
                    ExecutionStatus.EXECUTED_UNCONFIRMED
                    if any_sent
                    else ExecutionStatus.FAILED
                ),
                reason_code,
            )

        def guard_before_dispatch() -> _ExecutionOutcome | None:
            if stop_token.is_cancelled():
                return cancelled_outcome()
            if remaining_budget() <= 0.0:
                return failed_outcome("plan_timeout")
            return None

        try:
            cancelled = guard_before_dispatch()
            if cancelled is not None:
                return cancelled

            observation = self._state_source.current()
            current = observation.snapshot
            before_snapshot_id = current.snapshot_id
            current_snapshot_id = current.snapshot_id
            evidence_ids.append(current.snapshot_id)

            validation = self._validator.validate_plan(plan, current)
            if not validation.accepted:
                step_results.append(f"plan:{validation.reason_code}")
                return outcome(
                    ExecutionStatus.REJECTED,
                    validation.reason_code,
                    extra_evidence=validation.details,
                )

            for index, step in enumerate(plan.steps):
                guarded = guard_before_dispatch()
                if guarded is not None:
                    return guarded

                step_validation = self._validator.validate_step(plan, step, current)
                if not step_validation.accepted:
                    step_results.append(
                        f"{step.step_id}:precondition:{step_validation.reason_code}"
                    )
                    return failed_outcome(step_validation.reason_code)

                # Close the validation-to-dispatch race: token/budget may have
                # changed while validation was running.
                guarded = guard_before_dispatch()
                if guarded is not None:
                    return guarded
                dispatch_timeout = remaining_budget()
                if not math.isfinite(dispatch_timeout) or dispatch_timeout <= 0.0:
                    return outcome(
                        ExecutionStatus.EXECUTED_UNCONFIRMED,
                        "plan_timeout",
                    )

                dispatch_snapshot = current
                try:
                    dispatch_result = self._dispatch_port.dispatch(
                        step,
                        current,
                        timeout_seconds=dispatch_timeout,
                    )
                except TimeoutError:
                    step_results.append(f"{step.step_id}:dispatch_timeout")
                    return outcome(
                        ExecutionStatus.EXECUTED_UNCONFIRMED,
                        "plan_timeout",
                    )
                except OperationCancelled as exc:
                    step_results.append(f"{step.step_id}:cancelled:{exc.reason}")
                    return outcome(
                        ExecutionStatus.EXECUTED_UNCONFIRMED,
                        exc.reason,
                    )
                except Exception as exc:
                    reason = _exception_reason(exc)
                    step_results.append(f"{step.step_id}:dispatch_error:{reason}")
                    return outcome(
                        ExecutionStatus.EXECUTED_UNCONFIRMED,
                        reason,
                        extra_evidence=(str(exc),),
                    )

                dispatch_status = dispatch_result.status
                if dispatch_status == DispatchStatus.SENT:
                    any_sent = True
                evidence_ids.extend(dispatch_result.evidence_ids)
                step_results.append(f"{step.step_id}:{dispatch_status.value}")
                if dispatch_status == DispatchStatus.REJECTED:
                    if any_sent:
                        return outcome(
                            ExecutionStatus.EXECUTED_UNCONFIRMED,
                            dispatch_result.reason_code,
                        )
                    return outcome(
                        ExecutionStatus.REJECTED,
                        dispatch_result.reason_code,
                    )
                if dispatch_status == DispatchStatus.FAILED:
                    if any_sent:
                        return outcome(
                            ExecutionStatus.EXECUTED_UNCONFIRMED,
                            dispatch_result.reason_code,
                        )
                    return outcome(
                        ExecutionStatus.FAILED,
                        dispatch_result.reason_code,
                    )

                if stop_token.is_cancelled():
                    return cancelled_outcome()

                remaining = remaining_budget()
                if remaining <= 0.0:
                    return outcome(
                        ExecutionStatus.EXECUTED_UNCONFIRMED,
                        "plan_timeout",
                    )

                wait_timeout = min(step.timeout_seconds, remaining)
                try:
                    after_observation = self._state_source.wait_for_revision_change(
                        current.log_revision, wait_timeout
                    )
                except OperationCancelled as exc:
                    step_results.append(f"{step.step_id}:cancelled:{exc.reason}")
                    return outcome(
                        ExecutionStatus.EXECUTED_UNCONFIRMED,
                        exc.reason,
                    )
                except Exception as exc:
                    reason = _exception_reason(exc)
                    step_results.append(f"{step.step_id}:wait_error:{reason}")
                    return outcome(
                        ExecutionStatus.EXECUTED_UNCONFIRMED,
                        reason,
                        extra_evidence=(str(exc),),
                    )

                if stop_token.is_cancelled():
                    return cancelled_outcome()
                if after_observation is None:
                    return outcome(
                        ExecutionStatus.EXECUTED_UNCONFIRMED,
                        "revision_timeout",
                    )

                after = after_observation.snapshot
                evidence_ids.append(after.snapshot_id)
                current = after
                current_snapshot_id = after.snapshot_id
                if remaining_budget() <= 0.0:
                    return outcome(
                        ExecutionStatus.EXECUTED_UNCONFIRMED,
                        "plan_timeout",
                    )

                conditions = tuple(step.postconditions)
                if index == len(plan.steps) - 1:
                    conditions = (*conditions, *plan.postconditions)
                postcondition = self._postconditions.evaluate(
                    dispatch_snapshot,
                    after,
                    conditions,
                )
                evidence_ids.extend(postcondition.evidence_refs)
                if stop_token.is_cancelled():
                    return cancelled_outcome()
                if postcondition.status == postcondition.FAILED:
                    step_results[-1] += f":{postcondition.reason_code}"
                    return outcome(
                        ExecutionStatus.EXECUTED_UNCONFIRMED,
                        postcondition.reason_code,
                        after_snapshot_id=after.snapshot_id,
                    )
                if postcondition.status == postcondition.UNCONFIRMED:
                    step_results[-1] += f":{postcondition.reason_code}"
                    return outcome(
                        ExecutionStatus.EXECUTED_UNCONFIRMED,
                        postcondition.reason_code,
                        after_snapshot_id=after.snapshot_id,
                    )
                step_results[-1] += ":confirmed"

            guarded = guard_before_dispatch()
            if guarded is not None:
                return guarded
            return outcome(
                ExecutionStatus.EXECUTED_CONFIRMED,
                "postcondition_confirmed",
                after_snapshot_id=current_snapshot_id,
            )
        except OperationCancelled as exc:
            step_results.append(f"execution:cancelled:{exc.reason}")
            return outcome(
                (
                    ExecutionStatus.EXECUTED_UNCONFIRMED
                    if any_sent
                    else ExecutionStatus.CANCELLED
                ),
                exc.reason,
            )
        except Exception as exc:
            reason = _exception_reason(exc)
            step_results.append(f"execution_error:{reason}")
            return outcome(
                (
                    ExecutionStatus.EXECUTED_UNCONFIRMED
                    if any_sent
                    else ExecutionStatus.FAILED
                ),
                reason,
                extra_evidence=(type(exc).__name__, str(exc)),
            )


def _exception_reason(exc: Exception) -> str:
    for attribute in ("reason", "reason_code"):
        value = getattr(exc, attribute, None)
        if value:
            return str(value)
    message = str(exc).strip()
    return message or type(exc).__name__


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


__all__ = ["PlanExecutor"]
