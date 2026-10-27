"""Minimal application ports for the one-game MVP flow."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hslegend.domain import CardDefinition, GameSnapshot, PlanStep

from ..dto import (
    ConfigSnapshot,
    DispatchResult,
    ExecutionRecord,
    GameObservation,
    JournalAppendResult,
    LobbyState,
    RecommendationMode,
    RecommendationObservation,
)


@runtime_checkable
class ConfigRepositoryPort(Protocol):
    """Load one immutable application config snapshot."""

    def load(self) -> ConfigSnapshot:
        ...


@runtime_checkable
class CardCatalogPort(Protocol):
    """Look up static card metadata and normalized capabilities."""

    def get_card(self, card_id: str) -> CardDefinition | None:
        ...


@runtime_checkable
class RecommendationSourcePort(Protocol):
    """Return a normalized recommendation for a snapshot and mode."""

    def recommend(
        self,
        snapshot: GameSnapshot,
        mode: RecommendationMode,
    ) -> RecommendationObservation:
        ...


@runtime_checkable
class GameStateSourcePort(Protocol):
    """Read game observations and wait for a later log revision."""

    def current(self) -> GameObservation:
        ...

    def wait_for_revision_change(
        self,
        after_revision: int,
        timeout_seconds: float,
    ) -> GameObservation | None:
        ...


@runtime_checkable
class LobbyStatePort(Protocol):
    """Read the normalized current lobby phase."""

    def get_state(self) -> LobbyState:
        ...


@runtime_checkable
class ActionDispatchPort(Protocol):
    """Send one declarative step and report transport-level dispatch status."""

    def dispatch(
        self,
        step: PlanStep,
        snapshot: GameSnapshot,
    ) -> DispatchResult:
        ...


@runtime_checkable
class RunJournalPort(Protocol):
    """Append one execution record to a durable journal."""

    def append(self, record: ExecutionRecord) -> JournalAppendResult:
        ...


@runtime_checkable
class ArtifactStorePort(Protocol):
    """Store an evidence/frame reference and return its durable reference."""

    def store(self, source_ref: str, *, kind: str = "evidence") -> str:
        ...


__all__ = [
    "ActionDispatchPort",
    "ArtifactStorePort",
    "CardCatalogPort",
    "ConfigRepositoryPort",
    "GameStateSourcePort",
    "LobbyStatePort",
    "RecommendationSourcePort",
    "RunJournalPort",
]
