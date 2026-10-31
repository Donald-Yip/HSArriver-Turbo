# -*- coding: utf-8 -*-
"""古神「选择一个技能」：盒子只给技能名，号位靠实测记录表反查。

背景（用户 2026-10-27 截图，工程 PaddleOCR 复跑面板原文）：
  克苏恩，帝国之眼（BE_013，卡面第一行 `<b>古神</b>`）不能普通攻击：点它弹
  「选择一个技能」面板（四张技能牌等距横排）。盒子把这一步写得**和攻击一样**，
  只多出「选择卡牌 + 技能名」：

      操作2号位随从攻击 / 克苏恩，帝国之眼 / 选择卡牌 / 克苏恩的愤怒
      操作2号位随从攻击 / 克苏恩，帝国之眼 / 目标是对方2号位 / 导航员伊莉斯 /
      选择卡牌 / 克苏恩的凝视

  改动前 parser 把它当 ATTACK：没目标行 → `attack_target_required` 重试到烧绳；
  有目标行更糟 → 按普通攻击点两下，第二下正好落在技能面板第 3 张牌上。

这里锁住四件事：
  1. 面板号位（1 愤怒 / 2 领域 / 3 凝视 / 4 仆从）与点击坐标是**记录值**，不许
     被顺手改掉；
  2. 面板原文能解析成 USE_ABILITY（技能名 + 可选目标），普通攻击不受影响；
  3. 认不出卡/技能名时**零点击**拒绝（绝不按"大概是第几个"去点）；
  4. 手势顺序：右键收面板 → 点随从 → 点技能牌 →（有目标时）点目标，末尾不右键。
"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import click as hearthstone_click
from manual_controller import ClickExecutor, ManualController, UseAbilityAction
from src.flow.recommendation_flow import RecommendationFlow, ResultStatus
from src.game_state.choose_skill import (
    SKILL_PANEL_ORDER, skill_index, skill_name_at, skill_names,
)
from src.game_state.recommendation_adapter import (
    RecommendationStateError, adapt_action,
)
from src.parser.recommendation_parser import (
    RecommendationParseError, RecommendationParser,
)
from src.recommendation_models import ActionKind

# 用户 2026-10-27 截图里盒子面板的真实排版（OCR 原文）。
PANEL_A = ("打法参考A\n操作2号位随从攻击\n克苏恩，帝国之眼\n"
           "选择卡牌\n克苏恩的愤怒")
PANEL_B = ("打法参考A\n操作2号位随从攻击\n克苏恩，帝国之眼\n目标是对方2号位\n"
           "导航员伊莉斯\n选择卡牌\n克苏恩的凝视")
# 面板上同时出现 A/B 两段（截图原样）：只取 A 段，不能被 B 段的目标/技能名带跑。
PANEL_AB = (PANEL_A + "\n打法参考B\n操作2号位随从攻击\n克苏恩，帝国之眼\n"
            "目标是对方2号位\n导航员伊莉斯\n选择卡牌\n克苏恩的凝视")


def parse(text, turn=3, revision=7):
    return RecommendationParser().parse(
        SimpleNamespace(frame_id="frame", normalized_text=text,
                        confidence=0.99),
        turn_number=turn, log_revision=revision)


def minion(entity_id, zone_pos, card_id="CS2_182", name="普通随从"):
    return SimpleNamespace(entity_id=entity_id, zone_pos=zone_pos,
                           card_id=card_id, cardtype="MINION", name=name)


def location(entity_id, zone_pos, card_id="CATA_X", name="地标"):
    return SimpleNamespace(entity_id=entity_id, zone_pos=zone_pos,
                           card_id=card_id, cardtype="LOCATION", name=name)


def state(**overrides):
    data = dict(
        game_num_turns_in_play=3,
        is_my_turn=True,
        my_player_id="1",
        discover_choice_count=None,
        discover_choice_pending=False,
        general_choice_cards={},
        general_choice_entity_ids={},
        power_options={},
        my_hand_cards=[],
        my_minions=[minion("300", 1, name="一号"),
                    minion("301", 2, "BE_013", "克苏恩，帝国之眼")],
        my_locations=[],
        oppo_locations=[],
        oppo_minions=[minion("200", 1, name="敌方一号"),
                      minion("201", 2, name="导航员伊莉斯")],
        my_hero=SimpleNamespace(entity_id="hero", name="我方英雄", health=30),
        oppo_hero=SimpleNamespace(entity_id="enemy-hero", name="敌方英雄",
                                 health=30),
        my_last_mana=5,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


class RecordingClicks:
    def __init__(self):
        self.events = []

    def cancel_click(self):
        self.events.append("cancel")

    def choose_my_board_entity(self, index, count):
        self.events.append(("board", index, count))

    def choose_skill_card(self, index, count):
        self.events.append(("skill", index, count))

    def choose_my_minion(self, index, count):
        self.events.append(("mine", index, count))

    def choose_opponent_minion(self, index, count):
        self.events.append(("oppo", index, count))

    def choose_oppo_hero(self):
        self.events.append("oppo_hero")


def execute(action, current_state):
    recorder = RecordingClicks()
    controller = ManualController(
        output_func=lambda _msg: None,
        executor=ClickExecutor(click_module=recorder,
                               sleep_func=lambda _seconds: None,
                               action_context=nullcontext))
    result = controller.execute(action, current_state)
    return recorder.events, result


class RecordedPanelTests(unittest.TestCase):
    """号位与坐标是实测记录值：改了它们就等于换了点击行为，必须显式确认。"""

    def test_panel_order_matches_the_recorded_screenshot(self):
        def panel_order():
            self.assertEqual(
                ("克苏恩的愤怒", "克苏恩的领域", "克苏恩的凝视", "克苏恩的仆从"),
                skill_names("BE_013"))
            self.assertEqual(("克苏恩的愤怒", "克苏恩的领域", "克苏恩的凝视",
                              "克苏恩的仆从"),
                             tuple(name for name, _
                                   in SKILL_PANEL_ORDER["BE_013"]))

        def name_lookup_and_ocr_typo():
            self.assertEqual(0, skill_index("BE_013", "克苏恩的愤怒"))
            self.assertEqual(1, skill_index("BE_013", "克苏恩的领域"))
            self.assertEqual(2, skill_index("BE_013", "克苏恩的凝视"))
            self.assertEqual(3, skill_index("BE_013", "克苏恩的仆从"))
            # OCR 常把「愤」认成「惯」：唯一命中才认。
            self.assertEqual(0, skill_index("BE_013", "克苏恩的惯怒"))
            self.assertIsNone(skill_index("BE_013", "克苏恩的"))
            self.assertIsNone(skill_index("BE_013", "冰雪绽放"))
            self.assertIsNone(skill_index("BE_013", None))
            self.assertIsNone(skill_name_at("BE_013", 4))

        for case, check in (("panel_order", panel_order),
                            ("name_lookup_and_ocr_typo",
                             name_lookup_and_ocr_typo)):
            with self.subTest(case=case):
                check()

    def test_click_points_are_the_recorded_ones(self):
        with patch.object(hearthstone_click, "left_click") as left_click, \
                patch.object(hearthstone_click, "rand_sleep"):
            for index, point in enumerate(
                    ((480, 450), (790, 450), (1100, 450), (1410, 450))):
                with self.subTest(index=index):
                    left_click.reset_mock()
                    hearthstone_click.choose_skill_card(index, 4)
                    left_click.assert_called_once_with(*point)


class ParserTests(unittest.TestCase):
    def test_attack_line_plus_choice_line_is_an_ability(self):
        def without_target():
            proposed = parse(PANEL_A)

            self.assertEqual(ActionKind.USE_ABILITY, proposed.action)
            self.assertEqual("board_slot", proposed.source.kind)
            self.assertEqual("friendly", proposed.source.owner)
            self.assertEqual(2, proposed.source.index)
            self.assertEqual("克苏恩的愤怒", proposed.choice_name)
            self.assertIsNone(proposed.target)

        def with_target():
            proposed = parse(PANEL_B)

            self.assertEqual(ActionKind.USE_ABILITY, proposed.action)
            self.assertEqual(2, proposed.source.index)
            self.assertEqual("克苏恩的凝视", proposed.choice_name)
            self.assertEqual("enemy", proposed.target.owner)
            self.assertEqual("board_slot", proposed.target.kind)
            self.assertEqual(2, proposed.target.index)

        for case, check in (("without_target", without_target),
                            ("with_target", with_target)):
            with self.subTest(case=case):
                check()

    def test_reference_b_section_does_not_confuse_the_primary_action(self):
        proposed = parse(PANEL_AB)

        self.assertEqual(ActionKind.USE_ABILITY, proposed.action)
        self.assertEqual("克苏恩的愤怒", proposed.choice_name)
        self.assertIsNone(proposed.target)

    def test_plain_attack_stays_an_attack(self):
        """没有「选择卡牌」行时绝不能变成技能（普通随从交换必须照旧）。"""
        proposed = parse("打法参考A\n操作2号位随从攻击\n目标是对方1号位")

        self.assertEqual(ActionKind.ATTACK, proposed.action)
        self.assertEqual(2, proposed.source.index)
        self.assertEqual(1, proposed.target.index)

    def test_choice_line_without_skill_name_is_rejected(self):
        with self.assertRaisesRegex(RecommendationParseError,
                                    "ability_name_required"):
            parse("打法参考A\n操作2号位随从攻击\n选择卡牌")


class AdapterTests(unittest.TestCase):
    def test_named_skill_becomes_a_use_ability_action(self):
        def plain_skill():
            adapted = adapt_action(parse(PANEL_A), state())
            action = adapted.manual_action

            self.assertIsInstance(action, UseAbilityAction)
            self.assertEqual("BE_013", action.card_id)
            self.assertEqual(0, action.choice_index)          # 0 基
            self.assertEqual(4, action.choice_count)
            self.assertEqual("克苏恩的愤怒", action.choice_name)
            self.assertEqual(("friendly", "minion", 1),
                             (action.source.side, action.source.kind,
                              action.source.index))
            self.assertEqual("301", action.source.entity_id)
            self.assertIsNone(action.target)
            self.assertEqual("ability_used", adapted.postcondition)

        def gaze_skill():
            action = adapt_action(parse(PANEL_B), state()).manual_action

            self.assertEqual(2, action.choice_index)
            self.assertEqual(("enemy", "minion", 1),
                             (action.target.side, action.target.kind,
                              action.target.index))
            self.assertEqual("201", action.target.entity_id)

        def enemy_hero_target():
            text = ("打法参考A\n操作2号位随从攻击\n目标是对方英雄\n"
                    "选择卡牌\n克苏恩的凝视")
            action = adapt_action(parse(text), state()).manual_action
            self.assertEqual(("enemy", "hero"),
                             (action.target.side, action.target.kind))
            self.assertEqual("enemy-hero", action.target.entity_id)

        for case, check in (("plain_skill", plain_skill),
                            ("gaze_skill", gaze_skill),
                            ("enemy_hero_target", enemy_hero_target)):
            with self.subTest(case=case):
                check()

    def test_unknown_card_never_guesses_a_slot(self):
        def unknown_card():
            current = state(my_minions=[minion("300", 1, name="一号"),
                                        minion("301", 2, name="普通二号")])
            with self.assertRaisesRegex(RecommendationStateError,
                                        "ability_card_unknown"):
                adapt_action(parse(PANEL_A), current)

        def unknown_skill_name():
            bad = PANEL_A.replace("克苏恩的愤怒", "克苏恩的雷霆")
            with self.assertRaisesRegex(RecommendationStateError,
                                        "ability_option_unknown"):
                adapt_action(parse(bad), state())

        for case, check in (("unknown_card", unknown_card),
                            ("unknown_skill_name", unknown_skill_name)):
            with self.subTest(case=case):
                check()

    def test_source_must_be_a_minion_on_my_board(self):
        current = state(my_minions=[minion("300", 1, name="一号")],
                        my_locations=[location("302", 2)])
        with self.assertRaisesRegex(RecommendationStateError,
                                    "ability_source_not_minion"):
            adapt_action(parse(PANEL_A), current)

    def test_source_out_of_range_is_rejected(self):
        with self.assertRaisesRegex(RecommendationStateError,
                                    "ability_source_out_of_range"):
            adapt_action(parse("打法参考A\n操作5号位随从攻击\n选择卡牌\n"
                               "克苏恩的愤怒"), state())

    def test_target_out_of_range_is_rejected(self):
        bad = PANEL_B.replace("目标是对方2号位", "目标是对方9号位")
        with self.assertRaisesRegex(RecommendationStateError,
                                    "ability_target_out_of_range"):
            adapt_action(parse(bad), state())

    def test_target_contract_rejects_partial_ability_gestures(self):
        missing_target = PANEL_B.replace(
            "目标是对方2号位\n导航员伊莉斯\n", "")
        with self.assertRaisesRegex(RecommendationStateError,
                                    "ability_target_required"):
            adapt_action(parse(missing_target), state())

        unexpected_target = (
            "打法参考A\n操作2号位随从攻击\n克苏恩，帝国之眼\n"
            "目标是对方2号位\n导航员伊莉斯\n选择卡牌\n克苏恩的愤怒"
        )
        with self.assertRaisesRegex(RecommendationStateError,
                                    "ability_target_unexpected"):
            adapt_action(parse(unexpected_target), state())


class ClickSequenceTests(unittest.TestCase):
    def test_click_minion_then_skill_card(self):
        def skill_only():
            action = adapt_action(parse(PANEL_A), state()).manual_action
            events, result = execute(action, state())

            self.assertTrue(result.executed, result.message)
            self.assertEqual(["cancel", ("board", 1, 2), ("skill", 0, 4)],
                             events)
            self.assertIn("克苏恩的愤怒", result.message)

        def skill_then_target():
            action = adapt_action(parse(PANEL_B), state()).manual_action
            events, result = execute(action, state())

            self.assertTrue(result.executed, result.message)
            self.assertEqual(["cancel", ("board", 1, 2), ("skill", 2, 4),
                              ("oppo", 1, 2)], events)
            self.assertNotEqual("cancel", events[-1])   # 凝视还在等目标，不能右键
            self.assertIn("克苏恩的凝视", result.message)
            self.assertIn("对方2号位", result.message)

        for case, check in (("skill_only", skill_only),
                            ("skill_then_target", skill_then_target)):
            with self.subTest(case=case):
                check()

    def test_minion_changed_blocks_the_clicks(self):
        def minion_changed():
            action = adapt_action(parse(PANEL_A), state()).manual_action
            current = state(
                my_minions=[minion("300", 1, name="一号"),
                            minion("999", 2, "CS2_182", "换人了")])
            events, result = execute(action, current)

            self.assertFalse(result.executed)
            self.assertEqual([], events)

        def target_gone():
            action = adapt_action(parse(PANEL_B), state()).manual_action
            current = state(oppo_minions=[minion("200", 1, name="敌方一号")])
            events, result = execute(action, current)

            self.assertFalse(result.executed)
            self.assertEqual([], events)

        for case, check in (("minion_changed", minion_changed),
                            ("target_gone", target_gone)):
            with self.subTest(case=case):
                check()


class _Frames:
    def __init__(self):
        self.hashes = iter(("before", "after"))

    def capture(self, ocr_panel_ok=False):
        return SimpleNamespace(exact_hash=next(self.hashes, "after"),
                               frame_id="frame")

    @staticmethod
    def crop_recommendation(frame):
        return frame


class _Reader:
    def __init__(self, text):
        self.text = text

    def read(self, frame_supplier, _roi_supplier):
        frame = frame_supplier()
        return SimpleNamespace(normalized_text=self.text,
                               frame_id=frame.frame_id)


def _flow(text, states):
    return RecommendationFlow(
        capture=_Frames(),
        reader=_Reader(text),
        parser=RecommendationParser(),
        state_supplier=lambda: next(states),
        adapter=adapt_action,
        validator=None,
        controller=None,
    )


class VerifyResultTests(unittest.TestCase):
    def test_postcondition_verification_status(self):
        before_frame = SimpleNamespace(exact_hash="before", frame_id="frame")

        def no_log_change():
            """No immediate evidence must wait instead of replaying the same click."""
            proposed = parse(PANEL_A)
            adapted = adapt_action(proposed, state())
            before = state()
            flow = _flow(PANEL_A, iter([(before, 7)]))

            result = flow._verify_result(
                proposed, adapted, before_frame, before, 7)

            self.assertEqual(ResultStatus.UNCONFIRMED, result)

        def settled_ability():
            """技能结算有日志痕迹（这里用敌方随从掉血）：当已发动，等盒子更新。"""
            proposed = parse(PANEL_B)
            before = state()
            adapted = adapt_action(proposed, before)
            after = state(oppo_minions=[minion("200", 1, name="敌方一号"),
                                        SimpleNamespace(entity_id="201",
                                                        zone_pos=2,
                                                        card_id="X",
                                                        cardtype="MINION",
                                                        name="导航员伊莉斯",
                                                        damage=15,
                                                        health=5)])
            flow = _flow(PANEL_B, iter([(after, 8)]))

            result = flow._verify_result(
                proposed, adapted, before_frame, before, 7)

            self.assertEqual(ResultStatus.WAITING_RECOMMENDATION, result)

        def mana_spent():
            proposed = parse(PANEL_A)
            before = state()
            adapted = adapt_action(proposed, before)
            after = state(my_last_mana=3)
            flow = _flow(PANEL_A, iter([(after, 8)]))

            result = flow._verify_result(
                proposed, adapted, before_frame, before, 7)

            self.assertEqual(ResultStatus.WAITING_RECOMMENDATION, result)

        for case, check in (("no_log_change", no_log_change),
                            ("settled_ability", settled_ability),
                            ("mana_spent", mana_spent)):
            with self.subTest(case=case):
                check()


if __name__ == "__main__":
    unittest.main()
