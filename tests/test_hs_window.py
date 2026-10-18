# -*- coding: utf-8 -*-
"""hs_window：16:9 宽度换算与收敛（纯逻辑，不碰真窗口）。

滑条范围/步进的"真值"就在这里；这些用例锁住：
  * 基准 1920×1080 是严格 16:9；
  * 宽度收敛进 960~3840 并对齐 16 的步进（→ 高度正好是 9 的倍数）；
  * 收敛后的高度**不低于**炉石的最小客户区高度（400），否则游戏会弹回。
"""

import unittest

import hs_window


class HeightForWidthTests(unittest.TestCase):
    def test_known_pairs(self):
        self.assertEqual(540, hs_window.height_for_width(960))
        self.assertEqual(1080, hs_window.height_for_width(1920))
        self.assertEqual(405, hs_window.height_for_width(720))
        self.assertEqual(2160, hs_window.height_for_width(3840))

    def test_reference_is_exactly_16_9(self):
        w, h = hs_window.REFERENCE
        self.assertEqual(w * 9, h * 16)

    def test_min_is_half_of_the_reference(self):
        # 滑条下限 = 基准的一半 = 960×540（再小固定像素的盒子面板挤不下）。
        self.assertEqual(960, hs_window.MIN_16_9_WIDTH)
        self.assertEqual(540, hs_window.height_for_width(960))


class ClampWidthTests(unittest.TestCase):
    def test_value_in_range_is_kept_when_aligned(self):
        self.assertEqual(960, hs_window.clamp_width(960))

    def test_snaps_down_to_the_step(self):
        # 965 → 960（720 + 15×16）
        self.assertEqual(960, hs_window.clamp_width(965))

    def test_below_minimum_goes_to_minimum(self):
        self.assertEqual(hs_window.MIN_16_9_WIDTH, hs_window.clamp_width(0))
        self.assertEqual(hs_window.MIN_16_9_WIDTH, hs_window.clamp_width(719))

    def test_above_maximum_goes_to_maximum(self):
        self.assertEqual(hs_window.MAX_16_9_WIDTH, hs_window.clamp_width(99999))

    def test_garbage_falls_back_to_minimum(self):
        self.assertEqual(hs_window.MIN_16_9_WIDTH, hs_window.clamp_width("nope"))

    def test_every_clamped_width_is_aligned_and_tall_enough(self):
        for raw in range(0, 6000, 37):
            clamped = hs_window.clamp_width(raw)
            self.assertEqual(
                0,
                (clamped - hs_window.MIN_16_9_WIDTH) % hs_window.STEP_16_9,
                f"{raw} → {clamped} 没有对齐步进",
            )
            self.assertGreaterEqual(
                hs_window.height_for_width(clamped),
                hs_window.MIN_CLIENT_HEIGHT,
                f"{raw} → {clamped} 的高度低于炉石最小客户区，会被弹回",
            )


if __name__ == "__main__":
    unittest.main()
