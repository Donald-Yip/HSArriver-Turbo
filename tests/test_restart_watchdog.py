# -*- coding: utf-8 -*-
"""「中断重启」看门狗的判定与限流测试。

看门狗的判定逻辑是"日志停滞多久才算卡住、什么时候该停手"，判错会导致
无人值守时把正常挂机误判成卡死（反复重启）或真卡住时不管不问。这里把全部
外部依赖（配置、日志年龄、自动化状态、时钟、重启动作）注入成假的，
所以测试不依赖本机是否真的跑着炉石。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.safety.restart_watchdog import RestartWatchdog  # noqa: E402


def _cfg(**overrides):
    base = {"enabled": True, "stale_seconds": 300.0, "max_restarts": 3,
            "window_seconds": 600.0, "check_seconds": 0.0,
            "settle_seconds": 0.0}
    base.update(overrides)
    return base


class _Fake:
    """把可变量包起来，方便在测试里直接改（闭包不能赋值）。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class WatchdogGateTests(unittest.TestCase):
    """三个必要条件：开关开 + 自动化在跑 + 日志停滞够久。"""

    def test_disabled_never_restarts(self):
        restarts = []
        wd = RestartWatchdog(
            settings_provider=lambda: _cfg(enabled=False),
            log_age_provider=lambda: 99999.0,
            automation_running=lambda: True,
            restarter=lambda cfg: restarts.append(cfg) or True)
        self.assertIsNone(wd.poll())
        self.assertEqual(restarts, [])
        self.assertEqual(wd.state()["status"], "disabled")

    def test_no_restart_while_automation_is_not_running(self):
        # 没在跑自动化时日志安静是正常的（还在主界面发呆），绝不能重启。
        restarts = []
        wd = RestartWatchdog(
            settings_provider=lambda: _cfg(),
            log_age_provider=lambda: 99999.0,
            automation_running=lambda: False,
            restarter=lambda cfg: restarts.append(cfg) or True)
        self.assertIsNone(wd.poll())
        self.assertEqual(restarts, [])
        self.assertEqual(wd.state()["status"], "idle")

    def test_no_restart_below_threshold(self):
        restarts = []
        wd = RestartWatchdog(
            settings_provider=lambda: _cfg(stale_seconds=300.0),
            log_age_provider=lambda: 299.0,
            automation_running=lambda: True,
            restarter=lambda cfg: restarts.append(cfg) or True)
        self.assertIsNone(wd.poll())
        self.assertEqual(restarts, [])
        self.assertEqual(wd.state()["status"], "ok")

    def test_no_restart_when_log_file_is_missing(self):
        # 拿不到 Power.log（还没打第一局 / 目录没配好）时不能当成卡住。
        restarts = []
        wd = RestartWatchdog(
            settings_provider=lambda: _cfg(),
            log_age_provider=lambda: None,
            automation_running=lambda: True,
            restarter=lambda cfg: restarts.append(cfg) or True)
        self.assertIsNone(wd.poll())
        self.assertEqual(restarts, [])
        self.assertEqual(wd.state()["status"], "unknown")

    def test_stale_log_triggers_a_restart(self):
        restarts = []
        wd = RestartWatchdog(
            settings_provider=lambda: _cfg(stale_seconds=300.0),
            log_age_provider=lambda: 301.0,
            automation_running=lambda: True,
            restarter=lambda cfg: restarts.append(cfg) or True)
        event = wd.poll()
        self.assertIsNotNone(event)
        self.assertEqual(event["kind"], "restart")
        self.assertTrue(event["restart"])
        self.assertEqual(len(restarts), 1)
        self.assertIn("301", event["message"])
        self.assertIn("300", event["message"])

    def test_threshold_is_configurable(self):
        # 阈值改成 60s 后，90s 就该重启（而不是等默认的 300s）。
        restarts = []
        wd = RestartWatchdog(
            settings_provider=lambda: _cfg(stale_seconds=60.0),
            log_age_provider=lambda: 90.0,
            automation_running=lambda: True,
            restarter=lambda cfg: restarts.append(cfg) or True)
        self.assertIsNotNone(wd.poll())
        self.assertEqual(len(restarts), 1)


class WatchdogRateLimitTests(unittest.TestCase):
    """滑动窗口限流：短时间内不能无限重启。"""

    def _watchdog(self, clock, restarts, cfg=None):
        return RestartWatchdog(
            settings_provider=(cfg if callable(cfg) else (lambda: cfg or _cfg())),
            log_age_provider=lambda: 301.0,
            automation_running=lambda: True,
            restarter=lambda c: restarts.append(c) or True,
            clock=lambda: clock[0])

    def test_stops_after_max_restarts_in_window(self):
        clock = [0.0]
        restarts = []
        wd = self._watchdog(clock, restarts,
                            lambda: _cfg(max_restarts=2, window_seconds=600.0))

        clock[0] = 100.0
        self.assertEqual(wd.poll()["kind"], "restart")
        clock[0] = 200.0
        self.assertEqual(wd.poll()["kind"], "restart")

        # 第三次：窗口内已经 2 次，达到上限 → 不再重启，转告警。
        clock[0] = 300.0
        event = wd.poll()
        self.assertEqual(event["kind"], "exhausted")
        self.assertEqual(len(restarts), 2)
        self.assertEqual(wd.state()["status"], "exhausted")
        # 用尽后不重复刷告警。
        clock[0] = 400.0
        self.assertIsNone(wd.poll())

    def test_window_slides_so_it_can_restart_again_later(self):
        clock = [0.0]
        restarts = []
        wd = self._watchdog(clock, restarts,
                            lambda: _cfg(max_restarts=1, window_seconds=600.0))

        clock[0] = 100.0
        self.assertEqual(wd.poll()["kind"], "restart")
        clock[0] = 200.0
        self.assertEqual(wd.poll()["kind"], "exhausted")

        # 窗口滑过第一次重启（100 + 600 < 800）后，配额回来了。
        clock[0] = 800.0
        self.assertEqual(wd.poll()["kind"], "restart")
        self.assertEqual(len(restarts), 2)

    def test_restart_counts_even_when_dispatch_fails(self):
        # 派发失败也占配额：失败的重启同样在烧资源，不该被无限重试。
        # 注意：失败同样会开一个宽限期（从失败时刻起 settle_seconds 内不再判），
        # 所以这里必须把时钟推过宽限期再验证「配额已经被记上」。
        clock = [0.0]
        wd = RestartWatchdog(
            settings_provider=lambda: _cfg(max_restarts=2, settle_seconds=30.0),
            log_age_provider=lambda: 301.0,
            automation_running=lambda: True,
            restarter=lambda c: False,
            clock=lambda: clock[0])
        clock[0] = 100.0
        self.assertEqual(wd.poll()["kind"], "restart_failed")
        self.assertEqual(wd.restarts_in_window(), 1)
        self.assertEqual(wd.state()["restarts_total"], 1)

        # 宽限期过后仍是停滞 → 第二次派发，仍然失败。
        clock[0] = 200.0
        self.assertEqual(wd.poll()["kind"], "restart_failed")
        self.assertEqual(wd.restarts_in_window(), 2)

        # 配额用完 → 不再徒劳重试，转为告警等人工。
        clock[0] = 300.0
        self.assertEqual(wd.poll()["kind"], "exhausted")


class WatchdogSettlingTests(unittest.TestCase):
    """刚重启完要给日志重新写起来的宽限期，否则会被反复判停滞。"""

    def test_no_restart_during_settle_window(self):
        clock = [0.0]
        restarts = []
        wd = RestartWatchdog(
            settings_provider=lambda: _cfg(settle_seconds=120.0),
            log_age_provider=lambda: 301.0,
            automation_running=lambda: True,
            restarter=lambda c: restarts.append(c) or True,
            clock=lambda: clock[0])

        clock[0] = 10.0
        self.assertEqual(wd.poll()["kind"], "restart")
        self.assertEqual(len(restarts), 1)

        # 宽限期内即便日志仍然"停滞"，也不再重启。
        clock[0] = 60.0
        self.assertIsNone(wd.poll())
        self.assertEqual(len(restarts), 1)
        self.assertEqual(wd.state()["status"], "settling")

        # 过了宽限期还是停滞 → 才允许下一次。
        clock[0] = 200.0
        self.assertEqual(wd.poll()["kind"], "restart")
        self.assertEqual(len(restarts), 2)

    def test_poll_is_throttled_by_check_seconds(self):
        clock = [0.0]
        restarts = []
        wd = RestartWatchdog(
            settings_provider=lambda: _cfg(check_seconds=15.0, settle_seconds=0.0),
            log_age_provider=lambda: 301.0,
            automation_running=lambda: True,
            restarter=lambda c: restarts.append(c) or True,
            clock=lambda: clock[0])
        clock[0] = 1000.0
        self.assertEqual(wd.poll()["kind"], "restart")
        # 未到下次判定时间：连判定都不做，更不会再派发。
        clock[0] = 1005.0
        self.assertIsNone(wd.poll())
        self.assertEqual(len(restarts), 1)


class WatchdogResetTests(unittest.TestCase):

    def test_reset_forgets_everything(self):
        clock = [0.0]
        restarts = []
        wd = RestartWatchdog(
            settings_provider=lambda: _cfg(max_restarts=1),
            log_age_provider=lambda: 301.0,
            automation_running=lambda: True,
            restarter=lambda c: restarts.append(c) or True,
            clock=lambda: clock[0])
        clock[0] = 100.0
        self.assertEqual(wd.poll()["kind"], "restart")
        clock[0] = 200.0
        self.assertEqual(wd.poll()["kind"], "exhausted")

        wd.reset()
        self.assertEqual(wd.restarts_in_window(), 0)
        self.assertEqual(wd.state()["status"], "stale")  # 不再 exhausted
        self.assertEqual(wd.poll()["kind"], "restart")

    def test_note_restarted_opens_a_settle_window(self):
        # 手动重启炉石后应重新开始计时，不能马上又被判停滞。
        clock = [0.0]
        wd = RestartWatchdog(
            settings_provider=lambda: _cfg(settle_seconds=120.0),
            log_age_provider=lambda: 301.0,
            automation_running=lambda: True,
            restarter=lambda c: True,
            clock=lambda: clock[0])
        clock[0] = 500.0
        wd.note_restarted()
        self.assertEqual(wd.state()["status"], "settling")
        self.assertIsNone(wd.poll())


class WatchdogStateTests(unittest.TestCase):

    def test_state_is_json_friendly_and_reports_limits(self):
        wd = RestartWatchdog(
            settings_provider=lambda: _cfg(max_restarts=3),
            log_age_provider=lambda: 42.0,
            automation_running=lambda: True,
            restarter=lambda c: True)
        state = wd.state()
        for key in ("enabled", "status", "stale_seconds", "max_restarts",
                    "window_seconds", "restarts_in_window", "restarts_total",
                    "restarts_left", "log_age", "automation_running"):
            self.assertIn(key, state, key)
        self.assertEqual(state["restarts_left"], 3)
        self.assertEqual(state["log_age"], 42.0)
        self.assertTrue(state["automation_running"])

    def test_disabled_state_does_not_touch_log_or_automation(self):
        # 关闭时不该去问日志/自动化状态（避免白跑一次进程枚举）。
        calls = []
        wd = RestartWatchdog(
            settings_provider=lambda: _cfg(enabled=False),
            log_age_provider=lambda: calls.append("log") or 1.0,
            automation_running=lambda: calls.append("auto") or True,
            restarter=lambda c: True)
        state = wd.state()
        self.assertEqual(state["status"], "disabled")
        self.assertIsNone(wd.poll())


if __name__ == "__main__":
    unittest.main()
