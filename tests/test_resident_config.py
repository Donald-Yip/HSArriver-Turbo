# -*- coding: utf-8 -*-
"""常驻模式配置（ui_config.json 的 resident 段）的读取与收敛测试。

覆盖：默认值、中文/大小写别名、越界收敛、缺字段回退、缺段回退、每次调用
重新读文件（改完即时生效）。所有读写都落在临时 ui_config.json 上，
绝不碰仓库里真实的 ui_config.json。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config

DEFAULTS = {
    "enabled": False,
    "mode": "standard",
    "deck_index": None,
    "stale_in_game_seconds": 180.0,
    "stale_other_seconds": 300.0,
    "warn_seconds": 120.0,
    "max_restarts": 3,
    "window_seconds": 600.0,
    "check_seconds": 5.0,
    "settle_seconds": 120.0,
    "start_delay_seconds": 5.0,
    "dry_run": False,
}


class ResidentNormalizeTests(unittest.TestCase):
    """纯函数 normalize_resident_settings() 的规则。"""

    def test_defaults_match_the_contract(self):
        self.assertEqual(DEFAULTS, dict(config.DEFAULT_RESIDENT))
        self.assertEqual(DEFAULTS, config.normalize_resident_settings({}))
        self.assertEqual(DEFAULTS, config.normalize_resident_settings(None))
        self.assertEqual(DEFAULTS, config.normalize_resident_settings({"unknown": 1}))

    def test_modes_and_aliases(self):
        self.assertEqual(("standard", "wild", "casual"), config.RESIDENT_MODES)
        cases = {
            "standard": "standard",
            "wild": "wild",
            "casual": "casual",
            "标准": "standard",
            "狂野": "wild",
            "休闲": "casual",
            "WILD": "wild",
            " Standard ": "standard",
        }
        for alias, expected in cases.items():
            with self.subTest(alias=alias):
                self.assertEqual(
                    expected,
                    config.normalize_resident_settings({"mode": alias})["mode"],
                )

    def test_invalid_mode_falls_back_to_standard(self):
        for bad in ("狂野模式", "legend", "", 123, None, True):
            with self.subTest(bad=bad):
                self.assertEqual(
                    "standard",
                    config.normalize_resident_settings({"mode": bad})["mode"],
                )

    def test_deck_index_accepts_only_1_to_9(self):
        cases = [
            (1, 1),
            (9, 9),
            ("3", 3),
            (" 5 ", 5),
            (2.0, 2),
            (0, None),
            (10, None),
            (-1, None),
            (3.5, None),
            ("abc", None),
            ("", None),
            (None, None),
            (True, None),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(
                    expected,
                    config.normalize_resident_settings({"deck_index": value})["deck_index"],
                )

    def test_stale_in_game_is_clamped_to_60_3600(self):
        self.assertEqual(60.0, config.normalize_resident_settings(
            {"stale_in_game_seconds": 1})["stale_in_game_seconds"])
        self.assertEqual(3600.0, config.normalize_resident_settings(
            {"stale_in_game_seconds": 99999})["stale_in_game_seconds"])
        self.assertEqual(240.0, config.normalize_resident_settings(
            {"stale_in_game_seconds": 240})["stale_in_game_seconds"])

    def test_stale_other_is_never_below_stale_in_game_or_60(self):
        # 写坏成 10s 时抬到对局中阈值（180s），避免主菜单刚进去就被判卡住。
        cfg = config.normalize_resident_settings(
            {"stale_in_game_seconds": 180, "stale_other_seconds": 10})
        self.assertEqual(180.0, cfg["stale_other_seconds"])
        # 对局中阈值更大时，非对局阈值也跟着抬。
        cfg = config.normalize_resident_settings(
            {"stale_in_game_seconds": 1000, "stale_other_seconds": 500})
        self.assertEqual(1000.0, cfg["stale_other_seconds"])

    def test_warn_seconds_zero_is_valid_and_clamped_to_stale_in_game(self):
        # 0 = 关闭提前告警，是合法值，不能被当成“空值”回默认。
        self.assertEqual(0.0, config.normalize_resident_settings(
            {"warn_seconds": 0})["warn_seconds"])
        self.assertEqual(90.0, config.normalize_resident_settings(
            {"warn_seconds": 90})["warn_seconds"])
        # 提前告警不能晚于处理阈值：> stale_in_game 时夹到 stale_in_game。
        cfg = config.normalize_resident_settings(
            {"warn_seconds": 9999, "stale_in_game_seconds": 180})
        self.assertEqual(180.0, cfg["warn_seconds"])

    def test_check_seconds_is_clamped_to_2_60(self):
        self.assertEqual(2.0, config.normalize_resident_settings(
            {"check_seconds": 0.5})["check_seconds"])
        self.assertEqual(60.0, config.normalize_resident_settings(
            {"check_seconds": 999})["check_seconds"])
        self.assertEqual(15.0, config.normalize_resident_settings(
            {"check_seconds": 15})["check_seconds"])

    def test_max_restarts_is_clamped_to_1_20_and_cast_to_int(self):
        self.assertEqual(1, config.normalize_resident_settings(
            {"max_restarts": 0})["max_restarts"])
        self.assertEqual(20, config.normalize_resident_settings(
            {"max_restarts": 99})["max_restarts"])
        self.assertEqual(4, config.normalize_resident_settings(
            {"max_restarts": "4"})["max_restarts"])
        self.assertIsInstance(
            config.normalize_resident_settings({"max_restarts": 2.9})["max_restarts"],
            int,
        )

    def test_window_is_never_shorter_than_stale_other(self):
        cfg = config.normalize_resident_settings(
            {"stale_other_seconds": 300, "window_seconds": 100})
        self.assertEqual(300.0, cfg["window_seconds"])
        cfg = config.normalize_resident_settings(
            {"stale_other_seconds": 300, "window_seconds": 900})
        self.assertEqual(900.0, cfg["window_seconds"])

    def test_start_delay_is_clamped_to_0_60(self):
        self.assertEqual(0.0, config.normalize_resident_settings(
            {"start_delay_seconds": -5})["start_delay_seconds"])
        self.assertEqual(60.0, config.normalize_resident_settings(
            {"start_delay_seconds": 999})["start_delay_seconds"])
        self.assertEqual(5.0, config.normalize_resident_settings(
            {"start_delay_seconds": 5})["start_delay_seconds"])

    def test_settle_is_never_negative(self):
        self.assertEqual(0.0, config.normalize_resident_settings(
            {"settle_seconds": -30})["settle_seconds"])
        self.assertEqual(120.0, config.normalize_resident_settings(
            {"settle_seconds": 120})["settle_seconds"])

    def test_booleans_accept_common_spellings(self):
        self.assertTrue(config.normalize_resident_settings(
            {"enabled": "true"})["enabled"])
        self.assertFalse(config.normalize_resident_settings(
            {"enabled": 0})["enabled"])
        self.assertTrue(config.normalize_resident_settings(
            {"dry_run": "on"})["dry_run"])
        self.assertFalse(config.normalize_resident_settings(
            {"dry_run": ""})["dry_run"])

    def test_broken_types_fall_back_per_field(self):
        cfg = config.normalize_resident_settings({
            "enabled": True,
            "mode": "wild",
            "deck_index": 7,
            "stale_in_game_seconds": "oops",
            "max_restarts": "oops",
            "check_seconds": object(),
        })
        # 坏字段回默认，好字段保留（不能整段丢弃）。
        self.assertTrue(cfg["enabled"])
        self.assertEqual("wild", cfg["mode"])
        self.assertEqual(7, cfg["deck_index"])
        self.assertEqual(180.0, cfg["stale_in_game_seconds"])
        self.assertEqual(3, cfg["max_restarts"])
        self.assertEqual(5.0, cfg["check_seconds"])

    def test_result_contains_only_known_keys(self):
        cfg = config.normalize_resident_settings(
            {"enabled": True, "not_a_setting": 1})
        self.assertEqual(set(DEFAULTS), set(cfg))


class ResidentSettingsFileTests(unittest.TestCase):
    """resident_settings() 读临时 ui_config.json 的行为。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "ui_config.json"
        patcher = patch.object(config, "CONFIG_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, data) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf8")

    def test_missing_file_returns_defaults(self):
        self.assertEqual(DEFAULTS, config.resident_settings())

    def test_missing_section_returns_defaults(self):
        self._write({"delays": {"post_action_delay_seconds": 1}})
        self.assertEqual(DEFAULTS, config.resident_settings())

    def test_non_dict_section_returns_defaults(self):
        self._write({"resident": "oops"})
        self.assertEqual(DEFAULTS, config.resident_settings())

    def test_section_overrides_and_merges_with_defaults(self):
        self._write({"resident": {
            "enabled": True,
            "mode": "狂野",
            "deck_index": 2,
            "max_restarts": 5,
        }})
        cfg = config.resident_settings()
        self.assertTrue(cfg["enabled"])
        self.assertEqual("wild", cfg["mode"])
        self.assertEqual(2, cfg["deck_index"])
        self.assertEqual(5, cfg["max_restarts"])
        # 没写的字段回默认。
        self.assertEqual(300.0, cfg["stale_other_seconds"])
        self.assertEqual(120.0, cfg["warn_seconds"])
        self.assertEqual(120.0, cfg["settle_seconds"])

    def test_section_values_are_clamped(self):
        self._write({"resident": {
            "stale_in_game_seconds": 5,
            "stale_other_seconds": 5,
            "check_seconds": 0,
            "max_restarts": 0,
            "window_seconds": 1,
            "start_delay_seconds": 999,
        }})
        cfg = config.resident_settings()
        self.assertEqual(60.0, cfg["stale_in_game_seconds"])
        self.assertEqual(60.0, cfg["stale_other_seconds"])
        self.assertEqual(2.0, cfg["check_seconds"])
        self.assertEqual(1, cfg["max_restarts"])
        self.assertEqual(60.0, cfg["window_seconds"])
        self.assertEqual(60.0, cfg["start_delay_seconds"])

    def test_unknown_keys_are_dropped(self):
        self._write({"resident": {"enabled": True, "bogus": "x"}})
        self.assertEqual(set(DEFAULTS), set(config.resident_settings()))

    def test_each_call_rereads_the_file(self):
        # 守护设置必须"改完即时生效"，不能缓存。
        self._write({"resident": {"enabled": True}})
        self.assertTrue(config.resident_settings()["enabled"])
        self._write({"resident": {"enabled": False}})
        self.assertFalse(config.resident_settings()["enabled"])

    def test_other_sections_are_not_disturbed(self):
        self._write({"delays": {"post_action_delay_seconds": 1},
                     "resident": {"enabled": True}})
        self.assertEqual(DEFAULTS["mode"], config.resident_settings()["mode"])
        self.assertTrue(self.path.exists())


if __name__ == "__main__":
    unittest.main()
