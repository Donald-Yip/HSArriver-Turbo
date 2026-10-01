# -*- coding: utf-8 -*-
"""炉石重置（hs_restart）：路径解析 / 清空日志 / 中止进程 / 重新拉起。

全部外部依赖（taskkill、startfile、进程检测、sleep）都注入假实现，
所以这些用例既不需要真的炉石，也不会碰真实磁盘上的日志目录。
"""

import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import hs_restart
import log_overlay


def make_temp_dir() -> Path:
    """建一个普通权限的临时目录。

    不用 tempfile.TemporaryDirectory：它会把目录设成 0700，在受限环境里
    反而写不进去。这里自己 mkdir 再自己删。
    """
    path = (Path(tempfile.gettempdir())
            / f"hs_restart_{os.getpid()}_{uuid.uuid4().hex[:8]}")
    path.mkdir(parents=True, exist_ok=True)
    return path


class ResolvePathsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        # 真实布局：<...>\Battle.net\Hearthstone\Logs
        self.install = self.tmp / "Battle.net" / "Hearthstone"
        self.logs = self.install / "Logs"
        self.logs.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_derives_the_exe_from_the_logs_folder(self):
        (self.install / hs_restart.EXE_NAME).write_text("", encoding="utf-8")

        info = hs_restart.resolve_paths(self.logs)

        self.assertTrue(info["ok"])
        self.assertEqual(self.logs.resolve(), info["log_root"])
        # info 里的路径来自 Path.resolve()，而 tempfile.gettempdir() 可能给的是
        # 8.3 短名（如 C:\Users\DONALD~1\...），所以期望值也先 resolve 再比。
        self.assertEqual((self.install / hs_restart.EXE_NAME).resolve(),
                         info["exe"])
        self.assertTrue(info["exe_found"])

    def test_derives_the_battlenet_launcher_from_the_sibling_folder(self):
        launcher = (self.tmp / "Battle.net" / hs_restart.BATTLENET_EXE_NAME)
        launcher.write_text("", encoding="utf-8")

        info = hs_restart.resolve_paths(self.logs)

        self.assertTrue(info["ok"])
        self.assertEqual(launcher.resolve(), info["battlenet_exe"])
        self.assertTrue(info["battlenet_found"])

    def test_missing_exe_is_reported_but_still_ok(self):
        info = hs_restart.resolve_paths(self.logs)

        self.assertTrue(info["ok"])
        self.assertFalse(info["exe_found"])

    def test_empty_or_missing_folder_is_refused(self):
        for value in ("", "   ", self.tmp / "nope"):
            with self.subTest(value=value):
                info = hs_restart.resolve_paths(value)
                self.assertFalse(info["ok"])
                self.assertTrue(info["error"])

    def test_drive_root_is_refused(self):
        drive_root = Path(os.environ.get("SystemDrive", "C:") + "\\")

        info = hs_restart.resolve_paths(drive_root)

        self.assertFalse(info["ok"])
        self.assertIn("盘符根", info["error"])


class ClearLogsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.logs = self.tmp / "Logs"
        self.logs.mkdir()
        (self.logs / "Log.config").write_text("keep", encoding="utf-8")
        (self.logs / "Power.log").write_text("old", encoding="utf-8")
        session = self.logs / "Hearthstone_2026_10_01_17_23_03"
        session.mkdir()
        (session / "Power.log").write_text("old session", encoding="utf-8")
        # 上级目录里的东西绝不能被碰
        (self.tmp / "sibling.txt").write_text("safe", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_removes_everything_except_the_keep_list(self):
        result = hs_restart.clear_logs(self.logs)

        self.assertTrue(result["ok"])
        self.assertEqual(2, result["removed"])
        self.assertEqual(["Log.config"], result["skipped"])
        self.assertEqual(["Log.config"], sorted(p.name for p in self.logs.iterdir()))
        self.assertTrue((self.tmp / "sibling.txt").is_file())

    def test_reports_entries_it_could_not_remove(self):
        with patch.object(hs_restart.shutil, "rmtree",
                          side_effect=OSError("被占用")):
            result = hs_restart.clear_logs(self.logs)

        self.assertFalse(result["ok"])
        self.assertEqual(1, len(result["failed"]))
        self.assertIn("被占用", result["errors"][0])
        # 删不掉的那个 session 目录还在，另一个文件已经删掉了
        self.assertTrue((self.logs / "Log.config").is_file())
        self.assertFalse((self.logs / "Power.log").exists())

    def test_refuses_a_bad_root(self):
        result = hs_restart.clear_logs(self.tmp / "nope")

        self.assertFalse(result["ok"])
        self.assertEqual(0, result["removed"])
        self.assertTrue(result["errors"])


class _Probe:
    """进程检测替身：按给定序列返回，用完就一直返回最后一个。"""

    def __init__(self, *states):
        self.states = list(states)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if len(self.states) > 1:
            return self.states.pop(0)
        return self.states[0] if self.states else None


class KillHearthstoneTests(unittest.TestCase):
    def setUp(self):
        self.commands = []

        def runner(command, **kwargs):
            self.commands.append((command, kwargs))

        self.runner = runner

    def test_skips_when_the_game_is_not_running(self):
        result = hs_restart.kill_hearthstone(
            runner=self.runner, process_running=_Probe(False), sleeper=lambda _s: None)

        self.assertTrue(result["ok"])
        self.assertFalse(result["killed"])
        self.assertEqual([], self.commands)

    def test_kills_and_waits_until_the_process_is_gone(self):
        result = hs_restart.kill_hearthstone(
            runner=self.runner, process_running=_Probe(True, True, False),
            sleeper=lambda _s: None)

        self.assertTrue(result["ok"])
        self.assertTrue(result["killed"])
        self.assertEqual(1, len(self.commands))
        command, _kwargs = self.commands[0]
        self.assertEqual(["taskkill", "/F", "/T", "/IM", hs_restart.EXE_NAME],
                         command)

    def test_times_out_when_the_process_never_leaves(self):
        result = hs_restart.kill_hearthstone(
            runner=self.runner, process_running=_Probe(True),
            sleeper=lambda _s: None, timeout=0.0)

        self.assertFalse(result["ok"])
        self.assertTrue(result["running"])
        self.assertIn("没有退出", result["error"])

    def test_taskkill_failure_is_reported(self):
        def broken_runner(command, **kwargs):
            raise OSError("拒绝访问")

        result = hs_restart.kill_hearthstone(
            runner=broken_runner, process_running=_Probe(True),
            sleeper=lambda _s: None)

        self.assertFalse(result["ok"])
        self.assertIn("taskkill", result["error"])


class LaunchHearthstoneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.opened = []
        self.launcher_calls = []
        self.lines = []

        def opener(path):
            self.opened.append(path)

        def launcher():
            self.launcher_calls.append("launcher")
            return True

        self.opener = opener
        self.launcher = launcher
        self.logger = self.lines.append

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _exe(self):
        exe = self.tmp / hs_restart.EXE_NAME
        exe.write_text("", encoding="utf-8")
        return exe

    def _launch(self, **kwargs):
        params = dict(exe=self._exe(), launcher=self.launcher,
                      battlenet_ready=lambda: True,      # 默认：战网已在运行
                      opener=self.opener, logger=self.logger,
                      sleeper=lambda _s: None)
        params.update(kwargs)
        return hs_restart.launch_hearthstone(**params)

    def test_prefers_battlenet_and_waits_for_the_game_window(self):
        result = self._launch(process_running=_Probe(True),
                              window_ready=lambda: True, timeout=5.0)

        self.assertTrue(result["ok"])
        self.assertEqual("launcher", result["started"])
        self.assertEqual(["launcher"], self.launcher_calls)
        self.assertEqual([], self.opened)          # 根本没去碰 exe
        self.assertIn("战网", result["message"])

    def test_starts_battlenet_first_when_it_is_not_running(self):
        """回归：战网没开时直启 Hearthstone.exe 会秒退（重启一半又关了）。"""
        state = {"battlenet": False}

        def battlenet_ready():
            return state["battlenet"]

        def opener(path):
            self.opened.append(path)
            if Path(path).name == hs_restart.BATTLENET_EXE_NAME:
                state["battlenet"] = True

        battlenet_exe = self.tmp / hs_restart.BATTLENET_EXE_NAME
        battlenet_exe.write_text("", encoding="utf-8")

        result = self._launch(battlenet_exe=battlenet_exe,
                              battlenet_ready=battlenet_ready,
                              opener=opener,
                              process_running=_Probe(True),
                              window_ready=lambda: True, timeout=5.0,
                              battlenet_timeout=1.0)

        self.assertTrue(result["ok"])
        self.assertEqual("launcher", result["started"])
        self.assertEqual(str(battlenet_exe), self.opened[0])   # 先拉战网
        self.assertEqual(["launcher"], self.launcher_calls)     # 再点「开始游戏」
        self.assertTrue(any("先启动战网" in line for line in self.lines))

    def test_falls_back_to_the_exe_when_battlenet_is_unavailable(self):
        def missing_battlenet():
            self.launcher_calls.append("launcher")
            return False

        result = self._launch(launcher=missing_battlenet,
                              battlenet_ready=missing_battlenet,
                              process_running=_Probe(True),
                              window_ready=lambda: True, timeout=5.0)

        self.assertTrue(result["ok"])
        self.assertEqual("exe", result["started"])
        self.assertEqual([str(self._exe())], self.opened)

    def test_the_game_window_is_the_success_judgement(self):
        """进程一直没检测到、但游戏窗口出现了（例如用户已手动打开）也算成功。"""
        result = self._launch(process_running=_Probe(False),
                              window_ready=lambda: True, timeout=1.0)

        self.assertTrue(result["ok"])

    def test_fast_exit_is_detected_instead_of_reporting_success(self):
        """回归：原来一看进程在就报“已启动”，实际它秒退 —— 用户看到“重启一半又关了”。"""
        def missing_battlenet():
            return False

        result = self._launch(launcher=missing_battlenet,
                              battlenet_ready=missing_battlenet,
                              process_running=_Probe(True, False),
                              window_ready=lambda: False, timeout=2.0)

        self.assertFalse(result["ok"])
        self.assertIn("又退出", result["error"])
        self.assertTrue(any("没起来" in line for line in self.lines))

    def test_without_a_window_probe_the_process_must_stay_alive(self):
        ok = self._launch(process_running=_Probe(True), window_ready=None,
                          timeout=1.0, settle=0.05)
        died = self._launch(process_running=_Probe(True, False),
                            window_ready=None, timeout=1.0, settle=0.05)

        self.assertTrue(ok["ok"])
        self.assertFalse(died["ok"])
        self.assertIn("退出", died["error"])

    def test_reports_every_attempt_when_nothing_starts(self):
        def missing_battlenet():
            return False

        result = self._launch(launcher=missing_battlenet,
                              battlenet_ready=missing_battlenet,
                              process_running=_Probe(False),
                              window_ready=lambda: False, timeout=0.1)

        self.assertFalse(result["ok"])
        self.assertIn("战网", result["error"])
        self.assertIn("直接启动", result["error"])

    def test_without_exe_and_launcher_it_refuses(self):
        result = hs_restart.launch_hearthstone(
            None, opener=self.opener, process_running=_Probe(True),
            sleeper=lambda _s: None)

        self.assertFalse(result["ok"])
        self.assertIn(hs_restart.EXE_NAME, result["error"])

    def test_nowait_only_fires_the_launch(self):
        result = self._launch(wait=False)

        self.assertTrue(result["ok"])
        self.assertEqual(["launcher"], self.launcher_calls)


class OverlayDelayDisplayTests(unittest.TestCase):
    """延时与重试时间必须能在浮窗里看见。

    浮窗底部进度条靠解析「延时 X s 后……」这种日志行驱动
    （log_overlay._update_delay_from_line），所以这里直接拿启动过程播报的
    原话来验证：格式对得上、秒数对得上。
    """

    def _launch_lines(self, slept=None, **kwargs):
        lines = []
        params = dict(exe=None, launcher=lambda: True,
                      battlenet_ready=lambda: True,
                      process_running=_Probe(True, False),
                      window_ready=lambda: False,
                      sleeper=(slept.append if slept is not None
                               else (lambda _s: None)),
                      timeout=0.2, before_click_delay=5.0,
                      logger=lines.append)
        params.update(kwargs)
        hs_restart.launch_hearthstone(**params)
        return lines

    def test_five_second_delay_before_clicking_play(self):
        slept = []

        lines = self._launch_lines(slept=slept)

        self.assertIn(5.0, slept)                       # 真的等了 5 秒
        delay_lines = [line for line in lines if line.startswith("延时 ")]
        self.assertTrue(any("点战网「开始游戏」" in line for line in delay_lines))
        # 延时行排在“正在通过战网拉起炉石”之前
        self.assertLess(lines.index(next(line for line in lines
                                         if "点战网「开始游戏」" in line)),
                        lines.index(next(line for line in lines
                                         if line.startswith("正在通过"))))

    def test_lines_drive_the_overlay_delay_bar(self):
        lines = self._launch_lines()

        bars = []
        for line in lines:
            match = log_overlay._delay_start_re.search(line)
            if match:
                bars.append((float(match.group(1)),
                             log_overlay._delay_desc(line)))
        self.assertIn((5.0, "点战网「开始游戏」"), bars)
        self.assertTrue(any(total == 15.0 and "重试点「开始游戏」" in desc
                            for total, desc in bars),
                        f"没播报重试倒计时：{bars}")

    def test_that_line_really_sets_the_overlay_countdown(self):
        lines = self._launch_lines()
        line = next(line for line in lines
                    if line.startswith("延时 ") and "点战网「开始游戏」" in line)

        saved = log_overlay._DELAY
        try:
            log_overlay._DELAY = None
            log_overlay._update_delay_from_line(line)
            self.assertIsNotNone(log_overlay._DELAY)
            self.assertEqual(5.0, log_overlay._DELAY["total"])
            self.assertEqual("点战网「开始游戏」", log_overlay._DELAY["desc"])
        finally:
            log_overlay._DELAY = saved

    def test_retry_countdown_is_announced_with_the_attempt_number(self):
        lines = self._launch_lines(retry_after=0.1, timeout=1.0,
                                   before_click_delay=0.0)

        retry_lines = [line for line in lines if "重试点「开始游戏」" in line]
        self.assertTrue(retry_lines)
        self.assertIn("第 1 次", retry_lines[0])

    def test_wait_line_tells_how_long_and_how_often(self):
        lines = self._launch_lines(timeout=45.0, retry_after=15.0)

        wait = next(line for line in lines if line.startswith("等待炉石"))
        self.assertIn("45", wait)
        self.assertIn("15", wait)

    def test_zero_delay_does_not_print_a_countdown(self):
        lines = self._launch_lines(before_click_delay=0.0, retry_after=600.0)

        self.assertFalse(any("点战网「开始游戏」" in line and
                             line.startswith("延时 ") for line in lines))


if __name__ == "__main__":
    unittest.main()
