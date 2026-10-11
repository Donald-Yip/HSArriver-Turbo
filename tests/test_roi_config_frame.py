# -*- coding: utf-8 -*-
"""ui_config.json 的 ROI 口径：读取时把"屏幕坐标 / 客户区相对"判对。

背景（用户报告"校准框全跑偏、还不跟着炉石窗口动"）：老配置、以及**在炉石
最小化时按过保存**的配置，里面存的实际是**屏幕坐标**，但文件可能写着
``roi_frame="client"``。如果直接当客户区相对坐标用，所有框都会被整体偏移 +
缩放一次（跑到屏幕外、还拖不动）。`config._user_box()` 负责在读取时判对口径，
且必须与校准工具 `calibrate_roi` 完全一致（同一个
`display_mapping.interpret_roi_box`）。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import config
import display_mapping as dm

# 本机实测：客户区 1920×1080，左上角在屏幕 (958,320)。
ORIGIN = (958, 320)
# 用户真实踩坑的那份配置：文件写着 client，值其实是屏幕坐标。
POISONED = {
    "roi_frame": "client",
    "recommendation_roi": [960, 551, 1208, 843],
    "mulligan_confirm_roi": [1612, 1029, 1815, 1109],
    "ai_win_rate_roi": [1037, 363, 1197, 403],
    "ai_win_rate_wide_roi": [1022, 355, 1227, 415],
    "post_game_start_roi": [2276, 1200, 2442, 1295],
}


def make_temp_dir() -> Path:
    """建一个普通权限的临时目录（TemporaryDirectory 在受限环境里会写不进去）。"""
    path = Path(tempfile.gettempdir()) / f"hs_roi_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def mapping(origin=ORIGIN, client=(0, 0, 1920, 1080)):
    return dm.DisplayMapping(hwnd=1, client=tuple(client),
                            screen_left=origin[0], screen_top=origin[1])


class RoiFrameDetectionTests(unittest.TestCase):
    """`config._roi_declared_client`：文件里的标记可不可信。"""

    def test_missing_marker_is_not_a_client_declaration(self):
        self.assertFalse(config._roi_declared_client({}))

    def test_screen_marker_is_not_a_client_declaration(self):
        self.assertFalse(config._roi_declared_client({"roi_frame": "screen"}))

    def test_credible_client_marker_is_accepted(self):
        data = {"roi_frame": "client",
                "recommendation_roi": [2, 231, 250, 523],
                "post_game_rank_roi": [1230, 180, 1385, 225]}

        self.assertTrue(config._roi_declared_client(data))

    def test_client_marker_with_impossible_values_is_rejected(self):
        self.assertFalse(config._roi_declared_client(POISONED))

    def test_client_marker_without_boxes_is_accepted(self):
        self.assertTrue(config._roi_declared_client({"roi_frame": "client"}))


class UserBoxInterpretationTests(unittest.TestCase):
    """`config._user_box()` 必须返回**客户区相对坐标**，且位置不能变。"""

    def setUp(self):
        self.tmp = make_temp_dir()
        self.path = self.tmp / "ui_config.json"
        self._warned = config._ROI_FRAME_WARNED
        config._ROI_FRAME_WARNED = True          # 静音提示（内容由别的测试覆盖）
        self._patches = [
            patch.object(config, "ROOT", self.tmp),
            patch.object(dm, "display_mapping", return_value=mapping()),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in self._patches:
            item.stop()
        config._ROI_FRAME_WARNED = self._warned
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, data: dict):
        self.path.write_text(json.dumps(data, ensure_ascii=False),
                             encoding="utf-8")

    def test_poisoned_config_is_converted_so_positions_do_not_move(self):
        """假 client 标记（实为屏幕坐标）：读数换算后屏幕位置与原值一致。"""
        self._write(POISONED)
        live = mapping()

        for key, stored in POISONED.items():
            if key == "roi_frame":
                continue
            with self.subTest(key=key):
                client_box = config._user_box(key)
                self.assertIsNotNone(client_box)
                self.assertEqual(tuple(stored),
                                 live.box_to_screen(client_box),
                                 "换算后屏幕位置必须与用户校准时一致")

    def test_poisoned_config_stays_self_consistent_for_the_wide_box(self):
        """兜底框 = 主区域外扩：换算后仍然成立（说明口径判对了）。"""
        import screen_regions
        self._write(POISONED)

        main = config._user_box("ai_win_rate_roi")
        wide = config._user_box("ai_win_rate_wide_roi")

        self.assertEqual(
            screen_regions.expand_box(main, screen_regions.AI_WIN_RATE_WIDE_MARGIN),
            wide)

    def test_code_default_value_is_not_converted(self):
        """配置里写了和代码默认值一样的值：它本来就是客户区相对口径。"""
        default = (7, 200, 202, 500)
        self._write({"recommendation_roi": list(default)})

        self.assertEqual(default,
                         config._user_box("recommendation_roi", default))

    def test_trusted_client_config_is_returned_as_is(self):
        box = (2, 231, 250, 523)
        self._write({"roi_frame": "client", "recommendation_roi": list(box)})

        self.assertEqual(box, config._user_box("recommendation_roi",
                                               (7, 200, 202, 500)))

    def test_legacy_custom_box_that_looks_like_screen_coords_is_converted(self):
        box = (960, 551, 1208, 843)
        self._write({"recommendation_roi": list(box)})

        client_box = config._user_box("recommendation_roi", (7, 200, 202, 500))

        self.assertEqual(box, mapping().box_to_screen(client_box))

    def test_legacy_box_outside_the_client_area_is_kept(self):
        """在客户区之外 → 不可能是屏幕坐标，原样保留（框跟着窗口走）。"""
        box = (5, 5, 105, 105)
        self._write({"recommendation_roi": list(box)})

        self.assertEqual(box, config._user_box("recommendation_roi",
                                               (7, 200, 202, 500)))

    def test_invalid_boxes_are_ignored(self):
        self._write({"recommendation_roi": [10, 10, 5, 5],
                     "post_game_rank_roi": "nope"})

        self.assertIsNone(config._user_box("recommendation_roi", (7, 200, 202, 500)))
        self.assertIsNone(config._user_box("post_game_rank_roi",
                                           (1230, 180, 1385, 225)))

    def test_without_a_client_area_the_value_is_returned_as_is(self):
        """炉石没开（拿不到几何）时不换算：与改造前行为一致，风险最小。"""
        box = (960, 551, 1208, 843)
        self._write(POISONED)
        with patch.object(dm, "display_mapping", return_value=None):
            self.assertEqual(tuple(box),
                             config._user_box("recommendation_roi",
                                              (7, 200, 202, 500)))


class RecommendationConfigIntegrationTests(unittest.TestCase):
    """`RecommendationConfig()` 读盘后拿到的必须是"能用"的客户区相对坐标。"""

    def setUp(self):
        self.tmp = make_temp_dir()
        self._warned = config._ROI_FRAME_WARNED
        config._ROI_FRAME_WARNED = True
        (self.tmp / "ui_config.json").write_text(
            json.dumps(POISONED, ensure_ascii=False), encoding="utf-8")
        self._patches = [
            patch.object(config, "ROOT", self.tmp),
            patch.object(dm, "display_mapping", return_value=mapping()),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in self._patches:
            item.stop()
        config._ROI_FRAME_WARNED = self._warned
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_config_ends_up_with_client_relative_boxes(self):
        cfg = config.RecommendationConfig()
        live = mapping()

        self.assertEqual((2, 231, 250, 523), tuple(cfg.recommendation_roi))
        # 各消费者仍然按"客户区相对 + mapping"取值：屏幕位置必须还原成用户
        # 校准时的那一批像素。
        self.assertEqual((960, 551, 1208, 843),
                         dm.box_to_screen(cfg.recommendation_roi, live))
        self.assertEqual((2276, 1200, 2442, 1295),
                         dm.box_to_screen(cfg.post_game_start_roi, live))

    def test_the_rank_box_falls_back_to_the_code_default(self):
        cfg = config.RecommendationConfig()

        self.assertEqual((1230, 180, 1385, 225), tuple(cfg.post_game_rank_roi))
        self.assertIn("post_game_rank_roi", config._USER_BOX_KEYS)


if __name__ == "__main__":
    unittest.main()
