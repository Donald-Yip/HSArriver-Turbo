"""Recommendation adaptation contracts for the application layer."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hslegend.application.recommendation import AdaptedAction, RecommendationContext
from hslegend.domain import ProposedAction


@runtime_checkable
class CardSemanticsPort(Protocol):
    """Read-only card facts required by recommendation adaptation."""

    def get_card(self, card_id: str):
        ...


@runtime_checkable
class RecommendationAdapterPort(Protocol):
    """Adapt a semantic intent against one evidence-pinned context."""

    def adapt(
        self, proposed: ProposedAction, context: RecommendationContext
    ) -> AdaptedAction:
        ...


__all__ = ["CardSemanticsPort", "RecommendationAdapterPort"]
