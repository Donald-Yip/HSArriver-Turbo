"""Compatibility shim for unified choose-one semantics."""

from hslegend.adapters.legacy.card_queries import (
    card_text,
    choose_one_card_ids,
    choose_one_card_name,
    choose_one_card_names,
    choose_one_option_card_ids,
    is_choose_one_card,
)

__all__ = [
    "card_text",
    "choose_one_card_ids",
    "choose_one_card_name",
    "choose_one_card_names",
    "choose_one_option_card_ids",
    "is_choose_one_card",
]
