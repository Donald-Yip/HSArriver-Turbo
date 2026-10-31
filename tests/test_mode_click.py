# -*- coding: utf-8 -*-
"""模式切换点击点：坐标来源、覆盖、以及"别又写回盾牌下面"的回归锁。

背景：`hsctl mode wild|standard|casual` 是"点右上角模式按钮 → 点三个盾牌之一"。
最早这三个点写死在盾牌**下方**的卷轴/横幅上（y=590/700），点了没反应还顺手点到
别的东西。这里把实测坐标（2026-10 实机截图）锁住：三个点必须落在盾牌的安全
水平带里（y∈[330,430]），且与实测盾牌中心 x 偏差 ≤10px；ui_config.json 的
`mode_click` 段可以覆盖，格式与语义不变（[x, y]）。
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


class ModePointSourceTests(unittest.TestCase):
    def test_shield_points_stay_on_the_measured_shields(self):
        """回归锁：y 再被写回 590/700 之类的值就会红。"""
        points = config.default_mode_click_points()
        self.assertEqual(screen_regions.mode_click_points(), points)

        for key, (cx, _cy) in SHIELD_CENTERS.items():
            with self.subTest(shield=key):
                x, y = points[key]
                self.assertGreaterEqual(y, SAFE_BAND[0])
                self.assertLessEqual(y, SAFE_BAND[1])
                self.assertLessEqual(abs(x - cx), 10,
                                     f"{key} 的 x 离实测盾牌中心太远")

    def test_boxes_and_calibration_targets_center_on_the_points(self):
        for point_key, point in screen_regions.MODE_POINTS.items():
            with self.subTest(point=point_key):
                left, top, right, bottom = screen_regions.mode_point_box(point)
                self.assertEqual(point[0], (left + right) // 2)
                self.assertEqual(point[1], (top + bottom) // 2)
                self.assertEqual(screen_regions.MODE_MARK_SIZE, right - left)
                self.assertEqual(screen_regions.MODE_MARK_SIZE, bottom - top)

        # 隔离 ui_config.json：本机覆盖不该让"默认值是否一致"变红。
        tmp = make_temp_dir()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        original = config.CONFIG_PATH
        config.CONFIG_PATH = tmp / "ui_config.json"
        self.addCleanup(lambda: setattr(config, "CONFIG_PATH", original))

        targets = {t["key"]: t for t in screen_regions.calibration_targets()}
        for point_key, point in screen_regions.MODE_POINTS.items():
            with self.subTest(target=f"mode_{point_key}"):
                box = targets[f"mode_{point_key}"]["box"]
                self.assertEqual(
                    point, ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2))


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

    def test_override_wins_and_click_module_shares_it(self):
        import click as hs_click

        self._write({"mode_click": {"wild": [123, 456]}})
        points = config.load_mode_click_points()

        self.assertEqual((123.0, 456.0), points["wild"])
        # 没覆盖的项仍然是默认值；click 模块走同一来源。
        self.assertEqual(config.default_mode_click_points()["casual"],
                         points["casual"])
        self.assertEqual(config.load_mode_click_points(),
                         hs_click.mode_click_points())

    def test_missing_bad_or_wrong_type_sections_fall_back(self):
        cases = (
            ("missing", None),
            ("bad values", {"mode_click": {"wild": [1, 2, 3],
                                           "standard": "nope",
                                           "casual": [None, 4]}}),
            ("wrong type", {"mode_click": ["wild", 1]}),
        )
        for label, payload in cases:
            with self.subTest(case=label):
                if payload is not None:
                    self._write(payload)
                self.assertEqual(config.default_mode_click_points(),
                                 config.load_mode_click_points())


class BattleClickPointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = self.tmp / "ui_config.json"
        self.original = config.CONFIG_PATH
        config.CONFIG_PATH = self.path
        self.addCleanup(lambda: setattr(config, "CONFIG_PATH", self.original))

    def test_secondary_override_wins_immediately(self):
        import click as hs_click

        self.path.write_text(json.dumps({
            "battle_click": {"hero_power_secondary": [1200, 700]}
        }, ensure_ascii=False), encoding="utf8")

        points = config.load_battle_click_points()

        self.assertEqual((1200.0, 700.0), points["hero_power_secondary"])
        self.assertEqual(
            config.default_battle_click_points()["hero_power_primary"],
            points["hero_power_primary"])
        self.assertEqual((1200.0, 700.0),
                         hs_click.hero_power_point("secondary"))


if __name__ == "__main__":
    unittest.main()
