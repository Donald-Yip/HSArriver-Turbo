"""常驻模式的 Web 装配层：配置 API / status 字段 / 手动操作守卫 / 浮窗回调 / 看门狗让位。

判定逻辑在 src/safety/resident_supervisor.py（R1 单测覆盖）；这里只验证 web_ui 把
“配置 + 状态 + 手动动作 + 浮窗”接对了，不重复测阈值判定。
"""

import io
import sys
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import web_ui


class _FakeResident:
    """最小监督器替身：只记录装配层真正会调用的方法。"""

    def __init__(self, state=None):
        self._state = dict(state or {"enabled": False, "state": "disabled"})
        self.calls = []

    def state(self):
        return dict(self._state)

    def start(self):
        self.calls.append(("start",))

    def resume(self):
        self.calls.append(("resume",))

    def disable(self):
        self.calls.append(("disable",))

    def request_pause(self, reason):
        self.calls.append(("request_pause", reason))

    def pause(self, reason="paused"):
        self.calls.append(("pause", reason))

    def note_recovered(self, ok):
        self.calls.append(("note_recovered", ok))


class _WebStateCase(unittest.TestCase):
    """保存/恢复 web_ui 的全局运行态，测试之间互不污染。"""

    def setUp(self):
        self._saved = {
            "thread": web_ui.CTRL.automation_thread,
            "starting": web_ui.CTRL.starting,
            "stopped_by": web_ui.CTRL.stopped_by,
            "fsm": web_ui.CTRL.fsm,
            "resident": web_ui.RESIDENT,
            "alert": dict(web_ui._RESIDENT_ALERT),
            "arm_pending": web_ui._RESIDENT_ARM_PENDING,
            "arm_running": web_ui._RESIDENT_ARM_RUNNING,
        }
        web_ui.CTRL.automation_thread = None
        web_ui.CTRL.starting = False
        web_ui.CTRL.stopped_by = None
        web_ui._RESIDENT_ALERT.update({"text": None, "at": None})
        web_ui._RESIDENT_ARM_PENDING = False
        web_ui._RESIDENT_ARM_RUNNING = False

    def tearDown(self):
        web_ui.CTRL.automation_thread = self._saved["thread"]
        web_ui.CTRL.starting = self._saved["starting"]
        web_ui.CTRL.stopped_by = self._saved["stopped_by"]
        web_ui.CTRL.fsm = self._saved["fsm"]
        web_ui.RESIDENT = self._saved["resident"]
        web_ui._RESIDENT_ALERT.update(self._saved["alert"])
        web_ui._RESIDENT_ARM_PENDING = self._saved["arm_pending"]
        web_ui._RESIDENT_ARM_RUNNING = self._saved["arm_running"]


class ApiResidentTests(_WebStateCase):
    @contextmanager
    def _save_env(self, section, saved):
        with (
            patch.object(web_ui, "load_config",
                         side_effect=lambda: {"resident": dict(section)}),
            patch.object(web_ui, "save_config",
                         side_effect=lambda cfg: saved.update(cfg)),
            patch.object(web_ui, "_log"),
        ):
            yield

    def test_save_normalizes_and_persists(self):
        fake = _FakeResident()
        saved = {}
        with patch.object(web_ui, "RESIDENT", fake), self._save_env({}, saved):
            result = web_ui.api_resident({
                "enabled": True,
                "mode": "狂野",
                "deck_index": 3,
                "stale_in_game_seconds": 90,
                "stale_other_seconds": 400,
                "max_restarts": 5,
                "dry_run": True,
            })

        self.assertTrue(result["ok"])
        section = saved["resident"]
        self.assertTrue(section["enabled"])
        self.assertEqual("wild", section["mode"])          # 中文别名已归一化
        self.assertEqual(3, section["deck_index"])
        self.assertEqual(90.0, section["stale_in_game_seconds"])
        self.assertEqual(400.0, section["stale_other_seconds"])
        self.assertEqual(5, section["max_restarts"])
        self.assertTrue(section["dry_run"])
        self.assertIn(("resume",), fake.calls)              # 开启 → 让监督器起倒计时
        self.assertTrue(web_ui._RESIDENT_ARM_PENDING)       # 倒计时结束后走完整装配

    def test_save_persists_warn_seconds_and_zero_is_valid(self):
        """提前告警阈值：0=关是合法值，必须原样保存/回读。"""
        fake = _FakeResident()
        saved = {}
        with patch.object(web_ui, "RESIDENT", fake), self._save_env({}, saved):
            result = web_ui.api_resident({"warn_seconds": 0})

        self.assertTrue(result["ok"])
        self.assertEqual(0.0, saved["resident"]["warn_seconds"])
        self.assertEqual(0.0, result["settings"]["warn_seconds"])

        saved.clear()
        with patch.object(web_ui, "RESIDENT", fake), self._save_env({}, saved):
            web_ui.api_resident({"warn_seconds": 60})
        self.assertEqual(60.0, saved["resident"]["warn_seconds"])

    def test_warn_seconds_is_clamped_to_stale_in_game(self):
        fake = _FakeResident()
        saved = {}
        with patch.object(web_ui, "RESIDENT", fake), self._save_env({}, saved):
            result = web_ui.api_resident(
                {"warn_seconds": 9999, "stale_in_game_seconds": 180})

        self.assertTrue(result["ok"])
        self.assertEqual(180.0, saved["resident"]["warn_seconds"])

    def test_enable_refused_while_automation_running(self):
        fake = _FakeResident()
        saved = {}
        web_ui.CTRL.automation_thread = object()
        with patch.object(web_ui, "RESIDENT", fake), self._save_env({}, saved):
            result = web_ui.api_resident({"enabled": True})

        self.assertFalse(result["ok"])
        self.assertIn("先停止自动化", result["error"])
        self.assertEqual({}, saved)
        self.assertNotIn(("resume",), fake.calls)

    def test_disable_turns_the_supervisor_off(self):
        fake = _FakeResident({"enabled": True, "state": "running"})
        saved = {}
        with (patch.object(web_ui, "RESIDENT", fake),
              self._save_env({"enabled": True}, saved)):
            result = web_ui.api_resident({"enabled": False})

        self.assertTrue(result["ok"])
        self.assertFalse(saved["resident"]["enabled"])
        self.assertIn(("disable",), fake.calls)
        self.assertNotIn(("resume",), fake.calls)

    def test_out_of_range_values_are_clamped_not_crashing(self):
        fake = _FakeResident()
        saved = {}
        with patch.object(web_ui, "RESIDENT", fake), self._save_env({}, saved):
            result = web_ui.api_resident({
                "stale_in_game_seconds": 5,
                "max_restarts": 999,
                "start_delay_seconds": 600,
            })

        self.assertTrue(result["ok"])
        section = saved["resident"]
        self.assertEqual(60.0, section["stale_in_game_seconds"])   # 下限 60s
        self.assertEqual(20, section["max_restarts"])             # 上限 20
        self.assertEqual(60.0, section["start_delay_seconds"])    # 上限 60s

    def test_bad_types_are_refused_without_writing(self):
        fake = _FakeResident()
        saved = {}
        with patch.object(web_ui, "RESIDENT", fake), self._save_env({}, saved):
            bad_deck = web_ui.api_resident({"deck_index": "abc"})
            bad_number = web_ui.api_resident({"stale_other_seconds": "abc"})

        self.assertFalse(bad_deck["ok"])
        self.assertFalse(bad_number["ok"])
        self.assertEqual({}, saved)

    def test_empty_body_only_reads(self):
        fake = _FakeResident({"enabled": True, "state": "running", "mode": "wild"})
        saved = {}
        with (patch.object(web_ui, "RESIDENT", fake),
              self._save_env({"enabled": True, "mode": "wild"}, saved)):
            result = web_ui.api_resident(None)

        self.assertTrue(result["ok"])
        self.assertEqual("running", result["resident"]["state"])
        self.assertEqual("wild", result["settings"]["mode"])
        self.assertEqual({}, saved)

    def test_status_snapshot_exposes_resident_and_recovery_progress(self):
        fake = _FakeResident({"enabled": True, "state": "recovering",
                              "mode": "standard"})
        snapshot_patch = patch.object(
            web_ui, "_restart_snapshot",
            return_value={"running": True, "stage": "restarting",
                          "source": "resident", "ok": None, "message": None})
        with (patch.object(web_ui, "RESIDENT", fake), snapshot_patch):
            snapshot = web_ui.status_snapshot()

        resident = snapshot["resident"]
        self.assertEqual("recovering", resident["state"])
        self.assertTrue(resident["recover_running"])
        self.assertEqual("restarting", resident["recover_stage"])
        self.assertEqual("resident", resident["recover_source"])

    def test_state_exposes_early_warning_fields(self):
        """提前告警（warn_seconds）：state 暴露 warning/warning_at 给网页/浮窗/CLI。"""
        fake = _FakeResident({"enabled": True, "state": "running"})
        with (
            patch.object(web_ui, "RESIDENT", fake),
            patch.object(web_ui, "_restart_snapshot", return_value={}),
            patch.dict(web_ui._RESIDENT_WARN,
                       {"text": "对局中日志已停滞 130s", "at": "2026-11-01T12:00:00"}),
        ):
            state = web_ui._resident_state()

        self.assertEqual("对局中日志已停滞 130s", state["warning"])
        self.assertEqual("2026-11-01T12:00:00", state["warning_at"])

    def test_state_hides_old_warning_after_reset(self):
        """恢复后（warned=False）不再把旧 ⚠️ 挂在网页/CLI 上。"""
        fake = _FakeResident({"enabled": True, "state": "running",
                              "warned": False})
        with (
            patch.object(web_ui, "RESIDENT", fake),
            patch.object(web_ui, "_restart_snapshot", return_value={}),
            patch.dict(web_ui._RESIDENT_WARN,
                       {"text": "对局中日志已停滞 130s", "at": "2026-11-01T12:00:00"}),
        ):
            state = web_ui._resident_state()

        self.assertIsNone(state["warning"])
        self.assertIsNone(state["warning_at"])

    def test_state_is_safe_without_fsm(self):
        web_ui.CTRL.fsm = None
        with patch.object(web_ui, "RESIDENT", _FakeResident()):
            self.assertFalse(web_ui._resident_in_game())
            with patch.object(web_ui, "_resident_fsm", return_value=None):
                self.assertFalse(web_ui._resident_log_in_game())
            self.assertIsInstance(web_ui._overlay_resident(), dict)

    def test_in_game_reads_fsm_state(self):
        web_ui.CTRL.fsm = SimpleNamespace(FSM_state="Battling")
        self.assertTrue(web_ui._resident_in_game())
        web_ui.CTRL.fsm = SimpleNamespace(FSM_state="Main Menu")
        self.assertFalse(web_ui._resident_in_game())
        web_ui.CTRL.fsm = None
        self.assertFalse(web_ui._resident_in_game())


class ManualActionTests(_WebStateCase):
    def test_manual_actions_refused_while_resident_countdown(self):
        fake = _FakeResident({"enabled": True, "state": "countdown"})
        with patch.object(web_ui, "RESIDENT", fake):
            mode = web_ui.api_mode_click({"mode": "wild"})
            deck = web_ui.api_deck_click({"index": 1})
            enter = web_ui.api_enter_battle({"deck_index": 1})

        for result in (mode, deck, enter):
            self.assertFalse(result["ok"])
            self.assertIn("倒计时", result["error"])

    def test_manual_actions_refused_while_recovering(self):
        fake = _FakeResident({"enabled": True, "state": "recovering"})
        with patch.object(web_ui, "RESIDENT", fake):
            mode = web_ui.api_mode_click({"mode": "standard"})
        self.assertFalse(mode["ok"])
        self.assertIn("自动恢复", mode["error"])

    def test_manual_actions_allowed_while_resident_running(self):
        fake = _FakeResident({"enabled": True, "state": "running"})
        with (
            patch.object(web_ui, "RESIDENT", fake),
            patch.object(web_ui, "_mode_click_inner",
                         return_value={"ok": True, "message": "clicked"}),
            patch.object(web_ui.threading,
                         "Thread", side_effect=AssertionError("不应真的起线程")),
        ):
            result = web_ui.api_mode_click({"mode": "wild"})
        self.assertTrue(result["ok"])

    def test_stop_requests_resident_pause(self):
        fake = _FakeResident()
        with patch.object(web_ui, "RESIDENT", fake), patch.object(web_ui, "_log"):
            web_ui.api_stop({"mode": "now"})
        self.assertIn(("request_pause", "user_stop"), fake.calls)

    def test_internal_stop_does_not_pause_resident(self):
        """重启步骤内部的 api_stop 只是停自动化，不是用户喊停。"""
        fake = _FakeResident()
        with patch.object(web_ui, "RESIDENT", fake), patch.object(web_ui, "_log"):
            web_ui.api_stop({"mode": "now", "_resident_internal": True})
        self.assertNotIn(("request_pause", "user_stop"), fake.calls)

    def test_cancel_requests_resident_pause(self):
        fake = _FakeResident()
        battle_entry = SimpleNamespace(request_cancel=lambda: None)
        with (
            patch.object(web_ui, "RESIDENT", fake),
            patch.object(web_ui, "_log"),
            patch.dict(sys.modules, {"battle_entry": battle_entry}),
        ):
            web_ui.api_cancel({})
        self.assertIn(("request_pause", "user_cancel"), fake.calls)

    def test_cancel_reason_prefers_stopped_by(self):
        fake = _FakeResident()
        web_ui.CTRL.stopped_by = "user_immediate"
        battle_entry = SimpleNamespace(request_cancel=lambda: None)
        with (
            patch.object(web_ui, "RESIDENT", fake),
            patch.object(web_ui, "_log"),
            patch.dict(sys.modules, {"battle_entry": battle_entry}),
        ):
            web_ui.api_cancel({})
        self.assertIn(("request_pause", "user_immediate"), fake.calls)


class ArmAndSignalsTests(_WebStateCase):
    def test_enable_arms_after_countdown_then_start_automation(self):
        fake = _FakeResident()
        saved = {}
        with (
            patch.object(web_ui, "RESIDENT", fake),
            patch.object(web_ui, "load_config", side_effect=lambda: {}),
            patch.object(web_ui, "save_config",
                         side_effect=lambda cfg: saved.update(cfg)),
            patch.object(web_ui, "_log"),
        ):
            web_ui.api_resident({"enabled": True})
        self.assertTrue(web_ui._RESIDENT_ARM_PENDING)

        with patch.object(web_ui, "_resident_arm_dispatch",
                          return_value=True) as dispatch:
            self.assertTrue(web_ui._resident_start_automation())
        dispatch.assert_called_once()
        self.assertFalse(web_ui._RESIDENT_ARM_PENDING)

    def test_start_automation_without_arm_arms_when_not_in_game(self):
        web_ui._RESIDENT_ARM_PENDING = False
        with (
            patch.object(web_ui, "_resident_log_in_game", return_value=False),
            patch.object(web_ui, "_resident_arm_dispatch",
                         return_value=True) as dispatch,
            patch.object(web_ui, "api_start",
                         return_value={"ok": True}) as start,
        ):
            self.assertTrue(web_ui._resident_start_automation())
        dispatch.assert_called_once()
        start.assert_not_called()      # 不在局内绝不盲 api_start

    def test_arm_running_is_reported_as_automation_and_hides_log_age(self):
        web_ui._RESIDENT_ARM_RUNNING = True
        self.assertTrue(web_ui._resident_automation_running())
        self.assertTrue(web_ui._resident_process_running())
        with patch.object(web_ui, "_resident_monitor",
                          side_effect=AssertionError("装配期不应采样日志")):
            self.assertIsNone(web_ui._resident_log_age())

    def test_arm_worker_skips_entering_when_log_already_in_game(self):
        fake_be = SimpleNamespace(
            events=[],
            clear_cancel=lambda: fake_be.events.append("clear_cancel"),
            enter_battle=lambda **kw: fake_be.events.append("enter"),
            select_deck=lambda index, logger=None: {"ok": True},
            is_cancelled=lambda: False,
        )
        started = []
        with (
            patch.dict(sys.modules, {"battle_entry": fake_be}),
            patch.object(web_ui, "RESIDENT",
                         _FakeResident({"enabled": True, "state": "starting"})),
            patch.object(web_ui, "_hearthstone_window_ready", return_value=True),
            patch.object(web_ui, "_resident_fsm", return_value=None),
            patch.object(web_ui, "_resident_reset_log_reader"),
            patch.object(web_ui, "_resident_log_in_game", return_value=True),
            patch.object(web_ui, "_resident_enabled", return_value=True),
            patch.object(web_ui, "api_start",
                         side_effect=lambda body=None: started.append(body)
                         or {"ok": True}),
            patch.object(web_ui, "_log"),
        ):
            self.assertTrue(web_ui._resident_arm_worker(
                {"mode": "wild", "deck_index": None}))

        self.assertEqual(1, len(started))
        self.assertNotIn("enter", fake_be.events)
        self.assertFalse(web_ui._RESIDENT_ARM_RUNNING)

    def test_arm_worker_alert_and_no_start_when_entering_fails(self):
        fake_be = SimpleNamespace(
            events=[],
            clear_cancel=lambda: fake_be.events.append("clear_cancel"),
            enter_battle=lambda **kw: {"ok": False, "message": "没到主界面"},
            select_deck=lambda index, logger=None: {"ok": True},
            is_cancelled=lambda: False,
        )
        alerts, started = [], []
        with (
            patch.dict(sys.modules, {"battle_entry": fake_be}),
            patch.object(web_ui, "RESIDENT",
                         _FakeResident({"enabled": True, "state": "starting"})),
            patch.object(web_ui, "_hearthstone_window_ready", return_value=True),
            patch.object(web_ui, "_resident_fsm", return_value=None),
            patch.object(web_ui, "_resident_reset_log_reader"),
            patch.object(web_ui, "_resident_log_in_game", return_value=False),
            patch.object(web_ui, "_resident_enabled", return_value=True),
            patch.object(web_ui, "_resident_alert",
                         side_effect=lambda *args: alerts.append(args)),
            patch.object(web_ui, "api_start",
                         side_effect=lambda body=None: started.append(body)
                         or {"ok": True}),
            patch.object(web_ui, "_log"),
        ):
            self.assertFalse(web_ui._resident_arm_worker(
                {"mode": "wild", "deck_index": None}))

        self.assertEqual([], started)          # 失败绝不启动自动化
        self.assertTrue(alerts)

    def test_arm_worker_does_not_start_after_resident_was_disabled(self):
        fake_be = SimpleNamespace(
            events=[],
            clear_cancel=lambda: fake_be.events.append("clear_cancel"),
            enter_battle=lambda **kw: {"ok": True},
            select_deck=lambda index, logger=None: {"ok": True},
            is_cancelled=lambda: False,
        )
        started = []
        with (
            patch.dict(sys.modules, {"battle_entry": fake_be}),
            patch.object(web_ui, "_hearthstone_window_ready", return_value=True),
            patch.object(web_ui, "_resident_fsm", return_value=None),
            patch.object(web_ui, "_resident_reset_log_reader"),
            patch.object(web_ui, "_resident_log_in_game", return_value=True),
            patch.object(web_ui, "_resident_enabled", return_value=False),
            patch.object(web_ui, "api_start",
                         side_effect=lambda body=None: started.append(body)
                         or {"ok": True}),
            patch.object(web_ui, "_log"),
        ):
            self.assertFalse(web_ui._resident_arm_worker(
                {"mode": "wild", "deck_index": None}))

        self.assertEqual([], started)


class OverlayTests(_WebStateCase):
    def test_overlay_callbacks_are_wired(self):
        bound = {}
        overlay = SimpleNamespace(start=lambda **kwargs: bound.update(kwargs),
                                  is_running=lambda: False)
        with (
            patch.object(web_ui, "log_overlay", overlay),
            patch.object(web_ui.threading, "Thread"),
            patch.object(web_ui, "_log"),
        ):
            web_ui._bind_overlay()

        self.assertIs(bound["resident_callback"], web_ui._overlay_resident)
        self.assertIs(bound["on_resident_toggle"], web_ui._overlay_resident_toggle)
        self.assertIs(bound["on_resident_apply"], web_ui._overlay_resident_apply)

    def test_overlay_resident_callbacks_are_exception_safe(self):
        fake = _FakeResident({"enabled": True, "state": "running"})
        with patch.object(web_ui, "RESIDENT", fake):
            self.assertEqual("running", web_ui._overlay_resident()["state"])

        with patch.object(web_ui, "api_resident",
                          return_value={"ok": True, "message": "常驻已关闭。"}):
            self.assertEqual("常驻已关闭。", web_ui._overlay_resident_toggle())
        with patch.object(web_ui, "api_resident",
                          side_effect=RuntimeError("boom")):
            self.assertIn("失败", web_ui._overlay_resident_toggle())
        with patch.object(web_ui, "api_resident",
                          return_value={"ok": False, "error": "坏的设置"}):
            self.assertEqual({"ok": False, "message": "坏的设置"},
                             web_ui._overlay_resident_apply({}))

    def test_bind_resident_arms_when_config_already_enabled(self):
        fake = _FakeResident()
        with (
            patch.object(web_ui, "_resident_supervisor", return_value=fake),
            patch.object(web_ui, "_resident_enabled", return_value=True),
            # 启动分类读的是真实 log/resident_intent.json：测试固定成首次启动，
            # 避免开发机上残留的「手动停止」标记让这条断言时绿时红。
            patch.object(web_ui, "_resident_refresh_intent",
                         return_value={"kind": "first_run", "manual_stop": False,
                                       "reason": "", "at": ""}),
            patch.object(web_ui, "_log"),
        ):
            web_ui.CTRL.automation_thread = None
            self.assertIs(fake, web_ui._bind_resident())

        self.assertIn(("start",), fake.calls)
        self.assertTrue(web_ui._RESIDENT_ARM_PENDING)

    def test_bind_resident_does_not_arm_when_automation_running(self):
        fake = _FakeResident()
        with (
            patch.object(web_ui, "_resident_supervisor", return_value=fake),
            patch.object(web_ui, "_resident_enabled", return_value=True),
            patch.object(web_ui, "_resident_refresh_intent",
                         return_value={"kind": "first_run", "manual_stop": False,
                                       "reason": "", "at": ""}),
            patch.object(web_ui, "_log"),
        ):
            web_ui.CTRL.automation_thread = object()
            web_ui._bind_resident()

        self.assertFalse(web_ui._RESIDENT_ARM_PENDING)


class _FakeRequest:
    """最小 HTTP 请求替身：让 Handler.do_POST 的分发表可以脱离服务器直接跑。"""

    _read_json = web_ui.Handler._read_json

    def __init__(self, path, raw=b"{}"):
        self.path = path
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.payload = None
        self.code = 200

    def _json(self, payload, code=200):
        self.payload = payload
        self.code = code


class HttpRouteTests(unittest.TestCase):
    def test_post_route_dispatches_to_api_resident(self):
        sent = []
        request = _FakeRequest("/api/resident",
                               b'{"enabled": true, "mode": "wild"}')
        with patch.object(web_ui, "api_resident",
                          side_effect=lambda body: sent.append(body)
                          or {"ok": True, "message": "saved"}):
            web_ui.Handler.do_POST(request)

        self.assertEqual([{"enabled": True, "mode": "wild"}], sent)
        self.assertTrue(request.payload["ok"])
        self.assertEqual(200, request.code)

    def test_get_does_not_open_a_separate_resident_route(self):
        """读取走 /api/status；GET 不应另外实现 /api/resident。"""
        request = _FakeRequest("/api/resident")
        web_ui.Handler.do_GET(request)
        self.assertEqual(404, request.code)
        self.assertFalse(request.payload["ok"])


class WiringSmokeTests(_WebStateCase):
    def test_real_supervisor_accepts_web_ui_injections(self):
        """真实监督器 + web_ui 注入的回调：签名必须对得上（R1/R2 契约冒烟）。"""
        from src.safety.resident_supervisor import ResidentSupervisor

        settings = {
            "enabled": False,
            "mode": "standard",
            "deck_index": None,
            "stale_in_game_seconds": 180.0,
            "stale_other_seconds": 300.0,
            "max_restarts": 3,
            "window_seconds": 600.0,
            "check_seconds": 5.0,
            "settle_seconds": 120.0,
            "start_delay_seconds": 5.0,
            "dry_run": False,
        }
        supervisor = ResidentSupervisor(
            settings=lambda: dict(settings),
            log_age=lambda: None,
            in_game=lambda: False,
            process_running=lambda: True,
            automation_running=lambda: False,
            stopped_by=lambda: None,
            start_automation=web_ui._resident_start_automation,
            recover=web_ui._resident_recover_dispatch,
            alert=web_ui._resident_alert,
            # R2 注入的定时窗口挂起回调（任务 B）：签名必须对得上。
            hold_reason=web_ui._resident_schedule_hold,
        )
        with patch.object(web_ui, "_log"):
            self.assertEqual("disabled", supervisor.state()["state"])
            self.assertIsNone(supervisor.poll_once())
            supervisor.disable()
            self.assertEqual("disabled", supervisor.state()["state"])


class StartIntentTests(_WebStateCase):
    """网页「▶ 开启常驻并开始」：一次点击就保存并进入对战装配。"""

    @contextmanager
    def _save_env(self, section, saved):
        with (
            patch.object(web_ui, "load_config",
                         side_effect=lambda: {"resident": dict(section)}),
            patch.object(web_ui, "save_config",
                         side_effect=lambda cfg: saved.update(cfg)),
            patch.object(web_ui, "_log"),
        ):
            yield

    def test_start_intent_arms_when_automation_stopped(self):
        fake = _FakeResident({"enabled": True, "state": "paused"})
        saved = {}
        web_ui.CTRL.stopped_by = "user_immediate"
        with (patch.object(web_ui, "RESIDENT", fake),
              self._save_env({"enabled": True}, saved)):
            result = web_ui.api_resident({"start": True, "mode": "wild"})

        self.assertTrue(result["ok"])
        self.assertTrue(saved["resident"]["enabled"])
        self.assertIn(("resume",), fake.calls)
        self.assertIsNone(web_ui.CTRL.stopped_by)
        self.assertTrue(web_ui._RESIDENT_ARM_PENDING)
        self.assertIn("进入对战", result["message"])

    def test_start_intent_allowed_while_automation_is_running(self):
        fake = _FakeResident({"enabled": False, "state": "disabled"})
        saved = {}
        web_ui.CTRL.automation_thread = object()
        with (patch.object(web_ui, "RESIDENT", fake),
              self._save_env({"enabled": False}, saved)):
            result = web_ui.api_resident({"start": True})

        self.assertTrue(result["ok"])
        self.assertTrue(saved["resident"]["enabled"])
        self.assertIn(("resume",), fake.calls)
        # 已经在打：不重复派装配线程，只接管监督。
        self.assertFalse(web_ui._RESIDENT_ARM_PENDING)
        self.assertIn("当前对局", result["message"])

    def test_plain_save_keeps_paused_state(self):
        fake = _FakeResident({"enabled": True, "state": "paused"})
        saved = {}
        web_ui.CTRL.stopped_by = "user_immediate"
        with (patch.object(web_ui, "RESIDENT", fake),
              self._save_env({"enabled": True}, saved)):
            result = web_ui.api_resident({"mode": "wild"})

        self.assertTrue(result["ok"])
        self.assertNotIn(("resume",), fake.calls)
        self.assertEqual("user_immediate", web_ui.CTRL.stopped_by)
        self.assertIn("仍暂停", result["message"])


class StartAfterReadyToleranceTests(_WebStateCase):
    """装配收尾时用户已手动开始对战，不算失败（否则会误暂停常驻）。"""

    def test_already_running_counts_as_success(self):
        alerts = []
        web_ui.CTRL.automation_thread = object()
        with (
            patch.object(web_ui, "_resident_enabled", return_value=True),
            patch.object(web_ui, "_resident_stop_reason", return_value=None),
            patch.object(web_ui, "api_start",
                         return_value={"ok": False, "error": "自动化已经在运行中。"}),
            patch.object(web_ui, "_resident_alert",
                         side_effect=lambda *args: alerts.append(args)),
            patch.object(web_ui, "_log"),
        ):
            self.assertTrue(web_ui._resident_start_after_ready("常驻开启"))

        self.assertEqual([], alerts)

    def test_real_failure_still_reports_and_returns_false(self):
        alerts = []
        web_ui.CTRL.automation_thread = None
        with (
            patch.object(web_ui, "_resident_enabled", return_value=True),
            patch.object(web_ui, "_resident_stop_reason", return_value=None),
            patch.object(web_ui, "api_start",
                         return_value={"ok": False, "error": "日志目录不存在"}),
            patch.object(web_ui, "_resident_alert",
                         side_effect=lambda *args: alerts.append(args)),
            patch.object(web_ui, "_log"),
        ):
            self.assertFalse(web_ui._resident_start_after_ready("常驻开启"))

        self.assertTrue(alerts)


if __name__ == "__main__":
    unittest.main()
