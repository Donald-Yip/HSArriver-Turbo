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
        self.logs = self.tmp / "Logs"
        self.logs.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_derives_the_exe_from_the_logs_folder(self):
        (self.tmp / hs_restart.EXE_NAME).write_text("", encoding="utf-8")

        info = hs_restart.resolve_paths(self.logs)

        self.assertTrue(info["ok"])
        self.assertEqual(self.logs.resolve(), info["log_root"])
        self.assertEqual(self.tmp / hs_restart.EXE_NAME, info["exe"])
        self.assertTrue(info["exe_found"])

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
        self.calls = []

        def opener(path):
            self.opened.append(path)

        def fallback():
            self.calls.append("launcher")
            return True

        self.opener = opener
        self.fallback = fallback

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_starts_the_exe_when_it_exists(self):
        exe = self.tmp / hs_restart.EXE_NAME
        exe.write_text("", encoding="utf-8")

        result = hs_restart.launch_hearthstone(
            exe, opener=self.opener, fallback=self.fallback,
            process_running=_Probe(True), sleeper=lambda _s: None)

        self.assertTrue(result["ok"])
        self.assertEqual("exe", result["started"])
        self.assertEqual([str(exe)], self.opened)
        self.assertEqual([], self.calls)

    def test_falls_back_to_the_launcher_without_the_exe(self):
        result = hs_restart.launch_hearthstone(
            self.tmp / "missing.exe", opener=self.opener, fallback=self.fallback,
            process_running=_Probe(True), sleeper=lambda _s: None)

        self.assertTrue(result["ok"])
        self.assertEqual("launcher", result["started"])
        self.assertEqual(["launcher"], self.calls)

    def test_launcher_failure_is_reported(self):
        result = hs_restart.launch_hearthstone(
            self.tmp / "missing.exe", opener=self.opener, fallback=lambda: False,
            process_running=_Probe(True), sleeper=lambda _s: None)

        self.assertFalse(result["ok"])
        self.assertIn("战网", result["error"])

    def test_without_exe_and_launcher_it_refuses(self):
        result = hs_restart.launch_hearthstone(
            self.tmp / "missing.exe", opener=self.opener,
            process_running=_Probe(True), sleeper=lambda _s: None)

        self.assertFalse(result["ok"])
        self.assertIn("Hearthstone.exe", result["error"])

    def test_slow_start_returns_ok_with_a_warning(self):
        exe = self.tmp / hs_restart.EXE_NAME
        exe.write_text("", encoding="utf-8")

        result = hs_restart.launch_hearthstone(
            exe, opener=self.opener, process_running=_Probe(False),
            sleeper=lambda _s: None, timeout=0.0)

        self.assertTrue(result["ok"])
        self.assertIn("还没检测到", result["warning"])


if __name__ == "__main__":
    unittest.main()
