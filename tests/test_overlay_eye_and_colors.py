# -*- coding: utf-8 -*-
"""浮窗三项体验改动：

1. 「账号」行加一个眼睛按钮（👁 / 👁✖）切换是否显示战网昵称，选择会被记住；
2. 按钮缩小、一行两个，「本局结束后停止」单独一行；
3. 合并面板（action_feed 分块流）按 tag 着色，颜色只用浮窗现有调色板里的。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import action_feed
import config
import log_overlay
import web_ui
from tests.helpers.fake_tk import FakeEvent, overlay_harness


class _FakeThread:
    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target = target

    def start(self):
        return None  # 不真的开 Tk 窗口


def _content_frame(ui):
    """浮窗内容区：从「自动化日志」标题往上找到挂在 root 上的那个 Frame。"""
    node = ui.label("自动化日志").parent
    while node.parent is not None and node.parent is not ui.root:
        node = node.parent
    return node


class AccountEyeToggleTests(unittest.TestCase):
    def setUp(self):
        self._visible = log_overlay._ACCOUNT_VISIBLE[0]
        self._on_toggle = log_overlay._ON_TOGGLE_ACCOUNT
        log_overlay._ACCOUNT_VISIBLE[0] = True
        log_overlay._ON_TOGGLE_ACCOUNT = None

    def tearDown(self):
        log_overlay._ACCOUNT_VISIBLE[0] = self._visible
        log_overlay._ON_TOGGLE_ACCOUNT = self._on_toggle

    def test_eye_state_and_colors(self):
        """默认显示账号；同一个眼睛图标，绿=显示、白=隐藏，不加叉号。"""
        self.assertTrue(log_overlay.account_visible())
        self.assertEqual("👁", log_overlay.EYE_ICON)
        self.assertNotIn("✖", log_overlay.EYE_ICON)
        self.assertEqual(log_overlay.GREEN, log_overlay.EYE_COLOR_ON)
        self.assertEqual(log_overlay.TEXT, log_overlay.EYE_COLOR_OFF)
        self.assertNotEqual(log_overlay.EYE_COLOR_ON, log_overlay.EYE_COLOR_OFF)

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
            log_overlay.start(
                account_visible_setting=False, on_toggle_account=lambda _v: None
            )

        self.assertFalse(log_overlay.account_visible())

    def test_hidden_and_visible_account_rows(self):
        """隐藏账号时行内容：不泄露昵称、但保留匹配/不匹配的结论与告警色。"""
        cases = (
            # (info, show_account, 期望 value/marker, detail 里不该出现的)
            (
                {
                    "config": "TestUser#12345",
                    "players": {"1": "TestUser#12345", "2": "Other#2"},
                    "matched": True,
                },
                False,
                ("匹配", log_overlay.MARKER_ON),
                "TestUser",
            ),
            (
                {"config": "Old#1", "players": {"1": "New#2"}, "matched": False},
                False,
                ("不匹配", log_overlay.MARKER_OFF),
                "New#2",
            ),
            ({"config": "", "players": {}, "matched": None}, False, ("—", None), None),
        )
        for info, show_account, (value, marker), leaked in cases:
            with self.subTest(value=value, show_account=show_account):
                row = log_overlay.account_row(info, show_account=show_account)

                self.assertEqual(value, row["value"])
                if marker is not None:
                    self.assertEqual(marker, row["marker"])
                if leaked is not None:
                    self.assertNotIn(leaked, row["detail"])
                    self.assertEqual(log_overlay._ACCOUNT_HIDDEN_TEXT, row["detail"])

        mismatch = log_overlay.account_row(
            {"config": "Old#1", "players": {"1": "New#2"}, "matched": False},
            show_account=False,
        )
        self.assertEqual(log_overlay.DANGER, mismatch["value_color"])

        visible = log_overlay.account_row(
            {
                "config": "TestUser#12345",
                "players": {"1": "TestUser#12345"},
                "matched": True,
            },
            show_account=True,
        )
        self.assertEqual("TestUser#12345", visible["detail"])

    def test_eye_widget_toggles_visibility_on_click(self):
        saved = log_overlay._ACCOUNT_VISIBLE[0]
        toggles = []
        try:
            log_overlay._ACCOUNT_VISIBLE[0] = True
            with overlay_harness(
                _IS_RUNNING=lambda: True,
                _ON_TOGGLE_ACCOUNT=toggles.append,
            ) as ui:
                eye = ui.label(log_overlay.EYE_ICON)
                self.assertTrue(eye.placed)
                # 眼睛按钮钉在「账号」行最右：账号行左边是 ●/名称/数值/说明。
                self.assertEqual("right", eye.parent.pack_calls[-1]["side"])
                self.assertEqual(log_overlay.EYE_COLOR_ON, eye.options["fg"])
                self.assertEqual("break", eye.fire("<Button-1>", FakeEvent(eye)))
                self.assertFalse(log_overlay.account_visible())
                self.assertEqual([False], toggles)
                self.assertEqual(log_overlay.EYE_COLOR_OFF, eye.options["fg"])
        finally:
            log_overlay._ACCOUNT_VISIBLE[0] = saved


class AccountPreferencePersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "ui_config.json"
        self._saved_path = config.CONFIG_PATH
        config.CONFIG_PATH = self.path
        self.path.write_text(
            json.dumps(
                {
                    "name": "TestUser#12345",
                    "log_root": "D:\\Logs",
                    "auto_concede": {"enabled": True, "threshold": 20.0, "rounds": 3},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

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

        config.save_overlay_setting("show_account", True)

        self.assertTrue(config.overlay_settings()["show_account"])

    def test_web_bridge_and_overlay_binding_use_the_preference(self):
        self.assertFalse(web_ui._overlay_account_visible() is None)

        with patch.object(web_ui, "_log"):
            web_ui._overlay_save_account_visible(False)

        self.assertFalse(config.overlay_settings()["show_account"])
        self.assertFalse(web_ui._overlay_account_visible())

        bound = {}
        overlay = type(
            "O",
            (),
            {
                "start": lambda self, **kwargs: bound.update(kwargs),
                "is_running": lambda self: False,
            },
        )()

        with patch.object(web_ui, "log_overlay", overlay):
            web_ui._bind_overlay()

        self.assertIn("account_visible_setting", bound)
        self.assertIn("on_toggle_account", bound)
        self.assertIs(bound["on_toggle_account"], web_ui._overlay_save_account_visible)


class LogColorTests(unittest.TestCase):
    """合并面板（action_feed 分块流）的着色：只用浮窗现有调色板里的颜色。"""

    def test_feed_tag_mapping(self):
        action_feed.set_ocr_provider(None)
        action_feed.clear()
        try:
            cases = (
                # [推荐] 进 OCR 段（白字）；执行/延时/重试进执行段（白字）；
                # WARN/ERROR/⚠️ 用红色加粗；其余次要行灰字。
                ("[推荐] 打出1号位随从", "text"),
                ("[执行] 打出1号位随从", "exec"),
                ("[SYS] 动作 3s 未确认，重新执行。", "exec"),
                ("[12:04:52 WARN] 盒子面板暂不可读，继续重试。", "alert"),
                ("等待对手操作。", "dim"),
            )
            for line, _expected in cases:
                action_feed.push(line)

            tags = [
                tag
                for block in action_feed.snapshot()
                for _text, tag in block["ocr"] + block["exec"]
            ]
        finally:
            action_feed.clear()
        self.assertEqual([expected for _line, expected in cases], tags)

    def test_colors_come_from_the_existing_palette(self):
        palette = {
            log_overlay.GREEN,
            log_overlay.ACCENT,
            log_overlay.GOLD,
            log_overlay.WARN,
            log_overlay.DANGER,
            log_overlay.TEXT,
            log_overlay.DIM,
            log_overlay.OK,
            log_overlay.LINE,
        }
        for tag, color in log_overlay.FEED_TAG_COLORS.items():
            self.assertIn(color, palette, tag)

    def test_log_renderer_applies_the_palette_and_bold_alert(self):
        action_feed.clear()
        try:
            with overlay_harness(_IS_RUNNING=lambda: True) as ui:
                action_feed.push("[12:04:57 ERROR] 自动化线程异常退出")
                refresh = [cb for _delay, cb in ui.root.after_calls if cb][-1]
                refresh()
                text = next(
                    widget for widget in ui.widgets_of("text")
                    if widget.options.get("wrap") == "char")
                expected_tags = (
                    set(log_overlay.FEED_TAG_COLORS)
                    | {"bar_" + name for name in log_overlay.FEED_BAR_COLORS}
                    | {"bar_block_" + name for name in log_overlay.FEED_BAR_COLORS}
                    | {"card_stage"})
                self.assertEqual(expected_tags, set(text.tag_configs))
                for tag, color in log_overlay.FEED_TAG_COLORS.items():
                    self.assertEqual(
                        color, text.tag_configs[tag]["foreground"], tag)
                for name, color in log_overlay.FEED_BAR_COLORS.items():
                    self.assertEqual(
                        color, text.tag_configs["bar_" + name]["foreground"])
                for tag in log_overlay.FEED_BOLD_TAGS:
                    self.assertIn("bold", text.tag_configs[tag]["font"])
                inserts = [op for op in text.operations if op[0] == "insert"]
                self.assertTrue(inserts)
                self.assertTrue(any("card_alert" in str(op[3]) for op in inserts))
        finally:
            action_feed.clear()


class ButtonLayoutTests(unittest.TestCase):
    def test_destructive_buttons_confirm_before_callbacks(self):
        calls = []
        with (
            patch.object(log_overlay, "_confirm_restart", return_value=False) as confirm_restart,
            patch.object(log_overlay, "_confirm_clear_logs", return_value=False) as confirm_clear,
        ):
            with overlay_harness(
                _IS_RUNNING=lambda: True,
                _LOGS=lambda: {"files": 2},
                _ON_RESTART=lambda: calls.append("restart"),
                _ON_CLEAR_LOGS=lambda: calls.append("clear"),
                _ON_EXIT_OVERLAY=lambda: calls.append("exit_overlay"),
                _ON_EXIT=lambda: calls.append("exit"),
            ) as ui:
                restart = ui.button("重启炉石")
                clear = ui.button("清理日志")
                exit_overlay = ui.button("退出浮窗")
                self.assertEqual(log_overlay.WARN, restart.options["bg"])
                self.assertEqual(log_overlay.DANGER, ui.button("退出脚本").options["bg"])
                self.assertEqual(log_overlay.NEUTRAL, exit_overlay.options["bg"])

                restart.invoke()
                clear.invoke()
                self.assertEqual([], calls)

                confirm_restart.return_value = True
                confirm_clear.return_value = True
                restart.invoke()
                clear.invoke()
                self.assertEqual(["restart", "clear"], calls)

                exit_overlay.invoke()
                self.assertTrue(any(delay == 150 for delay, _callback in ui.root.after_calls))

        self.assertEqual(["restart", "clear", "exit_overlay"], calls)

    def test_button_grid_matches_the_layout_contract(self):
        expected = {
            "start": "开始对战", "halt": "中止", "stop_after": "本局结束后停止",
            "save": "保存日志", "calibrate": "校准", "open_logs": "打开日志",
            "clear_logs": "清理日志", "export_feedback": "导出本次出错截屏与日志",
            "restart": "重启炉石", "exit_overlay": "退出浮窗",
            "exit": "退出脚本", "self_check": "装好没反应？",
        }
        glyphs = ("▶", "⏹", "⏸", "💾", "♻", "🚪", "✖", "📊", "⏳", "▁", "▣", "✓", "✗")
        with overlay_harness(_IS_RUNNING=lambda: True) as ui:
            rows = {}
            for key, label in expected.items():
                button = ui.button(label)
                actual = button.grid_calls[-1]
                self.assertEqual(log_overlay.BTN_LAYOUT[key][0], actual["row"], key)
                self.assertEqual(log_overlay.BTN_LAYOUT[key][1], actual["column"], key)
                self.assertEqual(log_overlay.BTN_SPAN.get(key, 1), actual["columnspan"], key)
                rows.setdefault(actual["row"], []).append(key)
                # 按钮标签只用纯文本（拿块状字形当图标会像多了一个下划线）。
                for glyph in glyphs:
                    self.assertNotIn(glyph, label)

            # 一行最多两个按钮（263px 宽）；「本局结束后停止」必须独占一行，
            # 否则紧挨「中止」容易被误点。
            for row, keys in rows.items():
                self.assertLessEqual(len(keys), 2, (row, keys))
            self.assertEqual(["stop_after"], rows[log_overlay.BTN_LAYOUT["stop_after"][0]])

    def test_status_widgets_pack_left_to_right(self):
        with overlay_harness(
            _IS_RUNNING=lambda: True,
            _HUMAN_LIKE=lambda: {}, _CONCEDE_DETECT=lambda: {},
            _LIVENESS=lambda: {}, _ACCOUNT=lambda: {}, _LOGS=lambda: {},
        ) as ui:
            for name in ("活人感", "投降检测", "炉石", "账号", "日志"):
                row = ui.label(name).parent
                labels = [child for child in row.children if child.kind == "label"]
                self.assertEqual(4, len(labels), name)
                marker, value, detail = labels[0], labels[2], labels[3]
                self.assertEqual("●", marker.options["text"])
                self.assertEqual("left", marker.pack_calls[-1]["side"])
                self.assertEqual("left", ui.label(name).pack_calls[-1]["side"])
                self.assertEqual("left", value.pack_calls[-1]["side"])
                self.assertEqual("left", detail.pack_calls[-1]["side"])
                self.assertTrue(detail.pack_calls[-1]["expand"])
                self.assertEqual("left", detail.options["justify"])

    def test_detail_wrap_budget_never_goes_below_the_minimum(self):
        # 263 宽、圆点 25 + 名称 50 + 间距/内边距 22、数值 39 → 127
        self.assertEqual(127, log_overlay.detail_wrap_px(263, 136))
        # 空间被挤光时也不能变成 0（否则换行会碎成一列单字）
        self.assertEqual(
            log_overlay._DETAIL_MIN_PX, log_overlay.detail_wrap_px(263, 400)
        )
        self.assertEqual(log_overlay._DETAIL_MIN_PX, log_overlay.detail_wrap_px(10, 0))

    def test_details_are_fitted_by_real_font_width(self):
        """fit_text：放得下原样返回，放不下按字符截断加省略号。"""
        measure = lambda text: 10 * len(text)  # noqa: E731

        self.assertEqual("连  续", log_overlay.fit_text("连  续", 200, measure))
        self.assertEqual("", log_overlay.fit_text("", 50, measure))
        self.assertEqual("", log_overlay.fit_text(None, 50, measure))
        fitted = log_overlay.fit_text("一二三四五六七八九十", 45, measure)
        self.assertTrue(fitted.endswith("…"))
        self.assertLessEqual(measure(fitted), 45)
        # 截断点上正好落着一个省略号：只能留一个，不能变成「一二……」。
        self.assertEqual("一二…", log_overlay.fit_text("一二…三四五六", 45, measure))

    def test_minimize_event_collapses_and_restores_window(self):
        with overlay_harness(_IS_RUNNING=lambda: True) as ui:
            mini_bar = ui.label(log_overlay.RESTORE_TEXT).parent
            content = _content_frame(ui)
            opened = ui.root.geometries[-1]
            opened_height = int(opened.split("+")[0].split("x")[1])
            opened_origin = opened.split("+", 1)[1]

            self.assertTrue(content.packed)
            self.assertFalse(mini_bar.packed)
            ui.label(log_overlay.MINIMIZE_TEXT).fire("<Button-1>")
            self.assertFalse(content.packed)
            self.assertTrue(mini_bar.packed)
            self.assertEqual("x", mini_bar.pack_calls[-1]["fill"])
            collapsed = ui.root.geometries[-1]
            collapsed_height = int(collapsed.split("+")[0].split("x")[1])
            # 折叠成标题条：只矮不挪（用户拖动过窗口后最小化也不该弹回角落）。
            self.assertLess(collapsed_height, opened_height)
            self.assertEqual(opened_origin, collapsed.split("+", 1)[1])

            ui.label(log_overlay.RESTORE_TEXT).fire("<Button-1>")
            self.assertTrue(content.packed)
            self.assertFalse(mini_bar.packed)
            restored = ui.root.geometries[-1]
            self.assertEqual(opened_height, int(restored.split("+")[0].split("x")[1]))


if __name__ == "__main__":
    unittest.main()
