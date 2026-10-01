# -*- coding: utf-8 -*-
"""浮窗「校准」按钮 + 屏幕截图区域框叠加层（region_overlay）。"""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import log_overlay
import region_overlay
import web_ui


class FakeOverlay:
    """替身：不创建真窗口，只记录调用。"""

    def __init__(self, visible_after_toggle=True):
        self.visible = False
        self.after = visible_after_toggle
        self.toggles = 0

    def toggle(self):
        self.toggles += 1
        self.visible = self.after
        return self.visible

    def hide(self):
        self.visible = False

    def is_visible(self):
        return self.visible


class FakeCalibrationProc:
    """替身：模拟校准工具子进程（poll/terminate）。"""

    def __init__(self, alive=True):
        self.alive = alive
        self.terminated = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        self.alive = False


class _BlockingRunner:
    """替身窗口循环：一直阻塞到被放行，模拟真实叠加层“显示中”。"""

    def __init__(self):
        self.release = threading.Event()
        self.calls = 0

    def __call__(self):
        self.calls += 1
        self.release.wait(3)

    def finish(self):
        self.release.set()


class ToggleStateTests(unittest.TestCase):
    def tearDown(self):
        for thread in threading.enumerate():
            if thread.name == "hs-region-box":
                thread.join(3)

    def test_toggle_reports_the_new_state_from_the_runner(self):
        runner = _BlockingRunner()
        overlay = region_overlay.RegionBoxOverlay(runner=runner)
        self.addCleanup(runner.finish)

        self.assertFalse(overlay.is_visible())
        self.assertTrue(overlay.toggle())
        self.assertTrue(overlay.is_visible())
        self.assertFalse(overlay.toggle())

    def test_hide_stops_the_runner(self):
        runner = _BlockingRunner()
        overlay = region_overlay.RegionBoxOverlay(runner=runner)

        overlay.show()
        self.assertTrue(overlay.is_visible())
        self.assertFalse(overlay.hide())
        runner.finish()
        self.assertFalse(overlay.is_visible())

    def test_show_while_visible_does_not_start_twice(self):
        runner = _BlockingRunner()
        overlay = region_overlay.RegionBoxOverlay(runner=runner)
        self.addCleanup(runner.finish)

        overlay.show()
        for _ in range(50):
            if runner.calls:
                break
            threading.Event().wait(0.02)
        overlay.show()

        self.assertEqual(1, runner.calls)
        overlay.hide()

    def test_runner_exception_never_escapes(self):
        def boom():
            raise RuntimeError("炸了")

        overlay = region_overlay.RegionBoxOverlay(runner=boom)

        with patch("builtins.print"):
            overlay.show()
            for thread in threading.enumerate():
                if thread.name == "hs-region-box":
                    thread.join(3)

        self.assertFalse(overlay.is_visible())

    def test_module_level_toggle_uses_the_default_overlay(self):
        fake = FakeOverlay()
        with patch.object(region_overlay, "_DEFAULT", fake):
            self.assertTrue(region_overlay.toggle())
            self.assertEqual(1, fake.toggles)
            region_overlay.hide()
            self.assertFalse(region_overlay.is_visible())


class HintTests(unittest.TestCase):
    def test_title_is_the_required_prompt(self):
        lines = region_overlay.hint_lines(None)

        self.assertEqual("请对齐相应UI", region_overlay.HINT_TITLE)
        self.assertEqual("请对齐相应UI", lines[0]["text"])

    def test_panel_verdict_lines(self):
        unknown = [line["text"] for line in region_overlay.hint_lines(None)]
        visible = [line["text"] for line in region_overlay.hint_lines(True)]
        missing = [line["text"] for line in region_overlay.hint_lines(False)]

        self.assertEqual(3, len(unknown))
        self.assertIn("已检测到盒子面板", visible)
        self.assertTrue(any("移进绿框" in text for text in missing))

    def test_hint_text_has_no_emoji_glyphs(self):
        """PIL 用微软雅黑画字，emoji 会变成方框 —— 提示条里不许出现 emoji。"""
        banned = ((0x1F000, 0x1FAFF),   # emoji
                  (0x2190, 0x2BFF),     # 箭头 / 杂项符号 / 装饰符号
                  (0xFE00, 0xFE0F),     # 变体选择符
                  (0x2700, 0x27BF))     # 装饰性 dingbat
        for state in (None, True, False):
            for line in region_overlay.hint_lines(state):
                for ch in line["text"]:
                    code = ord(ch)
                    for low, high in banned:
                        self.assertFalse(low <= code <= high,
                                         f"{ch!r} 可能在微软雅黑里缺字形")

    def test_close_hint_mentions_esc_and_the_button(self):
        lines = region_overlay.hint_lines(None)

        self.assertIn("Esc", lines[-1]["text"])
        self.assertIn("校准", lines[-1]["text"])


class PaintLayerTests(unittest.TestCase):
    def test_layer_is_transparent_where_nothing_is_drawn(self):
        layer = region_overlay.paint_layer(400, 300, None)

        self.assertEqual("RGBA", layer.mode)
        self.assertEqual((400, 300), layer.size)
        self.assertEqual(0, layer.getpixel((399, 299))[3])   # 角落全透明
        # 提示条画在顶部中间：那里必须有不透明像素
        self.assertGreater(layer.getpixel((200, 30))[3], 0)

    def test_layer_draws_region_boxes(self):
        config = SimpleNamespace(
            desktop_size=(1920, 1080), desktop_dpi=96,
            recommendation_roi=(10, 40, 110, 240),
            mulligan_confirm_roi=(200, 300, 300, 360))

        layer = region_overlay.paint_layer(500, 400, None, config)

        # 绿框（recommendation）的左边框应当是不透明的绿色像素
        pixel = layer.getpixel((10, 140))
        self.assertEqual((99, 199, 111, 255), tuple(pixel))

    def test_panel_state_is_none_when_the_grab_fails(self):
        def broken_grab(*_args, **_kwargs):
            raise RuntimeError("截不到")

        overlay = region_overlay.RegionBoxOverlay(runner=lambda: None)
        with patch("PIL.ImageGrab.grab", side_effect=broken_grab):
            self.assertIsNone(overlay._panel_state())


class OverlayButtonTests(unittest.TestCase):
    def test_calibrate_button_exists_and_is_short(self):
        source = __import__("inspect").getsource(log_overlay._run)

        self.assertIn('_make_btn(btn_frame, "校准"', source)
        self.assertIn('_place(calibrate_btn, "calibrate")', source)
        self.assertIn("_ON_CALIBRATE", source)

    def test_callback_is_stored_by_start(self):
        saved = log_overlay._ON_CALIBRATE
        try:
            log_overlay._STARTED[0] = False
            with patch.object(log_overlay.threading, "Thread"):
                log_overlay.start(on_calibrate=_stub_callback)
            self.assertIs(_stub_callback, log_overlay._ON_CALIBRATE)
        finally:
            log_overlay._ON_CALIBRATE = saved
            log_overlay._STARTED[0] = False

    def test_web_ui_wires_the_callback(self):
        bound = {}
        overlay = SimpleNamespace(
            start=lambda **kwargs: bound.update(kwargs),
            is_running=lambda: False)

        with (
            patch.object(web_ui, "log_overlay", overlay),
            patch.object(web_ui.threading, "Thread"),
        ):
            web_ui._bind_overlay()

        self.assertIs(bound["on_calibrate"], web_ui._overlay_toggle_calibrate)
        # 关浮窗时要能把还开着的校准窗口一起收掉。
        self.assertIs(bound["on_calibrate_close"],
                      web_ui._overlay_close_calibrate)

    def test_stopping_the_overlay_hides_the_region_boxes(self):
        fake = FakeOverlay(visible_after_toggle=True)
        fake.visible = True
        saved = log_overlay._STOP.is_set()
        saved_close = log_overlay._ON_CALIBRATE_CLOSE
        try:
            log_overlay._ON_CALIBRATE_CLOSE = None
            with patch.dict("sys.modules", {"region_overlay": fake}):
                log_overlay.stop()
            self.assertFalse(fake.visible)
        finally:
            log_overlay._ON_CALIBRATE_CLOSE = saved_close
            if not saved:
                log_overlay._STOP.clear()

    def test_stopping_the_overlay_closes_the_calibration_window(self):
        calls = []
        saved = log_overlay._STOP.is_set()
        saved_close = log_overlay._ON_CALIBRATE_CLOSE
        try:
            log_overlay._ON_CALIBRATE_CLOSE = lambda: calls.append("closed")
            log_overlay.stop()
        finally:
            log_overlay._ON_CALIBRATE_CLOSE = saved_close
            if not saved:
                log_overlay._STOP.clear()

        self.assertEqual(["closed"], calls)


class CalibrationSessionTests(unittest.TestCase):
    """浮窗「校准」按钮与网页「校准」按钮 = 同一个校准窗口。"""

    def setUp(self):
        self.saved = web_ui._CALIBRATION_PROC
        web_ui._CALIBRATION_PROC = None

    def tearDown(self):
        web_ui._CALIBRATION_PROC = self.saved

    def _patch_popen(self, proc):
        started = []
        return started, patch.object(
            web_ui.subprocess, "Popen",
            side_effect=lambda *args, **kwargs: (started.append(args), proc)[1])

    def test_toggle_starts_then_closes_the_same_window(self):
        proc = FakeCalibrationProc()
        started, popen = self._patch_popen(proc)
        with popen, patch.object(web_ui, "_log"):
            self.assertTrue(web_ui._overlay_toggle_calibrate())
            self.assertEqual(1, len(started))
            self.assertIn("calibrate_roi.py", str(started[0]))
            self.assertFalse(web_ui._overlay_toggle_calibrate())
        self.assertTrue(proc.terminated)

    def test_toggle_reports_failures(self):
        logged = []
        with (
            patch.object(web_ui.subprocess, "Popen",
                         side_effect=OSError("窗口建不出来")),
            patch.object(web_ui, "_log",
                         side_effect=lambda level, msg: logged.append((level, msg))),
        ):
            self.assertFalse(web_ui._overlay_toggle_calibrate())

        self.assertEqual("WARN", logged[0][0])
        self.assertIn("窗口建不出来", logged[0][1])

    def test_web_button_reuses_a_running_window(self):
        web_ui._CALIBRATION_PROC = FakeCalibrationProc()
        with patch.object(web_ui.subprocess, "Popen") as popen, \
                patch.object(web_ui, "_log"):
            result = web_ui.api_calibrate()

        self.assertTrue(result["ok"])
        popen.assert_not_called()

    def test_closing_the_overlay_closes_the_calibration_window(self):
        proc = FakeCalibrationProc()
        web_ui._CALIBRATION_PROC = proc
        web_ui._overlay_close_calibrate()

        self.assertTrue(proc.terminated)
        self.assertFalse(web_ui._calibration_running())

    def test_closing_when_nothing_runs_is_a_no_op(self):
        with patch.object(web_ui, "_log") as log:
            web_ui._overlay_close_calibrate()
        log.assert_not_called()


def _stub_callback():
    return True


if __name__ == "__main__":
    unittest.main()
