"""Immutable static card metadata contracts."""

from __future__ import annotations

from dataclasses import dataclass, field

from .primitives import PrimitiveMixin


@dataclass(frozen=True, slots=True)
class CardCapabilitySet(PrimitiveMixin):
    """Normalized card facts used by rule and plan generation."""

    targetable: bool = False
    hand_target: bool = False
    board_target: bool = False
    choose_one: bool = False
    discover: bool = False
    starship: bool = False
    location: bool = False
    trade: bool = False
    timeline: bool = False


@dataclass(frozen=True, slots=True)
class CardDefinition(PrimitiveMixin):
    """Static card metadata; never a live game entity."""

    card_id: str
    name: str = ""
    mana_cost: int = 0
    card_type: str = ""
    text: str = ""
    capabilities: CardCapabilitySet = field(default_factory=CardCapabilitySet)
