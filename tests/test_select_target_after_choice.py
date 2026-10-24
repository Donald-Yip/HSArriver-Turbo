# -*- coding: utf-8 -*-
"""四选一/发现结算后的目标选择（真实面板：`选择对方2号位卡牌`）。

背景（用户 2026-10-24 压缩包实测）：
  克苏恩「凝视」、调酒师鲍勃「招募随从」这类“选项本身还要选一个随从”的
  四选一，选完选项后盒子会打印 `选择对方N号位卡牌`（下一行是目标卡名）。
  改动前 parser 只认 `选择我方N号位卡牌`（发现选项）和 `目标是对方N号位`，
  这一行会被 normalize 整个丢掉 → StableRecommendationReader 判
  `recommendation_not_stable`，脚本一路重试到烧绳。

这里锁住：
  1. 真实面板文本能被解析成 SELECT_TARGET，并点到对方对应号位的随从；
  2. “选项+目标同屏”时仍按 选项 → 目标 的顺序点；
  3. 目标实体变化/越界/英雄/我方（随从或手牌）都能正确处理；
  4. 判不出我方目标时**零点击**（绝不随机点一个随从）。
"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace

from manual_controller import (
    ClickExecutor, DiscoverChoiceAction, ManualController, SelectTargetAction,
)
from src.game_state.recommendation_adapter import (
    RecommendationStateError, adapt_action,
)
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_models import ActionKind

PROMPT = "打法参考A\n{0}"


def parse(text, turn=11, revision=7):
    return RecommendationParser().parse(
        SimpleNamespace(frame_id="frame", normalized_text=PROMPT.format(text),
                        confidence=0.99),
        turn_number=turn, log_revision=revision)


def minion(entity_id, zone_pos, card_id="CS2_182", name="普通随从"):
    return SimpleNamespace(entity_id=entity_id, zone_pos=zone_pos,
                           card_id=card_id, cardtype="MINION", name=name)


def hand_card(entity_id, name, cardtype="MINION", zone_pos=1):
    return SimpleNamespace(entity_id=entity_id, name=name,
                           cardtype=cardtype, zone_pos=zone_pos)


class RecordingClicks:
    def __init__(self):
        self.events = []

    def choose_card(self, index, count):
        self.events.append(("hand", index, count))

    def put_minion(self, index, count):
        self.events.append(("place", index, count))

    def choose_my_minion(self, index, count):
        self.events.append(("mine", index, count))

    def choose_opponent_minion(self, index, count):
        self.events.append(("oppo", index, count))

    def choose_my_hero(self):
        self.events.append(("mine_hero",))

    def choose_oppo_hero(self):
        self.events.append(("oppo_hero",))

    def choose_discover_card(self, index, count):
        self.events.append(("choice", index, count))

    def cancel_click(self):
        self.events.append(("cancel",))


def state(**overrides):
    data = dict(
        game_num_turns_in_play=11,
        is_my_turn=True,
        my_player_id="1",
        discover_choice_count=None,
        discover_choice_pending=False,
        general_choice_cards={},
        general_choice_entity_ids={},
        power_options={},
        my_hand_cards=[hand_card("100", "手牌A"), hand_card("101", "手牌B")],
        my_minions=[minion("300", 1, name="己方一号"), minion("301", 2, name="己方二号")],
        my_locations=[],
        oppo_locations=[],
        oppo_minions=[
            minion("200", 1, name="敌方一号"),
            minion("201", 2, name="腐蚀者扎卡兹"),
            minion("202", 3, name="敌方三号"),
            minion("203", 4, name="敌方四号"),
        ],
        my_hero=SimpleNamespace(entity_id="hero", name="我方英雄"),
        oppo_hero=SimpleNamespace(entity_id="enemy-hero", name="敌方英雄"),
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def execute(action, current_state):
    clicks = RecordingClicks()
    sleeps = []
    controller = ManualController(
        output_func=lambda _msg: None,
        executor=ClickExecutor(click_module=clicks,
                               sleep_func=sleeps.append,
                               action_context=nullcontext))
    result = controller.execute(action, current_state)
    return clicks.events, sleeps, result


class ParserTests(unittest.TestCase):
    def test_real_panel_text_is_a_select_target_action(self):
        text = "选择对方2号位卡牌\n腐蚀者扎卡兹"
        self.assertEqual("选择对方2号位卡牌",
                         RecommendationParser().normalize_action_text(
                             PROMPT.format(text)))

        proposed = parse(text)

        self.assertEqual(ActionKind.SELECT_TARGET, proposed.action)
        self.assertIsNone(proposed.source)
        self.assertEqual("enemy", proposed.target.owner)
        self.assertEqual("board_slot", proposed.target.kind)
        self.assertEqual(2, proposed.target.index)
        self.assertEqual("腐蚀者扎卡兹", proposed.target_name)

    def test_hero_and_friendly_minion_forms(self):
        hero = parse("选择对方英雄")
        self.assertEqual(ActionKind.SELECT_TARGET, hero.action)
        self.assertEqual("hero", hero.target.kind)

        friendly = parse("选择我方2号位随从\n己方二号")
        self.assertEqual(ActionKind.SELECT_TARGET, friendly.action)
        self.assertEqual("friendly", friendly.target.owner)
        self.assertEqual(2, friendly.target.index)
        self.assertEqual("己方二号", friendly.target_name)

    def test_choice_line_and_target_line_together_keep_one_action(self):
        proposed = parse("选择我方3号位卡牌\n选择对方2号位卡牌")
        self.assertEqual(ActionKind.CHOOSE_DISCOVER, proposed.action)
        self.assertEqual(3, proposed.source.index)
        self.assertEqual("enemy", proposed.target.owner)
        self.assertEqual(2, proposed.target.index)

    def test_play_card_line_accepts_the_select_target_phrasing(self):
        proposed = parse("打出2号位法术\n选择对方1号位卡牌")
        self.assertEqual(ActionKind.PLAY_CARD, proposed.action)
        self.assertEqual("enemy", proposed.target.owner)
        self.assertEqual("board_slot", proposed.target.kind)
        self.assertEqual(1, proposed.target.index)

    def test_multiple_target_lines_are_ambiguous(self):
        with self.assertRaisesRegex(Exception, "ambiguous_target"):
            parse("选择对方1号位卡牌\n选择对方2号位卡牌")


class AdapterAndClickTests(unittest.TestCase):
    def test_enemy_minion_target_is_clicked_once(self):
        proposed = parse("选择对方2号位卡牌\n腐蚀者扎卡兹")
        adapted = adapt_action(proposed, state())

        self.assertIsInstance(adapted.manual_action, SelectTargetAction)
        target = adapted.manual_action.target
        self.assertEqual(("enemy", "minion", 1), (target.side, target.kind,
                                                  target.index))
        self.assertEqual("201", target.entity_id)

        events, sleeps, result = execute(adapted.manual_action, state())
        self.assertTrue(result.executed, result.message)
        self.assertEqual([("oppo", 1, 4)], events)       # 点一下对方2号位，不右键
        self.assertEqual([], sleeps)

    def test_target_entity_changed_blocks_the_click(self):
        proposed = parse("选择对方2号位卡牌\n腐蚀者扎卡兹")
        adapted = adapt_action(proposed, state())
        current = state()
        current.oppo_minions[1].entity_id = "999"

        events, _, result = execute(adapted.manual_action, current)

        self.assertFalse(result.executed)
        self.assertEqual([], events)

    def test_target_out_of_range_is_rejected(self):
        with self.assertRaisesRegex(RecommendationStateError,
                                    "select_target_out_of_range"):
            adapt_action(parse("选择对方5号位卡牌"),
                         state(oppo_minions=[minion("200", 1)]))

    def test_enemy_hero_target_is_clicked(self):
        adapted = adapt_action(parse("选择对方英雄"), state())
        events, _, result = execute(adapted.manual_action, state())
        self.assertTrue(result.executed, result.message)
        self.assertEqual([("oppo_hero",)], events)

    def test_friendly_minion_target_is_clicked(self):
        adapted = adapt_action(parse("选择我方2号位随从\n己方二号"), state())
        target = adapted.manual_action.target
        self.assertEqual(("friendly", "minion", 1),
                         (target.side, target.kind, target.index))
        events, _, result = execute(adapted.manual_action, state())
        self.assertTrue(result.executed, result.message)
        self.assertEqual([("mine", 1, 2)], events)

    def test_choice_open_blocks_friendly_target_click(self):
        current = state(discover_choice_count=3)
        with self.assertRaisesRegex(RecommendationStateError,
                                    "select_target_while_choice_open"):
            adapt_action(parse("选择我方2号位随从\n己方二号"), current)

    def test_choice_and_target_screen_clicks_option_then_target(self):
        proposed = parse("选择我方3号位卡牌\n选择对方2号位卡牌")
        adapted = adapt_action(proposed, state(discover_choice_count=4))

        self.assertIsInstance(adapted.manual_action, DiscoverChoiceAction)
        self.assertEqual(2, adapted.manual_action.choice_index)
        self.assertEqual(4, adapted.manual_action.choice_count)
        self.assertEqual(1, adapted.manual_action.target.index)

        events, sleeps, result = execute(adapted.manual_action,
                                         state(discover_choice_count=4))
        self.assertTrue(result.executed, result.message)
        self.assertEqual([("choice", 2, 4), ("oppo", 1, 4), ("cancel",)],
                         events)
        self.assertEqual([0.3], sleeps)


class FriendlyChoiceTargetFallbackTests(unittest.TestCase):
    """"选择我方N号位卡牌"在没有选项面板时其实是选自己的目标。"""

    def test_matches_friendly_minion_by_name(self):
        proposed = parse("选择我方2号位卡牌\n己方二号")
        adapted = adapt_action(proposed, state())
        target = adapted.manual_action.target
        self.assertEqual(("friendly", "minion", 1),
                         (target.side, target.kind, target.index))
        events, _, result = execute(adapted.manual_action, state())
        self.assertTrue(result.executed, result.message)
        self.assertEqual([("mine", 1, 2)], events)

    def test_matches_hand_card_by_name(self):
        current = state(my_hand_cards=[hand_card("100", "手牌A"),
                                       hand_card("101", "手牌B")])
        proposed = parse("选择我方2号位卡牌\n手牌B")
        adapted = adapt_action(proposed, current)
        target = adapted.manual_action.target
        self.assertEqual(("friendly", "hand", 1),
                         (target.side, target.kind, target.index))
        events, _, result = execute(adapted.manual_action, current)
        self.assertTrue(result.executed, result.message)
        self.assertEqual([("hand", 1, 2)], events)

    def test_ambiguous_name_is_rejected_without_clicking(self):
        current = state(my_hand_cards=[hand_card("100", "手牌A"),
                                       hand_card("101", "己方二号")])
        with self.assertRaisesRegex(RecommendationStateError,
                                    "choice_target_ambiguous"):
            adapt_action(parse("选择我方2号位卡牌\n己方二号"), current)

    def test_missing_name_is_rejected(self):
        with self.assertRaisesRegex(RecommendationStateError,
                                    "choice_target_unknown"):
            adapt_action(parse("选择我方2号位卡牌"),
                         state(discover_choice_pending=False))

    def test_pending_choice_panel_waits_instead_of_clicking(self):
        with self.assertRaisesRegex(RecommendationStateError,
                                    "choice_panel_pending"):
            adapt_action(parse("选择我方2号位卡牌\n己方二号"),
                         state(discover_choice_pending=True))


if __name__ == "__main__":
    unittest.main()
