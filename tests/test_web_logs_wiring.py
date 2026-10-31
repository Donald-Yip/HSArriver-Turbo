# -*- coding: utf-8 -*-
"""浮窗「日志」行的数据链：扫描缓存、体积配色、打开位置、一键清理、运行日志裁剪。"""

import os
import shutil
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import log_overlay
import script_logs
import web_ui
from tests.helpers.fake_tk import overlay_harness


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
        self._clear = (web_ui._log_clear_state["at"], web_ui._log_clear_state["bytes"])
        web_ui._log_scan_cache.update({"at": 0.0, "data": None})
        web_ui._log_clear_state.update({"at": 0.0, "bytes": 0})

    def tearDown(self):
        web_ui.LOG_DIR, web_ui.RUNTIME_LOG = self._paths
        web_ui._log_scan_cache.update({"at": self._cache[0], "data": self._cache[1]})
        web_ui._log_clear_state.update({"at": self._clear[0], "bytes": self._clear[1]})
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _battle_log(self, name="对战日志_20261008_120000.txt", size=1000):
        path = self.logs / name
        path.write_text("x" * size, encoding="utf-8")
        return path


class LogsRowTests(unittest.TestCase):
    """浮窗「日志」行的配色与文案（纯函数，不建窗口）。"""

    def test_unknown_or_broken_data_shows_a_dash(self):
        for info in (None, {"bytes": "坏数据", "files": None}):
            with self.subTest(info=info):
                self.assertEqual("—", log_overlay.logs_row(info)["value"])
        self.assertEqual(
            log_overlay.MARKER_UNKNOWN, log_overlay.logs_row(None)["marker"])

    def test_size_thresholds_pick_the_marker(self):
        cases = (
            ("small", {"bytes": 40 * 1024**2, "files": 12},
             "40.0 MB", "12 个文件", log_overlay.GREEN),
            ("mid", {"bytes": 270 * 1024**2, "files": 194},
             "270.0 MB", None, log_overlay.WARN),
            ("huge", {"bytes": 2 * 1024**3, "files": 900},
             "2.0 GB", None, log_overlay.DANGER),
            # 阈值由调用方传入时以调用方为准
            ("caller_thresholds",
             {"bytes": 10 * 1024**2, "files": 1,
              "warn_bytes": 5 * 1024**2, "danger_bytes": 8 * 1024**2},
             None, None, log_overlay.DANGER),
        )
        for name, info, value, detail, marker in cases:
            with self.subTest(size=name):
                row = log_overlay.logs_row(info)
                if value is not None:
                    self.assertEqual(value, row["value"])
                if detail is not None:
                    self.assertEqual(detail, row["detail"])
                self.assertEqual(marker, row["marker"])
                if name == "small":
                    self.assertEqual(log_overlay.GREEN, row["value_color"])

    def test_recent_clear_flashes_then_falls_back_to_file_count(self):
        now = time.time()
        cases = (
            ("recent", {"bytes": 0, "files": 0, "cleared_at": now - 1.0,
                        "cleared_bytes": 270 * 1024**2}, "已清理 270.0 MB"),
            ("old", {"bytes": 0, "files": 0, "cleared_at": now - 60.0,
                     "cleared_bytes": 123}, "0 个文件"),
        )
        for name, info, detail in cases:
            with self.subTest(cleared=name):
                self.assertEqual(detail, log_overlay.logs_row(info, now=now)["detail"])


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

    def test_snapshot_cache_honours_ttl_force_and_invalidation(self):
        self._battle_log(size=10)
        first = web_ui._script_logs_snapshot(force=True)
        self._battle_log("对战日志_20261008_130000.txt", size=20)

        cached = web_ui._script_logs_snapshot()
        forced = web_ui._script_logs_snapshot(force=True)

        self.assertEqual(first["bytes"], cached["bytes"])  # 还是旧结果
        self.assertEqual(30, forced["bytes"])  # 强制重扫

        self._battle_log("对战日志_20261008_140000.txt", size=5)
        web_ui._script_logs_invalidate()
        self.assertEqual(35, web_ui._script_logs_snapshot()["bytes"])

    def test_overlay_callback_survives_a_broken_scanner(self):
        with patch.object(
            web_ui, "_script_logs_snapshot", side_effect=RuntimeError("磁盘挂了")
        ):
            self.assertIsNone(web_ui._overlay_logs())


class OverlayLogActionsTests(TempLogsCase):
    def test_clear_spawns_a_background_thread_and_returns(self):
        self._battle_log()

        with patch.object(web_ui.threading, "Thread") as thread_cls:
            self.assertTrue(web_ui._overlay_clear_logs())

        thread_cls.assert_called_once()
        self.assertIs(
            web_ui._clear_script_logs_worker, thread_cls.call_args.kwargs["target"]
        )
        self.assertTrue(thread_cls.call_args.kwargs["daemon"])

    def test_clear_worker_deletes_and_reports(self):
        self._battle_log(size=1000)
        self.runtime.write_text("r" * 1000, encoding="utf-8")
        logged = []

        with patch.object(
            web_ui,
            "_log",
            side_effect=lambda level, msg: logged.append((level, str(msg))),
        ):
            web_ui._clear_script_logs_worker()

        self.assertFalse(self.runtime.exists())
        self.assertEqual([], list(self.logs.glob("对战日志_*.txt")))
        messages = [msg for _level, msg in logged]
        self.assertTrue(any("已清理脚本日志" in msg for msg in messages), messages)
        self.assertTrue(any("释放 2.0 KB" in msg for msg in messages), messages)
        # 清完缓存也要失效：浮窗下一拍就显示 0
        self.assertIsNone(web_ui._log_scan_cache["data"])

    def test_clear_worker_reports_nothing_to_do_or_undeletable_files(self):
        logged = []

        def capture(level, msg):
            logged.append((level, str(msg)))

        with patch.object(web_ui, "_log", side_effect=capture):
            web_ui._clear_script_logs_worker()
        self.assertEqual([("SYS", "已无可清理的脚本日志。")], logged)

        self._battle_log()
        logged.clear()

        def fake_clear(log_dir=None, runtime_log=None, logger=None):
            return {
                "ok": False,
                "removed": 0,
                "freed_bytes": 0,
                "failed": ["对战日志_20261008_120000.txt"],
                "errors": ["PermissionError"],
                "skipped": [],
            }

        with (
            patch.object(script_logs, "clear", side_effect=fake_clear),
            patch.object(web_ui, "_log", side_effect=capture),
        ):
            web_ui._clear_script_logs_worker()

        self.assertTrue(
            any(level == "WARN" and "删不掉" in msg for level, msg in logged), logged
        )

    def test_open_logs_reports_the_paths_and_result(self):
        self._battle_log()
        logged = []
        opened = []

        def fake_open(log_dir=None, opener=None, runner=None):
            opened.append(log_dir)
            return {"ok": True, "opened": "file", "target": "X", "error": ""}

        with (
            patch.object(script_logs, "open_location", side_effect=fake_open),
            patch.object(
                web_ui,
                "_log",
                side_effect=lambda level, msg: logged.append((level, str(msg))),
            ),
        ):
            self.assertTrue(web_ui._overlay_open_logs())

        self.assertEqual([self.logs], opened)
        self.assertTrue(
            any("已打开" in msg and str(self.logs) in msg for _level, msg in logged),
            logged,
        )

        # 打不开时降级成 WARN，而不是静默成功
        logged.clear()

        def fake_fail(log_dir=None, opener=None, runner=None):
            return {"ok": False, "opened": None, "target": "", "error": "炸了"}

        with (
            patch.object(script_logs, "open_location", side_effect=fake_fail),
            patch.object(
                web_ui,
                "_log",
                side_effect=lambda level, msg: logged.append((level, str(msg))),
            ),
        ):
            self.assertFalse(web_ui._overlay_open_logs())
        self.assertEqual("WARN", logged[0][0])


class OverlayKeyTests(unittest.TestCase):
    def test_log_lines_reach_the_overlay_and_ocr_spam_does_not(self):
        for line in (
            "已清理脚本日志：删除 193 个文件，释放 214.2 MB",
            "已打开最新一份对战日志：C:\\logs",
        ):
            with self.subTest(line=line):
                self.assertTrue(web_ui._overlay_key(line))
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
        self.assertIn(
            "新日志", self.runtime.read_text(encoding="utf-8", errors="replace")
        )
        self.assertFalse(self.runtime.with_suffix(".txt.tmp").exists())

    def test_trim_is_throttled_by_interval(self):
        self.runtime.write_text("旧行\n" * 400_000, encoding="utf-8")
        calls = []
        real_replace = os.replace

        def fake_replace(src, dst):
            calls.append((str(src), str(dst)))
            return real_replace(src, dst)

        web_ui.LOG_TRIM_MIN_INTERVAL = 3600.0  # 本进程内只裁一次
        with (
            patch.object(web_ui.os, "replace", fake_replace),
            patch.object(web_ui, "_overlay_key", return_value=False),
        ):
            for index in range(5):
                web_ui._log("SYS", f"第 {index} 行")

        self.assertEqual(1, len(calls))

    def test_small_runtime_log_is_left_alone(self):
        original = "旧日志\n旧日志\n"
        self.runtime.write_text(original, encoding="utf-8")

        with patch.object(web_ui, "_overlay_key", return_value=False):
            web_ui._log("SYS", "小日志")

        written = self.runtime.read_text(encoding="utf-8")
        self.assertTrue(written.startswith(original), written)
        self.assertIn("[SYS] 小日志", written)
        self.assertLess(len(written), web_ui.LOG_TRIM_BYTES)
        self.assertFalse(self.runtime.with_suffix(".txt.tmp").exists())
        # 文件不存在时裁剪助手如实返回 False（不抛异常）
        self.assertFalse(
            web_ui._trim_runtime_log(self.tmp / "nope.txt", time.time()))


class OverlayWiringTests(unittest.TestCase):
    """浮窗侧：日志行 + 两个按钮 + 启动参数都接上了。"""

    def test_start_accepts_the_log_callbacks(self):
        import inspect

        params = inspect.signature(log_overlay.start).parameters
        for name in ("logs_callback", "on_open_logs", "on_clear_logs"):
            self.assertIn(name, params)

    def test_layout_pins_the_log_buttons_and_the_self_check_span(self):
        layout = log_overlay.BTN_LAYOUT
        self.assertEqual((3, 0), layout["open_logs"])
        self.assertEqual((3, 1), layout["clear_logs"])
        self.assertNotIn("open_logs", log_overlay.BTN_SPAN)
        self.assertNotIn("clear_logs", log_overlay.BTN_SPAN)
        slots = list(layout.values())
        self.assertEqual(len(slots), len(set(slots)))

        # 「装好没反应？」是最后一行（横跨整行）；退出脚本仍在它上面。
        row, _column = layout["self_check"]
        rows = [r for key, (r, _c) in layout.items() if key != "self_check"]
        self.assertEqual(max(rows) + 1, row)
        self.assertEqual(2, log_overlay.BTN_SPAN["self_check"])
        self.assertEqual(2, log_overlay.BTN_SPAN["exit"])
        self.assertEqual(6, layout["exit"][0])

    def test_run_renders_log_row_and_clears_only_after_confirmation(self):
        opened = []
        cleared = []
        info = {"files": 2, "bytes": 2048}
        expected_value = log_overlay.logs_row(info)["value"]

        with patch.object(
            log_overlay, "_confirm_clear_logs", return_value=False
        ) as confirm:
            with overlay_harness(
                _IS_RUNNING=lambda: False,
                _LOGS=lambda: info,
                _ON_OPEN_LOGS=lambda: opened.append(1),
                _ON_CLEAR_LOGS=lambda: cleared.append(1),
            ) as ui:
                self.assertTrue(ui.label("日志").packed)
                self.assertTrue(ui.label(expected_value).packed)

                ui.button("打开日志").invoke()
                ui.button("清理日志").invoke()

                self.assertEqual([1], opened)
                self.assertEqual([], cleared)
                confirm.assert_called_once()

                confirm.return_value = True
                ui.button("清理日志").invoke()

        self.assertEqual([1], cleared)

    def test_bind_overlay_passes_the_log_callbacks(self):
        bound = {}
        overlay = SimpleNamespace(
            start=lambda **kwargs: bound.update(kwargs), is_running=lambda: False
        )

        with patch.object(web_ui, "log_overlay", overlay):
            web_ui._bind_overlay()

        self.assertIs(bound["logs_callback"], web_ui._overlay_logs)
        self.assertIs(bound["on_open_logs"], web_ui._overlay_open_logs)
        self.assertIs(bound["on_clear_logs"], web_ui._overlay_clear_logs)

    def test_confirm_dialog_spells_out_what_gets_deleted(self):
        detail = log_overlay.confirm_clear_detail(
            193, "214.2 MB", "56.0 MB", "270.2 MB"
        )
        for token in ("193 个对战日志", "214.2 MB", "ui_log_last.txt", "270.2 MB"):
            self.assertIn(token, detail)
        self.assertNotIn("出错存档", detail)

        # 有出错存档时，必须把「会删掉未导出的错误截图/日志」的危害写清楚。
        with_archives = log_overlay.confirm_clear_detail(
            10,
            "10.0 MB",
            "1.0 MB",
            "29.0 MB",
            feedback_count=12,
            feedback_size="18.0 MB",
        )
        for token in ("出错存档", "12 份", "18.0 MB", "无法恢复", "建议先"):
            self.assertIn(token, with_archives)


class BattleFeedbackContextTests(unittest.TestCase):
    """日志行 → 反馈上下文（错误.json 的 instruction / raw）。"""

    def setUp(self):
        self._saved_ctx = dict(web_ui._battle_ctx)
        web_ui._battle_ctx.clear()

    def tearDown(self):
        web_ui._battle_ctx.clear()
        web_ui._battle_ctx.update(self._saved_ctx)

    def test_repeated_raw_lines_enter_the_context_once(self):
        # 重试期间同一段 OCR 原文会被反复打出来：错误.json 的 raw 只留一份，
        # 几十次重试不该把报告撑爆（2026-10-27 反馈：raw 一条都没有，反过来了）。
        events = []
        with (
            patch.object(web_ui.battle_record, "record"),
            patch.object(web_ui.feedback, "mark_success"),
            patch.object(
                web_ui.feedback, "note_event",
                side_effect=lambda info, ctx: events.append((info, dict(ctx))),
            ),
        ):
            web_ui._record_battle("INFO", "[推荐] 打出1号位法术")
            for _ in range(3):
                web_ui._record_battle("INFO", "[推荐] 原文：目标是已方1号位")
            web_ui._record_battle(
                "SYS", "当前推荐暂不可执行，继续重试：unsupported_spell_target")

        self.assertEqual(1, len(events))
        info, ctx = events[0]
        self.assertEqual("retry", info["kind"])
        self.assertEqual("打出1号位法术", ctx["instruction"])
        self.assertEqual(["目标是已方1号位"], ctx["raw"])


if __name__ == "__main__":
    unittest.main()
