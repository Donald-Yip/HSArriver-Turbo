# -*- coding: utf-8 -*-
"""常驻「每次恢复成功」的累计次数：浮窗/日志/状态里要能看到第几次。

背景（用户 2026-11-01）：常驻每次自愈成功后，希望在浮窗里能记录/看到这是本次
运行的第几次恢复。约定：

* 计数只在 **恢复真正成功**（重启炉石 → 重新开始自动化成功）时 +1；
  暂停/取消/失败都不算；
* 次数是 **本次控制台运行** 的累计值（进程重启归零），随 ``/api/status.resident
  的 ``recoveries_total`` 暴露，浮窗「最近事件」与日志里每次都会写出「第 N 次」；
* 监督器的 ``note_recovered(ok, message)`` 支持带次数文案，失败时行为不变。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import log_overlay
import web_ui
from src.safety.resident_supervisor import ResidentSupervisor


def _settings():
    return {
        "enabled": True,
        "mode": "standard",
        "deck_index": None,
        "stale_in_game_seconds": 180.0,
        "stale_other_seconds": 300.0,
        "max_restarts": 3,
        "window_seconds": 600.0,
        "check_seconds": 5.0,
        "settle_seconds": 120.0,
        "start_delay_seconds": 5.0,
        "dry_run": False,
    }


class RecoveryCountTests(unittest.TestCase):
    def setUp(self):
        self._saved_resident = web_ui.RESIDENT
        self._saved_count = web_ui._RESIDENT_RECOVERY_COUNT
        web_ui._RESIDENT_RECOVERY_COUNT = 0

    def tearDown(self):
        web_ui.RESIDENT = self._saved_resident
        web_ui._RESIDENT_RECOVERY_COUNT = self._saved_count

    def test_counter_increments_on_each_success(self):
        self.assertEqual(1, web_ui._resident_note_recovery_success())
        self.assertEqual(2, web_ui._resident_note_recovery_success())
        self.assertEqual(2, web_ui._RESIDENT_RECOVERY_COUNT)

    def test_state_exposes_the_count(self):
        web_ui.RESIDENT = SimpleNamespace(
            state=lambda: {"enabled": True, "state": "running"})
        web_ui._RESIDENT_RECOVERY_COUNT = 4
        self.assertEqual(4, web_ui._resident_state()["recoveries_total"])

    def test_finish_recovery_passes_count_message_to_supervisor(self):
        calls = []

        class _Supervisor:
            def note_recovered(self, ok, message=""):
                calls.append((ok, message))

        web_ui.RESIDENT = _Supervisor()
        web_ui._resident_finish_recovery(
            True, "常驻恢复成功（本次运行第 2 次）：自动化已重新开始，继续挂机。")
        self.assertEqual(1, len(calls))
        ok, message = calls[0]
        self.assertTrue(ok)
        self.assertIn("第 2 次", message)

    def test_finish_recovery_falls_back_for_old_supervisor(self):
        calls = []

        class _OldSupervisor:
            def note_recovered(self, ok):      # 旧签名：只收 ok
                calls.append(ok)

        web_ui.RESIDENT = _OldSupervisor()
        with patch.object(web_ui, "_log"):
            web_ui._resident_finish_recovery(True, "带次数的文案")
        self.assertEqual([True], calls)

    def test_supervisor_message_carries_the_count_and_survives_state(self):
        supervisor = ResidentSupervisor(
            settings=lambda: dict(_settings()),
            log_age=lambda: 1.0,
            in_game=lambda: False,
            process_running=lambda: True,
            automation_running=lambda: True,
            stopped_by=lambda: None,
            start_automation=lambda: True,
            recover=lambda cfg, reason: True,
            alert=lambda message: None,
            clock=lambda: 1000.0,
            sleep=lambda seconds: None,
        )
        supervisor.note_recovered(
            True, "常驻恢复成功（本次运行第 3 次）：自动化已重新开始，继续挂机。")
        state = supervisor.state()
        self.assertEqual("running", state["state"])
        self.assertIn("第 3 次", state["message"])

    def test_failure_message_does_not_mention_a_success_count(self):
        supervisor = ResidentSupervisor(
            settings=lambda: dict(_settings()),
            log_age=lambda: 1.0,
            in_game=lambda: False,
            process_running=lambda: True,
            automation_running=lambda: True,
            stopped_by=lambda: None,
            start_automation=lambda: True,
            recover=lambda cfg, reason: True,
            alert=lambda message: None,
            clock=lambda: 1000.0,
            sleep=lambda seconds: None,
        )
        supervisor.note_recovered(False)
        self.assertIn("恢复失败", supervisor.state()["message"])

    def test_overlay_recent_event_shows_the_count(self):
        text = log_overlay.resident_event_text(
            {"message": "常驻恢复成功（本次运行第 2 次）：自动化已重新开始，继续挂机。"})
        self.assertIn("第 2 次", text)


if __name__ == "__main__":
    unittest.main()
