# -*- coding: utf-8 -*-
"""常驻页文本纯函数（不建窗）：9 种状态、配额/目标/日志/最近事件。"""

from __future__ import annotations

import unittest

import log_overlay
from log_overlay import (
    ACCENT,
    DANGER,
    DIM,
    GREEN,
    resident_event_text,
    resident_log_text,
    resident_quota_text,
    resident_status_view,
    resident_target_text,
)


class ResidentStatusViewTests(unittest.TestCase):
    def _view(self, state, enabled=True, **extra):
        return resident_status_view(dict(extra, enabled=enabled, state=state))

    def test_running_is_green_and_pausable(self):
        view = self._view("running")
        self.assertEqual("运行中", view["state_text"])
        self.assertEqual(GREEN, view["fg"])
        self.assertEqual(GREEN, view["dot"])
        self.assertTrue(view["can_pause"])

    def test_countdown_shows_remaining_seconds(self):
        view = self._view("countdown", countdown_left=3)
        self.assertEqual("倒计时 3s", view["state_text"])
        self.assertEqual(ACCENT, view["fg"])
        self.assertTrue(view["can_pause"])
        # 没有 countdown_left 时退回启动延迟（展示用，不参与判定）。
        self.assertEqual(
            "倒计时 5s", self._view("countdown", start_delay_seconds=5)["state_text"]
        )

    def test_starting_and_recovering_are_accent_and_pausable(self):
        for state, text in (("starting", "启动中"), ("recovering", "恢复中…")):
            with self.subTest(state=state):
                view = self._view(state)
                self.assertEqual(text, view["state_text"])
                self.assertEqual(ACCENT, view["fg"])
                self.assertTrue(view["can_pause"])

    def test_paused_like_states_are_not_pausable(self):
        cases = {
            "paused": ("已暂停", DIM),
            "paused_exhausted": ("配额耗尽", DANGER),
            "error": ("出错", DANGER),
            "cancelled": ("已取消", DIM),
        }
        for state, (text, fg) in cases.items():
            with self.subTest(state=state):
                view = self._view(state)
                self.assertEqual(text, view["state_text"])
                self.assertEqual(fg, view["fg"])
                self.assertEqual(fg, view["dot"])
                self.assertFalse(view["can_pause"])

    def test_disabled_state_is_dim(self):
        view = self._view("disabled")
        self.assertEqual("未开启", view["state_text"])
        self.assertEqual(DIM, view["fg"])
        self.assertFalse(view["can_pause"])

    def test_enabled_false_wins_over_state(self):
        view = resident_status_view(
            {"enabled": False, "state": "running"}
        )
        self.assertEqual("未开启", view["state_text"])
        self.assertEqual(DIM, view["fg"])
        self.assertFalse(view["can_pause"])

    def test_missing_or_bad_state_does_not_crash(self):
        self.assertEqual("未开启", resident_status_view(None)["state_text"])
        self.assertEqual("未开启", resident_status_view({})["state_text"])
        view = resident_status_view({"enabled": True, "state": "weird"})
        self.assertEqual("weird", view["state_text"])
        self.assertFalse(view["can_pause"])


class ResidentTextTests(unittest.TestCase):
    def test_quota(self):
        self.assertEqual(
            "1 / 3",
            resident_quota_text({"restarts_in_window": 1, "max_restarts": 3}),
        )
        self.assertEqual("0 / 0", resident_quota_text({}))
        self.assertEqual(
            "2 / 5",
            resident_quota_text({"restarts_in_window": "2", "max_restarts": "5"}),
        )

    def test_target(self):
        self.assertEqual(
            "狂野 · 卡组 2",
            resident_target_text({"mode": "wild", "deck_index": 2}),
        )
        self.assertEqual(
            "标准 · 卡组 未指定", resident_target_text({"mode": "standard"})
        )
        self.assertEqual(
            "标准 · 卡组 未指定",
            resident_target_text({"deck_index": 0}),
        )
        # 未来新增模式：原样显示，不硬编码成“标准”。
        self.assertEqual(
            "brawl · 卡组 未指定", resident_target_text({"mode": "brawl"})
        )

    def test_log(self):
        self.assertEqual(
            "8s 前 · 对局中",
            resident_log_text({"log_age": 8, "in_game": True}),
        )
        self.assertEqual(
            "— · 其他界面", resident_log_text({"log_age": None})
        )
        self.assertEqual(
            "— · 对局中", resident_log_text({"in_game": True})
        )
        self.assertEqual(
            "13s 前 · 其他界面", resident_log_text({"log_age": 12.6})
        )

    def test_event_prefers_message_then_paused_reason(self):
        self.assertEqual(
            "日志停滞", resident_event_text({"message": "日志停滞",
                                              "paused_reason": "user_stop"})
        )
        self.assertEqual(
            "user_stop", resident_event_text({"paused_reason": "user_stop"})
        )
        self.assertEqual("—", resident_event_text({}))
        self.assertEqual("—", resident_event_text(None))

    def test_event_is_single_line(self):
        text = resident_event_text({"message": "第一行\n第二行\r\n第三行"})
        self.assertNotIn("\n", text)
        self.assertNotIn("\r", text)
        self.assertIn("第一行", text)
        self.assertIn("第三行", text)

    def test_event_long_text_is_truncated_with_ellipsis(self):
        long_text = "常驻自动恢复失败，" * 20
        text = resident_event_text({"message": long_text})
        self.assertLessEqual(len(text), 18)  # 约 18 字，避免“…重新…”半截
        self.assertTrue(text.endswith("…"))
        self.assertNotIn("\n", text)
        # 自定义上限。
        short = resident_event_text({"message": "一二三四五六七八九十"}, limit=6)
        self.assertEqual("一二三四五…", short)

    def test_event_shows_hhmm_prefix_when_available(self):
        text = resident_event_text({
            "message": "常驻自动恢复完成",
            "last_restart_at": "2026-10-31T22:31:05",
        })
        self.assertTrue(text.startswith("22:31 "))
        self.assertIn("常驻自动恢复完成", text)
        # 坏值/缺值不显示前缀，也不能崩。
        self.assertEqual(
            "常驻自动恢复完成", resident_event_text({"message": "常驻自动恢复完成"})
        )
        self.assertEqual(
            "—", resident_event_text({"last_restart_at": "not-a-time"})
        )


class ResidentRuleLinesTests(unittest.TestCase):
    def test_six_lines_follow_the_current_settings(self):
        lines = log_overlay.resident_rule_lines({
            "stale_in_game_seconds": 120,
            "stale_other_seconds": 240,
            "window_seconds": 900,
            "max_restarts": 5,
        })
        self.assertEqual(6, len(lines))
        self.assertIn("120s", lines[0])
        self.assertIn("重启炉石", lines[0])
        self.assertIn("240s", lines[1])
        self.assertIn("重连", lines[2])
        self.assertIn("不盲开局", lines[3])
        self.assertIn("暂停常驻", lines[4])
        self.assertIn("15 分钟", lines[5])
        self.assertIn("5 次", lines[5])

    def test_defaults_and_bad_input(self):
        lines = log_overlay.resident_rule_lines({})
        self.assertEqual(6, len(lines))
        self.assertIn("180s", lines[0])
        self.assertIn("300s", lines[1])
        self.assertIn("10 分钟", lines[5])
        self.assertIn("3 次", lines[5])
        self.assertEqual(6, len(log_overlay.resident_rule_lines(None)))


if __name__ == "__main__":
    unittest.main()
