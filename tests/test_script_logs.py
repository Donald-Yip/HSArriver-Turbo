# -*- coding: utf-8 -*-
"""脚本日志的纯函数：统计体积 / 打开位置 / 一键清理。

不碰真日志：全部在临时目录上跑，而且用"自己 mkdir"的临时目录
（tempfile.TemporaryDirectory 会把目录设成 0700，在受限环境里反而写不进去）。
"""

import os
import tempfile
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import script_logs


def make_temp_dir() -> Path:
    # realpath：沙箱的 TEMP 是 8.3 短名（DONALD~1），script_logs 会 resolve()
    # 成长名，两边都先归一化，断言才不会因为写法不同而失败。
    base = Path(os.path.realpath(tempfile.gettempdir()))
    path = base / f"hs_script_logs_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write(path: Path, text: str, mtime: float = None) -> Path:
    """写文件；给了 mtime 就用 os.utime 钉死修改时间（"最新一个"要可复现）。"""
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class FormatSizeTests(unittest.TestCase):
    def test_human_readable_sizes(self):
        cases = (
            (0, "0 B"),
            (None, "0 B"),
            (999, "999 B"),
            (1024, "1.0 KB"),
            (1536, "1.5 KB"),
            (1024**2, "1.0 MB"),
            (int(214.2 * 1024**2), "214.2 MB"),
            (int(1.2 * 1024**3), "1.2 GB"),
            (-5, "0 B"),
            ("不是数字", "—"),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(expected, script_logs.format_size(value))


class NamingTests(unittest.TestCase):
    def test_new_path_and_battle_log_name_matching(self):
        path = script_logs.new_battle_log_path(datetime(2026, 10, 8, 13, 5, 9))
        self.assertEqual("对战日志_20261008_130509.txt", path.name)
        self.assertEqual(script_logs.LOG_DIR, path.parent)
        self.assertTrue(script_logs.is_battle_log(path.name))

        self.assertTrue(script_logs.is_battle_log("对战日志_20261008.txt"))
        self.assertFalse(script_logs.is_battle_log("other.txt"))
        self.assertFalse(script_logs.is_battle_log("对战日志_20261008.log"))
        self.assertFalse(script_logs.is_battle_log(""))


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.logs = self.tmp / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.runtime = self.tmp / "ui_log_last.txt"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_empty_directory(self):
        result = script_logs.scan(self.logs, self.runtime)

        self.assertEqual(
            {
                "files": 0,
                "bytes": 0,
                "battle_files": 0,
                "battle_bytes": 0,
                "feedback_files": 0,
                "feedback_bytes": 0,
                "runtime_bytes": 0,
                "newest": None,
            },
            result,
        )

    def test_counts_only_battle_logs_plus_runtime(self):
        first = _write(
            self.logs / "对战日志_20261008_120000.txt", "a" * 10, 1_700_000_000
        )
        _write(self.logs / "对战日志_20261008_130000.txt", "b" * 20, 1_700_000_100)
        _write(self.logs / "notes.txt", "c" * 5)  # 不匹配，不计
        (self.logs / "subdir").mkdir()
        _write(self.runtime, "r" * 7)

        result = script_logs.scan(self.logs, self.runtime)

        self.assertEqual(2, result["battle_files"])
        self.assertEqual(30, result["battle_bytes"])
        self.assertEqual(7, result["runtime_bytes"])
        self.assertEqual(3, result["files"])  # 2 份对战日志 + 运行日志
        self.assertEqual(37, result["bytes"])
        self.assertNotEqual(first, result["newest"])
        self.assertEqual("对战日志_20261008_130000.txt", Path(result["newest"]).name)

    def test_missing_directory_and_stat_failures_are_not_errors(self):
        result = script_logs.scan(self.tmp / "nope", self.runtime)
        self.assertEqual(0, result["files"])

        class _BadEntry:
            name = "对战日志_坏文件.txt"

            def is_file(self):
                return True

            def stat(self):
                raise OSError("读不到")

        with patch.object(script_logs.os, "scandir", return_value=[_BadEntry()]):
            result = script_logs.scan(self.logs, self.runtime)

        self.assertEqual(0, result["battle_files"])

    def test_same_second_logs_fall_back_to_the_file_name(self):
        """同一秒里连着点两次「保存日志」：mtime 一样，得按文件名取最新。"""
        same = 1_700_000_000
        _write(self.logs / "对战日志_20261008_120000.txt", "a" * 10, same)
        _write(self.logs / "对战日志_20261008_120001.txt", "b" * 10, same)

        result = script_logs.scan(self.logs, self.runtime)

        self.assertEqual("对战日志_20261008_120001.txt", Path(result["newest"]).name)


class ClearTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.logs = self.tmp / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.runtime = self.tmp / "ui_log_last.txt"

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_deletes_battle_logs_and_runtime_only(self):
        (self.logs / "对战日志_20261008_120000.txt").write_text(
            "a" * 10, encoding="utf-8"
        )
        (self.logs / "对战日志_20261008_130000.txt").write_text(
            "b" * 20, encoding="utf-8"
        )
        keep = self.logs / "notes.txt"
        keep.write_text("c" * 5, encoding="utf-8")
        self.runtime.write_text("r" * 7, encoding="utf-8")
        logged = []

        result = script_logs.clear(self.logs, self.runtime, logger=logged.append)

        self.assertTrue(result["ok"])
        self.assertEqual(3, result["removed"])  # 2 份对战日志 + 运行日志
        self.assertEqual(37, result["freed_bytes"])
        self.assertFalse(self.runtime.exists())
        self.assertEqual([], list(self.logs.glob("对战日志_*.txt")))
        self.assertTrue(keep.exists())  # 不匹配的一个不动
        self.assertIn("notes.txt", result["skipped"])
        # 单份对战日志不逐条播报（一次可能删几百份），只留运行日志那一句。
        self.assertFalse(any("对战日志_" in text for text in logged), logged)

        # 再清一次是空跑。
        empty = script_logs.clear(self.logs, self.runtime)
        self.assertTrue(empty["ok"])
        self.assertEqual(0, empty["removed"])
        self.assertEqual(0, empty["freed_bytes"])

    def test_locked_file_is_reported_but_others_are_deleted(self):
        good = self.logs / "对战日志_20261008_120000.txt"
        good.write_text("a" * 10, encoding="utf-8")
        bad = self.logs / "对战日志_20261008_130000.txt"
        bad.write_text("b" * 20, encoding="utf-8")
        real_unlink = Path.unlink

        def fake_unlink(self, *args, **kwargs):
            if self.name == bad.name:
                raise PermissionError("正被打开")
            return real_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", fake_unlink):
            result = script_logs.clear(self.logs, self.runtime)

        self.assertFalse(result["ok"])
        self.assertEqual(1, result["removed"])
        self.assertEqual([bad.name], result["failed"])
        self.assertTrue(any("PermissionError" in err for err in result["errors"]))
        self.assertFalse(good.exists())

    def test_runtime_log_falls_back_to_truncate(self):
        self.runtime.write_text("r" * 100, encoding="utf-8")
        runtime_name = self.runtime.name
        real_unlink = Path.unlink

        def fake_unlink(self, *args, **kwargs):
            if self.name == runtime_name:
                raise PermissionError("正被打开")
            return real_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", fake_unlink):
            result = script_logs.clear(self.logs, self.runtime)

        self.assertTrue(result["ok"])  # 截断成功也算清掉
        self.assertEqual(1, result["removed"])
        self.assertEqual(100, result["freed_bytes"])
        self.assertEqual("", self.runtime.read_text(encoding="utf-8"))


class OpenLocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.logs = self.tmp / "logs"
        self.runtime = self.tmp / "ui_log_last.txt"
        self.runs = []
        self.opened = []

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _runner(self, argv):
        self.runs.append(list(argv))

    def _opener(self, path):
        self.opened.append(str(path))

    def test_selects_the_newest_battle_log(self):
        self.logs.mkdir(parents=True, exist_ok=True)
        _write(self.logs / "对战日志_20261008_120000.txt", "a", 1_700_000_000)
        _write(self.logs / "对战日志_20261008_130000.txt", "b", 1_700_000_100)

        result = script_logs.open_location(
            self.logs, opener=self._opener, runner=self._runner
        )

        self.assertTrue(result["ok"])
        self.assertEqual("file", result["opened"])
        self.assertEqual(1, len(self.runs))
        self.assertEqual("explorer", self.runs[0][0])
        self.assertIn("20261008_130000", self.runs[0][1])
        self.assertEqual([], self.opened)

    def test_opens_the_folder_when_there_is_no_battle_log_or_runner_fails(self):
        result = script_logs.open_location(
            self.logs, opener=self._opener, runner=self._runner
        )

        self.assertTrue(result["ok"])
        self.assertEqual("folder", result["opened"])
        self.assertEqual([str(self.logs)], self.opened)
        self.assertEqual([], self.runs)
        self.assertTrue(self.logs.is_dir())  # 目录会被建出来

        def boom(_argv):
            raise OSError("explorer 不给开")

        self.opened.clear()
        result = script_logs.open_location(self.logs, opener=self._opener,
                                           runner=boom)

        self.assertTrue(result["ok"])  # 退回打开目录
        self.assertEqual([str(self.logs)], self.opened)


class IsFeedbackTests(unittest.TestCase):
    def test_recognizes_work_dir_and_new_zip_names(self):
        # 工作目录 / 旧包名（前缀）
        self.assertTrue(script_logs.is_feedback("反馈_20261018_120000"))
        self.assertTrue(script_logs.is_feedback("反馈_20261018_120000.zip"))
        # 按天合并的自动出错存档目录
        self.assertTrue(script_logs.is_feedback("出错记录_20261018"))
        # 新包名：<紧凑日期>-<盒子推荐>-错误反馈.zip
        self.assertTrue(script_logs.is_feedback("20261018-打出1号位随从-错误反馈.zip"))
        # 别误伤
        self.assertFalse(script_logs.is_feedback("对战日志_20261018_120000.txt"))
        self.assertFalse(script_logs.is_feedback("my_错误反馈.zip.bak"))
        self.assertFalse(script_logs.is_feedback(""))


if __name__ == "__main__":
    unittest.main()
