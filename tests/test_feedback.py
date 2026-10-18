# -*- coding: utf-8 -*-
"""出错自动截图 + 反馈包：阈值 / 节流 / 上限 / 报告 / 导出。"""

import json
import os
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

import battle_record
import feedback


def retry_info(reason="recommendation_changed"):
    return {"kind": "retry", "reason": reason}


def error_info(text="boom"):
    return {"kind": "error", "text": text}


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.gettempdir())) / (
            f"hs_feedback_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        )
        self.tmp.mkdir(parents=True, exist_ok=True)
        self._dir_patch = patch.object(feedback, "LOG_DIR", self.tmp)
        self._dir_patch.start()
        self.calls = []

        def provider(dest, name):
            path = Path(dest) / f"{name}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"png")
            self.calls.append(str(path))
            return str(path)

        feedback.set_capture_provider(provider)
        feedback.set_async(False)
        feedback.reset(session_stamp="20260101_010101")

    def tearDown(self):
        self._dir_patch.stop()
        feedback.set_capture_provider(None)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _settings(self, **over):
        cfg = {
            "enabled": True,
            "retry_threshold": 3,
            "capture_on_error": True,
            "throttle_seconds": 0.0,
            "max_shots_per_run": 30,
            "auto_export_on_stop": False,
        }
        cfg.update(over)
        return patch.object(feedback, "_settings", lambda: cfg)

    def test_retry_below_threshold_records_nothing(self):
        with self._settings():
            self.assertIsNone(feedback.note_event(retry_info(), now=1.0))
            self.assertIsNone(feedback.note_event(retry_info(), now=2.0))
        self.assertEqual([], feedback.incidents())
        self.assertEqual([], self.calls)

    def test_retry_at_threshold_records_once_and_captures(self):
        with self._settings():
            feedback.note_event(retry_info(), now=1.0)
            feedback.note_event(retry_info(), now=2.0)
            hit = feedback.note_event(retry_info(), now=3.0)
            # 继续重试不再重复记账。
            self.assertIsNone(feedback.note_event(retry_info(), now=4.0))
        self.assertIsNotNone(hit)
        self.assertEqual(3, hit["retries"])
        self.assertEqual(1, len(feedback.incidents()))
        self.assertEqual("retry", feedback.incidents()[0]["type"])
        self.assertEqual(1, len(self.calls))
        self.assertEqual(1, len(feedback.incidents()[0]["screenshots"]))

    def test_success_resets_streak(self):
        with self._settings():
            feedback.note_event(retry_info(), now=1.0)
            feedback.note_event(retry_info(), now=2.0)
            feedback.mark_success()
            self.assertIsNone(feedback.note_event(retry_info(), now=3.0))
        self.assertEqual([], feedback.incidents())

    def test_error_records_and_captures_immediately(self):
        with self._settings():
            hit = feedback.note_event(error_info("invalid_entity"), now=1.0)
        self.assertIsNotNone(hit)
        self.assertEqual("error", hit["type"])
        self.assertIn("invalid_entity", hit["detail"])
        self.assertEqual(1, len(self.calls))

    def test_error_not_recorded_when_disabled(self):
        with self._settings(enabled=False):
            self.assertIsNone(feedback.note_event(error_info(), now=1.0))
        self.assertEqual([], feedback.incidents())

    def test_throttle_skips_second_retry_capture(self):
        with self._settings(throttle_seconds=10.0, retry_threshold=1):
            feedback.note_event(retry_info("a"), now=1_700_000_000.0)
            feedback.note_event(retry_info("b"), now=1_700_000_001.0)
        # 两条都记账，但只有第一条截了图（第二条在节流窗口内被挡）。
        self.assertEqual(2, len(feedback.incidents()))
        self.assertEqual(1, len(self.calls))

    def test_error_bypasses_throttle(self):
        # 出错一定要留图：报错不受 10s 节流限制。
        with self._settings(throttle_seconds=10.0):
            feedback.note_event(error_info("a"), now=1_700_000_000.0)
            feedback.note_event(error_info("b"), now=1_700_000_001.0)
        self.assertEqual(2, len(self.calls))

    def test_max_shots_cap(self):
        with self._settings(max_shots_per_run=1, throttle_seconds=0.0):
            feedback.note_event(error_info("a"), now=1.0)
            feedback.note_event(error_info("b"), now=2.0)
        self.assertEqual(1, len(self.calls))

    def test_context_is_carried_into_incident(self):
        ctx = {
            "turn": 4,
            "phase": "对局",
            "instruction": "打出1号位随从",
            "raw": ["原文"],
        }
        with self._settings(retry_threshold=1):
            hit = feedback.note_event(retry_info(), ctx=ctx, now=1.0)
        self.assertEqual(4, hit["turn"])
        self.assertEqual("对局", hit["phase"])
        self.assertEqual("打出1号位随从", hit["instruction"])


class ReportTests(unittest.TestCase):
    def test_report_text_lists_incidents(self):
        items = [
            {
                "index": 1,
                "time": "2026-10-18 12:00:00",
                "turn": 2,
                "phase": "对局",
                "type": "retry",
                "code": "recommendation_changed",
                "retries": 4,
                "instruction": "打出1号位随从",
                "raw": ["原文"],
                "detail": "",
                "screenshots": ["shots/a.png"],
            },
            {
                "index": 2,
                "time": "2026-10-18 12:01:00",
                "turn": 3,
                "phase": "对局",
                "type": "error",
                "code": "",
                "retries": 0,
                "instruction": "",
                "raw": [],
                "detail": "invalid_entity",
                "screenshots": [],
            },
        ]
        text = feedback.build_report_text(
            items, {"date": "x", "session": "s", "app": "app"}
        )
        self.assertIn("异常 1 · 重试 ×4", text)
        self.assertIn("recommendation_changed", text)
        self.assertIn("异常 2 · 报错", text)
        self.assertIn("invalid_entity", text)
        self.assertIn("shots/a.png", text)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.gettempdir())) / (
            f"hs_feedback_export_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        )
        self.tmp.mkdir(parents=True, exist_ok=True)
        self._dir_patch = patch.object(feedback, "LOG_DIR", self.tmp)
        self._dir_patch.start()

        def provider(dest, name):
            path = Path(dest) / f"{name}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"png")
            return str(path)

        feedback.set_capture_provider(provider)
        feedback.set_async(False)
        feedback.reset(session_stamp="20260101_010101")
        # 造一段结构化日志，确保 zip 里有对战日志。
        battle_record.start_session(session_id="s", now=1_700_000_000.0)
        battle_record.record("SYS", "[SYS] 回合 2 延时结束，开始本轮推荐读取。")
        battle_record.record("INFO", "[推荐] 打出1号位随从")
        battle_record.record("ERROR", "[ERROR] boom")
        with patch.object(
            feedback,
            "_settings",
            lambda: {
                "enabled": True,
                "retry_threshold": 3,
                "capture_on_error": True,
                "throttle_seconds": 0.0,
                "max_shots_per_run": 30,
                "auto_export_on_stop": False,
            },
        ):
            feedback.note_event(error_info("boom"), now=1.0)

    def tearDown(self):
        self._dir_patch.stop()
        feedback.set_capture_provider(None)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_bundle_contents(self):
        dst = self.tmp / "out.zip"
        result = feedback.export_bundle(dst=dst)
        self.assertTrue(result["ok"], result.get("error"))
        with zipfile.ZipFile(dst) as zf:
            names = set(zf.namelist())
            self.assertIn(feedback.REPORT_TEXT_NAME, names)
            self.assertIn(feedback.REPORT_JSON_NAME, names)
            self.assertIn(feedback.ENV_NAME, names)
            self.assertTrue(
                any(n.startswith(feedback.ARCHIVE_SUBDIR + "/") for n in names)
            )
            # 每个错误文件夹里：截图 + 错误.txt + 错误.json
            self.assertTrue(
                any(n.endswith("/" + feedback.SHOT_FILENAME) for n in names)
            )
            self.assertTrue(
                any(n.endswith("/" + feedback.ERROR_TEXT_NAME) for n in names)
            )
            self.assertTrue(
                any(n.endswith("/" + feedback.ERROR_JSON_NAME) for n in names)
            )
            self.assertTrue(any(n.endswith(".jsonl") for n in names))
            self.assertTrue(
                any(n.endswith(".txt") and n.startswith("对战日志_") for n in names)
            )
            errors = json.loads(zf.read(feedback.REPORT_JSON_NAME))
            self.assertEqual(1, errors["summary"]["error_incidents"])
            jsonl_line = [n for n in names if n.endswith(".jsonl")][0]
            for line in zf.read(jsonl_line).decode("utf-8").splitlines():
                json.loads(line)  # 每行都是合法 JSON


class ZipNameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.gettempdir())) / (
            f"hs_fb_name_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        )
        self.tmp.mkdir(parents=True, exist_ok=True)
        self._dir_patch = patch.object(feedback, "LOG_DIR", self.tmp)
        self._dir_patch.start()

    def tearDown(self):
        self._dir_patch.stop()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _name(self, instruction, when="2026-10-18 12:00:00"):
        items = [{"time": when, "instruction": instruction}]
        return feedback.feedback_zip_name(items, log_dir=self.tmp)

    def test_name_uses_event_date_and_recommendation(self):
        self.assertEqual(
            "20261018-打出1号位随从-错误反馈.zip", self._name("打出1号位随从")
        )

    def test_name_falls_back_when_no_recommendation(self):
        self.assertEqual("20261018-未知推荐-错误反馈.zip", self._name(""))

    def test_name_sanitizes_illegal_chars(self):
        self.assertEqual("20261018-ABCD-错误反馈.zip", self._name("A/B:C*D?"))

    def test_name_appends_index_on_conflict(self):
        (self.tmp / "20261018-打出1号位随从-错误反馈.zip").write_bytes(b"")
        self.assertEqual(
            "20261018-打出1号位随从-错误反馈-2.zip", self._name("打出1号位随从")
        )


class ExportOpenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.gettempdir())) / (
            f"hs_fb_open_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        )
        self.tmp.mkdir(parents=True, exist_ok=True)
        self._dir_patch = patch.object(feedback, "LOG_DIR", self.tmp)
        self._dir_patch.start()
        self.opened = []

        def provider(dest, name):
            path = Path(dest) / f"{name}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"png")
            return str(path)

        feedback.set_capture_provider(provider)
        feedback.set_async(False)
        feedback.set_opener(lambda path: (self.opened.append(path), True)[1])
        feedback.reset(session_stamp="20260101_010101")
        battle_record.start_session(session_id="s", now=1_700_000_000.0)
        battle_record.record("SYS", "[SYS] 回合 2 延时结束，开始本轮推荐读取。")
        battle_record.record("INFO", "[推荐] 打出1号位随从")
        with patch.object(
            feedback,
            "_settings",
            lambda: {
                "enabled": True,
                "retry_threshold": 3,
                "capture_on_error": True,
                "throttle_seconds": 0.0,
                "max_shots_per_run": 30,
                "auto_export_on_stop": False,
            },
        ):
            feedback.note_event(
                error_info("boom"),
                ctx={"turn": 2, "instruction": "打出1号位随从"},
                now=1_700_000_000.0,
            )

    def tearDown(self):
        self._dir_patch.stop()
        feedback.set_capture_provider(None)
        feedback.set_opener(None)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_name_and_open_folder(self):
        result = feedback.export_bundle(open_folder=True)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(result["opened"])
        self.assertEqual([result["path"]], self.opened)
        # 包名 = 紧凑日期(取出错当时) - 盒子推荐 - 错误反馈.zip
        self.assertTrue(result["name"].endswith("-错误反馈.zip"), result["name"])
        self.assertIn("打出1号位随从", result["name"])
        self.assertTrue(Path(result["path"]).exists())


class ArchiveTests(unittest.TestCase):
    """自动存档：每个错误一个文件夹，攒着不丢（无需点击导出）。"""

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.gettempdir())) / (
            f"hs_fb_archive_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        )
        self.tmp.mkdir(parents=True, exist_ok=True)
        self._dir_patch = patch.object(feedback, "LOG_DIR", self.tmp)
        self._dir_patch.start()

        def provider(dest, name):
            path = Path(dest) / f"{name}.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"png")
            return str(path)

        feedback.set_capture_provider(provider)
        feedback.set_async(False)
        feedback.reset(session_stamp="20260101_010101")

    def tearDown(self):
        self._dir_patch.stop()
        feedback.set_capture_provider(None)
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def _settings(self, **over):
        cfg = {
            "enabled": True,
            "retry_threshold": 3,
            "capture_on_error": True,
            "throttle_seconds": 0.0,
            "max_shots_per_run": 30,
            "auto_export_on_stop": False,
        }
        cfg.update(over)
        return patch.object(feedback, "_settings", lambda: cfg)

    def _folders(self):
        day = self.tmp / f"{feedback.ARCHIVE_PREFIX}20260101"
        return sorted(p for p in day.iterdir() if p.is_dir()) if day.is_dir() else []

    def test_error_creates_folder_with_three_files(self):
        with self._settings():
            feedback.note_event(
                error_info("invalid_entity"),
                ctx={"turn": 2, "instruction": "打出1号位随从"},
                now=1_766_000_000.0,
            )
        folders = self._folders()
        self.assertEqual(1, len(folders))
        self.assertTrue(folders[0].name.startswith("20260101_010101-01"))
        names = sorted(p.name for p in folders[0].iterdir())
        self.assertEqual(
            sorted(
                [
                    feedback.SHOT_FILENAME,
                    feedback.ERROR_TEXT_NAME,
                    feedback.ERROR_JSON_NAME,
                ]
            ),
            names,
        )
        body = (folders[0] / feedback.ERROR_TEXT_NAME).read_text(encoding="utf-8")
        self.assertIn("invalid_entity", body)
        self.assertIn("打出1号位随从", body)
        # 截图落盘后，错误文本要反映“已截到”。
        self.assertIn(feedback.SHOT_FILENAME, body)
        self.assertNotIn("未截到", body)

    def test_retry_archive_only_at_threshold(self):
        with self._settings(retry_threshold=3):
            feedback.note_event(retry_info(), now=1_766_000_000.0)
            feedback.note_event(retry_info(), now=1_766_000_001.0)
            self.assertEqual([], self._folders())  # 未达阈值不建
            feedback.note_event(retry_info(), now=1_766_000_002.0)
        self.assertEqual(1, len(self._folders()))  # 达阈值建一份

    def test_export_includes_only_current_run(self):
        with self._settings():
            feedback.note_event(error_info("boom"), now=1_766_000_000.0)
        # 另造一个“别的运行”的文件夹，导出时不该被打包。
        other = (
            self.tmp
            / f"{feedback.ARCHIVE_PREFIX}20260101"
            / "20250101_000000-99_回合9_报错"
        )
        other.mkdir(parents=True, exist_ok=True)
        (other / feedback.ERROR_TEXT_NAME).write_text("old", encoding="utf-8")

        result = feedback.export_bundle(dst=self.tmp / "out.zip")
        self.assertTrue(result["ok"], result.get("error"))
        with zipfile.ZipFile(self.tmp / "out.zip") as zf:
            names = zf.namelist()
        self.assertTrue(any("20260101_010101-01" in n for n in names))
        self.assertFalse(any("20250101_000000-99" in n for n in names))


if __name__ == "__main__":
    unittest.main()
