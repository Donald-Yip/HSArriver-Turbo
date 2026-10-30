"""Recommendation adaptation contracts for the application layer."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

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


@runtime_checkable
class StrategyPlannerPort(Protocol):
    """Local deck-strategy planner used when the box is not the decision source.

    Implemented by the legacy adapter that wraps ``search`` / ``ai``; the
    application layer only sees this protocol so it never imports root packages.
    """

    def plan(self, state: Any, cost: int, stopped=None) -> tuple[list, float]:
        """Return the best action sequence and its score for this turn."""

    def joint_plan(self, state: Any, actions, score, box_action) -> dict | None:
        """Compare a local line with the box first step (joint mode)."""

    def describe(self, action) -> str:
        """One-line Chinese description of an action for the plan overlay."""

    def identity_key(self, state: Any, action) -> tuple:
        """Stable identity for the same action (failure suppression key)."""


__all__ = [
    "CardSemanticsPort",
    "RecommendationAdapterPort",
    "StrategyPlannerPort",
]

