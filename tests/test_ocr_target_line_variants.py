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

    # 2026-10-27 反馈（旧包）：法术打自己 1 号位；OCR 读成「目标是已方1号位」。
    FEEDBACK_20261027_PANEL = [
        "网易炉石传说盒子", "对手记牌器", "推荐打法", "Beta",
        "打法参考A", "打出1号位法术", "漂浮教程", "目标是已方1号位",
        "暮光龙卵",
    ]

    def test_real_feedback_panels_are_parsed(self):
        cases = (
            ("2026-10-24-slot-2", self.PANEL, 2, 2),
            ("2026-10-27-slot-1", self.FEEDBACK_20261027_PANEL, 1, 1),
        )
        for name, lines, source_index, target_index in cases:
            with self.subTest(name):
                proposed = parse(lines)
                self.assertEqual(ActionKind.PLAY_CARD, proposed.action)
                self.assertEqual("SPELL", proposed.card_type)
                self.assertEqual(source_index, proposed.source.index)
                self.assertEqual("friendly", proposed.target.owner)
                self.assertEqual("board_slot", proposed.target.kind)
                self.assertEqual(target_index, proposed.target.index)

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

    def test_target_line_variant_matrix(self):
        cases = (
            ("friendly-己方", "己方", "friendly", 2),
            ("friendly-已方", "已方", "friendly", 2),
            ("friendly-巳方", "巳方", "friendly", 2),
            ("friendly-我方", "我方", "friendly", 2),
            ("enemy-故方", "故方", "enemy", 1),
        )
        for name, side, owner, index in cases:
            with self.subTest(name):
                proposed = parse([
                    "打法参考A", "打出2号位法术", f"目标是{side}{index}号位",
                ])
                self.assertEqual(owner, proposed.target.owner)
                self.assertEqual("board_slot", proposed.target.kind)
                self.assertEqual(index, proposed.target.index)

        # 修饰词（「…号位的随从」）不影响号位解析。
        proposed = parse([
            "打法参考A", "打出2号位法术", "目标是已方2号位的随从",
        ])
        self.assertEqual("friendly", proposed.target.owner)
        self.assertEqual(2, proposed.target.index)

    def test_hero_variant_matrix(self):
        cases = (
            ("friendly-已方", "目标是已方英雄", "friendly"),
            ("friendly-巳方", "目标是巳方英雄", "friendly"),
            ("enemy-故方", "目标是故方英雄", "enemy"),
        )
        for name, line, owner in cases:
            with self.subTest(name):
                target = parse(["打法参考A", "打出2号位法术", line]).target
                self.assertEqual(owner, target.owner)
                self.assertEqual("hero", target.kind)
                self.assertIsNone(target.index)

    def test_unknown_target_shape_is_still_rejected(self):
        # 没有号位的目标行仍然认不出，保持原来的拒绝行为，不瞎猜随从。
        for line in ("目标是我方随从", "目标是已方随从"):
            with self.subTest(line=line):
                with self.assertRaisesRegex(
                        RecommendationParseError, "unsupported_spell_target"):
                    parse(["打法参考A", "打出2号位法术", line])


class OcrTargetVariantAdapterTests(unittest.TestCase):
    # 2026-10-27 反馈：手牌 1（0 基）是「漂浮教程」，场面 1 号位是「暮光龙卵」。
    SPELL_CARD_ID = "BE_094"
    TARGET_CARD_ID = "CATA_210"

    @classmethod
    def _feedback_state(cls):
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
                SimpleNamespace(entity_id="100", card_id=cls.SPELL_CARD_ID,
                                cardtype="SPELL", name="漂浮教程"),
            ],
            my_minions=[
                minion("300", 1, cls.TARGET_CARD_ID, name="暮光龙卵"),
                minion("301", 2, name="二号"),
            ],
            my_locations=[],
            oppo_locations=[],
            oppo_minions=[minion("200", 1, name="敌方一号")],
            my_hero=SimpleNamespace(entity_id="hero"),
            oppo_hero=SimpleNamespace(entity_id="enemy-hero"),
        )

    def test_friendly_target_is_bound_to_hand_and_minion(self):
        cases = (
            ("2026-10-24", OcrTargetVariantParserTests.PANEL, state,
             "SPELL_1", 1, 1, "301"),
            ("2026-10-27", OcrTargetVariantParserTests.FEEDBACK_20261027_PANEL,
             self._feedback_state, self.SPELL_CARD_ID, 0, 0, "300"),
        )
        for name, panel, state_factory, card_id, hand_index, target_index, entity_id in cases:
            with self.subTest(name):
                action = adapt_action(parse(panel), state_factory()).manual_action
                self.assertEqual(hand_index, action.hand_index)
                self.assertEqual(card_id, action.card_id)
                self.assertEqual("friendly", action.target.side)
                self.assertEqual("minion", action.target.kind)
                self.assertEqual(target_index, action.target.index)
                self.assertEqual(entity_id, action.target.entity_id)


if __name__ == "__main__":
    unittest.main()
