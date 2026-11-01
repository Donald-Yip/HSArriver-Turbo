# -*- coding: utf-8 -*-
"""常驻「手动暂停/开始」与「错误后自动开始」的区分（web_ui 装配层）。

判定与落盘原语在 ``src/safety/resident_intent.py``（见 test_resident_intent.py）；
这里只验证 web_ui 接对了：

* 启动分类：manual → 不登记装配、常驻暂停；error → 按错误自动接管；first_run → 旧行为。
* 用户手动停止/急停/退出脚本/控制台自停 → 落盘「手动停止」标记。
* 用户手动点「开始对战」/显式开启常驻 → 清标记，不按错误恢复处理、不计恢复次数。
* 状态快照暴露 manual_stop / start_cause，供浮窗/网页/CLI 区分展示。
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import web_ui


class _InlineThread:
    """让 threading.Thread 同步跑 target：测 api_start 的 _boot 收尾不用等真线程。"""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self._target = target
        self._args = args or ()
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)

    def is_alive(self):
        return False


class _FakeSupervisor:
    """最小监督器替身：只记录装配层真正会调用的生命周期方法。"""

    def __init__(self, state=None):
        self._state = dict(state or {"enabled": True, "state": "paused"})
        self.calls = []

    def state(self):
        return dict(self._state)

    def start(self):
        self.calls.append(("start",))

    def pause(self, reason="paused"):
        self.calls.append(("pause", reason))

    def resume(self):
        self.calls.append(("resume",))


class _IntentCase(unittest.TestCase):
    """保存/恢复常驻意图的模块级全局，测试之间互不污染。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "resident_intent.json"
        self._saved = {
            "path": web_ui._RESIDENT_INTENT_PATH,
            "state": dict(web_ui._RESIDENT_INTENT_STATE),
            "cause": web_ui._RESIDENT_START_CAUSE,
            "handler": web_ui._CONSOLE_CTRL_HANDLER,
            "arm_pending": web_ui._RESIDENT_ARM_PENDING,
            "thread": web_ui.CTRL.automation_thread,
        }
        web_ui._RESIDENT_INTENT_PATH = self.path
        web_ui._RESIDENT_INTENT_STATE = {
            "kind": "first_run", "manual_stop": False, "scope": "",
            "reason": "", "at": "",
        }
        web_ui._RESIDENT_START_CAUSE = "manual"
        web_ui._RESIDENT_ARM_PENDING = False
        web_ui.CTRL.automation_thread = None

    def tearDown(self):
        web_ui._RESIDENT_INTENT_PATH = self._saved["path"]
        web_ui._RESIDENT_INTENT_STATE = self._saved["state"]
        web_ui._RESIDENT_START_CAUSE = self._saved["cause"]
        web_ui._CONSOLE_CTRL_HANDLER = self._saved["handler"]
        web_ui._RESIDENT_ARM_PENDING = self._saved["arm_pending"]
        web_ui.CTRL.automation_thread = self._saved["thread"]

    # ---- 辅助
    def _bind(self, kind, enabled=True, thread=None):
        fake = _FakeSupervisor()
        info = {"kind": kind, "manual_stop": kind == "manual", "scope": "",
                "reason": "", "at": ""}
        if kind == "manual":
            info.update({"reason": "浮窗退出脚本", "scope": "console",
                         "at": "2026-11-01T04:12:00"})
        logs = []
        with (
            patch.object(web_ui, "_resident_supervisor", return_value=fake),
            patch.object(web_ui, "_resident_enabled", return_value=enabled),
            patch.object(web_ui, "_resident_refresh_intent", return_value=info),
            patch.object(
                web_ui, "_log",
                side_effect=lambda level, message: logs.append(str(message)),
            ),
        ):
            web_ui.CTRL.automation_thread = thread
            supervisor = web_ui._bind_resident()
        return supervisor, fake, info, logs


class IntentRecordTests(_IntentCase):
    def test_record_writes_marker_and_updates_memory(self):
        with patch.object(web_ui, "_log"):
            web_ui._resident_record_manual_stop("用户停止自动化")
        self.assertTrue(self.path.exists())
        data = json.loads(self.path.read_text(encoding="utf8"))
        self.assertTrue(data["manual_stop"])
        self.assertEqual("用户停止自动化", data["reason"])
        self.assertEqual("automation", data["scope"], "默认只停自动化，不挡看门狗")
        self.assertTrue(web_ui._resident_manual_hold())
        self.assertEqual("manual", web_ui._resident_intent_state()["kind"])

    def test_console_scope_marks_watchdog_block(self):
        with patch.object(web_ui, "_log"):
            web_ui._resident_record_manual_stop("浮窗退出脚本", scope="console")
        data = json.loads(self.path.read_text(encoding="utf8"))
        self.assertEqual("console", data["scope"])

    def test_clear_replaces_marker_with_running_record(self):
        # 解除标记用 record_running 覆盖（manual_stop=false），这样之后崩溃
        # 仍能判成 error（会写「自启接管」），不会退化成 first_run。
        web_ui._resident_record_manual_stop("浮窗退出脚本", scope="console")
        with patch.object(web_ui, "_log"):
            self.assertTrue(web_ui._resident_clear_manual_stop("手动开始"))
        self.assertTrue(self.path.exists())
        data = json.loads(self.path.read_text(encoding="utf8"))
        self.assertFalse(data["manual_stop"])
        self.assertFalse(web_ui._resident_manual_hold())
        # 再清一次：没有标记时返回 False（不重复记日志）
        with patch.object(web_ui, "_log"):
            self.assertFalse(web_ui._resident_clear_manual_stop("手动开始"))

    def test_refresh_reads_corrupt_file_as_first_run(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{坏掉的 json", encoding="utf8")
        with patch.object(web_ui, "_log"):
            info = web_ui._resident_refresh_intent()
        self.assertEqual("first_run", info["kind"])
        self.assertFalse(info["manual_stop"])

    def test_non_console_process_does_not_touch_disk_or_memory(self):
        # 测试进程（unittest 是 __main__）即使路径未注入也不能写真实 log/ 文件，
        # 也不能把「手动停止」留在全局：一个测试里的 api_stop / main() 不该让
        # 后续测试的 api_start 误判成手动开始（曾导致全量测试卡死）。
        web_ui._RESIDENT_INTENT_PATH = None
        real = Path(web_ui.__file__).resolve().parent / "log" / "resident_intent.json"
        before = real.exists()
        try:
            with patch.object(web_ui, "_log"):
                web_ui._resident_record_manual_stop("测试")
            self.assertEqual(before, real.exists())
        finally:
            web_ui._RESIDENT_INTENT_PATH = self.path
        self.assertFalse(web_ui._resident_manual_hold())

    def test_state_exposes_manual_fields(self):
        web_ui._RESIDENT_INTENT_STATE = {
            "kind": "manual", "manual_stop": True, "scope": "console",
            "reason": "关闭控制台窗口", "at": "2026-11-01T04:12:00",
        }
        fake = _FakeSupervisor({"enabled": True, "state": "paused"})
        with (
            patch.object(web_ui, "RESIDENT", fake),
            patch.object(web_ui, "_restart_snapshot", return_value={}),
            patch.object(web_ui, "_log"),
        ):
            state = web_ui._resident_state()
        self.assertTrue(state["manual_stop"])
        self.assertEqual("console", state["manual_stop_scope"])
        self.assertEqual("关闭控制台窗口", state["manual_stop_reason"])
        self.assertEqual("2026-11-01T04:12:00", state["manual_stop_at"])
        self.assertIn("start_cause", state)


class BootClassificationTests(_IntentCase):
    def test_manual_exit_keeps_resident_paused_and_never_arms(self):
        _, fake, _, logs = self._bind("manual")
        # 不启动监督线程（避免 start_delay=0 时在 pause 前先判定一次并派发装配）。
        self.assertNotIn(("start",), fake.calls)
        self.assertIn(("pause", "手动退出"), fake.calls)
        self.assertFalse(web_ui._RESIDENT_ARM_PENDING)
        self.assertEqual("manual", web_ui._resident_start_cause())
        joined = "\n".join(logs)
        self.assertIn("上次为手动停止", joined)
        self.assertIn("浮窗退出脚本", joined)

    def test_manual_boot_regression_with_real_supervisor_delay_zero(self):
        # 回归 blocking：start_delay_seconds=0 时，旧实现先 start() 再 pause，监督
        # 线程会在 pause 前判定「自动化没在跑」并派发装配（真会拉起炉石/点鼠标）。
        from src.safety.resident_supervisor import ResidentSupervisor

        supervisor = ResidentSupervisor(
            settings={"enabled": True, "start_delay_seconds": 0.0},
            log_age=lambda: 0.0,
            in_game=lambda: False,
            process_running=lambda: True,
            automation_running=lambda: False,
            stopped_by=lambda: None,
            start_automation=lambda: self._forbidden_dispatch(),
            recover=lambda *a, **k: False,
            alert=lambda message: None,
            sleep=lambda seconds: None,
            clock=__import__("time").monotonic,
        )
        try:
            with (
                patch.object(web_ui, "_resident_refresh_intent",
                             return_value={"kind": "manual",
                                           "manual_stop": True,
                                           "scope": "console",
                                           "reason": "浮窗退出脚本", "at": ""}),
                patch.object(web_ui, "_resident_enabled", return_value=True),
                patch.object(web_ui, "_resident_supervisor",
                             return_value=supervisor),
                patch.object(web_ui, "_log"),
            ):
                web_ui._bind_resident()
            import time

            time.sleep(0.3)  # 给旧实现留出「线程先跑一拍」的窗口
            self.assertEqual("paused", supervisor.state()["state"])
            self.assertIsNone(
                getattr(supervisor, "_thread", None),
                "手动退出后不应启动监督线程（也就不会派发装配）",
            )
        finally:
            supervisor.stop()

    def _forbidden_dispatch(self):
        # 一旦被调用就说明 blocker 复现了；用异常让回归测试立刻失败。
        self.fail("手动退出后监督器不应派发 _resident_start_automation")

    def test_error_exit_autostarts_and_is_labelled_autostart(self):
        _, fake, _, logs = self._bind("error")
        self.assertIn(("start",), fake.calls)
        self.assertNotIn(("pause", "手动退出"), fake.calls)
        self.assertTrue(web_ui._RESIDENT_ARM_PENDING)
        self.assertEqual("autostart", web_ui._resident_start_cause())
        self.assertEqual("守护自启接管", web_ui._resident_arm_label())
        self.assertIn("错误后自动开始", "\n".join(logs))

    def test_first_run_keeps_legacy_auto_arm_behaviour(self):
        _, fake, _, _ = self._bind("first_run")
        self.assertTrue(web_ui._RESIDENT_ARM_PENDING)
        self.assertEqual("守护开启", web_ui._resident_arm_label())
        # 启动成功写一份运行记录：之后崩溃才能被分类成 error（而不是 first_run）。
        data = json.loads(self.path.read_text(encoding="utf8"))
        self.assertFalse(data["manual_stop"])
        self.assertEqual("console_boot", data["reason"])

    def test_manual_boot_does_not_overwrite_marker(self):
        with patch.object(web_ui, "_log"):
            web_ui._resident_record_manual_stop("浮窗退出脚本")
        self._bind("manual")
        data = json.loads(self.path.read_text(encoding="utf8"))
        self.assertTrue(data["manual_stop"])
        self.assertEqual("浮窗退出脚本", data["reason"])

    def test_manual_exit_is_not_armed_even_with_automation_missing(self):
        web_ui._RESIDENT_ARM_PENDING = True  # 上一轮残留
        self._bind("manual")
        self.assertFalse(web_ui._RESIDENT_ARM_PENDING)

    def test_disabled_resident_does_not_arm_but_still_paused_on_manual(self):
        _, fake, _, _ = self._bind("manual", enabled=False)
        self.assertIn(("pause", "手动退出"), fake.calls)
        self.assertFalse(web_ui._RESIDENT_ARM_PENDING)


class ManualStartTests(_IntentCase):
    def test_manual_start_clears_marker_and_resumes_without_recovery(self):
        fake = _FakeSupervisor()
        with (
            patch.object(web_ui, "RESIDENT", fake),
            patch.object(web_ui, "_resident_clear_manual_stop",
                         return_value=True) as clear,
            patch.object(web_ui, "_log"),
        ):
            web_ui._resident_after_manual_start()
        clear.assert_called_once_with("手动开始对战")
        self.assertIn(("resume",), fake.calls)
        self.assertEqual("manual", web_ui._resident_start_cause())

    def test_no_marker_means_no_resume(self):
        fake = _FakeSupervisor()
        with (
            patch.object(web_ui, "RESIDENT", fake),
            patch.object(web_ui, "_resident_clear_manual_stop",
                         return_value=False),
            patch.object(web_ui, "_log"),
        ):
            web_ui._resident_after_manual_start()
        self.assertNotIn(("resume",), fake.calls)
        # 即使本来就没标记，手动开始也应把来源改成 manual（否则 error 启动后
        # 日志还会写「自启接管」）。
        self.assertEqual("manual", web_ui._resident_start_cause())

    def test_manual_hold_blocks_start_automation_as_guard(self):
        # 双保险：即使监督线程被误启/误调度，manual hold 时也绝不派发装配。
        web_ui._RESIDENT_INTENT_STATE = {
            "kind": "manual", "manual_stop": True, "scope": "console",
            "reason": "浮窗退出脚本", "at": "",
        }
        with (
            patch.object(web_ui, "_resident_arm_dispatch",
                         side_effect=AssertionError("不应派发装配")),
            patch.object(web_ui, "_log"),
        ):
            self.assertTrue(web_ui._resident_start_automation())

    def test_manual_start_does_not_create_supervisor(self):
        with (
            patch.object(web_ui, "RESIDENT", None),
            patch.object(web_ui, "_resident_clear_manual_stop",
                         return_value=True),
            patch.object(web_ui, "_resident_supervisor",
                         side_effect=AssertionError("不应创建监督器")),
            patch.object(web_ui, "_log"),
        ):
            web_ui._resident_after_manual_start()


class ApiMarkingTests(_IntentCase):
    def test_api_stop_marks_manual_stop(self):
        with (
            patch.object(web_ui, "_resident_request_pause"),
            patch.object(web_ui, "_resident_record_manual_stop") as mark,
        ):
            web_ui.api_stop({"mode": "now"})
        mark.assert_called_once_with("用户停止自动化")

    def test_api_stop_after_game_marks_manual_stop(self):
        fake_fsm = SimpleNamespace(request_stop_after_game=lambda: None)
        web_ui.CTRL.automation_thread = object()
        web_ui.CTRL.fsm = fake_fsm
        try:
            with (
                patch.object(web_ui, "_resident_request_pause"),
                patch.object(web_ui, "_resident_record_manual_stop") as mark,
            ):
                web_ui.api_stop({"mode": "after_game"})
        finally:
            web_ui.CTRL.automation_thread = None
            web_ui.CTRL.fsm = None
        mark.assert_called_once_with("用户停止（本局结束后）")

    def test_internal_stop_does_not_mark_manual_stop(self):
        web_ui.CTRL.automation_thread = object()
        web_ui.CTRL.fsm = SimpleNamespace(request_immediate_stop=lambda: None)
        try:
            with (
                patch.object(web_ui, "_resident_request_pause"),
                patch.object(web_ui, "_resident_record_manual_stop") as mark,
            ):
                web_ui.api_stop({"mode": "now", "_resident_internal": True})
        finally:
            web_ui.CTRL.automation_thread = None
            web_ui.CTRL.fsm = None
        mark.assert_not_called()

    def test_api_cancel_marks_manual_stop(self):
        be = SimpleNamespace(request_cancel=lambda: None, is_cancelled=lambda: False)
        with (
            patch.dict(sys.modules, {"battle_entry": be}),
            patch.object(web_ui, "_resident_request_pause"),
            patch.object(web_ui, "_resident_record_manual_stop") as mark,
            patch.object(web_ui, "_log"),
        ):
            web_ui.api_cancel({})
        mark.assert_called_once_with("用户急停/取消")

    def test_api_cancel_from_hotkey_names_ctrlq(self):
        be = SimpleNamespace(request_cancel=lambda: None, is_cancelled=lambda: False)
        with (
            patch.dict(sys.modules, {"battle_entry": be}),
            patch.object(web_ui, "_resident_request_pause"),
            patch.object(web_ui, "_resident_record_manual_stop") as mark,
            patch.object(web_ui, "_log"),
        ):
            web_ui.api_cancel({"source": "hotkey"})
        mark.assert_called_once_with("Ctrl+Q 急停")

    def test_overlay_exit_marks_then_exits(self):
        with (
            patch.object(web_ui, "api_stop"),
            patch.object(web_ui, "_stop_calibration"),
            patch.object(web_ui, "_resident_record_manual_stop") as mark,
            patch.object(web_ui, "_log"),
            patch.object(os, "_exit") as exit_mock,
        ):
            web_ui._overlay_exit()
        mark.assert_called_once_with("浮窗退出脚本", scope="console")
        exit_mock.assert_called_once_with(0)

    def test_self_shutdown_marks_manual_stop(self):
        with (
            patch.object(web_ui, "_clear_port_file"),
            patch.object(web_ui, "_resident_record_manual_stop") as mark,
            patch.object(os, "_exit") as exit_mock,
        ):
            web_ui._self_shutdown()
        mark.assert_called_once_with(
            "控制台自停（hsctl serve stop）", scope="console"
        )
        exit_mock.assert_called_once_with(0)

    def test_restart_shutdown_keeps_intent_clean(self):
        # hsctl serve restart：停自动化走 internal（不写手动标记），自停也不写。
        with (
            patch.object(web_ui, "_clear_port_file"),
            patch.object(web_ui, "_resident_record_manual_stop") as mark,
            patch.object(web_ui, "api_stop") as stop,
            patch.object(os, "_exit") as exit_mock,
        ):
            web_ui.CTRL.automation_thread = None
            web_ui._self_shutdown(keep_intent=True)
        mark.assert_not_called()
        exit_mock.assert_called_once_with(0)
        for call in stop.call_args_list:
            if call.args:
                self.assertTrue(call.args[0].get("_resident_internal"))

    def test_serve_stop_api_passes_keep_intent(self):
        with (
            patch.object(web_ui, "_self_shutdown") as shutdown,
            patch.object(web_ui.threading, "Thread", _InlineThread),
        ):
            payload, code = web_ui.api_serve_stop(
                {"token": web_ui._SERVE_TOKEN, "keep_intent": True}
            )
        self.assertEqual(200, code)
        self.assertTrue(payload["ok"])
        self.assertEqual(True, shutdown.call_args.args[0])

    def test_manual_api_start_finishes_as_manual_start(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(web_ui, "load_config",
                             return_value={"name": "玩家#1234", "log_root": tmp}), \
                patch.object(web_ui, "_start_automation",
                             return_value=(True, "自动化已启动")), \
                patch.object(web_ui, "_resident_after_manual_start") as after, \
                patch.object(web_ui, "_resident_set_arm_pending"), \
                patch.object(web_ui, "_resident_note_automation_started"), \
                patch.object(web_ui, "_persist_schedule"), \
                patch.object(web_ui.threading, "Thread", _InlineThread), \
                patch.object(web_ui, "_log"):
            result = web_ui.api_start({})
        self.assertTrue(result["ok"])
        after.assert_called_once_with()

    def test_explicit_enable_clears_manual_marker(self):
        saved = {}
        with (
            patch.object(web_ui, "RESIDENT", _FakeSupervisor()),
            patch.object(web_ui, "load_config", side_effect=lambda: {}),
            patch.object(web_ui, "save_config",
                         side_effect=lambda cfg: saved.update(cfg)),
            patch.object(web_ui, "_resident_state", return_value={}),
            patch.object(web_ui, "_resident_resume") as resume,
            patch.object(web_ui, "_resident_clear_manual_stop") as clear,
            patch.object(web_ui, "_log"),
        ):
            result = web_ui.api_resident({"enabled": True})
        self.assertTrue(result["ok"])
        clear.assert_called_once_with("显式开启常驻")
        resume.assert_called_once()
        self.assertEqual("manual", web_ui._resident_start_cause())


class ConsoleHandlerTests(_IntentCase):
    def test_noop_on_non_windows(self):
        web_ui._CONSOLE_CTRL_HANDLER = None
        with patch.object(web_ui.os, "name", "posix"):
            self.assertFalse(web_ui._install_console_ctrl_handler())

    def test_idempotent_when_handler_already_registered(self):
        web_ui._CONSOLE_CTRL_HANDLER = object()
        self.assertFalse(web_ui._install_console_ctrl_handler())


if __name__ == "__main__":
    unittest.main()
