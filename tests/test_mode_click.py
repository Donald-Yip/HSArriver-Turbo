# -*- coding: utf-8 -*-
"""模式切换点击点：坐标来源、覆盖、以及"别又写回盾牌下面"的回归锁。

背景：`hsctl mode wild|standard|casual` 是"点右上角模式按钮 → 点三个盾牌之一"。
最早这三个点写死在盾牌**下方**的卷轴/横幅上（y=590/700），点了没反应还顺手点到
别的东西。这里把实测坐标（2026-10 实机截图）锁住：

    * 唯一来源 = screen_regions.MODE_POINTS；
    * 三个点必须落在盾牌的安全水平带里（y∈[330,430]）；
    * 与实测的盾牌中心 x 偏差 ≤10px；
    * ui_config.json 的 `mode_click` 段可以覆盖，格式与语义不变（[x, y]）。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

import config
import screen_regions


def make_temp_dir() -> Path:
    """建一个普通权限的临时目录。

    **不要用 tempfile.TemporaryDirectory/mkdtemp**：它把目录设成 0700，在受限
    环境（本仓库的测试沙箱）里反而往里面写不了文件；普通 mkdir 出来的目录可以。
    """
    path = Path(tempfile.gettempdir()) / f"hs_mode_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


# 实机截图量出来的三个盾牌中心（x 用左右镜像对称度求，见 screen_regions 注释）。
SHIELD_CENTERS = {"wild": (617, 380), "standard": (955, 375),
                  "casual": (1290, 380)}
SAFE_BAND = (330, 430)      # 三个盾牌都覆盖这条水平带
MENU_OPEN = (1405, 55)


class ModePointSourceTests(unittest.TestCase):
    def test_defaults_come_from_screen_regions(self):
        self.assertEqual(screen_regions.mode_click_points(),
                         config.default_mode_click_points())

    def test_expected_points(self):
        points = config.default_mode_click_points()
        self.assertEqual(MENU_OPEN, points["menu_open"])
        for key, expected in SHIELD_CENTERS.items():
            self.assertEqual(expected, points[key])

    def test_points_are_on_the_shields(self):
        """回归锁：y 再被写回 590/700 之类的值就会红。"""
        points = config.default_mode_click_points()
        for key, (cx, cy) in SHIELD_CENTERS.items():
            x, y = points[key]
            self.assertGreaterEqual(y, SAFE_BAND[0])
            self.assertLessEqual(y, SAFE_BAND[1])
            self.assertLessEqual(abs(x - cx), 10,
                                 f"{key} 的 x 离实测盾牌中心太远")

    def test_boxes_are_centred_on_the_points(self):
        for point_key, point in screen_regions.MODE_POINTS.items():
            left, top, right, bottom = screen_regions.mode_point_box(point)
            self.assertEqual((left + right) // 2, point[0])
            self.assertEqual((top + bottom) // 2, point[1])
            self.assertEqual(screen_regions.MODE_MARK_SIZE, right - left)
            self.assertEqual(screen_regions.MODE_MARK_SIZE, bottom - top)

    def test_targets_use_the_same_points(self):
        """校准目标里 mode_* 的框中心 = MODE_POINTS 的点（默认口径，不受本机覆盖影响）。

        必须隔离 ui_config.json：本机的 `mode_click` 覆盖（用户拖过框）不该让
        这条"默认值是否一致"的测试变红。
        """
        tmp = make_temp_dir()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        original = config.CONFIG_PATH
        config.CONFIG_PATH = tmp / "ui_config.json"
        self.addCleanup(lambda: setattr(config, "CONFIG_PATH", original))

        targets = {t["key"]: t for t in screen_regions.calibration_targets()}
        for point_key, point in screen_regions.MODE_POINTS.items():
            target = targets[f"mode_{point_key}"]
            box = target["box"]
            self.assertEqual(point,
                             ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2))


class ModeClickOverrideTests(unittest.TestCase):
    """`mode_click` 段的读取行为（schema 与宽容度都不能变）。"""

    def setUp(self):
        self._tmp = make_temp_dir()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.path = self._tmp / "ui_config.json"
        self._original = config.CONFIG_PATH
        config.CONFIG_PATH = self.path
        self.addCleanup(self._restore)

    def _restore(self):
        config.CONFIG_PATH = self._original

    def _write(self, payload):
        self.path.write_text(json.dumps(payload, ensure_ascii=False),
                             encoding="utf8")

    def test_defaults_when_no_section(self):
        self.assertEqual(config.default_mode_click_points(),
                         config.load_mode_click_points())

    def test_override_wins(self):
        self._write({"mode_click": {"wild": [123, 456]}})
        points = config.load_mode_click_points()

        self.assertEqual((123.0, 456.0), points["wild"])
        # 没覆盖的项仍然是默认值
        self.assertEqual(config.default_mode_click_points()["casual"],
                         points["casual"])

    def test_bad_values_fall_back_to_defaults(self):
        self._write({"mode_click": {"wild": [1, 2, 3], "standard": "nope",
                                    "casual": [None, 4]}})
        points = config.load_mode_click_points()

        self.assertEqual(config.default_mode_click_points(), points)

    def test_section_of_wrong_type_is_ignored(self):
        self._write({"mode_click": ["wild", 1]})

        self.assertEqual(config.default_mode_click_points(),
                         config.load_mode_click_points())

    def test_click_module_reads_the_same_points(self):
        """click.click_mode_points() 必须和 config 读到的完全一致（同一来源）。"""
        import click as hs_click

        self._write({"mode_click": {"standard": [700, 400]}})

        self.assertEqual(config.load_mode_click_points(),
                         hs_click.mode_click_points())


if __name__ == "__main__":
    unittest.main()
