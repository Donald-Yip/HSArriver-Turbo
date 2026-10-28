"""Application-level recommendation action contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hslegend.domain import GameSnapshot
from hslegend.domain.primitives import PrimitiveMixin


@dataclass(frozen=True, slots=True)
class AdaptedAction(PrimitiveMixin):
    """Declarative adapted recommendation safe for an adapter to execute."""

    action_kind: str
    source_entity_id: str | None = None
    target_entity_id: str | None = None
    postcondition: str = ""
    choice_index: int | None = None
    choice_count: int | None = None
    choice_name: str = ""
    details: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", tuple(self.details))


@dataclass(frozen=True, slots=True)
class RecommendationContext(PrimitiveMixin):
    """Live snapshot paired with an evidence-pinned recommendation."""

    snapshot: GameSnapshot
    instruction: str
    evidence_id: str = ""
    log_revision: int = 0


__all__ = ["AdaptedAction", "RecommendationContext"]
