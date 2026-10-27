"""Bounded mulligan use case built on the declarative plan executor."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
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
    LedgerError,
    PlanExecutor,
    PlanExecutorError,
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
from hslegend.domain import (
    ActionIntent,
    ActionPlan,
    Condition,
    GameSnapshot,
    PlanStep,
)
from hslegend.domain.primitives import PrimitiveMixin


_MULLIGAN_REPLACE = re.compile(r"^替换([1-9]\d*)号位卡牌$")
_KEEP_ALL = "保留全部卡牌"
_MULLIGAN_CHOICE_TYPES = frozenset({"mulligan", "mulligan_choice"})
_CLICKABLE_SLOT_COUNTS = {3: 3, 5: 4}


class MulliganInstructionError(ValueError):
    """A stable, fail-closed reason that a mulligan instruction is unusable."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class MulliganStatus(str, Enum):
    """Terminal status of one bounded mulligan command."""

    COMPLETED = "completed"
    EXECUTED_UNCONFIRMED = "executed_unconfirmed"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"
    GATE_BUSY = "gate_busy"


@dataclass(frozen=True, slots=True)
class MulliganCommand(PrimitiveMixin):
    """One bounded request to resolve the current mulligan surface."""

    command_id: str
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        if not isinstance(self.command_id, str) or not self.command_id.strip():
            raise ValueError("command_id must be non-empty text")
        if isinstance(self.timeout_seconds, bool) or not isinstance(
            self.timeout_seconds, (int, float)
        ):
            raise TypeError("timeout_seconds must be a number")
        timeout = float(self.timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0.0:
            raise ValueError("timeout_seconds must be finite and positive")


@dataclass(frozen=True, slots=True)
class MulliganResult(PrimitiveMixin):
    """Typed terminal mulligan result with durable execution evidence."""

    command_id: str
    status: MulliganStatus
    reason_code: str
    instruction: str = ""
    selected_indexes: tuple[int, ...] = ()
    snapshot_id: str = ""
    execution_record: ExecutionRecord | None = None
    journal_record_id: str = ""
    journal_durable: bool = False
    journal_duplicate: bool = False
    evidence_ids: tuple[str, ...] = ()
    release_error: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, MulliganStatus):
            object.__setattr__(self, "status", MulliganStatus(self.status))
        object.__setattr__(self, "selected_indexes", tuple(self.selected_indexes))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))

    @property
    def ok(self) -> bool:
        return self.status is MulliganStatus.COMPLETED


def parse_mulligan_instruction(instruction: str) -> tuple[int, ...]:
    """Parse exact replacement lines and return sorted unique 1-based slots.

    ``保留全部卡牌`` maps to an empty tuple. Unknown or mixed instructions are
    rejected rather than guessed from OCR-like noise.
    """

    if not isinstance(instruction, str):
        raise TypeError("instruction must be text")
    lines = [line.strip() for line in instruction.splitlines() if line.strip()]
    if not lines:
        raise MulliganInstructionError("empty_instruction")
    if _KEEP_ALL in lines:
        if len(lines) != 1:
            raise MulliganInstructionError("ambiguous_instruction")
        return ()

    slots: set[int] = set()
    for line in lines:
        match = _MULLIGAN_REPLACE.fullmatch(line)
        if match is None:
            raise MulliganInstructionError("unsupported_instruction")
        slots.add(int(match.group(1)))
    return tuple(sorted(slots))


def validate_mulligan_selection(
    slots: Iterable[int],
    hand_size: int,
) -> tuple[int, ...]:
    """Validate legacy 1-based slots and return sorted zero-based hand indexes.

    Legacy behavior exposes 3 clickable slots for a 3-card hand and 4 clickable
    slots for a 5-card hand; slot 1 maps to hand index 0.
    """

    if type(hand_size) is not int:
        raise TypeError("hand_size must be an int")
    clickable_count = _CLICKABLE_SLOT_COUNTS.get(hand_size)
    if clickable_count is None:
        raise MulliganInstructionError("unsupported_hand_size")

    normalized: set[int] = set()
    for slot in slots:
        if type(slot) is not int:
            raise TypeError("mulligan slots must be ints")
        if slot < 1:
            raise MulliganInstructionError("mulligan_slot_invalid")
        normalized.add(slot)
    if any(slot > clickable_count for slot in normalized):
        raise MulliganInstructionError("mulligan_slot_invalid")
    return tuple(slot - 1 for slot in sorted(normalized))


def selected_indexes_for_instruction(instruction: str, hand_size: int) -> tuple[int, ...]:
    """Parse and validate one instruction against a hand size."""

    return validate_mulligan_selection(
        parse_mulligan_instruction(instruction),
        hand_size,
    )


def build_mulligan_plan(
    snapshot: GameSnapshot,
    selected_indexes: Iterable[int],
    *,
    command_id: str,
    timeout_seconds: float,
) -> ActionPlan:
    """Build a pinned plan with non-waiting replaces and one confirming step."""

    if not isinstance(snapshot, GameSnapshot):
        raise TypeError("snapshot must be a GameSnapshot")
    indexes = tuple(sorted({int(index) for index in selected_indexes}))
    if any(index < 0 or index >= len(snapshot.my_player.hand) for index in indexes):
        raise ValueError("selected index is outside the current hand")

    steps: list[PlanStep] = []
    for index in indexes:
        entity = snapshot.my_player.hand[index]
        steps.append(
            PlanStep(
                step_id=f"mulligan:replace:{index + 1}",
                intent=ActionIntent(
                    action_type="mulligan_replace",
                    source=entity,
                    choice_index=index,
                    intent_id=f"{command_id}:replace:{index + 1}",
                ),
                preconditions=(
                    Condition("entity_exists", entity_id=entity.entity_id),
                    Condition(
                        "entity_zone",
                        entity_id=entity.entity_id,
                        expected="hand",
                    ),
                ),
                timeout_seconds=float(timeout_seconds),
                wait_for_change=False,
            )
        )
    steps.append(
        PlanStep(
            step_id="mulligan:confirm",
            intent=ActionIntent(
                action_type="mulligan_confirm",
                intent_id=f"{command_id}:confirm",
            ),
            postconditions=(Condition("snapshot_changed"),),
            timeout_seconds=float(timeout_seconds),
            wait_for_change=True,
        )
    )
    return ActionPlan(
        plan_id=f"mulligan:{command_id}:{snapshot.snapshot_id}",
        snapshot_id=snapshot.snapshot_id,
        snapshot_revision=snapshot.log_revision,
        game_generation=snapshot.game_generation,
        intent=ActionIntent(action_type="mulligan", intent_id=command_id),
        steps=tuple(steps),
        timeout_seconds=float(timeout_seconds),
        confirmation_required=True,
    )


class MulliganUseCase:
    """Resolve one recommendation against one stable snapshot and execute once."""

    def __init__(
        self,
        recommendation: RecommendationSourcePort,
        state: GameStateSourcePort,
        action_gate: ActionGate,
        stop_token: StopToken,
        executor: PlanExecutor,
    ) -> None:
        if not callable(getattr(recommendation, "recommend", None)):
            raise TypeError("recommendation must provide recommend()")
        if not callable(getattr(state, "current", None)) or not callable(
            getattr(state, "wait_for_revision_change", None)
        ):
            raise TypeError("state must provide current() and wait_for_revision_change()")
        if not isinstance(action_gate, ActionGate):
            raise TypeError("action_gate must be an ActionGate")
        if not isinstance(stop_token, StopToken):
            raise TypeError("stop_token must be a StopToken")
        if not isinstance(executor, PlanExecutor):
            raise TypeError("executor must be a PlanExecutor")
        self._recommendation = recommendation
        self._state = state
        self._action_gate = action_gate
        self._stop_token = stop_token
        self._executor = executor

    def run(
        self,
        command_id: str = "mulligan",
        timeout_seconds: float = 10.0,
    ) -> MulliganResult:
        """Convenience wrapper for callers without a command object."""

        return self.execute(
            MulliganCommand(
                command_id=command_id,
                timeout_seconds=timeout_seconds,
            )
        )

    def execute(self, command: MulliganCommand) -> MulliganResult:
        """Execute one mulligan command; ambiguous inputs are never dispatched."""

        if not isinstance(command, MulliganCommand):
            raise TypeError("command must be a MulliganCommand")

        try:
            self._stop_token.raise_if_cancelled()
        except OperationCancelled as exc:
            return self._result(command, MulliganStatus.CANCELLED, exc.reason)

        evidence_ids: list[str] = []
        try:
            observation = self._state.current()
            if not isinstance(observation, GameObservation):
                return self._result(
                    command,
                    MulliganStatus.FAILED,
                    "game_state_invalid",
                )
            snapshot = observation.snapshot
            evidence_ids.append(snapshot.snapshot_id)
        except OperationCancelled as exc:
            return self._result(command, MulliganStatus.CANCELLED, exc.reason)
        except Exception:
            return self._result(command, MulliganStatus.FAILED, "game_state_error")

        try:
            recommendation = self._recommendation.recommend(
                snapshot,
                RecommendationMode.MULLIGAN,
            )
        except OperationCancelled as exc:
            return self._result(command, MulliganStatus.CANCELLED, exc.reason)
        except Exception:
            return self._result(
                command,
                MulliganStatus.FAILED,
                "recommendation_error",
                snapshot_id=snapshot.snapshot_id,
                evidence_ids=tuple(evidence_ids),
            )
        if not isinstance(recommendation, RecommendationObservation):
            return self._result(
                command,
                MulliganStatus.FAILED,
                "recommendation_invalid",
                snapshot_id=snapshot.snapshot_id,
                evidence_ids=tuple(evidence_ids),
            )
        evidence_ids.append(recommendation.evidence_id)
        common = {
            "instruction": recommendation.instruction,
            "snapshot_id": snapshot.snapshot_id,
            "evidence_ids": tuple(_unique(evidence_ids)),
        }
        if recommendation.mode is not RecommendationMode.MULLIGAN:
            return self._result(
                command,
                MulliganStatus.REJECTED,
                "wrong_mode",
                **common,
            )
        if recommendation.snapshot_id != snapshot.snapshot_id:
            return self._result(
                command,
                MulliganStatus.REJECTED,
                "stale_recommendation",
                **common,
            )

        try:
            fresh_observation = self._state.current()
            if not isinstance(fresh_observation, GameObservation):
                return self._result(
                    command,
                    MulliganStatus.FAILED,
                    "game_state_invalid",
                    **common,
                )
            fresh = fresh_observation.snapshot
            evidence_ids.append(fresh.snapshot_id)
            common["evidence_ids"] = tuple(_unique(evidence_ids))
            if self._identity(fresh) != self._identity(snapshot):
                return self._result(
                    command,
                    MulliganStatus.REJECTED,
                    "stale_snapshot",
                    **common,
                )
            if not self._has_mulligan_surface(fresh):
                return self._result(
                    command,
                    MulliganStatus.REJECTED,
                    "mulligan_surface_missing",
                    **common,
                )
            selected_indexes = selected_indexes_for_instruction(
                recommendation.instruction,
                len(fresh.my_player.hand),
            )
        except OperationCancelled as exc:
            return self._result(
                command,
                MulliganStatus.CANCELLED,
                exc.reason,
                **common,
            )
        except MulliganInstructionError as exc:
            return self._result(
                command,
                MulliganStatus.REJECTED,
                exc.reason_code,
                **common,
            )
        except Exception:
            return self._result(
                command,
                MulliganStatus.FAILED,
                "validation_error",
                **common,
            )

        plan = build_mulligan_plan(
            fresh,
            selected_indexes,
            command_id=command.command_id,
            timeout_seconds=command.timeout_seconds,
        )
        try:
            record = self._executor.execute(
                plan,
                stop_token=self._stop_token,
                action_gate=self._action_gate,
                session_id=command.command_id,
                correlation_id=command.command_id,
                game_id=f"generation:{fresh.game_generation}",
                evidence_ids=(recommendation.evidence_id,),
            )
        except ActionLeaseHeld:
            return self._result(
                command,
                MulliganStatus.GATE_BUSY,
                "action_gate_busy",
                selected_indexes=selected_indexes,
                **common,
            )
        except OperationCancelled as exc:
            return self._result(
                command,
                MulliganStatus.CANCELLED,
                exc.reason,
                selected_indexes=selected_indexes,
                **common,
            )
        except LedgerError as exc:
            return self._result(
                command,
                MulliganStatus.REJECTED,
                _ledger_reason(exc),
                selected_indexes=selected_indexes,
                release_error=getattr(exc, "release_error", ""),
                **common,
            )
        except PlanExecutorError as exc:
            if isinstance(exc.cause, LedgerError):
                return self._result(
                    command,
                    MulliganStatus.REJECTED,
                    _ledger_reason(exc.cause),
                    selected_indexes=selected_indexes,
                    release_error=exc.release_error,
                    **common,
                )
            if isinstance(exc.cause, OperationCancelled):
                return self._result(
                    command,
                    MulliganStatus.CANCELLED,
                    exc.cause.reason,
                    selected_indexes=selected_indexes,
                    release_error=exc.release_error,
                    **common,
                )
            return self._result(
                command,
                MulliganStatus.EXECUTED_UNCONFIRMED,
                "execution_error",
                selected_indexes=selected_indexes,
                release_error=exc.release_error,
                **common,
            )
        except Exception:
            return self._result(
                command,
                MulliganStatus.EXECUTED_UNCONFIRMED,
                "execution_error",
                selected_indexes=selected_indexes,
                **common,
            )

        status = _status_for_record(record)
        result_evidence = _unique((*common["evidence_ids"], *record.evidence_ids))
        journal_record_id = record.journal_record_id
        return self._result(
            command,
            status,
            record.error_code,
            instruction=recommendation.instruction,
            selected_indexes=selected_indexes,
            snapshot_id=record.before_snapshot_id or fresh.snapshot_id,
            execution_record=record,
            journal_record_id=journal_record_id,
            journal_durable=record.journal_durable,
            journal_duplicate=record.journal_duplicate,
            evidence_ids=result_evidence,
            release_error=record.release_error,
        )

    def _result(
        self,
        command: MulliganCommand,
        status: MulliganStatus,
        reason_code: str,
        *,
        instruction: str = "",
        selected_indexes: Iterable[int] = (),
        snapshot_id: str = "",
        execution_record: ExecutionRecord | None = None,
        journal_record_id: str = "",
        journal_durable: bool = False,
        journal_duplicate: bool = False,
        evidence_ids: Iterable[str] = (),
        release_error: str = "",
    ) -> MulliganResult:
        return MulliganResult(
            command_id=command.command_id,
            status=status,
            reason_code=reason_code,
            instruction=instruction,
            selected_indexes=tuple(selected_indexes),
            snapshot_id=snapshot_id,
            execution_record=execution_record,
            journal_record_id=journal_record_id,
            journal_durable=journal_durable,
            journal_duplicate=journal_duplicate,
            evidence_ids=_unique(evidence_ids),
            release_error=release_error,
        )

    @staticmethod
    def _identity(snapshot: GameSnapshot) -> tuple[str, int, int]:
        return (
            snapshot.snapshot_id,
            snapshot.log_revision,
            snapshot.game_generation,
        )

    @staticmethod
    def _has_mulligan_surface(snapshot: GameSnapshot) -> bool:
        for choice in snapshot.choices:
            raw = getattr(choice.choice_type, "value", choice.choice_type)
            if str(raw).strip().lower() in _MULLIGAN_CHOICE_TYPES:
                return True
        return False


def _status_for_record(record: ExecutionRecord) -> MulliganStatus:
    mapping = {
        ExecutionStatus.EXECUTED_CONFIRMED: MulliganStatus.COMPLETED,
        ExecutionStatus.EXECUTED_UNCONFIRMED: MulliganStatus.EXECUTED_UNCONFIRMED,
        ExecutionStatus.REJECTED: MulliganStatus.REJECTED,
        ExecutionStatus.FAILED: MulliganStatus.FAILED,
        ExecutionStatus.CANCELLED: MulliganStatus.CANCELLED,
    }
    return mapping.get(record.status, MulliganStatus.FAILED)


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
    "MulliganCommand",
    "MulliganInstructionError",
    "MulliganResult",
    "MulliganStatus",
    "MulliganUseCase",
    "build_mulligan_plan",
    "parse_mulligan_instruction",
    "selected_indexes_for_instruction",
    "validate_mulligan_selection",
]
