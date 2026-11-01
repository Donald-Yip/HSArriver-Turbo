# -*- coding: utf-8 -*-
"""hsctl resident（常驻模式控制面）的离线测试：假控制台替代 web_ui.py。

只验证「命令行参数 → POST/GET /api/resident|status → 渲染/退出码」这条链路。
app 端的判定逻辑与 /api/resident 实现各有自己的测试，这里不重复。
"""

from __future__ import annotations

import json
import unittest

import hsctl
from tests.test_hsctl import STATUS_FIXTURE, _FakeConsole, run_cli

RESIDENT_FIXTURE = {
    "enabled": True,
    "state": "running",
    "mode": "wild",
    "deck_index": 2,
    "stale_in_game_seconds": 180.0,
    "stale_other_seconds": 300.0,
    "warn_seconds": 120.0,
    "max_restarts": 3,
    "window_seconds": 600.0,
    "check_seconds": 5.0,
    "settle_seconds": 120.0,
    "start_delay_seconds": 5.0,
    "dry_run": False,
    "restarts_in_window": 1,
    "restarts_total": 4,
    "restarts_left": 2,
    "settle_left": 0.0,
    "log_age": 12.0,
    "in_game": True,
    "recovering": False,
    "paused_reason": None,
    "waiting_reason": None,
    "last_event": "log_stale",
    "message": "日志停滞 181s，正在重启",
}


def _status_with_resident(**overrides):
    payload = dict(STATUS_FIXTURE)
    payload["resident"] = dict(RESIDENT_FIXTURE, **overrides)
    return payload


class ResidentParserTests(unittest.TestCase):
    """参数面：每个子形态都能解析到对应 handler / 字段。"""

    def setUp(self):
        self.parser = hsctl.build_parser()
        self.console = _FakeConsole({"/api/status": _status_with_resident()})
        self.addCleanup(self.console.close)

    def _parse(self, argv):
        return self.parser.parse_args(argv)

    def _base(self):
        return ["--port", str(self.console.port)]

    def test_bare_resident_defaults_to_status(self):
        ns = self._parse(["resident"])
        self.assertEqual("status", ns.resident_cmd)
        self.assertIs(hsctl.cmd_resident, ns.handler)

    def test_on_parses_mode_and_deck(self):
        ns = self._parse(["resident", "on", "--mode", "wild", "--deck", "2"])
        self.assertEqual("on", ns.resident_cmd)
        self.assertEqual("wild", ns.mode)
        self.assertEqual(2, ns.deck)

    def test_off_parses(self):
        self.assertEqual("off", self._parse(["resident", "off"]).resident_cmd)

    def test_set_parses_every_threshold(self):
        ns = self._parse([
            "resident", "set",
            "--mode", "standard", "--deck", "3",
            "--stale", "120", "--other-stale", "240", "--warn", "90",
            "--max-restarts", "5", "--window", "900",
            "--start-delay", "8", "--dry-run", "on",
        ])
        self.assertEqual("set", ns.resident_cmd)
        self.assertEqual("standard", ns.mode)
        self.assertEqual(3, ns.deck)
        self.assertEqual(120.0, ns.stale)
        self.assertEqual(240.0, ns.other_stale)
        self.assertEqual(90.0, ns.warn)
        self.assertEqual(5, ns.max_restarts)
        self.assertEqual(900.0, ns.window)
        self.assertEqual(8.0, ns.start_delay)
        self.assertEqual("on", ns.dry_run)

    def test_status_accepts_json_after_command(self):
        ns = self._parse(["resident", "status", "--json", *self._base()])
        self.assertTrue(ns.json)

    def test_unknown_subcommand_is_usage_error(self):
        import contextlib
        import io

        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self._parse(["resident", "nonsense"])


class ResidentCommandTests(unittest.TestCase):
    def setUp(self):
        self.console = _FakeConsole({
            "/api/status": _status_with_resident(),
            "/api/resident": {"ok": True, "message": "常驻模式已保存"},
        })
        self.addCleanup(self.console.close)

    def _base(self):
        return ["--port", str(self.console.port)]

    def _posts_to(self, path):
        return [body for method, p, body in self.console.requests
                if method == "POST" and p == path]

    # ---------------------------------------------------------- 读状态
    def test_status_text_reports_everything(self):
        code, out, _ = run_cli([*self._base(), "resident", "status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        for expect in ("守护（无人值守自愈）", "运行中", "狂野", "卡组 2",
                       "180s", "300s", "提前告警", "120s",
                       "3 次", "已用 1 次", "累计 4 次", "上次事件"):
            self.assertIn(expect, out)

    def test_status_text_shows_early_warning_text_if_any(self):
        self.console.httpd.routes["/api/status"] = _status_with_resident(
            warning="对局中日志已停滞 130s（提前告警阈值 120s）")
        code, out, _ = run_cli([*self._base(), "resident", "status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertIn("提前告警", out)
        self.assertIn("已停滞 130s", out)

    def test_status_text_tolerates_old_console_without_warn_seconds(self):
        payload = _status_with_resident()
        del payload["resident"]["warn_seconds"]
        self.console.httpd.routes["/api/status"] = payload
        code, out, _ = run_cli([*self._base(), "resident", "status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertIn("提前告警", out)

    def test_status_json_shape(self):
        code, out, _ = run_cli(["--json", *self._base(), "resident", "status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual("running", payload["resident"]["state"])
        self.assertEqual(2, payload["resident"]["deck_index"])

    def test_status_survives_missing_resident_field(self):
        # 老控制台 / 测试 fixture 可能没有 resident 段：不能崩。
        self.console.httpd.routes["/api/status"] = dict(STATUS_FIXTURE)
        code, out, _ = run_cli([*self._base(), "resident"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertIn("没有提供守护状态", out)

    def test_status_json_missing_field_is_empty_object(self):
        self.console.httpd.routes["/api/status"] = dict(STATUS_FIXTURE)
        code, out, _ = run_cli(["--json", *self._base(), "resident", "status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        payload = json.loads(out)
        self.assertEqual({"ok": True, "resident": {}}, payload)

    def test_hsctl_status_has_a_resident_line(self):
        code, out, _ = run_cli([*self._base(), "status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertIn("守护     开 · 运行中（守候中）", out)
        self.assertIn("提前告警 120s", out)

    def test_hsctl_status_survives_missing_resident_field(self):
        self.console.httpd.routes["/api/status"] = dict(STATUS_FIXTURE)
        code, out, _ = run_cli([*self._base(), "status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertIn("守护", out)

    # ---------------------------------------------------------- 等待定时窗口
    def test_status_text_shows_waiting_schedule_and_reason(self):
        self.console.httpd.routes["/api/status"] = _status_with_resident(
            state="waiting_schedule", waiting_reason="等待定时 23:00 开始",
        )
        code, out, _ = run_cli([*self._base(), "resident", "status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertIn("等待定时窗口", out)
        self.assertIn("等待原因", out)
        self.assertIn("等待定时 23:00 开始", out)

    def test_status_text_tolerates_waiting_state_without_reason(self):
        # 老控制台只给 state、没有 waiting_reason：标签照出，原因行不出现。
        self.console.httpd.routes["/api/status"] = _status_with_resident(
            state="waiting_schedule", waiting_reason=None,
        )
        code, out, _ = run_cli([*self._base(), "resident", "status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertIn("等待定时窗口", out)
        self.assertNotIn("等待原因", out)

    def test_status_text_tolerates_missing_waiting_reason_key(self):
        payload = _status_with_resident(state="waiting_schedule")
        del payload["resident"]["waiting_reason"]
        self.console.httpd.routes["/api/status"] = payload
        code, out, _ = run_cli([*self._base(), "resident", "status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertIn("等待定时窗口", out)

    def test_status_json_passes_waiting_reason_through(self):
        self.console.httpd.routes["/api/status"] = _status_with_resident(
            state="waiting_schedule", waiting_reason="等待定时 23:00 开始",
        )
        code, out, _ = run_cli(["--json", *self._base(), "resident", "status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        resident = json.loads(out)["resident"]
        self.assertEqual("waiting_schedule", resident["state"])
        self.assertEqual("等待定时 23:00 开始", resident["waiting_reason"])

    def test_hsctl_status_line_shows_waiting_reason(self):
        self.console.httpd.routes["/api/status"] = _status_with_resident(
            state="waiting_schedule", waiting_reason="等待定时 23:00 开始",
        )
        code, out, _ = run_cli([*self._base(), "status"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertIn(
            "守护     开 · 等待定时窗口（等待定时 23:00 开始）", out
        )

    # ---------------------------------------------------------- 写开关
    def test_on_posts_enabled_only(self):
        code, out, _ = run_cli([*self._base(), "resident", "on"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertIn("常驻模式已保存", out)
        self.assertEqual([{"enabled": True}], self._posts_to("/api/resident"))

    def test_on_carries_mode_and_deck(self):
        code, _, _ = run_cli([
            *self._base(), "resident", "on", "--mode", "wild", "--deck", "2",
        ])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertEqual(
            [{"enabled": True, "mode": "wild", "deck_index": 2}],
            self._posts_to("/api/resident"),
        )

    def test_on_accepts_chinese_mode_alias(self):
        run_cli([*self._base(), "resident", "on", "--mode", "狂野"])
        self.assertEqual("wild", self._posts_to("/api/resident")[0]["mode"])

    def test_off_posts_disabled(self):
        code, _, _ = run_cli([*self._base(), "resident", "off"])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertEqual([{"enabled": False}], self._posts_to("/api/resident"))

    def test_invalid_deck_is_rejected_before_posting(self):
        code, _, err = run_cli([*self._base(), "resident", "on", "--deck", "12"])
        self.assertEqual(hsctl.EXIT_USAGE, code)
        self.assertIn("1-9", err)
        self.assertEqual([], self._posts_to("/api/resident"))

    # ---------------------------------------------------------- 改参数
    def test_set_maps_threshold_flags(self):
        code, _, _ = run_cli([
            *self._base(), "resident", "set",
            "--mode", "standard", "--deck", "3",
            "--stale", "120", "--other-stale", "240", "--warn", "90",
            "--max-restarts", "5", "--window", "900",
            "--start-delay", "8", "--dry-run", "on",
        ])
        self.assertEqual(hsctl.EXIT_OK, code)
        self.assertEqual(
            [{
                "mode": "standard",
                "deck_index": 3,
                "stale_in_game_seconds": 120.0,
                "stale_other_seconds": 240.0,
                "warn_seconds": 90.0,
                "max_restarts": 5,
                "window_seconds": 900.0,
                "start_delay_seconds": 8.0,
                "dry_run": True,
            }],
            self._posts_to("/api/resident"),
        )

    def test_set_deck_zero_clears_the_deck(self):
        run_cli([*self._base(), "resident", "set", "--deck", "0"])
        self.assertIsNone(self._posts_to("/api/resident")[0]["deck_index"])

    def test_set_without_fields_is_an_error_and_posts_nothing(self):
        code, _, err = run_cli([*self._base(), "resident", "set"])
        self.assertEqual(hsctl.EXIT_API, code)
        self.assertIn("没有要改的字段", err)
        self.assertEqual([], self._posts_to("/api/resident"))

    def test_set_dry_run_off(self):
        run_cli([*self._base(), "resident", "set", "--dry-run", "off"])
        self.assertFalse(self._posts_to("/api/resident")[0]["dry_run"])

    def test_api_failure_maps_to_exit_1(self):
        self.console.httpd.routes["/api/resident"] = {
            "ok": False, "error": "先停止自动化再开启常驻",
        }
        code, _, err = run_cli([*self._base(), "resident", "on"])
        self.assertEqual(hsctl.EXIT_API, code)
        self.assertIn("先停止自动化再开启常驻", err)


if __name__ == "__main__":
    unittest.main()
