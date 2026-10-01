# -*- coding: utf-8 -*-
"""截图区域清单与区域框预览：坐标来源、越界判定、预览图生成。"""

import base64
import io
import unittest
from types import SimpleNamespace

from PIL import Image

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
            ["recommendation", "mulligan_confirm", "win_rate", "win_rate_wide"],
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

    def test_state_probe_points_are_reported(self):
        points = screen_regions.state_probe_points()

        self.assertEqual(3, len(points))
        self.assertIn((1090, 1070), [p["point"] for p in points])


class CalibrationTargetTests(unittest.TestCase):
    """可校准区域清单：校准工具、叠加层、AI胜率读取共用这一份来源。"""

    def test_lists_the_three_calibratable_targets(self):
        targets = screen_regions.calibration_targets(make_config())

        self.assertEqual(["recommendation", "mulligan_confirm", "win_rate"],
                         [target["key"] for target in targets])
        self.assertEqual(
            ["recommendation_roi", "mulligan_confirm_roi", "ai_win_rate_roi"],
            [target["config_key"] for target in targets])
        for target in targets:
            self.assertTrue(target["label"])
            self.assertTrue(target["short"])
            self.assertTrue(target["hint"])
            self.assertRegex(target["color"], r"^#[0-9a-fA-F]{6}$")

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

        self.assertEqual(4, len(result["regions"]))
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


if __name__ == "__main__":
    unittest.main()
