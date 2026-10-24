# -*- coding: utf-8 -*-
"""发版辅助工具的纯函数测试（不写盘、不跑 git）。"""

import tempfile
import unittest
from pathlib import Path

from tools import release


class ReleaseDateTests(unittest.TestCase):
    def test_accepts_the_formats_we_type(self):
        for raw in ("2026.10.24", "2026-10-24", "2026-1024"):
            with self.subTest(raw=raw):
                self.assertEqual("2026.10.24", release.parse_release_date(raw))

    def test_rejects_bad_dates(self):
        for raw in ("", "2026.13.40", "2026.02.30", "v2026.10.24", "abc"):
            with self.subTest(raw=raw):
                with self.assertRaises(SystemExit):
                    release.parse_release_date(raw)


class VersionFileTests(unittest.TestCase):
    SAMPLE = (
        '"""doc"""\n\nNAME = "HSLegendArriver"\n'
        'VERSION = "2026.10.18"\n\nROOT = 1\n'
    )

    def test_read_and_replace_keep_one_source(self):
        self.assertEqual("2026.10.18", release.read_version(self.SAMPLE))
        replaced = release.replace_version(self.SAMPLE, "2026.10.24")
        self.assertIn('VERSION = "2026.10.24"', replaced)
        self.assertEqual(1, replaced.count('VERSION = "'))

    def test_rejects_files_without_exactly_one_version(self):
        with self.assertRaises(RuntimeError):
            release.replace_version("NAME = 'x'\n", "2026.10.24")
        with self.assertRaises(RuntimeError):
            release.replace_version(
                'VERSION = "2026.10.01"\nVERSION = "2026.10.02"\n',
                "2026.10.24")


class ChangelogTests(unittest.TestCase):
    SAMPLE = (
        "# 更新日志\n\n| 日期 | 提交 | 一句话 |\n| --- | --- | --- |\n"
        "| 2026-10-18 | 本次 | 旧记录 |\n"
    )

    def test_new_row_goes_right_below_the_header(self):
        row = release.changelog_row("2026.10.24", "新功能  说明")
        updated = release.insert_changelog_row(self.SAMPLE, row)
        lines = updated.splitlines()
        self.assertEqual("| 2026-10-24 | 本次 | 新功能 说明 |", lines[4])
        self.assertEqual("| 2026-10-18 | 本次 | 旧记录 |", lines[5])
        self.assertEqual("2026.10.24", release.changelog_head_date(updated))

    def test_row_requires_a_summary(self):
        with self.assertRaises(ValueError):
            release.changelog_row("2026.10.24", "   ")

    def test_same_date_is_refused(self):
        row = release.changelog_row("2026.10.18", "重复")
        with self.assertRaises(RuntimeError):
            release.insert_changelog_row(self.SAMPLE, row)

    def test_broken_table_is_refused(self):
        with self.assertRaises(RuntimeError):
            release.insert_changelog_row("# 空文件\n", "| 2026-10-24 | x | y |")


class WriteLikeTests(unittest.TestCase):
    """写回时保留原文件换行符：CHANGELOG 是 LF，version.py 是 CRLF。"""

    def test_lf_file_stays_lf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.md"
            path.write_bytes(b"a\nb\n")
            release._write_like(path, "a\nb\nc\n", path.read_bytes())
            self.assertEqual(b"a\nb\nc\n", path.read_bytes())

    def test_crlf_file_stays_crlf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.py"
            path.write_bytes(b"a\r\nb\r\n")
            release._write_like(path, "a\nb\nc\n", path.read_bytes())
            self.assertEqual(b"a\r\nb\r\nc\r\n", path.read_bytes())


if __name__ == "__main__":
    unittest.main()
