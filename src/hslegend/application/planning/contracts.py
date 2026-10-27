"""Declarative planning contracts shared by application and plugins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from hslegend.domain import ActionIntent, ActionPlan, CardDefinition, GameSnapshot


@dataclass(frozen=True, slots=True)
class PlanContext:
    """Read-only inputs exposed to a plugin during plan generation."""

    snapshot: GameSnapshot
    intent: ActionIntent
    card: CardDefinition | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, GameSnapshot):
            raise TypeError("snapshot must be a GameSnapshot")
        if not isinstance(self.intent, ActionIntent):
            raise TypeError("intent must be an ActionIntent")
        if self.card is not None and not isinstance(self.card, CardDefinition):
            raise TypeError("card must be a CardDefinition or None")
        if self.intent.card_id is not None and self.card is not None:
            if self.intent.card_id != self.card.card_id:
                raise ValueError("card does not match intent.card_id")

    @property
    def card_id(self) -> str | None:
        return self.intent.card_id or (self.card.card_id if self.card else None)

    @property
    def is_targeted(self) -> bool:
        if self.intent.target is not None:
            return True
        if self.card is None:
            return False
        capabilities = self.card.capabilities
        return (
            capabilities.targetable
            or capabilities.hand_target
            or capabilities.board_target
        )

    def capability(self, name: str) -> bool:
        if self.card is None:
            return False
        capabilities = self.card.capabilities
        if not hasattr(capabilities, name):
            raise ValueError(f"unknown card capability: {name}")
        return bool(getattr(capabilities, name))


@runtime_checkable
class PlanPlugin(Protocol):
    """Pure extension point returning a declarative plan only."""

    @property
    def plugin_id(self) -> str:
        ...

    def build(self, context: PlanContext) -> ActionPlan:
        ...


__all__ = ["PlanContext", "PlanPlugin"]
