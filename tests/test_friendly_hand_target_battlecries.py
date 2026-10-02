"""手牌目标战吼/地标：名单来自 cards.json，不再只有魔眼秘术师和雷鸣流云。

「选择你手牌中的一张牌」这类卡的目标在盒子里同样写成「目标是我方N号位」，
但点击的是手牌扇形区（还要按打完这张牌之后的手牌数量算位置），所以名单必须
完整——漏一张就是点错目标或直接报错。
"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from manual_controller import (
    ClickExecutor,
    ManualController,
    PlayCardAction,
    Target,
)
from src.game_state.hand_target import (
    LEGACY_HAND_TARGET_CARD_IDS,
    friendly_hand_target_card_ids,
    is_friendly_hand_target_card,
)
from src.game_state.recommendation_adapter import (
    RecommendationStateError,
    adapt_action,
)
from src.parser.recommendation_parser import RecommendationParser

# cards.json 里「选择你手牌中的一张…」的随从（当前共 14 张），
# 守护巨龙之厅是同类地标，走另一条路径，见 test_targeted_locations.py。
HAND_TARGET_MINION_CARDS = (
    ("CATA_490", "魔眼秘术师"),
    ("CATA_563", "雷鸣流云"),
    ("AV_308", "墓地污染者"),
    ("CATA_200", "古神的眼线"),
    ("CATA_209", "战场轰炸手"),
    ("CATA_566", "托维尔雕琢师"),
    ("CATA_697", "恶念变异体"),
    ("CATA_721", "避难的幸存者"),
    ("CATA_897", "宝石囤积者"),
    ("CATA_979", "咒术专家"),
    ("CATA_EVENT_001", "毁焚火凤"),
    ("CORE_REV_511", "案卷书虫"),
    ("JAIL_313", "偷贩炼金师"),
    ("REV_511", "案卷书虫"),
)
HAND_TARGET_LOCATION_CARD_ID = "CATA_477"


class HandTargetCardDataTests(unittest.TestCase):
    """判定读卡牌描述：只收「目标确实是自己手牌」的卡。"""

    def test_local_metadata_covers_every_hand_target_card(self):
        ids = friendly_hand_target_card_ids()

        self.assertEqual(
            {card_id for card_id, _ in HAND_TARGET_MINION_CARDS}
            | {HAND_TARGET_LOCATION_CARD_ID},
            set(ids))

    def test_legacy_cards_are_always_kept(self):
        self.assertLessEqual(LEGACY_HAND_TARGET_CARD_IDS,
                             friendly_hand_target_card_ids())

    def test_lookup_is_cached(self):
        self.assertIs(friendly_hand_target_card_ids(),
                      friendly_hand_target_card_ids())

    def test_cards_that_only_move_cards_into_hand_are_not_hand_targets(self):
        # 这些都出现过「手牌」字样，但目标在战场上，或者干脆是随机的：
        # 收进来会把场面目标当成手牌点错牌。
        for card_id in (
                "CORE_EX1_049",   # 年轻的酒仙：把随从移回你的手牌
                "GIL_658",        # 碎枝：把复制置入你的手牌
                "BAR_080",        # 暗影猎手沃金：选随从，再和手牌里的交换
                "REV_370",        # 派对捣蛋鬼：选敌方随从，随机投手牌
                "CATA_161",       # 残恶梦魇：手牌中或战场上（两可）
                "BAR_841",        # 重装上阵：随机使手牌中的一张+1/+1
                "AV_206p",        # 女王的祝福：随机
                "JAIL_303"):      # 上古预言师：看的是对手的手牌
            with self.subTest(card_id=card_id):
                self.assertNotIn(card_id, friendly_hand_target_card_ids())

    def test_unreadable_card_data_falls_back_to_the_legacy_cards(self):
        """cards.json 读不出来时不能把原来能用的两张卡一起拖垮。"""
        friendly_hand_target_card_ids.cache_clear()
        self.addCleanup(friendly_hand_target_card_ids.cache_clear)
        with patch(
                "src.game_state.hand_target._metadata_hand_target_card_ids",
                side_effect=OSError("cards.json missing")):
            self.assertTrue(is_friendly_hand_target_card("CATA_490"))
            self.assertTrue(is_friendly_hand_target_card("CATA_563"))
            self.assertFalse(is_friendly_hand_target_card("CATA_200"))


class RecordingClickModule:
    def __init__(self):
        self.events = []

    def choose_card(self, hand_index, hand_count):
        self.events.append(("choose_card", hand_index, hand_count))

    def put_minion(self, gap_index, board_count):
        self.events.append(("put_minion", gap_index, board_count))

    def cancel_click(self):
        self.events.append(("cancel_click",))


class FriendlyHandTargetBattlecryTests(unittest.TestCase):
    @staticmethod
    def _proposed(source_slot, target_slot):
        ocr = SimpleNamespace(
            frame_id="frame-1",
            normalized_text=(
                "打法参考A\n"
                f"打出{source_slot}号位随从\n"
                f"目标是我方{target_slot}号位"
            ),
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

    def test_supported_minions_use_friendly_hand_target_flow(self):
        for card_id, card_name in HAND_TARGET_MINION_CARDS:
            with self.subTest(card_id=card_id):
                cards = [
                    SimpleNamespace(
                        card_id="SPELL_1", cardtype="SPELL",
                        entity_id="entity-1", name="法术一"),
                    SimpleNamespace(
                        card_id=card_id, cardtype="MINION",
                        entity_id="entity-2", name=card_name),
                    SimpleNamespace(
                        card_id="SPELL_2", cardtype="SPELL",
                        entity_id="entity-3", name="法术二"),
                    SimpleNamespace(
                        card_id="SPELL_3", cardtype="SPELL",
                        entity_id="entity-4", name="法术三"),
                ]
                state = SimpleNamespace(
                    game_num_turns_in_play=3,
                    is_my_turn=True,
                    my_hand_cards=cards,
                    my_minions=[],
                    my_locations=[],
                    my_board_slot_num=0,
                    oppo_minions=[],
                    oppo_board_slot_num=0,
                )
                proposed = self._proposed(source_slot=2, target_slot=4)
                try:
                    adapted = adapt_action(proposed, state)
                except RecommendationStateError as exc:
                    self.fail(
                        f"{card_id} should support a friendly hand target: "
                        f"{exc}")
                clicks = RecordingClickModule()
                sleeps = []

                result = self._controller(clicks, sleeps).execute(
                    adapted.manual_action, state)

                self.assertTrue(result.executed, result.message)
                self.assertEqual([
                    ("choose_card", 1, 4),
                    ("put_minion", 0, 0),
                    ("choose_card", 2, 3),
                    ("cancel_click",),
                ], clicks.events)
                self.assertEqual([0.9], sleeps)

    def test_cata_563_rejects_out_of_range_hand_target(self):
        state = SimpleNamespace(
            game_num_turns_in_play=3,
            my_hand_cards=[
                SimpleNamespace(
                    card_id="CATA_563", cardtype="MINION",
                    entity_id="entity-1"),
                SimpleNamespace(
                    card_id="SPELL_1", cardtype="SPELL",
                    entity_id="entity-2"),
            ],
            my_minions=[],
            my_locations=[],
        )

        with self.assertRaisesRegex(
                RecommendationStateError, "hand_target_out_of_range"):
            adapt_action(
                self._proposed(source_slot=1, target_slot=99), state)

    def test_cata_563_rejects_its_own_hand_slot_as_target(self):
        state = SimpleNamespace(
            game_num_turns_in_play=3,
            my_hand_cards=[
                SimpleNamespace(
                    card_id="SPELL_1", cardtype="SPELL",
                    entity_id="entity-1"),
                SimpleNamespace(
                    card_id="CATA_563", cardtype="MINION",
                    entity_id="entity-2"),
            ],
            my_minions=[],
            my_locations=[],
        )

        with self.assertRaisesRegex(
                RecommendationStateError, "hand_target_is_source"):
            adapt_action(
                self._proposed(source_slot=2, target_slot=2), state)

    def test_cata_563_rejects_replaced_hand_target_before_clicking(self):
        cards = [
            SimpleNamespace(
                card_id="CATA_563", cardtype="MINION",
                entity_id="entity-1", name="雷鸣流云"),
            SimpleNamespace(
                card_id="SPELL_1", cardtype="SPELL",
                entity_id="entity-2", name="法术一"),
        ]
        state = SimpleNamespace(
            game_num_turns_in_play=3,
            is_my_turn=True,
            my_hand_cards=cards,
            my_minions=[],
            my_locations=[],
            my_board_slot_num=0,
            oppo_minions=[],
            oppo_board_slot_num=0,
        )
        adapted = adapt_action(
            self._proposed(source_slot=1, target_slot=2), state)
        state.my_hand_cards[1] = SimpleNamespace(
            card_id="SPELL_2", cardtype="SPELL",
            entity_id="replacement", name="替换法术")
        clicks = RecordingClickModule()
        sleeps = []
        controller = self._controller(clicks, sleeps)

        result = controller.execute(adapted.manual_action, state)

        self.assertFalse(result.executed)
        self.assertEqual([], clicks.events)
        self.assertEqual([], sleeps)

    def test_cata_563_executor_rejects_source_card_as_hand_target(self):
        source = SimpleNamespace(
            card_id="CATA_563", cardtype="MINION",
            entity_id="entity-1", name="雷鸣流云")
        state = SimpleNamespace(
            game_num_turns_in_play=3,
            is_my_turn=True,
            my_hand_cards=[source],
            my_minions=[],
            my_locations=[],
            my_board_slot_num=0,
            oppo_minions=[],
            oppo_board_slot_num=0,
        )
        action = PlayCardAction(
            hand_index=0,
            card_id="CATA_563",
            cardtype="MINION",
            gap_index=0,
            target=Target("friendly", "hand", 0, "entity-1"),
            hand_entity_id="entity-1",
        )
        clicks = RecordingClickModule()
        sleeps = []
        controller = self._controller(clicks, sleeps)

        result = controller.execute(action, state)

        self.assertFalse(result.executed)
        self.assertEqual([], clicks.events)
        self.assertEqual([], sleeps)

    def test_target_before_source_keeps_its_hand_index(self):
        state = SimpleNamespace(
            game_num_turns_in_play=3,
            is_my_turn=True,
            my_hand_cards=[
                SimpleNamespace(
                    card_id="SPELL_1", cardtype="SPELL",
                    entity_id="entity-1", name="法术一"),
                SimpleNamespace(
                    card_id="SPELL_2", cardtype="SPELL",
                    entity_id="entity-2", name="法术二"),
                SimpleNamespace(
                    card_id="CATA_563", cardtype="MINION",
                    entity_id="entity-3", name="雷鸣流云"),
            ],
            my_minions=[],
            my_locations=[],
            my_board_slot_num=0,
            oppo_minions=[],
            oppo_board_slot_num=0,
        )
        adapted = adapt_action(
            self._proposed(source_slot=3, target_slot=1), state)
        clicks = RecordingClickModule()
        sleeps = []

        result = self._controller(clicks, sleeps).execute(
            adapted.manual_action, state)

        self.assertTrue(result.executed, result.message)
        self.assertEqual([
            ("choose_card", 2, 3),
            ("put_minion", 0, 0),
            ("choose_card", 0, 2),
            ("cancel_click",),
        ], clicks.events)

    def test_other_minions_cannot_use_friendly_hand_targets(self):
        source = SimpleNamespace(
            card_id="OTHER_MINION", cardtype="MINION",
            entity_id="entity-1", name="普通随从")
        target = SimpleNamespace(
            card_id="SPELL_1", cardtype="SPELL",
            entity_id="entity-2", name="法术一")
        state = SimpleNamespace(
            game_num_turns_in_play=3,
            is_my_turn=True,
            my_hand_cards=[source, target],
            my_minions=[],
            my_locations=[],
            my_board_slot_num=0,
            oppo_minions=[],
            oppo_board_slot_num=0,
        )
        action = PlayCardAction(
            hand_index=0,
            card_id="OTHER_MINION",
            cardtype="MINION",
            gap_index=0,
            target=Target("friendly", "hand", 1, "entity-2"),
            hand_entity_id="entity-1",
        )
        clicks = RecordingClickModule()
        sleeps = []

        result = self._controller(clicks, sleeps).execute(action, state)

        self.assertFalse(result.executed)
        self.assertEqual([], clicks.events)
        self.assertEqual([], sleeps)


if __name__ == "__main__":
    unittest.main()
