"""Compatibility shim for unified ability-panel semantics."""

from hslegend.adapters.legacy.card_queries import (
    card_text,
    is_choose_skill_card,
    is_old_god_card,
    skill_index,
    skill_name_at,
    skill_names,
    skill_panel,
)

# Compatibility view for tooling/tests; the production lookup reads the
# versioned rule pack through ``card_queries``.
def _compatibility_panel_order():
    from hslegend.adapters.legacy.card_queries import _semantics
    return {
        card_id: tuple((name, child) for name, child in _semantics().skill_panel(card_id))
        for card_id, card in _semantics().definitions.items()
        if card.ability_panel
    }


SKILL_PANEL_ORDER = _compatibility_panel_order()

__all__ = [
    "SKILL_PANEL_ORDER",
    "card_text",
    "is_choose_skill_card",
    "is_old_god_card",
    "skill_index",
    "skill_name_at",
    "skill_names",
    "skill_panel",
]
