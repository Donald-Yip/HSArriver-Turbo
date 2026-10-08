# -*- coding: utf-8 -*-
"""截图区域清单与区域框预览：坐标来源、越界判定、预览图生成。"""

import base64
import io
import unittest
from types import SimpleNamespace

from PIL import Image

import region_overlay
import screen_regions


def make_config(**overrides):
    base = dict(desktop_size=(1920, 1080), desktop_dpi=96,
                recommendation_roi=(7, 200, 202, 500),
                mulligan_confirm_roi=(860, 810, 1060, 890))
    base.update(overrides)
    return SimpleNamespace(**base)


def make_grabber(size=(1920, 1080), color=(18, 22, 30)):
    def grab():
        return Image.new("RGB", size, color)
    return grab


class RegionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.regions = screen_regions.screenshot_regions(make_config())

    def test_lists_every_screenshot_region(self):
        self.assertEqual(
            ["recommendation", "mulligan_confirm", "win_rate", "win_rate_wide",
             "post_game_start", "rank"],
            [region["key"] for region in self.regions])

    def test_boxes_come_from_the_config(self):
        boxes = {region["key"]: region["box"] for region in self.regions}

        self.assertEqual((7, 200, 202, 500), boxes["recommendation"])
        self.assertEqual((860, 810, 1060, 890), boxes["mulligan_confirm"])

    def test_boxes_are_well_formed_and_keys_unique(self):
        for region in self.regions:
            left, top, right, bottom = region["box"]
            self.assertLess(left, right)
            self.assertLess(top, bottom)
            self.assertRegex(region["color"], r"^#[0-9a-fA-F]{6}$")
            self.assertTrue(region["label"])
            self.assertTrue(region["note"])
        self.assertEqual(len(self.regions),
                         len({region["key"] for region in self.regions}))

    def test_invalid_config_box_falls_back_to_default(self):
        regions = screen_regions.screenshot_regions(
            make_config(recommendation_roi=(10, 10, 5, 5)))

        box = next(r["box"] for r in regions if r["key"] == "recommendation")
        self.assertEqual((7, 200, 202, 500), box)

    def test_win_rate_regions_match_the_automation(self):
        """预览画的框必须和自动投降实际截的区域一模一样。"""
        import FSM_action

        self.assertEqual(FSM_action._AI_WIN_RATE_REGIONS[0],
                         screen_regions.AI_WIN_RATE_REGION)
        self.assertEqual(FSM_action._AI_WIN_RATE_REGIONS[1],
                         screen_regions.AI_WIN_RATE_WIDE_REGION)

    def test_post_game_regions_match_the_automation(self):
        """每局结束用的「开始」按钮 / 段位框必须和 FSM_action 读的区域一致。"""
        import FSM_action

        self.assertEqual(FSM_action._POST_GAME_START_BUTTON_ROI,
                         screen_regions.POST_GAME_START_BUTTON_REGION)
        self.assertEqual(FSM_action._POST_GAME_RANK_ROI,
                         screen_regions.POST_GAME_RANK_REGION)
        boxes = {region["key"]: region["box"]
                 for region in screen_regions.screenshot_regions(make_config())}
        self.assertEqual(screen_regions.POST_GAME_START_BUTTON_REGION,
                         boxes["post_game_start"])
        self.assertEqual(screen_regions.POST_GAME_RANK_REGION, boxes["rank"])

    def test_state_probe_points_are_reported(self):
        points = screen_regions.state_probe_points()

        self.assertEqual(3, len(points))
        self.assertIn((1090, 1070), [p["point"] for p in points])


class CalibrationTargetTests(unittest.TestCase):
    """可校准区域清单：校准工具、叠加层、AI胜率读取共用这一份来源。"""

    def test_lists_the_calibratable_targets(self):
        targets = screen_regions.calibration_targets(make_config())

        self.assertEqual(["recommendation", "mulligan_confirm", "win_rate",
                          "post_game_start"],
                         [target["key"] for target in targets])
        self.assertEqual(
            ["recommendation_roi", "mulligan_confirm_roi", "ai_win_rate_roi",
             "post_game_start_roi"],
            [target["config_key"] for target in targets])
        for target in targets:
            self.assertTrue(target["label"])
            self.assertTrue(target["short"])
            self.assertTrue(target["hint"])
            self.assertRegex(target["color"], r"^#[0-9a-fA-F]{6}$")

    def test_calibrated_post_game_start_box_wins_over_the_default(self):
        targets = screen_regions.calibration_targets(
            make_config(post_game_start_roi=(1500, 850, 1600, 950)))

        box = next(t["box"] for t in targets if t["key"] == "post_game_start")
        regions = screen_regions.screenshot_regions(
            make_config(post_game_start_roi=(1500, 850, 1600, 950)))
        preview = next(r["box"] for r in regions
                       if r["key"] == "post_game_start")
        self.assertEqual((1500, 850, 1600, 950), box)
        self.assertEqual((1500, 850, 1600, 950), preview)

    def test_boxes_come_from_the_config_with_defaults(self):
        boxes = {target["key"]: target["box"]
                 for target in screen_regions.calibration_targets(make_config())}

        self.assertEqual((7, 200, 202, 500), boxes["recommendation"])
        self.assertEqual((860, 810, 1060, 890), boxes["mulligan_confirm"])
        self.assertEqual(screen_regions.AI_WIN_RATE_REGION, boxes["win_rate"])

    def test_calibrated_win_rate_box_wins_over_the_default(self):
        targets = screen_regions.calibration_targets(
            make_config(ai_win_rate_roi=(120, 12, 280, 52)))

        box = next(t["box"] for t in targets if t["key"] == "win_rate")
        self.assertEqual((120, 12, 280, 52), box)

    def test_wide_box_follows_the_calibrated_main_box(self):
        regions = screen_regions.screenshot_regions(
            make_config(ai_win_rate_roi=(100, 10, 260, 50)))

        wide = next(r["box"] for r in regions if r["key"] == "win_rate_wide")
        self.assertEqual(screen_regions.expand_box(
            (100, 10, 260, 50), screen_regions.AI_WIN_RATE_WIDE_MARGIN), wide)

    def test_default_wide_box_is_derived_from_the_default_main_box(self):
        self.assertEqual(
            (95, 0, 300, 60),
            screen_regions.expand_box(
                screen_regions.AI_WIN_RATE_REGION,
                screen_regions.AI_WIN_RATE_WIDE_MARGIN))

    def test_target_lookup_by_key(self):
        self.assertEqual(
            "ai_win_rate_roi",
            screen_regions.calibration_target(make_config(),
                                              "win_rate")["config_key"])
        self.assertIsNone(
            screen_regions.calibration_target(make_config(), "nope"))

    def test_automation_reads_the_calibrated_win_rate_region(self):
        """自动投降实际截的区域也要跟着校准值走。"""
        import FSM_action

        saved = FSM_action.recommendation_config
        try:
            FSM_action.recommendation_config = SimpleNamespace(
                ai_win_rate_roi=(120, 12, 280, 52),
                ai_win_rate_wide_roi=(105, 0, 310, 64))
            self.assertEqual(((120, 12, 280, 52), (105, 0, 310, 64)),
                             FSM_action._ai_win_rate_regions())
            FSM_action.recommendation_config = None
            self.assertEqual(FSM_action._AI_WIN_RATE_REGIONS,
                             FSM_action._ai_win_rate_regions())
        finally:
            FSM_action.recommendation_config = saved

    def test_state_probe_points_are_copies(self):
        points = screen_regions.state_probe_points()
        points[0]["point"] = (0, 0)

        self.assertEqual((1090, 1070),
                         screen_regions.state_probe_points()[0]["point"])


class RegionPreviewTests(unittest.TestCase):
    def preview(self, **kwargs):
        kwargs.setdefault("config", make_config())
        kwargs.setdefault("grabber", make_grabber())
        kwargs.setdefault("screen_metrics", lambda: (1920, 1080, 96))
        kwargs.setdefault("panel_detector", lambda crop: True)
        return screen_regions.build_region_preview(**kwargs)

    @staticmethod
    def _open(result):
        raw = base64.b64decode(result["image"].split(",", 1)[1])
        return Image.open(io.BytesIO(raw))

    def test_returns_a_jpeg_of_the_current_screen(self):
        result = self.preview()

        self.assertTrue(result["image"].startswith("data:image/jpeg;base64,"))
        with self._open(result) as image:
            self.assertEqual((1920, 1080), image.size)
            self.assertEqual("JPEG", image.format)
        self.assertEqual(1920, result["width"])
        self.assertEqual(1080, result["height"])
        self.assertFalse(result["scaled"])

    def test_healthy_environment_passes_every_required_check(self):
        result = self.preview()

        self.assertTrue(result["ok"])
        statuses = {check["key"]: check["status"] for check in result["checks"]}
        self.assertEqual("ok", statuses["resolution"])
        self.assertEqual("ok", statuses["dpi"])
        self.assertEqual("ok", statuses["panel"])

    def test_all_regions_are_inside_the_screen(self):
        result = self.preview()

        # 4 个可校准/兜底区域 + 每局结束用的「开始」按钮 / 段位两个只读框
        self.assertEqual(6, len(result["regions"]))
        self.assertTrue(all(r["in_bounds"] for r in result["regions"]))

    def test_wrong_resolution_is_reported_as_a_failure(self):
        result = self.preview(grabber=make_grabber(size=(1280, 720)),
                              screen_metrics=lambda: (1280, 720, 144))

        statuses = {check["key"]: check["status"] for check in result["checks"]}
        self.assertEqual("fail", statuses["resolution"])
        self.assertEqual("fail", statuses["dpi"])
        self.assertFalse(result["ok"])
        resolution = next(c for c in result["checks"]
                          if c["key"] == "resolution")
        self.assertIn("1920×1080", resolution["hint"])

    def test_region_outside_the_screen_is_flagged(self):
        result = self.preview(config=make_config(
            recommendation_roi=(1800, 200, 2100, 500)))

        statuses = {check["key"]: check["status"] for check in result["checks"]}
        self.assertEqual("fail", statuses["bounds-recommendation"])
        self.assertFalse(result["ok"])
        region = next(r for r in result["regions"]
                      if r["key"] == "recommendation")
        self.assertFalse(region["in_bounds"])

    def test_missing_box_panel_warns_and_tells_the_user_to_move_it(self):
        result = self.preview(panel_detector=lambda crop: False)

        panel = next(c for c in result["checks"] if c["key"] == "panel")
        self.assertEqual("warn", panel["status"])
        self.assertFalse(panel["required"])
        self.assertIn("拖进绿框", panel["hint"])
        # 不在对局时看不到盒子面板很正常，因此不算致命错误
        self.assertTrue(result["ok"])

    def test_panel_detector_receives_the_recommendation_crop(self):
        seen = []

        def detector(crop):
            seen.append(crop.size)
            return True

        self.preview(panel_detector=detector)

        self.assertEqual([(195, 300)], seen)   # 202-7=195, 500-200=300

    def test_detector_exception_only_warns(self):
        def boom(crop):
            raise RuntimeError("检测器炸了")

        result = self.preview(panel_detector=boom)

        panel = next(c for c in result["checks"] if c["key"] == "panel")
        self.assertEqual("warn", panel["status"])
        self.assertTrue(result["ok"])

    def test_wide_screen_is_downscaled_for_transport(self):
        result = self.preview(grabber=make_grabber(size=(3840, 2160)),
                              screen_metrics=lambda: (3840, 2160, 96))

        self.assertTrue(result["scaled"])
        self.assertEqual(3840, result["width"])   # 原始分辨率照实上报
        with self._open(result) as image:
            self.assertEqual(1920, image.size[0])

    def test_points_carry_bounds_information(self):
        result = self.preview()

        self.assertEqual(3, len(result["points"]))
        self.assertTrue(all(p["in_bounds"] for p in result["points"]))

    def test_grabber_failure_is_reported(self):
        with self.assertRaises(RuntimeError):
            self.preview(grabber=lambda: None)


class RegionBoxDrawingTests(unittest.TestCase):
    """画框本身：默认全画标签；校准窗口可以只跳过"当前目标"和它的标签。

    回归背景：同一个框被 `paint_layer` 和校准窗口的当前目标高亮各画一遍
    （两次描边差 1px），再叠上紧贴框边的深色标签底，看起来像"一个区域两个框"。
    """

    def setUp(self):
        from PIL import Image
        self.image = Image.new("RGB", (1920, 1080), (18, 22, 30))
        self.config = make_config(post_game_start_roi=(1325, 865, 1475, 935))

    def _draw(self, **kwargs):
        labels = {region["key"]: (region["box"], region["label_drawn"])
                  for region in screen_regions.draw_region_boxes(
                      self.image, self.config, **kwargs)}
        return labels

    def test_every_region_gets_a_label_by_default(self):
        labels = self._draw()

        for key, (_box, drawn) in labels.items():
            with self.subTest(key=key):
                self.assertTrue(drawn)

    def test_skip_labels_only_hides_the_named_region(self):
        labels = self._draw(skip_labels=("post_game_start",))

        self.assertFalse(labels["post_game_start"][1])
        for key, (_box, drawn) in labels.items():
            if key == "post_game_start":
                continue
            with self.subTest(key=key):
                self.assertTrue(drawn)

    def test_skip_keys_omits_the_region_entirely(self):
        """被跳过的区域在这一层里一个像素都不画（改由校准窗口单独高亮）。"""
        layer = region_overlay.paint_layer(
            1920, 1080, None, self.config, skip_keys=("post_game_start",))

        left, top, right, bottom = (1325, 865, 1475, 935)
        for x in range(left, right + 1):
            with self.subTest(x=x):
                self.assertEqual(0, layer.getpixel((x, top))[3])
                self.assertEqual(0, layer.getpixel((x, bottom))[3])

        # 但区域本身仍登记在清单里（网页预览/调用方还要它的坐标）
        region = next(r for r in screen_regions.screenshot_regions(self.config)
                      if r["key"] == "post_game_start")
        self.assertEqual((1325, 865, 1475, 935), region["box"])

    def test_other_regions_are_still_drawn(self):
        layer = region_overlay.paint_layer(
            1920, 1080, None, self.config, skip_keys=("post_game_start",))

        # 绿框（推荐面板）顶边必须还在：跳过只针对当前目标
        self.assertGreater(layer.getpixel((100, 200))[3], 0)


if __name__ == "__main__":
    unittest.main()
