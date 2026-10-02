"""残恶梦魇（CATA_161）：「手牌中或战场上的一个随从」这类两可目标的判定。

盒子对目标只给一个号位，而手牌和场面各有一套编号，所以光看「目标是我方3号位」
分不出是第 3 张手牌还是 3 号位随从。真实面板在目标行后面还会打印一行目标卡名
（对运行日志统计：带目标行的推荐比不带的多正好一行），适配层就用那行卡名判定；
名字拿不到或两边同名时退回「只有一边能对上号位」，两边都能对上就报错不点。
"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from manual_controller import ClickExecutor, ManualController
from src.game_state.hand_target import (
    LEGACY_HAND_OR_BOARD_CARD_IDS,
    allows_hand_target,
    hand_or_board_target_card_ids,
    is_friendly_hand_target_card,
    is_hand_or_board_target_card,
)
from src.game_state.recommendation_adapter import (
    RecommendationStateError,
    adapt_action,
)
from src.parser.recommendation_parser import RecommendationParser

HAND_OR_BOARD_CARD = "CATA_161"


def _minion(name, zone_pos, card_id=None):
    return SimpleNamespace(
        card_id=card_id or f"MINION_{zone_pos}", name=name,
        zone_pos=zone_pos, entity_id=f"board-{zone_pos}", cardtype="MINION")


def _hand_card(name, card_id=None, cardtype="MINION"):
    return SimpleNamespace(
        card_id=card_id or f"HAND_{name}", name=name, cardtype=cardtype,
        entity_id=f"hand-{name}")


class HandOrBoardCardDataTests(unittest.TestCase):
    def test_local_metadata_lists_only_the_ambiguous_card(self):
        self.assertEqual({HAND_OR_BOARD_CARD},
                         set(hand_or_board_target_card_ids()))
        self.assertTrue(is_hand_or_board_target_card(HAND_OR_BOARD_CARD))
        # 两可的卡不在「只能选手牌」那一类里，否则号位会被当成手牌位置。
        self.assertFalse(is_friendly_hand_target_card(HAND_OR_BOARD_CARD))

    def test_execution_gate_accepts_both_kinds(self):
        self.assertTrue(allows_hand_target(HAND_OR_BOARD_CARD))
        self.assertTrue(allows_hand_target("CATA_490"))
        self.assertFalse(allows_hand_target("OTHER_MINION"))

    def test_unreadable_card_data_falls_back_to_the_legacy_card(self):
        hand_or_board_target_card_ids.cache_clear()
        self.addCleanup(hand_or_board_target_card_ids.cache_clear)
        with patch(
                "src.game_state.hand_target._metadata_hand_or_board_card_ids",
                side_effect=OSError("cards.json missing")):
            self.assertTrue(is_hand_or_board_target_card(HAND_OR_BOARD_CARD))
            self.assertEqual(LEGACY_HAND_OR_BOARD_CARD_IDS,
                             hand_or_board_target_card_ids())


class TargetNameParsingTests(unittest.TestCase):
    """面板上目标行后面那行卡名要单独取出来（它不在规范化文本里）。"""

    @staticmethod
    def _parse(lines, turn_number=3):
        ocr = SimpleNamespace(
            frame_id="frame-1", normalized_text="\n".join(lines),
            confidence=0.99)
        return RecommendationParser().parse(
            ocr, turn_number=turn_number, log_revision=7)

    def test_name_after_the_target_line_is_captured(self):
        proposed = self._parse([
            "对手记牌器", "打法参考A", "打出1号位随从", "残恶梦魇",
            "目标是己方3号位", "错误产物", "放置于我方1号位"])

        self.assertEqual("错误产物", proposed.target_name)
        self.assertEqual(("board_slot", "friendly", 3),
                         (proposed.target.kind, proposed.target.owner,
                          proposed.target.index))
        # 卡名行不进规范化文本（所以只能单独留一份）。
        self.assertNotIn("错误产物", proposed.normalized_instruction)

    def test_no_name_line_leaves_target_name_empty(self):
        proposed = self._parse([
            "打法参考A", "打出1号位随从", "残恶梦魇", "目标是己方3号位",
            "放置于我方1号位"])

        self.assertIsNone(proposed.target_name)

    def test_hero_target_line_does_not_borrow_the_next_line_for_minions(self):
        # 目标名只在随从牌的目标行后面取；法术/英雄目标不用它。
        proposed = self._parse([
            "打法参考A", "打出1号位法术", "火球术", "目标是敌方1号位随从",
            "军情七处特工"])

        self.assertIsNone(proposed.target_name)


class HandOrBoardAdaptationTests(unittest.TestCase):
    @staticmethod
    def _state(hand_names, board_names):
        hand = [_hand_card(name, card_id=HAND_OR_BOARD_CARD if index == 0
                           else None)
                for index, name in enumerate(hand_names)]
        board = [_minion(name, index + 1)
                 for index, name in enumerate(board_names)]
        return SimpleNamespace(
            game_num_turns_in_play=3,
            is_my_turn=True,
            my_hand_cards=hand,
            my_minions=board,
            my_locations=[],
            my_board_slot_num=len(board),
            oppo_minions=[],
            oppo_locations=[],
            oppo_board_slot_num=0,
            my_hero=SimpleNamespace(entity_id="friendly-hero"),
            oppo_hero=SimpleNamespace(entity_id="enemy-hero"),
        )

    @staticmethod
    def _proposed(target_name=None, target_line="目标是己方3号位",
                  destination="放置于我方1号位"):
        lines = ["打法参考A", "打出1号位随从", "残恶梦魇", target_line]
        if target_name is not None:
            lines.append(target_name)
        if destination is not None:
            lines.append(destination)
        ocr = SimpleNamespace(
            frame_id="frame-1", normalized_text="\n".join(lines),
            confidence=0.99)
        return RecommendationParser().parse(
            ocr, turn_number=3, log_revision=7)

    def test_target_name_in_hand_resolves_to_the_hand_card(self):
        state = self._state(
            ["残恶梦魇", "法力燃烧", "错误产物"], ["甲虫", "军情七处特工"])
        adapted = adapt_action(self._proposed("错误产物"), state)

        self.assertEqual("hand", adapted.manual_action.target.kind)
        self.assertEqual(2, adapted.manual_action.target.index)
        self.assertEqual("hand-错误产物",
                         adapted.manual_action.target.entity_id)

    def test_target_name_on_the_board_resolves_to_the_board_minion(self):
        state = self._state(
            ["残恶梦魇", "法力燃烧", "暗影步"], ["甲虫", "军情七处特工", "错误产物"])
        adapted = adapt_action(self._proposed("错误产物"), state)

        self.assertEqual("minion", adapted.manual_action.target.kind)
        self.assertEqual("board-3", adapted.manual_action.target.entity_id)

    def test_without_a_name_only_the_side_that_fits_wins(self):
        # 手牌只剩 2 张，3号位不可能是手牌 → 场面。
        state = self._state(["残恶梦魇", "法力燃烧"], ["甲虫", "错误产物", "军情七处特工"])
        adapted = adapt_action(self._proposed(None), state)
        self.assertEqual("minion", adapted.manual_action.target.kind)

        # 场面是空的 → 只能是手牌。
        state = self._state(["残恶梦魇", "错误产物", "法力燃烧"], [])
        adapted = adapt_action(self._proposed(None), state)
        self.assertEqual("hand", adapted.manual_action.target.kind)

    def test_hand_card_that_is_not_a_minion_is_not_a_candidate(self):
        state = self._state(
            ["残恶梦魇", "法力燃烧", "错误产物"], ["甲虫", "乙虫", "军情七处特工"])
        state.my_hand_cards[2] = _hand_card("错误产物", cardtype="SPELL")

        adapted = adapt_action(self._proposed(None), state)

        self.assertEqual("minion", adapted.manual_action.target.kind)
        self.assertEqual("board-3", adapted.manual_action.target.entity_id)

    def test_board_slot_holding_a_location_is_not_a_candidate(self):
        # 地标占着 3 号位，残恶梦魇又不能指地标 → 只能指手牌。
        state = self._state(["残恶梦魇", "法力燃烧", "错误产物"], ["甲虫", "乙虫"])
        state.my_locations = [SimpleNamespace(
            card_id="LOCATION_1", name="罪碑坟场", zone_pos=3,
            entity_id="location-1", cardtype="LOCATION")]
        state.my_board_slot_num = 3

        adapted = adapt_action(self._proposed(None), state)

        self.assertEqual("hand", adapted.manual_action.target.kind)

    def test_same_name_on_both_sides_is_refused_instead_of_guessed(self):
        state = self._state(
            ["残恶梦魇", "法力燃烧", "错误产物"], ["甲虫", "军情七处特工", "错误产物"])

        with self.assertRaisesRegex(
                RecommendationStateError, "hand_or_board_target_ambiguous"):
            adapt_action(self._proposed(None), state)

    def test_no_candidate_at_all_is_refused(self):
        state = self._state(["残恶梦魇", "法力燃烧"], ["甲虫"])

        with self.assertRaisesRegex(
                RecommendationStateError, "hand_or_board_target_missing"):
            adapt_action(self._proposed(None), state)

    def test_hero_target_is_never_taken_as_a_hand_card(self):
        state = self._state(["残恶梦魇", "错误产物"], ["甲虫"])
        adapted = adapt_action(
            self._proposed(None, target_line="目标是我方英雄",
                           destination=None), state)

        self.assertEqual("hero", adapted.manual_action.target.kind)


class HandOrBoardExecutionTests(unittest.TestCase):
    class RecordingClickModule:
        def __init__(self):
            self.events = []

        def choose_card(self, hand_index, hand_count):
            self.events.append(("choose_card", hand_index, hand_count))

        def put_minion(self, gap_index, board_count):
            self.events.append(("put_minion", gap_index, board_count))

        def choose_my_minion(self, index, count):
            self.events.append(("choose_my_minion", index, count))

        def cancel_click(self):
            self.events.append(("cancel_click",))

    @staticmethod
    def _state():
        hand = [_hand_card("残恶梦魇", card_id=HAND_OR_BOARD_CARD),
                _hand_card("错误产物"), _hand_card("法力燃烧")]
        return SimpleNamespace(
            game_num_turns_in_play=3, is_my_turn=True, my_hand_cards=hand,
            my_minions=[_minion("军情七处特工", 1), _minion("错误产物", 2),
                        _minion("甲虫", 3)],
            my_locations=[], my_board_slot_num=3,
            oppo_minions=[], oppo_locations=[], oppo_board_slot_num=0,
        )

    def _controller(self, clicks, sleeps):
        return ManualController(
            output_func=lambda _message: None,
            executor=ClickExecutor(
                click_module=clicks, sleep_func=sleeps.append,
                action_context=nullcontext))

    @staticmethod
    def _proposed(target_name):
        ocr = SimpleNamespace(
            frame_id="frame-1",
            normalized_text="\n".join([
                "打法参考A", "打出1号位随从", "残恶梦魇", "目标是己方3号位",
                target_name, "放置于我方1号位"]),
            confidence=0.99)
        return RecommendationParser().parse(
            ocr, turn_number=3, log_revision=7)

    def test_hand_target_clicks_the_hand_fan(self):
        state = self._state()
        adapted = adapt_action(self._proposed("法力燃烧"), state)
        clicks = self.RecordingClickModule()
        sleeps = []

        result = self._controller(clicks, sleeps).execute(
            adapted.manual_action, state)

        self.assertTrue(result.executed, result.message)
        self.assertEqual([
            ("choose_card", 0, 3),
            ("put_minion", 0, 3),
            ("choose_card", 1, 2),
            ("cancel_click",),
        ], clicks.events)
        self.assertEqual([0.9], sleeps)

    def test_board_target_clicks_the_minion_after_placement(self):
        state = self._state()
        adapted = adapt_action(self._proposed("甲虫"), state)
        clicks = self.RecordingClickModule()
        sleeps = []

        result = self._controller(clicks, sleeps).execute(
            adapted.manual_action, state)

        self.assertTrue(result.executed, result.message)
        self.assertEqual([
            ("choose_card", 0, 3),
            ("put_minion", 0, 3),
            ("choose_my_minion", 3, 4),
            ("cancel_click",),
        ], clicks.events)
        # 场面目标是普通目标，等 0.4s 而不是手牌的 0.9s。
        self.assertEqual([0.4], sleeps)

    def test_replaced_hand_target_is_rejected_before_any_click(self):
        state = self._state()
        adapted = adapt_action(self._proposed("法力燃烧"), state)
        state.my_hand_cards[2] = _hand_card("替换手牌")
        clicks = self.RecordingClickModule()
        sleeps = []

        result = self._controller(clicks, sleeps).execute(
            adapted.manual_action, state)

        self.assertFalse(result.executed)
        self.assertEqual([], clicks.events)
        self.assertEqual([], sleeps)


if __name__ == "__main__":
    unittest.main()
