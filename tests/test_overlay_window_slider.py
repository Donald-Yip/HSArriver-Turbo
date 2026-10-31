# -*- coding: utf-8 -*-
"""浮窗「炉石窗口宽度」滑条：事件行为、回调接线与 web_ui 传参。

使用记录型 fake Tk 树，不打开真实窗口。
"""

from __future__ import annotations

import inspect
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import hs_window
import log_overlay
import web_ui
from tests.helpers.fake_tk import FakeEvent, overlay_harness


class SliderWiringTests(unittest.TestCase):
    def test_start_accepts_the_window_callbacks(self):
        params = inspect.signature(log_overlay.start).parameters
        for name in ("window_width_callback", "on_resize_window"):
            self.assertIn(name, params)

    def test_slider_canvas_events_update_value_and_apply_on_release(self):
        resized = []

        def resize(width):
            resized.append(width)
            return [2300, 1294]

        with overlay_harness(
            _IS_RUNNING=lambda: True,
            _WINDOW_WIDTH=lambda: 1280,
            _ON_RESIZE_WINDOW=resize,
        ) as ui:
            # 自绘 Canvas（不是 tk.Scale：它的滑钮是方块，且会带动整窗拖动）。
            self.assertEqual(0, ui.tk.scale_calls)
            canvas = next(
                widget for widget in ui.widgets_of("canvas")
                if ("<B1-Motion>", False) in widget.bindings
            )
            self.assertIn(("<Configure>", False), canvas.bindings)
            self.assertIn(("<ButtonRelease-1>", False), canvas.bindings)
            self.assertIn(log_overlay.SIZE_ROW_TITLE, [w.options.get("text") for w in ui.widgets_of("label")])

            self.assertEqual("break", canvas.fire("<Button-1>", FakeEvent(canvas, x=0, y=5)))
            self.assertEqual("960×540", next(
                w.options["text"] for w in ui.widgets_of("label")
                if w.options.get("text") == "960×540"
            ))
            self.assertEqual("break", canvas.fire("<B1-Motion>", FakeEvent(canvas, x=9999, y=5)))
            max_size = f"{int(hs_window.MAX_16_9_WIDTH)}×{int(round(hs_window.MAX_16_9_WIDTH * 9 / 16))}"
            self.assertIn(max_size, [w.options.get("text") for w in ui.widgets_of("label")])
            self.assertEqual("break", canvas.fire("<ButtonRelease-1>", FakeEvent(canvas, x=9999, y=5)))

        self.assertEqual([int(hs_window.MAX_16_9_WIDTH)], resized)

    def test_root_drag_ignores_slider_events(self):
        with overlay_harness(
            _IS_RUNNING=lambda: True,
            _WINDOW_WIDTH=lambda: 1280,
        ) as ui:
            canvas = next(
                widget for widget in ui.widgets_of("canvas")
                if ("<B1-Motion>", False) in widget.bindings
            )
            original_geometry = list(ui.root.geometries)

            ui.root.fire("<Button-1>", FakeEvent(canvas, x=10, y=10))
            ui.root.fire("<B1-Motion>", FakeEvent(canvas, x=100, y=100))
            self.assertEqual(original_geometry, ui.root.geometries)

            ui.root.fire("<Button-1>", FakeEvent(ui.root, x=10, y=10))
            ui.root.fire("<B1-Motion>", FakeEvent(ui.root, x=30, y=20))
            self.assertNotEqual(original_geometry, ui.root.geometries)


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
    def test_reads_the_current_client_width_or_none(self):
        for size, expected in (((960, 540), 960), (None, None)):
            with self.subTest(size=size):
                fake = SimpleNamespace(current_client_size=lambda size=size: size)
                with mock.patch.dict(sys.modules, {"hs_window": fake}):
                    self.assertEqual(expected, web_ui._overlay_window_width())

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


class SliderSyncActionTests(unittest.TestCase):
    """滑条该不该可用/置灰：必须每拍重新判断，不能只在开浮窗时判一次。

    用户 2026-10-26 反馈：先开浮窗、后启动炉石，滑条一直是灰的拖不动。
    """

    def test_enables_when_hearthstone_shows_up(self):
        self.assertEqual(
            "enable",
            log_overlay.slider_sync_action(False, 960, 2384, 16))

    def test_disables_when_hearthstone_is_gone(self):
        self.assertEqual(
            "disable",
            log_overlay.slider_sync_action(True, 2384, None, 16))

    def test_follows_an_external_resize(self):
        self.assertEqual(
            "follow",
            log_overlay.slider_sync_action(True, 960, 2384, 16))

    def test_none_when_nothing_really_changed(self):
        cases = (
            ("staying_disabled", False, 960, None),
            ("small_change", True, 2384, 2390),
        )
        for name, enabled, value, current in cases:
            with self.subTest(case=name):
                self.assertEqual(
                    "none",
                    log_overlay.slider_sync_action(enabled, value, current, 16))

    def test_broken_values_do_not_crash(self):
        self.assertEqual(
            "none", log_overlay.slider_sync_action(True, "坏值", 2384, 16))


if __name__ == "__main__":
    unittest.main()
