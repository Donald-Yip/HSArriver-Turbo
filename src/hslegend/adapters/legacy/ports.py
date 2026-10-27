"""Anti-corruption adapters for stable legacy Hearthstone systems.

Only this package may import legacy root modules or legacy ``src.*`` packages.
It exposes application ports and factories, never use cases or bootstrap objects.
"""

from __future__ import annotations

from dataclasses import dataclass

from hslegend.application.ports import (
    ActionDispatchPort,
    ArtifactStorePort,
    CardCatalogPort,
    ConfigRepositoryPort,
    GameStateSourcePort,
    LobbyStatePort,
    RecommendationSourcePort,
    RunJournalPort,
)


@dataclass(frozen=True, slots=True)
class NativePorts:
    """The complete port bundle consumed by native bootstrap."""

    config_repository: ConfigRepositoryPort
    card_catalog: CardCatalogPort
    recommendation_mulligan: RecommendationSourcePort
    recommendation_turn: RecommendationSourcePort
    game_state: GameStateSourcePort
    lobby_state: LobbyStatePort
    action_dispatch: ActionDispatchPort
    journal: RunJournalPort
    artifact_store: ArtifactStorePort

    def __post_init__(self) -> None:
        expected = (
            ("config_repository", ConfigRepositoryPort),
            ("card_catalog", CardCatalogPort),
            ("recommendation_mulligan", RecommendationSourcePort),
            ("recommendation_turn", RecommendationSourcePort),
            ("game_state", GameStateSourcePort),
            ("lobby_state", LobbyStatePort),
            ("action_dispatch", ActionDispatchPort),
            ("journal", RunJournalPort),
            ("artifact_store", ArtifactStorePort),
        )
        for field_name, port_type in expected:
            value = getattr(self, field_name)
            if not isinstance(value, port_type):
                raise TypeError(f"{field_name} must satisfy {port_type.__name__}")


__all__ = ["NativePorts"]
