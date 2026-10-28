"""Compatibility shim for unified hand-target semantics."""

from pathlib import Path

from hslegend.adapters.legacy.card_queries import (
    allows_hand_target,
    clear_legacy_cache,
    friendly_hand_target_card_ids,
    hand_or_board_target_card_ids,
    hand_target_allowed_cardtypes,
    hand_target_cardtype_mismatch,
    hand_target_rule,
)

LEGACY_HAND_TARGET_CARD_IDS = frozenset({"CATA_490", "CATA_563"})
LEGACY_HAND_OR_BOARD_CARD_IDS = frozenset({"CATA_161"})

clear_legacy_cache()

try:
    with (Path(__file__).resolve().parents[2] / "cards.json").open("rb"):
        _data_unavailable = False
except OSError:
    _data_unavailable = True


def is_friendly_hand_target_card(card_id):
    if _data_unavailable:
        return card_id in LEGACY_HAND_TARGET_CARD_IDS
    from hslegend.adapters.legacy import card_queries
    return card_queries.is_friendly_hand_target_card(card_id)


def is_hand_or_board_target_card(card_id):
    if _data_unavailable:
        return card_id in LEGACY_HAND_OR_BOARD_CARD_IDS
    from hslegend.adapters.legacy import card_queries
    return card_queries.is_hand_or_board_target_card(card_id)


__all__ = [
    "LEGACY_HAND_OR_BOARD_CARD_IDS",
    "LEGACY_HAND_TARGET_CARD_IDS",
    "allows_hand_target",
    "friendly_hand_target_card_ids",
    "hand_or_board_target_card_ids",
    "hand_target_allowed_cardtypes",
    "hand_target_cardtype_mismatch",
    "hand_target_rule",
    "is_friendly_hand_target_card",
    "is_hand_or_board_target_card",
]
