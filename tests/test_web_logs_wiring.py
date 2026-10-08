# -*- coding: utf-8 -*-
"""浮窗「日志」行的数据链：扫描缓存、体积配色、打开位置、一键清理、运行日志裁剪。"""

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import log_overlay
import script_logs
import web_ui


def make_temp_dir() -> Path:
    base = Path(os.path.realpath(tempfile.gettempdir()))
    path = base / f"hs_web_logs_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class TempLogsCase(unittest.TestCase):
    """把 web_ui 的日志路径指到临时目录，并清掉扫描缓存。"""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.logs = self.tmp / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.runtime = self.tmp / "ui_log_last.txt"
        self._paths = (web_ui.LOG_DIR, web_ui.RUNTIME_LOG)
        web_ui.LOG_DIR, web_ui.RUNTIME_LOG = self.logs, self.runtime
        self._cache = (web_ui._log_scan_cache["at"], web_ui._log_scan_cache["data"])
        self._clear = (web_ui._log_clear_state["at"],
                       web_ui._log_clear_state["bytes"])
        web_ui._log_scan_cache.update({"at": 0.0, "data": None})
        web_ui._log_clear_state.update({"at": 0.0, "bytes": 0})

    def tearDown(self):
        web_ui.LOG_DIR, web_ui.RUNTIME_LOG = self._paths
        web_ui._log_scan_cache.update({"at": self._cache[0], "data": self._cache[1]})
        web_ui._log_clear_state.update({"at": self._clear[0],
                                        "bytes": self._clear[1]})
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _battle_log(self, name="对战日志_20261008_120000.txt", size=1000):
        path = self.logs / name
        path.write_text("x" * size, encoding="utf-8")
        return path


class LogsRowTests(unittest.TestCase):
    """浮窗「日志」行的配色与文案（纯函数，不建窗口）。"""

    def test_unknown_data_shows_a_dash(self):
        row = log_overlay.logs_row(None)

        self.assertEqual("—", row["value"])
        self.assertEqual(log_overlay.MARKER_UNKNOWN, row["marker"])

    def test_small_logs_are_green(self):
        row = log_overlay.logs_row({"bytes": 40 * 1024 ** 2, "files": 12})

        self.assertEqual("40.0 MB", row["value"])
        self.assertEqual("12 个文件", row["detail"])
        self.assertEqual(log_overlay.GREEN, row["marker"])
        self.assertEqual(log_overlay.GREEN, row["value_color"])

    def test_mid_size_logs_are_warn(self):
        row = log_overlay.logs_row({"bytes": 270 * 1024 ** 2, "files": 194})

        self.assertEqual("270.0 MB", row["value"])
        self.assertEqual(log_overlay.WARN, row["marker"])

    def test_huge_logs_are_danger(self):
        row = log_overlay.logs_row({"bytes": 2 * 1024 ** 3, "files": 900})

        self.assertEqual(log_overlay.DANGER, row["marker"])
        self.assertEqual("2.0 GB", row["value"])

    def test_thresholds_come_from_the_caller(self):
        row = log_overlay.logs_row({"bytes": 10 * 1024 ** 2, "files": 1,
                                    "warn_bytes": 5 * 1024 ** 2,
                                    "danger_bytes": 8 * 1024 ** 2})

        self.assertEqual(log_overlay.DANGER, row["marker"])

    def test_recent_clear_is_flashed_in_the_detail(self):
        now = time.time()
        row = log_overlay.logs_row({"bytes": 0, "files": 0,
                                    "cleared_at": now - 1.0,
                                    "cleared_bytes": 270 * 1024 ** 2},
                                   now=now)

        self.assertEqual("已清理 270.0 MB", row["detail"])

    def test_old_clear_goes_back_to_the_file_count(self):
        now = time.time()
        row = log_overlay.logs_row({"bytes": 0, "files": 0,
                                    "cleared_at": now - 60.0,
                                    "cleared_bytes": 123},
                                   now=now)

        self.assertEqual("0 个文件", row["detail"])

    def test_broken_value_does_not_crash_the_row(self):
        row = log_overlay.logs_row({"bytes": "坏数据", "files": None})

        self.assertEqual("—", row["value"])


class SnapshotTests(TempLogsCase):
    def test_snapshot_counts_battle_and_runtime_logs(self):
        self._battle_log(size=2000)
        self.runtime.write_text("r" * 500, encoding="utf-8")

        data = web_ui._script_logs_snapshot(force=True)

        self.assertEqual(2, data["files"])
        self.assertEqual(2500, data["bytes"])
        self.assertEqual(2000, data["battle_bytes"])
        self.assertEqual(500, data["runtime_bytes"])
        self.assertEqual(web_ui.LOG_WARN_BYTES, data["warn_bytes"])

    def test_snapshot_is_cached_until_ttl_or_force(self):
        self._battle_log(size=10)
        first = web_ui._script_logs_snapshot(force=True)
        self._battle_log("对战日志_20261008_130000.txt", size=20)

        cached = web_ui._script_logs_snapshot()
        forced = web_ui._script_logs_snapshot(force=True)

        self.assertEqual(first["bytes"], cached["bytes"])     # 还是旧结果
        self.assertEqual(30, forced["bytes"])                 # 强制重扫

    def test_invalidate_makes_the_next_read_fresh(self):
        self._battle_log(size=10)
        web_ui._script_logs_snapshot(force=True)
        self._battle_log("对战日志_20261008_130000.txt", size=20)

        web_ui._script_logs_invalidate()

        self.assertEqual(30, web_ui._script_logs_snapshot()["bytes"])

    def test_overlay_callback_survives_a_broken_scanner(self):
        with patch.object(web_ui, "_script_logs_snapshot",
                          side_effect=RuntimeError("磁盘挂了")):
            self.assertIsNone(web_ui._overlay_logs())


class OverlayLogActionsTests(TempLogsCase):
    def test_clear_spawns_a_background_thread_and_returns(self):
        self._battle_log()

        with patch.object(web_ui.threading, "Thread") as thread_cls:
            self.assertTrue(web_ui._overlay_clear_logs())

        thread_cls.assert_called_once()
        self.assertIs(web_ui._clear_script_logs_worker,
                      thread_cls.call_args.kwargs["target"])
        self.assertTrue(thread_cls.call_args.kwargs["daemon"])

    def test_clear_worker_deletes_and_reports(self):
        self._battle_log(size=1000)
        self.runtime.write_text("r" * 1000, encoding="utf-8")
        logged = []

        with patch.object(web_ui, "_log",
                          side_effect=lambda level, msg: logged.append(
                              (level, str(msg)))):
            web_ui._clear_script_logs_worker()

        self.assertFalse(self.runtime.exists())
        self.assertEqual([], list(self.logs.glob("对战日志_*.txt")))
        messages = [msg for _level, msg in logged]
        self.assertTrue(any("已清理脚本日志" in msg for msg in messages), messages)
        self.assertTrue(any("释放 2.0 KB" in msg for msg in messages), messages)
        # 清完缓存也要失效：浮窗下一拍就显示 0
        self.assertIsNone(web_ui._log_scan_cache["data"])

    def test_clear_worker_says_when_there_is_nothing_to_clear(self):
        logged = []

        with patch.object(web_ui, "_log",
                          side_effect=lambda level, msg: logged.append(
                              (level, str(msg)))):
            web_ui._clear_script_logs_worker()

        self.assertEqual([("SYS", "已无可清理的脚本日志。")], logged)

    def test_clear_worker_warns_about_files_it_could_not_delete(self):
        self._battle_log()
        logged = []

        def fake_clear(log_dir=None, runtime_log=None, logger=None):
            return {"ok": False, "removed": 0, "freed_bytes": 0,
                    "failed": ["对战日志_20261008_120000.txt"],
                    "errors": ["PermissionError"], "skipped": []}

        with (patch.object(script_logs, "clear", side_effect=fake_clear),
              patch.object(web_ui, "_log",
                           side_effect=lambda level, msg: logged.append(
                               (level, str(msg))))):
            web_ui._clear_script_logs_worker()

        self.assertTrue(any(level == "WARN" and "删不掉" in msg
                            for level, msg in logged), logged)

    def test_open_logs_reports_the_paths_and_result(self):
        self._battle_log()
        logged = []
        opened = []

        def fake_open(log_dir=None, opener=None, runner=None):
            opened.append(log_dir)
            return {"ok": True, "opened": "file", "target": "X",
                    "error": ""}

        with (patch.object(script_logs, "open_location",
                           side_effect=fake_open),
              patch.object(web_ui, "_log",
                           side_effect=lambda level, msg: logged.append(
                               (level, str(msg))))):
            self.assertTrue(web_ui._overlay_open_logs())

        self.assertEqual([self.logs], opened)
        self.assertTrue(any("已打开" in msg and str(self.logs) in msg
                            for _level, msg in logged), logged)

    def test_open_logs_failure_is_a_warning(self):
        logged = []

        def fake_open(log_dir=None, opener=None, runner=None):
            return {"ok": False, "opened": None, "target": "", "error": "炸了"}

        with (patch.object(script_logs, "open_location", side_effect=fake_open),
              patch.object(web_ui, "_log",
                           side_effect=lambda level, msg: logged.append(
                               (level, str(msg))))):
            self.assertFalse(web_ui._overlay_open_logs())

        self.assertEqual("WARN", logged[0][0])


class OverlayKeyTests(unittest.TestCase):
    def test_log_lines_reach_the_overlay(self):
        self.assertTrue(web_ui._overlay_key(
            "已清理脚本日志：删除 193 个文件，释放 214.2 MB"))
        self.assertTrue(web_ui._overlay_key("已打开最新一份对战日志：C:\\logs"))

    def test_ocr_spam_still_filtered(self):
        self.assertFalse(web_ui._overlay_key("[OCR] 图 273x347 行 0 置信 0.00"))


class LogTrimTests(TempLogsCase):
    def setUp(self):
        super().setUp()
        self._trim = (web_ui.LOG_TRIM_MIN_INTERVAL, list(web_ui._log_trim_at))
        web_ui._log_trim_at[0] = 0.0

    def tearDown(self):
        web_ui.LOG_TRIM_MIN_INTERVAL = self._trim[0]
        web_ui._log_trim_at[:] = self._trim[1]
        super().tearDown()

    def test_big_runtime_log_is_trimmed_atomically(self):
        self.runtime.write_text("旧行\n" * 400_000, encoding="utf-8")
        self.assertGreater(self.runtime.stat().st_size, web_ui.LOG_TRIM_BYTES)

        with patch.object(web_ui, "_overlay_key", return_value=False):
            web_ui._log("SYS", "新日志")

        self.assertLessEqual(self.runtime.stat().st_size, web_ui.LOG_TRIM_BYTES)
        self.assertIn("新日志",
                      self.runtime.read_text(encoding="utf-8", errors="replace"))
        self.assertFalse(self.runtime.with_suffix(".txt.tmp").exists())

    def test_trim_is_throttled_by_interval(self):
        self.runtime.write_text("旧行\n" * 400_000, encoding="utf-8")
        calls = []
        real_replace = os.replace

        def fake_replace(src, dst):
            calls.append((str(src), str(dst)))
            return real_replace(src, dst)

        web_ui.LOG_TRIM_MIN_INTERVAL = 3600.0       # 本进程内只裁一次
        with (patch.object(web_ui.os, "replace", fake_replace),
              patch.object(web_ui, "_overlay_key", return_value=False)):
            for index in range(5):
                web_ui._log("SYS", f"第 {index} 行")

        self.assertEqual(1, len(calls))

    def test_small_runtime_log_is_left_alone(self):
        with (patch.object(web_ui, "_overlay_key", return_value=False),
              patch.object(web_ui.os, "replace") as replace):
            web_ui._log("SYS", "小日志")

        replace.assert_not_called()

    def test_trim_helper_returns_false_for_a_missing_file(self):
        self.assertFalse(web_ui._trim_runtime_log(self.tmp / "nope.txt",
                                                  time.time()))


class OverlayWiringTests(unittest.TestCase):
    """浮窗侧：日志行 + 两个按钮 + 启动参数都接上了。"""

    def test_start_accepts_the_log_callbacks(self):
        import inspect
        params = inspect.signature(log_overlay.start).parameters
        for name in ("logs_callback", "on_open_logs", "on_clear_logs"):
            self.assertIn(name, params)

    def test_buttons_exist_and_do_not_share_slots(self):
        layout = log_overlay.BTN_LAYOUT
        self.assertEqual((3, 0), layout["open_logs"])
        self.assertEqual((3, 1), layout["clear_logs"])
        self.assertNotIn("open_logs", log_overlay.BTN_SPAN)
        self.assertNotIn("clear_logs", log_overlay.BTN_SPAN)
        slots = list(layout.values())
        self.assertEqual(len(slots), len(set(slots)))

    def test_exit_is_still_the_last_spanning_row(self):
        layout = log_overlay.BTN_LAYOUT
        row, _column = layout["exit"]
        rows = [r for key, (r, _c) in layout.items() if key != "exit"]
        self.assertEqual(max(rows) + 1, row)
        self.assertEqual(2, log_overlay.BTN_SPAN["exit"])

    def test_run_uses_the_log_row_and_confirms_before_clearing(self):
        import inspect
        source = inspect.getsource(log_overlay._run)

        self.assertIn("_status_row(\"日志\")", source)
        self.assertIn("logs_row(lg_info)", source)
        self.assertIn('_make_btn(btn_frame, "打开日志", NEUTRAL', source)
        self.assertIn('_make_btn(btn_frame, "清理日志", WARN', source)
        self.assertIn("_confirm_clear_logs(root, info)", source)
        self.assertIn("已无可清理的脚本日志。", source)
        self.assertIn("_ON_OPEN_LOGS", source)
        self.assertIn("_ON_CLEAR_LOGS", source)

    def test_bind_overlay_passes_the_log_callbacks(self):
        bound = {}
        overlay = SimpleNamespace(start=lambda **kwargs: bound.update(kwargs),
                                  is_running=lambda: False)

        with patch.object(web_ui, "log_overlay", overlay):
            web_ui._bind_overlay()

        self.assertIs(bound["logs_callback"], web_ui._overlay_logs)
        self.assertIs(bound["on_open_logs"], web_ui._overlay_open_logs)
        self.assertIs(bound["on_clear_logs"], web_ui._overlay_clear_logs)

    def test_confirm_dialog_text_mentions_both_files(self):
        detail = log_overlay.confirm_clear_detail(193, "214.2 MB", "56.0 MB",
                                                 "270.2 MB")

        self.assertIn("193 个对战日志", detail)
        self.assertIn("214.2 MB", detail)
        self.assertIn("ui_log_last.txt", detail)
        self.assertIn("270.2 MB", detail)


if __name__ == "__main__":
    unittest.main()
