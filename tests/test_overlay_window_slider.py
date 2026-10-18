# -*- coding: utf-8 -*-
"""浮窗「炉石窗口宽度」滑条：回调接线 + 构建代码存在 + web_ui 传参。

不真的开 Tk 窗口：只从源码/签名断言接线，和现有的浮窗接线测试同一套路。
"""

from __future__ import annotations

import inspect
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import log_overlay
import web_ui


class SliderWiringTests(unittest.TestCase):
    def test_start_accepts_the_window_callbacks(self):
        params = inspect.signature(log_overlay.start).parameters
        for name in ("window_width_callback", "on_resize_window"):
            self.assertIn(name, params)

    def test_run_builds_a_custom_canvas_slider(self):
        source = inspect.getsource(log_overlay._run)
        # 自绘 Canvas 滑条（不用 tk.Scale：方钮丑 + 按下会连浮窗一起拖）。
        self.assertIn("tk.Canvas(", source)
        self.assertNotIn("tk.Scale(", source)
        self.assertIn("SIZE_ROW_TITLE", source)
        self.assertIn("_ON_RESIZE_WINDOW", source)
        # 滑条在 widget 级吃掉事件（return "break"），别冒泡到 root 的拖窗 handler。
        self.assertIn('return "break"', source)
        self.assertIn('slider_canvas.bind("<B1-Motion>"', source)
        self.assertIn('slider_canvas.bind("<ButtonRelease-1>"', source)

    def test_root_drag_ignores_events_from_the_slider(self):
        source = inspect.getsource(log_overlay._run)
        self.assertIn("def _from_slider(event)", source)
        self.assertIn("_from_slider(event)", source)
        self.assertIn('_drag["active"]', source)

    def test_slider_range_constants_exist_and_are_sane(self):
        self.assertLess(log_overlay.SIZE_SLIDER_MIN, log_overlay.SIZE_SLIDER_MAX)
        self.assertGreater(log_overlay.SIZE_SLIDER_STEP, 1)

    def test_min_is_half_of_the_reference(self):
        # 滑条下限 = 960（960×540 = 基准的一半）。
        self.assertEqual(960, log_overlay.SIZE_SLIDER_MIN)

    def test_height_grew_to_fit_the_slider_row(self):
        # 723 是加滑条行之前的设计值；加了必须变高。
        self.assertGreater(log_overlay.WINDOW_HEIGHT, 723)


class BindOverlayTests(unittest.TestCase):
    def test_bind_overlay_passes_the_window_callbacks(self):
        bound = {}
        overlay = SimpleNamespace(
            start=lambda **kwargs: bound.update(kwargs), is_running=lambda: False
        )
        with (
            mock.patch.object(web_ui, "log_overlay", overlay),
            mock.patch.object(web_ui, "_verify_overlay_opened_async"),
        ):
            web_ui._bind_overlay()

        self.assertIn("window_width_callback", bound)
        self.assertIn("on_resize_window", bound)
        self.assertIs(bound["on_resize_window"], web_ui._overlay_resize_window)


class ResizeCallbackTests(unittest.TestCase):
    def test_reads_the_current_client_width(self):
        fake = SimpleNamespace(current_client_size=lambda: (960, 540))
        with mock.patch.dict(sys.modules, {"hs_window": fake}):
            self.assertEqual(960, web_ui._overlay_window_width())

    def test_missing_window_returns_none(self):
        fake = SimpleNamespace(current_client_size=lambda: None)
        with mock.patch.dict(sys.modules, {"hs_window": fake}):
            self.assertIsNone(web_ui._overlay_window_width())

    def test_resize_delegates_to_hs_window_and_reports_actual_size(self):
        calls = {}

        def set_client_width(width):
            calls["width"] = width
            return (960, 540)

        fake = SimpleNamespace(set_client_width=set_client_width)
        with (
            mock.patch.dict(sys.modules, {"hs_window": fake}),
            mock.patch.object(web_ui, "_log"),
        ):
            result = web_ui._overlay_resize_window(960)

        self.assertEqual(960, calls["width"])
        self.assertEqual([960, 540], result)

    def test_resize_reports_failure_as_none(self):
        fake = SimpleNamespace(set_client_width=lambda width: None)
        with (
            mock.patch.dict(sys.modules, {"hs_window": fake}),
            mock.patch.object(web_ui, "_log"),
        ):
            self.assertIsNone(web_ui._overlay_resize_window(960))


if __name__ == "__main__":
    unittest.main()
