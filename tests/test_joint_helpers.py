# -*- coding: utf-8 -*-
"""R18 联合模式纯函数：本地/盒子分数比较与打法一致性判定。

* ``search.search.joint_prefers_local``：本地是否显著更优（默认高 20%）；
* ``search.search.joint_margin``：本地相对盒子的超出幅度；
* ``search.serialize.joint_actions_consistent`` / ``joint_outcome_label``：
  两套打法是否一致（忽略顺序/结束回合/单侧字段）与归属标签。
"""

import math
import unittest

from search.search import joint_margin, joint_prefers_local
from search.serialize import joint_actions_consistent, joint_outcome_label


def slot(kind, owner=None, index=None):
    return {"kind": kind, "owner": owner, "index": index}


def action(kind="play_card", card_id=None, entity_id=None,
           choice_card_id=None, source=None, target=None):
    """按 search/serialize.serialize_action 的输出形状造动作 dict。"""
    return {
        "action": kind,
        "instruction": "",
        "card_id": card_id,
        "entity_id": entity_id,
        "choice_card_id": choice_card_id,
        "source": source,
        "target": target,
    }


def play_card(card_id="TIME_025", index=6, owner="friendly"):
    return action(card_id=card_id,
                  source=slot("hand", owner=owner, index=index))


class JointPrefersLocalTests(unittest.TestCase):
    """本地要比盒子高 20% 才接管；边界值按 >= 判定。"""

    def test_positive_scores_need_twenty_percent(self):
        self.assertTrue(joint_prefers_local(100, 80))
        self.assertFalse(joint_prefers_local(100, 90))

    def test_boundary_is_inclusive(self):
        # 80 + 0.2 * 80 = 96，本地正好 96 也算显著更优。
        self.assertTrue(joint_prefers_local(96, 80))
        self.assertFalse(joint_prefers_local(95, 80))

    def test_negative_scores_use_absolute_margin(self):
        # 负分不能按乘法比较（会翻转方向），按 local >= box + 0.2*|box|。
        self.assertTrue(joint_prefers_local(-10, -20))   # -10 >= -16
        self.assertFalse(joint_prefers_local(-18, -20))  # -18 < -16
        self.assertTrue(joint_prefers_local(-16, -20))   # 边界 -16

    def test_zero_box_score_requires_strictly_better(self):
        self.assertFalse(joint_prefers_local(0, 0))
        self.assertTrue(joint_prefers_local(1, 0))
        self.assertFalse(joint_prefers_local(-1, 0))

    def test_custom_margin(self):
        self.assertTrue(joint_prefers_local(110, 100, margin=0.1))
        self.assertFalse(joint_prefers_local(109, 100, margin=0.1))
        self.assertTrue(joint_prefers_local(120, 100, margin=0.2))

    def test_nan_and_invalid_scores_do_not_select_local(self):
        self.assertFalse(joint_prefers_local(float("nan"), 10))
        self.assertFalse(joint_prefers_local(10, float("nan")))
        self.assertFalse(joint_prefers_local(None, 10))
        self.assertFalse(joint_prefers_local("bad", 10))


class JointMarginTests(unittest.TestCase):
    def test_margin_is_relative_excess(self):
        self.assertAlmostEqual(0.25, joint_margin(100, 80))
        self.assertAlmostEqual(0.0, joint_margin(80, 80))
        self.assertAlmostEqual(-0.2, joint_margin(64, 80))

    def test_zero_box_score(self):
        self.assertEqual(1.0, joint_margin(1, 0))
        self.assertEqual(0.0, joint_margin(0, 0))
        self.assertEqual(0.0, joint_margin(-1, 0))

    def test_negative_box_uses_absolute_base(self):
        # (-10 - (-20)) / 20 = 0.5
        self.assertAlmostEqual(0.5, joint_margin(-10, -20))
        # (-30 - (-20)) / 20 = -0.5
        self.assertAlmostEqual(-0.5, joint_margin(-30, -20))

    def test_invalid_scores_return_zero(self):
        self.assertEqual(0.0, joint_margin(None, 10))
        self.assertEqual(0.0, joint_margin(10, "bad"))


class JointActionsConsistentTests(unittest.TestCase):
    """打法一致性：只看两边都写明的字段，顺序与结束回合不影响。"""

    def test_same_actions_in_different_order_are_consistent(self):
        attack = action("attack",
                        source=slot("board_slot", owner="friendly", index=1),
                        target=slot("board_slot", owner="enemy", index=1))
        local = [play_card(), attack]
        box = [attack, play_card()]

        self.assertTrue(joint_actions_consistent(local, box))

    def test_extra_end_turn_on_either_side_is_ignored(self):
        local = [play_card(), action("end_turn")]
        box = [play_card()]

        self.assertTrue(joint_actions_consistent(local, box))
        self.assertTrue(joint_actions_consistent(box, local))
        self.assertTrue(joint_actions_consistent(
            [play_card(), action("end_turn")],
            [action("end_turn"), play_card()]))

    def test_different_slot_index_is_inconsistent(self):
        local = [play_card(index=6)]
        box = [play_card(index=5)]

        self.assertFalse(joint_actions_consistent(local, box))

    def test_fields_written_on_one_side_only_are_not_differences(self):
        # 盒子面板常写实体 id/落位，本地搜索不写；单侧字段不能算差异。
        local = [action(card_id="TIME_025",
                        source=slot("hand", owner="friendly", index=6))]
        box = [action(card_id="TIME_025", entity_id="67",
                      source=slot("hand"))]

        self.assertTrue(joint_actions_consistent(local, box))
        self.assertTrue(joint_actions_consistent(box, local))

    def test_slot_kind_aliases_are_the_same_slot(self):
        local = [action(source=slot("hand_slot", owner="friendly", index=6))]
        box = [action(source=slot("hand", owner="friendly", index=6))]

        self.assertTrue(joint_actions_consistent(local, box))
        self.assertTrue(joint_actions_consistent(
            [action(source=slot("board", index=2))],
            [action(source=slot("board_slot", index=2))]))
        self.assertTrue(joint_actions_consistent(
            [action("choose_discover", source=slot("discover_slot", index=1))],
            [action("choose_discover", source=slot("discover", index=1))]))

    def test_real_kind_mismatch_is_inconsistent(self):
        self.assertFalse(joint_actions_consistent(
            [action(source=slot("hand", index=6))],
            [action(source=slot("board_slot", index=6))]))

    def test_card_id_mismatch_when_both_sides_write_it(self):
        self.assertFalse(joint_actions_consistent(
            [play_card(card_id="TIME_025")],
            [play_card(card_id="CORE_UNG_205")]))
        # 只有一侧写 card_id 时不算差异。
        self.assertTrue(joint_actions_consistent(
            [play_card(card_id="TIME_025")],
            [action(source=slot("hand", index=6))]))

    def test_entity_or_choice_mismatch_when_both_sides_write_it(self):
        self.assertFalse(joint_actions_consistent(
            [play_card() | {"entity_id": "67"}],
            [play_card() | {"entity_id": "68"}]))
        self.assertFalse(joint_actions_consistent(
            [action("choose_discover", choice_card_id="A")],
            [action("choose_discover", choice_card_id="B")]))
        self.assertTrue(joint_actions_consistent(
            [action("choose_discover", choice_card_id="A")],
            [action("choose_discover")]))

    def test_different_action_kind_is_inconsistent(self):
        self.assertFalse(joint_actions_consistent(
            [play_card()],
            [action("attack")]))

    def test_different_sequence_length_is_inconsistent(self):
        attack = action("attack")
        self.assertFalse(joint_actions_consistent(
            [play_card(), attack],
            [play_card()]))
        self.assertFalse(joint_actions_consistent([play_card()], []))

    def test_empty_sequences_are_consistent(self):
        self.assertTrue(joint_actions_consistent([], []))
        self.assertTrue(joint_actions_consistent([action("end_turn")],
                                                 [action("end_turn")]))
        self.assertTrue(joint_actions_consistent(None, None))


class JointOutcomeLabelTests(unittest.TestCase):
    def test_labels_follow_the_twenty_percent_rule(self):
        self.assertEqual("local", joint_outcome_label(100, 80))
        self.assertEqual("box", joint_outcome_label(80, 100))
        self.assertEqual("same", joint_outcome_label(100, 90))
        self.assertEqual("same", joint_outcome_label(0, 0))
        self.assertEqual("local", joint_outcome_label(1, 0))

    def test_negative_and_boundary_scores(self):
        self.assertEqual("local", joint_outcome_label(-10, -20))
        self.assertEqual("box", joint_outcome_label(-20, -10))
        self.assertEqual("local", joint_outcome_label(96, 80))
        self.assertEqual("same", joint_outcome_label(95, 80))

    def test_nan_is_not_attributed_to_either_side(self):
        self.assertEqual("same", joint_outcome_label(float("nan"), 10))
        self.assertEqual("same", joint_outcome_label(10, float("nan")))
        self.assertTrue(math.isfinite(joint_margin(10, 10)))


if __name__ == "__main__":
    unittest.main()
