# -*- coding: utf-8 -*-
"""回归：自动化不再每局自动点卡组（用户 2026-10-23 口径）。

`battle_entry.deck_index` 原本用于远程遥控，但自动化每局点「开始」前都会按它
点第 N 格，覆盖玩家手选的卡组。现在：
  * `ChoosingHeroAction` 不再调用 `select_deck_if_configured`；
  * 持久设置只保留 `deck --clear` 清理旧值；
  * 选卡组改用显式命令 deck-click / enter-battle --deck。
"""

import unittest
from unittest.mock import patch

import FSM_action
import web_ui


class ChoosingHeroNoAutoDeckTests(unittest.TestCase):
    def setUp(self):
        self._saved = (
            FSM_action.stop_after_current_game,
            FSM_action.quitting_flag,
            FSM_action.choose_hero_count,
            FSM_action._keep_score_count,
        )
        FSM_action.stop_after_current_game = False
        FSM_action.quitting_flag = False
        FSM_action.choose_hero_count = 0
        FSM_action._keep_score_count = 0

    def tearDown(self):
        (FSM_action.stop_after_current_game,
         FSM_action.quitting_flag,
         FSM_action.choose_hero_count,
         FSM_action._keep_score_count) = self._saved

    def test_choosing_hero_does_not_auto_select_a_deck(self):
        actions = []

        with (
            patch.object(FSM_action, "print_out"),
            patch.object(FSM_action.time, "sleep"),
            patch.object(FSM_action, "_read_rank_target", return_value=""),
            patch.object(
                FSM_action.click,
                "run_hearthstone_action",
                side_effect=lambda action: actions.append(action),
            ),
            patch("battle_entry.select_deck_if_configured") as picker,
        ):
            result = FSM_action.ChoosingHeroAction()

        self.assertEqual(FSM_action.FSM_MATCHING, result)
        picker.assert_not_called()
        self.assertEqual([FSM_action.click.match_opponent], actions)


class DeckSettingApiTests(unittest.TestCase):
    def test_saving_an_index_is_refused_with_guidance(self):
        with (
            patch.object(web_ui, "_log"),
            patch("config.save_battle_entry_setting") as save,
        ):
            result = web_ui.api_save_deck({"index": 8})

        self.assertFalse(result["ok"])
        self.assertIn("deck-click", result["error"])
        save.assert_not_called()

    def test_clear_still_removes_the_legacy_value(self):
        with (
            patch.object(web_ui, "_log"),
            patch(
                "config.save_battle_entry_setting",
                return_value={"deck_index": None},
            ) as save,
        ):
            result = web_ui.api_save_deck({"clear": True})

        self.assertTrue(result["ok"])
        self.assertIsNone(result["deck_index"])
        save.assert_called_once_with("deck_index", None)


if __name__ == "__main__":
    unittest.main()
