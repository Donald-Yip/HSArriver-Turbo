# -*- coding: utf-8 -*-
"""TAG_CHANGE 里的「非数字 Entity」：只有玩家名能映射到玩家实体。

酒馆战棋的日志会直接写随从/英雄名（"Entity=暗影之鱼人"、"调酒师鲍勃"）：
这些实体没有 id，旧逻辑一律当玩家名 → 每条 tag 都 warn 一次（启动重放当前
Power.log 时满屏 "Invalid entity_id: 0"），还会把 tag 写到玩家实体上。
"""

import unittest
from unittest.mock import patch

import log_state as log_state_module
from constants.constants import LOG_LINE_TAG_CHANGE
from log_op import LineInfoContainer, parse_line
from log_state import LogState, PlayerEntity, update_state


def _tag_change(entity, tag, value):
    return LineInfoContainer(
        LOG_LINE_TAG_CHANGE, entity=entity, tag=tag, value=value)


class EntityNameTagChangeTests(unittest.TestCase):
    def setUp(self):
        self.state = LogState()
        self.state.add_player_entity("2", "1", PlayerEntity())
        self.state.add_player_entity("3", "2", PlayerEntity())
        self.state.my_player_id = "1"
        self.state.oppo_player_id = "2"

    def test_battlegrounds_minion_names_are_skipped_silently(self):
        with patch.object(log_state_module, "warn_print") as warn:
            ok = update_state(self.state, _tag_change(
                "暗影之鱼人", "BACON_OLD_GOD_ATTACK", "0"))
            update_state(self.state, _tag_change(
                "调酒师鲍勃", "CURRENT_PLAYER", "1"))

        self.assertFalse(ok)
        warn.assert_not_called()
        self.assertNotIn("BACON_OLD_GOD_ATTACK",
                         self.state.oppo_entity.tag_dict)
        self.assertNotIn("CURRENT_PLAYER", self.state.oppo_entity.tag_dict)

    def test_real_battlegrounds_log_line_is_ignored(self):
        parsed = parse_line(
            "D 22:21:29.0154372 GameState.DebugPrintPower() -     "
            "TAG_CHANGE Entity=暗影之鱼人 tag=BACON_OLD_GOD_ATTACK value=0 ")
        self.assertIsNotNone(parsed)

        with patch.object(log_state_module, "warn_print") as warn:
            ok = update_state(self.state, parsed)

        self.assertFalse(ok)
        warn.assert_not_called()

    def test_player_names_still_map_to_player_entities(self):
        with patch.object(log_state_module, "MY_NAME", "Priscilla#51346"):
            update_state(self.state, _tag_change(
                "Priscilla#51346", "CURRENT_PLAYER", "1"))
            update_state(self.state, _tag_change(
                "UNKNOWN HUMAN PLAYER", "PLAYSTATE", "PLAYING"))
            update_state(self.state, _tag_change("Bob#1234", "TURN", "7"))

        self.assertEqual("1",
                         self.state.my_entity.query_tag("CURRENT_PLAYER"))
        self.assertEqual("PLAYING",
                         self.state.oppo_entity.query_tag("PLAYSTATE"))
        self.assertEqual("7", self.state.oppo_entity.query_tag("TURN"))

    def test_player_entity_ids_above_three_are_accepted(self):
        """战棋 8 个玩家：玩家实体 id 会大于 3，旧断言会直接抛 AssertionError。"""
        state = LogState()
        state.add_player_entity("4", "1", PlayerEntity())
        state.add_player_entity("5", "2", PlayerEntity())
        state.my_player_id = "1"
        state.oppo_player_id = "2"

        with patch.object(log_state_module, "MY_NAME", "Priscilla#51346"):
            update_state(state, _tag_change(
                "Priscilla#51346", "MULLIGAN_STATE", "INPUT"))

        self.assertEqual("INPUT",
                         state.my_entity.query_tag("MULLIGAN_STATE"))

    def test_unknown_numeric_entity_warns_once_per_game(self):
        with patch.object(log_state_module, "warn_print") as warn:
            update_state(self.state, _tag_change("999", "ZONE", "HAND"))
            update_state(self.state, _tag_change("999", "ZONE", "PLAY"))
            update_state(self.state, _tag_change("998", "ZONE", "PLAY"))

        invalid = [call for call in warn.call_args_list
                   if "Invalid entity_id" in call.args[0]]
        self.assertEqual(2, len(invalid))  # 999 一次 + 998 一次

    def test_warning_bookkeeping_resets_each_game(self):
        with patch.object(log_state_module, "warn_print"):
            update_state(self.state, _tag_change("999", "ZONE", "HAND"))
        self.assertIn("999", self.state._invalid_entity_warned)

        self.state.flush()

        self.assertEqual(set(), self.state._invalid_entity_warned)


if __name__ == "__main__":
    unittest.main()
