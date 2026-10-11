# -*- coding: utf-8 -*-
"""截图区域清单与区域框预览：坐标来源、越界判定、预览图生成。"""

import base64
import io
import unittest
from types import SimpleNamespace

from PIL import Image

import display_mapping
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
             "post_game_start", "rank", "main_menu_battle", "deck_select_title",
             "deck_grid",
             "mode_menu_open", "mode_wild", "mode_standard", "mode_casual"],
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
                          "post_game_start", "rank",
                          "main_menu_battle", "deck_select_title", "deck_grid",
                          "mode_menu_open", "mode_wild", "mode_standard",
                          "mode_casual"],
                         [target["key"] for target in targets])
        self.assertEqual(
            ["recommendation_roi", "mulligan_confirm_roi", "ai_win_rate_roi",
             "post_game_start_roi", "post_game_rank_roi",
             "main_menu_battle_roi", "deck_select_title_roi", "deck_grid_roi",
             "mode_menu_open", "mode_wild", "mode_standard", "mode_casual"],
            [target["config_key"] for target in targets])
        for target in targets:
            self.assertTrue(target["label"])
            self.assertTrue(target["short"])
            self.assertTrue(target["hint"])
            self.assertRegex(target["color"], r"^#[0-9a-fA-F]{6}$")
        # 模式点击点带 point_key（保存进 ui_config.json 的 `mode_click` 段，
        # 而不是 ROI 框），且框大小固定、中心就是点击点。
        mode_targets = [t for t in targets if t.get("point_key")]
        self.assertEqual(4, len(mode_targets))
        for target in mode_targets:
            left, top, right, bottom = target["box"]
            self.assertEqual(screen_regions.MODE_MARK_SIZE, right - left)
            self.assertEqual(screen_regions.MODE_MARK_SIZE, bottom - top)

    def test_calibratable_config_keys_are_unique(self):
        """config_key 是 ui_config.json 的顶层键，重了就会互相覆盖。"""
        keys = [t["config_key"] for t in screen_regions.CALIBRATION_TARGETS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_interface_recognition_boxes_match_battle_entry(self):
        """界面识别的三个框必须与 battle_entry 的默认值一致（防止两边改岔）。

        与 AI 胜率区域那条规矩一样：坐标只应有一个出处，改了一边另一边会红。
        """
        import battle_entry

        self.assertEqual(battle_entry.MAIN_MENU_BATTLE_DEFAULT,
                         screen_regions.MAIN_MENU_BATTLE_REGION)
        self.assertEqual(battle_entry.DECK_SELECT_TITLE_DEFAULT,
                         screen_regions.DECK_SELECT_TITLE_REGION)
        self.assertEqual(battle_entry.DECK_GRID_DEFAULT,
                         screen_regions.DECK_GRID_REGION)
        # 文字框可以很薄：它的缩放下限必须小于框高，否则手柄拖不出来
        for key in ("main_menu_battle", "deck_select_title"):
            target = next(t for t in screen_regions.calibration_targets(make_config())
                          if t["key"] == key)
            height = target["box"][3] - target["box"][1]
            self.assertLess(target["min_size"], height)

    def test_the_rank_box_is_calibratable(self):
        """用户反馈"段位数字无法校准"：它必须是可拖、可保存的目标。"""
        targets = screen_regions.calibration_targets(make_config())
        rank = next(t for t in targets if t["key"] == "rank")

        self.assertEqual("post_game_rank_roi", rank["config_key"])
        self.assertEqual(screen_regions.POST_GAME_RANK_REGION, rank["box"])
        regions = screen_regions.screenshot_regions(make_config())
        preview = next(r for r in regions if r["key"] == "rank")
        self.assertEqual("post_game_rank_roi", preview["config_key"])

    def test_calibrated_rank_box_wins_over_the_default(self):
        targets = screen_regions.calibration_targets(
            make_config(post_game_rank_roi=(1200, 170, 1400, 230)))
        regions = screen_regions.screenshot_regions(
            make_config(post_game_rank_roi=(1200, 170, 1400, 230)))

        box = next(t["box"] for t in targets if t["key"] == "rank")
        preview = next(r["box"] for r in regions if r["key"] == "rank")
        self.assertEqual((1200, 170, 1400, 230), box)
        self.assertEqual((1200, 170, 1400, 230), preview)

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
        # 注入"读不到客户区"的映射：默认用例只关心截图/画框/检查项本身，
        # 不该受测试机上是否正跑着炉石影响（真机上 window 几何随时会变）。
        kwargs.setdefault("mapping_provider", lambda: None)
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
        self.assertEqual("ok", statuses["dpi"])
        self.assertEqual("ok", statuses["panel"])

    def test_all_regions_are_inside_the_screen(self):
        result = self.preview()

        # 所有可校准目标 + AI胜率兜底框；数量跟着注册表走，别写死数字，
        # 否则加一个目标就要来改一次。
        self.assertEqual(len(screen_regions.CALIBRATION_TARGETS) + 1,
                         len(result["regions"]))
        self.assertTrue(all(r["in_bounds"] for r in result["regions"]))

    def test_mode_points_are_marked_as_click_points(self):
        """模式点击点也要能在预览/校准里看到（框中心=实际点击点）。"""
        regions = {r["key"]: r for r in screen_regions.screenshot_regions()}

        for key in ("mode_menu_open", "mode_wild", "mode_standard",
                    "mode_casual"):
            self.assertIn(key, regions)
            box = regions[key]["box"]
            self.assertEqual(screen_regions.MODE_MARK_SIZE, box[2] - box[0])
            self.assertEqual(screen_regions.MODE_MARK_SIZE, box[3] - box[1])

    def test_resolution_is_no_longer_a_failure(self):
        """换了分辨率不再判失败：客户区几何由 display_mapping 换算。

        3840×2160 的屏幕（不是配置里的 1920×1080）在改造前会直接判 FAIL；
        现在只有 DPI 提示（warn），且整个预览仍然通过。
        """
        result = self.preview(grabber=make_grabber(size=(3840, 2160)),
                              screen_metrics=lambda: (3840, 2160, 144),
                              max_width=3840)

        statuses = {check["key"]: check["status"] for check in result["checks"]}
        self.assertNotIn("resolution", statuses)
        self.assertEqual("warn", statuses["dpi"])
        self.assertNotIn("client-aspect", statuses)
        self.assertTrue(result["ok"], result["checks"])
        dpi = next(c for c in result["checks"] if c["key"] == "dpi")
        self.assertFalse(dpi["required"])
        self.assertIn("100%", dpi["hint"])

    def test_missing_client_mapping_is_only_a_hint(self):
        result = self.preview()

        client = next(c for c in result["checks"] if c["key"] == "client")
        self.assertEqual("warn", client["status"])
        self.assertFalse(client["required"])
        self.assertTrue(result["ok"])

    def test_client_mapping_is_reported_and_boxes_follow_it(self):
        """客户区在 (960,551)：框与判定点都要整体偏过去。"""
        mapping = display_mapping.DisplayMapping(
            hwnd=1, client=(0, 0, 1920, 1080),
            screen_left=960, screen_top=551)
        result = self.preview(
            grabber=make_grabber(size=(3840, 2160)),
            screen_metrics=lambda: (3840, 2160, 96),
            max_width=3840,
            mapping_provider=lambda: mapping)

        self.assertEqual({"width": 1920, "height": 1080}, result["client"])
        self.assertTrue(result["mapping"]["available"])
        self.assertEqual([960, 551], result["mapping"]["screen_origin"])
        region = next(r for r in result["regions"] if r["key"] == "win_rate")
        self.assertEqual((110, 8, 270, 48), region["box"])           # 客户区相对
        self.assertEqual((1070, 559, 1230, 599), region["screen_box"])
        probe = result["points"][0]
        self.assertEqual((2050, 1621), probe["screen_point"])

    def test_bad_client_aspect_is_a_failure(self):
        mapping = display_mapping.DisplayMapping(
            hwnd=1, client=(0, 0, 1920, 1200), screen_left=0, screen_top=0)
        result = self.preview(
            grabber=make_grabber(size=(1920, 1200)),
            screen_metrics=lambda: (1920, 1200, 96),
            mapping_provider=lambda: mapping)

        statuses = {check["key"]: check["status"] for check in result["checks"]}
        self.assertEqual("fail", statuses["client-aspect"])
        self.assertFalse(result["ok"])

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
        # mapping=None：不换算，框按客户区相对坐标画（测试机窗口位置不影响）。
        layer = region_overlay.paint_layer(
            1920, 1080, None, self.config, skip_keys=("post_game_start",),
            mapping=None)

        # 绿框（推荐面板）顶边必须还在：跳过只针对当前目标
        self.assertGreater(layer.getpixel((100, 200))[3], 0)


if __name__ == "__main__":
    unittest.main()
