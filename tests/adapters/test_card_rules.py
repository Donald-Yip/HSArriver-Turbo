"""Tests for declarative card rule loading over cards.json metadata."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hslegend.adapters.cards import CardCatalogError, JsonCardSemantics
from hslegend.domain import TargetScope


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


CARDS = [
    {
        "id": "MINION_1",
        "name": "技能随从",
        "type": "MINION",
        "text": "在本局对战中，你可以使用全部技能。",
    },
    {
        "id": "MINION_1t1",
        "name": "技能一",
        "type": "SPELL",
        "text": "造成2点伤害。",
    },
    {
        "id": "MINION_1t2",
        "name": "技能二",
        "type": "SPELL",
        "text": "获得2点护甲值。",
    },
    {
        "id": "HAND_CARD",
        "name": "手牌目标",
        "type": "MINION",
        "text": "战吼：选择你手牌中的一张法术牌。",
    },
    {
        "id": "CHOICE_CARD",
        "name": "抉择牌",
        "type": "SPELL",
        "mechanics": ["CHOOSE_ONE"],
        "text": "抉择：造成2点伤害；或获得2点护甲值。",
    },
    {
        "id": "CHOICE_CARDa",
        "name": "选项一",
        "type": "SPELL",
        "text": "造成2点伤害。",
    },
    {
        "id": "CHOICE_CARDb",
        "name": "选项二",
        "type": "SPELL",
        "text": "获得2点护甲值。",
    },
]


class CardRulePackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cards = self.root / "cards.json"
        write_json(self.cards, CARDS)

    def _catalog(self, cards):
        rules = self.root / "rules.json"
        write_json(rules, {"schema_version": 1, "cards": cards})
        return JsonCardSemantics(self.cards, rules_path=rules)

    def test_rule_pack_supplies_measured_ability_order(self) -> None:
        catalog = self._catalog({
            "MINION_1": {
                "ability_panel": ["MINION_1t1", "MINION_1t2"],
                "ability_target_required": [False, True],
            }
        })
        card = catalog.get_card("MINION_1")
        self.assertEqual(("MINION_1t1", "MINION_1t2"), card.ability_panel)
        self.assertEqual((False, True), card.ability_target_required)
        self.assertFalse(catalog.ability_requires_target("MINION_1", 0))
        self.assertTrue(catalog.ability_requires_target("MINION_1", 1))

    def test_metadata_derives_choose_one_options(self) -> None:
        catalog = self._catalog({})
        card = catalog.get_card("CHOICE_CARD")
        self.assertEqual(("CHOICE_CARDa", "CHOICE_CARDb"), card.choose_one_options)

    def test_metadata_derives_hand_target_scope_and_types(self) -> None:
        catalog = self._catalog({})
        card = catalog.get_card("HAND_CARD")
        self.assertEqual(TargetScope.HAND, card.target_scope)
        self.assertEqual(("SPELL",), card.target_card_types)

    def test_rule_pack_can_override_derived_scope(self) -> None:
        catalog = self._catalog({
            "HAND_CARD": {"target_scope": "hand_or_board"}
        })
        self.assertEqual(TargetScope.HAND_OR_BOARD, catalog.get_card("HAND_CARD").target_scope)

    def test_unknown_rule_card_and_panel_child_fail_closed(self) -> None:
        with self.assertRaisesRegex(CardCatalogError, "unknown card"):
            self._catalog({"MISSING": {}}).get_card("MINION_1")
        with self.assertRaisesRegex(CardCatalogError, "unknown ability panel child"):
            self._catalog({
                "MINION_1": {"ability_panel": ["NOT_A_CARD"]}
            }).get_card("MINION_1")

    def test_duplicate_ability_child_fails_closed(self) -> None:
        with self.assertRaisesRegex(CardCatalogError, "duplicate ability panel child"):
            self._catalog({
                "MINION_1": {"ability_panel": ["MINION_1t1", "MINION_1t1"]}
            }).get_card("MINION_1")

    def test_hero_power_slot_is_declared_data(self) -> None:
        cards = json.loads(self.cards.read_text(encoding="utf-8"))
        cards.append({
            "id": "HERO_POWER_2",
            "name": "第二技能",
            "type": "HERO_POWER",
            "text": "使一个随从获得+3攻击力。",
        })
        cards_path = self.root / "cards_with_hero_power.json"
        rules_path = self.root / "hero_power_rules.json"
        write_json(cards_path, cards)
        write_json(rules_path, {"schema_version": 1, "cards": {
            "HERO_POWER_2": {"hero_power_slot": "secondary"}
        }})
        catalog = JsonCardSemantics(cards_path, rules_path=rules_path)
        self.assertEqual("secondary",
                         catalog.hero_power_slot("HERO_POWER_2").value)

    def test_target_contract_length_must_match_panel(self) -> None:
        with self.assertRaisesRegex(CardCatalogError,
                                    "ability_target_required must match"):
            self._catalog({
                "MINION_1": {
                    "ability_panel": ["MINION_1t1", "MINION_1t2"],
                    "ability_target_required": [True],
                }
            }).get_card("MINION_1")


if __name__ == "__main__":
    unittest.main()
