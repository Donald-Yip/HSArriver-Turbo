# -*- coding: utf-8 -*-
"""R18K FSM 接线：盒子兜底快照构建 + 传给 RecommendationFlow 的参数。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import FSM_action


class BoxFallbackFsmWiringTests(unittest.TestCase):
    _GLOBALS = (
        "auto_mulligan_flow", "recommendation_flow",
        "recommendation_config", "recommendation_capture", "mulligan_capture",
        "recommendation_parser", "recommendation_reader", "mulligan_reader",
        "recommendation_validator", "_box_fallback",
    )

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "ui_config.json"
        patcher = patch.object(config, "CONFIG_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self._saved = {name: getattr(FSM_action, name, None)
                       for name in self._GLOBALS}
        self.addCleanup(self._restore)

    def _restore(self):
        for name, value in self._saved.items():
            setattr(FSM_action, name, value)

    def _write_config(self, box_fallback):
        self.path.write_text(
            json.dumps({"box_fallback": box_fallback}, ensure_ascii=False),
            encoding="utf8")

    def _initialize(self):
        # 昂贵的 OCR 组件给占位对象：接线测试不需要真的识别。
        FSM_action.recommendation_parser = object()
        FSM_action.recommendation_reader = object()
        FSM_action.mulligan_reader = object()
        FSM_action.recommendation_validator = object()
        FSM_action.initialize_recommendation_automation()

    def test_enabled_builds_snapshot_and_wires_flow(self):
        self._write_config({
            "enabled": True,
            "strategy": "通用",
            "fail_streak": 3,
            "stall_seconds": 4.5,
            "mulligan_fail_streak": 2,
        })

        self._initialize()

        snapshot = FSM_action._box_fallback
        self.assertIsNotNone(snapshot)
        self.assertEqual("通用", snapshot["key"])
        self.assertEqual("通用", snapshot["label"])
        self.assertEqual(3, snapshot["fail_streak"])
        self.assertEqual(4.5, snapshot["stall_seconds"])
        self.assertIsNotNone(snapshot["planner"])
        self.assertTrue(callable(snapshot["chooser"]))

        flow = FSM_action.recommendation_flow
        self.assertTrue(flow.fallback_enabled)
        self.assertIs(flow.fallback_planner, snapshot["planner"])
        self.assertEqual(3, flow.fallback_fail_streak)
        self.assertEqual(4.5, flow.fallback_stall_seconds)
        self.assertEqual("通用", flow.fallback_strategy_name)

    def test_disabled_builds_no_snapshot(self):
        self._write_config({
            "enabled": False,
            "strategy": "通用",
        })

        self._initialize()

        self.assertIsNone(FSM_action._box_fallback)
        self.assertFalse(FSM_action.recommendation_flow.fallback_enabled)


if __name__ == "__main__":
    unittest.main()
