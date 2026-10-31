"""炉石存活检测：进程消失（闪退）与 Power.log 停滞（卡死）。

回归背景（issue 反馈）：挂机时炉石在换牌界面闪退，脚本完全没察觉，继续对失效
画面做 OCR，空转近 3 小时才被手动停止。修复后：
  * Hearthstone.exe 进程消失（本轮见过它才算“消失”）→ 判定退出（fatal）；
  * 对局中 Power.log 长时间无新增 → 先告警，更久则判定无响应（fatal）；
  * 主菜单/匹配阶段的日志本来就安静，绝不能因此误判。
"""

import unittest

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


if __name__ == "__main__":
    unittest.main()
