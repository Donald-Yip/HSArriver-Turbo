# -*- coding: utf-8 -*-
"""常驻意图记录（``src/safety/resident_intent.py``）测试。

覆盖：缺文件=first_run、手动停止落盘、分类（manual/error/first_run）、清除、
坏文件/坏 JSON 兜底、写盘失败不抛异常、reason 截断、原子写不残留 .tmp。
全部落在临时目录，绝不碰仓库真实的 ``log/resident_intent.json``。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.safety import resident_intent


class _TempPathCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "log" / "resident_intent.json"


class ReadTests(_TempPathCase):
    def test_missing_file_is_first_run(self):
        self.assertEqual({}, resident_intent.read(self.path))
        self.assertEqual("first_run", resident_intent.classify_boot(self.path))
        self.assertFalse(resident_intent.manual_stop_active(self.path))
        self.assertEqual(
            {"kind": "first_run", "manual_stop": False, "scope": "",
             "reason": "", "at": ""},
            resident_intent.summary(self.path),
        )

    def test_corrupt_json_is_treated_as_first_run(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json", encoding="utf8")
        self.assertEqual({}, resident_intent.read(self.path))
        self.assertEqual("first_run", resident_intent.classify_boot(self.path))

    def test_non_object_json_is_treated_as_first_run(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("[1, 2, 3]", encoding="utf8")
        self.assertEqual({}, resident_intent.read(self.path))
        self.assertEqual("first_run", resident_intent.classify_boot(self.path))

    def test_default_path_lives_under_repo_log(self):
        self.assertEqual(
            Path(resident_intent.ROOT) / "log" / "resident_intent.json",
            resident_intent.DEFAULT_PATH,
        )


class RecordTests(_TempPathCase):
    def test_record_manual_stop_writes_atomic_payload(self):
        payload = resident_intent.record_manual_stop(
            "浮窗退出脚本", at="2026-11-01T13:30:00", path=self.path
        )
        self.assertTrue(self.path.exists())
        # 原子写：临时文件必须已经消失
        self.assertFalse(self.path.with_name(self.path.name + ".tmp").exists())
        data = json.loads(self.path.read_text(encoding="utf8"))
        self.assertEqual(payload, data)
        self.assertEqual(1, data["version"])
        self.assertTrue(data["manual_stop"])
        self.assertEqual("浮窗退出脚本", data["reason"])
        self.assertEqual("2026-11-01T13:30:00", data["at"])
        self.assertIsInstance(data["pid"], int)

    def test_record_creates_missing_log_directory(self):
        self.assertFalse(self.path.parent.exists())
        resident_intent.record_manual_stop("用户停止", path=self.path)
        self.assertTrue(self.path.parent.is_dir())

    def test_record_sets_manual_classification_and_active_flag(self):
        resident_intent.record_manual_stop("Ctrl+Q 急停", path=self.path)
        self.assertTrue(resident_intent.manual_stop_active(self.path))
        self.assertEqual("manual", resident_intent.classify_boot(self.path))
        info = resident_intent.summary(self.path)
        self.assertEqual("manual", info["kind"])
        self.assertTrue(info["manual_stop"])
        self.assertEqual("Ctrl+Q 急停", info["reason"])
        self.assertTrue(info["at"])

    def test_record_overwrites_previous_reason(self):
        resident_intent.record_manual_stop("第一次", path=self.path)
        resident_intent.record_manual_stop("第二次", path=self.path)
        self.assertEqual("第二次", resident_intent.summary(self.path)["reason"])

    def test_blank_reason_falls_back(self):
        resident_intent.record_manual_stop("   ", path=self.path)
        self.assertEqual("manual", resident_intent.summary(self.path)["reason"])
        resident_intent.record_manual_stop(None, path=self.path)
        self.assertEqual("manual", resident_intent.summary(self.path)["reason"])

    def test_long_reason_is_truncated(self):
        resident_intent.record_manual_stop("x" * 500, path=self.path)
        reason = resident_intent.summary(self.path)["reason"]
        self.assertEqual(resident_intent.MAX_REASON_LENGTH, len(reason))

    def test_explicit_pid_is_recorded(self):
        resident_intent.record_manual_stop("手动", pid=4321, path=self.path)
        self.assertEqual(4321, resident_intent.read(self.path)["pid"])

    def test_record_running_classifies_crash_as_error(self):
        resident_intent.record_running("console_boot", at="2026-11-01T13:40:00",
                                       path=self.path)
        data = resident_intent.read(self.path)
        self.assertFalse(data["manual_stop"])
        self.assertEqual("console_boot", data["reason"])
        # 控制台在这之后崩溃（没来得及再写文件）→ 下次启动必须是 error。
        self.assertEqual("error", resident_intent.classify_boot(self.path))
        self.assertFalse(resident_intent.manual_stop_active(self.path))

    def test_record_running_overwrites_manual_marker(self):
        resident_intent.record_manual_stop("用户停止", path=self.path)
        resident_intent.record_running(path=self.path)
        self.assertEqual("error", resident_intent.classify_boot(self.path))

    def test_manual_stop_scope_controls_watchdog(self):
        # 退出整个控制台：看门狗必须跳过自动拉起。
        resident_intent.record_manual_stop(
            "浮窗退出脚本", scope="console", path=self.path
        )
        self.assertEqual("console", resident_intent.summary(self.path)["scope"])
        self.assertTrue(resident_intent.manual_stop_active(self.path))
        self.assertTrue(resident_intent.autostart_blocked(self.path))

    def test_automation_scope_does_not_block_watchdog(self):
        # 只是停了自动化：常驻保持暂停，但控制台崩了看门狗仍要拉起。
        resident_intent.record_manual_stop(
            "用户停止自动化", scope="automation", path=self.path
        )
        self.assertTrue(resident_intent.manual_stop_active(self.path))
        self.assertFalse(resident_intent.autostart_blocked(self.path))
        self.assertEqual("automation", resident_intent.summary(self.path)["scope"])

    def test_unknown_scope_falls_back_to_console(self):
        resident_intent.record_manual_stop(
            "手动", scope="whatever", path=self.path
        )
        self.assertEqual("console", resident_intent.summary(self.path)["scope"])

    def test_record_running_is_not_blocked_for_watchdog(self):
        resident_intent.record_running(path=self.path)
        self.assertFalse(resident_intent.autostart_blocked(self.path))

    def test_write_failure_is_swallowed(self):
        # 把 path 指到一个目录上：写盘必然失败，但调用方不能收到异常。
        self.path.mkdir(parents=True)
        payload = resident_intent.record_manual_stop("手动", path=self.path)
        self.assertTrue(payload["manual_stop"])
        self.assertFalse(resident_intent.manual_stop_active(self.path))


class ClearTests(_TempPathCase):
    def test_clear_removes_marker_and_resets_classification(self):
        resident_intent.record_manual_stop("用户停止", path=self.path)
        self.assertTrue(resident_intent.clear(self.path))
        self.assertFalse(self.path.exists())
        self.assertFalse(resident_intent.manual_stop_active(self.path))
        self.assertEqual("first_run", resident_intent.classify_boot(self.path))

    def test_clear_is_idempotent(self):
        self.assertTrue(resident_intent.clear(self.path))
        self.assertTrue(resident_intent.clear(self.path))

    def test_clear_removes_stale_tmp_file(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stale = self.path.with_name(self.path.name + ".tmp")
        stale.write_text("{}", encoding="utf8")
        resident_intent.clear(self.path)
        self.assertFalse(stale.exists())


class ClassificationTests(_TempPathCase):
    def test_file_without_manual_flag_is_error(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"version": 1, "manual_stop": False}), encoding="utf8"
        )
        self.assertEqual("error", resident_intent.classify_boot(self.path))
        self.assertFalse(resident_intent.manual_stop_active(self.path))

    def test_truthy_manual_flag_is_manual(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"manual_stop": 1, "reason": "user_stop"}), encoding="utf8"
        )
        self.assertEqual("manual", resident_intent.classify_boot(self.path))


if __name__ == "__main__":
    unittest.main()
