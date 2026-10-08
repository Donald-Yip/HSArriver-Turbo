# -*- coding: utf-8 -*-
"""发现/选项之后还要再点一个目标（鲍勃「招募随从」这类牌）。

线上实测的卡死：`BG31_BOB（调酒师鲍勃）` 第 2 项 `BG31_BOBt2 招募随从` =
「将一个敌方随从的一张复制置入你的手牌」——选完这一项，游戏还会让你点一个
对方随从。改动前 `DiscoverChoiceAction` 没有 target 字段、`_execute` 点完选项
就返回"已执行"，于是游戏停在选目标态、盒子推荐不变，一直重试到烧绳。

这里锁住三件事：
  1. 盒子给目标 → 点选项之后确实去点那个目标（顺序不能反）；
  2. 盒子没给目标 → 行为与改动前逐字节一致（只点选项、不等待）；
  3. 目标/选项在执行前失效 → 一次点击都不发。
"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace

from log_op import parse_line
from log_state import LogState, update_state
from manual_controller import (
    ClickExecutor, DiscoverChoiceAction, ManualController,
)
from src.game_state.recommendation_adapter import (
    RecommendationStateError, adapt_action,
)
from src.parser.recommendation_parser import RecommendationParser


class RecordingClicks:
    """只记录点击、不真碰鼠标（与 test_targeted_battlecries 同一套写法）。"""

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

    def click_middle(self):
        self.events.append(("middle",))

    def cancel_click(self):
        self.events.append(("cancel",))


class DiscoverWithTargetTests(unittest.TestCase):
    def choice(self, count=4, player="1", cards=("BG31_BOBt", "BG31_BOBt2",
                                                "BG31_BOBt3", "BG31_BOBt4")):
        state = LogState()
        state.my_player_id = "1"
        self.feed(state,
                  "GameState.DebugPrintEntityChoices() - id=8 Player=1 ChoiceType=GENERAL")
        for index, card_id in enumerate(cards[:count]):
            self.feed(state,
                "GameState.DebugPrintEntityChoices() - "
                f"Entities[{index}]=[entityName=选项 id={100 + index} "
                f"zone=SETASIDE zonePos=0 cardId={card_id} player={player}]")
        self.feed(state, "ChoiceCardMgr.WaitThenShowChoices() - id=8 BEGIN")
        return state

    @staticmethod
    def feed(state, text):
        parsed = parse_line("D 21:29:00.0000000 " + text)
        if parsed is None:
            raise AssertionError("log line not parsed: " + text)
        update_state(state, parsed)

    @classmethod
    def power_option(cls, state, card_id, name, choices):
        """喂一段 DebugPrintOptions：母卡 + 选项子卡（真实日志形态）。

        行尾的 `0 ` 是 `errorParam=` 的值：解析器会 rstrip，真实日志这行后面
        还有内容，所以必须留非空尾巴。
        """
        cls.feed(state, "GameState.DebugPrintOptions() - id=23")
        cls.feed(state,
                 f"GameState.DebugPrintOptions() -   option 5 type=POWER "
                 f"mainEntity=[entityName={name} id=152 zone=PLAY zonePos=0 "
                 f"cardId={card_id} player=1] error=NONE errorParam=0 ")
        for index, (option_name, option_card) in enumerate(choices):
            cls.feed(state,
                     f"GameState.DebugPrintOptions() -     subOption {index} "
                     f"entity=[entityName={option_name} id={153 + index} "
                     f"zone=SETASIDE zonePos=0 cardId={option_card} player=1] "
                     f"error=NONE errorParam=0 ")

    @staticmethod
    def minion(entity_id, zone_pos, card_id="CS2_182"):
        return SimpleNamespace(entity_id=entity_id, zone_pos=zone_pos,
                               card_id=card_id, cardtype="MINION", name="随从")

    def state(self, log, oppo=2):
        return SimpleNamespace(
            game_num_turns_in_play=3, is_my_turn=True, my_player_id="1",
            discover_choice_count=log.discover_choice_count,
            general_choice_cards=dict(log.general_choice_cards),
            general_choice_entity_ids=dict(log.general_choice_entity_ids),
            power_options=dict(log.power_options),
            my_hand_cards=[SimpleNamespace(entity_id="152", card_id="EX1_154",
                                           cardtype="SPELL", name="愤怒")],
            my_minions=[self.minion("300", 1)],
            my_locations=[], oppo_locations=[],
            oppo_minions=[self.minion(str(200 + i), i + 1)
                          for i in range(oppo)],
            my_hero=SimpleNamespace(entity_id="hero"),
            oppo_hero=SimpleNamespace(entity_id="enemy-hero"))

    def proposed(self, index=2, target="目标是对方1号位"):
        lines = [f"选择我方{index}号位卡牌"]
        if target:
            lines.append(target)
        return RecommendationParser().parse(SimpleNamespace(
            frame_id="frame", normalized_text="打法参考A\n" + "\n".join(lines),
            confidence=.99), turn_number=3, log_revision=7)

    def execute(self, action, state, expect_ok=True):
        clicks = RecordingClicks()
        sleeps = []
        controller = ManualController(output_func=lambda _: None,
            executor=ClickExecutor(click_module=clicks, sleep_func=sleeps.append,
                                   action_context=nullcontext))
        result = controller.execute(action, state)
        if expect_ok:
            self.assertTrue(result.executed, result.message)
        else:
            self.assertFalse(result.executed, result.message)
        return clicks.events, sleeps, result

    # ---------------------------------------------------------------- 主用例
    def test_bob_recruit_copies_enemy_minion(self):
        state = self.state(self.choice())
        action = adapt_action(self.proposed(index=2), state).manual_action
        self.assertEqual(1, action.choice_index)          # 0 基：第 2 项
        self.assertEqual(4, action.choice_count)
        self.assertEqual("enemy", action.target.side)
        self.assertEqual("minion", action.target.kind)
        self.assertEqual(0, action.target.index)          # 对方 1 号位
        self.assertEqual("200", action.target.entity_id)
        events, sleeps, _ = self.execute(action, state)
        # 先点选项，等一会儿，再点目标，最后右键取消（顺序不能反）。
        self.assertEqual([("choice", 1, 4), ("oppo", 0, 2), ("cancel",)], events)
        self.assertEqual([0.3], sleeps)

    def test_damaged_minion_buff_from_real_panel_shape(self):
        """凶险梦魇（EDR_570）：真实日志形态「选择卡牌 / 动荡能量」。"""
        log = LogState()
        log.my_player_id = "1"
        self.power_option(log, "EDR_570", "凶险梦魇",
                          (("噩梦爆发", "EDR_570A"), ("动荡能量", "EDR_570B")))
        state = self.state(log)
        state.my_hand_cards = [SimpleNamespace(entity_id="152",
                                               card_id="EDR_570",
                                               cardtype="SPELL", name="凶险梦魇")]
        state.my_minions = [self.minion("301", 1), self.minion("302", 2)]
        proposed = RecommendationParser().parse(SimpleNamespace(
            frame_id="frame", confidence=.99, normalized_text=(
                "打法参考A\n打出1号位法术\n目标是己方2号位\n"
                "选择卡牌\n动荡能量")), turn_number=3, log_revision=7)
        action = adapt_action(proposed, state).manual_action
        self.assertEqual(1, action.choose_one.choice_index)
        self.assertEqual(1, action.target.index)
        events, _, _ = self.execute(action, state)
        self.assertEqual(
            [("cancel",), ("hand", 0, 1), ("middle",),
             ("choice", 1, 2), ("mine", 1, 2), ("cancel",)], events)

    def test_anger_damage_option_targets_enemy_minion(self):
        """愤怒（EX1_154）：盒子点名要目标的那个选项。"""
        log = LogState()
        log.my_player_id = "1"
        self.power_option(log, "EX1_154", "愤怒",
                          (("阳炎之怒", "EX1_154a"), ("自然之怒", "EX1_154b")))
        state = self.state(log, oppo=1)
        state.my_hand_cards = [SimpleNamespace(entity_id="152",
                                               card_id="EX1_154",
                                               cardtype="SPELL", name="愤怒")]
        state.my_minions = []
        proposed = RecommendationParser().parse(SimpleNamespace(
            frame_id="frame", confidence=.99, normalized_text=(
                "打法参考A\n打出1号位法术\n目标是对方1号位\n"
                "选择卡牌\n阳炎之怒")), turn_number=3, log_revision=7)
        action = adapt_action(proposed, state).manual_action
        self.assertEqual(0, action.choose_one.choice_index)
        events, _, _ = self.execute(action, state)
        self.assertEqual(
            [("cancel",), ("hand", 0, 1), ("middle",),
             ("choice", 0, 2), ("oppo", 0, 1), ("cancel",)], events)

    # ---------------------------------------------------------------- 边界
    def test_discover_without_target_keeps_old_behaviour(self):
        log = self.choice(3)
        state = self.state(log)
        action = adapt_action(self.proposed(index=2, target=None),
                              state).manual_action
        self.assertIsNone(action.target)
        events, sleeps, _ = self.execute(action, state)
        self.assertEqual([("choice", 1, 3)], events)   # 与改动前一致
        self.assertEqual([], sleeps)

    def test_discover_target_out_of_range_is_rejected(self):
        state = self.state(self.choice(), oppo=1)
        with self.assertRaisesRegex(RecommendationStateError,
                                    "discover_target_out_of_range"):
            adapt_action(self.proposed(index=2, target="目标是对方5号位"), state)

    def test_discover_target_entity_changed_blocks_click(self):
        state = self.state(self.choice())
        action = adapt_action(self.proposed(index=2), state).manual_action
        state.oppo_minions[0].entity_id = "999"          # 目标已经换人了
        events, _, result = self.execute(action, state, expect_ok=False)
        self.assertEqual([], events)                     # 连选项都不点
        self.assertIn("目标", result.message)

    def test_discover_choice_entity_changed_blocks_click(self):
        state = self.state(self.choice())
        action = adapt_action(self.proposed(index=2), state).manual_action
        self.assertEqual("101", action.choice_entity_id)
        state.general_choice_entity_ids = {0: "100", 1: "777"}   # 2 号位换人了
        events, _, result = self.execute(action, state, expect_ok=False)
        self.assertEqual([], events)
        self.assertIn("选项", result.message)

    def test_general_choice_binds_entity_id_from_log(self):
        state = self.state(self.choice())
        action = adapt_action(self.proposed(index=2), state).manual_action
        self.assertEqual("101", action.choice_entity_id)
        events, _, _ = self.execute(action, state)
        self.assertEqual([("choice", 1, 4), ("oppo", 0, 2), ("cancel",)], events)

    def test_target_is_not_clicked_when_the_panel_has_none(self):
        """盒子只写「选择我方2号位卡牌」时，即便游戏里那个选项还要目标，
        也不许脚本自己去点——点了会点偏，行为必须与改动前一致。"""
        state = self.state(self.choice())
        action = adapt_action(self.proposed(index=2, target=None),
                              state).manual_action
        events, _, _ = self.execute(action, state)
        self.assertEqual([("choice", 1, 4)], events)

    def test_timeline_choice_still_works(self):
        log = LogState()
        log.my_player_id = "1"
        self.feed(log,
                  "GameState.DebugPrintEntityChoices() - id=8 Player=1 ChoiceType=GENERAL")
        for index, card_id in enumerate(("TIME_000ta", "TIME_000tb")):
            self.feed(log,
                "GameState.DebugPrintEntityChoices() - "
                f"Entities[{index}]=[entityName=选项 id={100 + index} "
                f"zone=SETASIDE zonePos=0 cardId={card_id} player=1]")
        self.feed(log, "ChoiceCardMgr.WaitThenShowChoices() - id=8 BEGIN")
        state = self.state(log)
        proposed = RecommendationParser().parse(SimpleNamespace(
            frame_id="frame", confidence=.99,
            normalized_text="打法参考A\n选择我方2号位卡牌"), turn_number=3,
            log_revision=7)
        action = adapt_action(proposed, state).manual_action
        self.assertEqual("TIME_000tb", action.timeline_card_id)
        self.assertIsNone(action.target)


class DiscoverTargetModelTests(unittest.TestCase):
    def test_action_defaults_keep_old_construction_working(self):
        action = DiscoverChoiceAction(1, 3)
        self.assertIsNone(action.target)
        self.assertIsNone(action.choice_entity_id)
        self.assertIsNone(action.choice_parent_entity_id)
        self.assertIsNone(action.timeline_card_id)


if __name__ == "__main__":
    unittest.main()
