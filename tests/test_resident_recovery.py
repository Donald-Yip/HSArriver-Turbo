"""常驻自动恢复链路：动作顺序 / 重连短路 / 失败不启动自动化 / 异常路径。

只验证 web_ui 的装配编排（mock 掉重启步骤、battle_entry、点击、api_start）；
停滞判定本身由 tests.test_resident_supervisor 覆盖。
"""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import web_ui


class _FakeResident:
    """监督器替身：只关心 note_recovered 收到了什么。"""

    def __init__(self):
        self.notes = []
        self.pauses = []

    def state(self):
        return {"enabled": True, "state": "recovering"}

    def pause(self, reason="paused"):
        self.pauses.append(str(reason))

    def note_recovered(self, ok):
        self.notes.append(bool(ok))

    def request_pause(self, reason):
        self.pauses.append(reason)

    def resume(self):
        pass

    def disable(self):
        pass


class _FakeBattleEntry:
    """battle_entry 替身：记录调用顺序，可注入结果与回调。"""

    def __init__(self):
        self.events = []
        self.cancelled = False
        self.enter_result = {"ok": True, "stage": "done",
                             "message": "已进入选择套牌界面"}
        self.deck_result = {"ok": True, "message": "已点第 1 个卡组"}
        self.on_enter = None
        self.enter_kwargs = None
        self.deck_calls = 0

    def clear_cancel(self):
        self.events.append("clear_cancel")

    def is_cancelled(self):
        return bool(self.cancelled)

    def enter_battle(self, **kwargs):
        self.events.append("enter")
        self.enter_kwargs = kwargs
        if self.on_enter is not None:
            self.on_enter()
        return dict(self.enter_result)

    def select_deck(self, index, logger=None):
        self.deck_calls += 1
        self.events.append(("deck", index))
        return dict(self.deck_result)

    def request_cancel(self):
        self.events.append("request_cancel")


class _SyncThread:
    """把 dispatch 起的工作线程变成同步执行，便于断言。"""

    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


class _RecoveryCase(unittest.TestCase):
    def setUp(self):
        self.fake_be = _FakeBattleEntry()
        self.events = []
        # battle_entry 的调用也记进同一条事件流，才能断言“谁先谁后”。
        self.fake_be.events = self.events
        self.finished = []
        self.alerts = []
        self.started = []
        self.mode_results = []
        self.log_sequence = []
        self.log_default = False
        self.steps_result = True
        self.steps_error = None
        self.steps_calls = 0
        self.api_start_result = {"ok": True, "message": "started"}

        self._saved_resident = web_ui.RESIDENT
        self._saved_restart = dict(web_ui._RESTART_STATE)
        self._saved_arm_running = web_ui._RESIDENT_ARM_RUNNING
        self._saved_arm_pending = web_ui._RESIDENT_ARM_PENDING
        self._saved_stopped_by = web_ui.CTRL.stopped_by
        self._saved_autothread = web_ui.CTRL.automation_thread
        self._saved_starting = web_ui.CTRL.starting
        self._saved_recovery_count = web_ui._RESIDENT_RECOVERY_COUNT
        web_ui._RESIDENT_RECOVERY_COUNT = 0       # 恢复成功次数：每个用例从 0 数起
        web_ui.CTRL.automation_thread = None
        web_ui.CTRL.starting = False
        web_ui.CTRL.stopped_by = None
        self.fake_resident = _FakeResident()
        web_ui.RESIDENT = self.fake_resident
        self._patches = [
            patch.dict(sys.modules, {"battle_entry": self.fake_be}),
            patch.object(web_ui, "_log"),
            patch.object(web_ui.traceback, "print_exc"),
            patch.object(web_ui, "_restart_stage", side_effect=self._stage),
            patch.object(web_ui, "_restart_finish", side_effect=self._finish),
            patch.object(web_ui, "_restart_hearthstone_steps",
                         side_effect=self._steps),
            patch.object(web_ui, "api_start", side_effect=self._api_start),
            patch.object(web_ui, "_resident_log_in_game",
                         side_effect=self._log_in_game),
            patch.object(web_ui, "_resident_fsm", side_effect=self._fsm),
            patch.object(web_ui, "_resident_enabled", return_value=True),
            patch.object(web_ui, "_resident_alert",
                         side_effect=lambda *args: self.alerts.append(args)),
            patch.object(web_ui, "_mode_click_inner",
                         side_effect=self._mode_click),
            patch.object(web_ui, "RESIDENT_SELECT_RETRY_DELAY_SECONDS", 0.0),
            patch.object(web_ui, "RESIDENT_RECONNECT_PROBE_SECONDS", 0.0),
            patch.object(web_ui, "RESIDENT_RECONNECT_PROBE_INTERVAL", 0.0),
        ]
        for patcher in self._patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self._patches):
            patcher.stop()
        web_ui.RESIDENT = self._saved_resident
        web_ui._RESTART_STATE.clear()
        web_ui._RESTART_STATE.update(self._saved_restart)
        web_ui._RESIDENT_ARM_RUNNING = self._saved_arm_running
        web_ui._RESIDENT_ARM_PENDING = self._saved_arm_pending
        web_ui.CTRL.stopped_by = self._saved_stopped_by
        web_ui.CTRL.automation_thread = self._saved_autothread
        web_ui.CTRL.starting = self._saved_starting
        web_ui._RESIDENT_RECOVERY_COUNT = self._saved_recovery_count

    # ---------------------------------------------------------- 替身实现
    def _stage(self, stage):
        self.events.append(("stage", stage))

    def _finish(self, stage, ok, message):
        self.finished.append((stage, bool(ok), message))
        self.events.append(("finish", stage))

    def _steps(self):
        self.steps_calls += 1
        self.events.append("steps")
        if self.steps_error is not None:
            raise self.steps_error
        return self.steps_result

    def _api_start(self, body=None):
        self.started.append(dict(body or {}))
        self.events.append("start")
        return dict(self.api_start_result)

    def _log_in_game(self, fsm=None):
        if self.log_sequence:
            return bool(self.log_sequence.pop(0))
        return bool(self.log_default)

    def _fsm(self):
        def _init():
            self.events.append("fsm_init")

        return SimpleNamespace(init=_init)

    def _mode_click(self, mode):
        self.events.append(("mode", mode))
        if self.mode_results:
            return dict(self.mode_results.pop(0))
        return {"ok": True, "mode": mode, "message": "clicked"}

    def _run(self, cfg=None):
        return web_ui._resident_recover_worker(
            dict(cfg or {"mode": "wild", "deck_index": 2}), "log_stale")

    def _plain_events(self):
        return [event for event in self.events if isinstance(event, str)]

    def _stages(self):
        return [
            event[1]
            for event in self.events
            if isinstance(event, tuple) and len(event) == 2
            and event[0] == "stage"
        ]


class NormalRecoveryTests(_RecoveryCase):
    def test_full_recovery_order(self):
        self.log_sequence = [False]  # 10s 探测：还没在局内
        self.assertTrue(self._run())

        self.assertEqual(
            ["clear_cancel", "steps", "fsm_init", "enter", "start"],
            self._plain_events(),
        )
        self.assertEqual(
            ["restarting", "waiting_session", "entering",
             "selecting_mode", "selecting_deck", "starting"],
            self._stages(),
        )
        self.assertIn(("mode", "wild"), self.events)
        self.assertEqual(1, self.fake_be.deck_calls)
        self.assertEqual([{"keep_score": True, "keep_schedule": True}], self.started)
        self.assertEqual([("recovered", True,
                           "常驻恢复：已重启并继续挂机")], self.finished)
        self.assertEqual([True], self.fake_resident.notes)
        # 恢复真正成功：本次运行的累计次数 +1（浮窗/日志显示「第 1 次」）。
        self.assertEqual(1, web_ui._RESIDENT_RECOVERY_COUNT)

    def test_deck_is_skipped_when_not_configured(self):
        self.assertTrue(self._run({"mode": "standard", "deck_index": None}))
        self.assertEqual(0, self.fake_be.deck_calls)
        self.assertNotIn("selecting_deck", self._stages())
        self.assertEqual([True], self.fake_resident.notes)


class ReconnectTests(_RecoveryCase):
    def test_enter_aborted_by_log_in_game_goes_reconnect_path(self):
        self.log_sequence = [False]  # 探测没读到在局内
        self.fake_be.enter_result = {"ok": False, "stage": "aborted",
                                     "message": "已在局内，停止点击"}

        def _enter():
            # enter_battle 内部下一轮 should_abort 就读到“已在局内” → 立刻停手
            self.log_default = True
            self.aborted = self.fake_be.enter_kwargs["should_abort"]()

        self.fake_be.on_enter = _enter
        self.assertTrue(self._run())

        self.assertTrue(self.aborted)
        self.assertEqual(
            ["clear_cancel", "steps", "fsm_init", "enter", "start"],
            self._plain_events(),
        )
        self.assertNotIn("selecting_mode", self._stages())
        self.assertNotIn("selecting_deck", self._stages())
        self.assertNotIn("mode", [event[0] for event in self.events
                                  if isinstance(event, tuple)])
        self.assertEqual(0, self.fake_be.deck_calls)
        self.assertEqual([True], self.fake_resident.notes)

    def test_should_abort_also_honours_manual_cancel(self):
        self.fake_be.enter_result = {"ok": False, "stage": "aborted",
                                     "message": "cancelled"}

        def _enter():
            # 点击循环里用户按了急停：should_abort 必须立刻返回 True。
            self.fake_be.cancelled = True
            self.aborted = self.fake_be.enter_kwargs["should_abort"]()

        self.fake_be.on_enter = _enter
        self.assertFalse(self._run())

        self.assertTrue(self.aborted)
        self.assertEqual("cancelled", self.finished[0][0])
        self.assertEqual([], self.started)

    def test_cancelled_entering_does_not_start_automation(self):
        self.fake_be.cancelled = True
        self.fake_be.enter_result = {"ok": False, "stage": "aborted",
                                     "message": "cancelled"}
        self.assertFalse(self._run())

        self.assertEqual([], self.started)
        self.assertEqual("cancelled", self.finished[0][0])
        self.assertTrue(self.fake_resident.pauses)   # 被取消 → 常驻必须停手

    def test_cancel_during_probe_stops_without_entering(self):
        self.fake_be.cancelled = True
        self.assertFalse(self._run())

        self.assertNotIn("enter", self._plain_events())
        self.assertEqual("cancelled", self.finished[0][0])
        self.assertEqual([], self.started)

    def test_cancel_after_reconnect_detection_does_not_start(self):
        self.log_default = True          # 探测直接判为“已在局内”
        self.fake_be.cancelled = True    # 但用户已经按了急停
        self.assertFalse(self._run())

        self.assertNotIn("enter", self._plain_events())
        self.assertEqual("cancelled", self.finished[0][0])
        self.assertEqual([], self.started)
        self.assertTrue(self.fake_resident.pauses)

    def test_disabling_resident_during_recovery_does_not_start(self):
        self.log_default = True
        with patch.object(web_ui, "_resident_enabled", return_value=False):
            self.assertFalse(self._run())

        self.assertEqual([], self.started)             # 常驻关了就不许再拉起
        self.assertEqual("cancelled", self.finished[0][0])
        self.assertEqual([False], self.fake_resident.notes)


class FailureTests(_RecoveryCase):
    def test_restart_steps_failure_stops_early(self):
        self.steps_result = False
        self.assertFalse(self._run())

        self.assertEqual([("restart_failed", False,
                           "常驻恢复：重启炉石失败（见日志）")], self.finished)
        self.assertNotIn("enter", self._plain_events())
        self.assertEqual([], self.started)
        self.assertEqual([False], self.fake_resident.notes)
        self.assertEqual(0, web_ui._RESIDENT_RECOVERY_COUNT)   # 失败不计恢复成功

    def test_entering_failure_does_not_start_automation(self):
        self.fake_be.enter_result = {"ok": False, "stage": "timeout_main_menu",
                                     "message": "没认出主界面"}
        self.assertFalse(self._run())

        self.assertEqual("recover_failed", self.finished[0][0])
        self.assertEqual([], self.started)
        self.assertEqual([False], self.fake_resident.notes)
        self.assertEqual(0, self.fake_be.deck_calls)

    def test_mode_selection_failure_never_starts_automation(self):
        self.mode_results = [
            {"ok": False, "error": "菜单没弹出"},
            {"ok": False, "error": "菜单没弹出"},
        ]
        self.assertFalse(self._run())

        mode_clicks = [e for e in self.events if isinstance(e, tuple)
                       and e[0] == "mode"]
        self.assertEqual(2, len(mode_clicks))      # 失败重试 1 次
        self.assertEqual("select_failed", self.finished[0][0])
        self.assertFalse(self.finished[0][1])
        self.assertEqual([], self.started)         # 关键：不启动自动化
        self.assertEqual([False], self.fake_resident.notes)

    def test_deck_selection_failure_never_starts_automation(self):
        self.fake_be.deck_result = {"ok": False, "skipped": True,
                                    "error": "第 2 格不像卡组"}
        self.assertFalse(self._run())

        self.assertEqual(2, self.fake_be.deck_calls)   # 2s 后重试 1 次
        self.assertEqual("select_failed", self.finished[0][0])
        self.assertEqual([], self.started)
        self.assertEqual([False], self.fake_resident.notes)

    def test_api_start_failure_marks_recovery_failed(self):
        self.api_start_result = {"ok": False, "error": "自动化初始化失败"}
        self.assertFalse(self._run())

        self.assertEqual(1, len(self.started))
        self.assertEqual("recover_failed", self.finished[0][0])
        self.assertIn("自动化初始化失败", self.finished[0][2])
        self.assertEqual([False], self.fake_resident.notes)
        self.assertEqual(0, web_ui._RESIDENT_RECOVERY_COUNT)   # 启动失败不算恢复成功

    def test_exception_path_finishes_with_exception(self):
        self.steps_error = RuntimeError("boom")
        self.assertFalse(self._run())

        self.assertEqual("exception", self.finished[0][0])
        self.assertFalse(self.finished[0][1])
        self.assertIn("boom", self.finished[0][2])
        self.assertEqual([False], self.fake_resident.notes)


class DispatchTests(_RecoveryCase):
    def test_dispatch_refused_when_a_restart_is_already_running(self):
        web_ui._RESTART_STATE["running"] = True
        self.assertFalse(
            web_ui._resident_recover_dispatch({"mode": "wild"}, "log_stale"))
        self.assertEqual(0, self.steps_calls)

    def test_dispatch_starts_resident_recovery_thread(self):
        recorded = []
        worker = lambda cfg, reason: recorded.append((dict(cfg), reason))
        with (
            patch.object(web_ui, "_resident_recover_worker", side_effect=worker),
            patch.object(web_ui.threading, "Thread", _SyncThread),
        ):
            self.assertTrue(
                web_ui._resident_recover_dispatch({"mode": "wild"},
                                                  "log_stale"))

        self.assertEqual([({"mode": "wild"}, "log_stale")], recorded)
        self.assertEqual("resident", web_ui._RESTART_STATE.get("source"))
        self.assertTrue(web_ui._RESTART_STATE.get("running"))

    def test_dispatch_accepts_reason_only_signature(self):
        recorded = []
        worker = lambda cfg, reason: recorded.append((dict(cfg), reason))
        with (
            patch.object(web_ui, "_resident_recover_worker", side_effect=worker),
            patch.object(web_ui.threading, "Thread", _SyncThread),
        ):
            self.assertTrue(web_ui._resident_recover_dispatch("process_gone"))

        self.assertEqual("process_gone", recorded[0][1])
        self.assertIsInstance(recorded[0][0], dict)


class _PauseableSupervisor:
    """R1 语义的最小替身：recovering 里 pause() 只登记，note_recovered() 才落地。"""

    def __init__(self, state="recovering", paused_reason=None):
        self._state = state
        self._pending = False
        self._paused_reason = paused_reason
        self.pauses = []
        self.notes = []
        self.resumes = []

    def state(self):
        return {"enabled": True, "state": self._state,
                "dry_run": False, "last_event": None,
                "paused_reason": self._paused_reason}

    def pause(self, reason="paused"):
        self.pauses.append(str(reason))
        self._paused_reason = str(reason)
        if self._state == "recovering":
            self._pending = True
        elif self._state in ("countdown", "starting", "running", "paused"):
            self._state = "paused"

    def request_pause(self, reason="paused"):
        self.pause(reason)

    def note_recovered(self, ok):
        self.notes.append(bool(ok))
        if ok:
            self._state = "paused" if self._pending else "running"
        else:
            self._state = "error"

    def resume(self):
        self.resumes.append(True)
        self._state = "countdown"
        self._paused_reason = None

    def disable(self):
        self._state = "disabled"


class _ResidentFixCase(_RecoveryCase):
    """B1/B2/B3 修复用例的公共装配。"""

    def _arm(self):
        with (
            patch.object(web_ui, "_hearthstone_window_ready", return_value=True),
            patch.object(web_ui, "_resident_reset_log_reader"),
        ):
            return web_ui._resident_arm_worker(
                {"mode": "wild", "deck_index": 2})


class CancelDuringRestartTests(_ResidentFixCase):
    def test_ctrlq_during_restart_steps_aborts_before_entering(self):
        """B1：重启步骤（1–3 分钟）期间 Ctrl+Q 的取消不能被清掉。"""
        supervisor = _PauseableSupervisor("recovering")
        web_ui.RESIDENT = supervisor

        def _steps_with_cancel():
            self.events.append("steps")
            supervisor.request_pause("user_cancel")   # 用户按了 Ctrl+Q
            self.fake_be.cancelled = True
            return True

        with patch.object(web_ui, "_restart_hearthstone_steps",
                          side_effect=_steps_with_cancel):
            self.assertFalse(self._run())

        self.assertEqual("paused", supervisor.state()["state"])
        self.assertNotIn("enter", self._plain_events())   # 不再进对战/点安全中心点
        self.assertEqual([], self.started)                # 不启动自动化
        self.assertEqual("cancelled", self.finished[0][0])


class ArmStopTests(_ResidentFixCase):
    def test_stop_during_arm_stops_clicks_and_never_starts(self):
        """B2：装配期间 stop → 点击循环停手、不启动自动化。"""
        supervisor = _PauseableSupervisor("starting")
        web_ui.RESIDENT = supervisor
        web_ui._RESIDENT_ARM_RUNNING = True
        cancel_calls = []

        def _cancel():
            cancel_calls.append(1)
            self.fake_be.cancelled = True      # api_cancel 置的急停开关生效

        self.fake_be.request_cancel = _cancel
        self.fake_be.on_enter = lambda: web_ui.api_cancel({})
        self.fake_be.enter_result = {"ok": False, "stage": "aborted",
                                     "message": "cancelled"}

        self.assertFalse(self._arm())

        self.assertEqual([1], cancel_calls)
        self.assertEqual([], self.started)
        self.assertEqual(0, self.fake_be.deck_calls)
        self.assertEqual("paused", supervisor.state()["state"])


class ArmFailureTests(_ResidentFixCase):
    def _assert_no_start_and_paused(self, supervisor):
        self.assertEqual("paused", supervisor.state()["state"])
        self.assertEqual([], self.started)
        self.assertEqual(0, self.fake_be.deck_calls)

    def test_entering_failure_pauses_and_never_starts(self):
        supervisor = _PauseableSupervisor("starting")
        web_ui.RESIDENT = supervisor
        self.fake_be.enter_result = {"ok": False, "stage": "timeout_main_menu",
                                     "message": "没认出主界面"}

        self.assertFalse(self._arm())

        self._assert_no_start_and_paused(supervisor)
        self.assertTrue(self.alerts)

    def test_mode_failure_pauses_and_never_starts(self):
        supervisor = _PauseableSupervisor("starting")
        web_ui.RESIDENT = supervisor
        with patch.object(web_ui, "_resident_select_mode_with_retry",
                          return_value={"ok": False, "error": "菜单没弹出"}):
            self.assertFalse(self._arm())

        self._assert_no_start_and_paused(supervisor)
        self.assertTrue(self.alerts)

    def test_deck_failure_pauses_and_never_starts(self):
        supervisor = _PauseableSupervisor("starting")
        web_ui.RESIDENT = supervisor
        with (
            patch.object(web_ui, "_resident_select_mode_with_retry",
                         return_value={"ok": True}),
            patch.object(web_ui, "_resident_select_deck_with_retry",
                         return_value={"ok": False, "error": "不像卡组"}),
        ):
            self.assertFalse(self._arm())

        self._assert_no_start_and_paused(supervisor)
        self.assertTrue(self.alerts)

    def test_exception_pauses_and_never_starts(self):
        supervisor = _PauseableSupervisor("starting")
        web_ui.RESIDENT = supervisor
        with patch.object(web_ui, "_hearthstone_window_ready",
                          side_effect=RuntimeError("boom")):
            self.assertFalse(web_ui._resident_arm_worker(
                {"mode": "wild", "deck_index": 2}))

        self._assert_no_start_and_paused(supervisor)

    def test_success_path_still_starts_automation(self):
        supervisor = _PauseableSupervisor("starting")
        web_ui.RESIDENT = supervisor
        self.log_default = True

        self.assertTrue(self._arm())

        self.assertEqual(1, len(self.started))
        self.assertNotEqual("paused", supervisor.state()["state"])


class StartAutomationTests(_RecoveryCase):
    def test_dead_automation_in_game_uses_api_start(self):
        """B3：日志确认在局内 → 允许 api_start 续跑。"""
        web_ui._RESIDENT_ARM_PENDING = False
        start = patch.object(web_ui, "api_start", side_effect=self._api_start)
        with patch.object(web_ui, "_resident_log_in_game", return_value=True):
            with start as start_mock:
                self.assertTrue(web_ui._resident_start_automation())

        self.assertEqual([{"keep_score": True, "keep_schedule": True}], self.started)
        start_mock.assert_called_once()

    def test_dead_automation_not_in_game_arms_instead_of_blind_start(self):
        """B3：日志不在局内 → 走装配（进对战→模式→卡组），绝不盲 api_start。"""
        web_ui._RESIDENT_ARM_PENDING = False
        dispatched = []
        with (
            patch.object(web_ui, "_resident_log_in_game", return_value=False),
            patch.object(web_ui, "_resident_arm_dispatch",
                         side_effect=lambda: dispatched.append(True) or True),
            patch.object(web_ui, "api_start", side_effect=self._api_start),
        ):
            self.assertTrue(web_ui._resident_start_automation())

        self.assertEqual([True], dispatched)
        self.assertEqual([], self.started)


class ResumeAfterStopTests(_RecoveryCase):
    def test_enabling_after_stop_clears_stopped_by_and_arms(self):
        """B4/N11：会话里 stop 过之后开关常驻，必须清 stopped_by 并进入倒计时/装配。"""
        supervisor = _PauseableSupervisor("paused")
        web_ui.RESIDENT = supervisor
        web_ui.CTRL.stopped_by = "user_immediate"
        saved = {}
        pending_seen = []

        def _save(cfg):
            saved.update(cfg)
            pending_seen.append(web_ui._RESIDENT_ARM_PENDING)

        web_ui._RESIDENT_ARM_PENDING = False
        with (
            patch.object(web_ui, "load_config", side_effect=lambda: {}),
            patch.object(web_ui, "save_config", side_effect=_save),
        ):
            result = web_ui.api_resident({"enabled": True})

        self.assertTrue(result["ok"])
        self.assertTrue(saved["resident"]["enabled"])
        self.assertIsNone(web_ui.CTRL.stopped_by)
        self.assertEqual([True], pending_seen)     # 先登记待办再落盘（N11）
        self.assertEqual([True], supervisor.resumes)
        self.assertEqual("countdown", supervisor.state()["state"])
        self.assertTrue(web_ui._RESIDENT_ARM_PENDING)

    def test_plain_settings_save_keeps_paused_and_does_not_resume(self):
        """blocking：浮窗「保存常驻设置」/hsctl set（不带 enabled）不得恢复常驻。"""
        supervisor = _PauseableSupervisor("paused")
        web_ui.RESIDENT = supervisor
        web_ui.CTRL.stopped_by = "user_immediate"
        saved = {}
        web_ui._RESIDENT_ARM_PENDING = False
        with (
            patch.object(web_ui, "load_config",
                         side_effect=lambda: {"resident": {"enabled": True}}),
            patch.object(web_ui, "save_config",
                         side_effect=lambda cfg: saved.update(cfg)),
        ):
            result = web_ui.api_resident(
                {"mode": "wild", "stale_in_game_seconds": 200})

        self.assertTrue(result["ok"])
        self.assertEqual([], supervisor.resumes)              # 没 resume
        self.assertEqual("user_immediate", web_ui.CTRL.stopped_by)  # 停止标记保留
        self.assertEqual("paused", supervisor.state()["state"])
        self.assertIn("仍暂停", result["message"])
        self.assertEqual("wild", saved["resident"]["mode"])
        self.assertEqual(200.0, saved["resident"]["stale_in_game_seconds"])
        self.assertFalse(web_ui._RESIDENT_ARM_PENDING)        # 纯保存不 arm

    def test_explicit_enable_resumes_and_clears_stopped_by(self):
        """显式 enabled=True（浮窗一键恢复/CLI on）才 resume 并清停止标记。"""
        supervisor = _PauseableSupervisor("paused")
        web_ui.RESIDENT = supervisor
        web_ui.CTRL.stopped_by = "user_immediate"
        with (
            patch.object(web_ui, "load_config",
                         side_effect=lambda: {"resident": {"enabled": True}}),
            patch.object(web_ui, "save_config"),
        ):
            result = web_ui.api_resident({"enabled": True})

        self.assertTrue(result["ok"])
        self.assertEqual([True], supervisor.resumes)
        self.assertIsNone(web_ui.CTRL.stopped_by)
        self.assertEqual("countdown", supervisor.state()["state"])
        self.assertIn("已恢复", result["message"])


class RestartWorkerResidentRecoveryTests(_RecoveryCase):
    """手动重启炉石：常驻管事 → 到主界面后自动恢复；不管事/失败 → 保持现状。"""

    def _manual_restart(self):
        resume = patch.object(web_ui, "_resident_resume_after_restart")
        with (
            patch.object(web_ui, "_restart_wait_main_menu", return_value=True),
            resume as resume_mock,
        ):
            ok = web_ui._restart_hearthstone_worker()
        return ok, resume_mock

    def test_manual_restart_resumes_resident_when_it_was_acting(self):
        with patch.object(web_ui, "_resident_will_act", return_value=True):
            ok, resume = self._manual_restart()

        self.assertTrue(ok)
        resume.assert_called_once_with()

    def test_manual_restart_does_not_resume_when_resident_not_acting(self):
        with patch.object(web_ui, "_resident_will_act", return_value=False):
            ok, resume = self._manual_restart()

        self.assertTrue(ok)
        resume.assert_not_called()

    def test_manual_restart_failure_still_restores_resident(self):
        """should-fix4：重启失败也要恢复常驻，让它按配额自愈，不能留在暂停态。"""
        with (
            patch.object(web_ui, "_resident_will_act", return_value=True),
            patch.object(web_ui, "_restart_wait_main_menu", return_value=False),
            patch.object(web_ui, "_resident_resume_after_restart") as resume,
        ):
            self.assertFalse(web_ui._restart_hearthstone_worker())

        resume.assert_called_once_with()

    def test_restart_steps_failure_still_restores_resident(self):
        self.steps_result = False
        with (
            patch.object(web_ui, "_resident_will_act", return_value=True),
            patch.object(web_ui, "_resident_resume_after_restart") as resume,
        ):
            self.assertFalse(web_ui._restart_hearthstone_worker())

        resume.assert_called_once_with()

    def test_was_resident_captured_before_restart_steps(self):
        """捕获时机必须在停自动化之前：重启步骤会改 stopped_by 让 will_act 变 False。"""
        def _will_act():
            self.events.append("will_act")
            return True

        with (
            patch.object(web_ui, "_resident_will_act", side_effect=_will_act),
            patch.object(web_ui, "_restart_wait_main_menu", return_value=True),
            patch.object(web_ui, "_resident_resume_after_restart"),
        ):
            self.assertTrue(web_ui._restart_hearthstone_worker())

        self.assertLess(self.events.index("will_act"), self.events.index("steps"))

    def test_manual_restart_cancelled_by_ctrlq_keeps_resident_paused(self):
        """blocking：重启期间用户 Ctrl+Q/停止 → finally 不得恢复常驻、不得清停止标记。"""
        supervisor = _PauseableSupervisor("paused", paused_reason="手动重启")
        web_ui.RESIDENT = supervisor
        web_ui.CTRL.stopped_by = None
        web_ui._RESIDENT_ARM_PENDING = False
        logs = []

        def _wait_menu():
            # 等待主界面期间用户按 Ctrl+Q / 点停止
            self.fake_be.cancelled = True
            web_ui.CTRL.stopped_by = "user_cancel"
            supervisor.pause("user_cancel")
            return False

        with (
            patch.object(web_ui, "_resident_will_act", return_value=True),
            patch.object(web_ui, "_restart_wait_main_menu",
                         side_effect=_wait_menu),
            patch.object(web_ui, "_log",
                         side_effect=lambda level, msg: logs.append((level, msg))),
            patch.object(web_ui, "_resident_arm_dispatch") as dispatch,
        ):
            self.assertFalse(web_ui._restart_hearthstone_worker())

        state = supervisor.state()
        self.assertEqual("paused", state["state"])
        self.assertEqual("user_cancel", state["paused_reason"])
        self.assertFalse(web_ui._RESIDENT_ARM_PENDING)   # 没有 arm_pending
        self.assertEqual([], supervisor.resumes)         # 没有 resume
        self.assertEqual([], self.started)               # api_start 未被调用
        dispatch.assert_not_called()                     # 不会自己去装配
        self.assertEqual("user_cancel", web_ui.CTRL.stopped_by)  # 停止标记保留
        self.assertNotIn("clear_cancel", self.events)    # 取消信号没被清掉
        self.assertTrue(any(level == "WARN" and "常驻保持暂停" in msg
                            for level, msg in logs))


class ManualRestartIsolationTests(_RecoveryCase):
    """should-fix4：手动重启期间暂停监督器，避免它派发启动/白烧配额。"""

    def _real_supervisor(self):
        from src.safety.resident_supervisor import ResidentSupervisor

        self.starts = []
        settings = {
            "enabled": True, "mode": "standard", "deck_index": None,
            "stale_in_game_seconds": 180.0, "stale_other_seconds": 300.0,
            "max_restarts": 3, "window_seconds": 600.0,
            "check_seconds": 0.0, "settle_seconds": 0.0,
            "start_delay_seconds": 0.0, "dry_run": False,
        }
        return ResidentSupervisor(
            settings=lambda: dict(settings),
            log_age=lambda: None,
            in_game=lambda: False,
            process_running=lambda: True,
            automation_running=(
                lambda: web_ui.CTRL.automation_thread is not None),
            stopped_by=lambda: web_ui.CTRL.stopped_by,
            start_automation=lambda: self.starts.append(1) or True,
            recover=lambda *args, **kwargs: False,
            alert=lambda message: None,
        )

    def test_supervisor_pauses_for_manual_restart_and_poll_dispatches_nothing(self):
        supervisor = self._real_supervisor()
        web_ui.RESIDENT = supervisor
        seen = {}

        def _steps():
            seen["state"] = supervisor.state()["state"]
            seen["event"] = supervisor.poll_once()
            seen["starts"] = list(self.starts)
            return True

        with (
            patch.object(web_ui, "_resident_will_act", return_value=True),
            patch.object(web_ui, "_restart_hearthstone_steps", side_effect=_steps),
            patch.object(web_ui, "_restart_wait_main_menu", return_value=True),
        ):
            self.assertTrue(web_ui._restart_hearthstone_worker())

        self.assertEqual("paused", seen["state"])     # 重启步骤期间监督器已停手
        self.assertIsNone(seen["event"])              # paused 不判定、不派发
        self.assertEqual([], seen["starts"])          # 没有 automation_started
        self.assertEqual(0, supervisor.state()["restarts_in_window"])  # 不占 quota
        # 收尾必须恢复常驻（arm 待办 + resume），让它自己重新拉起自动化。
        self.assertTrue(web_ui._RESIDENT_ARM_PENDING)
        self.assertIn(supervisor.state()["state"],
                      ("countdown", "starting", "running"))

    def test_non_acting_resident_is_not_paused_by_manual_restart(self):
        supervisor = _PauseableSupervisor("running")
        web_ui.RESIDENT = supervisor
        with (
            patch.object(web_ui, "_resident_will_act", return_value=False),
            patch.object(web_ui, "_restart_wait_main_menu", return_value=True),
            patch.object(web_ui, "_resident_resume_after_restart") as resume,
        ):
            self.assertTrue(web_ui._restart_hearthstone_worker())

        self.assertEqual("running", supervisor.state()["state"])
        self.assertEqual([], supervisor.pauses)
        resume.assert_not_called()


class OverlayRestartIsolationTests(_RecoveryCase):
    """should-fix4：浮窗「重启炉石」也走 _restart_begin + 串行锁。"""

    def test_overlay_restart_refuses_when_another_restart_is_running(self):
        web_ui._RESTART_STATE["running"] = True
        with patch.object(web_ui.threading, "Thread") as thread_cls:
            self.assertFalse(web_ui._overlay_restart_hearthstone())
        thread_cls.assert_not_called()
        self.assertTrue(web_ui._RESTART_STATE["running"])

    def test_overlay_restart_begins_a_manual_restart(self):
        web_ui._RESTART_STATE["running"] = False
        with patch.object(web_ui.threading, "Thread") as thread_cls:
            self.assertTrue(web_ui._overlay_restart_hearthstone())

        thread_cls.assert_called_once()
        kwargs = thread_cls.call_args.kwargs
        self.assertIs(web_ui._restart_hearthstone_worker, kwargs["target"])
        self.assertTrue(web_ui._RESTART_STATE["running"])
        self.assertEqual("manual", web_ui._RESTART_STATE["source"])


class RestartStepsKeepScheduleTests(unittest.TestCase):
    """blocking3：三种重启共用的内部 stop 必须带 keep_schedule=True。"""

    def setUp(self):
        self._saved_thread = web_ui.CTRL.automation_thread
        self._saved_fsm = web_ui.CTRL.fsm
        web_ui.CTRL.automation_thread = None
        web_ui.CTRL.fsm = None

    def tearDown(self):
        web_ui.CTRL.automation_thread = self._saved_thread
        web_ui.CTRL.fsm = self._saved_fsm

    def test_internal_stop_passes_keep_schedule(self):
        web_ui.CTRL.automation_thread = object()
        captured = []

        def _stop(body):
            captured.append(dict(body))
            web_ui.CTRL.automation_thread = None
            return {"ok": True}

        hs = SimpleNamespace(
            resolve_paths=lambda root: {
                "ok": True, "log_root": "C:/Logs",
                "exe": "C:/Hearthstone.exe", "battlenet_exe": None},
            kill_hearthstone=lambda: {
                "ok": True, "killed": True, "running": False, "message": "ok"},
            clear_logs=lambda root, logger=None: {
                "ok": True, "removed": 0, "skipped": [], "errors": []},
            launch_hearthstone=lambda exe, **kwargs: {
                "ok": True, "message": "launched"},
        )
        with (
            patch.dict(sys.modules, {"hs_restart": hs}),
            patch.object(web_ui, "load_config",
                         return_value={"log_root": "C:/Logs"}),
            patch.object(web_ui, "api_stop", side_effect=_stop),
            patch.object(web_ui, "_focus_hearthstone_if_present",
                         return_value=False),
            patch.object(web_ui, "_log"),
            patch.object(web_ui.CTRL, "fsm",
                         SimpleNamespace(init=lambda: None)),
        ):
            self.assertTrue(web_ui._restart_hearthstone_steps())

        self.assertEqual(1, len(captured))
        self.assertTrue(captured[0].get("_resident_internal"))
        self.assertTrue(captured[0].get("keep_schedule"))


class ResumeAfterRestartTests(_RecoveryCase):
    def _resume_after_restart(self, cancelled=False):
        calls = {"clear_cancel": 0}
        fake_be = SimpleNamespace(
            is_cancelled=lambda: bool(cancelled),
            clear_cancel=lambda: calls.__setitem__(
                "clear_cancel", calls["clear_cancel"] + 1),
        )
        logs = []
        with (
            patch.object(web_ui, "_resident_enabled", return_value=True),
            patch.dict(sys.modules, {"battle_entry": fake_be}),
            patch.object(web_ui, "_log",
                         side_effect=lambda level, msg: logs.append((level, msg))),
        ):
            result = web_ui._resident_resume_after_restart()
        return result, calls["clear_cancel"], logs

    def test_resume_after_restart_clears_stop_arms_and_resumes(self):
        supervisor = _PauseableSupervisor("paused", paused_reason="手动重启")
        web_ui.RESIDENT = supervisor
        web_ui.CTRL.stopped_by = "schedule"    # 非用户来源的过期标记照旧清掉
        web_ui._RESIDENT_ARM_PENDING = False

        ok, cleared, _logs = self._resume_after_restart()

        self.assertTrue(ok)
        self.assertEqual(1, cleared)           # arm_pending 之前先清残留取消
        self.assertIsNone(web_ui.CTRL.stopped_by)
        self.assertTrue(web_ui._RESIDENT_ARM_PENDING)
        self.assertEqual([True], supervisor.resumes)

    def test_resume_after_restart_keeps_paused_when_cancel_flag_set(self):
        supervisor = _PauseableSupervisor("paused", paused_reason="手动重启")
        web_ui.RESIDENT = supervisor
        web_ui.CTRL.stopped_by = "user_cancel"
        web_ui._RESIDENT_ARM_PENDING = False

        ok, cleared, logs = self._resume_after_restart(cancelled=True)

        self.assertFalse(ok)
        self.assertEqual(0, cleared)           # 没有去清用户的取消信号
        self.assertEqual("user_cancel", web_ui.CTRL.stopped_by)
        self.assertFalse(web_ui._RESIDENT_ARM_PENDING)
        self.assertEqual([], supervisor.resumes)
        self.assertTrue(any(level == "WARN" and "常驻保持暂停" in msg
                            for level, msg in logs))

    def test_resume_after_restart_keeps_paused_for_user_stop_reasons(self):
        for reason in ("user_cancel", "user_stop", "user_immediate",
                       "user_after_game", "rank_reached"):
            with self.subTest(reason=reason):
                supervisor = _PauseableSupervisor(
                    "paused", paused_reason=reason)
                web_ui.RESIDENT = supervisor
                web_ui.CTRL.stopped_by = reason
                web_ui._RESIDENT_ARM_PENDING = False

                ok, cleared, _logs = self._resume_after_restart()

                self.assertFalse(ok)
                self.assertEqual(0, cleared)
                self.assertEqual(reason, web_ui.CTRL.stopped_by)
                self.assertFalse(web_ui._RESIDENT_ARM_PENDING)
                self.assertEqual([], supervisor.resumes)

    def test_resume_after_restart_does_nothing_when_resident_disabled(self):
        supervisor = _PauseableSupervisor("running")
        web_ui.RESIDENT = supervisor
        web_ui.CTRL.stopped_by = "user_immediate"
        web_ui._RESIDENT_ARM_PENDING = False
        with patch.object(web_ui, "_resident_enabled", return_value=False):
            self.assertFalse(web_ui._resident_resume_after_restart())

        self.assertEqual("user_immediate", web_ui.CTRL.stopped_by)
        self.assertFalse(web_ui._RESIDENT_ARM_PENDING)
        self.assertEqual([], supervisor.resumes)


if __name__ == "__main__":
    unittest.main()
