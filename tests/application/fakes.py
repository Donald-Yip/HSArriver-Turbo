"""Deterministic in-memory fakes for application port contract tests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from hslegend.application import (
    ConfigSnapshot,
    DispatchResult,
    ExecutionRecord,
    GameObservation,
    JournalAppendResult,
    LobbyState,
    RecommendationMode,
    RecommendationObservation,
)
from hslegend.domain import CardDefinition, GameSnapshot, PlanStep


class ConfigRepositoryFake:
    def __init__(self, config: ConfigSnapshot | None = None) -> None:
        self._config = config or ConfigSnapshot()
        self.load_count = 0

    def load(self) -> ConfigSnapshot:
        self.load_count += 1
        return self._config


class CardCatalogFake:
    def __init__(self, cards: Iterable[CardDefinition] = ()) -> None:
        self._cards = {card.card_id: card for card in cards}
        self.lookups: list[str] = []

    def get_card(self, card_id: str) -> CardDefinition | None:
        self.lookups.append(card_id)
        return self._cards.get(card_id)


class RecommendationSourceFake:
    def __init__(
        self,
        observations: Mapping[RecommendationMode, RecommendationObservation],
    ) -> None:
        self._observations = dict(observations)
        self.calls: list[tuple[GameSnapshot, RecommendationMode]] = []

    def recommend(
        self,
        snapshot: GameSnapshot,
        mode: RecommendationMode,
    ) -> RecommendationObservation:
        self.calls.append((snapshot, mode))
        return self._observations[mode]


class GameStateSourceFake:
    def __init__(self, observations: Sequence[GameObservation]) -> None:
        if not observations:
            raise ValueError("at least one game observation is required")
        self._observations = list(observations)
        self.wait_calls: list[tuple[int, float]] = []

    def current(self) -> GameObservation:
        return self._observations[-1]

    def wait_for_revision_change(
        self,
        after_revision: int,
        timeout_seconds: float,
    ) -> GameObservation | None:
        self.wait_calls.append((after_revision, timeout_seconds))
        for observation in self._observations:
            if observation.snapshot.log_revision > after_revision:
                return observation
        return None

    def push(self, observation: GameObservation) -> None:
        self._observations.append(observation)


class LobbyStateSourceFake:
    def __init__(self, states: Sequence[LobbyState]) -> None:
        if not states:
            raise ValueError("at least one lobby state is required")
        self._states = list(states)
        self.timeout_calls: list[float] = []

    def get_state(self, *, timeout_seconds: float) -> LobbyState:
        self.timeout_calls.append(timeout_seconds)
        return self._states[-1]

    def push(self, state: LobbyState) -> None:
        self._states.append(state)


class ActionDispatchFake:
    def __init__(self, results: Iterable[DispatchResult] = ()) -> None:
        self._results = list(results)
        self.calls: list[tuple[PlanStep, GameSnapshot]] = []
        self.timeout_calls: list[float] = []

    def dispatch(
        self,
        step: PlanStep,
        snapshot: GameSnapshot,
        *,
        timeout_seconds: float,
    ) -> DispatchResult:
        self.calls.append((step, snapshot))
        self.timeout_calls.append(timeout_seconds)
        if not self._results:
            raise LookupError("no dispatch result queued")
        return self._results.pop(0)


class RunJournalFake:
    def __init__(self) -> None:
        self._records: list[ExecutionRecord] = []

    def append(self, record: ExecutionRecord) -> JournalAppendResult:
        self._records.append(record)
        return JournalAppendResult(
            record_id=f"journal-{len(self._records)}",
            durable=True,
            evidence_ids=record.evidence_ids,
        )

    @property
    def records(self) -> tuple[ExecutionRecord, ...]:
        return tuple(self._records)


class ArtifactStoreFake:
    def __init__(self) -> None:
        self._stored: list[tuple[str, str]] = []

    def store(self, source_ref: str, *, kind: str = "evidence") -> str:
        self._stored.append((source_ref, kind))
        return f"artifact-{len(self._stored)}"

    @property
    def stored(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._stored)


__all__ = [
    "ActionDispatchFake",
    "ArtifactStoreFake",
    "CardCatalogFake",
    "ConfigRepositoryFake",
    "GameStateSourceFake",
    "LobbyStateSourceFake",
    "RecommendationSourceFake",
    "RunJournalFake",
]
