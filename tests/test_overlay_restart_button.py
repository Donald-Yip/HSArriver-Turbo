# -*- coding: utf-8 -*-
"""浮窗「重启炉石」与「退出浮窗」：按钮接线 + 后台编排顺序。

不真的动炉石/日志目录：hs_restart 换成假模块，只验证编排顺序与失败时的中止。
"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import log_overlay
import web_ui


def _stub():
    return True


class OverlayWiringTests(unittest.TestCase):
    def test_start_stores_the_new_callbacks(self):
        saved = (log_overlay._ON_RESTART, log_overlay._ON_EXIT_OVERLAY)
        try:
            log_overlay._STARTED[0] = False
            with patch.object(log_overlay.threading, "Thread"):
                log_overlay.start(on_restart=_stub, on_exit_overlay=_stub)
            self.assertIs(_stub, log_overlay._ON_RESTART)
            self.assertIs(_stub, log_overlay._ON_EXIT_OVERLAY)
        finally:
            log_overlay._ON_RESTART, log_overlay._ON_EXIT_OVERLAY = saved
            log_overlay._STARTED[0] = False

    def test_web_ui_wires_both_callbacks(self):
        bound = {}
        overlay = SimpleNamespace(start=lambda **kwargs: bound.update(kwargs),
                                  is_running=lambda: False)

        with (
            patch.object(web_ui, "log_overlay", overlay),
            patch.object(web_ui.threading, "Thread"),
        ):
            web_ui._bind_overlay()

        self.assertIs(bound["on_restart"], web_ui._overlay_restart_hearthstone)
        self.assertIs(bound["on_exit_overlay"], web_ui._overlay_exit_overlay)

    def test_exit_overlay_callback_only_logs(self):
        """退出浮窗只写一行日志 + 放掉就绪状态，绝不碰 _overlay_exit（会 os._exit）。"""
        logged = []
        saved = web_ui.CTRL.prepared
        try:
            with (
                patch.object(web_ui, "_log",
                             side_effect=lambda level, msg: logged.append((level, msg))),
                patch.object(web_ui, "_overlay_exit") as script_exit,
            ):
                web_ui.CTRL.prepared = True
                web_ui._overlay_exit_overlay()

            script_exit.assert_not_called()
            self.assertFalse(web_ui.CTRL.prepared)     # 关掉浮窗 = 回到未就绪
        finally:
            web_ui.CTRL.prepared = saved
        self.assertEqual("SYS", logged[0][0])
        self.assertIn("日志浮窗", logged[0][1])


class RestartButtonTests(unittest.TestCase):
    def test_button_returns_immediately_and_works_in_a_thread(self):
        with patch.object(web_ui.threading, "Thread") as thread_cls:
            self.assertTrue(web_ui._overlay_restart_hearthstone())

        thread_cls.assert_called_once()
        kwargs = thread_cls.call_args.kwargs
        self.assertIs(web_ui._restart_hearthstone_worker, kwargs["target"])
        self.assertTrue(kwargs["daemon"])


class RestartWorkerTests(unittest.TestCase):
    """编排顺序：resolve → (停自动化) → fsm.init → kill → clear → launch。"""

    def setUp(self):
        self.calls = []
        self.saved_thread = web_ui.CTRL.automation_thread
        self.saved_fsm = web_ui.CTRL.fsm
        web_ui.CTRL.automation_thread = None
        web_ui.CTRL.fsm = SimpleNamespace(
            init=lambda: self.calls.append(("fsm_init",)))

    def tearDown(self):
        web_ui.CTRL.automation_thread = self.saved_thread
        web_ui.CTRL.fsm = self.saved_fsm

    def _fake_hs_restart(self, resolve=None, kill=None, clear=None,
                         launch=None):
        calls = self.calls
        module = SimpleNamespace(
            resolve_paths=resolve or (
                lambda log_root: calls.append(("resolve", log_root)) or {
                    "ok": True, "log_root": Path("C:/Logs"),
                    "exe": Path("C:/Hearthstone.exe"), "exe_found": True,
                    "error": ""}),
            kill_hearthstone=kill or (
                lambda: calls.append(("kill",)) or {
                    "ok": True, "killed": True, "message": "Hearthstone.exe 已退出"}),
            clear_logs=clear or (
                lambda root, logger=None: calls.append(("clear", root)) or {
                    "ok": True, "removed": 2, "skipped": ["Log.config"],
                    "failed": [], "errors": []}),
            launch_hearthstone=launch or (
                lambda exe, **kwargs:
                calls.append(("launch", exe)) or {
                    "ok": True, "started": "launcher", "running": True,
                    "message": "炉石已启动（战网「开始游戏」）"}),
        )
        return module

    def _run(self, module, config=None):
        with (
            patch.dict("sys.modules", {"hs_restart": module}),
            patch.object(web_ui, "load_config",
                         return_value=config if config is not None
                         else {"log_root": "C:/Logs"}),
            patch.object(web_ui, "_log") as logger,
        ):
            web_ui._restart_hearthstone_worker()
        return logger

    @staticmethod
    def _messages(logger):
        return [call.args[1] for call in logger.call_args_list]

    def test_runs_every_step_in_order(self):
        logger = self._run(self._fake_hs_restart())

        self.assertEqual(["resolve", "fsm_init", "kill", "clear", "launch"],
                         [call[0] for call in self.calls])
        self.assertEqual("C:/Logs", self.calls[0][1])
        self.assertEqual(Path("C:/Hearthstone.exe"), self.calls[-1][1])
        self.assertTrue(any("重启完成" in msg for msg in self._messages(logger)))

    def test_stops_a_running_automation_before_touching_anything(self):
        def fake_stop(body):
            self.calls.append(("stop", body))
            web_ui.CTRL.automation_thread = None
            return {"ok": True}

        web_ui.CTRL.automation_thread = object()
        with patch.object(web_ui, "api_stop", side_effect=fake_stop) as stop:
            logger = self._run(self._fake_hs_restart())

        stop.assert_called_once_with({"mode": "now"})
        self.assertEqual(["resolve", "stop", "fsm_init", "kill", "clear",
                          "launch"],
                         [call[0] for call in self.calls])
        self.assertTrue(any("自动化已停止" in msg for msg in self._messages(logger)))

    def test_gives_up_when_the_automation_will_not_stop(self):
        web_ui.CTRL.automation_thread = object()      # 永远不会被清掉
        with (
            patch.object(web_ui, "api_stop"),
            patch.object(web_ui, "AUTOMATION_STOP_TIMEOUT", 0.0),
        ):
            logger = self._run(self._fake_hs_restart())

        # 只做了只读的路径解析，绝没杀进程/删文件
        self.assertEqual(["resolve"], [call[0] for call in self.calls])
        self.assertEqual("ERROR", logger.call_args_list[-1].args[0])
        self.assertIn("停下来", logger.call_args_list[-1].args[1])

    def test_refuses_a_bad_log_folder(self):
        module = self._fake_hs_restart(
            resolve=lambda log_root: {"ok": False, "log_root": None, "exe": None,
                                      "exe_found": False,
                                      "error": "日志目录不存在：C:/nope"})

        logger = self._run(module)

        self.assertEqual([], self.calls)
        self.assertEqual("ERROR", logger.call_args_list[0].args[0])
        self.assertIn("日志目录不存在", logger.call_args_list[0].args[1])

    def test_stops_before_wiping_when_the_game_will_not_die(self):
        def failing_kill():
            self.calls.append(("kill",))
            return {"ok": False, "killed": True, "running": True,
                    "error": "Hearthstone.exe 在 20s 内没有退出"}

        module = self._fake_hs_restart(kill=failing_kill)

        logger = self._run(module)

        self.assertEqual(["resolve", "fsm_init", "kill"],
                         [call[0] for call in self.calls])
        self.assertNotIn("clear", [call[0] for call in self.calls])
        self.assertEqual("ERROR", logger.call_args_list[-1].args[0])

    def test_reports_a_failed_relaunch(self):
        def failing_launch(exe, **kwargs):
            self.calls.append(("launch", exe))
            return {"ok": False, "started": None, "running": False,
                    "error": "战网「开始游戏」：进程起来后马上又退出了"}

        module = self._fake_hs_restart(launch=failing_launch)

        logger = self._run(module)

        self.assertEqual("ERROR", logger.call_args_list[-1].args[0])
        self.assertIn("没能自动启动", logger.call_args_list[-1].args[1])
        self.assertTrue(any("clear" == call[0] for call in self.calls))

    def test_keeps_going_when_the_kill_cannot_be_confirmed(self):
        """回归：杀进程没确认就中止的话，炉石会停在“被关掉且没人重启”的状态。"""
        def unsure_kill():
            self.calls.append(("kill",))
            return {"ok": False, "killed": False, "running": None,
                    "error": "读不到进程列表，无法确认炉石是否已退出"}

        module = self._fake_hs_restart(kill=unsure_kill)

        logger = self._run(module)

        self.assertEqual(["resolve", "fsm_init", "kill", "clear", "launch"],
                         [call[0] for call in self.calls])
        self.assertTrue(any("继续尝试把炉石拉起来" in msg
                            for msg in self._messages(logger)))


if __name__ == "__main__":
    unittest.main()
