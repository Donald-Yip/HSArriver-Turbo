# -*- coding: utf-8 -*-
"""Local-strategy action adaptation: prepare (预备) and minion abilities.

The local deck-strategy engine emits ``TRADE_CARD`` for both trade and prepare,
and a separate ``USE_MINION_ABILITY`` for on-board ability activation. These
tests pin the two adapter-side contracts that keep the execution layer correct:

* prepare returns the ``tempt_mana_spent`` postcondition (the card comes back to
  hand, so ``hand_card_left`` would never confirm and the flow would retry);
* ability activation resolves the skill-panel index from ``choice_card_id``
  (short names like 愤怒/领域 do not match the full card names).
"""

import unittest
from types import SimpleNamespace

from hslegend.adapters.legacy.recommendation import (
    _adapt_trade_card,
    _adapt_use_ability,
)
from hslegend.domain import ActionKind, ProposedAction, SlotRef
from strategy_entity import StrategyMinion


def _proposed(action, **overrides):
    fields = dict(
        action_id="a1", frame_id="search:5:10", created_at=0.0,
        turn_number=5, log_revision=10,
        raw_instruction="", normalized_instruction="",
        action=action, ocr_confidence=1.0, semantic_confidence=1.0,
    )
    fields.update(overrides)
    return ProposedAction(**fields)


def _state(hand=(), my_minions=(), oppo_minions=()):
    return SimpleNamespace(
        is_my_turn=True,
        game_num_turns_in_play=5,
        my_hand_cards=list(hand),
        my_minions=list(my_minions),
        oppo_minions=list(oppo_minions),
        my_locations=[],
        oppo_locations=[],
        my_hero=None,
        oppo_hero=None,
        my_hero_power=None,
        my_hero_powers=[],
    )


def _card(card_id, entity_id="1", cost=2):
    return SimpleNamespace(
        card_id=card_id, cardtype="SPELL", entity_id=entity_id,
        current_cost=cost, name=card_id)


def _minion(card_id, zone_pos, entity_id):
    return StrategyMinion(
        entity_id=entity_id, card_id=card_id, zone="PLAY",
        zone_pos=zone_pos, current_cost=0, overload=0, is_mine=True,
        attack=5, max_health=5, damage=0, exhausted=0,
        num_turns_in_play=1)


class PrepareAdaptationTests(unittest.TestCase):
    def test_prepare_instruction_uses_tempt_mana_postcondition(self):
        state = _state(hand=[_card("TLC_451")])
        proposed = _proposed(
            ActionKind.TRADE_CARD,
            source=SlotRef("hand", "friendly", 1),
            normalized_instruction="预备手牌1号卡牌",
        )
        adapted = _adapt_trade_card(proposed, state)
        self.assertEqual("tempt_mana_spent", adapted.postcondition)
        self.assertEqual("1", adapted.source_entity_id)

    def test_trade_instruction_keeps_hand_card_left(self):
        state = _state(hand=[_card("GAME_005")])
        proposed = _proposed(
            ActionKind.TRADE_CARD,
            source=SlotRef("hand", "friendly", 1),
            normalized_instruction="交易手牌1号卡牌",
        )
        adapted = _adapt_trade_card(proposed, state)
        self.assertEqual("hand_card_left", adapted.postcondition)


class MinionAbilityAdaptationTests(unittest.TestCase):
    def test_choice_card_id_resolves_skill_panel_index(self):
        body = _minion("BE_013", 1, "9")
        enemy = _minion("CS2_182", 1, "7")
        state = _state(my_minions=[body], oppo_minions=[enemy])
        proposed = _proposed(
            ActionKind.USE_MINION_ABILITY,
            source=SlotRef("board_slot", "friendly", 1),
            target=SlotRef("board_slot", "enemy", 1),
            choice_card_id="BE_013t3",
            choice_name="凝视",
            card_type="ABILITY",
        )
        adapted = _adapt_use_ability(proposed, state)
        self.assertEqual(2, adapted.manual_action.choice_index)
        self.assertEqual("ability_used", adapted.postcondition)
        self.assertEqual("9", adapted.source_entity_id)

    def test_unknown_choice_card_id_fails_closed(self):
        body = _minion("BE_013", 1, "9")
        state = _state(my_minions=[body])
        proposed = _proposed(
            ActionKind.USE_MINION_ABILITY,
            source=SlotRef("board_slot", "friendly", 1),
            choice_card_id="BE_999t9",
            choice_name=None,
        )
        with self.assertRaises(Exception):
            _adapt_use_ability(proposed, state)


if __name__ == "__main__":
    unittest.main()
