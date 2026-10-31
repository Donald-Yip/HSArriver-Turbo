# -*- coding: utf-8 -*-
"""Web 侧环境自检：缓存、状态摘要、日志输出、/api/selfcheck 与页面元素。"""

import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import web_ui

INDEX_HTML = Path(__file__).resolve().parent.parent / "web" / "index.html"


def fake_result(ok=True, failed=0):
    return {
        "ok": ok, "failed": failed, "warned": 0, "passed": 3, "total": 3,
        "summary": "自检通过：3 项全部达标" if ok else f"自检未通过：{failed} 项必须修复",
        "python": "3.12.14", "checked_at": "2026-01-01T00:00:00",
        "items": [{"key": "python", "label": "Python 3.12（64 位）",
                   "status": "ok", "detail": "3.12.14（64 位）", "hint": "",
                   "purpose": "脚本运行的基础环境", "required": True}],
    }


class FakeSelfcheckRunner:
    """Callable fake injected at the public ``selfcheck.run_checks`` seam."""

    def __init__(self, factory):
        self._factory = factory
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self._factory()


class SelfcheckCacheTests(unittest.TestCase):
    def setUp(self):
        self._saved = web_ui._selfcheck_state["result"]
        web_ui._selfcheck_state["result"] = None

    def tearDown(self):
        web_ui._selfcheck_state["result"] = self._saved

    def test_result_is_cached_until_forced(self):
        runner = FakeSelfcheckRunner(fake_result)
        with patch.object(web_ui.selfcheck, "run_checks", new=runner):
            first = web_ui.run_selfcheck()
            second = web_ui.run_selfcheck()
            third = web_ui.run_selfcheck(force=True)

        self.assertEqual(2, runner.calls)
        self.assertIs(first, second)
        self.assertEqual(first["summary"], third["summary"])

    def test_status_summary_before_and_after_a_run(self):
        self.assertEqual({"checked": False}, web_ui.selfcheck_summary())

        runner = FakeSelfcheckRunner(lambda: fake_result(ok=False, failed=2))
        with patch.object(web_ui.selfcheck, "run_checks", new=runner):
            web_ui.run_selfcheck(force=True)

        summary = web_ui.selfcheck_summary()

        self.assertTrue(summary["checked"])
        self.assertFalse(summary["ok"])
        self.assertEqual(2, summary["failed"])
        self.assertEqual("3.12.14", summary["python"])

    def test_crashed_selfcheck_still_returns_a_conclusion(self):
        with patch.object(web_ui.selfcheck, "run_checks",
                          side_effect=RuntimeError("炸了")), \
                patch.object(web_ui.traceback, "print_exc"):
            result = web_ui.run_selfcheck(force=True)

        self.assertFalse(result["ok"])
        self.assertEqual(1, result["failed"])
        self.assertIn("炸了", result["summary"])

    def test_start_selfcheck_async_runs_in_the_background(self):
        started = []

        class _Thread:
            def __init__(self, target=None, name=None, daemon=None):
                self.target = target
                self.name = name
                self.daemon = daemon

            def start(self):
                started.append(self.name)

        with patch.object(web_ui.threading, "Thread", _Thread):
            thread = web_ui.start_selfcheck_async()

        self.assertEqual(["hs-selfcheck"], started)
        self.assertEqual("hs-selfcheck", thread.name)
        self.assertTrue(thread.daemon)

    def test_api_selfcheck_wraps_the_result(self):
        runner = FakeSelfcheckRunner(fake_result)
        with patch.object(web_ui.selfcheck, "run_checks", new=runner):
            payload = web_ui.api_selfcheck(force=True)

        self.assertTrue(payload["ok"])
        self.assertIn("items", payload["result"])
        self.assertEqual(1, runner.calls)


class SelfcheckLoggingTests(unittest.TestCase):
    def test_failures_and_warnings_are_logged_with_hints(self):
        result = {"summary": "自检未通过：1 项必须修复", "items": [
            {"key": "python", "label": "Python 3.12（64 位）", "status": "fail",
             "detail": "3.13.1（64 位）", "hint": "请装 Python 3.12"},
            {"key": "numpy", "label": "numpy", "status": "ok",
             "detail": "已安装 2.5.2", "hint": ""},
            {"key": "hearthstone", "label": "炉石窗口", "status": "warn",
             "detail": "现在没找到炉石窗口", "hint": "点开始运行会拉起"},
        ]}
        logged = []

        with patch.object(web_ui, "_log",
                          side_effect=lambda level, msg: logged.append((level, msg))):
            web_ui._log_selfcheck(result)

        levels = [level for level, _ in logged]
        text = "\n".join(msg for _, msg in logged)
        self.assertIn("ERROR", levels)
        self.assertIn("WARN", levels)
        self.assertIn("请装 Python 3.12", text)
        self.assertIn("点开始运行会拉起", text)
        self.assertNotIn("numpy", text)      # 通过的项不刷屏

    def test_selfcheck_lines_stay_out_of_the_overlay(self):
        """自检明细不往浮窗里灌，浮窗只留对局关键行。

        注意「炉石客户区：读不到（炉石未运行）」这种行本身含「炉石」二字，
        以前会被关键字匹配放进浮窗（用户 2026-10-26 反馈：浮窗一直红着两条
        自检，看着像挂机出故障）；自检整类都不该进浮窗。
        """
        self.assertFalse(web_ui._overlay_key("环境自检 ✅ Python 3.12（64 位）：3.12.14"))
        self.assertFalse(web_ui._overlay_key(
            "环境自检 ⚠ 炉石客户区：读不到（炉石未运行）；屏幕 3840×2160"
            " —— 坐标换算依赖客户区几何；启动炉石后重跑自检即可看到实际客户区。"))
        # 真正的存活告警仍必须进浮窗（别把「炉石」相关的告警一起过滤掉）。
        self.assertTrue(web_ui._overlay_key("⚠️ 炉石进程已退出，自动化已自动停止"))


class SelfcheckApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), web_ui.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      name="test-selfcheck-web", daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self._saved = web_ui._selfcheck_state["result"]
        web_ui._selfcheck_state["result"] = None

    def tearDown(self):
        web_ui._selfcheck_state["result"] = self._saved

    def _get(self, path):
        with urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=60) as resp:
            return json.loads(resp.read().decode("utf8"))

    def _post(self, path, payload=None):
        from urllib.request import Request
        request = Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload or {}).encode("utf8"),
            headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=60) as resp:
            return json.loads(resp.read().decode("utf8"))

    def test_get_returns_items_and_post_forces_a_rerun(self):
        runner = FakeSelfcheckRunner(fake_result)
        with patch.object(web_ui.selfcheck, "run_checks", new=runner):
            data = self._get("/api/selfcheck")

            self.assertTrue(data["ok"])
            self.assertTrue(data["result"]["ok"])
            self.assertEqual(1, len(data["result"]["items"]))
            self.assertEqual(1, runner.calls)      # GET 命中缓存只跑一次

            self._post("/api/selfcheck")

        self.assertEqual(2, runner.calls)          # POST 强制重跑

    def test_status_exposes_the_selfcheck_summary(self):
        web_ui._selfcheck_state["result"] = fake_result(ok=False, failed=1)
        with patch.object(web_ui.CTRL, "fsm", None):
            status = self._get("/api/status")

        self.assertTrue(status["selfcheck"]["checked"])
        self.assertEqual(1, status["selfcheck"]["failed"])

    def test_unknown_route_still_404s(self):
        with self.assertRaises(HTTPError) as caught:
            self._get("/api/nope")

        self.assertEqual(404, caught.exception.code)


class SelfcheckPageTests(unittest.TestCase):
    def setUp(self):
        self.html = INDEX_HTML.read_text(encoding="utf8")

    def test_page_contract_for_selfcheck(self):
        for token in ('id="selfcheckBadge"', 'id="btnSelfcheck"',
                      'id="selfcheckSummary"', 'id="selfcheckList"',
                      'id="cardSelfcheck"',
                      'post("/api/selfcheck"', 'get("/api/selfcheck")',
                      "Python 必须是 3.12", "paddlepaddle",
                      "环境自检发现", "pip install -r requirements.txt"):
            with self.subTest(token=token):
                self.assertIn(token, self.html)


if __name__ == "__main__":
    unittest.main()
