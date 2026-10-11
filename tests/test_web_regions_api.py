# -*- coding: utf-8 -*-
"""Web 侧截图区域框：/api/regions 接口与页面元素。"""

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

import web_ui

INDEX_HTML = Path(__file__).resolve().parent.parent / "web" / "index.html"


class RegionsApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), web_ui.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      name="test-regions-web", daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _get(self, path):
        with urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=60) as resp:
            return json.loads(resp.read().decode("utf8"))

    def test_endpoint_returns_the_preview(self):
        preview = {"ok": True, "image": "data:image/jpeg;base64,AAA",
                   "width": 1920, "height": 1080, "regions": [], "points": [],
                   "checks": [], "generated_at": "2026-01-01T00:00:00"}
        with patch.object(web_ui.screen_regions, "build_region_preview",
                          return_value=dict(preview)), \
                patch.object(web_ui, "_log"):
            data = self._get("/api/regions")

        self.assertTrue(data["ok"])
        self.assertEqual("data:image/jpeg;base64,AAA", data["result"]["image"])

    def test_endpoint_reports_a_screenshot_failure(self):
        with patch.object(web_ui.screen_regions, "build_region_preview",
                          side_effect=RuntimeError("截图失败啦")), \
                patch.object(web_ui.traceback, "print_exc"):
            data = self._get("/api/regions")

        self.assertFalse(data["ok"])
        self.assertIn("截图失败啦", data["error"])

    def test_api_regions_logs_a_failed_check(self):
        preview = {"ok": False, "image": "data:image/jpeg;base64,AAA",
                   "width": 1280, "height": 720, "regions": [], "points": [],
                   "checks": [{"key": "client-aspect", "label": "客户区比例",
                               "status": "fail", "detail": "客户区 1280×800 不是 16:9",
                               "hint": "", "required": True}],
                   "generated_at": "2026-01-01T00:00:00"}
        logged = []

        with patch.object(web_ui.screen_regions, "build_region_preview",
                          return_value=dict(preview)), \
                patch.object(web_ui, "_log",
                             side_effect=lambda level, msg: logged.append((level, msg))):
            payload = web_ui.api_regions()

        self.assertTrue(payload["ok"])
        self.assertEqual("WARN", logged[0][0])
        self.assertIn("1280×800", logged[0][1])

    def test_healthy_preview_is_logged_with_the_client_geometry(self):
        """分辨率不再是判据之后，日志报的是客户区几何。"""
        preview = {"ok": True, "image": "data:image/jpeg;base64,AAA",
                   "width": 3840, "height": 2160,
                   "client": {"width": 1920, "height": 1080},
                   "mapping": {"available": True,
                               "client": {"width": 1920, "height": 1080},
                               "screen_origin": [960, 551]},
                   "regions": [{"key": "recommendation"}], "points": [],
                   "checks": [], "generated_at": "2026-01-01T00:00:00"}
        logged = []

        with patch.object(web_ui.screen_regions, "build_region_preview",
                          return_value=dict(preview)), \
                patch.object(web_ui, "_log",
                             side_effect=lambda level, msg: logged.append((level, msg))):
            web_ui.api_regions()

        self.assertEqual("SYS", logged[0][0])
        self.assertIn("客户区 1920×1080", logged[0][1])
        self.assertIn("(960, 551)", logged[0][1])

    def test_healthy_preview_without_hearthstone_falls_back_to_screen_size(self):
        preview = {"ok": True, "image": "data:image/jpeg;base64,AAA",
                   "width": 1920, "height": 1080,
                   "mapping": {"available": False},
                   "regions": [], "points": [],
                   "checks": [], "generated_at": "2026-01-01T00:00:00"}
        logged = []

        with patch.object(web_ui.screen_regions, "build_region_preview",
                          return_value=dict(preview)), \
                patch.object(web_ui, "_log",
                             side_effect=lambda level, msg: logged.append((level, msg))):
            web_ui.api_regions()

        self.assertEqual("SYS", logged[0][0])
        self.assertIn("1920×1080", logged[0][1])
        self.assertIn("炉石未运行", logged[0][1])


class RegionsPageTests(unittest.TestCase):
    def setUp(self):
        self.html = INDEX_HTML.read_text(encoding="utf8")

    def test_page_points_at_the_overlay_calibrate_button(self):
        """按钮已从网页移到日志浮窗，页面只保留指路说明。"""
        self.assertIn("日志浮窗", self.html)
        self.assertIn("「校准」", self.html)
        self.assertIn("请对齐相应UI", self.html)

    def test_page_no_longer_has_the_in_page_preview(self):
        for token in ('id="btnRegions"', 'id="regionsImg"',
                      'id="regionsResult"'):
            self.assertNotIn(token, self.html)

    def test_page_explains_what_the_region_boxes_mean(self):
        for token in ("盒子推荐面板", "换牌确认按钮", "AI胜率浮动条",
                      "阶段判定点", "鼠标穿透"):
            self.assertIn(token, self.html)


if __name__ == "__main__":
    unittest.main()
