# -*- coding: utf-8 -*-
"""常驻监督器（ResidentSupervisor）的判定/限流/暂停/线程测试。

监督器是"无人值守时把炉石和自动化自己拉回来"的大脑：判错会导致正常挂机
被反复重启、或真卡住时不管不问。这里把全部外部依赖（配置、日志年龄、
进程/自动化状态、停止来源、恢复/启动动作、时钟、sleep）注入成假的，
所以测试不依赖本机是否真的跑着炉石，也不会有任何实际点击/重启。
"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.safety.resident_supervisor import (  # noqa: E402
    PROCESS_GONE_GRACE_SECONDS,
    ResidentSupervisor,
    START_RATE_MAX,
)


def _cfg(**overrides):
    base = {
        "enabled": True,
        "mode": "standard",
        "deck_index": None,
        "stale_in_game_seconds": 180.0,
        "stale_other_seconds": 300.0,
        # 默认关掉提前告警，既有停滞/恢复用例保持原语义；warn 用例自行打开。
        "warn_seconds": 0.0,
        "max_restarts": 3,
        "window_seconds": 600.0,
        "check_seconds": 0.0,
        "settle_seconds": 0.0,
        "start_delay_seconds": 0.0,
        "dry_run": False,
    }
    base.update(overrides)
    return base


class _Harness:
    """收集假依赖的调用与返回值，方便逐分支断言。"""

    def __init__(self, cfg=None, **providers):
        self.cfg = _cfg(**(cfg or {}))
        self.clock = [0.0]
        self.calls = {
            "log_age": 0,
            "in_game": 0,
            "process_running": 0,
            "automation_running": 0,
            "stopped_by": 0,
        }
        self.values = {
            "log_age": 10.0,
            "in_game": False,
            "process_running": True,
            "automation_running": True,
            "stopped_by": None,
        }
        self.values.update(providers)
        self.start_calls = []
        self.recover_calls = []
        self.alerts = []
        self.warns = []
        self.start_result = True
        self.recover_result = True
        # 定时窗口挂起回调的返回值（None = 不挂起；异常对象 = 抛异常）。
        self.hold = None
        self.hold_calls = 0

    # ------------------------------------------------------------ providers
    def _provide(self, name):
        self.calls[name] += 1
        value = self.values[name]
        if isinstance(value, BaseException):
            raise value
        return value

    def log_age(self):
        return self._provide("log_age")

    def in_game(self):
        return self._provide("in_game")

    def process_running(self):
        return self._provide("process_running")

    def automation_running(self):
        return self._provide("automation_running")

    def stopped_by(self):
        return self._provide("stopped_by")

    def hold_reason(self):
        self.hold_calls += 1
        value = self.hold
        if isinstance(value, BaseException):
            raise value
        return value

    # ------------------------------------------------------------ actions
    def start_automation(self):
        self.start_calls.append(self.clock[0])
        if isinstance(self.start_result, BaseException):
            raise self.start_result
        return self.start_result

    def recover(self, cfg, reason):
        self.recover_calls.append((dict(cfg), reason))
        if isinstance(self.recover_result, BaseException):
            raise self.recover_result
        return self.recover_result

    def alert(self, message):
        self.alerts.append(message)

    def warn(self, message):
        self.warns.append(message)

    # ------------------------------------------------------------ helpers
    def build(self, sleep=None, thread=False):
        sup = ResidentSupervisor(
            settings=lambda: self.cfg,
            log_age=self.log_age,
            in_game=self.in_game,
            process_running=self.process_running,
            automation_running=self.automation_running,
            stopped_by=self.stopped_by,
            start_automation=self.start_automation,
            recover=self.recover,
            alert=self.alert,
            warn=self.warn,
            clock=lambda: self.clock[0],
            sleep=sleep or (lambda seconds: None),
            hold_reason=self.hold_reason,
        )
        if not thread:
            # 非线程用例不启动真线程：避免后台判定与断言竞争（线程行为单独测）。
            sup._ensure_thread = lambda: None
        return sup

    def advance(self, seconds):
        self.clock[0] += float(seconds)


class GatingTests(unittest.TestCase):
    """开关 / 倒计时 / 恢复中 / 暂停 / 取消等前置门控。"""

    def test_disabled_never_reads_providers_or_dispatches(self):
        h = _Harness(cfg={"enabled": False})
        sup = h.build()
        self.assertEqual("disabled", sup.state()["state"])
        self.assertIsNone(sup.poll_once())
        self.assertEqual("disabled", sup.state()["state"])
        self.assertEqual({}, {k: v for k, v in h.calls.items() if v})
        self.assertEqual([], h.start_calls)
        self.assertEqual([], h.recover_calls)

    def test_state_disabled_does_not_touch_providers(self):
        h = _Harness(cfg={"enabled": False})
        sup = h.build()
        state = sup.state()
        self.assertEqual("disabled", state["state"])
        self.assertIsNone(state["log_age"])
        self.assertFalse(state["in_game"])
        self.assertEqual({}, {k: v for k, v in h.calls.items() if v})

    def test_countdown_blocks_until_start_delay(self):
        h = _Harness(cfg={"start_delay_seconds": 5.0}, log_age=9999.0, in_game=True)
        sup = h.build()
        self.assertEqual("countdown", sup.state()["state"])
        self.assertIsNone(sup.poll_once())
        h.advance(4.9)
        self.assertIsNone(sup.poll_once())
        self.assertEqual("countdown", sup.state()["state"])
        self.assertEqual([], h.recover_calls)

    def test_countdown_finish_reports_event_then_judges(self):
        h = _Harness(cfg={"start_delay_seconds": 5.0},
                     log_age=9999.0, in_game=True)
        sup = h.build()
        h.advance(5.0)
        event = sup.poll_once()
        self.assertIsNotNone(event)
        self.assertEqual("countdown_done", event["kind"])
        self.assertEqual("starting", sup.state()["state"])
        # 下一次 poll 才真正判定：日志停滞 → 触发恢复。
        event = sup.poll_once()
        self.assertIsNotNone(event)
        self.assertEqual("restart", event["kind"])
        self.assertEqual("recovering", sup.state()["state"])
        self.assertEqual(1, len(h.recover_calls))

    def test_start_delay_zero_starts_immediately(self):
        h = _Harness()
        sup = h.build()
        h.values["log_age"] = 9999.0
        h.values["in_game"] = True
        event = sup.poll_once()
        self.assertEqual("restart", event["kind"])
        self.assertEqual("recovering", sup.state()["state"])

    def test_recovering_blocks_further_polls(self):
        h = _Harness(log_age=9999.0, in_game=True)
        sup = h.build()
        first = sup.poll_once()
        self.assertEqual("restart", first["kind"])
        self.assertTrue(sup.state()["recovering"])
        h.advance(1000.0)
        self.assertIsNone(sup.poll_once())          # 等 note_recovered 收尾
        self.assertEqual(1, len(h.recover_calls))

    def test_paused_blocks_polls(self):
        h = _Harness()
        sup = h.build()
        sup.pause("manual")
        self.assertEqual("paused", sup.state()["state"])
        self.assertIsNone(sup.poll_once())

    def test_cancelled_blocks_polls_until_resume(self):
        h = _Harness()
        sup = h.build()
        sup.start()
        sup.stop()
        self.assertEqual("cancelled", sup.state()["state"])
        self.assertIsNone(sup.poll_once())
        sup.resume()
        self.assertIn(sup.state()["state"], ("countdown", "starting"))
        self.assertIsNone(sup.poll_once())
        self.assertEqual("running", sup.state()["state"])

    def test_error_blocks_polls_until_resume(self):
        h = _Harness(log_age=9999.0, in_game=True)
        sup = h.build()
        sup.poll_once()
        sup.note_recovered(False)
        self.assertEqual("error", sup.state()["state"])
        self.assertIsNone(sup.poll_once())
        h.advance(10.0)
        sup.resume()
        event = sup.poll_once()
        self.assertEqual("restart", event["kind"])

    def test_recover_failure_message_explains_how_to_reenable(self):
        # should-fix5：失败后必须告诉用户「已停止自愈 + 怎么重新开启」，
        # 否则网页/浮窗只有一个 error 状态，用户不知道下一步做什么。
        h = _Harness(log_age=9999.0, in_game=True)
        sup = h.build()
        sup.poll_once()
        sup.note_recovered(False)
        state = sup.state()
        self.assertEqual("error", state["state"])
        self.assertEqual("recover_failed", state["last_event"])
        self.assertIn("停止自愈", state["message"])
        self.assertIn("hsctl resident on", state["message"])
        self.assertIn("重新恢复", state["message"])

    def test_start_from_disabled_arms_supervision(self):
        h = _Harness(cfg={"enabled": False})
        sup = h.build()
        sup.start()
        self.assertEqual("disabled", sup.state()["state"])   # 配置仍关闭
        h.cfg["enabled"] = True
        sup.start()
        self.assertIn(sup.state()["state"], ("starting", "countdown"))
        self.assertIsNone(sup.poll_once())
        self.assertEqual("running", sup.state()["state"])

    def test_repeated_start_does_not_reset_countdown(self):
        h = _Harness(cfg={"start_delay_seconds": 5.0})
        sup = h.build()
        h.advance(2.0)
        sup.start()          # 已在倒计时中：不能把 5s 重新续期
        self.assertEqual("countdown", sup.state()["state"])
        h.advance(3.0)
        event = sup.poll_once()
        self.assertEqual("countdown_done", event["kind"])


class HoldTests(unittest.TestCase):
    """定时窗口挂起（hold_reason）：窗口外待机、窗口到点恢复判定。

    契约（web_ui 注入 ``hold_reason`` 后依赖它）：
    * 非空 → ``waiting_schedule``，不派发启动/恢复、不占配额、不改 settle；
    * 同一 reason 只记一次事件（reason 变化时重新记录）；
    * 清空 → 回 ``running`` 并**当次**继续判定（不等 check_seconds）；
    * ``pause()`` / ``resume()`` 可进出等待态；暂停/错误等状态优先；
    * 回调抛异常按"不挂起"处理。
    """

    HOLD = "等待定时 23:00 开始"

    def test_hold_enters_waiting_schedule_without_actions(self):
        h = _Harness(automation_running=False, log_age=9999.0, in_game=True)
        h.hold = self.HOLD
        sup = h.build()
        event = sup.poll_once()
        self.assertIsNotNone(event)
        self.assertEqual("waiting_schedule", event["kind"])
        self.assertEqual(self.HOLD, event["reason"])
        self.assertIn("常驻待机", event["message"])
        state = sup.state()
        self.assertEqual("waiting_schedule", state["state"])
        self.assertEqual(self.HOLD, state["waiting_reason"])
        # 待机期间不拉起自动化、不派发恢复、不占配额。
        self.assertEqual([], h.start_calls)
        self.assertEqual([], h.recover_calls)
        self.assertEqual(0, state["restarts_total"])
        self.assertEqual(0, state["restarts_in_window"])

    def test_hold_checked_only_after_enabled_gate(self):
        h = _Harness(cfg={"enabled": False})
        h.hold = self.HOLD
        sup = h.build()
        self.assertIsNone(sup.poll_once())
        self.assertEqual("disabled", sup.state()["state"])
        self.assertEqual(0, h.hold_calls)               # 关开关时不调用挂起回调

    def test_same_reason_is_recorded_once(self):
        h = _Harness(automation_running=False)
        h.hold = self.HOLD
        sup = h.build()
        self.assertEqual("waiting_schedule", sup.poll_once()["kind"])
        self.assertIsNone(sup.poll_once())              # 同一 reason 去重
        h.advance(120.0)
        self.assertIsNone(sup.poll_once())
        state = sup.state()
        self.assertEqual("waiting_schedule", state["state"])
        self.assertEqual("waiting_schedule", state["last_event"])
        self.assertEqual([], h.start_calls)

    def test_reason_change_records_new_event(self):
        h = _Harness(automation_running=False)
        h.hold = self.HOLD
        sup = h.build()
        sup.poll_once()
        changed = "等待定时 01:30 开始"
        h.hold = changed
        event = sup.poll_once()
        self.assertIsNotNone(event)
        self.assertEqual("waiting_schedule", event["kind"])
        self.assertIn("01:30", event["message"])
        self.assertEqual(changed, sup.state()["waiting_reason"])

    def test_hold_cleared_returns_to_running_and_records_resume(self):
        h = _Harness(log_age=10.0)                      # 日志正常：恢复后无事可做
        h.hold = self.HOLD
        sup = h.build()
        sup.poll_once()
        h.hold = None
        self.assertIsNone(sup.poll_once())
        state = sup.state()
        self.assertEqual("running", state["state"])
        self.assertIsNone(state["waiting_reason"])
        self.assertEqual("resume", state["last_event"])
        self.assertIn("定时窗口已到", state["message"])

    def test_hold_cleared_judges_immediately_not_after_check_seconds(self):
        h = _Harness(cfg={"check_seconds": 60.0, "stale_in_game_seconds": 180.0},
                     log_age=9999.0, in_game=True)
        h.hold = self.HOLD
        sup = h.build()
        self.assertEqual("waiting_schedule", sup.poll_once()["kind"])
        h.hold = None
        # 恢复当次 poll 就判定（不等 check_seconds）：日志停滞 → 触发恢复。
        event = sup.poll_once()
        self.assertIsNotNone(event)
        self.assertEqual("restart", event["kind"])
        self.assertEqual("log_stale", event["reason"])

    def test_waiting_does_not_change_settle_or_quota(self):
        h = _Harness(log_age=9999.0, in_game=True, cfg={"settle_seconds": 120.0})
        sup = h.build()
        sup.poll_once()                                 # restart → recovering
        sup.note_recovered(True)                        # running + settle
        before = sup.state()
        h.advance(1.0)
        h.hold = self.HOLD
        sup.poll_once()                                 # 进入 waiting
        during = sup.state()
        self.assertEqual("waiting_schedule", during["state"])
        self.assertAlmostEqual(before["settle_left"] - 1.0,
                               during["settle_left"], places=6)
        self.assertEqual(before["restarts_in_window"],
                         during["restarts_in_window"])
        self.assertEqual(before["restarts_total"], during["restarts_total"])

    def test_hold_does_not_override_user_pause(self):
        h = _Harness(log_age=10.0)
        h.hold = self.HOLD
        sup = h.build()
        sup.poll_once()                                 # waiting_schedule
        sup.pause("user")
        state = sup.state()
        self.assertEqual("paused", state["state"])
        self.assertEqual("user", state["paused_reason"])
        self.assertIsNone(state["waiting_reason"])
        h.hold = "等待定时 08:00 开始"
        self.assertIsNone(sup.poll_once())              # 暂停优先，不会被拉回等待
        self.assertEqual("paused", sup.state()["state"])

    def test_hold_does_not_revive_error_state(self):
        h = _Harness(log_age=9999.0, in_game=True)
        sup = h.build()
        sup.poll_once()                                 # restart
        sup.note_recovered(False)                       # error
        h.hold = self.HOLD
        self.assertIsNone(sup.poll_once())
        self.assertEqual("error", sup.state()["state"])
        self.assertEqual(1, len(h.recover_calls))

    def test_pause_from_waiting_schedule(self):
        h = _Harness(log_age=10.0)
        h.hold = self.HOLD
        sup = h.build()
        sup.poll_once()
        sup.pause("user_stop")
        state = sup.state()
        self.assertEqual("paused", state["state"])
        self.assertEqual("user_stop", state["paused_reason"])
        self.assertIsNone(state["waiting_reason"])
        self.assertEqual("paused", state["last_event"])

    def test_resume_from_waiting_schedule_enters_countdown(self):
        h = _Harness(cfg={"start_delay_seconds": 5.0})
        h.hold = self.HOLD
        sup = h.build()
        self.assertEqual("countdown", sup.state()["state"])
        sup.poll_once()                                 # hold → waiting
        self.assertEqual("waiting_schedule", sup.state()["state"])
        h.hold = None
        sup.resume()
        state = sup.state()
        self.assertEqual("countdown", state["state"])
        self.assertIsNone(state["waiting_reason"])
        self.assertIsNone(sup.poll_once())              # 倒计时没到不判定
        self.assertEqual([], h.recover_calls)

    def test_hold_exception_is_treated_as_none(self):
        h = _Harness(log_age=10.0)
        h.hold = RuntimeError("schedule provider down")
        sup = h.build()
        self.assertIsNone(sup.poll_once())              # 不抛、不挂起
        state = sup.state()
        self.assertEqual("running", state["state"])
        self.assertIsNone(state["waiting_reason"])
        self.assertGreaterEqual(h.hold_calls, 1)

    def test_hold_empty_string_is_treated_as_none(self):
        h = _Harness(log_age=10.0)
        h.hold = "   "
        sup = h.build()
        self.assertIsNone(sup.poll_once())
        self.assertEqual("running", sup.state()["state"])
        self.assertIsNone(sup.state()["waiting_reason"])


class ProcessGoneTests(unittest.TestCase):
    """炉石进程消失：连续 ≥6s 才动作，且期间不误拉起自动化。"""

    def test_process_gone_needs_six_seconds(self):
        h = _Harness(process_running=False)
        sup = h.build()
        self.assertIsNone(sup.poll_once())
        h.advance(PROCESS_GONE_GRACE_SECONDS - 0.1)
        self.assertIsNone(sup.poll_once())
        self.assertEqual([], h.recover_calls)
        h.advance(0.2)
        event = sup.poll_once()
        self.assertEqual("restart", event["kind"])
        self.assertEqual("process_gone", event["reason"])
        self.assertEqual("process_gone", h.recover_calls[0][1])

    def test_process_gone_timer_resets_when_process_returns(self):
        h = _Harness(process_running=False)
        sup = h.build()
        self.assertIsNone(sup.poll_once())              # 0s：开始计时
        h.advance(2.0)
        h.values["process_running"] = True
        self.assertIsNone(sup.poll_once())              # 2s：回来了，计时清零
        h.advance(1.0)
        h.values["process_running"] = False
        self.assertIsNone(sup.poll_once())              # 3s：重新计时
        h.advance(5.5)
        self.assertIsNone(sup.poll_once())              # 8.5s：距重新计时只 5.5s
        self.assertEqual([], h.recover_calls)
        h.advance(0.6)
        event = sup.poll_once()                         # 距重新计时 6.1s → 动作
        self.assertEqual("process_gone", event["reason"])

    def test_process_gone_does_not_start_automation_while_waiting(self):
        h = _Harness(process_running=False, automation_running=False)
        sup = h.build()
        self.assertIsNone(sup.poll_once())
        h.advance(3.0)
        self.assertIsNone(sup.poll_once())
        self.assertEqual([], h.start_calls)             # 进程没了，先别拉自动化

    def test_dry_run_process_gone_does_not_dispatch(self):
        h = _Harness(cfg={"dry_run": True}, process_running=False)
        sup = h.build()
        self.assertIsNone(sup.poll_once())
        h.advance(PROCESS_GONE_GRACE_SECONDS + 0.5)
        event = sup.poll_once()
        self.assertEqual("would_restart", event["kind"])
        self.assertEqual("process_gone", event["reason"])
        self.assertTrue(event["dry_run"])
        self.assertEqual([], h.recover_calls)
        self.assertEqual(0, sup.state()["restarts_total"])


class AutomationTests(unittest.TestCase):
    """自动化停摆：常驻负责拉起，但有 10 分钟 3 次的限流。"""

    def test_start_automation_when_not_running(self):
        h = _Harness(automation_running=False)
        sup = h.build()
        event = sup.poll_once()
        self.assertEqual("automation_started", event["kind"])
        self.assertEqual(1, len(h.start_calls))
        self.assertEqual("starting", sup.state()["state"])

    def test_no_start_when_automation_running(self):
        h = _Harness(automation_running=True)
        sup = h.build()
        self.assertIsNone(sup.poll_once())
        self.assertEqual([], h.start_calls)
        self.assertEqual("running", sup.state()["state"])

    def test_start_automation_is_rate_limited(self):
        h = _Harness(automation_running=False)
        sup = h.build()
        for index in range(START_RATE_MAX):
            h.advance(1.0)
            event = sup.poll_once()
            self.assertEqual("automation_started", event["kind"], index)
        h.advance(1.0)
        event = sup.poll_once()
        self.assertEqual("error", event["kind"])
        self.assertEqual(START_RATE_MAX, len(h.start_calls))
        self.assertEqual("error", sup.state()["state"])
        self.assertEqual(1, len(h.alerts))
        self.assertIn("上限", h.alerts[0])
        # error 后不再判定，也不再告警刷屏。
        h.advance(1000.0)
        self.assertIsNone(sup.poll_once())
        self.assertEqual(1, len(h.alerts))

    def test_start_failure_is_reported(self):
        h = _Harness(automation_running=False)
        h.start_result = False
        sup = h.build()
        event = sup.poll_once()
        self.assertEqual("start_failed", event["kind"])
        self.assertEqual(1, len(h.alerts))
        self.assertEqual("starting", sup.state()["state"])

    def test_start_exception_does_not_kill_the_call(self):
        h = _Harness(automation_running=False)
        h.start_result = RuntimeError("api_start exploded")
        sup = h.build()
        event = sup.poll_once()
        self.assertEqual("start_failed", event["kind"])
        self.assertTrue(any("异常" in message for message in h.alerts))

    def test_dry_run_does_not_start_automation(self):
        h = _Harness(cfg={"dry_run": True}, automation_running=False)
        sup = h.build()
        event = sup.poll_once()
        self.assertEqual("would_start_automation", event["kind"])
        self.assertTrue(event["dry_run"])
        self.assertEqual([], h.start_calls)

    def test_stopped_by_pauses_instead_of_starting(self):
        h = _Harness(automation_running=False, stopped_by="user_cancel")
        sup = h.build()
        event = sup.poll_once()
        self.assertEqual("paused", event["kind"])
        self.assertEqual("user_cancel", event["reason"])
        self.assertEqual("paused", sup.state()["state"])
        self.assertEqual("user_cancel", sup.state()["paused_reason"])
        self.assertEqual([], h.start_calls)
        self.assertIsNone(sup.poll_once())              # 暂停后不再判定


class StaleLogTests(unittest.TestCase):
    """日志停滞：对局中 / 非对局界面用不同阈值。"""

    def test_in_game_uses_stale_in_game_threshold(self):
        h = _Harness(log_age=179.0, in_game=True)
        sup = h.build()
        self.assertIsNone(sup.poll_once())
        h.values["log_age"] = 180.0
        event = sup.poll_once()
        self.assertEqual("restart", event["kind"])
        self.assertEqual("log_stale", event["reason"])

    def test_other_screens_use_stale_other_threshold(self):
        h = _Harness(log_age=299.0, in_game=False)
        sup = h.build()
        self.assertIsNone(sup.poll_once())
        h.values["log_age"] = 300.0
        event = sup.poll_once()
        self.assertEqual("restart", event["kind"])
        self.assertEqual("log_stale", event["reason"])

    def test_in_game_provider_error_falls_back_to_other_threshold(self):
        h = _Harness(log_age=190.0, in_game=RuntimeError("fsm down"))
        sup = h.build()
        # 若误当成"对局中"（阈值 180）会触发；正确应回退到 300 阈值。
        self.assertIsNone(sup.poll_once())
        self.assertEqual([], h.recover_calls)

    def test_missing_log_age_does_nothing(self):
        h = _Harness(log_age=None)
        sup = h.build()
        self.assertIsNone(sup.poll_once())
        self.assertEqual([], h.recover_calls)

    def test_log_age_provider_error_does_nothing(self):
        h = _Harness(log_age=RuntimeError("no power.log"))
        sup = h.build()
        self.assertIsNone(sup.poll_once())
        self.assertEqual([], h.recover_calls)

    def test_recover_receives_config_and_reason(self):
        h = _Harness(log_age=9999.0, in_game=True)
        sup = h.build()
        event = sup.poll_once()
        self.assertEqual("restart", event["kind"])
        cfg, reason = h.recover_calls[0]
        self.assertEqual("log_stale", reason)
        self.assertTrue(cfg["enabled"])
        self.assertEqual(180.0, cfg["stale_in_game_seconds"])

    def test_check_seconds_throttles_judgement(self):
        h = _Harness(cfg={"check_seconds": 60.0}, log_age=100.0, in_game=True)
        sup = h.build()
        self.assertIsNone(sup.poll_once())              # 首次判定：未到阈值
        h.values["log_age"] = 9999.0
        h.advance(30.0)
        self.assertIsNone(sup.poll_once())              # 未到下次判定
        self.assertEqual([], h.recover_calls)
        h.advance(30.0)
        event = sup.poll_once()
        self.assertEqual("restart", event["kind"])
        self.assertEqual(1, len(h.recover_calls))


class QuotaAndSettleTests(unittest.TestCase):
    """滑动窗口配额 + 恢复后宽限期。"""

    def test_quota_exhausted_pauses_and_alerts_once(self):
        h = _Harness(cfg={"max_restarts": 2}, log_age=9999.0, in_game=True)
        sup = h.build()
        self.assertEqual("restart", sup.poll_once()["kind"])
        sup.note_recovered(True)
        h.advance(10.0)
        self.assertEqual("restart", sup.poll_once()["kind"])
        sup.note_recovered(True)
        h.advance(20.0)
        event = sup.poll_once()
        self.assertEqual("quota_exhausted", event["kind"])
        self.assertEqual("paused_exhausted", sup.state()["state"])
        self.assertEqual(2, len(h.recover_calls))
        self.assertEqual(1, len(h.alerts))
        self.assertEqual(2, sup.state()["restarts_in_window"])
        self.assertEqual(0, sup.state()["restarts_left"])
        # 耗尽后不再判定 / 不再重复告警。
        h.advance(30.0)
        self.assertIsNone(sup.poll_once())
        self.assertEqual(1, len(h.alerts))

    def test_failed_recover_still_consumes_quota(self):
        h = _Harness(log_age=9999.0, in_game=True, cfg={"settle_seconds": 50.0})
        h.recover_result = False
        sup = h.build()
        event = sup.poll_once()
        self.assertEqual("restart_failed", event["kind"])
        self.assertEqual("running", sup.state()["state"])
        self.assertEqual(1, sup.state()["restarts_in_window"])
        self.assertEqual(1, sup.state()["restarts_total"])

    def test_settle_window_blocks_immediate_retrigger(self):
        h = _Harness(log_age=9999.0, in_game=True, cfg={"settle_seconds": 120.0})
        sup = h.build()
        self.assertEqual("restart", sup.poll_once()["kind"])
        sup.note_recovered(True)
        self.assertGreater(sup.state()["settle_left"], 0.0)
        h.advance(60.0)
        self.assertIsNone(sup.poll_once())              # 宽限期内不判定
        self.assertEqual(1, len(h.recover_calls))
        h.advance(61.0)
        self.assertEqual("restart", sup.poll_once()["kind"])

    def test_note_recovered_success_clears_recovering(self):
        h = _Harness(log_age=9999.0, in_game=True, cfg={"settle_seconds": 30.0})
        sup = h.build()
        sup.poll_once()
        self.assertTrue(sup.state()["recovering"])
        sup.note_recovered(True)
        state = sup.state()
        self.assertFalse(state["recovering"])
        self.assertEqual("running", state["state"])
        self.assertGreater(state["settle_left"], 0.0)

    def test_quota_window_slides_after_pause(self):
        h = _Harness(cfg={"max_restarts": 1, "window_seconds": 600.0},
                     log_age=9999.0, in_game=True)
        sup = h.build()
        self.assertEqual("restart", sup.poll_once()["kind"])
        sup.note_recovered(True)
        h.advance(10.0)
        self.assertEqual("quota_exhausted", sup.poll_once()["kind"])
        sup.resume()
        h.advance(700.0)                                # 配额窗口滑过
        event = sup.poll_once()
        self.assertEqual("restart", event["kind"])
        self.assertEqual(2, len(h.recover_calls))


class PauseTests(unittest.TestCase):
    """手动暂停（含"恢复中请求暂停"的延后生效语义）。"""

    def test_pause_while_running_then_resume(self):
        h = _Harness(log_age=10.0)
        sup = h.build()
        sup.poll_once()
        self.assertEqual("running", sup.state()["state"])
        sup.pause("manual")
        self.assertEqual("paused", sup.state()["state"])
        self.assertEqual("manual", sup.state()["paused_reason"])
        self.assertIsNone(sup.poll_once())
        sup.resume()
        self.assertIsNone(sup.poll_once())
        self.assertEqual("running", sup.state()["state"])

    def test_pause_during_countdown_cancels_it(self):
        h = _Harness(cfg={"start_delay_seconds": 10.0})
        sup = h.build()
        sup.pause("user")
        self.assertEqual("paused", sup.state()["state"])
        h.advance(20.0)
        self.assertIsNone(sup.poll_once())
        self.assertEqual("paused", sup.state()["state"])

    def test_request_pause_during_recovering_defers_until_note_recovered(self):
        h = _Harness(log_age=9999.0, in_game=True)
        sup = h.build()
        sup.poll_once()
        self.assertEqual("recovering", sup.state()["state"])
        sup.request_pause("user_stop")
        # 恢复还没收尾：状态保持 recovering，只记 pending。
        self.assertEqual("recovering", sup.state()["state"])
        self.assertEqual("user_stop", sup.state()["paused_reason"])
        sup.note_recovered(True)
        self.assertEqual("paused", sup.state()["state"])
        self.assertEqual("user_stop", sup.state()["paused_reason"])
        self.assertIsNone(sup.poll_once())

    def test_stopped_by_pauses_and_records_reason(self):
        h = _Harness(stopped_by="user_cancel")
        sup = h.build()
        sup.poll_once()
        self.assertEqual("paused", sup.state()["state"])
        self.assertEqual("user_cancel", sup.state()["paused_reason"])

    def test_disable_blocks_poll_until_resume(self):
        h = _Harness()
        sup = h.build()
        sup.poll_once()
        sup.disable()
        self.assertEqual("disabled", sup.state()["state"])
        self.assertIsNone(sup.poll_once())
        self.assertEqual("disabled", sup.state()["state"])
        sup.resume()
        self.assertIn(sup.state()["state"], ("starting", "countdown"))
        self.assertIsNone(sup.poll_once())
        self.assertEqual("running", sup.state()["state"])


class NoteAutomationStartedTests(unittest.TestCase):
    """外部刚启动自动化：进入 settle 宽限期，旧日志年龄不算卡死。

    用户反馈场景：人为暂停很久后点「开始对战」，暂停期间 Power.log 攒下
    很大的年龄，常驻不该立刻把它当卡死接管。
    """

    def test_settle_window_suppresses_stale_judgement(self):
        h = _Harness(log_age=9999.0, in_game=True, cfg={"settle_seconds": 120.0})
        sup = h.build()
        sup.note_automation_started()
        self.assertGreater(sup.state()["settle_left"], 0.0)
        h.advance(60.0)
        self.assertIsNone(sup.poll_once())              # 宽限期内不判卡死
        self.assertEqual([], h.recover_calls)
        h.advance(61.0)
        event = sup.poll_once()                         # 宽限期过后照常判定
        self.assertIsNotNone(event)
        self.assertEqual("restart", event["kind"])
        self.assertEqual("log_stale", event["reason"])
        self.assertEqual(1, len(h.recover_calls))

    def test_does_not_touch_quota_or_record_event(self):
        h = _Harness(log_age=9999.0, in_game=True, cfg={"settle_seconds": 30.0})
        sup = h.build()
        self.assertEqual("restart", sup.poll_once()["kind"])   # 占 1 次配额
        sup.note_recovered(True)                        # running / recovered
        before = sup.state()
        sup.note_automation_started()
        after = sup.state()
        self.assertEqual(before["restarts_total"], after["restarts_total"])
        self.assertEqual(before["restarts_in_window"],
                         after["restarts_in_window"])
        self.assertEqual("recovered", after["last_event"])     # 不记事件
        self.assertEqual("running", after["state"])

    def test_does_not_change_paused_state(self):
        h = _Harness()
        sup = h.build()
        sup.pause("user")
        sup.note_automation_started()
        state = sup.state()
        self.assertEqual("paused", state["state"])
        self.assertEqual("user", state["paused_reason"])
        self.assertEqual("paused", state["last_event"])

    def test_does_not_change_disabled_state(self):
        h = _Harness()
        sup = h.build()
        sup.disable()
        sup.note_automation_started()
        self.assertEqual("disabled", sup.state()["state"])

    def test_state_starting_is_preserved(self):
        h = _Harness(cfg={"start_delay_seconds": 0.0})
        sup = h.build()
        self.assertEqual("starting", sup.state()["state"])
        sup.note_automation_started()
        self.assertEqual("starting", sup.state()["state"])


class PauseRaceTests(unittest.TestCase):
    """判定与用户暂停的竞态：已暂停/已取消时绝不能偷偷拉起自动化。"""

    def test_pause_between_decision_and_dispatch_blocks_start(self):
        h = _Harness(automation_running=False)
        sup = h.build()

        def pause_then_report_stopped():
            # 模拟 poll 读到"自动化没在跑"之后、派发之前用户按了暂停。
            sup.pause("user")
            return False

        sup._automation_running = pause_then_report_stopped
        self.assertIsNone(sup.poll_once())              # 不派发、不返回事件
        self.assertEqual([], h.start_calls)
        state = sup.state()
        self.assertEqual("paused", state["state"])
        self.assertEqual("user", state["paused_reason"])

    def test_stop_between_decision_and_dispatch_blocks_start(self):
        h = _Harness(automation_running=False)
        sup = h.build()

        def stop_then_report_stopped():
            sup.stop()
            return False

        sup._automation_running = stop_then_report_stopped
        self.assertIsNone(sup.poll_once())
        self.assertEqual([], h.start_calls)
        self.assertEqual("cancelled", sup.state()["state"])

    def test_pause_inside_start_callback_prevents_next_dispatch(self):
        h = _Harness(automation_running=False)
        sup = h.build()
        calls = []

        def start_and_pause():
            calls.append(sup._clock())
            sup.pause("user")
            return True

        sup._start_automation = start_and_pause
        event = sup.poll_once()
        self.assertEqual("automation_started", event["kind"])
        self.assertEqual(1, len(calls))
        self.assertEqual("paused", sup.state()["state"])
        self.assertIsNone(sup.poll_once())              # 暂停后第二轮不再派发
        self.assertEqual(1, len(calls))

    def test_aborted_dispatch_does_not_consume_start_quota(self):
        h = _Harness(automation_running=False)
        sup = h.build()

        def pause_then_report_stopped():
            sup.pause("user")
            return False

        sup._automation_running = pause_then_report_stopped
        for _ in range(START_RATE_MAX + 1):
            self.assertIsNone(sup.poll_once())          # 每次都被暂停拦下
            sup.resume()                                # 回到 starting 再来
        sup._automation_running = lambda: False
        event = sup.poll_once()
        self.assertEqual("automation_started", event["kind"])
        self.assertEqual(1, len(h.start_calls))


class PauseFromDisabledTests(unittest.TestCase):
    """``pause()`` 必须能作用于 ``disabled``（配置 enabled 但还没开跑）。"""

    def test_pause_from_disabled_is_respected(self):
        h = _Harness(cfg={"enabled": False})
        sup = h.build()
        self.assertEqual("disabled", sup.state()["state"])
        sup.pause("user")
        state = sup.state()
        self.assertEqual("paused", state["state"])
        self.assertEqual("user", state["paused_reason"])
        # 配置改成 enabled：不能自动进 countdown 并接管。
        h.cfg["enabled"] = True
        self.assertIsNone(sup.poll_once())
        self.assertEqual("paused", sup.state()["state"])
        self.assertEqual([], h.start_calls)

    def test_pause_after_disable_is_respected(self):
        h = _Harness()
        sup = h.build()
        sup.disable()
        self.assertEqual("disabled", sup.state()["state"])
        sup.pause("user")
        self.assertEqual("paused", sup.state()["state"])
        self.assertEqual("user", sup.state()["paused_reason"])


class DryRunTests(unittest.TestCase):
    """dry_run：只判定/报告，不派发、不记账、不改状态。"""

    def test_stale_log_reports_without_dispatch(self):
        h = _Harness(cfg={"dry_run": True}, log_age=9999.0, in_game=True)
        sup = h.build()
        event = sup.poll_once()
        self.assertEqual("would_restart", event["kind"])
        self.assertEqual("log_stale", event["reason"])
        self.assertTrue(event["dry_run"])
        self.assertEqual([], h.recover_calls)
        self.assertEqual(0, sup.state()["restarts_total"])
        self.assertEqual("running", sup.state()["state"])
        self.assertEqual([], h.alerts)

    def test_repeated_dry_run_still_reports(self):
        h = _Harness(cfg={"dry_run": True}, log_age=9999.0, in_game=True)
        sup = h.build()
        for _ in range(2):
            event = sup.poll_once()
            self.assertEqual("would_restart", event["kind"])
        self.assertEqual(0, sup.state()["restarts_total"])

    def test_stopped_by_reports_without_pausing(self):
        h = _Harness(cfg={"dry_run": True}, stopped_by="user_cancel")
        sup = h.build()
        event = sup.poll_once()
        self.assertEqual("would_pause", event["kind"])
        self.assertEqual("user_cancel", event["reason"])
        self.assertTrue(event["dry_run"])
        # dry_run 不改状态：构造时 start_delay=0 停在 starting，不会转 paused。
        self.assertIn(sup.state()["state"], ("starting", "running"))


class WarnTests(unittest.TestCase):
    """提前告警（warn_seconds，原存活检测告警档并入守护）：只报一次、不动作。"""

    def _warn_harness(self, **cfg):
        base = {"warn_seconds": 120.0}
        base.update(cfg)
        h = _Harness(cfg=base, log_age=130.0, in_game=True)
        return h, h.build()

    def test_between_warn_and_threshold_reports_once(self):
        h, sup = self._warn_harness()

        event = sup.poll_once()
        self.assertIsNotNone(event)
        self.assertEqual("stale_warning", event["kind"])
        self.assertEqual("log_stale", event["reason"])
        self.assertFalse(event["dry_run"])
        self.assertIn("提前告警", event["message"])
        self.assertIn("将自动恢复", event["message"])
        self.assertEqual(1, len(h.warns))
        self.assertEqual(event["message"], h.warns[0])
        # 只告警、不动作、不占配额。
        self.assertEqual([], h.recover_calls)
        self.assertEqual([], h.start_calls)
        self.assertEqual([], h.alerts)
        self.assertEqual(0, sup.state()["restarts_in_window"])
        self.assertTrue(sup.state()["warned"])
        self.assertEqual(120.0, sup.state()["warn_seconds"])

        # 同一段停滞只报一次。
        self.assertIsNone(sup.poll_once())
        self.assertEqual(1, len(h.warns))

    def test_fresh_log_resets_then_warn_again(self):
        h, sup = self._warn_harness()
        self.assertEqual("stale_warning", sup.poll_once()["kind"])

        h.values["log_age"] = 10.0          # 日志重新写起来 → 复位
        self.assertIsNone(sup.poll_once())
        self.assertFalse(sup.state()["warned"])

        h.values["log_age"] = 150.0         # 再次停滞 → 再报一次
        self.assertEqual("stale_warning", sup.poll_once()["kind"])
        self.assertEqual(2, len(h.warns))

    def test_reaching_stop_threshold_still_recovers(self):
        h, sup = self._warn_harness()
        self.assertEqual("stale_warning", sup.poll_once()["kind"])

        h.values["log_age"] = 200.0         # 超过 180s 处理阈值
        event = sup.poll_once()
        self.assertEqual("restart", event["kind"])
        self.assertEqual(1, len(h.recover_calls))

    def test_note_automation_started_resets_warning(self):
        h, sup = self._warn_harness()
        self.assertEqual("stale_warning", sup.poll_once()["kind"])
        self.assertTrue(sup.state()["warned"])

        sup.note_automation_started()
        self.assertFalse(sup.state()["warned"])
        self.assertEqual("stale_warning", sup.poll_once()["kind"])
        self.assertEqual(2, len(h.warns))

    def test_note_recovered_resets_warning(self):
        h, sup = self._warn_harness()
        self.assertEqual("stale_warning", sup.poll_once()["kind"])

        sup.note_recovered(True)
        self.assertFalse(sup.state()["warned"])

    def test_countdown_restart_resets_warning(self):
        h, sup = self._warn_harness()
        self.assertEqual("stale_warning", sup.poll_once()["kind"])

        sup.pause("user_stop")
        sup.resume()                        # 重新倒计时
        self.assertFalse(sup.state()["warned"])

    def test_warn_seconds_zero_disables_warning(self):
        h, sup = self._warn_harness(warn_seconds=0.0)

        self.assertIsNone(sup.poll_once())
        self.assertEqual([], h.warns)
        self.assertFalse(sup.state()["warned"])

    def test_dry_run_does_not_warn(self):
        # dry_run 口径是「只判定、不告警、不动作」：提前告警也不发。
        h, sup = self._warn_harness(dry_run=True)

        self.assertIsNone(sup.poll_once())
        self.assertEqual([], h.warns)
        self.assertFalse(sup.state()["warned"])
        self.assertEqual([], h.recover_calls)

    def test_warning_message_uses_the_right_threshold_for_screen(self):
        h = _Harness(cfg={"warn_seconds": 120.0}, log_age=250.0, in_game=False)
        sup = h.build()
        event = sup.poll_once()
        self.assertEqual("stale_warning", event["kind"])
        # 非对局界面阈值 300s（不是对局中的 180s）。
        self.assertIn("300s 将自动恢复", event["message"])


class StateTests(unittest.TestCase):
    """state() 字段契约（R2/R3 按它渲染状态）。"""

    REQUIRED_KEYS = {
        "enabled", "state", "mode", "deck_index",
        "stale_in_game_seconds", "stale_other_seconds", "warn_seconds",
        "warned", "max_restarts",
        "window_seconds", "check_seconds", "settle_seconds",
        "start_delay_seconds", "dry_run", "restarts_in_window",
        "restarts_total", "restarts_left", "settle_left", "log_age",
        "in_game", "recovering", "paused_reason", "waiting_reason",
        "last_event", "message",
    }

    def test_state_exposes_the_contract_fields(self):
        h = _Harness(log_age=42.0, in_game=True)
        sup = h.build()
        sup.poll_once()
        state = sup.state()
        self.assertEqual(self.REQUIRED_KEYS, set(state))
        self.assertTrue(state["enabled"])
        self.assertEqual("running", state["state"])
        self.assertEqual("standard", state["mode"])
        self.assertIsNone(state["deck_index"])
        self.assertEqual(180.0, state["stale_in_game_seconds"])
        self.assertEqual(300.0, state["stale_other_seconds"])
        self.assertEqual(0.0, state["warn_seconds"])
        self.assertFalse(state["warned"])
        self.assertEqual(3, state["max_restarts"])
        self.assertEqual(600.0, state["window_seconds"])
        self.assertEqual(0.0, state["check_seconds"])
        self.assertEqual(0.0, state["settle_seconds"])
        self.assertEqual(0.0, state["start_delay_seconds"])
        self.assertFalse(state["dry_run"])
        self.assertEqual(0, state["restarts_in_window"])
        self.assertEqual(0, state["restarts_total"])
        self.assertEqual(3, state["restarts_left"])
        self.assertEqual(0.0, state["settle_left"])
        self.assertEqual(42.0, state["log_age"])
        self.assertTrue(state["in_game"])
        self.assertFalse(state["recovering"])
        self.assertIsNone(state["paused_reason"])
        # 非等待态 waiting_reason 必须是 None（R2/R3 据此渲染）。
        self.assertIsNone(state["waiting_reason"])
        # 还没发生任何事件时 last_event/message 都是 None（R2/R3 用 or "" 兜底）。
        self.assertIsNone(state["last_event"])
        self.assertIsNone(state["message"])

    def test_state_after_restart_shows_quota_and_settle(self):
        h = _Harness(log_age=9999.0, in_game=True, cfg={"settle_seconds": 120.0})
        sup = h.build()
        sup.poll_once()
        state = sup.state()
        self.assertEqual("recovering", state["state"])
        self.assertTrue(state["recovering"])
        self.assertEqual(1, state["restarts_in_window"])
        self.assertEqual(1, state["restarts_total"])
        self.assertEqual(2, state["restarts_left"])
        self.assertGreater(state["settle_left"], 0.0)
        self.assertEqual("restart", state["last_event"])
        self.assertIn("已触发第 1/3 次", state["message"])


class ThreadTests(unittest.TestCase):
    """后台线程：daemon、幂等、循环异常不能杀死线程。"""

    def test_thread_is_daemon_and_start_is_idempotent(self):
        h = _Harness()
        sup = h.build(sleep=lambda seconds: time.sleep(0.001), thread=True)
        sup.start()
        thread = sup._thread
        self.assertIsNotNone(thread)
        self.assertTrue(thread.daemon)
        sup.start()
        self.assertIs(sup._thread, thread)
        self.assertTrue(thread.is_alive())
        sup.stop()
        self.assertFalse(thread.is_alive())
        self.assertEqual("cancelled", sup.state()["state"])

    def test_loop_survives_poll_exceptions(self):
        h = _Harness()
        sup = h.build(sleep=lambda seconds: time.sleep(0.001), thread=True)
        calls = {"count": 0}

        def flaky_poll():
            calls["count"] += 1
            raise RuntimeError("provider exploded")

        sup.poll_once = flaky_poll          # 让每次判定都抛异常
        sup.start()
        deadline = time.time() + 3.0
        while calls["count"] < 5 and time.time() < deadline:
            time.sleep(0.005)
        thread = sup._thread
        self.assertGreaterEqual(calls["count"], 5)
        self.assertTrue(thread.is_alive())              # 线程没被异常杀死
        self.assertEqual(1, len(h.alerts))              # 同一异常告警被限流
        self.assertIn("常驻监督循环异常", h.alerts[0])
        sup.stop()
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
