"""Immutable application-layer commands, observations, and results."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

from hslegend.domain import GameSnapshot
from hslegend.domain.primitives import PrimitiveMixin, deep_freeze_primitive


class RecommendationMode(str, Enum):
    """The two recommendation surfaces required by the one-game flow."""

    MULLIGAN = "mulligan"
    TURN = "turn"


class LobbyPhase(str, Enum):
    """Normalized lobby phases; adapters own platform-specific details."""

    MAIN_MENU = "main_menu"
    DECK_SELECT = "deck_select"
    MATCHING = "matching"
    IN_GAME = "in_game"
    RESULT = "result"
    UNKNOWN = "unknown"


class DispatchStatus(str, Enum):
    """Whether an action input was sent, rejected, or failed."""

    SENT = "sent"
    REJECTED = "rejected"
    FAILED = "failed"


class ExecutionStatus(str, Enum):
    """Stable action execution states from the interface contract."""

    NOT_EXECUTED = "not_executed"
    EXECUTED_CONFIRMED = "executed_confirmed"
    EXECUTED_UNCONFIRMED = "executed_unconfirmed"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OneGameStatus(str, Enum):
    """Terminal status of a one-game command."""

    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _confidence(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0.0 <= normalized <= 1.0:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")
    return normalized


def _non_negative(value: float, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return normalized


def _freeze_finite_primitive(value: Any) -> Any:
    """Recursively freeze a primitive value and reject non-finite floats."""

    frozen = deep_freeze_primitive(value)

    def validate(item: Any) -> None:
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("config values must contain only finite floats")
        if isinstance(item, tuple):
            for child in item:
                validate(child)

    validate(frozen)
    return frozen


@dataclass(frozen=True, slots=True)
class ConfigSnapshot(PrimitiveMixin):
    """Typed immutable config values loaded by an adapter.

    Values are canonical primitives so the application can carry config without
    importing legacy config objects or retaining mutable dictionaries.
    """

    values: tuple[tuple[str, Any], ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        frozen = _freeze_finite_primitive(self.values)
        if not isinstance(frozen, tuple):
            raise TypeError("config values must be a mapping or sequence")
        for item in frozen:
            if not isinstance(item, tuple) or len(item) != 2:
                raise TypeError("config values must contain key/value pairs")
            if not isinstance(item[0], str):
                raise TypeError("config keys must be strings")
        object.__setattr__(self, "values", frozen)

    def get(self, key: str, default: Any = None) -> Any:
        for item_key, value in self.values:
            if item_key == key:
                return value
        return default


@dataclass(frozen=True, slots=True)
class RecommendationObservation(PrimitiveMixin):
    """Normalized recommendation without clicks, coordinates, or callbacks."""

    mode: RecommendationMode
    instruction: str
    confidence: float
    evidence_id: str
    snapshot_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.mode, RecommendationMode):
            object.__setattr__(self, "mode", RecommendationMode(self.mode))
        object.__setattr__(self, "confidence", _confidence(self.confidence, "confidence"))


@dataclass(frozen=True, slots=True)
class GameObservation(PrimitiveMixin):
    """A game snapshot plus confidence in the source observation."""

    snapshot: GameSnapshot
    source_confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_confidence",
            _confidence(self.source_confidence, "source_confidence"),
        )


@dataclass(frozen=True, slots=True)
class LobbyState(PrimitiveMixin):
    """Normalized lobby phase with source evidence."""

    phase: LobbyPhase
    observed_at: float
    confidence: float = 1.0
    evidence_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.phase, LobbyPhase):
            object.__setattr__(self, "phase", LobbyPhase(self.phase))
        object.__setattr__(self, "observed_at", _non_negative(self.observed_at, "observed_at"))
        object.__setattr__(self, "confidence", _confidence(self.confidence, "confidence"))


@dataclass(frozen=True, slots=True)
class DispatchResult(PrimitiveMixin):
    """Result of sending one declarative plan step to the game."""

    status: DispatchStatus
    reason_code: str = "sent"
    dispatch_id: str = ""
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, DispatchStatus):
            object.__setattr__(self, "status", DispatchStatus(self.status))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))

    @classmethod
    def sent(cls, dispatch_id: str, *evidence_ids: str) -> "DispatchResult":
        return cls(DispatchStatus.SENT, "sent", dispatch_id, tuple(evidence_ids))

    @classmethod
    def rejected(cls, reason_code: str, *evidence_ids: str) -> "DispatchResult":
        return cls(DispatchStatus.REJECTED, reason_code, "", tuple(evidence_ids))

    @classmethod
    def failed(cls, reason_code: str, *evidence_ids: str) -> "DispatchResult":
        return cls(DispatchStatus.FAILED, reason_code, "", tuple(evidence_ids))


@dataclass(frozen=True, slots=True)
class JournalAppendResult(PrimitiveMixin):
    """Durable result of appending one execution record."""

    record_id: str
    durable: bool = True
    duplicate: bool = False
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))


@dataclass(frozen=True, slots=True)
class ExecutionRecord(PrimitiveMixin):
    """One action execution attempt recorded by the run journal."""

    record_id: str
    correlation_id: str
    session_id: str
    game_id: str
    intent_id: str
    plan_id: str
    action_id: str
    before_snapshot_id: str
    after_snapshot_id: str
    status: ExecutionStatus
    step_results: tuple[str, ...] = ()
    error_code: str = ""
    elapsed_seconds: float = 0.0
    evidence_ids: tuple[str, ...] = ()
    journal_record_id: str = ""
    journal_durable: bool = False
    journal_duplicate: bool = False
    journal_evidence_ids: tuple[str, ...] = ()
    release_error: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, ExecutionStatus):
            object.__setattr__(self, "status", ExecutionStatus(self.status))
        object.__setattr__(self, "step_results", tuple(self.step_results))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        object.__setattr__(self, "journal_evidence_ids", tuple(self.journal_evidence_ids))
        object.__setattr__(
            self,
            "elapsed_seconds",
            _non_negative(self.elapsed_seconds, "elapsed_seconds"),
        )


@dataclass(frozen=True, slots=True)
class OneGameCommand(PrimitiveMixin):
    """Request to run at most one game through the future use-case layer."""

    command_id: str
    deck_id: str
    mode: str = "traditional"
    max_turns: int | None = None

    def __post_init__(self) -> None:
        if self.max_turns is None:
            return
        if type(self.max_turns) is not int:
            raise TypeError("max_turns must be an int or None")
        if self.max_turns < 1:
            raise ValueError("max_turns must be positive when provided")


@dataclass(frozen=True, slots=True)
class OneGameResult(PrimitiveMixin):
    """Terminal result of a one-game command; business outcome is separate."""

    command_id: str
    session_id: str
    status: OneGameStatus
    reason_code: str = ""
    game_id: str = ""
    journal_record_id: str = ""
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, OneGameStatus):
            object.__setattr__(self, "status", OneGameStatus(self.status))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))


__all__ = [
    "ConfigSnapshot",
    "DispatchResult",
    "DispatchStatus",
    "ExecutionRecord",
    "ExecutionStatus",
    "GameObservation",
    "JournalAppendResult",
    "LobbyPhase",
    "LobbyState",
    "OneGameCommand",
    "OneGameResult",
    "OneGameStatus",
    "RecommendationMode",
    "RecommendationObservation",
]
