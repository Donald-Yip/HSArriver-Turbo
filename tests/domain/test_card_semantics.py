"""Tests for pure card semantic contracts."""

from __future__ import annotations

import unittest

from hslegend.domain import CardDefinition, TargetScope
from hslegend.domain.cards import card_names_close, card_names_match, normalize_card_name


class CardSemanticTests(unittest.TestCase):
    def test_target_scope_is_normalized_and_immutable(self) -> None:
        card = CardDefinition(
            "CARD_1",
            target_scope="hand",
            target_card_types=("SPELL", "spell"),
            choose_one_options=("CARD_1a",),
            ability_panel=("CARD_1t1",),
        )
        self.assertIs(TargetScope.HAND, card.target_scope)
        self.assertEqual(("SPELL",), card.target_card_types)

    def test_invalid_target_scope_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CardDefinition("CARD_1", target_scope="both")

    def test_card_name_normalization_ignores_punctuation_and_width(self) -> None:
        self.assertEqual("克苏恩帝国之眼", normalize_card_name("克苏恩，帝国之眼"))
        self.assertEqual("abc123", normalize_card_name("ＡＢＣ－１２３"))

    def test_card_names_match_existing_target_name_rules(self) -> None:
        self.assertTrue(card_names_match("克苏恩，帝国之眼", "克苏恩帝国之眼"))
        self.assertTrue(card_names_match("克苏恩的愤怒", "克苏恩的惯怒"))
        self.assertFalse(card_names_close("克苏恩的愤怒", "克苏恩的"))
        self.assertTrue(card_names_match("克苏恩的愤怒", "克苏恩的"))

    def test_short_names_do_not_accept_single_character_typos(self) -> None:
        self.assertTrue(card_names_close("冰雪", "冰雪"))
        self.assertFalse(card_names_close("冰雪", "冰霜"))


if __name__ == "__main__":
    unittest.main()
