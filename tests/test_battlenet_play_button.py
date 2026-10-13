# -*- coding: utf-8 -*-
"""战网「开始游戏」点击：屏幕坐标出口 + 蓝键自动定位的回归锁。

背景（用户实机反馈「暴雪战网的按钮点不到」）：`enter_HS` 拿
`GetWindowRect` 的**屏幕坐标**去调 `left_click`，而 left_click 的入参是
**客户区相对坐标**，会被 `to_screen` 按炉石客户区再换算一遍。炉石窗口不在
屏幕原点时（窗口化/多屏），点击点整体偏移到战网窗外。本机实机复现：炉石
客户区在 (907,475)，想点 (1406,1614)，实际点 (2313,2089)。

第二层：按钮位置写死 (left+180, bottom-110)，而战网窗口大小/位置随用户
拖动变化，偏移会对不上。现在优先扫 Blizzard 蓝播放键取中心，扫不到才退回
历史偏移。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

import click
from display_mapping import DisplayMapping


def make_frame(width, height, pills=()):
    """造一张 BGRA 画面：浅灰底 + 若干蓝块。pills = [(x, y, w, h), ...]"""
    img = np.full((height, width, 4), 224, dtype="uint8")
    img[:, :, 3] = 255
    for (x, y, w, h) in pills:
        img[y:y + h, x:x + w, 0] = 255      # B
        img[y:y + h, x:x + w, 1] = 140      # G
        img[y:y + h, x:x + w, 2] = 30       # R
    return img


class EnterHsScreenCoordinateTests(unittest.TestCase):
    """回归锁：战网点击必须走屏幕坐标出口，不再被 to_screen 换算。"""

    WINDOW = (100, 50, 700, 500)            # 战网窗口 rect（屏幕坐标）

    def _run_enter_hs(self, battlenet_hwnd=1234):
        calls = []
        with patch.object(click, "rand_sleep", lambda *_: None), \
                patch.object(click, "test_hs_available", lambda: False), \
                patch.object(click, "get_battlenet_hwnd",
                             lambda: battlenet_hwnd), \
                patch.object(click, "move_window_foreground",
                             lambda *a, **k: True), \
                patch.object(click, "win32gui") as fake_win32, \
                patch.object(click, "click_button",
                             lambda *a, **k: calls.append((a, k))), \
                patch.object(click, "_bitblt_region", lambda zone: None), \
                patch.object(click, "_display_mapping",
                              lambda *a, **k: DisplayMapping(
                                  198708, (0, 0, 1920, 1080), 907, 475)):
            fake_win32.GetWindowRect.return_value = self.WINDOW
            result = click.enter_HS()
        return result, calls

    def test_clicks_the_unconverted_screen_point(self):
        """核心回归：想点 (280,390) 就点 (280,390)，不许被偏移成 (1187,865)。"""
        result, calls = self._run_enter_hs()
        self.assertTrue(result)
        self.assertEqual(1, len(calls))
        (args, kwargs), = calls
        self.assertEqual((280, 390), (args[0], args[1]))
        self.assertIs(click.Button.left, args[2])
        self.assertFalse(kwargs.get("require_hearthstone", True))

    def test_game_running_only_foregrounds(self):
        """炉石已在跑时只切前台，不点战网。"""
        with patch.object(click, "rand_sleep", lambda *_: None), \
                patch.object(click, "test_hs_available", lambda: True), \
                patch.object(click, "get_HS_hwnd", lambda: 198708), \
                patch.object(click, "move_window_foreground",
                             lambda *a, **k: True), \
                patch.object(click, "click_button") as fake_click:
            click.enter_HS()
        fake_click.assert_not_called()

    def test_missing_battlenet_returns_false(self):
        result, calls = self._run_enter_hs(battlenet_hwnd=0)
        self.assertFalse(result)
        self.assertEqual([], calls)

    def test_warns_when_the_point_is_covered(self):
        """点击点被别的窗口盖住时给警告（诊断用，不拦点击）。"""
        warnings = []
        with patch.object(click, "rand_sleep", lambda *_: None), \
                patch.object(click, "test_hs_available", lambda: False), \
                patch.object(click, "get_battlenet_hwnd", lambda: 1234), \
                patch.object(click, "move_window_foreground",
                             lambda *a, **k: True), \
                patch.object(click, "win32gui") as fake_win32, \
                patch.object(click, "click_button", lambda *a, **k: None), \
                patch.object(click, "_bitblt_region", lambda zone: None), \
                patch.object(click, "_point_targets_window",
                             lambda *a, **k: False), \
                patch.object(click, "warn_print",
                             lambda text: warnings.append(text)):
            fake_win32.GetWindowRect.return_value = self.WINDOW
            click.enter_HS()
        self.assertEqual(1, len(warnings))
        self.assertIn("被其它窗口盖住", warnings[0])


class PointTargetsWindowTests(unittest.TestCase):
    """点击命中判定：子窗口算命中、外窗算没命中、判不了不拦。"""

    def test_direct_hit(self):
        with patch.object(click.win32gui, "WindowFromPoint",
                          lambda pt: 1234), \
                patch.object(click.win32gui, "GetAncestor",
                          lambda hwnd, flags: 1234):
            self.assertTrue(click._point_targets_window(10, 20, 1234))

    def test_child_window_counts_as_hit(self):
        # 点在战网的子控件上：GetAncestor(GA_ROOT) 归并到根窗口后算命中
        with patch.object(click.win32gui, "WindowFromPoint",
                          lambda pt: 99), \
                patch.object(click.win32gui, "GetAncestor",
                          lambda hwnd, flags: 1234):
            self.assertTrue(click._point_targets_window(10, 20, 1234))

    def test_foreign_window_is_a_miss(self):
        with patch.object(click.win32gui, "WindowFromPoint",
                          lambda pt: 555), \
                patch.object(click.win32gui, "GetAncestor",
                          lambda hwnd, flags: 555):
            self.assertFalse(click._point_targets_window(10, 20, 1234))

    def test_unknown_defaults_to_allowing_the_click(self):
        with patch.object(click.win32gui, "WindowFromPoint",
                          side_effect=RuntimeError("boom")):
            self.assertTrue(click._point_targets_window(10, 20, 1234))


class BattlenetPlayPointTests(unittest.TestCase):
    """蓝键定位：扫到用中心，扫不到退回历史偏移。"""

    WINDOW = (100, 50, 700, 500)            # 600x450
    # 扫描区 = (100, 365, 370, 500) → 270x135

    def test_blue_button_center_is_used(self):
        pill = (60, 40, 160, 48)            # 区内的实心蓝块
        frame = make_frame(270, 135, pills=[pill])
        point = click.battlenet_play_point(
            *self.WINDOW, grabber=lambda zone: frame)
        # 块中心（区内 (140,64)）→ 屏幕 (240, 429)
        self.assertEqual((240, 429), point)

    def test_no_blue_falls_back_to_legacy_offset(self):
        frame = make_frame(270, 135)        # 一片浅灰
        point = click.battlenet_play_point(
            *self.WINDOW, grabber=lambda zone: frame)
        self.assertEqual((280, 390), point)

    def test_tiny_blue_blob_is_ignored(self):
        # 6x5=30px² < 1500，多半是图标/链接，不能当播放键
        frame = make_frame(270, 135, pills=[(10, 10, 6, 5)])
        point = click.battlenet_play_point(
            *self.WINDOW, grabber=lambda zone: frame)
        self.assertEqual((280, 390), point)

    def test_grabber_failure_falls_back(self):
        point = click.battlenet_play_point(
            *self.WINDOW, grabber=lambda zone: None)
        self.assertEqual((280, 390), point)

    def test_largest_blob_wins(self):
        # 大块在右侧、小块在左侧：取面积最大的
        frame = make_frame(270, 135,
                           pills=[(10, 10, 8, 8), (90, 50, 160, 48)])
        point = click.battlenet_play_point(
            *self.WINDOW, grabber=lambda zone: frame)
        # 大块中心（区内 (170,74)）→ 屏幕 (270, 439)
        self.assertEqual((270, 439), point)

    def test_degenerate_window_falls_back(self):
        point = click.battlenet_play_point(
            100, 50, 100, 50, grabber=lambda zone: make_frame(10, 10))
        self.assertEqual((280, -60), point)


if __name__ == "__main__":
    unittest.main()
