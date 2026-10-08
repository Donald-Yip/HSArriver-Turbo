import unittest
from contextlib import nullcontext
from types import SimpleNamespace

from manual_controller import ClickExecutor, ManualController
from src.game_state.recommendation_adapter import (
    RecommendationStateError,
    adapt_action,
)
from src.parser.recommendation_parser import RecommendationParser


class RecordingClickModule:
    def __init__(self):
        self.events = []

    def choose_my_board_entity(self, index, count):
        self.events.append(("choose_my_board_entity", index, count))

    def choose_my_minion(self, index, count):
        self.events.append(("choose_my_minion", index, count))

    def choose_opponent_minion(self, index, count):
        self.events.append(("choose_opponent_minion", index, count))

    def choose_my_hero(self):
        self.events.append(("choose_my_hero",))

    def choose_oppo_hero(self):
        self.events.append(("choose_oppo_hero",))

    def choose_card(self, index, count):
        self.events.append(("choose_card", index, count))

    def cancel_click(self):
        self.events.append(("cancel_click",))


class TargetedLocationTests(unittest.TestCase):
    @staticmethod
    def _state():
        friendly_minion = SimpleNamespace(
            card_id="MINION_FRIENDLY",
            entity_id="friendly-minion",
            zone_pos=1,
            name="暗影投弹手",
        )
        location = SimpleNamespace(
            card_id="REV_290",
            entity_id="location-1",
            zone_pos=2,
            name="罪碑坟场",
        )
        enemy_minion = SimpleNamespace(
            card_id="MINION_ENEMY",
            entity_id="enemy-minion",
            zone_pos=1,
            name="敌方随从",
        )
        return SimpleNamespace(
            game_num_turns_in_play=3,
            is_my_turn=True,
            my_hand_cards=[],
            my_minions=[friendly_minion],
            my_locations=[location],
            my_board_slot_num=2,
            oppo_minions=[enemy_minion],
            oppo_locations=[],
            oppo_board_slot_num=1,
            my_hero=SimpleNamespace(entity_id="friendly-hero"),
            oppo_hero=SimpleNamespace(entity_id="enemy-hero"),
        )

    @staticmethod
    def _proposed(target_line=None, source_slot=2):
        lines = [
            "打法参考A", f"操作{source_slot}号位地标", "罪碑坟场"]
        if target_line is not None:
            lines.extend((target_line, "暗影投弹手"))
        ocr = SimpleNamespace(
            frame_id="frame-1",
            normalized_text="\n".join(lines),
            confidence=0.99,
        )
        return RecommendationParser().parse(
            ocr, turn_number=3, log_revision=7)

    @staticmethod
    def _controller(clicks, sleeps):
        return ManualController(
            output_func=lambda _message: None,
            executor=ClickExecutor(
                click_module=clicks,
                sleep_func=sleeps.append,
                action_context=nullcontext,
            ),
        )

    def test_friendly_minion_target_is_bound_and_clicked_after_delay(self):
        state = self._state()
        try:
            adapted = adapt_action(
                self._proposed("目标是己方1号位"), state)
        except Exception as exc:
            self.fail(f"targeted location should be supported: {exc}")
        clicks = RecordingClickModule()
        sleeps = []

        result = self._controller(clicks, sleeps).execute(
            adapted.manual_action, state)

        self.assertTrue(result.executed, result.message)
        self.assertEqual("friendly-minion", adapted.target_entity_id)
        self.assertEqual([0.3], sleeps)
        self.assertEqual([
            ("choose_my_board_entity", 1, 2),
            ("choose_my_minion", 0, 2),
            ("cancel_click",),
        ], clicks.events)

    def test_locations_support_all_board_and_hero_target_sides(self):
        cases = (
            ("目标是己方1号位", "friendly", "minion",
             "friendly-minion"),
            ("目标是敌方1号位随从", "enemy", "minion",
             "enemy-minion"),
            ("目标是我方英雄", "friendly", "hero", "friendly-hero"),
            ("目标是对方英雄", "enemy", "hero", "enemy-hero"),
        )
        for target_line, side, kind, entity_id in cases:
            with self.subTest(target_line=target_line):
                try:
                    adapted = adapt_action(
                        self._proposed(target_line), self._state())
                except Exception as exc:
                    self.fail(
                        f"location target should be supported: {exc}")

                self.assertEqual(side, adapted.manual_action.target.side)
                self.assertEqual(kind, adapted.manual_action.target.kind)
                self.assertEqual(
                    entity_id, adapted.manual_action.target.entity_id)
                self.assertEqual(entity_id, adapted.target_entity_id)

    def test_enemy_minion_and_hero_targets_click_expected_entity(self):
        cases = (
            ("目标是敌方1号位随从",
             ("choose_opponent_minion", 0, 1)),
            ("目标是我方英雄", ("choose_my_hero",)),
            ("目标是对方英雄", ("choose_oppo_hero",)),
        )
        for target_line, target_click in cases:
            with self.subTest(target_line=target_line):
                state = self._state()
                adapted = adapt_action(self._proposed(target_line), state)
                clicks = RecordingClickModule()
                sleeps = []

                result = self._controller(clicks, sleeps).execute(
                    adapted.manual_action, state)

                self.assertTrue(result.executed, result.message)
                self.assertEqual([0.3], sleeps)
                self.assertEqual([
                    ("choose_my_board_entity", 1, 2),
                    target_click,
                    ("cancel_click",),
                ], clicks.events)

    def test_target_screen_index_includes_location_before_minion(self):
        state = self._state()
        state.my_locations[0].zone_pos = 1
        state.my_minions[0].zone_pos = 2
        adapted = adapt_action(
            self._proposed("目标是己方2号位", source_slot=1), state)
        clicks = RecordingClickModule()
        sleeps = []

        result = self._controller(clicks, sleeps).execute(
            adapted.manual_action, state)

        self.assertTrue(result.executed, result.message)
        self.assertEqual([0.3], sleeps)
        self.assertEqual([
            ("choose_my_board_entity", 0, 2),
            ("choose_my_minion", 1, 2),
            ("cancel_click",),
        ], clicks.events)

    def test_untargeted_location_still_clicks_only_the_location(self):
        state = self._state()
        adapted = adapt_action(self._proposed(), state)
        clicks = RecordingClickModule()
        sleeps = []

        result = self._controller(clicks, sleeps).execute(
            adapted.manual_action, state)

        self.assertTrue(result.executed, result.message)
        self.assertIsNone(adapted.manual_action.target)
        self.assertEqual([], sleeps)
        self.assertEqual([
            ("choose_my_board_entity", 1, 2),
            ("cancel_click",),
        ], clicks.events)

    def test_replaced_location_target_is_rejected_before_any_click(self):
        state = self._state()
        try:
            adapted = adapt_action(
                self._proposed("目标是己方1号位"), state)
        except Exception as exc:
            self.fail(f"targeted location should be supported: {exc}")
        state.my_minions[0] = SimpleNamespace(
            card_id="REPLACEMENT",
            entity_id="replacement-minion",
            zone_pos=1,
            name="替换随从",
        )
        clicks = RecordingClickModule()
        sleeps = []

        result = self._controller(clicks, sleeps).execute(
            adapted.manual_action, state)

        self.assertFalse(result.executed)
        self.assertEqual([], sleeps)
        self.assertEqual([], clicks.events)

    def test_location_board_slot_cannot_be_used_as_a_minion_target(self):
        with self.assertRaisesRegex(
                RecommendationStateError, "target_not_minion"):
            adapt_action(
                self._proposed("目标是己方2号位"), self._state())

    def test_hero_power_keeps_using_the_shared_target_grammar(self):
        ocr = SimpleNamespace(
            frame_id="frame-2",
            normalized_text="使用英雄技能\n目标是敌方1号位随从",
            confidence=0.99,
        )

        proposed = RecommendationParser().parse(
            ocr, turn_number=3, log_revision=7)

        self.assertEqual("enemy", proposed.target.owner)
        self.assertEqual("board_slot", proposed.target.kind)
        self.assertEqual(1, proposed.target.index)


class HandTargetLocationTests(unittest.TestCase):
    """守护巨龙之厅：地标让你选一张自己的手牌，盒子也写成「目标是我方N号位」。"""

    @staticmethod
    def _state():
        location = SimpleNamespace(
            card_id="CATA_477",
            entity_id="location-1",
            zone_pos=1,
            name="守护巨龙之厅",
        )
        hand = [
            SimpleNamespace(
                card_id=f"HAND_{index}", entity_id=f"hand-{index}",
                name=f"手牌{index}")
            for index in (1, 2, 3)
        ]
        return SimpleNamespace(
            game_num_turns_in_play=3,
            is_my_turn=True,
            my_hand_cards=hand,
            my_minions=[],
            my_locations=[location],
            my_board_slot_num=1,
            oppo_minions=[],
            oppo_locations=[],
            oppo_board_slot_num=0,
        )

    @staticmethod
    def _proposed(target_line="目标是我方3号位", source_slot=1):
        ocr = SimpleNamespace(
            frame_id="frame-1",
            normalized_text="\n".join(
                ("打法参考A", f"操作{source_slot}号位地标", target_line)),
            confidence=0.99,
        )
        return RecommendationParser().parse(
            ocr, turn_number=3, log_revision=7)

    @staticmethod
    def _controller(clicks, sleeps):
        return ManualController(
            output_func=lambda _message: None,
            executor=ClickExecutor(
                click_module=clicks,
                sleep_func=sleeps.append,
                action_context=nullcontext,
            ),
        )

    def test_hand_target_is_clicked_in_the_hand_fan(self):
        state = self._state()
        adapted = adapt_action(self._proposed(), state)
        clicks = RecordingClickModule()
        sleeps = []

        result = self._controller(clicks, sleeps).execute(
            adapted.manual_action, state)

        self.assertTrue(result.executed, result.message)
        self.assertEqual("friendly", adapted.manual_action.target.side)
        self.assertEqual("hand", adapted.manual_action.target.kind)
        self.assertEqual(2, adapted.manual_action.target.index)
        self.assertEqual("hand-3", adapted.target_entity_id)
        # 0.3s = 点地标后等目标 UI 出现；0.9s = 手牌目标本身出现得慢（统一入口里等）
        self.assertEqual([0.3, 0.9], sleeps)
        self.assertEqual([
            ("choose_my_board_entity", 0, 1),
            ("choose_card", 2, 3),
            ("cancel_click",),
        ], clicks.events)

    def test_hand_target_out_of_range_is_refused(self):
        with self.assertRaisesRegex(
                RecommendationStateError, "hand_target_out_of_range"):
            adapt_action(self._proposed("目标是我方4号位"), self._state())

    def test_replaced_hand_target_is_rejected_before_any_click(self):
        state = self._state()
        adapted = adapt_action(self._proposed(), state)
        state.my_hand_cards[2] = SimpleNamespace(
            card_id="HAND_NEW", entity_id="hand-new", name="替换手牌")
        clicks = RecordingClickModule()
        sleeps = []

        result = self._controller(clicks, sleeps).execute(
            adapted.manual_action, state)

        self.assertFalse(result.executed)
        self.assertEqual([], clicks.events)
        self.assertEqual([], sleeps)

    def test_other_locations_still_target_the_board(self):
        """名单外的地标（罪碑坟场）保持原行为：目标在场面。"""
        state = TargetedLocationTests._state()
        adapted = adapt_action(
            TargetedLocationTests._proposed("目标是己方1号位"), state)

        self.assertEqual("minion", adapted.manual_action.target.kind)


if __name__ == "__main__":
    unittest.main()
