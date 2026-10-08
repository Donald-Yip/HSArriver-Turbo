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
    hand_target_allowed_cardtypes,
    hand_target_cardtype_mismatch,
    hand_target_rule,
    is_friendly_hand_target_card,
)
from src.game_state.recommendation_adapter import (
    RecommendationStateError,
    adapt_action,
)
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_models import ActionKind, SlotRef

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
# 卡面不写「选择手牌中一张」，但打出去后同样要点一张自己手牌的卡：
#   * 「检视你手牌中的三张牌，选择一张」（维希度斯的窟穴，地标）
#   * 「从你的3张手牌中选择一张」（处理证据）
#   * 「使你手牌中一张牌的法力值消耗减少…」（纯净圣母、塔姆辛、活体园林…）
HAND_TARGET_SPECIFIED_CARDS = (
    "WON_103",           # 维希度斯的窟穴（地标）
    "REV_507",           # 处理证据
    "CORE_REV_507",      # 处理证据（核心）
    "BE_036",            # 纯净圣母
    "BOM_03_Tamsin_02p",  # 塔姆辛（英雄技能）
    "EDR_518",           # 活体园林
    "CAP_101",           # 跟随引线
    "CAP_402",           # 跟随证据
    "CAP_802",           # 跟随幽灵
)


class HandTargetCardDataTests(unittest.TestCase):
    """判定读卡牌描述：只收「目标确实是自己手牌」的卡。"""

    def test_local_metadata_covers_every_hand_target_card(self):
        ids = friendly_hand_target_card_ids()

        self.assertEqual(
            {card_id for card_id, _ in HAND_TARGET_MINION_CARDS}
            | {HAND_TARGET_LOCATION_CARD_ID}
            | set(HAND_TARGET_SPECIFIED_CARDS),
            set(ids))

    def test_specified_hand_card_patterns_are_covered(self):
        """「检视/从 N 张手牌中选一张」「使…你手牌中一张…」也要按手牌目标处理。"""
        for card_id in HAND_TARGET_SPECIFIED_CARDS:
            with self.subTest(card_id=card_id):
                self.assertTrue(is_friendly_hand_target_card(card_id))

    def test_legacy_cards_are_always_kept(self):
        self.assertLessEqual(LEGACY_HAND_TARGET_CARD_IDS,
                             friendly_hand_target_card_ids())

    def test_lookup_is_cached(self):
        self.assertIs(friendly_hand_target_card_ids(),
                      friendly_hand_target_card_ids())

    def test_cards_that_only_move_cards_into_hand_are_not_hand_targets(self):
        # 这些都出现过「手牌」字样，但目标在战场上，或者干脆是随机的/不指定
        # 哪一张：收进来会把场面目标当成手牌点错牌。
        for card_id in (
                "CORE_EX1_049",   # 年轻的酒仙：把随从移回你的手牌
                "GIL_658",        # 碎枝：把复制置入你的手牌
                "BAR_080",        # 暗影猎手沃金：选随从，再和手牌里的交换
                "REV_370",        # 派对捣蛋鬼：选敌方随从，随机投手牌
                "CATA_161",       # 残恶梦魇：手牌中或战场上（两可）
                "BAR_841",        # 重装上阵：随机使手牌中的一张+1/+1
                "AV_206p",        # 女王的祝福：随机
                "JAIL_303",       # 上古预言师：看的是对手的手牌
                "WORK_021",       # 预留泊位：一张随机随从牌（不指定哪张）
                "BOT_423",        # 梦境花栽种师：回合结束时随机
                "RLK_710"):       # 霜牙之剑：英雄攻击后触发（自动挑牌）
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


class HandTargetRuleTests(unittest.TestCase):
    """判定规则：收录谁、排除谁、目标类型要求。"""

    def test_rule_kinds(self):
        cases = (
            ("CATA_490", "hand"),        # 魔眼秘术师：只能选手牌
            ("BE_036", "hand"),          # 纯净圣母：卡面不写「选择」也是手牌
            ("WON_103", "hand"),         # 维希度斯的窟穴（地标）
            ("CATA_161", "hand_or_board"),  # 残恶梦魇：两可
            ("AV_206p", "none"),         # 女王的祝福：随机取一张
            ("WORK_021", "none"),        # 预留泊位：一张随机随从牌
            ("RLK_710", "none"),         # 霜牙之剑：攻击后自动挑
            ("CORE_EX1_049", "none"),    # 年轻的酒仙：手牌只是落点
        )
        for card_id, kind in cases:
            with self.subTest(card_id=card_id):
                self.assertEqual(kind, hand_target_rule(card_id)["kind"])

    def test_cardtype_requirements(self):
        cases = (
            ("CATA_563", {"SPELL"}),     # 选择手牌中的一张法术牌
            ("CATA_477", {"MINION"}),    # 地标：手牌中的一张随从牌
            ("BE_036", set()),           # 只是一张牌：不限制
            ("CATA_490", set()),
        )
        for card_id, expected in cases:
            with self.subTest(card_id=card_id):
                self.assertEqual(
                    expected, set(hand_target_allowed_cardtypes(card_id)))

    def test_mismatch_helper_is_conservative(self):
        spell = SimpleNamespace(cardtype="SPELL")
        minion = SimpleNamespace(cardtype="MINION")
        unknown = SimpleNamespace(cardtype="")

        self.assertTrue(hand_target_cardtype_mismatch("CATA_563", minion))
        self.assertFalse(hand_target_cardtype_mismatch("CATA_563", spell))
        self.assertFalse(hand_target_cardtype_mismatch("CATA_490", minion))
        self.assertFalse(hand_target_cardtype_mismatch("CATA_563", unknown))


class HandTargetTypeMismatchTests(unittest.TestCase):
    """盒子号位指到类型不符的牌时：适配层拒绝，而不是点错牌。"""

    @staticmethod
    def _state(source_card_id, *cardtypes):
        hand = []
        for index, cardtype in enumerate(cardtypes):
            card_id = source_card_id if index == 0 else f"HAND_{index}"
            hand.append(SimpleNamespace(
                card_id=card_id, cardtype=cardtype,
                entity_id=f"hand-{index}", name=f"手牌{index}"))
        return SimpleNamespace(
            game_num_turns_in_play=3, my_hand_cards=hand,
            my_minions=[], my_locations=[], oppo_minions=[],
            general_choice_cards={}, discover_choice_count=None)

    @staticmethod
    def _proposed(state, target):
        """把当前手牌的第一张开出去（它的 card_id 决定手牌目标判定），目标是 target。"""
        return SimpleNamespace(
            action=ActionKind.PLAY_CARD,
            source=SlotRef("hand_slot", "friendly", 1),
            destination=None, target=target, target_name=None,
            card_type="SPELL", turn_number=3, choice_name=None)

    def _adapt(self, state, target):
        from src.game_state.recommendation_adapter import adapt_action
        return adapt_action(self._proposed(state, target), state)

    def test_matching_type_is_accepted(self):
        state = self._state("CATA_563", "SPELL", "SPELL")

        adapted = self._adapt(state, SlotRef("board_slot", "friendly", 2))

        self.assertEqual("hand", adapted.manual_action.target.kind)
        self.assertEqual(1, adapted.manual_action.target.index)

    def test_wrong_type_is_refused(self):
        state = self._state("CATA_563", "SPELL", "MINION")

        with self.assertRaisesRegex(RecommendationStateError,
                                    "hand_target_cardtype_mismatch"):
            self._adapt(state, SlotRef("board_slot", "friendly", 2))

    def test_unconstrained_card_accepts_any_type(self):
        # 魔眼秘术师（CATA_490）是「选择一张牌」，不限制类型
        state = self._state("CATA_490", "MINION", "MINION")

        adapted = self._adapt(state, SlotRef("board_slot", "friendly", 2))

        self.assertEqual("hand", adapted.manual_action.target.kind)
        self.assertEqual(1, adapted.manual_action.target.index)


class FollowUpHandTargetLogTests(unittest.TestCase):
    """收尾日志要写清「选手中第 N 张（卡名）」，便于和日志复盘对照。"""

    def test_executor_renumbers_after_playing_the_card(self):
        clicks = RecordingClickModule()
        executor = self._executor(clicks)

        # 打出去的是 1 号位（selected_index=0），面板给的号位是 3 → 牌面上第 2 张
        result = self._run_play(executor, target_index=3, selected_index=0)

        self.assertIn(("choose_card", 1, 4), clicks.events)
        self.assertIn("选手中第 3 号位（选中卡）", result.message)

    def test_source_after_target_keeps_the_index(self):
        clicks = RecordingClickModule()
        executor = self._executor(clicks)

        # 打出去的是 4 号位（selected_index=3），目标号位 3 在它前面 → 位置不变
        self._run_play(executor, target_index=3, selected_index=4)

        self.assertIn(("choose_card", 2, 4), clicks.events)

    @staticmethod
    def _executor(clicks):
        controller_click = SimpleNamespace(
            choose_card=clicks.choose_card, cancel_click=clicks.cancel_click,
            choose_my_board_entity=clicks.choose_my_board_entity,
            put_minion=clicks.put_minion)
        return ClickExecutor(click_module=controller_click,
                             sleep_func=lambda _seconds: None)

    @staticmethod
    def _run_play(executor, target_index, selected_index):
        hand = [
            SimpleNamespace(card_id=("CATA_490" if index == selected_index
                                     else f"HAND_{index}"),
                            cardtype="MINION", entity_id=f"hand-{index}",
                            name=("魔眼秘术师" if index == selected_index
                                  else "选中卡"), zone_pos=index + 1)
            for index in range(5)
        ]
        state = SimpleNamespace(
            my_hand_cards=hand, my_minions=[], my_locations=[],
            oppo_minions=[], oppo_locations=[],
            my_board_slot_num=0, oppo_board_slot_num=0,
            game_num_turns_in_play=3, is_my_turn=True)
        target_slot = target_index - 1
        action = PlayCardAction(
            selected_index, hand[selected_index].card_id, "MINION",
            gap_index=0,
            # Target.index 是 0 基（适配层把盒子的 1 基号位减 1 后放进来的）
            target=Target("friendly", "hand", target_slot,
                          hand[target_slot].entity_id))
        controller = ManualController(
            output_func=lambda _message: None,
            executor=executor)
        result = controller.execute(
            controller.bind_to_turn(action, state), state)

        assert result.executed, result.message
        return result


class RecordingClickModule:
    def __init__(self):
        self.events = []

    def choose_card(self, hand_index, hand_count):
        self.events.append(("choose_card", hand_index, hand_count))

    def put_minion(self, gap_index, board_count):
        self.events.append(("put_minion", gap_index, board_count))

    def choose_my_board_entity(self, entity_index, entity_num):
        self.events.append(("choose_my_board_entity", entity_index, entity_num))

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
