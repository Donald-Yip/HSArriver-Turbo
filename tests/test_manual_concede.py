# -*- coding: utf-8 -*-
"""手动投降（`hsctl concede now` / POST /api/concede_now）：点击序列与守卫。

需求背景（用户 2026-10-11）：自动打的时候想立刻认输，不该被迫先 stop（stop 只让
FSM 停下、不会点认输，卡着的对局还在打）。所以手动投降**不要求**先停自动化，
但必须和 FSM 抢鼠标这件事错开 —— 复用 click.hearthstone_action_session()。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import click as hs_click
import web_ui


class ManualConcedeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._patchers = []

        def fake_session():
            self.calls.append("session:enter")
            return _NullCtx()

        def fake_setting():
            self.calls.append("click_setting")

        def fake_concede():
            self.calls.append("click_concede")

        for target, kwargs in (
            ("hearthstone_action_session", {"side_effect": fake_session}),
            ("click_setting", {"side_effect": fake_setting}),
            ("click_concede", {"side_effect": fake_concede}),
        ):
            p = patch.object(hs_click, target, **kwargs)
            p.start()
            self._patchers.append(p)
        p = patch.object(web_ui, "time", **{"sleep": lambda *_: None})
        p.start()
        self._patchers.append(p)
        p = patch.object(web_ui, "_log")
        p.start()
        self._patchers.append(p)
        # 默认：没有队列 / 没有 enter_battle / 没有自动化在跑
        self._saved_queue = dict(web_ui._QUEUE_STATE)
        web_ui._QUEUE_STATE["running"] = False
        web_ui._ENTER_STATE["running"] = False
        self._saved_thread = web_ui.CTRL.automation_thread
        web_ui.CTRL.automation_thread = None

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        web_ui._QUEUE_STATE.update(self._saved_queue)
        web_ui.CTRL.automation_thread = self._saved_thread

    def test_clicks_gear_then_concede(self):
        result = web_ui.api_concede_now({})

        self.assertTrue(result["ok"])
        # 必须是「点齿轮 → 点认输」这个顺序
        self.assertEqual(["session:enter", "click_setting", "click_concede"],
                         self.calls)

    def test_rejected_while_queue_is_running(self):
        web_ui._QUEUE_STATE["running"] = True
        try:
            result = web_ui.api_concede_now({})
        finally:
            web_ui._QUEUE_STATE["running"] = False

        self.assertFalse(result["ok"])
        self.assertEqual([], self.calls)

    def test_allowed_while_automation_is_running(self):
        """这正是它的用途：自动打在跑的时候也要能立刻投降。"""
        import unittest.mock as mock
        with mock.patch.object(web_ui.CTRL, "automation_thread",
                               mock.Mock()) as thread:
            thread.is_alive = lambda: True
            result = web_ui.api_concede_now({})

        self.assertTrue(result["ok"])
        self.assertIn("click_concede", self.calls)

    def test_click_failure_is_reported_not_raised(self):
        with patch.object(hs_click, "click_setting",
                          side_effect=RuntimeError("boom")):
            result = web_ui.api_concede_now({})

        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


if __name__ == "__main__":
    unittest.main()
