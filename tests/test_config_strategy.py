# -*- coding: utf-8 -*-
"""策略选择 / 联合模式 / 最大搜索深度的配置读写回归测试。

所有读写都落在临时 ui_config.json 上：monkeypatch config.CONFIG_PATH，
测试结束自动还原，绝不碰仓库里真实的 ui_config.json。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config


class StrategyConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "ui_config.json"
        patcher = patch.object(config, "CONFIG_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    # ---------------------------------------------------------------- 辅助
    def _read(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf8"))

    def _write(self, data: dict) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8"
        )

    # ---------------------------------------------------------------- 默认值
    def test_defaults_are_box_ai(self):
        self.assertEqual("hsbox", config.strategy_setting())
        self.assertTrue(config.uses_box_ai())
        self.assertEqual("盒子AI", config.strategy_label())
        self.assertFalse(config.joint_setting())
        self.assertEqual(config.DEFAULT_MAX_SEARCH, config.max_search_setting())

    def test_choices_include_box_ai_and_the_three_decks(self):
        choices = config.strategy_choices()
        self.assertEqual("盒子AI", choices["hsbox"])
        for deck in ("任务牧", "动物园术", "强化学习动物园术"):
            self.assertIn(deck, choices)

        options = config.strategy_options()
        keys = [row["key"] for row in options]
        self.assertEqual("hsbox", keys[0])          # 盒子AI 永远排第一
        self.assertEqual(len(choices), len(options))
        for row in options:
            self.assertTrue(row["key"])
            self.assertTrue(row["label"])

    # ---------------------------------------------------------------- 策略
    def test_save_strategy_round_trip_and_preserves_unrelated_keys(self):
        saved = config.save_strategy_setting("动物园术")

        self.assertEqual("动物园术", saved)
        self.assertEqual("动物园术", config.strategy_setting())
        self.assertEqual("动物园术", config.strategy_label())
        self.assertFalse(config.uses_box_ai())
        self.assertEqual("动物园术", self._read()["strategy"])

        original = {
            "name": "TestUser#1234",
            "log_root": "D:\\HearthstoneLogs",
            "strategy": "hsbox",
            "max_search": 42,
            "delays": {"post_action_delay_seconds": 1.5},
            "human_like": {"enabled": True},
        }
        self._write(original)
        config.save_strategy_setting("任务牧")

        data = self._read()
        self.assertEqual("任务牧", data["strategy"])
        self.assertEqual(original["name"], data["name"])
        self.assertEqual(original["log_root"], data["log_root"])
        self.assertEqual(original["max_search"], data["max_search"])
        self.assertEqual(original["delays"], data["delays"])
        self.assertEqual(original["human_like"], data["human_like"])

    def test_unknown_strategy_raises_key_error(self):
        with self.assertRaises(KeyError):
            config.save_strategy_setting("不存在的卡组")

        self.assertFalse(self.path.exists())          # 校验失败不落盘

    def test_broken_strategy_falls_back_to_box_ai(self):
        self._write({"strategy": "已经被删掉的卡组"})

        self.assertEqual(config.DEFAULT_STRATEGY, config.strategy_setting())
        self.assertTrue(config.uses_box_ai())

    def test_dict_forms_are_accepted(self):
        self._write({"strategy": {"key": "强化学习动物园术"}})
        self.assertEqual("强化学习动物园术", config.strategy_setting())
        self.assertEqual("强化学习动物园术", config.strategy_label())

        self._write({"joint": {"enabled": True}})
        self.assertTrue(config.joint_setting())
        self._write({"joint": {"enabled": False}})
        self.assertFalse(config.joint_setting())

    # ---------------------------------------------------------------- 联合模式
    def test_joint_round_trip(self):
        self.assertTrue(config.save_joint_setting(True))
        self.assertTrue(config.joint_setting())
        self.assertTrue(self._read()["joint"])

        self.assertFalse(config.save_joint_setting(False))
        self.assertFalse(config.joint_setting())
        self.assertFalse(self._read()["joint"])

    # ---------------------------------------------------------------- 最大搜索深度
    def test_max_search_bounds_are_clamped(self):
        self.assertEqual(1, config.save_max_search_setting(0))
        self.assertEqual(1, config.max_search_setting())
        self.assertEqual(100000, config.save_max_search_setting(999999))
        self.assertEqual(100000, config.max_search_setting())
        self.assertEqual(1500, config.save_max_search_setting(1500))
        self.assertEqual(1500, config.max_search_setting())

        # 文件里的坏值读取时也要夹住。
        self._write({"max_search": "oops"})
        self.assertEqual(config.DEFAULT_MAX_SEARCH, config.max_search_setting())
        self._write({"max_search": 0})
        self.assertEqual(1, config.max_search_setting())
        self._write({"max_search": 999999})
        self.assertEqual(100000, config.max_search_setting())

    def test_max_search_save_preserves_strategy_and_rejects_non_integer(self):
        with self.assertRaises(ValueError):
            config.save_max_search_setting("abc")
        with self.assertRaises(ValueError):
            config.save_max_search_setting(None)

        self.assertFalse(self.path.exists())

        self._write({"strategy": "任务牧"})
        config.save_max_search_setting(200)

        self.assertEqual("任务牧", config.strategy_setting())
        self.assertEqual(200, self._read()["max_search"])


if __name__ == "__main__":
    unittest.main()
