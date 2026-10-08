# -*- coding: utf-8 -*-
"""校准工具（calibrate_roi）的纯逻辑：目标切换、命中判定、保存、渲染。

不建真窗口：只验证拖框/保存/绘制这些可以离屏测的部分。
"""

import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import calibrate_roi
import screen_regions


def make_temp_dir() -> Path:
    """建一个普通权限的临时目录。

    不用 tempfile.TemporaryDirectory：它会把目录设成 0700，在受限环境里
    反而写不进去。这里自己 mkdir 再自己删。
    """
    path = Path(tempfile.gettempdir()) / f"hs_calib_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def make_session(**kwargs):
    session = calibrate_roi.CalibrationSession(selftest=True, ocr_enabled=False,
                                               **kwargs)
    session.width, session.height = 1920, 1080
    return session


class TargetTests(unittest.TestCase):
    def test_every_registered_target_is_editable(self):
        session = make_session()

        self.assertEqual(4, len(session.targets))
        self.assertEqual("recommendation", session.active["key"])
        for target in session.targets:
            self.assertEqual(4, len(target["box"]))
        self.assertIn("post_game_start_roi",
                      [target["config_key"] for target in session.targets])

    def test_start_key_selects_that_target(self):
        session = make_session(start_key="win_rate")

        self.assertEqual("win_rate", session.active["key"])

    def test_unknown_start_key_falls_back_to_the_first(self):
        session = make_session(start_key="nope")

        self.assertEqual("recommendation", session.active["key"])

    def test_cycle_wraps_around(self):
        session = make_session()
        session.select(0)
        session.cycle(1)
        self.assertEqual(1, session.active_index)
        session.select(len(session.targets) - 1)
        session.cycle(1)
        self.assertEqual(0, session.active_index)

    def test_select_ignores_out_of_range(self):
        session = make_session()
        session.select(0)
        session.select(99)
        session.select(-1)

        self.assertEqual(0, session.active_index)


class HitTestTests(unittest.TestCase):
    WIDTH, HEIGHT = 1920, 1080

    def setUp(self):
        self.session = make_session()
        self.session.targets[0]["box"] = [100, 200, 300, 500]
        self.session.active_index = 0

    def test_finds_the_resize_handle_and_the_frame(self):
        self.assertEqual(("corner", None),
                         self.session._hit_test(306, 506, self.WIDTH,
                                                self.HEIGHT))
        self.assertEqual(("edge", None),
                         self.session._hit_test(105, 350, self.WIDTH,
                                                self.HEIGHT))

    def test_finds_the_target_rows_and_the_save_button(self):
        rect, index = self.session._chip_rects(self.WIDTH, self.HEIGHT)[1]
        self.assertEqual(("target", 1),
                         self.session._hit_test((rect[0] + rect[2]) // 2,
                                                (rect[1] + rect[3]) // 2,
                                                self.WIDTH, self.HEIGHT))
        save = self.session._save_rect(self.WIDTH, self.HEIGHT)
        self.assertEqual(("save", None),
                         self.session._hit_test((save[0] + save[2]) // 2,
                                                (save[1] + save[3]) // 2,
                                                self.WIDTH, self.HEIGHT))

    def test_empty_screen_area_is_click_through(self):
        self.assertIsNone(self.session._hit_test(1500, 900, self.WIDTH,
                                                 self.HEIGHT))

    def test_the_target_card_never_covers_the_win_rate_box(self):
        """目标条放在正中央：左上角的 AI胜率浮动条必须露出来。"""
        card = calibrate_roi.card_rect(self.WIDTH, self.HEIGHT)
        for box in (screen_regions.AI_WIN_RATE_REGION,
                    screen_regions.AI_WIN_RATE_WIDE_REGION):
            overlaps = (box[0] < card[2] and box[2] > card[0]
                        and box[1] < card[3] and box[3] > card[1])
            self.assertFalse(overlaps, f"目标条压住了 AI胜率区域 {box}")

    def test_the_target_card_is_centred_on_the_screen(self):
        left, top, right, bottom = calibrate_roi.card_rect(self.WIDTH,
                                                           self.HEIGHT)

        self.assertAlmostEqual(self.WIDTH / 2, (left + right) / 2, delta=1)
        self.assertAlmostEqual(self.HEIGHT / 2, (top + bottom) / 2, delta=1)

    def test_the_target_card_stays_on_a_tiny_screen(self):
        left, top, _, _ = calibrate_roi.card_rect(320, 200)

        # 屏幕比卡片还小时贴左上角，不出现负数坐标
        self.assertEqual(8, left)
        self.assertEqual(8, top)

    def test_moving_and_resizing_only_touch_the_active_target(self):
        session = self.session
        other = list(session.targets[1]["box"])
        session.drag = ("move", 10, 20)
        session._on_mouse_move(500, 400, 1920, 1080)

        self.assertEqual([490, 380, 690, 680], session.targets[0]["box"])
        self.assertEqual(other, session.targets[1]["box"])

    def test_resize_keeps_the_minimum_size(self):
        self.session.targets[0]["box"] = [100, 200, 300, 500]
        self.session.drag = ("resize",)
        self.session._on_mouse_move(120, 210, 1920, 1080)

        self.assertEqual([100, 200, 160, 260], self.session.targets[0]["box"])

    def test_boxes_never_leave_the_screen(self):
        self.session.targets[0]["box"] = [1800, 1000, 1900, 1080]
        self.session.drag = ("resize",)
        self.session._on_mouse_move(5000, 5000, 1920, 1080)

        self.assertEqual([1800, 1000, 1920, 1080],
                         self.session.targets[0]["box"])


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.path = self.tmp / "ui_config.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_writes_every_target_and_keeps_other_settings(self):
        self.path.write_text(json.dumps({"name": "TestUser#12345",
                                         "log_root": "D:\\Logs"},
                                        ensure_ascii=False), encoding="utf-8")
        session = make_session()
        session.targets[0]["box"] = [10, 200, 210, 520]
        session.targets[2]["box"] = [110, 8, 270, 48]
        with patch.object(calibrate_roi, "_user_config_path",
                          return_value=self.path):
            self.assertTrue(session.save(flash=True))

        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual([10, 200, 210, 520], data["recommendation_roi"])
        self.assertEqual([110, 8, 270, 48], data["ai_win_rate_roi"])
        # 兜底区域永远跟着主区域外扩（不给用户单独拖）。
        self.assertEqual([95, 0, 300, 60], data["ai_win_rate_wide_roi"])
        # 其它配置原样保留，不被校准结果覆盖。
        self.assertEqual("TestUser#12345", data["name"])
        self.assertEqual("D:\\Logs", data["log_root"])
        self.assertGreater(session.saved_flash_until, 0)

    def test_save_refuses_to_write_boxes_outside_the_screen(self):
        session = make_session()
        session.targets[0]["box"] = [3000, 3000, 3200, 3200]
        with patch.object(calibrate_roi, "_user_config_path",
                          return_value=self.path):
            self.assertTrue(session.save())

        data = json.loads(self.path.read_text(encoding="utf-8"))
        # 收进屏幕内，但尺寸（200×200）保持不变
        self.assertEqual([1720, 880, 1920, 1080], data["recommendation_roi"])

    def test_save_keeps_small_boxes_small(self):
        """AI胜率浮动条比推荐面板小得多，保存时不许被最小尺寸撑大。"""
        session = make_session()
        session.targets[2]["box"] = [110, 8, 270, 48]
        with patch.object(calibrate_roi, "_user_config_path",
                          return_value=self.path):
            self.assertTrue(session.save())

        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual([110, 8, 270, 48], data["ai_win_rate_roi"])

    def test_save_reports_a_write_failure(self):
        session = make_session()
        missing = self.tmp / "no_such_dir" / "ui_config.json"
        with patch.object(calibrate_roi, "_user_config_path",
                          return_value=missing):
            self.assertFalse(session.save())


class RenderTests(unittest.TestCase):
    def test_render_draws_the_overlay_and_the_target_card(self):
        session = make_session()
        layer = session.render(900, 700)

        self.assertEqual("RGBA", layer.mode)
        self.assertEqual((900, 700), layer.size)
        # 正中央的目标条必须画出来（不透明），屏幕右下角保持透明
        card = calibrate_roi.card_rect(900, 700)
        self.assertGreater(layer.getpixel(((card[0] + card[2]) // 2,
                                           (card[1] + card[3]) // 2))[3], 0)
        self.assertEqual(0, layer.getpixel((899, 699))[3])

    def test_render_leaves_the_win_rate_box_visible(self):
        """AI胜率浮动条那一块不能被目标条盖住（哪怕是透明像素也要看得见）。"""
        session = make_session()
        session.targets[2]["box"] = list(screen_regions.AI_WIN_RATE_REGION)
        layer = session.render(1920, 1080)

        box = screen_regions.AI_WIN_RATE_REGION
        center = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
        card = calibrate_roi.card_rect(1920, 1080)
        inside_card = (card[0] <= center[0] <= card[2]
                       and card[1] <= center[1] <= card[3])
        self.assertFalse(inside_card, "目标条压住 AI胜率浮动条了")
        # 中心点属于框内部（未被粗框/卡片涂实），透明才能看见底下的游戏画面
        self.assertEqual(0, layer.getpixel(center)[3])

    def test_preview_config_follows_the_in_progress_boxes(self):
        session = make_session()
        session.targets[0]["box"] = [11, 22, 233, 344]
        session.targets[2]["box"] = [110, 8, 270, 48]

        config = session._preview_config()

        self.assertEqual((11, 22, 233, 344), config.recommendation_roi)
        self.assertEqual((110, 8, 270, 48), config.ai_win_rate_roi)
        self.assertEqual((95, 0, 300, 60), config.ai_win_rate_wide_roi)


if __name__ == "__main__":
    unittest.main()
