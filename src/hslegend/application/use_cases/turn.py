"""Bounded one-turn loop over declarative plans and the shared executor."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from enum import Enum

from hslegend.application.dto import (
    ExecutionRecord,
    ExecutionStatus,
    GameObservation,
    RecommendationMode,
    RecommendationObservation,
)
from hslegend.application.planning import (
    ArchivedActionError,
    AttemptLimitExceeded,
    ImmediateReplayBlocked,
    InFlightAttemptError,
    InvalidPluginPlanError,
    LedgerError,
    MissingCardDefinitionError,
    PlanBuilder,
    PlanBuilderError,
    PlanExecutor,
    PlanExecutorError,
    PluginConflictError,
    PluginNotFoundError,
    PluginRegistryError,
)
from hslegend.application.ports import (
    GameStateSourcePort,
    RecommendationSourcePort,
)
from hslegend.application.runtime import (
    ActionGate,
    ActionLeaseHeld,
    OperationCancelled,
    StopToken,
)
from hslegend.domain import ActionPlan, GameSnapshot
from hslegend.domain.primitives import PrimitiveMixin

from .turn_intent import TurnIntentError, TurnIntentParser


Clock = Callable[[], float]


class PlayTurnStatus(str, Enum):
    """Terminal status of one bounded turn command."""

    COMPLETED = "completed"
    ENDED = "ended"
    UNCONFIRMED = "unconfirmed"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"
    GATE_BUSY = "gate_busy"


@dataclass(frozen=True, slots=True)
class PlayTurnCommand(PrimitiveMixin):
    """One bounded request to play the current local turn."""

    command_id: str
    max_actions: int = 10
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not isinstance(self.command_id, str) or not self.command_id.strip():
            raise ValueError("command_id must be non-empty text")
        if type(self.max_actions) is not int:
            raise TypeError("max_actions must be an int")
        if self.max_actions < 1:
            raise ValueError("max_actions must be positive")
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (int, float)
        ):
            raise TypeError("timeout_seconds must be a number")
        timeout = float(self.timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0.0:
            raise ValueError("timeout_seconds must be finite and positive")


@dataclass(frozen=True, slots=True)
class PlayTurnResult(PrimitiveMixin):
    """Typed terminal turn result with every action record and evidence ID."""

    command_id: str
    status: PlayTurnStatus
    actions: int = 0
    ended_turn: bool = False
    reason_code: str = ""
    evidence_ids: tuple[str, ...] = ()
    action_records: tuple[ExecutionRecord, ...] = ()
    release_error: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, PlayTurnStatus):
            object.__setattr__(self, "status", PlayTurnStatus(self.status))
        if type(self.actions) is not int or self.actions < 0:
            raise ValueError("actions must be a non-negative int")
        if type(self.ended_turn) is not bool:
            raise TypeError("ended_turn must be a bool")
        object.__setattr__(self, "action_records", tuple(self.action_records))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))


class PlayTurnUseCase:
    """Execute at most one local turn through injected planning contracts."""

    def __init__(
        self,
        recommendation: RecommendationSourcePort,
        state: GameStateSourcePort,
        builder: PlanBuilder,
        executor: PlanExecutor,
        action_gate: ActionGate,
        stop_token: StopToken,
        clock: Clock | None = None,
        parser: TurnIntentParser | None = None,
    ) -> None:
        if not callable(getattr(recommendation, "recommend", None)):
            raise TypeError("recommendation must provide recommend()")
        if not callable(getattr(state, "current", None)) or not callable(
            getattr(state, "wait_for_revision_change", None)
        ):
            raise TypeError("state must provide current() and wait_for_revision_change()")
        if not isinstance(builder, PlanBuilder):
            raise TypeError("builder must be a PlanBuilder")
        if not isinstance(executor, PlanExecutor):
            raise TypeError("executor must be a PlanExecutor")
        if not isinstance(action_gate, ActionGate):
            raise TypeError("action_gate must be an ActionGate")
        if not isinstance(stop_token, StopToken):
            raise TypeError("stop_token must be a StopToken")
        if parser is not None and not isinstance(parser, TurnIntentParser):
            raise TypeError("parser must be a TurnIntentParser or None")
        self._recommendation = recommendation
        self._state = state
        self._builder = builder
        self._executor = executor
        self._action_gate = action_gate
        self._stop_token = stop_token
        self._clock = clock or time.monotonic
        self._parser = parser or TurnIntentParser()

    def execute(self, command: PlayTurnCommand) -> PlayTurnResult:
        """Run one bounded turn, stopping on the first unsafe outcome."""

        if not isinstance(command, PlayTurnCommand):
            raise TypeError("command must be a PlayTurnCommand")

        records: list[ExecutionRecord] = []
        evidence: list[str] = []
        actions = 0
        try:
            deadline = float(self._clock()) + float(command.timeout_seconds)
        except Exception:
            return self._result(command, PlayTurnStatus.FAILED, "clock_error")

        while actions < command.max_actions:
            if self._stop_token.is_cancelled():
                return self._result(
                    command,
                    PlayTurnStatus.CANCELLED,
                    self._stop_token.reason or "cancelled",
                    actions=actions,
                    evidence_ids=evidence,
                    action_records=records,
                )
            if self._remaining(deadline) <= 0.0:
                return self._result(
                    command,
                    PlayTurnStatus.FAILED,
                    "turn_timeout",
                    actions=actions,
                    evidence_ids=evidence,
                    action_records=records,
                )

            try:
                observation = self._state.current()
            except OperationCancelled as exc:
                return self._cancelled(command, exc.reason, actions, evidence, records)
            except Exception:
                return self._failed(
                    command, "game_state_error", actions, evidence, records
                )
            if not isinstance(observation, GameObservation):
                return self._failed(
                    command, "game_state_invalid", actions, evidence, records
                )
            snapshot = observation.snapshot
            evidence.append(snapshot.snapshot_id)
            if snapshot.is_end:
                return self._result(
                    command,
                    PlayTurnStatus.ENDED,
                    "game_ended",
                    actions=actions,
                    evidence_ids=evidence,
                    action_records=records,
                )
            if not snapshot.is_my_turn:
                return self._result(
                    command,
                    PlayTurnStatus.ENDED,
                    "local_turn_ended",
                    actions=actions,
                    evidence_ids=evidence,
                    action_records=records,
                )

            try:
                recommendation = self._recommendation.recommend(
                    snapshot,
                    RecommendationMode.TURN,
                )
            except OperationCancelled as exc:
                return self._cancelled(command, exc.reason, actions, evidence, records)
            except Exception:
                return self._failed(
                    command, "recommendation_error", actions, evidence, records
                )
            if not isinstance(recommendation, RecommendationObservation):
                return self._failed(
                    command, "recommendation_invalid", actions, evidence, records
                )
            evidence.append(recommendation.evidence_id)
            if recommendation.mode is not RecommendationMode.TURN:
                return self._rejected(
                    command, "wrong_mode", actions, evidence, records
                )
            if recommendation.snapshot_id != snapshot.snapshot_id:
                return self._rejected(
                    command, "stale_recommendation", actions, evidence, records
                )

            try:
                fresh_observation = self._state.current()
            except OperationCancelled as exc:
                return self._cancelled(command, exc.reason, actions, evidence, records)
            except Exception:
                return self._failed(
                    command, "game_state_error", actions, evidence, records
                )
            if not isinstance(fresh_observation, GameObservation):
                return self._failed(
                    command, "game_state_invalid", actions, evidence, records
                )
            fresh = fresh_observation.snapshot
            evidence.append(fresh.snapshot_id)
            if self._identity(fresh) != self._identity(snapshot):
                return self._rejected(
                    command, "stale_snapshot", actions, evidence, records
                )
            if not fresh.is_my_turn:
                return self._result(
                    command,
                    PlayTurnStatus.ENDED,
                    "local_turn_ended",
                    actions=actions,
                    evidence_ids=evidence,
                    action_records=records,
                )

            try:
                intent = self._parser.parse(fresh, recommendation.instruction)
            except TurnIntentError as exc:
                return self._rejected(
                    command, exc.reason_code, actions, evidence, records
                )
            except OperationCancelled as exc:
                return self._cancelled(command, exc.reason, actions, evidence, records)
            except Exception:
                return self._failed(
                    command, "turn_parse_error", actions, evidence, records
                )

            intent = replace(
                intent,
                intent_id=f"turn:{command.command_id}:{fresh.snapshot_id}:{actions}",
            )
            try:
                plan = self._builder.build(fresh, intent)
            except PluginConflictError:
                return self._rejected(
                    command, "plugin_conflict", actions, evidence, records
                )
            except PluginNotFoundError:
                return self._rejected(
                    command, "plugin_not_found", actions, evidence, records
                )
            except MissingCardDefinitionError:
                return self._rejected(
                    command, "card_not_found", actions, evidence, records
                )
            except InvalidPluginPlanError:
                return self._failed(
                    command, "invalid_plugin_plan", actions, evidence, records
                )
            except (PluginRegistryError, PlanBuilderError):
                return self._failed(
                    command, "plan_build_failed", actions, evidence, records
                )
            except (TypeError, ValueError):
                return self._rejected(
                    command, "invalid_action", actions, evidence, records
                )
            except OperationCancelled as exc:
                return self._cancelled(command, exc.reason, actions, evidence, records)
            except Exception:
                return self._failed(
                    command, "plan_build_error", actions, evidence, records
                )

            remaining = self._remaining(deadline)
            if remaining <= 0.0:
                return self._failed(
                    command, "turn_timeout", actions, evidence, records
                )
            plan = self._clamp_plan(plan, remaining)

            try:
                record = self._executor.execute(
                    plan,
                    stop_token=self._stop_token,
                    action_gate=self._action_gate,
                    session_id=command.command_id,
                    correlation_id=command.command_id,
                    game_id=f"generation:{fresh.game_generation}",
                    evidence_ids=(snapshot.snapshot_id, recommendation.evidence_id),
                )
            except ActionLeaseHeld:
                return self._result(
                    command,
                    PlayTurnStatus.GATE_BUSY,
                    "action_gate_busy",
                    actions=actions,
                    evidence_ids=evidence,
                    action_records=records,
                )
            except OperationCancelled as exc:
                return self._cancelled(command, exc.reason, actions, evidence, records)
            except LedgerError as exc:
                return self._rejected(
                    command,
                    _ledger_reason(exc),
                    actions,
                    evidence,
                    records,
                    release_error=getattr(exc, "release_error", ""),
                )
            except PlanExecutorError as exc:
                if isinstance(exc.cause, LedgerError):
                    return self._rejected(
                        command,
                        _ledger_reason(exc.cause),
                        actions,
                        evidence,
                        records,
                        release_error=exc.release_error,
                    )
                if isinstance(exc.cause, OperationCancelled):
                    return self._cancelled(
                        command,
                        exc.cause.reason,
                        actions,
                        evidence,
                        records,
                        release_error=exc.release_error,
                    )
                return self._result(
                    command,
                    PlayTurnStatus.UNCONFIRMED,
                    "execution_error",
                    actions=actions,
                    evidence_ids=evidence,
                    action_records=records,
                    release_error=exc.release_error,
                )
            except Exception:
                return self._result(
                    command,
                    PlayTurnStatus.UNCONFIRMED,
                    "execution_error",
                    actions=actions,
                    evidence_ids=evidence,
                    action_records=records,
                )

            records.append(record)
            actions += 1
            evidence.extend(record.evidence_ids)
            evidence.extend(record.journal_evidence_ids)
            if record.release_error:
                return self._result(
                    command,
                    PlayTurnStatus.FAILED,
                    "gate_release_error",
                    actions=actions,
                    evidence_ids=evidence,
                    action_records=records,
                    release_error=record.release_error,
                )
            status = _status_for_record(record)
            if status is not PlayTurnStatus.COMPLETED:
                return self._result(
                    command,
                    status,
                    record.error_code,
                    actions=actions,
                    ended_turn=False,
                    evidence_ids=evidence,
                    action_records=records,
                )

            if self._stop_token.is_cancelled():
                return self._cancelled(
                    command,
                    self._stop_token.reason or "cancelled",
                    actions,
                    evidence,
                    records,
                )
            if intent.action_type == "end_turn":
                return self._result(
                    command,
                    PlayTurnStatus.ENDED,
                    "end_turn",
                    actions=actions,
                    ended_turn=True,
                    evidence_ids=evidence,
                    action_records=records,
                )

            try:
                after_observation = self._state.current()
            except OperationCancelled as exc:
                return self._cancelled(command, exc.reason, actions, evidence, records)
            except Exception:
                return self._failed(
                    command, "game_state_error", actions, evidence, records
                )
            if not isinstance(after_observation, GameObservation):
                return self._failed(
                    command, "game_state_invalid", actions, evidence, records
                )
            after = after_observation.snapshot
            evidence.append(after.snapshot_id)
            if after.game_generation != fresh.game_generation:
                return self._failed(
                    command,
                    "game_generation_changed",
                    actions,
                    evidence,
                    records,
                )
            if after.log_revision <= fresh.log_revision:
                return self._failed(
                    command,
                    "stale_post_action_state",
                    actions,
                    evidence,
                    records,
                )
            if self._stop_token.is_cancelled():
                return self._cancelled(
                    command,
                    self._stop_token.reason or "cancelled",
                    actions,
                    evidence,
                    records,
                )
            if after.is_end:
                return self._result(
                    command,
                    PlayTurnStatus.ENDED,
                    "game_ended",
                    actions=actions,
                    evidence_ids=evidence,
                    action_records=records,
                )
            if not after.is_my_turn:
                return self._result(
                    command,
                    PlayTurnStatus.ENDED,
                    "local_turn_ended",
                    actions=actions,
                    evidence_ids=evidence,
                    action_records=records,
                )
            if actions >= command.max_actions:
                return self._result(
                    command,
                    PlayTurnStatus.COMPLETED,
                    "max_actions_reached",
                    actions=actions,
                    evidence_ids=evidence,
                    action_records=records,
                )

        return self._result(
            command,
            PlayTurnStatus.COMPLETED,
            "max_actions_reached",
            actions=actions,
            evidence_ids=evidence,
            action_records=records,
        )

    def _remaining(self, deadline: float) -> float:
        try:
            return deadline - float(self._clock())
        except Exception:
            return 0.0

    @staticmethod
    def _clamp_plan(plan: ActionPlan, remaining: float) -> ActionPlan:
        timeout = min(float(plan.timeout_seconds), remaining)
        return replace(
            plan,
            timeout_seconds=timeout,
            steps=tuple(
                replace(step, timeout_seconds=min(float(step.timeout_seconds), timeout))
                for step in plan.steps
            ),
        )

    @staticmethod
    def _identity(snapshot: GameSnapshot) -> tuple[str, int, int]:
        return (
            snapshot.snapshot_id,
            snapshot.log_revision,
            snapshot.game_generation,
        )

    def _cancelled(
        self,
        command: PlayTurnCommand,
        reason_code: str,
        actions: int,
        evidence: Iterable[str],
        records: Iterable[ExecutionRecord],
        *,
        release_error: str = "",
    ) -> PlayTurnResult:
        return self._result(
            command,
            PlayTurnStatus.CANCELLED,
            reason_code,
            actions=actions,
            evidence_ids=evidence,
            action_records=records,
            release_error=release_error,
        )

    def _rejected(
        self,
        command: PlayTurnCommand,
        reason_code: str,
        actions: int,
        evidence: Iterable[str],
        records: Iterable[ExecutionRecord],
        *,
        release_error: str = "",
    ) -> PlayTurnResult:
        return self._result(
            command,
            PlayTurnStatus.REJECTED,
            reason_code,
            actions=actions,
            evidence_ids=evidence,
            action_records=records,
            release_error=release_error,
        )

    def _failed(
        self,
        command: PlayTurnCommand,
        reason_code: str,
        actions: int,
        evidence: Iterable[str],
        records: Iterable[ExecutionRecord],
        *,
        release_error: str = "",
    ) -> PlayTurnResult:
        return self._result(
            command,
            PlayTurnStatus.FAILED,
            reason_code,
            actions=actions,
            evidence_ids=evidence,
            action_records=records,
            release_error=release_error,
        )

    @staticmethod
    def _result(
        command: PlayTurnCommand,
        status: PlayTurnStatus,
        reason_code: str,
        *,
        actions: int = 0,
        ended_turn: bool = False,
        evidence_ids: Iterable[str] = (),
        action_records: Iterable[ExecutionRecord] = (),
        release_error: str = "",
    ) -> PlayTurnResult:
        records = tuple(action_records)
        evidence = list(evidence_ids)
        for record in records:
            evidence.extend(record.evidence_ids)
            evidence.extend(record.journal_evidence_ids)
        return PlayTurnResult(
            command_id=command.command_id,
            status=status,
            actions=actions,
            ended_turn=ended_turn,
            reason_code=reason_code,
            evidence_ids=_unique(evidence),
            action_records=records,
            release_error=release_error,
        )


def _status_for_record(record: ExecutionRecord) -> PlayTurnStatus:
    mapping = {
        ExecutionStatus.EXECUTED_CONFIRMED: PlayTurnStatus.COMPLETED,
        ExecutionStatus.EXECUTED_UNCONFIRMED: PlayTurnStatus.UNCONFIRMED,
        ExecutionStatus.REJECTED: PlayTurnStatus.REJECTED,
        ExecutionStatus.FAILED: PlayTurnStatus.FAILED,
        ExecutionStatus.CANCELLED: PlayTurnStatus.CANCELLED,
    }
    return mapping.get(record.status, PlayTurnStatus.FAILED)


def _ledger_reason(exc: LedgerError) -> str:
    if isinstance(exc, ImmediateReplayBlocked):
        return "immediate_replay_blocked"
    if isinstance(exc, InFlightAttemptError):
        return "attempt_in_flight"
    if isinstance(exc, ArchivedActionError):
        return "archived_action"
    if isinstance(exc, AttemptLimitExceeded):
        return "attempt_limit_exceeded"
    return "execution_ledger_error"


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


__all__ = [
    "Clock",
    "PlayTurnCommand",
    "PlayTurnResult",
    "PlayTurnStatus",
    "PlayTurnUseCase",
]
