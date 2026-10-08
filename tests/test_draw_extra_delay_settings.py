# -*- coding: utf-8 -*-
"""抽牌额外延时的配置与接线：默认 1s/张、ui_config 可覆盖、网页可调、FSM 接线。"""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import FSM_action
import web_ui
from config import _USER_DELAY_KEYS, RecommendationConfig
from src.flow.recommendation_flow import RecommendationFlow

INDEX_HTML = Path(__file__).resolve().parent.parent / "web" / "index.html"
KEY = "draw_extra_delay_per_card_seconds"


class ConfigTests(unittest.TestCase):
    def test_default_is_one_second_per_card(self):
        self.assertEqual(1.0, getattr(RecommendationConfig, KEY))
        self.assertIn(KEY, _USER_DELAY_KEYS)

    def test_key_is_part_of_the_user_delay_whitelist(self):
        # 白名单尾部顺序会随新增项变化（例如后来的 discover_target_delay_seconds），
        # 这里只锁"抽牌延时在延迟项区域里、且排在 OCR 缩放之前"。
        self.assertIn(KEY, _USER_DELAY_KEYS)
        self.assertLess(_USER_DELAY_KEYS.index(KEY),
                        _USER_DELAY_KEYS.index("ocr_preprocess_scale"))

    def test_config_leaf_matches_the_field(self):
        from src.recommendation_config import RecommendationConfig as Forwarded

        self.assertTrue(hasattr(Forwarded, KEY))


class FlowDefaultTests(unittest.TestCase):
    def test_flow_defaults_to_disabled_for_library_callers(self):
        flow = RecommendationFlow(
            capture=None, reader=None, parser=None, state_supplier=None,
            adapter=None, validator=None, controller=None)

        self.assertEqual(0.0, flow.draw_extra_delay_per_card)

    def test_flow_takes_the_configured_seconds(self):
        flow = RecommendationFlow(
            capture=None, reader=None, parser=None, state_supplier=None,
            adapter=None, validator=None, controller=None,
            draw_extra_delay_per_card=2.5)

        self.assertEqual(2.5, flow.draw_extra_delay_per_card)


class FsmWiringTests(unittest.TestCase):
    def test_production_flow_gets_the_configured_draw_delay(self):
        """initialize_recommendation_automation() 必须把配置传进流程。"""
        names = ("recommendation_flow", "recommendation_config",
                 "recommendation_capture", "recommendation_parser",
                 "recommendation_reader", "mulligan_reader",
                 "recommendation_validator")
        originals = {name: getattr(FSM_action, name) for name in names}

        def stub_config():
            return SimpleNamespace(
                recommendation_roi=(7, 200, 202, 500),
                desktop_size=(1920, 1080), desktop_dpi=96,
                mulligan_post_ocr_delay_seconds=1.0,
                post_action_delay_seconds=0.5,
                result_timeout_seconds=5.0,
                draw_extra_delay_per_card_seconds=1.5)

        try:
            FSM_action.recommendation_parser = object()
            FSM_action.recommendation_reader = object()
            FSM_action.mulligan_reader = object()
            FSM_action.recommendation_validator = object()
            with patch.object(FSM_action, "RecommendationConfig", stub_config):
                FSM_action.initialize_recommendation_automation()

            flow = FSM_action.recommendation_flow
            self.assertEqual(1.5, flow.draw_extra_delay_per_card)
        finally:
            for name, value in originals.items():
                setattr(FSM_action, name, value)


class WebSettingsTests(unittest.TestCase):
    def setUp(self):
        self.saved = {}

        def _save(cfg):
            self.saved = cfg

        def _stub_config():
            """让 _current_delays() 读“已保存”的值，而不是磁盘上的真配置。"""
            values = dict(self.saved.get("delays") or {})
            return SimpleNamespace(**{
                key: values.get(key, 1.0) for key in _USER_DELAY_KEYS})

        self._patches = [
            patch.object(web_ui, "load_config",
                         side_effect=lambda: dict(self.saved)),
            patch.object(web_ui, "save_config", side_effect=_save),
            patch.object(web_ui, "RecommendationConfig", _stub_config),
            patch.object(web_ui, "_log"),
        ]
        for item in self._patches:
            item.start()
        self._thread = web_ui.CTRL.automation_thread
        web_ui.CTRL.automation_thread = None

    def tearDown(self):
        web_ui.CTRL.automation_thread = self._thread
        for item in self._patches:
            item.stop()

    def test_status_exposes_the_draw_delay(self):
        delays = web_ui._current_delays()

        self.assertIn(KEY, delays)
        self.assertEqual(1.0, delays[KEY])

    def test_saving_writes_the_value(self):
        result = web_ui.api_save_delays({KEY: 2.5})

        self.assertTrue(result["ok"])
        self.assertEqual(2.5, self.saved["delays"][KEY])
        self.assertEqual(2.5, result["delays"][KEY])

    def test_out_of_range_value_is_refused(self):
        result = web_ui.api_save_delays({KEY: 99})

        self.assertFalse(result["ok"])
        self.assertIn("介于", result["error"])
        self.assertEqual({}, self.saved)

    def test_non_numeric_value_is_refused(self):
        result = web_ui.api_save_delays({KEY: "abc"})

        self.assertFalse(result["ok"])
        self.assertIn("数字", result["error"])

    def test_zero_disables_the_extra_delay(self):
        result = web_ui.api_save_delays({KEY: 0})

        self.assertTrue(result["ok"])
        self.assertEqual(0.0, self.saved["delays"][KEY])


class WebPageTests(unittest.TestCase):
    def setUp(self):
        self.html = INDEX_HTML.read_text(encoding="utf8")

    def test_delay_field_exists(self):
        self.assertIn('id="inpDrawDelay"', self.html)
        self.assertIn("抽牌额外延时", self.html)

    def test_page_sends_and_renders_the_value(self):
        self.assertIn(
            'draw_extra_delay_per_card_seconds: $("inpDrawDelay").value',
            self.html)
        self.assertIn(
            '$("inpDrawDelay").value = s.delays.draw_extra_delay_per_card_seconds',
            self.html)

    def test_hint_explains_the_free_turn_start_draw(self):
        self.assertIn("回合开始那 1 张常规抽牌不算", self.html)


if __name__ == "__main__":
    unittest.main()
