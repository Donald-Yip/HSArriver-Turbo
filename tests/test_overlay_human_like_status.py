"""浮窗显示活人感开关状态 + 活人感延时驱动底部进度条。"""

import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import log_overlay
from tests.helpers.fake_tk import overlay_harness


class _FakeThread:
    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target = target
        self._args = args

    def start(self):
        return None       # 不真的开 Tk 窗口


class BrandHeaderTests(unittest.TestCase):
    """浮窗顶部品牌行：只留本项目大名（副标题按用户要求删掉了）。"""

    def test_rendered_brand_header_has_no_subtitle_widget(self):
        self.assertFalse(hasattr(log_overlay, "BRAND_SUB"))
        with overlay_harness(_IS_RUNNING=lambda: True) as ui:
            rendered = [str(w.options.get("text", "")) for w in ui.widgets_of("label")]
            self.assertIn(log_overlay.BRAND_NAME, rendered)
            self.assertFalse(any(text.startswith("炉石传说") for text in rendered))


class HumanLikeLabelTests(unittest.TestCase):
    def test_row_states(self):
        with self.subTest(state="unknown"):
            row = log_overlay.human_like_row(None)
            self.assertEqual("—", row["value"])
            self.assertEqual(log_overlay.MARKER_UNKNOWN, row["marker"])
            self.assertEqual("", row["detail"])

        with self.subTest(state="disabled"):
            row = log_overlay.human_like_row({"enabled": False})
            self.assertEqual("关", row["value"])
            self.assertEqual(log_overlay.MARKER_OFF, row["marker"])   # 关 = 红点

        with self.subTest(state="enabled with params"):
            row = log_overlay.human_like_row({
                "enabled": True, "post_delay_min": 0.5, "post_delay_max": 3.0,
                "hover_min": 0.2, "hover_max": 1.0})
            self.assertEqual("开", row["value"])
            self.assertEqual(log_overlay.GREEN, row["value_color"])
            self.assertEqual(log_overlay.MARKER_ON, row["marker"])    # 开 = 绿点
            self.assertIn("0.5~3s", row["detail"])
            self.assertIn("悬停 0.2~1s", row["detail"])

        with self.subTest(state="enabled without params"):
            row = log_overlay.human_like_row({"enabled": True})
            self.assertEqual("开", row["value"])
            self.assertEqual("", row["detail"])

        with self.subTest(state="hand hover off"):
            row = log_overlay.human_like_row({
                "enabled": True, "hand_hover_enabled": False,
                "minion_hover_enabled": True,
                "post_delay_min": 0.5, "post_delay_max": 3.0,
                "hover_min": 0.2, "hover_max": 1.0,
                "minion_hover_min": 0.2, "minion_hover_max": 1.5})

            self.assertEqual("开", row["value"])
            self.assertNotIn("0.5~3s", row["detail"])     # 没有随机延时了
            self.assertIn("看随从 0.2~1.5s", row["detail"])

        with self.subTest(state="both hovers off"):
            row = log_overlay.human_like_row({
                "enabled": True, "hand_hover_enabled": False,
                "minion_hover_enabled": False,
                "post_delay_min": 0.5, "post_delay_max": 3.0,
                "hover_min": 0.2, "hover_max": 1.0})

            self.assertEqual("开", row["value"])
            self.assertEqual("两个悬停都已关", row["detail"])

        with self.subTest(state="minion hover off keeps hand detail"):
            row = log_overlay.human_like_row({
                "enabled": True, "hand_hover_enabled": True,
                "minion_hover_enabled": False,
                "post_delay_min": 0.5, "post_delay_max": 3.0,
                "hover_min": 0.2, "hover_max": 1.0})

            self.assertIn("0.5~3s", row["detail"])
            self.assertIn("悬停 0.2~1s", row["detail"])

        with self.subTest(state="overlay callback reports params"):
            import web_ui

            with patch.object(web_ui, "_current_human_like", return_value={
                    "enabled": True, "post_delay_min": 0.5,
                    "post_delay_max": 3.0, "hover_min": 0.2, "hover_max": 1.0}):
                info = web_ui._overlay_human_like()

            row = log_overlay.human_like_row(info)
            self.assertEqual("开", row["value"])
            self.assertIn("0.5~3s", row["detail"])
            self.assertIn("悬停 0.2~1s", row["detail"])

        with self.subTest(state="overlay callback reports disabled"):
            import web_ui

            with patch.object(web_ui, "_current_human_like", return_value={
                    "enabled": False, "post_delay_min": 0.5,
                    "post_delay_max": 3.0, "hover_min": 0.2, "hover_max": 1.0}):
                info = web_ui._overlay_human_like()

            row = log_overlay.human_like_row(info)
            self.assertEqual("关", row["value"])


class ConcedeDetectLabelTests(unittest.TestCase):
    def test_row_states(self):
        with self.subTest(state="unknown"):
            row = log_overlay.concede_detect_row(None)
            self.assertEqual("—", row["value"])
            self.assertEqual(log_overlay.MARKER_UNKNOWN, row["marker"])

        with self.subTest(state="disabled"):
            row = log_overlay.concede_detect_row({"enabled": False})
            self.assertEqual("关", row["value"])
            self.assertEqual(log_overlay.MARKER_OFF, row["marker"])   # 关 = 红点

        with self.subTest(state="not read this turn"):
            row = log_overlay.concede_detect_row({
                "enabled": True, "threshold": 20.0, "rounds": 3, "rate": None,
                "streak": 0, "checked_turn": 4, "triggered": False})
            self.assertEqual("未读到", row["value"])
            self.assertIn("第 4 回合", row["detail"])
            self.assertIn("连续 0/3", row["detail"])
            self.assertEqual(log_overlay.MARKER_ON, row["marker"])    # 开着 = 绿点
            self.assertEqual(log_overlay.WARN, row["value_color"])

        with self.subTest(state="below threshold"):
            row = log_overlay.concede_detect_row({
                "enabled": True, "threshold": 20.0, "rounds": 3, "rate": 15.4,
                "streak": 2, "checked_turn": 5, "triggered": False})
            self.assertEqual("15%", row["value"])
            self.assertIn("低于阈值 20%", row["detail"])
            self.assertIn("连续 2/3", row["detail"])
            self.assertEqual(log_overlay.WARN, row["value_color"])

        with self.subTest(state="above threshold"):
            row = log_overlay.concede_detect_row({
                "enabled": True, "threshold": 20.0, "rounds": 3, "rate": 64.0,
                "streak": 0, "checked_turn": 5, "triggered": False})
            self.assertEqual("64%", row["value"])
            self.assertIn("阈值 20%", row["detail"])
            self.assertNotIn("低于阈值", row["detail"])
            self.assertEqual(log_overlay.TEXT, row["value_color"])
            self.assertEqual(log_overlay.MARKER_ON, row["marker"])

        with self.subTest(state="triggered"):
            row = log_overlay.concede_detect_row({
                "enabled": True, "threshold": 20.0, "rounds": 3, "rate": 9.0,
                "streak": 3, "checked_turn": 6, "triggered": True})
            self.assertEqual("已触发认输", row["value"])
            self.assertEqual(log_overlay.DANGER, row["value_color"])

        with self.subTest(state="overlay callback returns state"):
            import web_ui

            with patch.object(web_ui, "_concede_detect_state",
                              return_value={"enabled": True, "threshold": 20.0,
                                            "rounds": 3, "rate": 10.0,
                                            "streak": 1, "checked_turn": 2,
                                            "triggered": False}):
                row = log_overlay.concede_detect_row(
                    web_ui._overlay_concede_detect())

            self.assertEqual("10%", row["value"])
            self.assertIn("连续 1/3", row["detail"])


class StartWiringTests(unittest.TestCase):
    def setUp(self):
        self._started = log_overlay._STARTED[0]
        self._human_like = log_overlay._HUMAN_LIKE
        self._concede = log_overlay._CONCEDE_DETECT

    def tearDown(self):
        log_overlay._STARTED[0] = self._started
        log_overlay._HUMAN_LIKE = self._human_like
        log_overlay._CONCEDE_DETECT = self._concede

    def test_start_accepts_status_callbacks(self):
        params = inspect.signature(log_overlay.start).parameters
        human_like_callback = lambda: {"enabled": True}    # noqa: E731
        concede_callback = lambda: {"enabled": False}      # noqa: E731

        for keyword, callback, attr in (
            ("human_like_callback", human_like_callback, "_HUMAN_LIKE"),
            ("concede_callback", concede_callback, "_CONCEDE_DETECT"),
        ):
            with self.subTest(callback=keyword):
                self.assertIn(keyword, params)

                with (
                    patch.object(log_overlay, "_run"),
                    patch.object(log_overlay.threading, "Thread", _FakeThread),
                ):
                    log_overlay._STARTED[0] = False
                    log_overlay.start(**{keyword: callback})

                self.assertIs(callback, getattr(log_overlay, attr))


class DelayBarTests(unittest.TestCase):
    """活人感延时行必须能驱动浮窗底部进度条，并在结束时清空。"""

    def setUp(self):
        self._started = log_overlay._STARTED[0]
        self._delay = log_overlay._DELAY
        self._lines = list(log_overlay._LINES)
        log_overlay._STARTED[0] = True
        log_overlay._LINES.clear()

    def tearDown(self):
        log_overlay._STARTED[0] = self._started
        log_overlay._DELAY = self._delay
        log_overlay._LINES.clear()
        log_overlay._LINES.extend(self._lines)

    def test_delay_line_drives_and_clears_the_bar(self):
        log_overlay.push("[SYS] 活人感 延时 2.4s 后（手牌区随机悬停等待）", "SYS")

        self.assertIsNotNone(log_overlay._DELAY)
        self.assertAlmostEqual(2.4, log_overlay._DELAY["total"], places=3)
        self.assertIn("活人感", log_overlay._DELAY["desc"])
        self.assertIn("悬停", log_overlay._DELAY["desc"])

        log_overlay.push("[SYS] 延时结束", "SYS")

        self.assertIsNone(log_overlay._DELAY)

    def test_delay_line_text_log_threshold(self):
        long_line = "[SYS] 活人感 延时 3.0s 后（手牌区随机悬停等待）"
        short_line = "[SYS] 活人感 延时 0.5s 后（手牌区随机悬停等待）"

        with self.subTest(case="long delay line also stays in text log"):
            log_overlay.push(long_line, "SYS")

            self.assertIsNotNone(log_overlay._DELAY)
            self.assertIn(long_line,
                          [line for line, _turn in log_overlay._LINES])

        with self.subTest(case="short delay line only drives the bar"):
            log_overlay.push(short_line, "SYS")

            self.assertIsNotNone(log_overlay._DELAY)
            self.assertNotIn(short_line,
                             [text for text, _turn in log_overlay._LINES])


class WebCallbackTests(unittest.TestCase):
    def test_concede_state_matrix(self):
        import web_ui

        live = {"enabled": True, "threshold": 20.0, "rounds": 3, "rate": 15.0,
                "streak": 2, "checked_turn": 5, "triggered": False}

        with self.subTest(case="prefers live fsm values"):
            fsm = SimpleNamespace(concede_detection_state=lambda: dict(live))

            with patch.object(web_ui.CTRL, "fsm", fsm):
                state = web_ui._concede_detect_state()

            self.assertEqual(live, state)

        with self.subTest(case="falls back to config"):
            with (
                patch.object(web_ui.CTRL, "fsm", None),
                patch.object(web_ui, "load_config", return_value={
                    "auto_concede": {"enabled": True, "threshold": 25.0,
                                     "rounds": 4}}),
            ):
                state = web_ui._concede_detect_state()

            self.assertTrue(state["enabled"])
            self.assertEqual(25.0, state["threshold"])
            self.assertEqual(4, state["rounds"])
            self.assertIsNone(state["rate"])

        with self.subTest(case="status snapshot exposes detection state"):
            snapshot_state = {"enabled": True, "threshold": 20.0, "rounds": 3,
                              "rate": 33.0, "streak": 0, "checked_turn": 7,
                              "triggered": False}
            fsm = SimpleNamespace(
                concede_detection_state=lambda: dict(snapshot_state),
                stop_after_current_game=False, FSM_state="Battling",
                game_count=1, win_count=1, concede_count=0)

            with patch.object(web_ui.CTRL, "fsm", fsm):
                snapshot = web_ui.status_snapshot()

            self.assertEqual(snapshot_state, snapshot["concede_detect"])


if __name__ == "__main__":
    unittest.main()
