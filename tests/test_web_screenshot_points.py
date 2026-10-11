# -*- coding: utf-8 -*-
"""抓图标记（--points mode）与「控制台自停」接口。

两条都是给 agent 用的：
  * `screenshot --points mode` —— 只截图、不点击，把模式切换的四个点击点画在图上，
    用来核对 `hsctl mode` 到底点在哪儿（不需要真的点一次碰运气）；
  * `POST /api/serve/stop` —— 让控制台**自己**退出。提权起的控制台，非提权进程
    结束不了它（taskkill 报 Access denied），所以必须留这条不需要提权的通道；
    token 只写在本机端口文件里，网络上的请求拿不到，也就停不掉。
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import web_ui


class ModePointMarkerTests(unittest.TestCase):
    """`_draw_mode_points`：图上十字的位置必须等于点击点。"""

    @staticmethod
    def blank(width=1920, height=1080):
        return Image.new("RGB", (width, height), (10, 20, 30))

    def test_draws_one_marker_per_point(self):
        image = self.blank()

        drawn = web_ui._draw_mode_points(image, {"kind": "client"},
                                         web_ui.screen_regions.mode_click_points())

        self.assertEqual(4, len(drawn))
        self.assertEqual({"menu_open", "wild", "standard", "casual"},
                         {item["key"] for item in drawn})
        pixels = image.load()
        for item in drawn:
            x, y = item["image"]
            self.assertEqual((255, 0, 0), pixels[x, y],
                             f"{item['key']} 的十字中心不在点击点上")

    def test_scales_with_a_degraded_fullscreen_frame(self):
        """客户区抓不到时拍的是整屏：标记要按信息里的映射挪 + 按比例缩放。"""
        image = self.blank(3840, 2160)
        info = {"kind": "client", "fallback": "full",
                "marks": {"wild": [1234, 760]}}

        with mock.patch.object(web_ui, "_target_frame_size",
                               return_value=(1920, 1080)):
            drawn = web_ui._draw_mode_points(image, info, {"wild": (617, 380)})

        self.assertEqual([1234, 760], drawn[0]["image"])

    def test_points_without_marks_use_client_coordinates(self):
        image = self.blank(1920, 1080)

        drawn = web_ui._draw_mode_points(image, {"kind": "client"},
                                         {"wild": (617, 380)})

        self.assertEqual([617, 380], drawn[0]["image"])
        self.assertEqual([617, 380], drawn[0]["client"])


class ScreenshotPointsApiTests(unittest.TestCase):
    """`api_screenshot(points="mode")`：标记确实画进了返回的那张图。

    这里**不 mock `_save_frame`**（那会把"把 info 合进返回值 + 真的存盘"这段
    也绕过去）：让它照常落盘，再把 PNG 读回来数红十字，最后清理测试产物。
    """

    def setUp(self):
        self.produced = []

        def track(path):
            if path:
                self.produced.append(Path(path))

        self._track = track
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for path in self.produced:
            try:
                path.unlink()
            except OSError:
                pass

    def _call(self, body):
        image = Image.new("RGB", (1920, 1080), (0, 0, 0))
        with mock.patch.object(web_ui, "capture_frame",
                               return_value=(image, {"kind": "client"})):
            payload = web_ui.api_screenshot(dict(body, quiet=True))
        self._track(payload.get("path"))
        self._track(payload.get("latest"))
        return payload

    def test_points_mode_draws_the_markers(self):
        payload = self._call({"points": "mode", "out": "test_points_mode.png"})

        self.assertTrue(payload["ok"], payload)
        self.assertEqual(4, len(payload["mode_points"]))
        with Image.open(payload["path"]) as saved:
            frame = saved.convert("RGB")
        pixels = frame.load()
        for item in payload["mode_points"]:
            x, y = item["image"]
            self.assertEqual((255, 0, 0), pixels[x, y],
                             f"{item['key']} 的十字中心不在点击点上")

    def test_without_points_nothing_is_drawn(self):
        payload = self._call({"out": "test_points_off.png"})

        self.assertNotIn("mode_points", payload)
        with Image.open(payload["path"]) as saved:
            self.assertEqual((0, 0, 0), saved.convert("RGB").load()[617, 380])

    def test_unknown_points_value_is_rejected(self):
        payload = self._call({"points": "whatever"})

        self.assertFalse(payload["ok"])
        self.assertIn("points", payload["error"])


class ServeStopApiTests(unittest.TestCase):
    """token 校验：对的自停，错的 403 且**不能**退出。"""

    def setUp(self):
        self.shutdown = mock.patch.object(web_ui, "_self_shutdown")
        self.shutdown_mock = self.shutdown.start()
        self.addCleanup(self.shutdown.stop)

    def test_missing_token_is_forbidden(self):
        payload, code = web_ui.api_serve_stop({})

        self.assertEqual(403, code)
        self.assertFalse(payload["ok"])

    def test_wrong_token_is_forbidden(self):
        payload, code = web_ui.api_serve_stop({"token": "nope"})

        self.assertEqual(403, code)
        self.assertFalse(payload["ok"])

    def test_correct_token_is_accepted(self):
        payload, code = web_ui.api_serve_stop({"token": web_ui._SERVE_TOKEN})

        self.assertEqual(200, code)
        self.assertTrue(payload["ok"])

    def test_self_shutdown_stops_automation_then_exits(self):
        """自停收尾：先停自动化 → 清端口文件 → os._exit(0)。

        注意：这个测试**不能**用类级 setUp 里的 `_self_shutdown` 补丁，所以
        临时解掉它（否则 `web_ui._self_shutdown()` 只是打到 Mock，什么都不会发生）。
        """
        calls = []
        fake_thread = mock.Mock()
        self.shutdown.stop()
        try:
            with mock.patch.object(web_ui, "CTRL",
                                   mock.Mock(automation_thread=fake_thread)), \
                    mock.patch.object(web_ui, "api_stop",
                                      return_value={"ok": True}), \
                    mock.patch.object(web_ui.time, "time",
                                      side_effect=[0.0, 999.0]), \
                    mock.patch.object(web_ui.time, "sleep"), \
                    mock.patch.object(web_ui, "_clear_port_file") as clear, \
                    mock.patch.object(web_ui.os, "_exit") as exit_fn:
                clear.side_effect = lambda: calls.append("clear")
                exit_fn.side_effect = lambda code: calls.append(("exit", code))
                web_ui._self_shutdown()
        finally:
            self.shutdown.start()

        self.assertEqual(["clear", ("exit", 0)], calls)


class PortFileTests(unittest.TestCase):
    """端口文件必须带上自停 token 与控制台解释器（doctor 也要读它）。"""

    def test_payload_carries_token_and_executable(self):
        written = {}
        target = web_ui.ROOT / ".tmp-tests" / "ui_port_probe.json"

        def fake_replace(src, dst):
            written["text"] = Path(src).read_text(encoding="utf8")
            os.unlink(src)

        with mock.patch.object(web_ui, "PORT_FILE", target), \
                mock.patch.object(web_ui.os, "replace", side_effect=fake_replace):
            web_ui._write_port_file(8765)

        payload = json.loads(written["text"])
        self.assertEqual(8765, payload["port"])
        self.assertTrue(payload["token"])
        self.assertTrue(payload["executable"])


if __name__ == "__main__":
    unittest.main()
