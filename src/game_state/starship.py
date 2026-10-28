"""Compatibility shim for unified starship semantics."""

from hslegend.adapters.legacy.card_queries import starship_card_ids

LEGACY_STARSHIP_CARD_ID = "SC_999t"


def is_starship_card(card_id) -> bool:
    try:
        return card_id in starship_card_ids()
    except Exception:
        return card_id == LEGACY_STARSHIP_CARD_ID


__all__ = ["LEGACY_STARSHIP_CARD_ID", "is_starship_card", "starship_card_ids"]
