"""Immutable static card metadata contracts and pure name helpers."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

from .primitives import PrimitiveMixin


class TargetScope(str, Enum):
    """Where a card effect may require the player to choose a target."""

    NONE = "none"
    BOARD = "board"
    HAND = "hand"
    HAND_OR_BOARD = "hand_or_board"


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
    target_scope: TargetScope = TargetScope.NONE
    target_card_types: tuple[str, ...] = ()
    choose_one_options: tuple[str, ...] = ()
    ability_panel: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            scope = self.target_scope if isinstance(self.target_scope, TargetScope) else TargetScope(self.target_scope)
        except ValueError as exc:
            raise ValueError(f"invalid target scope: {self.target_scope!r}") from exc
        object.__setattr__(self, "target_scope", scope)
        normalized_types = []
        for value in self.target_card_types:
            normalized = str(value).strip().upper()
            if normalized and normalized not in normalized_types:
                normalized_types.append(normalized)
        object.__setattr__(self, "target_card_types", tuple(normalized_types))
        object.__setattr__(self, "choose_one_options", tuple(self.choose_one_options))
        object.__setattr__(self, "ability_panel", tuple(self.ability_panel))


_NOISE = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize_card_name(value: object) -> str:
    """Normalize a card name for deterministic identity comparisons."""

    if value is None:
        return ""
    return _NOISE.sub("", unicodedata.normalize("NFKC", str(value))).lower()


def card_names_close(left: object, right: object) -> bool:
    """Return whether two names differ by at most one OCR-like mistake."""

    normalized_left = normalize_card_name(left)
    normalized_right = normalize_card_name(right)
    if not normalized_left or not normalized_right:
        return False
    if normalized_left == normalized_right:
        return True
    if min(len(normalized_left), len(normalized_right)) < 3:
        return False
    if abs(len(normalized_left) - len(normalized_right)) > 1:
        return False
    if len(normalized_left) == len(normalized_right):
        return sum(a != b for a, b in zip(normalized_left, normalized_right)) <= 1
    shorter, longer = (
        (normalized_left, normalized_right)
        if len(normalized_left) < len(normalized_right)
        else (normalized_right, normalized_left)
    )
    index = 0
    skipped = False
    for char in longer:
        if index < len(shorter) and shorter[index] == char:
            index += 1
            continue
        if skipped:
            return False
        skipped = True
    return True


def card_names_match(left: object, right: object) -> bool:
    """Match a displayed card name to a catalog name with conservative OCR tolerance."""

    normalized_left = normalize_card_name(left)
    normalized_right = normalize_card_name(right)
    if not normalized_left or not normalized_right:
        return False
    if normalized_left == normalized_right:
        return True
    if min(len(normalized_left), len(normalized_right)) < 3:
        return False
    if normalized_left in normalized_right or normalized_right in normalized_left:
        return True
    return card_names_close(normalized_left, normalized_right)


__all__ = [
    "CardCapabilitySet",
    "CardDefinition",
    "TargetScope",
    "card_names_close",
    "card_names_match",
    "normalize_card_name",
]
