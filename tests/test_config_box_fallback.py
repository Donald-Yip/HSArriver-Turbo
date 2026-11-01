# -*- coding: utf-8 -*-
"""盒子失效兜底配置（ui_config.json 的 box_fallback 段）读写回归测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config


class BoxFallbackConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "ui_config.json"
        patcher = patch.object(config, "CONFIG_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf8"))

    def _write(self, data: dict) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")

    def test_defaults_are_off_with_generic_strategy(self):
        cfg = config.box_fallback_settings()
        self.assertFalse(cfg["enabled"])
        self.assertEqual("通用", cfg["strategy"])
        self.assertEqual(config.DEFAULT_BOX_FALLBACK["fail_streak"],
                         cfg["fail_streak"])
        self.assertEqual(config.DEFAULT_BOX_FALLBACK["stall_seconds"],
                         cfg["stall_seconds"])
        self.assertEqual(config.DEFAULT_BOX_FALLBACK["mulligan_fail_streak"],
                         cfg["mulligan_fail_streak"])

    def test_bad_values_are_normalized(self):
        self._write({"box_fallback": {
            "enabled": "false",
            "strategy": "hsbox",
            "fail_streak": "oops",
            "stall_seconds": 999,
            "mulligan_fail_streak": -3,
        }})
        cfg = config.box_fallback_settings()
        self.assertFalse(cfg["enabled"])
        self.assertEqual("通用", cfg["strategy"])       # 盒子AI 不是本地策略
        self.assertEqual(config.DEFAULT_BOX_FALLBACK["fail_streak"],
                         cfg["fail_streak"])
        self.assertEqual(120.0, cfg["stall_seconds"])   # 夹到上限
        self.assertEqual(1, cfg["mulligan_fail_streak"])  # 夹到下限

    def test_unknown_strategy_falls_back_to_generic(self):
        self._write({"box_fallback": {"strategy": "已经被删掉的卡组"}})
        self.assertEqual("通用", config.box_fallback_settings()["strategy"])

    def test_save_round_trip_preserves_unrelated_keys(self):
        original = {
            "name": "TestUser#1234",
            "strategy": "任务牧",
            "max_search": 42,
            "delays": {"post_action_delay_seconds": 0.2},
        }
        self._write(original)
        saved = config.save_box_fallback_setting(
            enabled=True, fail_streak=4, stall_seconds=5.5)
        self.assertTrue(saved["enabled"])
        self.assertEqual(4, saved["fail_streak"])
        self.assertEqual(5.5, saved["stall_seconds"])

        data = self._read()
        self.assertEqual("通用", data["box_fallback"]["strategy"])
        self.assertTrue(data["box_fallback"]["enabled"])
        for key, value in original.items():
            self.assertEqual(value, data[key])

    def test_save_rejects_hsbox_and_unknown_strategy(self):
        for key in ("hsbox", "不存在的卡组"):
            with self.subTest(strategy=key):
                with self.assertRaises(KeyError):
                    config.save_box_fallback_setting(strategy=key)
        self.assertFalse(self.path.exists())      # 校验失败不落盘

    def test_save_rejects_junk_numbers(self):
        for kwargs in (
                {"fail_streak": "abc"},
                {"fail_streak": 0},
                {"fail_streak": 999},
                {"stall_seconds": "abc"},
                {"stall_seconds": 0},
                {"stall_seconds": 999},
                {"mulligan_fail_streak": 0}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    config.save_box_fallback_setting(**kwargs)

    def test_disabled_round_trip(self):
        config.save_box_fallback_setting(enabled=True)
        self.assertTrue(config.box_fallback_settings()["enabled"])
        config.save_box_fallback_setting(enabled=False)
        self.assertFalse(config.box_fallback_settings()["enabled"])
        self.assertFalse(self._read()["box_fallback"]["enabled"])


if __name__ == "__main__":
    unittest.main()
