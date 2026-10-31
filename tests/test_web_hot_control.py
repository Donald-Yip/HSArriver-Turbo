# -*- coding: utf-8 -*-
"""「热更新通道」回归测试。

CLI / agent 需要在自动化**运行中**改参数（请求体带 "hot": true），而网页必须
继续维持「运行中禁止修改」的保护。这里把 CTRL.automation_thread 假装成"在跑"，
验证两条路径分得开、且延时确实是**就地**改写正在运行的 frozen 配置实例。

不写真实 ui_config.json：load_config / save_config 都被替换成内存桩。
"""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import web_ui


@dataclass(frozen=True)
class _FrozenConfig:
    """模仿 RecommendationConfig：frozen dataclass，普通 setattr 会抛异常。"""

    pre_action_delay_seconds: float = 7.0
    first_turn_per_card_delay_seconds: float = 3.5
    post_action_delay_seconds: float = 0.2
    ocr_preprocess_scale: float = 1.4


class _RunningThread:
    def is_alive(self):
        return True


class HotChannelTests(unittest.TestCase):
    """运行中改参数的两种行为（网页 vs CLI/agent）。"""

    def setUp(self):
        self.saved_thread = web_ui.CTRL.automation_thread
        web_ui.CTRL.automation_thread = _RunningThread()
        self.addCleanup(lambda: setattr(web_ui.CTRL, "automation_thread",
                                       self.saved_thread))
        self.saved_cfg = {}
        self.cfg = {"delays": {}, "human_like": {}, "liveness": {},
                    "auto_concede": {}, "rank_stop": {},
                    "name": "", "log_root": ""}
        for name, kwargs in (
            ("load_config", {"side_effect": lambda: dict(self.cfg)}),
            ("save_config", {"side_effect": self._record_save}),
            ("_log", {}),
            ("_current_delays", {"return_value": {}}),
        ):
            patcher = patch.object(web_ui, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _record_save(self, cfg):
        self.saved_cfg = dict(cfg)

    # ------------------------------------------------ 守卫没有被放松
    def test_endpoints_without_hot_stay_blocked_while_running(self):
        cases = (
            ("delays", web_ui.api_save_delays, {"pre_action_delay_seconds": 1}),
            ("human_like", web_ui.api_save_human_like, {"enabled": True}),
            ("liveness", web_ui.api_save_liveness, {"enabled": False}),
            ("concede", web_ui.api_save_concede, {"enabled": True}),
            ("rank_stop", web_ui.api_save_rank_stop, {"enabled": True}),
            ("config", web_ui.api_save_config, {"name": "A#1"}),
        )
        for name, api, body in cases:
            with self.subTest(endpoint=name):
                result = api(body)
                self.assertFalse(result["ok"])
                self.assertIn("运行中", result["error"])
        self.assertEqual({}, self.saved_cfg)

    # ------------------------------------------------ 带 hot 就能改
    def test_hot_flag_saves_through_while_running(self):
        cases = (
            ("human_like", web_ui.api_save_human_like,
             {"enabled": True, "post_delay_min": 0.5, "post_delay_max": 2.0},
             lambda saved: (
                 self.assertTrue(saved["human_like"]["enabled"]),
                 self.assertEqual(saved["human_like"]["post_delay_max"], 2.0))),
            ("liveness", web_ui.api_save_liveness,
             {"enabled": False, "log_stale_warn_seconds": 60,
              "log_stale_stop_seconds": 120},
             lambda saved: self.assertFalse(saved["liveness"]["enabled"])),
            ("concede", web_ui.api_save_concede,
             {"enabled": True, "threshold": 12, "rounds": 4},
             lambda saved: (
                 self.assertEqual(saved["auto_concede"]["rounds"], 4),
                 self.assertEqual(saved["auto_concede"]["threshold"], 12.0))),
            ("rank_stop", web_ui.api_save_rank_stop,
             {"enabled": True, "mode": "legend_number", "legend_number": 500},
             lambda saved: self.assertEqual(
                 saved["rank_stop"]["legend_number"], 500)),
        )
        for name, api, body, check in cases:
            with self.subTest(endpoint=name):
                result = api(dict(body, hot=True))
                self.assertTrue(result["ok"])
                check(self.saved_cfg)

    def test_config_hot_applies_constants_while_running(self):
        with patch.object(web_ui, "_apply_constants") as apply_constants:
            result = web_ui.api_save_config(
                {"hot": True, "name": "A#1", "log_root": "D:\\Logs"})
        self.assertTrue(result["ok"])
        apply_constants.assert_called_once_with("A#1", "D:\\Logs")

    # ------------------------------------------------ 延时的就地热更新
    def test_hot_delays_mutates_the_live_frozen_config(self):
        fake = types.ModuleType("FSM_action")
        fake.recommendation_config = _FrozenConfig()
        with patch.dict(sys.modules, {"FSM_action": fake}):
            result = web_ui.api_save_delays(
                {"hot": True, "pre_action_delay_seconds": 3.5})
        self.assertTrue(result["ok"])
        self.assertEqual(result["effects"]["pre_action_delay_seconds"], "live")
        # 关键：frozen dataclass 也被就地改写了 —— 说明流程对象持有的同一实例
        # 会立刻看到新值（而不是只改了 ui_config.json 等下次重开）。
        self.assertEqual(fake.recommendation_config.pre_action_delay_seconds, 3.5)
        self.assertEqual(
            self.saved_cfg["delays"]["pre_action_delay_seconds"], 3.5)

    def test_hot_delays_reports_effect_scope(self):
        fake = types.ModuleType("FSM_action")
        fake.recommendation_config = _FrozenConfig()
        with patch.dict(sys.modules, {"FSM_action": fake}):
            result = web_ui.api_save_delays({
                "hot": True,
                "post_action_delay_seconds": 0.5,
                "ocr_preprocess_scale": 2.0,
                "discover_target_delay_seconds": 1.0})
        self.assertEqual(result["effects"]["discover_target_delay_seconds"], "live")
        self.assertEqual(result["effects"]["post_action_delay_seconds"], "next_start")
        self.assertEqual(result["effects"]["ocr_preprocess_scale"], "restart")
        self.assertIn("note", result)

    def test_hot_delays_without_running_automation_only_persists(self):
        """没在跑时带 hot 也不能报错（CLI 的 set 默认总是带 hot）。"""
        web_ui.CTRL.automation_thread = None
        fake = types.ModuleType("FSM_action")
        fake.recommendation_config = _FrozenConfig()
        with patch.dict(sys.modules, {"FSM_action": fake}):
            result = web_ui.api_save_delays(
                {"hot": True, "pre_action_delay_seconds": 2.0})
        self.assertTrue(result["ok"])
        self.assertEqual(
            self.saved_cfg["delays"]["pre_action_delay_seconds"], 2.0)
        # 没在跑就不该动内存里的实例（下次「开始对战」会重新读盘）
        self.assertEqual(fake.recommendation_config.pre_action_delay_seconds, 7.0)

    # ------------------------------------------------ 校验规则照旧
    def test_hot_delays_still_enforces_bounds(self):
        result = web_ui.api_save_delays(
            {"hot": True, "pre_action_delay_seconds": 9999})
        self.assertFalse(result["ok"])
        self.assertIn("必须介于", result["error"])

    def test_hot_delays_still_rejects_non_numeric(self):
        result = web_ui.api_save_delays(
            {"hot": True, "pre_action_delay_seconds": "abc"})
        self.assertFalse(result["ok"])

    def test_hot_delays_ignores_unknown_keys(self):
        result = web_ui.api_save_delays({"hot": True, "not_a_key": 1})
        self.assertTrue(result["ok"])

    # ------------------------------------------------ 辅助函数
    def test_hot_requested_truthiness(self):
        self.assertTrue(web_ui._hot_requested({"hot": True}))
        self.assertFalse(web_ui._hot_requested({}))
        self.assertFalse(web_ui._hot_requested(None))
        self.assertFalse(web_ui._hot_requested({"hot": 0}))


class ScreenshotApiTests(unittest.TestCase):
    """按需抓图 API：只截图、落盘、返回路径；参数错误要优雅拒绝。"""

    _TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp-tests"

    def setUp(self):
        self._TMP_ROOT.mkdir(exist_ok=True)
        self.dir = self._TMP_ROOT / f"shots_{os.getpid()}"
        self.dir.mkdir(exist_ok=True)
        for name, value in (("SCREENSHOT_DIR", self.dir),
                            ("SCREENSHOT_LATEST", self.dir / "agent_latest.png")):
            patcher = patch.object(web_ui, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        log_patcher = patch.object(web_ui, "_log")
        log_patcher.start()
        self.addCleanup(log_patcher.stop)

    @staticmethod
    def _fake_frame(width=64, height=36):
        from PIL import Image
        return Image.new("RGB", (width, height), (12, 34, 56))

    def test_writes_png_and_reports_paths(self):
        with patch.object(web_ui, "capture_frame",
                          return_value=(self._fake_frame(), {"kind": "client",
                                                             "note": "测试帧"})):
            result = web_ui.api_screenshot({"kind": "client"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["width"], 64)
        self.assertEqual(result["height"], 36)
        self.assertEqual(result["kind"], "client")
        self.assertTrue(Path(result["path"]).is_file())
        self.assertEqual(Path(result["latest"]),
                         self.dir / "agent_latest.png")
        self.assertTrue((self.dir / "agent_latest.png").is_file())

    def test_max_width_scales_down(self):
        with patch.object(web_ui, "capture_frame",
                          return_value=(self._fake_frame(400, 200),
                                        {"kind": "full"})):
            result = web_ui.api_screenshot({"kind": "full", "max_width": 100})
        self.assertEqual(result["width"], 100)
        self.assertEqual(result["height"], 50)      # 等比
        self.assertEqual(result["scaled_to_width"], 100)

    def test_does_not_upscale_when_smaller(self):
        with patch.object(web_ui, "capture_frame",
                          return_value=(self._fake_frame(64, 36),
                                        {"kind": "full"})):
            result = web_ui.api_screenshot({"kind": "full", "max_width": 999})
        self.assertEqual(result["width"], 64)
        self.assertNotIn("scaled_to_width", result)

    def test_out_name_is_honoured(self):
        with patch.object(web_ui, "capture_frame",
                          return_value=(self._fake_frame(), {"kind": "roi"})):
            result = web_ui.api_screenshot({"out": "mine.png"})
        self.assertEqual(Path(result["path"]).name, "mine.png")

    def test_bad_kind_is_rejected(self):
        result = web_ui.api_screenshot({"kind": "nope"})
        self.assertFalse(result["ok"])
        self.assertIn("不支持的抓图类型", result["error"])

    def test_bad_max_width_is_rejected(self):
        with patch.object(web_ui, "capture_frame",
                          return_value=(self._fake_frame(), {"kind": "full"})):
            result = web_ui.api_screenshot({"max_width": "abc"})
        self.assertFalse(result["ok"])
        self.assertIn("max_width", result["error"])

    def test_capture_exception_becomes_error_not_crash(self):
        with patch.object(web_ui, "capture_frame",
                          side_effect=RuntimeError("boom")), \
                patch.object(web_ui.traceback, "print_exc"):
            result = web_ui.api_screenshot({})
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])

    def test_none_frame_is_reported(self):
        with patch.object(web_ui, "capture_frame", return_value=(None, {})):
            result = web_ui.api_screenshot({})
        self.assertFalse(result["ok"])
        self.assertIn("没有得到画面", result["error"])

    def test_quiet_suppresses_log(self):
        with patch.object(web_ui, "capture_frame",
                          return_value=(self._fake_frame(), {"kind": "full"})), \
                patch.object(web_ui, "_log") as log:
            web_ui.api_screenshot({"quiet": True})
            log.assert_not_called()

    # ---------------------------------------------------------- 窗口状态守卫
    def test_window_state_guard(self):
        """最小化 / 屏幕外 / 没窗口都必须退化为整屏，只放行健康的客户区。"""
        import display_mapping
        cases = (
            ("no_window", {"hwnd_of": 0}, False, "未运行"),
            ("minimized", {"hwnd_of": 4242,
                           "read_client_rect": (0, 0, 0, 0)}, False, "最小化"),
            ("offscreen", {"hwnd_of": 4242,
                           "read_client_rect": (0, 0, 1920, 1080),
                           "client_to_screen": (-32000, -32000)},
             False, "屏幕外"),
            ("healthy", {"hwnd_of": 4242,
                         "read_client_rect": (0, 0, 1920, 1080),
                         "client_to_screen": (787, 491)}, True, ""),
        )
        for name, mocks, expected_ok, fragment in cases:
            with self.subTest(window=name), ExitStack() as stack:
                for attr, value in mocks.items():
                    stack.enter_context(
                        patch.object(display_mapping, attr, return_value=value))
                ok, why = web_ui._hs_client_capture_ready()
                self.assertEqual(expected_ok, ok)
                if fragment:
                    self.assertIn(fragment, why)
                else:
                    self.assertEqual("", why)

    def test_client_capture_falls_back_when_minimized(self):
        import display_mapping
        with patch.object(web_ui, "_hs_client_capture_ready",
                          return_value=(False, "炉石窗口不可见或已最小化（0×0）")), \
                patch.object(display_mapping, "capture_region") as capture:
            image, info = web_ui.capture_frame("client")
        self.assertEqual(info["fallback"], "full")
        self.assertIn("最小化", info["note"])
        self.assertGreater(image.width, 0)
        capture.assert_not_called()          # 退化时不该再去截客户区


class PortExclusivityTests(unittest.TestCase):
    """回归：Python 的 HTTPServer 默认开 SO_REUSEADDR，而 Windows 上它的语义是
    "允许绑定别人已占用的端口" —— 于是两个实例会同时 LISTENING 在同一端口，
    请求随机落地（网页打到 A、浮窗属于 B，还共用一份 ui_config.json）。
    端口顺延逻辑因此形同虚设，必须显式关掉。"""

    def test_control_server_refuses_to_share_a_port(self):
        self.assertFalse(web_ui._ControlServer.allow_reuse_address,
                         "开着它 = 端口顺延失效（Windows 语义）")
        first = web_ui._ControlServer(("127.0.0.1", 0), web_ui.Handler)
        self.addCleanup(first.server_close)
        port = int(first.server_address[1])
        with self.assertRaises(OSError):
            second = web_ui._ControlServer(("127.0.0.1", port), web_ui.Handler)
            second.server_close()


class PortFileTests(unittest.TestCase):
    """端口文件：CLI/agent 靠它找到控制台（端口会被占用顺延）。

    只写在仓库里既有的 .tmp-tests 目录（不新建子目录、不用 %TEMP%）：
    受限环境里"新建目录再写"可能被拒绝，测试不该依赖那种能力。
    """

    _TMP_ROOT = Path(__file__).resolve().parent.parent / ".tmp-tests"

    def _scratch(self, name):
        self._TMP_ROOT.mkdir(exist_ok=True)
        path = self._TMP_ROOT / f"ui_port_{name}_{os.getpid()}.txt"
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        return path

    def test_write_and_clear(self):
        target = self._scratch("roundtrip")
        with patch.object(web_ui, "PORT_FILE", target):
            web_ui._write_port_file(8790)
            info = json.loads(target.read_text(encoding="utf8"))
            self.assertEqual(info["port"], 8790)
            self.assertEqual(info["pid"], os.getpid())
            self.assertIn(":8790", info["url"])
            # 原子写：落盘后不留半截临时文件
            self.assertFalse(target.with_name(target.name + ".tmp").exists())
            web_ui._clear_port_file()
            self.assertFalse(target.exists())

    def test_clear_is_silent_when_missing(self):
        target = self._scratch("missing")
        with patch.object(web_ui, "PORT_FILE", target):
            web_ui._clear_port_file()      # 不应抛异常
        self.assertFalse(target.exists())


class RestartHearthstoneApiTests(unittest.TestCase):
    """重启炉石的 API：必须显式确认、预检失败立即回错、不能并发触发两次。"""

    def setUp(self):
        saved = web_ui._RESTART_STATE["running"]
        self.addCleanup(web_ui._RESTART_STATE.__setitem__, "running", saved)
        web_ui._RESTART_STATE["running"] = False

    def test_requires_confirm(self):
        result = web_ui.api_restart_hearthstone({})
        self.assertFalse(result["ok"])
        self.assertIn("confirm", result["error"])

    def test_preflight_failure_returns_error_immediately(self):
        import hs_restart
        with patch.object(hs_restart, "resolve_paths",
                          return_value={"ok": False, "error": "日志目录没配"}):
            result = web_ui.api_restart_hearthstone({"confirm": True})
        self.assertFalse(result["ok"])
        self.assertIn("日志目录没配", result["error"])
        self.assertFalse(web_ui._RESTART_STATE["running"])

    def test_starts_named_background_thread(self):
        import hs_restart
        names = []

        class _Thread:
            def __init__(self, target=None, name=None, daemon=None):
                names.append(name)

            def start(self):
                pass

        with patch.object(hs_restart, "resolve_paths",
                          return_value={"ok": True}), \
                patch.object(web_ui.threading, "Thread", _Thread):
            result = web_ui.api_restart_hearthstone({"confirm": True})
        self.assertTrue(result["ok"])
        self.assertEqual(names, ["hs-restart-api"])
        # 线程体没跑完，标记必须留在"进行中"，否则会被并发触发第二次
        self.assertTrue(web_ui._RESTART_STATE["running"])

    def test_double_start_is_rejected(self):
        web_ui._RESTART_STATE["running"] = True
        import hs_restart
        with patch.object(hs_restart, "resolve_paths",
                          return_value={"ok": True}):
            result = web_ui.api_restart_hearthstone({"confirm": True})
        self.assertFalse(result["ok"])
        self.assertIn("还没结束", result["error"])


if __name__ == "__main__":
    unittest.main()
