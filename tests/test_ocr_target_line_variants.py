# -*- coding: utf-8 -*-
"""目标行 OCR 变体：盒子写「己方」，OCR 常认成「已方/巳方」。

真实反馈（2026-10-24 `打出3号位法术-错误反馈.zip`）：
  面板是「打出2号位法术 / 漂浮教程 / 目标是己方2号位 / 暮光时空跃迁者」，
  OCR 读成「目标是已方2号位」→ parser 只认「己方|我方」→ 目标行匹配不上，
  整条法术推荐被判 `unsupported_spell_target`，连续重试 70+ 次。
这里锁住：只放宽阵营词/修饰词，数字与号位仍严格；真认不出的目标行照旧拒绝。
"""

import unittest
from types import SimpleNamespace

from src.game_state.recommendation_adapter import adapt_action
from src.parser.recommendation_parser import (
    RecommendationParseError, RecommendationParser,
)
from src.recommendation_models import ActionKind


def parse(lines, turn=10, revision=7):
    text = "\n".join(lines)
    return RecommendationParser().parse(
        SimpleNamespace(frame_id="frame", normalized_text=text,
                        confidence=0.99),
        turn_number=turn, log_revision=revision)


def minion(entity_id, zone_pos, card_id="CS2_182", name="随从"):
    return SimpleNamespace(entity_id=entity_id, zone_pos=zone_pos,
                           card_id=card_id, cardtype="MINION", name=name)


def state():
    return SimpleNamespace(
        game_num_turns_in_play=10,
        is_my_turn=True,
        my_player_id="1",
        discover_choice_count=None,
        discover_choice_pending=False,
        general_choice_cards={},
        general_choice_entity_ids={},
        power_options={},
        my_hand_cards=[
            SimpleNamespace(entity_id="100", card_id="CARD_A",
                            cardtype="MINION", name="手牌A"),
            SimpleNamespace(entity_id="101", card_id="SPELL_1",
                            cardtype="SPELL", name="漂浮教程"),
        ],
        my_minions=[minion("300", 1, name="一号"), minion("301", 2, name="二号")],
        my_locations=[],
        oppo_locations=[],
        oppo_minions=[minion("200", 1, name="敌方一号")],
        my_hero=SimpleNamespace(entity_id="hero"),
        oppo_hero=SimpleNamespace(entity_id="enemy-hero"),
    )


class OcrTargetVariantParserTests(unittest.TestCase):
    PANEL = [
        "对手记牌器",
        "推荐打法Beta",
        "打法参考A",
        "打出2号位法术",
        "漂浮教程",
        "目标是已方2号位",
        "暮光时空跃迁者",
    ]

    def test_real_feedback_panel_is_parsed(self):
        proposed = parse(self.PANEL)

        self.assertEqual(ActionKind.PLAY_CARD, proposed.action)
        self.assertEqual("SPELL", proposed.card_type)
        self.assertEqual("friendly", proposed.target.owner)
        self.assertEqual("board_slot", proposed.target.kind)
        self.assertEqual(2, proposed.target.index)

    def test_reference_b_section_does_not_confuse_the_target(self):
        # 第 4 个反馈截图同屏还有「打法参考B / 结束回合」，不能把 B 段算进来。
        proposed = parse([
            "打法参考A", "打出3号位法术", "漂浮教程", "目标是己方2号位",
            "虚空领主", "打法参考B", "结束回合",
        ])
        self.assertEqual(ActionKind.PLAY_CARD, proposed.action)
        self.assertEqual(3, proposed.source.index)
        self.assertEqual("friendly", proposed.target.owner)
        self.assertEqual(2, proposed.target.index)

    def test_common_side_variants(self):
        for side in ("己方", "已方", "巳方", "我方"):
            with self.subTest(side=side):
                proposed = parse([
                    "打法参考A", "打出2号位法术", f"目标是{side}2号位",
                ])
                self.assertEqual("friendly", proposed.target.owner)
                self.assertEqual(2, proposed.target.index)

    def test_enemy_side_variant_and_modifiers(self):
        proposed = parse([
            "打法参考A", "打出2号位法术", "目标是故方1号位卡牌",
        ])
        self.assertEqual("enemy", proposed.target.owner)
        self.assertEqual(1, proposed.target.index)

        proposed = parse([
            "打法参考A", "打出2号位法术", "目标是已方2号位的随从",
        ])
        self.assertEqual("friendly", proposed.target.owner)
        self.assertEqual(2, proposed.target.index)

    def test_hero_variants(self):
        for line, owner in (("目标是已方英雄", "friendly"),
                            ("目标是巳方英雄", "friendly"),
                            ("目标是故方英雄", "enemy")):
            with self.subTest(line=line):
                self.assertEqual(owner, parse([
                    "打法参考A", "打出2号位法术", line]).target.owner)

    def test_unknown_target_shape_is_still_rejected(self):
        # 没有号位的目标行仍然认不出，保持原来的拒绝行为，不瞎猜随从。
        with self.assertRaisesRegex(RecommendationParseError,
                                    "unsupported_spell_target"):
            parse(["打法参考A", "打出2号位法术", "目标是我方随从"])

        with self.assertRaisesRegex(RecommendationParseError,
                                    "unsupported_spell_target"):
            parse(["打法参考A", "打出2号位法术", "目标是已方随从"])


class OcrTargetVariantAdapterTests(unittest.TestCase):
    def test_friendly_target_is_bound_to_the_minion(self):
        proposed = parse(OcrTargetVariantParserTests.PANEL)
        adapted = adapt_action(proposed, state())
        action = adapted.manual_action
        self.assertEqual(1, action.hand_index)
        self.assertEqual("friendly", action.target.side)
        self.assertEqual("minion", action.target.kind)
        self.assertEqual(1, action.target.index)      # 场面 2 号位 → 集合下标 1
        self.assertEqual("301", action.target.entity_id)


if __name__ == "__main__":
    unittest.main()
