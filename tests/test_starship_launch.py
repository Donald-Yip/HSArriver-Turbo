"""星舰发射：识别、适配、两次点击与坐标换算。

来源：群友改的星舰逻辑（原始 zip 见 Downloads），按本仓库结构迁移——
- 星舰卡不再只硬编码 `SC_999t`，改为读本地 cards.json 里带 STARSHIP 机制的卡；
- 新增明确发射指令「发射N号位星舰」（也接受「操作N号位星舰」「发射我方N号位星舰」）；
- 盒子把发射写成「操作N号位随从攻击」且没有目标行时，只要那张卡是星舰也走发射；
  带目标的星舰攻击仍按普通攻击处理，避免把已发射星舰的攻击误当成再次发射；
- 执行：点场上星舰 → 等 0.8s（组件面板要画出来）→ 点「发射」按钮（客户区坐标换算）。
"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import click as hearthstone_click
from manual_controller import (
    ClickExecutor,
    LaunchStarshipAction,
    ManualController,
)
from src.game_state.recommendation_adapter import (
    RecommendationStateError,
    adapt_action,
)
from src.game_state.starship import (
    LEGACY_STARSHIP_CARD_ID,
    is_starship_card,
    starship_card_ids,
)
from src.parser.recommendation_parser import (
    RecommendationParseError,
    RecommendationParser,
)
from src.recommendation_models import ActionKind

# cards.json 里带 STARSHIP 机制的卡（共 8 张，SC_999t 是老实现唯一认识的那张）。
STARSHIP_CARD = "GDB_100t2"
PLAIN_MINION_CARD = "MINION_NOT_A_STARSHIP"


def _ocr(instruction):
    return SimpleNamespace(
        frame_id="frame-1", normalized_text=instruction, confidence=0.99)


class StarshipCardDataTests(unittest.TestCase):
    def test_local_metadata_lists_the_starship_cards(self):
        ids = starship_card_ids()

        self.assertIn(LEGACY_STARSHIP_CARD_ID, ids)
        self.assertIn(STARSHIP_CARD, ids)
        self.assertEqual(8, len(ids))          # 本地 cards.json 当前收录数

    def test_lookup_is_cached(self):
        self.assertIs(starship_card_ids(), starship_card_ids())

    def test_is_starship_card(self):
        self.assertTrue(is_starship_card(LEGACY_STARSHIP_CARD_ID))
        self.assertFalse(is_starship_card(PLAIN_MINION_CARD))

    def test_unreadable_card_data_falls_back_to_the_legacy_card(self):
        """cards.json 读不出来时不能把普通攻击一起拖垮：退回原来那张硬编码卡。"""
        with patch("src.game_state.starship.starship_card_ids",
                   side_effect=OSError("cards.json missing")):
            self.assertTrue(is_starship_card(LEGACY_STARSHIP_CARD_ID))
            self.assertFalse(is_starship_card(STARSHIP_CARD))


class StarshipParserTests(unittest.TestCase):
    def setUp(self):
        self.parser = RecommendationParser()

    def _parse(self, instruction):
        return self.parser.parse(_ocr(instruction), turn_number=3,
                                 log_revision=7)

    def test_explicit_launch_instructions(self):
        for text, slot in (("发射1号位星舰", 1),
                           ("操作2号位星舰", 2),
                           ("发射我方3号位星舰", 3)):
            with self.subTest(text=text):
                proposed = self._parse(text)

                self.assertEqual(ActionKind.LAUNCH_STARSHIP, proposed.action)
                self.assertEqual(("board_slot", "friendly", slot),
                                 (proposed.source.kind, proposed.source.owner,
                                  proposed.source.index))
                self.assertIsNone(proposed.target)

    def test_launch_line_survives_the_reference_filter(self):
        proposed = self._parse("打法参考A\n发射1号位星舰")

        self.assertEqual(ActionKind.LAUNCH_STARSHIP, proposed.action)

    def test_launch_with_a_target_line_is_refused(self):
        with self.assertRaises(RecommendationParseError):
            self._parse("发射1号位星舰\n目标是对方英雄")

    def test_minion_attack_without_target_still_parses_as_attack(self):
        """盒子把发射写成「操作N号位随从攻击」时也解析成攻击，由适配层按卡判断。"""
        proposed = self._parse("操作1号位随从攻击")

        self.assertEqual(ActionKind.ATTACK, proposed.action)
        self.assertIsNone(proposed.target)

    def test_ordinary_minion_attack_keeps_its_target(self):
        proposed = self._parse("操作1号位随从攻击\n目标是敌方2号位随从")

        self.assertEqual(ActionKind.ATTACK, proposed.action)
        self.assertEqual(("board_slot", "enemy", 2),
                         (proposed.target.kind, proposed.target.owner,
                          proposed.target.index))

    def test_location_instruction_is_unaffected(self):
        proposed = self._parse("操作1号位地标")

        self.assertEqual(ActionKind.USE_LOCATION, proposed.action)


class StarshipAdapterTests(unittest.TestCase):
    @staticmethod
    def _starship(card_id=STARSHIP_CARD, entity_id="starship-1", zone_pos=1):
        return SimpleNamespace(
            card_id=card_id, entity_id=entity_id, zone_pos=zone_pos,
            name="星舰")

    @staticmethod
    def _state(my_minions, oppo_minions=()):
        return SimpleNamespace(
            game_num_turns_in_play=3,
            my_minions=list(my_minions),
            my_locations=[],
            oppo_minions=list(oppo_minions),
            oppo_locations=[],
            my_hero=SimpleNamespace(entity_id="friendly-hero"),
            oppo_hero=SimpleNamespace(entity_id="enemy-hero"),
        )

    def _adapt(self, instruction, state):
        parser = RecommendationParser()
        return adapt_action(
            parser.parse(_ocr(instruction), turn_number=3, log_revision=7),
            state)

    def test_explicit_launch_binds_the_starship(self):
        state = self._state([self._starship()])

        adapted = self._adapt("发射1号位星舰", state)

        self.assertIsInstance(adapted.manual_action, LaunchStarshipAction)
        self.assertEqual((0, STARSHIP_CARD, "starship-1"),
                         (adapted.manual_action.starship_index,
                          adapted.manual_action.card_id,
                          adapted.manual_action.starship_entity_id))
        self.assertEqual("starship-1", adapted.source_entity_id)
        self.assertIsNone(adapted.target_entity_id)
        self.assertEqual("starship_launched", adapted.postcondition)

    def test_explicit_launch_refuses_a_non_starship_slot(self):
        state = self._state([SimpleNamespace(
            card_id=PLAIN_MINION_CARD, entity_id="minion-1", zone_pos=1)])

        with self.assertRaises(RecommendationStateError) as caught:
            self._adapt("发射1号位星舰", state)

        self.assertEqual("source_not_starship", str(caught.exception))

    def test_plain_minion_instruction_on_a_starship_lands_its_launch(self):
        """没有目标行 + 该位置是星舰 = 盒子其实在说「发射」。"""
        state = self._state([self._starship()])

        adapted = self._adapt("操作1号位随从攻击", state)

        self.assertIsInstance(adapted.manual_action, LaunchStarshipAction)
        self.assertEqual("starship_launched", adapted.postcondition)

    def test_targeted_instruction_on_a_starship_stays_an_attack(self):
        enemy = SimpleNamespace(
            card_id="MINION_ENEMY", entity_id="enemy-1", zone_pos=1)
        state = self._state([self._starship()], [enemy])

        adapted = self._adapt("操作1号位随从攻击\n目标是对方英雄", state)

        self.assertNotIsInstance(adapted.manual_action, LaunchStarshipAction)
        self.assertEqual("combat_state_changed", adapted.postcondition)

    def test_plain_minion_without_a_target_still_requires_a_target(self):
        state = self._state([SimpleNamespace(
            card_id=PLAIN_MINION_CARD, entity_id="minion-1", zone_pos=1)])

        with self.assertRaises(RecommendationStateError) as caught:
            self._adapt("操作1号位随从攻击", state)

        self.assertEqual("attack_target_required", str(caught.exception))

    def test_starship_can_sit_in_a_later_board_slot(self):
        state = self._state([
            SimpleNamespace(card_id=PLAIN_MINION_CARD, entity_id="minion-1",
                            zone_pos=1),
            self._starship(entity_id="starship-2", zone_pos=2),
        ])

        adapted = self._adapt("发射2号位星舰", state)

        self.assertEqual((1, "starship-2"),
                         (adapted.manual_action.starship_index,
                          adapted.manual_action.starship_entity_id))


class RecordingClickModule:
    def __init__(self):
        self.events = []

    def choose_my_board_entity(self, index, count):
        self.events.append(("choose_my_board_entity", index, count))

    def click_launch_starship(self):
        self.events.append(("click_launch_starship",))

    def cancel_click(self):
        self.events.append(("cancel_click",))


class StarshipExecutionTests(unittest.TestCase):
    def test_board_click_then_panel_wait_then_launch(self):
        clicks = RecordingClickModule()
        sleeps = []
        executor = ClickExecutor(click_module=clicks,
                                 sleep_func=sleeps.append,
                                 action_context=nullcontext)

        executor.launch_starship(2, 4)

        self.assertEqual([("choose_my_board_entity", 2, 4),
                          ("click_launch_starship",)], clicks.events)
        self.assertEqual([0.8], sleeps)        # 等组件面板画出来再点发射

    def test_execute_validates_the_entity_then_launches(self):
        starship = SimpleNamespace(
            card_id=STARSHIP_CARD, entity_id="starship-1", zone_pos=1,
            name="星舰")
        state = SimpleNamespace(
            is_my_turn=True, game_num_turns_in_play=3,
            my_minions=[starship], my_locations=[], my_board_slot_num=1)
        clicks = RecordingClickModule()
        sleeps = []
        controller = ManualController(
            output_func=lambda _message: None,
            executor=ClickExecutor(click_module=clicks,
                                   sleep_func=sleeps.append,
                                   action_context=nullcontext),
        )

        result = controller.execute(
            LaunchStarshipAction(0, STARSHIP_CARD, "starship-1"), state)

        self.assertTrue(result.executed, result.message)
        self.assertEqual([("choose_my_board_entity", 0, 1),
                          ("click_launch_starship",)], clicks.events)
        self.assertEqual([0.8], sleeps)

    def test_execute_rejects_a_changed_entity(self):
        starship = SimpleNamespace(
            card_id=STARSHIP_CARD, entity_id="starship-2", zone_pos=1,
            name="星舰")
        state = SimpleNamespace(
            is_my_turn=True, game_num_turns_in_play=3,
            my_minions=[starship], my_locations=[], my_board_slot_num=1)
        executor = ClickExecutor(click_module=RecordingClickModule(),
                                 sleep_func=lambda _seconds: None,
                                 action_context=nullcontext)
        controller = ManualController(output_func=lambda _message: None,
                                      executor=executor)

        result = controller.execute(
            LaunchStarshipAction(0, STARSHIP_CARD, "starship-1"), state)

        self.assertFalse(result.executed)
        self.assertEqual([], executor.click.events)


class StarshipLaunchClickTests(unittest.TestCase):
    """发射按钮坐标：1920x1080 客户区校准点 (1070,920)，按客户区等比换算。"""

    def setUp(self):
        self.clicks = []
        self._patches = [
            patch.object(hearthstone_click, "get_HS_hwnd", return_value=101),
            patch.object(hearthstone_click.win32gui, "GetClientRect",
                         return_value=(0, 0, 1920, 1080)),
            patch.object(hearthstone_click.win32gui, "ClientToScreen",
                         side_effect=lambda _hwnd, point: point),
            patch.object(hearthstone_click, "left_click",
                         side_effect=lambda x, y: self.clicks.append((x, y))),
            patch.object(hearthstone_click, "rand_sleep"),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in self._patches:
            item.stop()

    def test_reference_layout_clicks_the_calibrated_point(self):
        hearthstone_click.click_launch_starship()

        self.assertEqual([hearthstone_click.STARSHIP_LAUNCH_REF_POS],
                         self.clicks)

    def test_scales_with_the_client_area_and_window_offset(self):
        with (
            patch.object(hearthstone_click.win32gui, "GetClientRect",
                         return_value=(0, 0, 1280, 720)),
            patch.object(hearthstone_click.win32gui, "ClientToScreen",
                         side_effect=lambda _hwnd, point: (point[0] + 100,
                                                           point[1] + 50)),
        ):
            hearthstone_click.click_launch_starship()

        self.assertEqual([(100 + round(1280 * 1070 / 1920),
                           50 + round(720 * 920 / 1080))], self.clicks)

    def test_missing_window_is_refused_without_clicking(self):
        with patch.object(hearthstone_click, "get_HS_hwnd", return_value=0):
            with self.assertRaises(ValueError):
                hearthstone_click.click_launch_starship()

        self.assertEqual([], self.clicks)

    def test_invalid_client_area_is_refused_without_clicking(self):
        with patch.object(hearthstone_click.win32gui, "GetClientRect",
                          return_value=(0, 0, 0, 0)):
            with self.assertRaises(ValueError):
                hearthstone_click.click_launch_starship()

        self.assertEqual([], self.clicks)


if __name__ == "__main__":
    unittest.main()
