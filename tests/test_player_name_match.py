"""玩家昵称校验：换战网账号后忘记改「用户 ID」要能提示出来。

回归背景（issue 反馈）：脚本靠昵称区分敌我，昵称不匹配时整局都会被判成对手
回合而不出牌，日志里却看不出异常。现在读到双方玩家名后会比对一次并醒目提示。

顺带覆盖日志浮窗的「账号」状态行（匹配绿点 / 不匹配红点）。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import log_overlay
import log_state
import FSM_action


def _state(**names):
    return SimpleNamespace(player_names=dict(names), game_generation=1)


class PlayerNameCheckTests(unittest.TestCase):
    def test_match_with_full_name(self):
        info = log_state.player_name_check(
            _state(**{"1": "TestUser#12345", "2": "UNKNOWN HUMAN PLAYER"}),
            my_name="TestUser#12345")

        self.assertTrue(info["matched"])
        self.assertEqual("TestUser#12345", info["config"])

    def test_match_is_case_insensitive_and_allows_bare_name(self):
        info = log_state.player_name_check(
            _state(**{"1": "testuser#12345", "2": "路人甲#1111"}),
            my_name="TestUser")

        self.assertTrue(info["matched"])

    def test_mismatch_when_account_changed(self):
        info = log_state.player_name_check(
            _state(**{"1": "NewAccount#2222", "2": "UNKNOWN HUMAN PLAYER"}),
            my_name="TestUser#12345")

        self.assertFalse(info["matched"])
        self.assertIn("NewAccount#2222", info["players"].values())

    def test_two_real_names_both_unknown_to_config_is_a_mismatch(self):
        info = log_state.player_name_check(
            _state(**{"1": "A#1", "2": "B#2"}), my_name="C#3")

        self.assertFalse(info["matched"])

    def test_only_one_player_seen_is_inconclusive(self):
        """只读到一条玩家名时不做判断（日志成对给出，读到一半信息不足）。"""
        self.assertIsNone(log_state.player_name_check(
            _state(**{"1": "Someone#1"}), my_name="TestUser#12345"))

    def test_only_unknown_placeholder_names_is_inconclusive(self):
        self.assertIsNone(log_state.player_name_check(
            _state(**{"1": "UNKNOWN HUMAN PLAYER", "2": "UNKNOWN HUMAN PLAYER"}),
            my_name="TestUser#12345"))

    def test_empty_config_is_not_treated_as_a_match(self):
        info = log_state.player_name_check(
            _state(**{"1": "Someone#1", "2": "Other#2"}), my_name="")

        self.assertFalse(info["matched"])

    def test_no_player_names_yet(self):
        self.assertIsNone(log_state.player_name_check(
            _state(), my_name="TestUser#12345"))


class FsmWarningTests(unittest.TestCase):
    def setUp(self):
        self._saved = (FSM_action.log_state, FSM_action._name_match_result,
                       FSM_action._name_match_reported)
        FSM_action.log_state = log_state.LogState()
        FSM_action.log_state.player_names = {"1": "NewAccount#2222",
                                             "2": "UNKNOWN HUMAN PLAYER"}
        FSM_action.log_state.game_generation = 7
        FSM_action._name_match_result = None
        FSM_action._name_match_reported = None
        self.warnings = []

    def tearDown(self):
        (FSM_action.log_state, FSM_action._name_match_result,
         FSM_action._name_match_reported) = self._saved

    def _run(self, my_name):
        with (
            patch.object(log_state, "MY_NAME", my_name),
            patch.object(FSM_action, "warn_print",
                         side_effect=self.warnings.append),
        ):
            return FSM_action.check_player_name_match()

    def test_mismatch_warns_once_with_the_real_names(self):
        info = self._run("TestUser#12345")

        self.assertFalse(info["matched"])
        self.assertEqual(1, len(self.warnings))
        self.assertIn("不匹配", self.warnings[0])
        self.assertIn("NewAccount#2222", self.warnings[0])
        self.assertIn("#编号", self.warnings[0])

        # 同一局不再重复刷屏，但界面查询仍能拿到结果
        self._run("TestUser#12345")
        self.assertEqual(1, len(self.warnings))
        self.assertFalse(FSM_action.player_name_state()["matched"])

    def test_match_does_not_warn(self):
        info = self._run("NewAccount#2222")

        self.assertTrue(info["matched"])
        self.assertEqual([], self.warnings)

    def test_state_is_exposed_for_the_ui(self):
        self._run("TestUser#12345")

        state = FSM_action.player_name_state()
        self.assertFalse(state["matched"])
        self.assertEqual("TestUser#12345", state["config"])

    def test_unknown_state_when_nothing_read_yet(self):
        state = FSM_action.player_name_state()
        self.assertIsNone(state["matched"])


class OverlayAccountRowTests(unittest.TestCase):
    """「账号」行：状态 → 文案/颜色/标记，以及说明列的截断。"""

    def test_row_states(self):
        cases = (
            # (info, 期望 value, 期望 marker, 期望颜色/None=不校验, detail 片段)
            (None, "—", log_overlay.MARKER_UNKNOWN, None, None),
            ({"config": "X#1", "players": {}, "matched": None},
             "—", None, None, None),
            ({"config": "X#1", "players": {"1": "X#1"}, "matched": True},
             "匹配", log_overlay.MARKER_ON, log_overlay.GREEN, None),
            ({"config": "Old#1111", "players": {"1": "New#2222"},
              "matched": False},
             "不匹配", log_overlay.MARKER_OFF, log_overlay.DANGER, "New#2222"),
        )
        for info, value, marker, color, detail in cases:
            with self.subTest(value=value):
                row = log_overlay.account_row(info)

                self.assertEqual(value, row["value"])
                if marker is not None:
                    self.assertEqual(marker, row["marker"])
                if color is not None:
                    self.assertEqual(color, row["value_color"])
                if detail is not None:
                    self.assertIn(detail, row["detail"])

    def test_detail_column_is_clipped(self):
        """说明列宽度固定：超长昵称必须截断，短昵称原样显示。"""
        long_row = log_overlay.account_row({
            "config": "Old#1111",
            "players": {"1": "一个非常非常非常长的战网昵称#54321"},
            "matched": False})
        self.assertLessEqual(len(long_row["detail"]), 16)
        self.assertTrue(long_row["detail"].endswith("…"))

        short_row = log_overlay.account_row({"config": "A#1",
                                             "players": {"1": "A#1"},
                                             "matched": True})
        self.assertEqual("A#1", short_row["detail"])


class OverlayLivenessRowTests(unittest.TestCase):
    """存活状态行：每个 status 的文案与颜色。"""

    def test_row_states(self):
        cases = (
            # (info, 期望 value, 期望 marker/None=不校验, 期望颜色/None=不校验,
            #  detail 片段)
            (None, "—", None, None, None),
            ({"status": "disabled"}, "关", log_overlay.MARKER_OFF,
             None, None),
            ({"status": "ok", "log_age": 12.0, "in_game": True},
             "运行中", None, log_overlay.GREEN, "12s"),
            ({"status": "gone", "log_age": None}, "已退出", None,
             log_overlay.DANGER, "停止"),
            ({"status": "warning", "log_age": 130.0, "in_game": True},
             "疑似卡死", None, log_overlay.WARN, None),
            ({"status": "stale", "log_age": 400.0, "in_game": True},
             "无响应", None, log_overlay.DANGER, "400s"),
            ({"status": "idle"}, "未运行", None, None, None),
        )
        for info, value, marker, color, detail in cases:
            with self.subTest(status=(info or {}).get("status")):
                row = log_overlay.hearthstone_row(info)

                self.assertEqual(value, row["value"])
                if marker is not None:
                    self.assertEqual(marker, row["marker"])
                if color is not None:
                    self.assertEqual(color, row["value_color"])
                if detail is not None:
                    self.assertIn(detail, row["detail"])

    def test_alert_lines_are_highlighted(self):
        self.assertTrue(log_overlay._is_alert_line("⚠️ 炉石已退出：Hearthstone.exe 进程消失"))
        self.assertTrue(log_overlay._is_alert_line(
            "[12:00:00 WARN] ⚠️ 用户 ID 与日志玩家名不匹配：……"))
        self.assertFalse(log_overlay._is_alert_line("[推荐] 打出1号位随从"))


if __name__ == "__main__":
    unittest.main()
