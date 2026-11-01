# -*- coding: utf-8 -*-
"""「守护」接管存活检测：致命信号交给守护自动恢复，不再先停止 + 告警。

冲突背景（用户 2026-11-01 反馈）：守护（原常驻）开启时，炉石进程消失/对局中
日志卡死会先被存活检测判为 fatal → 「自动化已自动停止」告警并置 quitting_flag；
紧接着守护监督器又重启炉石、恢复对战 → 日志里出现「已自动停止」和「恢复成功」
两套矛盾文案。

修法（三卡合并后）：只要 ``FSM_action._guardian_takes_over()`` 为真（守护
enabled 且非 dry_run、状态在 countdown/starting/running/recovering），**任何**
fatal 事件都只写一条 [SYS] 说明并让位；守护关闭/暂停/配额耗尽/出错/旧版 web_ui
时保持原行为（fatal → 告警 + 停止自动化，最小安全网）。非 fatal 事件只写日志。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import FSM_action
import web_ui

GONE = {"kind": "process_gone", "fatal": True,
        "message": "炉石已退出：Hearthstone.exe 进程消失（已确认 7s 未回来）"}
STALE_STOP = {"kind": "log_stale_stop", "fatal": True,
              "message": "对局中炉石日志已 400s 无响应"}
STALE_WARN = {"kind": "log_stale_warn", "fatal": False,
              "message": "对局中炉石日志已 130s 没更新，请留意"}


class _FakeMonitor:
    def __init__(self, event):
        self._event = event
        self.calls = []

    def check(self, in_game=False, detail=""):
        self.calls.append({"in_game": in_game, "detail": detail})
        return self._event


class GuardianLivenessDelegationTests(unittest.TestCase):
    def setUp(self):
        self._saved = (FSM_action.quitting_flag, FSM_action._liveness_alert,
                       FSM_action.FSM_state)
        FSM_action.quitting_flag = False
        FSM_action._liveness_alert = None
        FSM_action.FSM_state = "Battling"
        FSM_action.shutdown_event.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        (FSM_action.quitting_flag, FSM_action._liveness_alert,
         FSM_action.FSM_state) = self._saved
        FSM_action.shutdown_event.clear()

    def _run(self, event=GONE, will_act=True, with_helper=True):
        logs = []
        patches = [
            patch.object(FSM_action, "hearthstone_liveness", _FakeMonitor(event)),
            patch.object(FSM_action.manual_controller, "output",
                         side_effect=logs.append),
            patch.object(FSM_action, "error_print", side_effect=logs.append),
        ]
        saved_helper = None
        if with_helper:
            patches.append(patch.object(web_ui, "_resident_will_act",
                                        return_value=will_act))
        else:
            # 模拟旧版 web_ui：这个函数根本不存在（不是被 patch 成 True）。
            saved_helper = web_ui.__dict__.pop("_resident_will_act", None)
        try:
            for item in patches:
                item.start()
            result = FSM_action.check_hearthstone_liveness()
        finally:
            for item in reversed(patches):
                item.stop()
            if not with_helper and saved_helper is not None:
                web_ui._resident_will_act = saved_helper
        return result, logs

    # ---------------------------------------------------------- 守护动手 → 让位
    def test_process_gone_is_delegated_when_guardian_is_acting(self):
        event, logs = self._run(will_act=True)

        self.assertIsNotNone(event)
        self.assertEqual("process_gone", event["kind"])
        # 不再「自动停止 + 告警」：交给守护重启恢复。
        self.assertFalse(FSM_action.quitting_flag)
        self.assertFalse(FSM_action.shutdown_event.is_set())
        self.assertIsNone(FSM_action._liveness_alert)
        self.assertTrue(any("交由「守护」自动重启恢复" in line for line in logs), logs)
        self.assertFalse(any("自动化已自动停止" in line for line in logs), logs)

    def test_log_stale_stop_is_also_delegated_when_guardian_is_acting(self):
        """三卡合并后守护接管**所有** fatal（不只 process_gone）。"""
        event, logs = self._run(event=STALE_STOP, will_act=True)

        self.assertIsNotNone(event)
        self.assertEqual("log_stale_stop", event["kind"])
        self.assertFalse(FSM_action.quitting_flag)
        self.assertFalse(FSM_action.shutdown_event.is_set())
        self.assertIsNone(FSM_action._liveness_alert)
        self.assertTrue(any("交由「守护」自动重启恢复" in line for line in logs), logs)

    # ---------------------------------------------------------- 最小安全网
    def test_process_gone_still_stops_when_guardian_is_not_acting(self):
        self._run(will_act=False)

        self.assertTrue(FSM_action.quitting_flag)
        self.assertTrue(FSM_action.shutdown_event.is_set())
        self.assertIn("炉石已退出", FSM_action._liveness_alert or "")

    def test_log_stale_stop_still_stops_when_guardian_is_not_acting(self):
        self._run(event=STALE_STOP, will_act=False)

        self.assertTrue(FSM_action.quitting_flag)
        self.assertTrue(FSM_action.shutdown_event.is_set())
        self.assertIn("400s 无响应", FSM_action._liveness_alert or "")

    def test_old_web_ui_without_helper_keeps_old_behaviour(self):
        self._run(will_act=True, with_helper=False)

        self.assertTrue(FSM_action.quitting_flag)
        self.assertTrue(FSM_action.shutdown_event.is_set())

    # ---------------------------------------------------------- 非 fatal
    def test_non_fatal_event_only_logs(self):
        # 守护会动手时不重复写：提前告警由守护自己按 warn_seconds 发，旧文案
        # 「将自动停止」与守护实际「自动重启恢复」矛盾（R9 复审）。
        _event, logs = self._run(event=STALE_WARN, will_act=True)
        self.assertEqual([], logs)

        # 守护不动手（关闭/暂停/耗尽）→ 最小安全网照旧写一条 [SYS] 提示。
        _event, logs = self._run(event=STALE_WARN, will_act=False)
        self.assertFalse(FSM_action.quitting_flag)
        self.assertFalse(FSM_action.shutdown_event.is_set())
        self.assertIsNone(FSM_action._liveness_alert)
        self.assertTrue(any("[SYS]" in line for line in logs), logs)
        self.assertFalse(any("交由「守护」" in line for line in logs), logs)

    def test_helper_exception_falls_back_to_old_behaviour(self):
        with (
            patch.object(FSM_action, "hearthstone_liveness", _FakeMonitor(GONE)),
            patch.object(FSM_action.manual_controller, "output"),
            patch.object(FSM_action, "error_print"),
            patch.object(web_ui, "_resident_will_act",
                         side_effect=RuntimeError("boom")),
        ):
            self.assertFalse(FSM_action._guardian_takes_over())
            FSM_action.check_hearthstone_liveness()
        self.assertTrue(FSM_action.quitting_flag)
        self.assertIn("炉石已退出", FSM_action._liveness_alert or "")


class GuardianTakesOverUnitTests(unittest.TestCase):
    def test_true_false_and_missing_helper(self):
        with patch.object(web_ui, "_resident_will_act", return_value=True):
            self.assertTrue(FSM_action._guardian_takes_over())
        with patch.object(web_ui, "_resident_will_act", return_value=False):
            self.assertFalse(FSM_action._guardian_takes_over())
        saved = web_ui.__dict__.pop("_resident_will_act", None)
        try:
            self.assertFalse(FSM_action._guardian_takes_over())
        finally:
            if saved is not None:
                web_ui._resident_will_act = saved


if __name__ == "__main__":
    unittest.main()
