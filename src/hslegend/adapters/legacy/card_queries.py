"""Unified card queries for legacy recommendation adapters."""

from __future__ import annotations

from functools import lru_cache

from hslegend.adapters.cards import default_card_semantics
from hslegend.domain import TargetScope


@lru_cache(maxsize=1)
def _semantics():
    return default_card_semantics()

def card_text(card_id):
    return _semantics().card_text(card_id)


def card_name(card_id):
    card = _semantics().get_card(card_id or "")
    return card.name if card is not None else ""


@lru_cache(maxsize=1)
def choose_one_card_ids():
    return frozenset(
        card_id for card_id, card in _semantics().definitions.items()
        if card.capabilities.choose_one
    )


def choose_one_card_names():
    return {
        card_id: card.name
        for card_id, card in _semantics().definitions.items()
        if card.capabilities.choose_one and card.name
    }


def choose_one_option_card_ids(source_card_id):
    return _semantics().choose_one_option_card_ids(source_card_id)


def choose_one_option_card_ids_ordered(source_card_id):
    card = _semantics().get_card(source_card_id or "")
    return tuple(card.choose_one_options) if card is not None else ()


def is_choose_one_card(card_id):
    return _semantics().is_choose_one_card(card_id)


def choose_one_card_name(card_id):
    return _semantics().choose_one_card_name(card_id)


def is_old_god_card(card_id):
    return _semantics().is_old_god_card(card_id)


def skill_panel(card_id):
    return _semantics().skill_panel(card_id)


def skill_names(card_id):
    return tuple(name for name, _ in skill_panel(card_id))


def skill_name_at(card_id, index):
    panel = skill_panel(card_id)
    if index is None or not 0 <= index < len(panel):
        return None
    return panel[index][0]


def skill_index(card_id, name):
    return _semantics().skill_index(card_id, name)


def ability_requires_target(card_id, index):
    return _semantics().ability_requires_target(card_id, index)


def hero_power_slot(card_id):
    return _semantics().hero_power_slot(card_id)


def is_choose_skill_card(card_id):
    return bool(skill_panel(card_id)) or is_old_god_card(card_id)


@lru_cache(maxsize=1)
def starship_card_ids():
    return frozenset(
        card_id for card_id, card in _semantics().definitions.items()
        if card.capabilities.starship
    )


def is_starship_card(card_id):
    return _semantics().is_starship_card(card_id)


def is_rewind_card(card_id):
    return _semantics().is_rewind_card(card_id)


@lru_cache(maxsize=1)
def friendly_hand_target_card_ids():
    return frozenset(
        card_id for card_id, card in _semantics().definitions.items()
        if card.target_scope is TargetScope.HAND
    )


@lru_cache(maxsize=1)
def hand_or_board_target_card_ids():
    return frozenset(
        card_id for card_id, card in _semantics().definitions.items()
        if card.target_scope is TargetScope.HAND_OR_BOARD
    )


def is_friendly_hand_target_card(card_id):
    return _semantics().is_friendly_hand_target_card(card_id)


def is_hand_or_board_target_card(card_id):
    return _semantics().is_hand_or_board_target_card(card_id)


def hand_target_allowed_cardtypes(card_id):
    return _semantics().hand_target_allowed_cardtypes(card_id)


def hand_target_rule(card_id):
    return _semantics().hand_target_rule(card_id)


def hand_target_cardtype_mismatch(card_id, hand_card):
    return _semantics().hand_target_cardtype_mismatch(card_id, hand_card)


def allows_hand_target(card_id):
    return _semantics().allows_hand_target(card_id)


def clear_legacy_cache():
    """Clear compatibility-layer caches after a module reload or data failure."""
    _semantics.cache_clear()
    for cached in (
        choose_one_card_ids,
        starship_card_ids,
        friendly_hand_target_card_ids,
        hand_or_board_target_card_ids,
    ):
        cached.cache_clear()


__all__ = [name for name in globals() if not name.startswith("_")]
