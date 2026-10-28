# -*- coding: utf-8 -*-
"""Attack target recovery when HSAng drops the target-slot line.

Field screenshot 2026-10-29 showed a valid recommendation:

    操作5号位随从攻击 / 团队领袖 / 目标是对方英雄 / 萨格拉斯

OCR omitted the ``目标是对方英雄`` line, leaving only the source and target
card names. The parser should retain the trailing target name and the adapter
should bind it only when it uniquely matches an enemy entity.
"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace

from manual_controller import AttackAction, ClickExecutor, ManualController
from src.game_state.recommendation_adapter import (
    RecommendationStateError,
    adapt_action,
)
from src.parser.recommendation_parser import RecommendationParser


def parse(text):
    return RecommendationParser().parse(
        SimpleNamespace(frame_id="frame", normalized_text=text,
                        confidence=0.93),
        turn_number=21,
        log_revision=9,
    )


def state(*, hero_name="萨格拉斯", minions=()):
    return SimpleNamespace(
        game_num_turns_in_play=21,
        is_my_turn=True,
        my_hand_cards=[],
        my_minions=[
            SimpleNamespace(entity_id=str(100 + index), zone_pos=index + 1,
                            card_id=f"SELF_{index}", cardtype="MINION",
                            name=name, damage=0, exhausted=0)
            for index, name in enumerate(("一", "二", "三", "四", "团队领袖"))
        ],
        my_locations=[],
        oppo_locations=[],
        oppo_minions=list(minions),
        my_hero=SimpleNamespace(entity_id="hero", name="我", health=30),
        oppo_hero=SimpleNamespace(entity_id="enemy-hero", name=hero_name,
                                  health=30),
    )


class AttackTargetNameFallbackTests(unittest.TestCase):
    def test_parser_keeps_trailing_target_name_when_target_line_is_missing(self):
        proposed = parse(
            "打法参考A\n操作5号位随从攻击\n团队领袖\n萨格拉斯")
        self.assertEqual("萨格拉斯", proposed.target_name)
        self.assertIsNone(proposed.target)

    def test_adapter_binds_a_unique_enemy_hero_name(self):
        adapted = adapt_action(parse(
            "打法参考A\n操作5号位随从攻击\n团队领袖\n萨格拉斯"), state())
        self.assertIsInstance(adapted.manual_action, AttackAction)
        self.assertEqual(("enemy", "hero"),
                         (adapted.manual_action.target.side,
                          adapted.manual_action.target.kind))
        self.assertEqual("enemy-hero", adapted.target_entity_id)

    def test_adapter_binds_a_unique_enemy_minion_name(self):
        enemy = SimpleNamespace(
            entity_id="enemy-minion", zone_pos=2, card_id="ENEMY",
            cardtype="MINION", name="萨格拉斯", damage=0, health=5,
        )
        adapted = adapt_action(parse(
            "打法参考A\n操作5号位随从攻击\n团队领袖\n萨格拉斯"),
            state(hero_name="另一个英雄", minions=[enemy]))
        self.assertEqual(("enemy", "minion"),
                         (adapted.manual_action.target.side,
                          adapted.manual_action.target.kind))
        self.assertEqual("enemy-minion", adapted.target_entity_id)

    def test_duplicate_names_are_not_guessed(self):
        enemy = SimpleNamespace(
            entity_id="enemy-minion", zone_pos=1, card_id="ENEMY",
            cardtype="MINION", name="萨格拉斯", damage=0, health=5,
        )
        with self.assertRaisesRegex(RecommendationStateError,
                                    "attack_target_ambiguous"):
            adapt_action(parse(
                "打法参考A\n操作5号位随从攻击\n团队领袖\n萨格拉斯"),
                state(minions=[enemy]))

    def test_missing_target_name_still_requires_a_target(self):
        proposed = parse(
            "打法参考A\n操作5号位随从攻击\n团队领袖")
        with self.assertRaisesRegex(RecommendationStateError,
                                    "attack_target_required"):
            adapt_action(proposed, state())


if __name__ == "__main__":
    unittest.main()
