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
                calls.append(("launch", exe, kwargs)) or {
                    "ok": True, "started": "launcher", "running": True,
                    "message": "炉石已启动（战网「开始游戏」）"}),
        )
        return module

    def _fake_battle_entry(self, result=None):
        """假的 battle_entry：只记一步 wait_main_menu，不回真屏幕。"""
        calls = self.calls
        return SimpleNamespace(
            wait_for_main_menu=lambda **kwargs:
            calls.append(("wait_main_menu", kwargs)) or (
                result if result is not None
                else {"ok": True, "state": "main_menu", "message": "已到主界面"}))

    def _run(self, module, config=None, menu_result=None):
        with (
            patch.dict("sys.modules", {
                "hs_restart": module,
                "battle_entry": self._fake_battle_entry(menu_result)}),
            patch.object(web_ui, "load_config",
                         return_value=config if config is not None
                         else {"log_root": "C:/Logs"}),
            patch.object(web_ui, "_focus_hearthstone_if_present",
                         return_value=False),
            # 这些用例只验证「手动重启」的编排顺序；常驻是否管事另有测试覆盖。
            # 不隔离的话，全量跑时前面调用过 status_snapshot 的用例会把
            # web_ui.RESIDENT 单例建出来，重启收尾会多写一行 SYS（常驻恢复），
            # 于是「最后一条日志是 ERROR」的断言会倒在全量顺序上、单跑却通过。
            patch.object(web_ui, "_resident_will_act", return_value=False),
            patch.object(web_ui, "_log") as logger,
        ):
            web_ui._restart_hearthstone_worker()
        return logger

    @staticmethod
    def _messages(logger):
        return [call.args[1] for call in logger.call_args_list]

    def test_runs_every_step_in_order(self):
        logger = self._run(self._fake_hs_restart())

        # 重启的完成判据是「认到主界面」：launch 之后必须还有 wait_main_menu。
        self.assertEqual(["resolve", "fsm_init", "kill", "clear", "launch",
                          "wait_main_menu"],
                         [call[0] for call in self.calls])
        self.assertEqual("C:/Logs", self.calls[0][1])
        self.assertEqual(Path("C:/Hearthstone.exe"), self.calls[4][1])
        # 启动与等主界面都要带置前回调，否则游戏在底层点不着。
        self.assertIn("foreground", self.calls[4][2])
        self.assertIn("foreground", self.calls[5][1])
        self.assertTrue(any("炉石已重新启动" in msg for msg in self._messages(logger)))
        self.assertTrue(any("主界面" in msg for msg in self._messages(logger)))

    def test_stops_a_running_automation_or_gives_up_when_it_will_not_stop(self):
        with self.subTest(case="stops_first"):
            def fake_stop(body):
                self.calls.append(("stop", body))
                web_ui.CTRL.automation_thread = None
                return {"ok": True}

            web_ui.CTRL.automation_thread = object()
            with patch.object(web_ui, "api_stop", side_effect=fake_stop) as stop:
                logger = self._run(self._fake_hs_restart())

            stop.assert_called_once()
            self.assertEqual("now", stop.call_args.args[0]["mode"])
            self.assertEqual(["resolve", "stop", "fsm_init", "kill", "clear",
                              "launch", "wait_main_menu"],
                             [call[0] for call in self.calls])
            self.assertTrue(any("自动化已停止" in msg
                                for msg in self._messages(logger)))

        with self.subTest(case="will_not_stop"):
            self.calls.clear()
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

    def test_aborts_before_destructive_steps_when_resolve_or_kill_fails(self):
        with self.subTest(case="bad_log_folder"):
            module = self._fake_hs_restart(
                resolve=lambda log_root: {
                    "ok": False, "log_root": None, "exe": None,
                    "exe_found": False, "error": "日志目录不存在：C:/nope"})
            logger = self._run(module)

            self.assertEqual([], self.calls)
            self.assertEqual("ERROR", logger.call_args_list[0].args[0])
            self.assertIn("日志目录不存在", logger.call_args_list[0].args[1])

        with self.subTest(case="game_will_not_die"):
            self.calls.clear()

            def failing_kill():
                self.calls.append(("kill",))
                return {"ok": False, "killed": True, "running": True,
                        "error": "Hearthstone.exe 在 20s 内没有退出"}

            logger = self._run(self._fake_hs_restart(kill=failing_kill))

            self.assertEqual(["resolve", "fsm_init", "kill"],
                             [call[0] for call in self.calls])
            self.assertNotIn("clear", [call[0] for call in self.calls])
            self.assertEqual("ERROR", logger.call_args_list[-1].args[0])

        with self.subTest(case="kill_unconfirmed_keeps_going"):
            # 回归：杀进程没确认就中止的话，炉石会停在“被关掉且没人重启”的状态。
            self.calls.clear()

            def unsure_kill():
                self.calls.append(("kill",))
                return {"ok": False, "killed": False, "running": None,
                        "error": "读不到进程列表，无法确认炉石是否已退出"}

            logger = self._run(self._fake_hs_restart(kill=unsure_kill))

            self.assertEqual(["resolve", "fsm_init", "kill", "clear", "launch",
                              "wait_main_menu"],
                             [call[0] for call in self.calls])
            self.assertTrue(any("继续尝试把炉石拉起来" in msg
                                for msg in self._messages(logger)))

    def test_reports_a_failed_relaunch_or_a_missing_main_menu(self):
        with self.subTest(case="relaunch_failed"):
            def failing_launch(exe, **kwargs):
                self.calls.append(("launch", exe))
                return {"ok": False, "started": None, "running": False,
                        "error": "战网「开始游戏」：进程起来后马上又退出了"}

            logger = self._run(self._fake_hs_restart(launch=failing_launch))

            self.assertEqual("ERROR", logger.call_args_list[-1].args[0])
            self.assertIn("没能自动启动", logger.call_args_list[-1].args[1])
            self.assertTrue(any("clear" == call[0] for call in self.calls))

        with self.subTest(case="main_menu_timeout"):
            # 炉石进程起来了但一直没到主界面：不能谎报重启成功。
            self.calls.clear()
            logger = self._run(
                self._fake_hs_restart(),
                menu_result={"ok": False, "state": "unknown",
                             "message": "等待主界面（传统对战）超时（最近一次匹配分 0.29）"})

            self.assertEqual("wait_main_menu", self.calls[-1][0])
            self.assertEqual("WARN", logger.call_args_list[-1].args[0])
            self.assertIn("没到主界面", logger.call_args_list[-1].args[1])
            # 状态要对得上：没成功就不能是 running
            self.assertFalse(web_ui._restart_snapshot()["running"])
            self.assertFalse(web_ui._restart_snapshot()["ok"])


if __name__ == "__main__":
    unittest.main()
