# -*- coding: utf-8 -*-
"""浮窗三项体验改动：

1. 「账号」行加一个眼睛按钮（👁 / 👁✖）切换是否显示战网昵称，选择会被记住；
2. 按钮缩小、一行两个，「本局结束后停止」单独一行；
3. 正文日志按标签着色，颜色只用浮窗现有调色板里的。
"""

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import log_overlay
import web_ui


class _FakeThread:
    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target = target

    def start(self):
        return None       # 不真的开 Tk 窗口


class AccountEyeToggleTests(unittest.TestCase):
    def setUp(self):
        self._visible = log_overlay._ACCOUNT_VISIBLE[0]
        self._on_toggle = log_overlay._ON_TOGGLE_ACCOUNT
        log_overlay._ACCOUNT_VISIBLE[0] = True
        log_overlay._ON_TOGGLE_ACCOUNT = None

    def tearDown(self):
        log_overlay._ACCOUNT_VISIBLE[0] = self._visible
        log_overlay._ON_TOGGLE_ACCOUNT = self._on_toggle

    def test_default_is_visible(self):
        self.assertTrue(log_overlay.account_visible())
        self.assertEqual("👁", log_overlay.EYE_ICON)

    def test_eye_colors_encode_the_state(self):
        """同一个眼睛图标，绿色=显示账号，白色=隐藏账号。"""
        self.assertEqual(log_overlay.GREEN, log_overlay.EYE_COLOR_ON)
        self.assertEqual(log_overlay.TEXT, log_overlay.EYE_COLOR_OFF)
        self.assertNotEqual(log_overlay.EYE_COLOR_ON, log_overlay.EYE_COLOR_OFF)

    def test_eye_has_no_cross_mark(self):
        self.assertNotIn("✖", log_overlay.EYE_ICON)

    def test_toggle_flips_and_notifies_the_saver(self):
        saved = []
        log_overlay._ON_TOGGLE_ACCOUNT = saved.append

        self.assertFalse(log_overlay.toggle_account_visibility())
        self.assertFalse(log_overlay.account_visible())

        self.assertTrue(log_overlay.toggle_account_visibility())
        self.assertTrue(log_overlay.account_visible())
        self.assertEqual([False, True], saved)

    def test_save_failure_still_toggles(self):
        def _boom(_value):
            raise RuntimeError("磁盘只读")

        log_overlay._ON_TOGGLE_ACCOUNT = _boom

        self.assertFalse(log_overlay.toggle_account_visibility())
        self.assertFalse(log_overlay.account_visible())

    def test_start_seeds_the_saved_state(self):
        with (
            patch.object(log_overlay, "_run"),
            patch.object(log_overlay.threading, "Thread", _FakeThread),
        ):
            log_overlay._STARTED[0] = False
            log_overlay.start(account_visible_setting=False,
                              on_toggle_account=lambda _v: None)

        self.assertFalse(log_overlay.account_visible())

    def test_hidden_and_visible_account_rows(self):
        """隐藏账号时行内容：不泄露昵称、但保留匹配/不匹配的结论与告警色。"""
        cases = (
            # (info, show_account, 期望 value/color/marker, detail 里不该出现的)
            ({"config": "TestUser#12345",
              "players": {"1": "TestUser#12345", "2": "Other#2"},
              "matched": True}, False,
             ("匹配", log_overlay.MARKER_ON), "TestUser"),
            ({"config": "Old#1", "players": {"1": "New#2"}, "matched": False},
             False, ("不匹配", log_overlay.MARKER_OFF), "New#2"),
            ({"config": "", "players": {}, "matched": None}, False,
             ("—", None), None),
        )
        for info, show_account, (value, marker), leaked in cases:
            with self.subTest(value=value, show_account=show_account):
                row = log_overlay.account_row(info, show_account=show_account)

                self.assertEqual(value, row["value"])
                if marker is not None:
                    self.assertEqual(marker, row["marker"])
                if leaked is not None:
                    self.assertNotIn(leaked, row["detail"])
                    self.assertEqual(log_overlay._ACCOUNT_HIDDEN_TEXT,
                                     row["detail"])

        self.assertEqual(
            log_overlay.DANGER,
            log_overlay.account_row(
                {"config": "Old#1", "players": {"1": "New#2"},
                 "matched": False},
                show_account=False)["value_color"])

        visible = log_overlay.account_row(
            {"config": "TestUser#12345",
             "players": {"1": "TestUser#12345"}, "matched": True},
            show_account=True)
        self.assertEqual("TestUser#12345", visible["detail"])

    def test_eye_is_rendered_and_clickable(self):
        source = inspect.getsource(log_overlay._run)
        self.assertIn("toggle_account_visibility()", source)
        self.assertIn('bind("<Button-1>"', source)
        # 眼睛放在「账号」行最右侧（pack side="right" 的第一个就是最右）
        self.assertIn("with_eye=True", source)
        self.assertIn('eye_box.pack(side="right"', source)


class AccountPreferencePersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "ui_config.json"
        self._saved_path = config.CONFIG_PATH
        config.CONFIG_PATH = self.path
        self.path.write_text(json.dumps(
            {"name": "TestUser#12345", "log_root": "D:\\Logs",
             "auto_concede": {"enabled": True, "threshold": 20.0, "rounds": 3}},
            ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        config.CONFIG_PATH = self._saved_path
        self._tmp.cleanup()

    def test_default_shows_the_account(self):
        self.assertTrue(config.overlay_settings()["show_account"])

    def test_save_round_trip_keeps_other_settings(self):
        config.save_overlay_setting("show_account", False)

        self.assertFalse(config.overlay_settings()["show_account"])
        written = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertFalse(written["overlay"]["show_account"])
        self.assertEqual("TestUser#12345", written["name"])
        self.assertEqual("D:\\Logs", written["log_root"])
        self.assertTrue(written["auto_concede"]["enabled"])

        with self.assertRaises(KeyError):
            config.save_overlay_setting("show_name", True)

    def test_broken_config_file_is_rebuilt(self):
        self.path.write_text("{ 这不是 JSON", encoding="utf-8")

        config.save_overlay_setting("show_account", False)

        self.assertFalse(config.overlay_settings()["show_account"])

    def test_web_bridge_and_overlay_binding_use_the_preference(self):
        self.assertFalse(web_ui._overlay_account_visible() is None)

        with patch.object(web_ui, "_log"):
            web_ui._overlay_save_account_visible(False)

        self.assertFalse(config.overlay_settings()["show_account"])
        self.assertFalse(web_ui._overlay_account_visible())

        bound = {}
        overlay = type("O", (), {
            "start": lambda self, **kwargs: bound.update(kwargs),
            "is_running": lambda self: False})()

        with patch.object(web_ui, "log_overlay", overlay):
            web_ui._bind_overlay()

        self.assertIn("account_visible_setting", bound)
        self.assertIn("on_toggle_account", bound)
        self.assertIs(bound["on_toggle_account"],
                      web_ui._overlay_save_account_visible)


class LogColorTests(unittest.TestCase):
    def test_colors_come_from_the_existing_palette(self):
        palette = {log_overlay.GREEN, log_overlay.ACCENT, log_overlay.GOLD,
                   log_overlay.WARN, log_overlay.DANGER, log_overlay.TEXT,
                   log_overlay.DIM, log_overlay.OK}
        for tag, color in log_overlay.LOG_TAG_COLORS.items():
            self.assertIn(color, palette, tag)

    def test_distinct_tags_get_distinct_colors(self):
        # 每类标签颜色都不同，才看得出区别（告警/报错共用红色是刻意的）。
        colors = list(log_overlay.LOG_TAG_COLORS.values())
        self.assertEqual(len(set(colors)), len(colors))

    def test_tag_mapping(self):
        cases = {
            "[推荐] 打出1号位随从": "reco",
            "[执行] 开始执行操作": "exec",
            "[12:04:45 SYS] 自动投降检测：AI胜率 15.4%": "sys",
            "[12:04:31 SYS] 回合 5 延时结束，开始本轮推荐读取。": "turn",
            "[12:04:33 INFO] 你赢得了这场对战": "dim",
            "[12:04:52 WARN] 盒子面板暂不可读，继续重试。": "warn",
            "等待对手操作。": "dim",
        }
        for line, expected in cases.items():
            self.assertEqual(expected, log_overlay.log_line_tag(line), line)

    def test_error_and_alerts_are_the_highlighted_tag(self):
        """ERROR 与 ⚠️ 都算“必须马上看见”，用红色加粗。"""
        for line in ("[12:04:57 ERROR] 自动化线程异常退出",
                     "[12:04:57 ERROR] ⚠️ 炉石已退出：Hearthstone.exe 进程消失",
                     "[12:04:58 WARN] ⚠️ 用户 ID 与日志玩家名不匹配……"):
            self.assertEqual("alert", log_overlay.log_line_tag(line), line)

    def test_renderer_uses_the_tag_function_and_configures_every_tag(self):
        source = inspect.getsource(log_overlay._run)
        self.assertIn("log_line_tag(ln)", source)
        self.assertIn("for tag_name, color in LOG_TAG_COLORS.items()", source)
        for tag in log_overlay.LOG_TAG_COLORS:
            self.assertIn(tag, source.replace("tag_name", tag))
        self.assertNotIn('"act"', source)


class ButtonLayoutTests(unittest.TestCase):
    def test_two_buttons_per_row_and_stop_after_alone(self):
        rows = {}
        for key, (row, column) in log_overlay.BTN_LAYOUT.items():
            rows.setdefault(row, []).append(column)

        # 5 行：开始/中止、本局结束后停止、校准/保存日志、重启炉石/退出浮窗、
        # 退出脚本（横跨整行）。
        self.assertEqual({0: [0, 1], 1: [0], 2: [0, 1], 3: [0, 1], 4: [0]}, rows)
        for columns in rows.values():
            self.assertLessEqual(len(columns), 2)

    def test_calibrate_button_sits_next_to_save(self):
        self.assertEqual(log_overlay.BTN_LAYOUT["save"], (2, 1))
        self.assertEqual(log_overlay.BTN_LAYOUT["calibrate"], (2, 0))
        self.assertNotIn("calibrate", log_overlay.BTN_SPAN)
        self.assertEqual(log_overlay.BTN_LAYOUT["restart"], (3, 0))
        self.assertEqual(log_overlay.BTN_LAYOUT["exit_overlay"], (3, 1))
        self.assertNotIn("restart", log_overlay.BTN_SPAN)
        self.assertNotIn("exit_overlay", log_overlay.BTN_SPAN)
        slots = list(log_overlay.BTN_LAYOUT.values())
        self.assertEqual(len(slots), len(set(slots)))

    def test_destructive_buttons_guard_themselves(self):
        """退出浮窗 ≠ 退出脚本；重启炉石必须先确认，取消时不调回调。"""
        self.assertNotEqual(log_overlay.BTN_LAYOUT["exit_overlay"],
                            log_overlay.BTN_LAYOUT["exit"])
        source = inspect.getsource(log_overlay._run)
        self.assertIn('_make_btn(btn_frame, "✖  退出浮窗", NEUTRAL', source)
        self.assertIn('_make_btn(btn_frame, "🚪  退出脚本", DANGER', source)
        self.assertIn('_make_btn(btn_frame, "♻  重启炉石", WARN', source)

        body = source.split("def _call_exit_overlay", 1)[1]
        body = body.split("exit_overlay_btn = ", 1)[0]
        self.assertIn("_ON_EXIT_OVERLAY", body)
        self.assertIn("stop", body)
        self.assertNotIn("_ON_EXIT(", body)

        restart = source.split("def _call_restart", 1)[1]
        restart = restart.split("restart_btn = ", 1)[0]
        self.assertIn("_confirm_restart(root)", restart)
        self.assertIn("_ON_RESTART", restart)
        self.assertIn("已取消重启炉石", restart)

        confirm = inspect.getsource(log_overlay._confirm_restart)
        self.assertIn('attributes("-topmost", True)', confirm)
        self.assertIn("Hearthstone.exe", confirm)
        self.assertIn("Log.config", confirm)

    def test_spanning_rows(self):
        self.assertEqual(2, log_overlay.BTN_SPAN["exit"])
        row, _column = log_overlay.BTN_LAYOUT["exit"]
        rows = [r for key, (r, _c) in log_overlay.BTN_LAYOUT.items()
                if key != "exit"]
        self.assertEqual(max(rows) + 1, row)

        self.assertEqual(2, log_overlay.BTN_SPAN["stop_after"])
        row, _column = log_overlay.BTN_LAYOUT["stop_after"]
        self.assertNotIn(row, [r for key, (r, _c) in log_overlay.BTN_LAYOUT.items()
                               if key != "stop_after"])

    def test_window_metrics_and_grid_helpers(self):
        # 旧版是 5 行、字号 10、pady 5；现在必须更小，否则省不出日志高度。
        self.assertLessEqual(log_overlay.BTN_FONT_SIZE, 9)
        self.assertLessEqual(log_overlay.BTN_PADY, 4)
        # 宽度 263 → 300：状态行右侧说明列必须放得下（用户反馈显示不全）。
        self.assertEqual(300, log_overlay.WINDOW_WIDTH)
        # 654 - 17：删掉「炉石传说 · 自动对战」副标题那一行。
        self.assertEqual(637, log_overlay.WINDOW_HEIGHT)

        source = inspect.getsource(log_overlay._run)
        self.assertIn("def _place(btn, key)", source)
        for key in log_overlay.BTN_LAYOUT:
            self.assertIn(f'_place({key}_btn, "{key}")', source)

    def test_status_rows_wrap_instead_of_being_clipped(self):
        """说明列按 wraplength 换行：绝不再被窗口右边缘切掉。"""
        self.assertGreater(log_overlay._DETAIL_WRAP_PX, 100)
        self.assertLess(log_overlay._DETAIL_WRAP_PX_EYE,
                        log_overlay._DETAIL_WRAP_PX)
        self.assertGreaterEqual(log_overlay._DETAIL_MAX_LINES, 2)
        source = inspect.getsource(log_overlay._run)
        self.assertIn("wraplength=wrap_px", source)
        self.assertIn("_DETAIL_WRAP_PX_EYE", source)

    def test_overlay_can_be_minimized_to_a_title_bar(self):
        """最小化 = 折叠成标题条（浮窗是 TOOLWINDOW，iconify 之后无法还原）。"""
        self.assertLess(log_overlay.MINIMIZED_HEIGHT,
                        log_overlay.WINDOW_HEIGHT)
        self.assertEqual(
            f"{log_overlay.WINDOW_WIDTH}x{log_overlay.MINIMIZED_HEIGHT}+8+9",
            log_overlay.minimized_geometry(log_overlay.WINDOW_WIDTH,
                                           log_overlay.WINDOW_HEIGHT,
                                           8, 9, collapsed=True))
        self.assertEqual(
            f"{log_overlay.WINDOW_WIDTH}x{log_overlay.WINDOW_HEIGHT}+8+9",
            log_overlay.minimized_geometry(log_overlay.WINDOW_WIDTH,
                                           log_overlay.WINDOW_HEIGHT,
                                           8, 9, collapsed=False))
        source = inspect.getsource(log_overlay._run)
        self.assertIn("def _set_collapsed(flag)", source)
        self.assertIn("MINIMIZE_TEXT", source)
        self.assertIn("RESTORE_TEXT", source)
        self.assertIn("content.pack_forget()", source)
        self.assertIn("mini_bar.pack(fill=\"x\")", source)

    def test_details_are_fitted_by_real_font_width(self):
        """fit_text：放得下原样返回，放不下按字符截断加省略号。"""
        measure = lambda text: 10 * len(text)          # noqa: E731

        self.assertEqual("连  续", log_overlay.fit_text("连  续", 200, measure))
        self.assertEqual("", log_overlay.fit_text("", 50, measure))
        self.assertEqual("", log_overlay.fit_text(None, 50, measure))
        fitted = log_overlay.fit_text("一二三四五六七八九十", 45, measure)
        self.assertTrue(fitted.endswith("…"))
        self.assertLessEqual(measure(fitted), 45)
        # 截断点上正好落着一个省略号：只能留一个，不能变成「一二……」。
        self.assertEqual("一二…",
                         log_overlay.fit_text("一二…三四五六", 45, measure))


if __name__ == "__main__":
    unittest.main()
