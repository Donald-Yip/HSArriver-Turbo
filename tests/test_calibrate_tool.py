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


class CardButtonTests(unittest.TestCase):
    """目标条底部两个按钮：「保存全部区域 (S)」+「退出 (Esc)」。

    按下和抬起要落在同一个按钮上才算点中（和普通按钮一致）。
    """

    WIDTH, HEIGHT = 1920, 1080

    def setUp(self):
        self.session = make_session()

    @staticmethod
    def _center(rect):
        return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)

    def test_hit_test_finds_both_buttons(self):
        save = self.session._save_rect(self.WIDTH, self.HEIGHT)
        exit_ = self.session._exit_rect(self.WIDTH, self.HEIGHT)

        self.assertEqual(("save", None),
                         self.session._hit_test(*self._center(save),
                                                self.WIDTH, self.HEIGHT))
        self.assertEqual(("exit", None),
                         self.session._hit_test(*self._center(exit_),
                                                self.WIDTH, self.HEIGHT))

    def test_buttons_do_not_overlap_and_stay_inside_the_card(self):
        save = self.session._save_rect(self.WIDTH, self.HEIGHT)
        exit_ = self.session._exit_rect(self.WIDTH, self.HEIGHT)
        left, top, right, bottom = self.session._card_rect(self.WIDTH,
                                                           self.HEIGHT)

        self.assertLess(save[2], exit_[0])          # 保存按钮在退出按钮左边
        for rect in (save, exit_):
            self.assertGreaterEqual(rect[0], left)
            self.assertLessEqual(rect[2], right)
            self.assertGreaterEqual(rect[1], top)
            self.assertLessEqual(rect[3], bottom)

    def test_clicking_the_exit_button_closes_the_window(self):
        exit_ = self.session._exit_rect(self.WIDTH, self.HEIGHT)
        x, y = self._center(exit_)

        self.session._on_mouse_down(x, y, self.WIDTH, self.HEIGHT)
        self.session._on_mouse_up(x, y, self.WIDTH, self.HEIGHT)

        self.assertFalse(self.session.running)

    def test_pressing_save_and_releasing_on_it_saves(self):
        x, y = self._center(self.session._save_rect(self.WIDTH, self.HEIGHT))

        with patch.object(calibrate_roi.CalibrationSession, "save") as save_call:
            self.session._on_mouse_down(x, y, self.WIDTH, self.HEIGHT)
            self.session._on_mouse_up(x, y, self.WIDTH, self.HEIGHT)

        save_call.assert_called_once_with(flash=True)

    def test_press_and_release_must_match(self):
        """按在保存上、松在退出上（或反过来）都不触发任何动作。"""
        save = self._center(self.session._save_rect(self.WIDTH, self.HEIGHT))
        exit_ = self._center(self.session._exit_rect(self.WIDTH, self.HEIGHT))

        with patch.object(calibrate_roi.CalibrationSession, "save") as save_call:
            self.session._on_mouse_down(*save, self.WIDTH, self.HEIGHT)
            self.session._on_mouse_up(*exit_, self.WIDTH, self.HEIGHT)
            save_call.assert_not_called()
        self.assertTrue(self.session.running)

        self.session._on_mouse_down(*exit_, self.WIDTH, self.HEIGHT)
        self.session._on_mouse_up(*save, self.WIDTH, self.HEIGHT)
        self.assertTrue(self.session.running)

    def test_hover_tracks_the_button_for_highlighting(self):
        exit_ = self._center(self.session._exit_rect(self.WIDTH, self.HEIGHT))
        self.session.dirty = False

        self.session._on_mouse_move(*exit_, self.WIDTH, self.HEIGHT)

        self.assertEqual(("exit", None), self.session.hover)
        self.assertTrue(self.session.dirty)

    def test_both_buttons_are_painted(self):
        layer = self.session.render(self.WIDTH, self.HEIGHT)

        for rect in (self.session._save_rect(self.WIDTH, self.HEIGHT),
                     self.session._exit_rect(self.WIDTH, self.HEIGHT)):
            self.assertGreater(layer.getpixel(self._center(rect))[3], 0)


class HintWrapTests(unittest.TestCase):
    """说明自动换行：每行都在卡片内宽之内，超出行数才截断加省略号。"""

    class _Font:
        """假字体：每个字符 10px（不依赖 PIL / 字体文件）。"""

        @staticmethod
        def getlength(text):
            return 10 * len(str(text))

    def test_short_text_stays_on_one_line(self):
        self.assertEqual(["一二三"],
                         calibrate_roi.wrap_text("一二三", self._Font, 100))

    def test_empty_text_is_one_empty_line(self):
        self.assertEqual([""], calibrate_roi.wrap_text("", self._Font, 100))

    def test_long_text_wraps_within_the_width(self):
        lines = calibrate_roi.wrap_text("一二三四五六七八九十", self._Font, 45)

        self.assertGreater(len(lines), 1)
        self.assertEqual("一二三四五六七八九十", "".join(lines))
        for line in lines:
            self.assertLessEqual(self._Font.getlength(line), 45)

    def test_too_many_lines_are_truncated_with_an_ellipsis(self):
        lines = calibrate_roi.wrap_text("一" * 30, self._Font, 30, max_lines=2)

        self.assertEqual(2, len(lines))
        self.assertTrue(lines[-1].endswith("…"))
        for line in lines:
            self.assertLessEqual(self._Font.getlength(line), 30)

    def test_every_real_hint_fits_the_card(self):
        """四个目标的说明（真实字体）每一行都不许超出卡片内宽。"""
        font = calibrate_roi.label_font(calibrate_roi.HINT_FONT_SIZE)
        inner = calibrate_roi.CARD_W - 2 * calibrate_roi.CARD_PAD
        session = make_session()

        for target in session.targets:
            with self.subTest(target=target["key"]):
                lines = calibrate_roi.wrap_text(target["hint"], font, inner)

                self.assertLessEqual(len(lines),
                                     calibrate_roi.HINT_MAX_LINES)
                for line in lines:
                    self.assertNotEqual("", line)
                    self.assertLessEqual(font.getlength(line), inner + 1)

    def test_card_grows_only_for_the_wrapped_hint(self):
        session = make_session()
        session.active_index = 0
        self.assertEqual(calibrate_roi.CARD_H, session._card_height())

        # 最后那个目标的说明最长（会换行），卡片相应加高一行。
        session.active_index = len(session.targets) - 1
        self.assertGreater(session._card_height(), calibrate_roi.CARD_H)

    def test_hint_parts_split_the_highlighted_head(self):
        self.assertEqual(("框住按钮：", "检测它是否出现"),
                         calibrate_roi._hint_parts("框住按钮：检测它是否出现"))
        self.assertEqual(("框住按钮:", "检测它是否出现"),
                         calibrate_roi._hint_parts("框住按钮:检测它是否出现"))
        self.assertEqual(("", "没有冒号的说明"),
                         calibrate_roi._hint_parts("没有冒号的说明"))


if __name__ == "__main__":
    unittest.main()
