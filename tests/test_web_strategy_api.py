# -*- coding: utf-8 -*-
"""Web 控制台「策略选择」接口：保存参数、运行中保护、状态载荷与页面接线。

用临时 ui_config.json（monkeypatch web_ui.CONFIG_PATH 与 config.CONFIG_PATH），
不写真实配置；所有测试不启动 HTTP 服务、不启动自动化。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import web_ui

INDEX_HTML = Path(__file__).resolve().parent.parent / "web" / "index.html"


class StrategyApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_path = Path(self._tmp.name) / "ui_config.json"
        # web_ui.CONFIG_PATH 供 load_config/save_config；config.CONFIG_PATH 供
        # strategy_*/joint_*/max_search_* 这组函数（它们每次调用都重新读盘）。
        for module in (web_ui, config):
            patcher = patch.object(module, "CONFIG_PATH", self.config_path)
            patcher.start()
            self.addCleanup(patcher.stop)
        log_patcher = patch.object(web_ui, "_log")
        log_patcher.start()
        self.addCleanup(log_patcher.stop)
        saved_thread = web_ui.CTRL.automation_thread
        self.addCleanup(setattr, web_ui.CTRL, "automation_thread", saved_thread)
        web_ui.CTRL.automation_thread = None

    def _read(self) -> dict:
        return json.loads(self.config_path.read_text(encoding="utf8"))

    def _running(self):
        web_ui.CTRL.automation_thread = object()

    # ---------------------------------------------------------------- 保存策略
    def test_save_strategy_round_trip(self):
        result = web_ui.api_save_strategy({"strategy": "动物园术"})

        self.assertTrue(result["ok"])
        self.assertEqual("动物园术", result["strategy"]["key"])
        self.assertEqual("动物园术", result["strategy"]["label"])
        self.assertIn("下一局生效", result["message"])
        self.assertEqual("动物园术", self._read()["strategy"])
        self.assertEqual("动物园术", config.strategy_setting())

    def test_unknown_strategy_is_refused(self):
        result = web_ui.api_save_strategy({"strategy": "不存在的卡组"})

        self.assertFalse(result["ok"])
        self.assertIn("未知策略", result["error"])
        self.assertFalse(self.config_path.exists())

    def test_missing_or_bad_strategy_is_refused(self):
        for body in ({}, {"strategy": ""}, {"strategy": 123}):
            result = web_ui.api_save_strategy(body)
            self.assertFalse(result["ok"], body)
            self.assertIn("请选择策略", result["error"])

    # ---------------------------------------------------------------- 联合模式
    def test_save_joint_round_trip(self):
        result = web_ui.api_save_joint({"joint": True})

        self.assertTrue(result["ok"])
        self.assertTrue(result["joint"])
        self.assertTrue(self._read()["joint"])
        self.assertTrue(config.joint_setting())

        # 请求里没带 joint 时沿用当前值（和活人感等卡片的写法一致）
        result = web_ui.api_save_joint({})
        self.assertTrue(result["ok"])
        self.assertTrue(result["joint"])

    def test_save_joint_can_turn_it_off(self):
        web_ui.api_save_joint({"joint": True})
        result = web_ui.api_save_joint({"joint": False})

        self.assertTrue(result["ok"])
        self.assertFalse(result["joint"])
        self.assertFalse(self._read()["joint"])

    # ---------------------------------------------------------------- 搜索深度
    def test_save_max_search_round_trip_and_clamps(self):
        result = web_ui.api_save_max_search({"max_search": 999999})
        self.assertTrue(result["ok"])
        self.assertEqual(100000, result["max_search"])
        self.assertEqual(100000, self._read()["max_search"])

        self.assertEqual(
            1, web_ui.api_save_max_search({"max_search": 0})["max_search"])

    def test_save_max_search_rejects_non_integer(self):
        result = web_ui.api_save_max_search({"max_search": "abc"})

        self.assertFalse(result["ok"])
        self.assertIn("整数", result["error"])
        self.assertFalse(self.config_path.exists())

    # ---------------------------------------------------------------- 运行中保护
    def test_running_automation_is_refused_without_hot(self):
        self._running()

        results = (
            web_ui.api_save_strategy({"strategy": "任务牧"}),
            web_ui.api_save_joint({"joint": True}),
            web_ui.api_save_max_search({"max_search": 100}),
        )
        for result in results:
            self.assertFalse(result["ok"])
            self.assertIn("运行中", result["error"])
        self.assertFalse(self.config_path.exists())

    def test_hot_flag_allows_saving_while_running(self):
        self._running()

        self.assertTrue(
            web_ui.api_save_strategy({"hot": True, "strategy": "任务牧"})["ok"])
        self.assertTrue(
            web_ui.api_save_joint({"hot": True, "joint": True})["ok"])
        self.assertTrue(
            web_ui.api_save_max_search({"hot": True, "max_search": 300})["ok"])

        data = self._read()
        self.assertEqual("任务牧", data["strategy"])
        self.assertTrue(data["joint"])
        self.assertEqual(300, data["max_search"])

    # ---------------------------------------------------------------- status 载荷
    def test_status_snapshot_exposes_strategy_fields(self):
        web_ui.api_save_strategy({"strategy": "任务牧"})
        web_ui.api_save_joint({"joint": True})
        web_ui.api_save_max_search({"max_search": 800})

        with (
            patch.object(web_ui.CTRL, "fsm", None),
            # 存活检测会去探测真实进程/日志，这里不需要，直接给空状态保持快速、
            # 不依赖本机环境。
            patch.object(web_ui, "_liveness_state", return_value={}),
        ):
            snapshot = web_ui.status_snapshot()

        self.assertEqual("任务牧", snapshot["strategy"]["key"])
        self.assertEqual("任务牧", snapshot["strategy"]["label"])
        self.assertTrue(snapshot["joint"])
        self.assertEqual(800, snapshot["max_search"])

        option_keys = {row["key"] for row in snapshot["strategy_options"]}
        self.assertIn("hsbox", option_keys)
        self.assertIn("任务牧", option_keys)
        # 既有字段不能被挤掉
        for key in ("delays", "config", "human_like", "running"):
            self.assertIn(key, snapshot)


class StrategyPageTests(unittest.TestCase):
    def setUp(self):
        self.html = INDEX_HTML.read_text(encoding="utf8")

    def test_strategy_card_controls_exist(self):
        for token in (
            'id="inpStrategy"',
            'id="chkJoint"',
            'id="inpMaxSearch"',
            'id="btnSaveStrategy"',
        ):
            self.assertIn(token, self.html)

    def test_page_posts_to_the_strategy_apis(self):
        for token in (
            'post("/api/strategy"',
            'post("/api/joint"',
            'post("/api/max_search"',
        ):
            self.assertIn(token, self.html)

    def test_page_hints_explain_the_modes(self):
        self.assertIn("盒子AI", self.html)
        self.assertIn("改策略下一局生效", self.html)


if __name__ == "__main__":
    unittest.main()
