"""定时窗口作为常驻外层约束：hold / keep_schedule / 到点四分支 / 结束暂停。

只验证 web_ui 装配层（监督器与配置都用替身，不真实点击、不真实起线程）；
监督器本身的 waiting_schedule 判定由 tests.test_resident_supervisor 覆盖。
"""

import threading
import time
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import web_ui


class _FakeResident:
    """监督器替身：state() 可指定，resume() 可挂一个“常驻真的把自动化拉起来”的副作用。"""

    def __init__(self, state="running", paused_reason=None, on_resume=None):
        self._state = state
        self._paused_reason = paused_reason
        self.on_resume = on_resume
        self.resumes = 0
        self.pauses = []

    def state(self):
        return {
            "enabled": True,
            "state": self._state,
            "paused_reason": self._paused_reason,
            "waiting_reason": (
                "等待定时 23:00 开始"
                if self._state == "waiting_schedule"
                else None
            ),
        }

    def resume(self):
        self.resumes += 1
        if self.on_resume is not None:
            self.on_resume()

    def request_pause(self, reason="paused"):
        self.pauses.append(str(reason))


class _DeadThread:
    """已退出的自动化线程替身（结束监控据此收尾）。"""

    def is_alive(self):
        return False


class _SyncThread:
    """把 api_start 起的 boot 线程变成同步执行，便于断言。"""

    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


def _resident_cfg(**overrides):
    cfg = {
        "enabled": True,
        "mode": "standard",
        "deck_index": None,
        "stale_in_game_seconds": 180.0,
        "stale_other_seconds": 300.0,
        "max_restarts": 3,
        "window_seconds": 600.0,
        "check_seconds": 0.0,
        "settle_seconds": 0.0,
        "start_delay_seconds": 0.0,
        "dry_run": False,
    }
    cfg.update(overrides)
    return cfg


class _ScheduleCase(unittest.TestCase):
    """保存/恢复 web_ui 全局运行态，测试之间互不污染。"""

    def setUp(self):
        self._saved = {
            "resident": web_ui.RESIDENT,
            "schedule": dict(web_ui.CTRL.schedule),
            "scheduler_thread": web_ui.CTRL.scheduler_thread,
            "autothread": web_ui.CTRL.automation_thread,
            "starting": web_ui.CTRL.starting,
            "phase": web_ui.CTRL.phase,
            "stopped_by": web_ui.CTRL.stopped_by,
            "last_error": web_ui.CTRL.last_error,
            "fsm": web_ui.CTRL.fsm,
            "arm_pending": web_ui._RESIDENT_ARM_PENDING,
            "arm_running": web_ui._RESIDENT_ARM_RUNNING,
        }
        web_ui.RESIDENT = None
        web_ui.CTRL.schedule = {"start": None, "end": None}
        web_ui.CTRL.scheduler_thread = None
        web_ui.CTRL.automation_thread = None
        web_ui.CTRL.starting = False
        web_ui.CTRL.phase = "idle"
        web_ui.CTRL.stopped_by = None
        web_ui.CTRL.last_error = None
        web_ui.CTRL.fsm = None
        web_ui._RESIDENT_ARM_PENDING = False
        web_ui._RESIDENT_ARM_RUNNING = False
        self.logs = []
        self.persisted = []

    def tearDown(self):
        web_ui.RESIDENT = self._saved["resident"]
        web_ui.CTRL.schedule = self._saved["schedule"]
        web_ui.CTRL.scheduler_thread = self._saved["scheduler_thread"]
        web_ui.CTRL.automation_thread = self._saved["autothread"]
        web_ui.CTRL.starting = self._saved["starting"]
        web_ui.CTRL.phase = self._saved["phase"]
        web_ui.CTRL.stopped_by = self._saved["stopped_by"]
        web_ui.CTRL.last_error = self._saved["last_error"]
        web_ui.CTRL.fsm = self._saved["fsm"]
        web_ui._RESIDENT_ARM_PENDING = self._saved["arm_pending"]
        web_ui._RESIDENT_ARM_RUNNING = self._saved["arm_running"]

    # -------------------------------------------------------------- 工具
    def _log(self, level, message):
        self.logs.append((str(level), str(message)))

    def _messages(self, level=None):
        return [
            message
            for lv, message in self.logs
            if level is None or lv == level
        ]

    @staticmethod
    def _past(seconds=60):
        return datetime.now() - timedelta(seconds=seconds)

    def _run_at_window(self, resident=None, settings=None, end_dt=None):
        """跑一次「到点」调度（开始时间已过，不在开始前等待）。"""
        start_dt = self._past()
        web_ui.CTRL.schedule = {"start": start_dt, "end": end_dt}
        if resident is not None:
            web_ui.RESIDENT = resident
        patchers = [
            patch.object(web_ui, "_log", side_effect=self._log),
            patch.object(
                web_ui, "_resident_settings",
                return_value=dict(settings if settings is not None else {"enabled": False}),
            ),
            patch.object(
                web_ui, "_persist_schedule",
                side_effect=lambda s, e: self.persisted.append((s, e)),
            ),
        ]
        for patcher in patchers:
            patcher.start()
        try:
            web_ui._schedule_worker(start_dt, end_dt)
        finally:
            for patcher in reversed(patchers):
                patcher.stop()
        return self.logs

    def _supervisor(self, settings=None, **overrides):
        from src.safety.resident_supervisor import ResidentSupervisor

        kwargs = dict(
            settings=lambda: dict(settings if settings is not None else _resident_cfg()),
            log_age=lambda: None,
            in_game=lambda: False,
            process_running=lambda: True,
            automation_running=lambda: True,
            stopped_by=lambda: web_ui.CTRL.stopped_by,
            start_automation=lambda: False,
            recover=lambda *args, **kwargs: False,
            alert=lambda message: None,
        )
        kwargs.update(overrides)
        return ResidentSupervisor(**kwargs)


# ------------------------------------------------------------------ hold
class ScheduleHoldTests(_ScheduleCase):
    def test_hold_returns_waiting_text_before_start(self):
        start = datetime.now() + timedelta(hours=2)
        web_ui.CTRL.schedule = {"start": start, "end": None}

        text = web_ui._resident_schedule_hold()

        self.assertEqual(f"等待定时 {start:%H:%M} 开始", text)

    def test_hold_is_none_without_plan_or_after_start(self):
        self.assertIsNone(web_ui._resident_schedule_hold())
        web_ui.CTRL.schedule = {"start": self._past(), "end": None}
        self.assertIsNone(web_ui._resident_schedule_hold())

    def test_supervisor_gets_schedule_hold_injected(self):
        captured = {}

        class _FakeSupervisor:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        with (
            patch.object(web_ui, "RESIDENT", None),
            patch("src.safety.resident_supervisor.ResidentSupervisor",
                  _FakeSupervisor),
            patch.object(web_ui, "_log"),
        ):
            supervisor = web_ui._resident_supervisor()

        self.assertIsInstance(supervisor, _FakeSupervisor)
        self.assertIs(captured["hold_reason"], web_ui._resident_schedule_hold)

    def test_cancelling_schedule_clears_hold_and_resident_starts_assembly(self):
        web_ui.CTRL.schedule = {"start": datetime.now() + timedelta(hours=1),
                                "end": None}
        started = []
        supervisor = self._supervisor(
            automation_running=lambda: False,
            start_automation=lambda: started.append(1) or True,
            hold_reason=web_ui._resident_schedule_hold,
        )

        first = supervisor.poll_once()
        self.assertEqual("waiting_schedule", first["kind"])
        self.assertEqual("waiting_schedule", supervisor.state()["state"])

        with patch.object(web_ui, "_persist_schedule"), \
                patch.object(web_ui, "_log"):
            result = web_ui.api_cancel_schedule()

        self.assertTrue(result["ok"])
        self.assertIsNone(web_ui._resident_schedule_hold())
        event = supervisor.poll_once()
        self.assertEqual("automation_started", event["kind"])
        self.assertEqual([1], started)          # 取消定时后常驻立刻进入装配/启动


# ------------------------------------------------------- keep_schedule
class ApiStartKeepScheduleTests(_ScheduleCase):
    def _start_env(self):
        persisted = []
        started = []

        def _start(reset_stats=True):
            started.append(reset_stats)
            return True, "自动化已启动"

        return persisted, started, [
            patch.object(web_ui, "load_config",
                         return_value={"name": "A#1", "log_root": "C:/logs"}),
            patch.object(web_ui.os.path, "isdir", return_value=True),
            patch.object(web_ui, "_log"),
            patch.object(web_ui, "_persist_schedule",
                         side_effect=lambda s, e: persisted.append((s, e))),
            patch.object(web_ui, "_start_automation", side_effect=_start),
            patch.object(web_ui.threading, "Thread", _SyncThread),
        ]

    def test_default_api_start_still_clears_schedule(self):
        web_ui.CTRL.schedule = {"start": datetime.now() + timedelta(hours=1),
                                "end": None}
        persisted, started, patchers = self._start_env()
        for patcher in patchers:
            patcher.start()
        try:
            result = web_ui.api_start({})
        finally:
            for patcher in reversed(patchers):
                patcher.stop()

        self.assertTrue(result["ok"])
        self.assertIsNone(web_ui.CTRL.schedule["start"])   # 旧语义：手动开始清定时
        self.assertEqual([(None, None)], persisted)
        self.assertEqual([True], started)

    def test_keep_schedule_api_start_really_keeps_plan(self):
        start = datetime.now() + timedelta(hours=1)
        web_ui.CTRL.schedule = {"start": start, "end": None}
        persisted, started, patchers = self._start_env()
        for patcher in patchers:
            patcher.start()
        try:
            result = web_ui.api_start({"keep_schedule": True})
        finally:
            for patcher in reversed(patchers):
                patcher.stop()

        self.assertTrue(result["ok"])
        self.assertIs(start, web_ui.CTRL.schedule["start"])  # 计划原样保留
        self.assertEqual([], persisted)                      # 也不落盘清计划
        self.assertEqual([True], started)

    def test_resident_start_helpers_pass_keep_schedule(self):
        seen = []

        def _api_start(body=None):
            seen.append(dict(body or {}))
            return {"ok": True}

        with (
            patch.object(web_ui, "_resident_stop_reason", return_value=None),
            patch.object(web_ui, "_resident_enabled", return_value=True),
            patch.object(web_ui, "api_start", side_effect=_api_start),
        ):
            self.assertTrue(web_ui._resident_start_after_ready("常驻开启"))

        web_ui._RESIDENT_ARM_PENDING = False
        with (
            patch.object(web_ui, "_resident_log_in_game", return_value=True),
            patch.object(web_ui, "_resident_stop_reason", return_value=None),
            patch.object(web_ui, "api_start", side_effect=_api_start),
        ):
            self.assertTrue(web_ui._resident_start_automation())

        self.assertEqual(2, len(seen))
        for body in seen:
            self.assertTrue(body.get("keep_score"))
            self.assertTrue(body.get("keep_schedule"))


# ------------------------------------------------------ 到点四分支
class ScheduleStartBranchTests(_ScheduleCase):
    def test_automation_already_running_skips_start(self):
        thread = _DeadThread()
        web_ui.CTRL.automation_thread = thread
        started = []
        with patch.object(
            web_ui, "_start_automation",
            side_effect=lambda **kwargs: started.append(1) or (True, ""),
        ):
            self._run_at_window()

        self.assertEqual([], started)                    # 防双线程：不再启动
        self.assertIs(thread, web_ui.CTRL.automation_thread)
        self.assertTrue(any("跳过本次启动" in m for m in self._messages()))

    def test_waiting_schedule_delegates_and_only_one_thread_is_created(self):
        fake = _FakeResident("waiting_schedule")

        def _resume():
            # 常驻自己装配成功后挂上唯一一个自动化线程。
            web_ui.CTRL.automation_thread = _DeadThread()

        fake.on_resume = _resume
        started = []
        with patch.object(
            web_ui, "_start_automation",
            side_effect=lambda **kwargs: started.append(1) or (True, ""),
        ):
            self._run_at_window(
                resident=fake, settings={"enabled": True, "dry_run": False})

        self.assertEqual([], started)                    # 不自己起第二个线程
        self.assertEqual(1, fake.resumes)
        self.assertTrue(web_ui._RESIDENT_ARM_PENDING)    # 登记了装配待办
        self.assertIsNotNone(web_ui.CTRL.automation_thread)
        self.assertTrue(any("交由常驻接管" in m for m in self._messages()))

    def test_countdown_delegates_too(self):
        fake = _FakeResident("countdown")

        def _resume():
            web_ui.CTRL.automation_thread = _DeadThread()

        fake.on_resume = _resume
        self._run_at_window(
            resident=fake, settings={"enabled": True, "dry_run": False})

        self.assertEqual(1, fake.resumes)
        self.assertTrue(web_ui._RESIDENT_ARM_PENDING)

    def test_paused_by_schedule_is_resumed_and_stale_stop_marker_cleared(self):
        fake = _FakeResident("paused", paused_reason="schedule")

        def _resume():
            web_ui.CTRL.automation_thread = _DeadThread()

        fake.on_resume = _resume
        web_ui.CTRL.stopped_by = "schedule"     # 上一个窗口结束留下的过期标记
        self._run_at_window(
            resident=fake, settings={"enabled": True, "dry_run": False})

        self.assertEqual(1, fake.resumes)
        self.assertIsNone(web_ui.CTRL.stopped_by)
        self.assertTrue(web_ui._RESIDENT_ARM_PENDING)

    def test_paused_by_user_does_not_start_warns_and_clears_plan(self):
        fake = _FakeResident("paused", paused_reason="user_immediate")
        started = []
        with patch.object(
            web_ui, "_start_automation",
            side_effect=lambda **kwargs: started.append(1) or (True, ""),
        ):
            self._run_at_window(
                resident=fake, settings={"enabled": True, "dry_run": False})

        self.assertEqual([], started)
        self.assertEqual(0, fake.resumes)
        self.assertEqual("idle", web_ui.CTRL.phase)
        # should-fix6：跳过分支必须清计划并落盘，否则计划悬挂成 no-op。
        self.assertIsNone(web_ui.CTRL.schedule["start"])
        self.assertEqual([(None, None)], self.persisted)
        self.assertTrue(any("常驻已暂停" in m and "hsctl resident on" in m
                            for m in self._messages("WARN")))

    def test_paused_exhausted_does_not_start_and_clears_plan(self):
        fake = _FakeResident("paused_exhausted")
        started = []
        with patch.object(
            web_ui, "_start_automation",
            side_effect=lambda **kwargs: started.append(1) or (True, ""),
        ):
            self._run_at_window(
                resident=fake, settings={"enabled": True, "dry_run": False})

        self.assertEqual([], started)
        self.assertEqual(0, fake.resumes)
        self.assertIsNone(web_ui.CTRL.schedule["start"])
        self.assertEqual([(None, None)], self.persisted)

    def test_resident_off_uses_legacy_start(self):
        started = []
        with patch.object(
            web_ui, "_start_automation",
            side_effect=lambda **kwargs: started.append(1) or (True, ""),
        ):
            self._run_at_window(settings={"enabled": False, "dry_run": False})

        self.assertEqual([1], started)

    def test_dry_run_resident_uses_legacy_start(self):
        fake = _FakeResident("waiting_schedule")
        started = []
        with patch.object(
            web_ui, "_start_automation",
            side_effect=lambda **kwargs: started.append(1) or (True, ""),
        ):
            self._run_at_window(
                resident=fake, settings={"enabled": True, "dry_run": True})

        self.assertEqual([1], started)          # dry_run：常驻不派发，走旧路径
        self.assertEqual(0, fake.resumes)

    def test_dry_run_with_paused_resident_still_uses_legacy_start(self):
        """should-fix6：dry_run 时统一走旧启动，不能按 paused 分支整窗跳过。"""
        fake = _FakeResident("paused", paused_reason="user_immediate")
        started = []
        with patch.object(
            web_ui, "_start_automation",
            side_effect=lambda **kwargs: started.append(1) or (True, ""),
        ):
            self._run_at_window(
                resident=fake, settings={"enabled": True, "dry_run": True})

        self.assertEqual([1], started)
        self.assertEqual(0, fake.resumes)

    def test_delegate_timeout_pauses_resident_and_clears_plan(self):
        fake = _FakeResident("waiting_schedule")   # resume 不会产生自动化线程
        started = []
        with (
            patch.object(web_ui, "_start_automation",
                         side_effect=lambda **kwargs: started.append(1) or (True, "")),
            patch.object(web_ui, "RESIDENT_SCHEDULE_START_TIMEOUT_SECONDS", 0.01),
            patch.object(web_ui, "RESIDENT_SCHEDULE_START_POLL_SECONDS", 0.001),
        ):
            self._run_at_window(
                resident=fake, settings={"enabled": True, "dry_run": False})

        self.assertEqual([], started)
        self.assertEqual(1, fake.resumes)
        self.assertEqual("idle", web_ui.CTRL.phase)
        self.assertTrue(any("超时" in m for m in self._messages("ERROR")))
        # should-fix6：监控要退出时必须撤掉装配待办并暂停常驻，避免它事后才启动。
        self.assertFalse(web_ui._RESIDENT_ARM_PENDING)
        self.assertIn("定时启动超时", fake.pauses)
        self.assertIsNone(web_ui.CTRL.schedule["start"])
        self.assertEqual([(None, None)], self.persisted)


# ------------------------------------------------------ 结束逻辑
class ScheduleEndTests(_ScheduleCase):
    def test_window_end_still_marks_stopped_by_schedule(self):
        web_ui.CTRL.automation_thread = _DeadThread()
        stop_calls = []
        web_ui.CTRL.fsm = SimpleNamespace(
            request_stop_after_game=lambda: stop_calls.append(1))

        start_dt = self._past(120)
        end_dt = self._past(60)
        self._run_at_window(end_dt=end_dt)

        self.assertEqual("stopping", web_ui.CTRL.phase)
        self.assertEqual("schedule", web_ui.CTRL.stopped_by)
        self.assertEqual([1], stop_calls)

    def test_mid_window_thread_flap_does_not_end_monitoring(self):
        """blocking2：常驻自愈让线程短暂为 None，监控线程不能因此提前退出。"""
        start_dt = self._past(5)
        end_dt = datetime.now() + timedelta(seconds=0.9)
        fake = _DeadThread()
        web_ui.CTRL.schedule = {"start": start_dt, "end": end_dt}
        web_ui.CTRL.automation_thread = fake
        stop_calls = []
        web_ui.CTRL.fsm = SimpleNamespace(
            request_stop_after_game=lambda: stop_calls.append(1))
        with (
            patch.object(web_ui, "_log", side_effect=self._log),
            patch.object(web_ui, "_resident_settings",
                         return_value={"enabled": False}),
            # 线程启动稍慢时别落到真实启动路径（正常时序下走“已在跑 → 跳过”）。
            patch.object(web_ui, "_start_automation",
                         side_effect=lambda **kwargs: (True, "")),
            patch.object(web_ui, "_persist_schedule",
                         side_effect=lambda s, e: self.persisted.append((s, e))),
        ):
            worker = threading.Thread(
                target=web_ui._schedule_worker, args=(start_dt, end_dt),
                daemon=True)
            worker.start()
            time.sleep(0.3)
            web_ui.CTRL.automation_thread = None      # 自愈内部 stop：线程瞬时为 None
            time.sleep(0.5)                           # 跨过至少一次 0.5s 检查
            web_ui.CTRL.automation_thread = fake      # 自愈完成：线程回来了
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())

        # 旧实现会在 0.5s 检查点看到 None 就 return，到点不会停。
        self.assertEqual([1], stop_calls)
        self.assertEqual("schedule", web_ui.CTRL.stopped_by)

    def test_end_waits_for_thread_to_come_back_before_stopping(self):
        """blocking2：到点时线程暂时为 None（常驻正在重启）→ 等它回来再发停止请求。"""
        start_dt = self._past(120)
        end_dt = self._past(60)
        web_ui.CTRL.schedule = {"start": start_dt, "end": end_dt}
        stop_calls = []
        web_ui.CTRL.fsm = SimpleNamespace(
            request_stop_after_game=lambda: stop_calls.append(1))
        fake = _DeadThread()

        def _appear():
            time.sleep(0.1)
            web_ui.CTRL.automation_thread = fake

        bringer = threading.Thread(target=_appear, daemon=True)
        bringer.start()
        try:
            with (
                patch.object(web_ui, "_log", side_effect=self._log),
                patch.object(web_ui, "_resident_settings",
                             return_value={"enabled": False}),
                patch.object(web_ui, "_persist_schedule",
                             side_effect=lambda s, e: self.persisted.append((s, e))),
                patch.object(web_ui, "_start_automation",
                             side_effect=lambda **kwargs: (True, "")),
                patch.object(web_ui, "RESIDENT_SCHEDULE_START_TIMEOUT_SECONDS", 2.0),
                patch.object(web_ui, "RESIDENT_SCHEDULE_START_POLL_SECONDS", 0.02),
            ):
                web_ui._schedule_worker(start_dt, end_dt)
        finally:
            bringer.join(timeout=2)

        self.assertEqual([1], stop_calls)
        self.assertEqual("schedule", web_ui.CTRL.stopped_by)
        self.assertEqual([], self.persisted)      # 有线程可停 → 不清计划

    def test_end_timeout_without_any_thread_warns_and_clears_plan(self):
        """blocking2：到点后 600s 仍无线程可停 → WARN + 清计划落盘。"""
        start_dt = self._past(120)
        end_dt = self._past(60)
        web_ui.CTRL.schedule = {"start": start_dt, "end": end_dt}
        web_ui.CTRL.automation_thread = None
        web_ui.CTRL.fsm = SimpleNamespace(
            request_stop_after_game=lambda: self.fail("不该有线程可停"))
        with (
            patch.object(web_ui, "_log", side_effect=self._log),
            patch.object(web_ui, "_resident_settings",
                         return_value={"enabled": False}),
            patch.object(web_ui, "_persist_schedule",
                         side_effect=lambda s, e: self.persisted.append((s, e))),
            patch.object(web_ui, "_start_automation",
                         side_effect=lambda **kwargs: (True, "")),
            patch.object(web_ui, "RESIDENT_SCHEDULE_START_TIMEOUT_SECONDS", 0.15),
            patch.object(web_ui, "RESIDENT_SCHEDULE_START_POLL_SECONDS", 0.01),
        ):
            web_ui._schedule_worker(start_dt, end_dt)

        self.assertEqual([(None, None)], self.persisted)
        self.assertIsNone(web_ui.CTRL.schedule["start"])
        self.assertEqual("idle", web_ui.CTRL.phase)
        self.assertTrue(any("没有自动化线程可停" in m
                            for m in self._messages("WARN")))

    def test_end_wait_exits_when_plan_is_cancelled(self):
        """nit：等待线程回来期间计划被取消/替换 → 返回 cancelled 且不再动。"""
        start_dt = self._past(120)
        end_dt = self._past(60)
        web_ui.CTRL.schedule = {"start": start_dt, "end": end_dt}
        web_ui.CTRL.fsm = SimpleNamespace(
            request_stop_after_game=lambda: self.fail("计划已取消，不该停自动化"))

        def _cancel():
            time.sleep(0.1)
            web_ui.CTRL.schedule = {"start": None, "end": None}

        canceller = threading.Thread(target=_cancel, daemon=True)
        canceller.start()
        try:
            with (
                patch.object(web_ui, "_log", side_effect=self._log),
                patch.object(web_ui, "_resident_settings",
                             return_value={"enabled": False}),
                patch.object(web_ui, "_persist_schedule",
                             side_effect=lambda s, e: self.persisted.append((s, e))),
                patch.object(web_ui, "_start_automation",
                             side_effect=lambda **kwargs: (True, "")),
                patch.object(web_ui, "RESIDENT_SCHEDULE_START_TIMEOUT_SECONDS", 2.0),
                patch.object(web_ui, "RESIDENT_SCHEDULE_START_POLL_SECONDS", 0.02),
            ):
                web_ui._schedule_worker(start_dt, end_dt)
        finally:
            canceller.join(timeout=2)

        self.assertIsNone(web_ui.CTRL.schedule["start"])
        # 取消方负责落盘；等待者命中 cancelled 后不再清计划/不再 WARN 超时。
        self.assertEqual([], self.persisted)
        self.assertFalse(any("没有自动化线程可停" in m
                             for m in self._messages("WARN")))

    def test_schedule_stop_pauses_resident_with_schedule_reason(self):
        web_ui.CTRL.automation_thread = _DeadThread()
        web_ui.CTRL.fsm = SimpleNamespace(request_stop_after_game=lambda: None)
        self._run_at_window(end_dt=self._past(60))
        self.assertEqual("schedule", web_ui.CTRL.stopped_by)

        supervisor = self._supervisor(automation_running=lambda: True)
        event = supervisor.poll_once()

        self.assertEqual("paused", event["kind"])
        state = supervisor.state()
        self.assertEqual("paused", state["state"])
        self.assertEqual("schedule", state["paused_reason"])


# ------------------------------------------------------ 内部 stop 保计划
class ApiStopKeepScheduleTests(_ScheduleCase):
    """blocking3：内部 stop 不能毁掉定时计划；用户手动 stop 旧行为不变。"""

    def _stop(self, body, fsm=None):
        web_ui.CTRL.automation_thread = _DeadThread()
        web_ui.CTRL.fsm = fsm or SimpleNamespace(
            request_immediate_stop=lambda: None,
            request_stop_after_game=lambda: None,
        )
        base_cfg = {"schedule_start": "2026-11-01T23:00"}
        save_calls = []
        with (
            patch.object(web_ui, "load_config", return_value=dict(base_cfg)),
            patch.object(web_ui, "save_config",
                         side_effect=lambda cfg: save_calls.append(dict(cfg))),
            patch.object(web_ui, "_log"),
        ):
            result = web_ui.api_stop(body)
        return result, save_calls

    def test_internal_stop_keeps_schedule_and_disk_plan(self):
        start = datetime(2026, 11, 1, 23, 0)
        web_ui.CTRL.schedule = {"start": start, "end": None}

        result, saves = self._stop(
            {"mode": "now", "_resident_internal": True})

        self.assertTrue(result["ok"])
        self.assertIs(start, web_ui.CTRL.schedule["start"])
        self.assertEqual([], saves)          # 磁盘 schedule_start 原样保留

    def test_keep_schedule_flag_keeps_plan_without_internal_flag(self):
        start = datetime(2026, 11, 1, 23, 0)
        web_ui.CTRL.schedule = {"start": start, "end": None}

        result, saves = self._stop({"mode": "now", "keep_schedule": True})

        self.assertTrue(result["ok"])
        self.assertIs(start, web_ui.CTRL.schedule["start"])
        self.assertEqual([], saves)

    def test_user_stop_still_clears_schedule(self):
        start = datetime(2026, 11, 1, 23, 0)
        web_ui.CTRL.schedule = {"start": start, "end": None}

        result, saves = self._stop({"mode": "now"})

        self.assertTrue(result["ok"])
        self.assertIsNone(web_ui.CTRL.schedule["start"])
        self.assertEqual(1, len(saves))
        self.assertIsNone(saves[0]["schedule_start"])

    def test_internal_after_game_stop_keeps_window(self):
        start = datetime(2026, 11, 1, 23, 0)
        end = datetime(2026, 11, 2, 2, 0)
        web_ui.CTRL.schedule = {"start": start, "end": end}
        stop_calls = []

        result, saves = self._stop(
            {"mode": "after_game", "_resident_internal": True},
            fsm=SimpleNamespace(
                request_stop_after_game=lambda: stop_calls.append(1),
                request_immediate_stop=lambda: None,
            ),
        )

        self.assertTrue(result["ok"])
        self.assertIs(end, web_ui.CTRL.schedule["end"])
        self.assertEqual([1], stop_calls)
        self.assertEqual([], saves)


# ------------------------------------------------------ 状态合并
class ResidentStateScheduleTests(_ScheduleCase):
    def test_state_merges_waiting_reason_and_schedule_window(self):
        fake = _FakeResident("waiting_schedule")
        start = datetime(2026, 11, 1, 23, 0)
        end = datetime(2026, 11, 2, 2, 0)
        web_ui.CTRL.schedule = {"start": start, "end": end}
        with (
            patch.object(web_ui, "RESIDENT", fake),
            patch.object(web_ui, "_restart_snapshot", return_value={}),
        ):
            state = web_ui._resident_state()

        self.assertEqual("等待定时 23:00 开始", state["waiting_reason"])
        self.assertEqual("2026-11-01T23:00", state["schedule_start"])
        self.assertEqual("2026-11-02T02:00", state["schedule_end"])

    def test_state_schedule_fields_are_none_without_plan(self):
        fake = _FakeResident("running")
        with (
            patch.object(web_ui, "RESIDENT", fake),
            patch.object(web_ui, "_restart_snapshot", return_value={}),
        ):
            state = web_ui._resident_state()

        self.assertIsNone(state["schedule_start"])
        self.assertIsNone(state["schedule_end"])
        self.assertIsNone(state["waiting_reason"])


if __name__ == "__main__":
    unittest.main()
