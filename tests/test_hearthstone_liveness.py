"""炉石存活检测：进程消失（闪退）与 Power.log 停滞（卡死）。

回归背景（issue 反馈）：挂机时炉石在换牌界面闪退，脚本完全没察觉，继续对失效
画面做 OCR，空转近 3 小时才被手动停止。修复后：
  * Hearthstone.exe 进程消失（本轮见过它才算“消失”）→ 判定退出（fatal）；
  * 对局中 Power.log 长时间无新增 → 先告警，更久则判定无响应（fatal）；
  * 主菜单/匹配阶段的日志本来就安静，绝不能因此误判。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import log_overlay
import web_ui
from src.safety.hearthstone_liveness import HearthstoneLiveness, hearthstone_process_running


class _Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
        return self.now


BASE_CFG = {
    "enabled": True,
    "process_grace_seconds": 6.0,
    "log_stale_warn_seconds": 120.0,
    "log_stale_stop_seconds": 300.0,
}


class _MonitorFixture(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.running = True
        self.age = None
        self.cfg = dict(BASE_CFG)
        self.monitor = HearthstoneLiveness(
            process_checker=lambda: self.running,
            log_path_provider=lambda: "Power.log" if self.age is not None else None,
            settings_provider=lambda: dict(self.cfg),
            clock=self.clock,
            file_age=lambda _path: self.age,
            cache_seconds=0.0)


class ProcessLivenessTests(_MonitorFixture):
    def test_inactive_or_unknown_process_is_not_fatal(self):
        """没开过 / 枚举失败 / 已禁用：都不能判定成闪退。"""
        self.running = False
        self.assertIsNone(self.monitor.check())
        self.assertEqual("idle", self.monitor.sample()["status"])

        unknown = HearthstoneLiveness(
            process_checker=lambda: None,
            log_path_provider=lambda: None,
            settings_provider=lambda: dict(BASE_CFG),
            clock=self.clock, cache_seconds=0.0)
        self.assertIsNone(unknown.check())
        self.assertEqual("unknown", unknown.sample()["status"])

        self.cfg["enabled"] = False
        self.clock.advance(600)
        self.assertIsNone(self.monitor.check())
        self.assertEqual("disabled", self.monitor.sample()["status"])

    def test_process_gone_after_grace_is_fatal_once(self):
        self.assertIsNone(self.monitor.check())          # 见过进程
        self.running = False
        self.clock.advance(30)
        self.assertIsNone(self.monitor.check())          # 第一次发现“没了”只是开始计时
        self.clock.advance(10)                           # 超过 6s 宽限期

        event = self.monitor.check()
        self.assertIsNotNone(event)
        self.assertEqual("process_gone", event["kind"])
        self.assertTrue(event["fatal"])
        self.assertIn("炉石已退出", event["message"])
        self.assertEqual("gone", self.monitor.sample()["status"])
        self.assertIsNone(self.monitor.check())          # 不刷屏

    def test_process_coming_back_before_grace_is_not_reported(self):
        self.monitor.check()
        self.running = False
        self.clock.advance(2)
        self.assertIsNone(self.monitor.check())

        self.running = True
        self.assertIsNone(self.monitor.check())
        self.clock.advance(600)

        self.assertIsNone(self.monitor.check())

    def test_reset_forgets_the_previous_run(self):
        self.monitor.check()
        self.running = False
        self.clock.advance(1)
        self.monitor.check()
        self.clock.advance(60)
        self.assertIsNotNone(self.monitor.check())

        self.monitor.reset()

        self.assertIsNone(self.monitor.check())          # 新一轮不计旧账
        self.assertEqual("idle", self.monitor.sample()["status"])


class LogStalenessTests(_MonitorFixture):
    def test_fresh_missing_or_out_of_game_log_is_ok(self):
        cases = (
            ("fresh", 5.0, True),
            ("no power log", None, True),
            ("menu phase", 900.0, False),
        )
        for label, age, in_game in cases:
            with self.subTest(case=label):
                self.age = age
                self.assertIsNone(self.monitor.check(in_game=in_game))
                self.assertEqual(
                    "ok", self.monitor.sample(in_game=in_game)["status"])

    def test_warn_then_stop_includes_context(self):
        self.age = 130.0
        warn = self.monitor.check(in_game=True)
        self.assertIsNotNone(warn)
        self.assertFalse(warn["fatal"])
        self.assertIn("Power.log", warn["message"])
        self.assertEqual("warning", self.monitor.sample(in_game=True)["status"])
        self.assertIsNone(self.monitor.check(in_game=True))   # 同类告警只报一次

        self.age = 420.0
        stop = self.monitor.check(
            in_game=True, detail="连续 12 次推荐读取失败")
        self.assertIsNotNone(stop)
        self.assertTrue(stop["fatal"])
        self.assertEqual("log_stale_stop", stop["kind"])
        self.assertIn("连续 12 次推荐读取失败", stop["message"])
        self.assertEqual("stale", self.monitor.sample(in_game=True)["status"])

    def test_thresholds_come_from_config(self):
        self.cfg["log_stale_warn_seconds"] = 30.0
        self.cfg["log_stale_stop_seconds"] = 45.0
        self.age = 31.0

        self.assertFalse(self.monitor.check(in_game=True)["fatal"])

        self.age = 46.0
        self.assertTrue(self.monitor.check(in_game=True)["fatal"])


class NativeProcessTests(unittest.TestCase):
    """用注入的进程列表验证判定逻辑；真实枚举位于 tests/native。"""

    def test_process_check_contract(self):
        self.assertIs(False, hearthstone_process_running(
            process_lister=lambda: ["notepad.exe"]))
        self.assertIs(True, hearthstone_process_running(
            process_lister=lambda: ["HEARTHSTONE.EXE"]))
        self.assertIsNone(hearthstone_process_running(
            process_lister=lambda: None))
        self.assertIsNone(hearthstone_process_running(
            process_lister=lambda: (_ for _ in ()).throw(OSError("boom"))))


class DefaultSettingsTests(unittest.TestCase):
    """默认阈值来自「守护」配置（原 liveness/watchdog 两段已删除）。"""

    def test_default_settings_follow_guardian_config(self):
        import src.safety.hearthstone_liveness as liveness

        with patch("config.resident_settings",
                   return_value={"warn_seconds": 90.0,
                                 "stale_in_game_seconds": 240.0}):
            cfg = liveness._default_settings()

        self.assertTrue(cfg["enabled"])
        self.assertEqual(90.0, cfg["log_stale_warn_seconds"])
        self.assertEqual(240.0, cfg["log_stale_stop_seconds"])

    def test_warn_zero_means_use_the_stop_threshold(self):
        import src.safety.hearthstone_liveness as liveness

        with patch("config.resident_settings",
                   return_value={"warn_seconds": 0.0,
                                 "stale_in_game_seconds": 240.0}):
            cfg = liveness._default_settings()

        self.assertEqual(240.0, cfg["log_stale_warn_seconds"])


class WebLivenessSamplingTests(unittest.TestCase):
    """网页/浮窗读的存活采样（原 test_web_liveness_api.py 的有效断言迁移而来）。"""

    def test_prefers_live_state_from_the_state_machine(self):
        live = {"enabled": True, "status": "gone", "process": "stopped",
                "log_age": None, "alert": "炉石已退出", "in_game": True}
        fsm = SimpleNamespace(hearthstone_liveness_state=lambda: dict(live))

        with patch.object(web_ui.CTRL, "fsm", fsm):
            state = web_ui._liveness_state()

        self.assertEqual("gone", state["status"])
        self.assertEqual("炉石已退出", state["alert"])

    def test_samples_the_monitor_without_automation(self):
        monitor = SimpleNamespace(sample=lambda in_game=False: {
            "enabled": True, "status": "ok", "process": "running",
            "log_age": 3.0, "alert": None, "in_game": in_game})
        module = SimpleNamespace(default_monitor=lambda: monitor)

        with (
            patch.object(web_ui.CTRL, "fsm", None),
            patch.dict("sys.modules",
                       {"src.safety.hearthstone_liveness": module}),
        ):
            state = web_ui._liveness_state()

        self.assertEqual("ok", state["status"])
        self.assertFalse(state["in_game"])

    def test_status_snapshot_exposes_liveness_sample_without_old_keys(self):
        live = {"enabled": True, "status": "stale", "process": "running",
                "log_age": 400.0, "alert": "炉石疑似无响应", "in_game": True}
        fsm = SimpleNamespace(hearthstone_liveness_state=lambda: dict(live),
                              stop_after_current_game=False,
                              FSM_state="Battling", game_count=0, win_count=0,
                              concede_count=0)
        # status_snapshot 会读 resident 状态；这里塞一个假监督器，避免惰性创建
        # 真 RESIDENT（旧 bug：后续 test_start_latency 的同步假线程会把它跑成死循环）。
        fake_resident = SimpleNamespace(state=lambda: {
            "enabled": False, "state": "disabled"})

        with (
            patch.object(web_ui.CTRL, "fsm", fsm),
            patch.object(web_ui, "RESIDENT", fake_resident),
        ):
            snapshot = web_ui.status_snapshot()

        self.assertEqual("stale", snapshot["liveness"]["status"])
        self.assertTrue(snapshot["liveness"]["enabled"])
        # 旧的两张卡（存活检测阈值/中断重启）已删除：不再有这两个顶层 key。
        self.assertNotIn("liveness_alert", snapshot)
        self.assertNotIn("watchdog", snapshot)

    def test_overlay_liveness_row_reflects_sampling(self):
        live = {"enabled": True, "status": "ok", "log_age": 5.0,
                "in_game": True, "alert": None}

        with patch.object(web_ui, "_liveness_state", return_value=dict(live)):
            row = log_overlay.hearthstone_row(web_ui._overlay_liveness())

        self.assertEqual("运行中", row["value"])
        self.assertEqual(log_overlay.GREEN, row["value_color"])

    def test_liveness_callback_is_wired_into_the_overlay(self):
        bound = {}
        overlay = SimpleNamespace(
            start=lambda **kwargs: bound.update(kwargs),
            is_running=lambda: False)

        with (
            patch.object(web_ui, "log_overlay", overlay),
            patch.object(web_ui.threading, "Thread"),
        ):
            web_ui._bind_overlay()

        self.assertIs(bound["liveness_callback"], web_ui._overlay_liveness)
        self.assertIs(bound["account_callback"], web_ui._overlay_account)


if __name__ == "__main__":
    unittest.main()
