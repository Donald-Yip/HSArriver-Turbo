# -*- coding: utf-8 -*-
"""hsctl（命令行控制台）的离线测试：用一个假 HTTP 服务替代 web_ui.py。

不依赖 FSM_action / OCR 等重组件，也不碰真机配置 —— 只验证「命令行参数 → HTTP
请求 → 渲染/退出码」这条链路，这是 agent 能否可靠驱动脚本的关键。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
import unittest.mock
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import hsctl


def _free_port() -> int:
    """拿一个当前空闲的端口（用于断言「连不上」的路径）。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


STATUS_FIXTURE = {
    "server_time": "2026-05-30T22:31:05",
    "is_admin": True,
    "running": True,
    "starting": False,
    "prepared": True,
    "overlay_running": True,
    "phase": "playing",
    "stopped_by": None,
    "stop_after_game": False,
    "state": "Battling",
    "state_label": "对战中",
    "in_game": True,
    "games": 12,
    "wins": 8,
    "concede_count": 1,
    "win_rate": 66.7,
    "game_elapsed_sec": 200,
    "schedule_start": None,
    "schedule_end": None,
    "concede": {"enabled": True, "threshold": 15.0, "rounds": 3},
    "concede_detect": {"enabled": True, "threshold": 15.0, "rounds": 3,
                       "rate": 12.0, "streak": 2, "checked_turn": 7,
                       "triggered": False},
    "rank_stop": {"enabled": False, "mode": "legend", "legend_number": 1000,
                  "last_number": None, "last_legend": None, "last_raw": None,
                  "last_empty": False, "triggered": False, "stop_reason": None},
    "human_like": {
        "enabled": True, "hand_hover_enabled": True, "minion_hover_enabled": True,
        "post_delay_min": 0.5, "post_delay_max": 3.0, "hover_min": 0.5,
        "hover_max": 1.0, "minion_hover_min": 4.0, "minion_hover_max": 5.0,
    },
    "liveness": {"enabled": True, "status": "ok", "process": "running",
                 "log_age": 40, "alert": None,
                 "process_grace_seconds": 6.0,
                 "log_stale_warn_seconds": 120.0,
                 "log_stale_stop_seconds": 300.0},
    "liveness_alert": None,
    "name_match": {"config": "Priscilla#51346", "players": {}, "matched": True},
    "delays": {
        "mulligan_ready_delay_seconds": 20.0,
        "mulligan_post_ocr_delay_seconds": 1.0,
        "mulligan_retry_delay_seconds": 5.0,
        "first_turn_per_card_delay_seconds": 3.5,
        "pre_action_delay_seconds": 7.0,
        "post_action_delay_seconds": 0.2,
        "draw_extra_delay_per_card_seconds": 1.0,
        "discover_target_delay_seconds": 2.0,
        "ocr_preprocess_scale": 1.4,
    },
    "selfcheck": {"checked": True, "ok": True, "failed": 0, "warned": 0,
                  "summary": "全部通过", "checked_at": "2026-05-30T22:30:00",
                  "python": "3.12.14"},
    "config": {"name": "Priscilla#51346",
               "log_root": "D:\\Battle.net\\Hearthstone\\Logs"},
    "last_error": None,
    "last_summary": None,
    "hotkey_ok": True,
}


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeHS/1.0"

    def log_message(self, *args):        # 静默
        pass

    def _reply(self, payload, code=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        self.server.requests.append(("GET", self.path, None))
        self._reply(self.server.routes.get(path, {"ok": True}))

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf8")) if raw else {}
        except Exception:
            body = {"__unparsable__": raw.decode("utf8", "replace")}
        self.server.requests.append(("POST", self.path, body))
        self._reply(self.server.routes.get(self.path, {"ok": True}))


class _FakeConsole:
    """假控制台：注册路由 + 记录收到的请求。"""

    def __init__(self, routes=None):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.routes = dict(routes or {})
        self.httpd.requests = []
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()

    @property
    def requests(self):
        return self.httpd.requests

    def posts(self):
        return [r for r in self.requests if r[0] == "POST"]

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def run_cli(argv):
    """跑一次 hsctl，返回 (退出码, stdout, stderr)。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = hsctl.main(argv)
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.console = _FakeConsole({"/api/status": dict(STATUS_FIXTURE)})
        self.addCleanup(self.console.close)

    def _base(self):
        return ["--port", str(self.console.port)]

    # ------------------------------------------------ 基础链路
    def test_status_json_roundtrip(self):
        code, out, _ = run_cli(["--json", *self._base(), "status"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertEqual(json.loads(out)["phase"], "playing")

    def test_json_flag_works_after_the_command_too(self):
        code, out, _ = run_cli(["status", "--json", *self._base()])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("phase", json.loads(out))

    def test_status_human_render(self):
        code, out, _ = run_cli([*self._base(), "status"])
        self.assertEqual(code, hsctl.EXIT_OK)
        for expect in ("运行中", "Priscilla#51346", "匹配", "12 场 8 胜",
                       "自动投降", "活人感", "延时", "自检"):
            self.assertIn(expect, out)

    def test_status_render_survives_empty_payload(self):
        # 控制台刚起来、字段还没填满时不能把 CLI 打崩。
        self.console.httpd.routes["/api/status"] = {}
        code, out, _ = run_cli([*self._base(), "status"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("未运行", out)

    # ------------------------------------------------ 退出码
    def test_api_failure_maps_to_exit_1(self):
        self.console.httpd.routes["/api/start"] = {
            "ok": False, "error": "自动化已经在运行中。"}
        code, _, err = run_cli([*self._base(), "start"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("自动化已经在运行中。", err)

    def test_api_failure_json_is_machine_readable_on_stdout(self):
        self.console.httpd.routes["/api/start"] = {"ok": False, "error": "nope"}
        code, out, _ = run_cli(["--json", *self._base(), "start"])
        self.assertEqual(code, hsctl.EXIT_API)
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["kind"], "api")

    def test_unreachable_maps_to_exit_3(self):
        code, out, _ = run_cli(["--json", "--port", str(_free_port()), "status"])
        self.assertEqual(code, hsctl.EXIT_UNREACHABLE)
        self.assertEqual(json.loads(out)["kind"], "transport")

    def test_unreachable_message_points_at_serve_start(self):
        code, _, err = run_cli(["--port", str(_free_port()), "status"])
        self.assertEqual(code, hsctl.EXIT_UNREACHABLE)
        self.assertIn("hsctl serve start", err)

    # ------------------------------------------------ 对战控制
    def test_stop_after_game_body(self):
        self.console.httpd.routes["/api/stop"] = {
            "ok": True, "message": "本局对战结束后将自动停止"}
        code, out, _ = run_cli([*self._base(), "stop", "--after-game"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("本局对战结束后将自动停止", out)
        self.assertEqual(self.console.posts()[-1][2], {"mode": "after_game"})

    def test_stop_defaults_to_immediate(self):
        self.console.httpd.routes["/api/stop"] = {"ok": True, "message": "ok"}
        run_cli([*self._base(), "stop"])
        self.assertEqual(self.console.posts()[-1][2], {"mode": "now"})

    def test_start_defaults_to_reset_score(self):
        self.console.httpd.routes["/api/start"] = {"ok": True, "message": "ok"}
        run_cli([*self._base(), "start"])
        self.assertEqual(self.console.posts()[-1][2], {"keep_score": False})

    def test_resume_keeps_score(self):
        self.console.httpd.routes["/api/start"] = {"ok": True, "message": "恢复"}
        run_cli([*self._base(), "resume"])
        self.assertEqual(self.console.posts()[-1][2], {"keep_score": True})

    def test_prepare_posts_empty_body(self):
        self.console.httpd.routes["/api/prepare"] = {"ok": True, "message": "就绪"}
        run_cli([*self._base(), "prepare"])
        self.assertEqual(self.console.posts()[-1], ("POST", "/api/prepare", {}))

    # ------------------------------------------------ 参数热更新
    def test_delays_set_sends_hot_and_numbers(self):
        self.console.httpd.routes["/api/delays"] = {
            "ok": True, "message": "延时配置已保存",
            "effects": {"pre_action_delay_seconds": "live"}}
        code, _, _ = run_cli([*self._base(), "delays", "set",
                              "pre_action_delay_seconds=5",
                              "post_action_delay_seconds=0.2"])
        self.assertEqual(code, hsctl.EXIT_OK)
        body = self.console.posts()[-1][2]
        self.assertIs(body["hot"], True)
        self.assertEqual(body["pre_action_delay_seconds"], 5.0)
        self.assertEqual(body["post_action_delay_seconds"], 0.2)

    def test_delays_set_no_hot(self):
        self.console.httpd.routes["/api/delays"] = {"ok": True, "message": "ok"}
        run_cli([*self._base(), "delays", "set", "--no-hot",
                 "pre_action_delay_seconds=1"])
        self.assertIs(self.console.posts()[-1][2]["hot"], False)

    def test_delays_set_rejects_unknown_key_before_sending(self):
        code, _, err = run_cli([*self._base(), "delays", "set", "nope=1"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("未知延时字段", err)
        self.assertEqual(self.console.posts(), [])

    def test_delays_set_rejects_non_numeric(self):
        code, _, err = run_cli([*self._base(), "delays", "set",
                                "pre_action_delay_seconds=abc"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("必须是数字", err)
        self.assertEqual(self.console.posts(), [])

    def test_delays_show_labels_effect_scope(self):
        code, out, _ = run_cli([*self._base(), "delays"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("立即生效", out)          # pre_action 等
        self.assertIn("下次「开始对战」生效", out)  # post_action 等

    def test_human_like_set_sends_only_given_fields(self):
        self.console.httpd.routes["/api/human_like"] = {"ok": True, "message": "ok"}
        run_cli([*self._base(), "human-like", "set", "--enabled",
                 "--post-delay-min", "0.5"])
        self.assertEqual(self.console.posts()[-1][2],
                         {"hot": True, "enabled": True, "post_delay_min": 0.5})

    def test_human_like_set_without_fields_is_an_error(self):
        code, _, err = run_cli([*self._base(), "human-like", "set"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("没有要改的字段", err)

    def test_concede_set(self):
        self.console.httpd.routes["/api/concede"] = {"ok": True, "message": "ok"}
        run_cli([*self._base(), "concede", "set", "--threshold", "12",
                 "--rounds", "4"])
        self.assertEqual(self.console.posts()[-1][2],
                         {"hot": True, "threshold": 12.0, "rounds": 4})

    def test_rank_stop_set(self):
        self.console.httpd.routes["/api/rank_stop"] = {"ok": True, "message": "ok"}
        run_cli([*self._base(), "rank-stop", "set", "--enabled",
                 "--mode", "legend_number", "--legend-number", "500"])
        self.assertEqual(self.console.posts()[-1][2],
                         {"hot": True, "enabled": True,
                          "mode": "legend_number", "legend_number": 500})

    def test_liveness_shows_current_thresholds(self):
        code, out, _ = run_cli([*self._base(), "liveness"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("120", out)
        self.assertIn("300", out)

    # ------------------------------------------------ 浮窗 / 定时 / 校准
    def test_overlay_on_when_already_on_is_a_noop(self):
        code, out, _ = run_cli([*self._base(), "overlay", "on"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("已经开着", out)
        self.assertEqual(self.console.posts(), [])

    def test_overlay_off_posts(self):
        self.console.httpd.routes["/api/overlay"] = {
            "ok": True, "message": "日志浮窗已关闭"}
        code, _, _ = run_cli([*self._base(), "overlay", "off"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertEqual([r[1] for r in self.console.posts()], ["/api/overlay"])

    def test_overlay_toggle_always_posts(self):
        self.console.httpd.routes["/api/overlay"] = {"ok": True, "message": "ok"}
        run_cli([*self._base(), "overlay", "toggle"])
        self.assertEqual(len(self.console.posts()), 1)

    def test_schedule_at_in_minutes(self):
        self.console.httpd.routes["/api/schedule"] = {
            "ok": True, "message": "定时任务已设置"}
        code, out, _ = run_cli([*self._base(), "schedule", "at", "--in", "30"])
        self.assertEqual(code, hsctl.EXIT_OK)
        body = self.console.posts()[-1][2]
        datetime.fromisoformat(body["start"])       # 必须是合法 ISO
        self.assertIn("定时任务已设置", out)

    def test_schedule_at_duration_sets_end(self):
        self.console.httpd.routes["/api/schedule"] = {"ok": True, "message": "ok"}
        run_cli([*self._base(), "schedule", "at", "07:30", "--duration", "90"])
        body = self.console.posts()[-1][2]
        start = datetime.fromisoformat(body["start"])
        end = datetime.fromisoformat(body["end"])
        self.assertEqual((end - start).total_seconds(), 90 * 60)

    def test_schedule_rejects_unparsable_time(self):
        code, _, err = run_cli([*self._base(), "schedule", "at", "今晚八点"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("看不懂的时间", err)

    def test_schedule_cancel(self):
        self.console.httpd.routes["/api/schedule/cancel"] = {
            "ok": True, "message": "定时任务已取消"}
        code, out, _ = run_cli([*self._base(), "schedule", "cancel"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("已取消", out)

    def test_schedule_show_when_none(self):
        code, out, _ = run_cli([*self._base(), "schedule"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("（无）", out)

    def test_calibrate(self):
        self.console.httpd.routes["/api/calibrate"] = {
            "ok": True, "message": "校准窗口已启动"}
        code, out, _ = run_cli([*self._base(), "calibrate"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("校准窗口已启动", out)

    # ------------------------------------------------ 日志
    def test_log_tail_lines(self):
        self.console.httpd.routes["/api/logs"] = {"ok": True, "seq": 3, "lines": [
            {"seq": 1, "t": "10:00:00", "level": "SYS", "msg": "第一条"},
            {"seq": 2, "t": "10:00:01", "level": "SYS", "msg": "第二条"},
            {"seq": 3, "t": "10:00:02", "level": "WARN", "msg": "第三条"}]}
        code, out, _ = run_cli([*self._base(), "log", "-n", "2"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertNotIn("第一条", out)
        self.assertIn("第三条", out)
        self.assertIn("[ WARN]", out)

    def test_log_json_is_one_object_per_line(self):
        self.console.httpd.routes["/api/logs"] = {"ok": True, "seq": 1, "lines": [
            {"seq": 1, "t": "10:00:00", "level": "SYS", "msg": "hi"}]}
        code, out, _ = run_cli(["--json", *self._base(), "log"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertEqual(json.loads(out.strip())["msg"], "hi")

    # ------------------------------------------------ 高风险命令
    def test_restart_hearthstone_requires_explicit_confirmation(self):
        fake_stdin = unittest.mock.MagicMock()
        fake_stdin.isatty.return_value = False
        with unittest.mock.patch("sys.stdin", fake_stdin):
            code, _, err = run_cli([*self._base(), "restart-hearthstone"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("--yes", err)
        self.assertEqual(self.console.posts(), [])

    def test_restart_hearthstone_prompt_at_eof_cancels(self):
        """isatty 为真但 stdin 已经 EOF（agent/重定向）时必须按「不确认」处理。"""
        fake_stdin = unittest.mock.MagicMock()
        fake_stdin.isatty.return_value = True
        with unittest.mock.patch("sys.stdin", fake_stdin), \
                unittest.mock.patch("builtins.input", side_effect=EOFError):
            code, out, _ = run_cli([*self._base(), "restart-hearthstone"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("已取消", out)
        self.assertEqual(self.console.posts(), [])

    def test_restart_hearthstone_with_yes_sends_confirm(self):
        """--yes：派发后继续等到主界面，完成才返回 0。"""
        self.console.httpd.routes["/api/restart_hearthstone"] = {
            "ok": True, "message": "已开始重启炉石（……等主界面）"}
        finished = {"ok": True, "restart": {
            "running": False, "stage": "main_menu", "ok": True,
            "message": "已重启并到达主界面（「传统对战」可点）"}}
        with unittest.mock.patch.object(hsctl.Ctx, "get",
                                        return_value=finished):
            code, out, _ = run_cli([*self._base(), "restart-hearthstone", "--yes"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("已开始重启炉石", out)
        self.assertIn("传统对战", out)
        self.assertEqual(self.console.posts()[-1][2], {"confirm": True})

    def test_restart_hearthstone_reports_main_menu_timeout(self):
        """进程起来了但没到主界面 = 失败（不能"派发即成功"）。"""
        self.console.httpd.routes["/api/restart_hearthstone"] = {
            "ok": True, "message": "已开始重启炉石"}
        timed_out = {"ok": True, "restart": {
            "running": False, "stage": "main_menu_timeout", "ok": False,
            "message": "炉石已重启，但 180s 内没到主界面（最近一次匹配分 0.29）"}}
        with unittest.mock.patch.object(hsctl.Ctx, "get",
                                        return_value=timed_out):
            code, out, _ = run_cli([*self._base(), "restart-hearthstone", "--yes"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("没到主界面", out)

    def test_restart_hearthstone_no_wait_skips_polling(self):
        self.console.httpd.routes["/api/restart_hearthstone"] = {
            "ok": True, "message": "已开始重启炉石"}
        with unittest.mock.patch.object(hsctl.Ctx, "get",
                                        side_effect=AssertionError("不该轮询")):
            code, out, _ = run_cli([*self._base(), "restart-hearthstone",
                                    "--yes", "--no-wait"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("已开始重启炉石", out)

    # ------------------------------------------------ 抓图（看画面）
    def test_screenshot_defaults_to_client_kind(self):
        sent = []

        def fake_post(path, body=None):
            sent.append((path, body))
            return {"ok": True, "path": r"C:\tmp\a.png", "latest": r"C:\tmp\l.png",
                    "width": 1920, "height": 1080, "kind": "client",
                    "note": "炉石客户区（脚本操作的正是这块）"}

        with unittest.mock.patch.object(hsctl.Ctx, "post",
                                        side_effect=fake_post):
            code, out, _ = run_cli([*self._base(), "screenshot"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertEqual(sent[0][0], "/api/screenshot")
        self.assertEqual(sent[0][1], {"kind": "client"})
        self.assertIn(r"C:\tmp\a.png", out)
        self.assertIn("1920×1080", out)

    def test_screenshot_passes_kind_and_max_width(self):
        sent = []

        def fake_post(path, body=None):
            sent.append(body)
            return {"ok": True, "path": "p", "latest": "l", "width": 800,
                    "height": 450, "kind": "boxes", "note": ""}

        with unittest.mock.patch.object(hsctl.Ctx, "post",
                                        side_effect=fake_post):
            run_cli([*self._base(), "screenshot", "--kind", "boxes",
                     "--max-width", "800", "--out", "x.png"])
        self.assertEqual(sent[0], {"kind": "boxes", "max_width": 800,
                                   "out": "x.png"})

    def test_screenshot_shows_fallback_reason(self):
        def fake_post(path, body=None):
            return {"ok": True, "path": "p", "latest": "l", "width": 8,
                    "height": 4, "kind": "client",
                    "note": "炉石窗口不可见或已最小化（0×0）；已退化为整屏截图"}

        with unittest.mock.patch.object(hsctl.Ctx, "post",
                                        side_effect=fake_post):
            code, out, _ = run_cli([*self._base(), "screenshot"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("最小化", out)

    def test_screenshot_json_mode(self):
        def fake_post(path, body=None):
            return {"ok": True, "path": "p", "width": 8, "height": 4}

        with unittest.mock.patch.object(hsctl.Ctx, "post",
                                        side_effect=fake_post):
            code, out, _ = run_cli(["--json", *self._base(), "screenshot"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertEqual(json.loads(out)["width"], 8)

    def test_screenshot_api_failure_maps_to_exit_1(self):
        with unittest.mock.patch.object(
                hsctl.Ctx, "post",
                side_effect=hsctl.ApiFailure("不支持的抓图类型：nope")):
            code, _, err = run_cli([*self._base(), "screenshot", "--kind",
                                    "full"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("不支持的抓图类型", err)

    def test_screenshot_watch_overwrites_one_file(self):
        bodies = []

        def fake_post(path, body=None):
            bodies.append(body)
            return {"ok": True, "path": r"C:\tmp\agent_live.png",
                    "latest": "l", "width": 8, "height": 4, "kind": "client",
                    "note": ""}

        with unittest.mock.patch.object(hsctl.Ctx, "post",
                                        side_effect=fake_post), \
                unittest.mock.patch.object(hsctl.time, "sleep",
                                           side_effect=KeyboardInterrupt):
            code, out, _ = run_cli([*self._base(), "screenshot", "--watch"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertEqual(len(bodies), 1)
        self.assertEqual(bodies[0]["out"], "agent_live.png")
        self.assertIs(bodies[0]["quiet"], True, "跟随抓图要带 quiet，别刷满网页日志")
        self.assertIn("agent_live.png", out)

    # ------------------------------------------------ serve
    def test_serve_status_reports_running(self):
        code, out, _ = run_cli(["--json", *self._base(), "serve", "status"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertTrue(json.loads(out)["running"])

    def test_serve_status_when_nothing_is_running(self):
        code, out, _ = run_cli(["--json", "--port", str(_free_port()),
                                "serve", "status"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertFalse(json.loads(out)["running"])


class ServeStopSafetyTests(unittest.TestCase):
    """serve stop 的安全性：端口上没人监听时，绝不能去 kill 残留的 pid。

    端口文件是"上次运行的产物"，进程被硬杀时会残留；里面的 pid 可能已经被
    系统复用给别的进程。所以「没在监听」是权威结论，只清理文件、不动进程。
    """

    _TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp-tests"

    def test_stale_port_file_is_cleaned_without_killing_anything(self):
        self._TMP_ROOT.mkdir(exist_ok=True)
        stale_port = _free_port()
        stale_path = self._TMP_ROOT / f"ui_port_stale_{os.getpid()}.txt"
        stale_path.write_text(json.dumps({"port": stale_port,
                                          "host": "127.0.0.1",
                                          "pid": 999999}), encoding="utf8")
        self.addCleanup(lambda: stale_path.unlink(missing_ok=True))
        killed = []
        with unittest.mock.patch.object(
                hsctl, "read_port_file",
                return_value={"port": stale_port, "host": "127.0.0.1",
                              "pid": 999999}), \
                unittest.mock.patch.object(hsctl, "PORT_FILE", stale_path), \
                unittest.mock.patch.object(
                    hsctl, "PID_FILE", self._TMP_ROOT / "no_such.pid"), \
                unittest.mock.patch.object(
                    hsctl.os, "kill",
                    side_effect=lambda *a: killed.append(a)):
            code, out, _ = run_cli(["--port", str(stale_port),
                                    "serve", "stop"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("没在运行", out)
        self.assertEqual(killed, [], "端口没人监听时不能去 kill 残留 pid")
        self.assertFalse(stale_path.exists(), "残留的端口文件应被清理")


class TaskHintTests(unittest.TestCase):
    """计划任务的命令拼装。

    踩过的坑：`/tr` 本身要用引号包住，如果里面再嵌 `cmd /c cd /d "路径" && ...`
    这种带引号的命令，用户粘到 cmd 里会被内层引号提前截断 —— 命令根本跑不通。
    所以任务动作改成一个启动器脚本，`/tr` 只留一个普通路径。
    """

    _TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp-tests"

    def setUp(self):
        self._TMP_ROOT.mkdir(exist_ok=True)
        launcher = self._TMP_ROOT / f"hs_task_{os.getpid()}.cmd"
        patcher = unittest.mock.patch.object(hsctl, "TASK_LAUNCHER", launcher)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: launcher.unlink(missing_ok=True))
        self.launcher = launcher

    def test_action_is_a_path_not_an_inline_command(self):
        action = hsctl._task_action()
        self.assertNotIn("&&", action, "任务动作里不该出现 &&（会逼出嵌套引号）")
        self.assertEqual(action, str(self.launcher))

    def test_hint_command_has_exactly_one_level_of_quoting(self):
        hint = hsctl._task_create_hint()
        # 形如：schtasks /create /tn "名" /tr "路径" /sc ONCE /st 00:00 /rl HIGHEST /f
        self.assertEqual(hint.count('"'), 4, f"引号层数不对：{hint}")
        for token in ("/tn", "/tr", "/sc", "ONCE", "/st", "00:00",
                      "/rl", "HIGHEST", "/f"):
            self.assertIn(token, hint)
        self.assertNotIn("&&", hint)

    def test_argv_requests_highest_privileges(self):
        argv = hsctl._task_create_argv()
        self.assertIn("/rl", argv)
        self.assertEqual(argv[argv.index("/rl") + 1], "HIGHEST")
        self.assertEqual(argv[argv.index("/tn") + 1], hsctl.TASK_NAME)

    def test_launcher_is_written_with_clean_crlf(self):
        hsctl._ensure_task_launcher()
        raw = self.launcher.read_bytes()
        self.assertNotIn(b"\r\r\n", raw, "write_text 的换行翻译会把 \\r\\n 变成 \\r\\r\\n")
        self.assertIn(b"\r\n", raw)
        text = raw.decode("utf8")
        self.assertIn("serve start --wait 120", text)
        self.assertIn("chcp 65001", text)          # 让中文输出在任务窗口里正常显示
        self.assertIn(str(hsctl.ROOT), text)

    def test_launcher_reruns_write_is_idempotent(self):
        hsctl._ensure_task_launcher()
        first = self.launcher.read_bytes()
        hsctl._ensure_task_launcher()
        self.assertEqual(first, self.launcher.read_bytes())


class ElevationTests(unittest.TestCase):
    """提权两条路：--elevate（弹 UAC）与 --via-task（不弹 UAC）。

    测试里**绝不能**碰到真的 ShellExecuteW —— 那会在用户屏幕上弹出一个真的 UAC
    框。所有路径都 patch 掉。
    """

    def setUp(self):
        self.console = _FakeConsole({"/api/status": dict(STATUS_FIXTURE)})
        self.addCleanup(self.console.close)
        self.base = ["--port", str(self.console.port)]
        # 计划任务启动器也必须重定向：否则 install-task / --via-task 的用例会把
        # log\hs_serve_elevated.cmd 写进真实仓库（测试不该有这种副作用）。
        tmp_root = Path(__file__).resolve().parent.parent / ".tmp-tests"
        tmp_root.mkdir(exist_ok=True)
        launcher = tmp_root / f"hs_task_elev_{os.getpid()}.cmd"
        for target, attr, value in (
                (hsctl, "TASK_LAUNCHER", launcher),
                (hsctl, "PORT_FILE", tmp_root / f"ui_port_elev_{os.getpid()}.txt"),
                (hsctl, "PID_FILE", tmp_root / f"ui_pid_elev_{os.getpid()}.pid")):
            patcher = unittest.mock.patch.object(target, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lambda: launcher.unlink(missing_ok=True))
        # 让 start 之前的"已在运行就跳过"探活失败，从而真的走到启动分支
        patcher = unittest.mock.patch.object(hsctl.Ctx, "_probe",
                                            return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---- --elevate
    def test_elevate_denied_gives_clear_message(self):
        with unittest.mock.patch.object(hsctl, "_launch_runas",
                                        return_value=(False, 5)):
            code, _, err = run_cli([*self.base, "serve", "start", "--elevate",
                                    "--wait", "1"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("提权被拒绝", err)

    def test_elevate_other_failure_reports_code(self):
        with unittest.mock.patch.object(hsctl, "_launch_runas",
                                        return_value=(False, 31)):
            code, _, err = run_cli([*self.base, "serve", "start", "--elevate",
                                    "--wait", "1"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("31", err)

    def test_elevate_success_waits_for_console(self):
        with unittest.mock.patch.object(hsctl, "_launch_runas",
                                        return_value=(True, 42)), \
                unittest.mock.patch.object(hsctl, "_wait_for_console",
                                           return_value="http://127.0.0.1:9999"), \
                unittest.mock.patch.object(hsctl, "_report_ready") as ready:
            code, out, _ = run_cli([*self.base, "serve", "start", "--elevate",
                                    "--wait", "1"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("UAC", out)
        ready.assert_called_once_with("http://127.0.0.1:9999")

    def test_elevate_not_ready_reports(self):
        with unittest.mock.patch.object(hsctl, "_launch_runas",
                                        return_value=(True, 42)), \
                unittest.mock.patch.object(hsctl, "_wait_for_console",
                                           return_value=None):
            code, _, err = run_cli([*self.base, "serve", "start", "--elevate",
                                    "--wait", "1"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("没有就绪", err)

    # ---- --via-task
    def test_via_task_without_task_gives_admin_hint(self):
        with unittest.mock.patch.object(hsctl, "_task_state",
                                        return_value={"exists": False}):
            code, _, err = run_cli([*self.base, "serve", "start", "--via-task",
                                    "--wait", "1"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("计划任务还没注册", err)
        self.assertIn("/rl HIGHEST", err)

    def test_via_task_triggers_schtasks_run(self):
        calls = []

        def fake_run(argv):
            calls.append(argv)
            return 0, ""

        with unittest.mock.patch.object(
                hsctl, "_task_state",
                return_value={"exists": True, "highest": True}), \
                unittest.mock.patch.object(hsctl, "_run_quiet",
                                           side_effect=fake_run), \
                unittest.mock.patch.object(hsctl, "_wait_for_console",
                                           return_value="http://127.0.0.1:8765"), \
                unittest.mock.patch.object(hsctl, "_report_ready"):
            code, out, _ = run_cli([*self.base, "serve", "start", "--via-task",
                                    "--wait", "1"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertEqual(calls, [["schtasks", "/run", "/tn", hsctl.TASK_NAME]])
        self.assertIn("不弹 UAC", out)

    def test_via_task_warns_when_not_highest(self):
        with unittest.mock.patch.object(
                hsctl, "_task_state",
                return_value={"exists": True, "highest": False}), \
                unittest.mock.patch.object(hsctl, "_run_quiet",
                                           return_value=(0, "")), \
                unittest.mock.patch.object(hsctl, "_wait_for_console",
                                           return_value="http://127.0.0.1:8765"), \
                unittest.mock.patch.object(hsctl, "_report_ready"):
            code, out, _ = run_cli([*self.base, "serve", "start", "--via-task",
                                    "--wait", "1"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("最高权限", out)

    def test_via_task_run_failure_reports(self):
        with unittest.mock.patch.object(
                hsctl, "_task_state",
                return_value={"exists": True, "highest": True}), \
                unittest.mock.patch.object(hsctl, "_run_quiet",
                                           return_value=(1, "拒绝访问")):
            code, _, err = run_cli([*self.base, "serve", "start", "--via-task",
                                    "--wait", "1"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("触发计划任务失败", err)
        self.assertIn("PowerShell", err)

    # ---- schtasks 被策略拦掉 → PowerShell 兜底
    def test_run_task_now_falls_back_to_powershell(self):
        """有的机器把 schtasks.exe 列进黑名单：必须自动改走 Start-ScheduledTask。"""
        calls = []

        def fake_run(argv):
            calls.append(argv)
            if argv[0] == "schtasks":
                return 1, "Access is denied."
            return 0, ""

        with unittest.mock.patch.object(hsctl, "_run_quiet",
                                        side_effect=fake_run):
            result = hsctl._run_task_now()

        self.assertTrue(result["ok"])
        self.assertEqual("powershell", result["mechanism"])
        self.assertIn("schtasks", calls[0][0])
        self.assertEqual("powershell", calls[1][0])
        command = " ".join(calls[1])
        self.assertIn("Start-ScheduledTask", command)
        self.assertIn(hsctl.TASK_NAME, command)

    def test_run_task_now_prefers_schtasks(self):
        calls = []

        def fake_run(argv):
            calls.append(argv)
            return 0, ""

        with unittest.mock.patch.object(hsctl, "_run_quiet",
                                        side_effect=fake_run):
            result = hsctl._run_task_now()

        self.assertTrue(result["ok"])
        self.assertEqual("schtasks", result["mechanism"])
        self.assertEqual(1, len(calls))

    def test_run_task_now_reports_both_failures(self):
        with unittest.mock.patch.object(hsctl, "_run_quiet",
                                        return_value=(1, "拦了")):
            result = hsctl._run_task_now()

        self.assertFalse(result["ok"])
        self.assertIn("schtasks", result["detail"])
        self.assertIn("PowerShell", result["detail"])

    def test_via_task_uses_the_powershell_fallback(self):
        with unittest.mock.patch.object(
                hsctl, "_task_state",
                return_value={"exists": True, "highest": True}), \
                unittest.mock.patch.object(
                    hsctl, "_run_task_now",
                    return_value={"ok": True, "mechanism": "powershell",
                                  "detail": "schtasks 不可用"}), \
                unittest.mock.patch.object(hsctl, "_wait_for_console",
                                           return_value="http://127.0.0.1:8765"), \
                unittest.mock.patch.object(hsctl, "_report_ready"):
            code, out, _ = run_cli([*self.base, "serve", "start", "--via-task",
                                    "--wait", "1"])

        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("PowerShell", out)

    # ---- install-task / uninstall-task / task-status
    def test_install_task_without_admin_prints_command_and_does_nothing(self):
        ran = []
        with unittest.mock.patch.object(hsctl, "is_user_admin",
                                        return_value=False), \
                unittest.mock.patch.object(
                    hsctl, "_run_quiet",
                    side_effect=lambda argv: (ran.append(argv), (0, ""))[1]), \
                unittest.mock.patch.object(hsctl, "_task_state",
                                           return_value={"exists": False}):
            code, out, _ = run_cli([*self.base, "serve", "install-task"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("管理员", out)
        self.assertIn("/rl HIGHEST", out)
        self.assertEqual(ran, [], "非管理员时不该真的去建任务")

    def test_install_task_with_admin_creates_it(self):
        ran = []
        with unittest.mock.patch.object(hsctl, "is_user_admin",
                                        return_value=True), \
                unittest.mock.patch.object(
                    hsctl, "_run_quiet",
                    side_effect=lambda argv: (ran.append(argv), (0, ""))[1]), \
                unittest.mock.patch.object(
                    hsctl, "_task_state",
                    side_effect=[{"exists": False},
                                 {"exists": True, "highest": True}]):
            code, out, _ = run_cli([*self.base, "serve", "install-task"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertEqual(len(ran), 1)
        self.assertEqual(ran[0][:3], ["schtasks", "/create", "/tn"])
        self.assertIn("/rl", ran[0])
        self.assertIn("已注册", out)

    def test_install_task_existing_without_force_keeps_it(self):
        ran = []
        with unittest.mock.patch.object(
                hsctl, "_task_state",
                return_value={"exists": True, "highest": True}), \
                unittest.mock.patch.object(
                    hsctl, "_run_quiet",
                    side_effect=lambda argv: (ran.append(argv), (0, ""))[1]):
            code, out, _ = run_cli([*self.base, "serve", "install-task"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("已存在", out)
        self.assertEqual(ran, [])

    def test_uninstall_task_deletes(self):
        ran = []
        with unittest.mock.patch.object(
                hsctl, "_task_state",
                return_value={"exists": True}), \
                unittest.mock.patch.object(
                    hsctl, "_run_quiet",
                    side_effect=lambda argv: (ran.append(argv), (0, ""))[1]):
            code, _, _ = run_cli([*self.base, "serve", "uninstall-task"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertEqual(ran[0], ["schtasks", "/delete", "/tn",
                                  hsctl.TASK_NAME, "/f"])

    def test_uninstall_task_when_missing_is_noop(self):
        with unittest.mock.patch.object(hsctl, "_task_state",
                                        return_value={"exists": False}):
            code, out, _ = run_cli([*self.base, "serve", "uninstall-task"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("不存在", out)

    def test_task_status_json(self):
        with unittest.mock.patch.object(
                hsctl, "_task_state",
                return_value={"exists": True, "highest": True,
                              "name": hsctl.TASK_NAME}):
            code, out, _ = run_cli(["--json", *self.base,
                                    "serve", "task-status"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertTrue(json.loads(out)["highest"])


class KillPidTests(unittest.TestCase):
    """结束进程：os.kill 失败要退到 taskkill，都失败要给出可操作的原因。"""

    def test_falls_back_to_taskkill(self):
        ran = []
        with unittest.mock.patch.object(hsctl.os, "kill",
                                        side_effect=PermissionError(5, "拒绝访问")), \
                unittest.mock.patch.object(
                    hsctl, "_run_quiet",
                    side_effect=lambda argv: (ran.append(argv), (0, ""))[1]):
            ok, detail = hsctl._kill_pid(4321)
        self.assertTrue(ok)
        self.assertEqual(detail, "taskkill")
        self.assertEqual(ran[0], ["taskkill", "/PID", "4321"])

    def test_reports_both_failures(self):
        with unittest.mock.patch.object(hsctl.os, "kill",
                                        side_effect=PermissionError(5, "拒绝访问")), \
                unittest.mock.patch.object(hsctl, "_run_quiet",
                                           return_value=(1, "ERROR: Access denied")):
            ok, detail = hsctl._kill_pid(4321)
        self.assertFalse(ok)
        self.assertIn("taskkill", detail)

    def test_stop_surfaces_elevation_hint_on_access_denied(self):
        """提权控制台杀不掉时，要告诉用户改用管理员 shell（而不是甩一个 WinError）。

        注意还要让 `_pid_alive` 说"进程还在"：真出这个提示的场景就是进程确实活着
        且结束不了（提权）。进程若已经不在，走的是下面那条"当成已停止"的分支。
        """
        self.console = _FakeConsole({
            "/api/status": dict(STATUS_FIXTURE, running=False)})
        self.addCleanup(self.console.close)
        with unittest.mock.patch.object(
                hsctl, "read_port_file",
                return_value={"port": self.console.port, "pid": 4321}), \
                unittest.mock.patch.object(hsctl, "_pid_image_name",
                                           return_value="python.exe"), \
                unittest.mock.patch.object(hsctl, "_pid_alive",
                                           return_value=True), \
                unittest.mock.patch.object(hsctl, "_kill_pid",
                                           return_value=(False, "Access denied")):
            code, _, err = run_cli(["--port", str(self.console.port),
                                    "serve", "stop"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("管理员", err)
        self.assertIn("hsctl stop", err)

    def test_stop_succeeds_when_the_pid_is_already_gone(self):
        """pid 已经不在（刚好自己退了）时别报"权限不足"，直接当成已停止并收尾。

        真机踩到过：进程已经不在了，却提示"用管理员 PowerShell"，
        把一个不存在的问题甩给用户，端口文件还留在那儿。
        """
        self.console = _FakeConsole({
            "/api/status": dict(STATUS_FIXTURE, running=False)})
        self.addCleanup(self.console.close)
        with unittest.mock.patch.object(
                hsctl, "read_port_file",
                return_value={"port": self.console.port, "pid": 4321}), \
                unittest.mock.patch.object(hsctl, "_pid_image_name",
                                           return_value="python.exe"), \
                unittest.mock.patch.object(hsctl, "_pid_alive",
                                           return_value=False), \
                unittest.mock.patch.object(hsctl, "_kill_pid",
                                           return_value=(False, "not found")):
            code, out, err = run_cli(["--port", str(self.console.port),
                                      "serve", "stop"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertNotIn("管理员", err)
        self.assertIn("已停止", out)


class TableDriftTests(unittest.TestCase):
    """CLI 里的延时字段表必须和 web_ui / config 保持一致，否则会给出错误提示。"""

    def test_delay_keys_match_config_whitelist(self):
        import config
        self.assertEqual(set(hsctl.DELAY_KEYS), set(config._USER_DELAY_KEYS))

    def test_delay_effect_table_matches_web_ui(self):
        import web_ui
        for key in hsctl.DELAY_KEYS:
            self.assertEqual(hsctl.DELAY_EFFECT[key],
                             web_ui._delay_effect_scope(key),
                             f"{key} 的生效时机在两个文件里不一致")

    def test_screenshot_kinds_match_web_ui(self):
        import web_ui
        self.assertEqual(tuple(hsctl.SCREENSHOT_KINDS),
                         tuple(web_ui.SCREENSHOT_KINDS))


class ParserTests(unittest.TestCase):
    def test_bare_command_without_tty_prints_help(self):
        # 必须把 stdin 伪装成"非交互"，否则裸命令会进 REPL 并卡在 input()。
        fake_stdin = unittest.mock.MagicMock()
        fake_stdin.isatty.return_value = False
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch("sys.stdin", fake_stdin), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = hsctl.main([])
        self.assertEqual(code, hsctl.EXIT_USAGE)
        self.assertIn("hsctl", out.getvalue())

    def test_unknown_command_is_usage_error(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                hsctl.main(["no-such-command"])
        self.assertEqual(ctx.exception.code, hsctl.EXIT_USAGE)


class DoctorAndEnvTests(unittest.TestCase):
    """`hsctl doctor` / `hsctl env`：跨机器环境的"自诊 + 保存"入口。

    这两条命令是给别的 agent 用的：拿到 verdict 与 next_steps 后自己配置并保存，
    所以这里锁住"输出字段 + 保存到本机 log/env.json"的行为。
    """

    def setUp(self):
        self.console = _FakeConsole({"/api/status": dict(STATUS_FIXTURE),
                                     "/api/selfcheck": {"ok": True, "result": {
                                         "ok": True, "python": "3.12.14",
                                         "executable": hsctl._normalize_python(
                                             os.sys.executable)}}})
        self.addCleanup(self.console.close)
        self.base = ["--port", str(self.console.port)]
        self.tmp = Path(tempfile.gettempdir()) / (
            f"hs_doctor_{os.getpid()}_{uuid.uuid4().hex[:8]}")
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.env_file = self.tmp / "env.json"
        self.patchers = [unittest.mock.patch.object(hsctl, "ENV_FILE",
                                                    self.env_file),
                         unittest.mock.patch.object(hsctl, "ROOT", self.tmp),
                         unittest.mock.patch.object(hsctl, "_where_pythons",
                                                    return_value=[])]
        for item in self.patchers:
            item.start()
        self.addCleanup(lambda: [p.stop() for p in self.patchers])
        os.environ.pop("HS_PYTHON", None)

    def test_doctor_json_shape(self):
        code, out, _ = run_cli(["--json", *self.base, "doctor"])

        self.assertEqual(code, hsctl.EXIT_OK)
        payload = json.loads(out)
        for key in ("verdict", "this_python", "candidates", "console",
                    "next_steps", "env_file", "env_template", "note"):
            self.assertIn(key, payload)
        self.assertTrue(payload["console"]["running"])
        self.assertTrue(payload["console"]["url"].startswith("http://127.0.0.1:"))

    def test_doctor_verdict_when_console_is_down(self):
        dead_port = _free_port()
        code, out, _ = run_cli(["--json", "--port", str(dead_port), "doctor"])

        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn(json.loads(out)["verdict"],
                      ("console_not_running", "env_ok_unsaved",
                       "env_deps_missing", "env_no_valid_interpreter"))

    def test_env_pin_saves_the_local_interpreter(self):
        interpreter = str(self.tmp / ".venv" / "Scripts" / "python.exe")
        Path(interpreter).parent.mkdir(parents=True, exist_ok=True)
        Path(interpreter).write_text("", encoding="utf8")
        record = {"ok": True, "executable": interpreter, "version": (3, 12, 14),
                  "missing": [], "reason": "ok"}
        with unittest.mock.patch.object(hsctl, "verify_python",
                                        return_value=record):
            code, out, _ = run_cli(["--json", "env", "pin", interpreter])

        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertEqual(interpreter, json.loads(out)["interpreter"])
        saved = json.loads(self.env_file.read_text(encoding="utf8"))
        self.assertEqual(interpreter, saved["interpreter"])
        self.assertEqual("hsctl env --pin", saved["source"])

    def test_env_pin_rejects_bad_interpreter(self):
        with unittest.mock.patch.object(
                hsctl, "verify_python",
                return_value={"ok": False, "executable": "X:/py.exe",
                              "version": None, "missing": [],
                              "reason": "缺依赖：pywin32"}):
            code, _, err = run_cli(["env", "pin", "X:/py.exe"])

        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("不合格", err)
        self.assertIn("pywin32", err)
        self.assertFalse(self.env_file.exists())

    def test_env_discover_lists_candidates_with_verdicts(self):
        candidate = str(self.tmp / "python.exe")
        Path(candidate).write_text("", encoding="utf8")
        record = {"ok": True, "executable": candidate, "version": (3, 12, 14),
                  "missing": [], "reason": "ok"}
        with unittest.mock.patch.object(hsctl, "discover_candidates",
                                        return_value=[candidate]), \
                unittest.mock.patch.object(hsctl, "verify_python",
                                           return_value=record):
            code, out, _ = run_cli(["--json", "env", "discover"])

        self.assertEqual(code, hsctl.EXIT_OK)
        payload = json.loads(out)
        self.assertTrue(payload["candidates"][0]["ok"])
        self.assertEqual(candidate, payload["candidates"][0]["path"])


class HelpCoverageTests(unittest.TestCase):
    """每个子命令的 --help 都必须能渲染出来。

    argparse 会对 help 文本做 % 格式化，所以 help 里出现裸 '%'（例如「阈值(%)」）
    会在**渲染帮助时**抛 ValueError —— 这种错只有真的打印帮助才暴露，因此这里把
    整棵子命令树走一遍。
    """

    def test_every_subcommand_help_renders(self):
        parser = hsctl.build_parser()
        seen = []

        def walk(current, path):
            current.format_help()
            seen.append(" ".join(path) or "hsctl")
            for action in current._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for name, sub in action.choices.items():
                        walk(sub, path + [name])

        walk(parser, [])
        self.assertGreater(len(seen), 30, f"子命令数量异常：{len(seen)}")
        self.assertIn("concede set", seen)      # 就是它踩过 % 格式化的坑


if __name__ == "__main__":
    unittest.main()
