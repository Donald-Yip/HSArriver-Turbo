# -*- coding: utf-8 -*-
"""display_mapping 的换算数学测试（纯 Python，不 import win32）。

覆盖三类场景：
1. **恒等**：客户区 1920×1080 且窗口在原点 → 换算结果与改造前逐像素相同；
2. **真实故障场景**：本机实测的客户区 1920×1080 @屏幕 (960,551) → 统一加偏移；
3. **等比缩放**：客户区 1600×900 / 1280×720 / 3840×2160 → 按比例换算。

`display_mapping()` 支持注入 rect_reader / origin_reader，所以这些用例不碰
真窗口、任何机器上都能跑。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import display_mapping as dm

# 本机实测：炉石窗口客户区 1920×1080，左上角在桌面 (960, 551)。
MEASURED_ORIGIN = (960, 551)


def make_mapping(client=(0, 0, 1920, 1080), origin=(0, 0), reference=None):
    return dm.DisplayMapping(
        hwnd=1, client=tuple(client), screen_left=origin[0], screen_top=origin[1],
        reference=tuple(reference) if reference else dm.REFERENCE_CLIENT)


class IdentityMappingTests(unittest.TestCase):
    """客户区 1920×1080 且窗口在 (0,0)：一切与改造前相同。"""

    def setUp(self):
        self.mapping = make_mapping()

    def test_scale_is_one(self):
        self.assertEqual(1.0, self.mapping.scale_x)
        self.assertEqual(1.0, self.mapping.scale_y)

    def test_points_pass_through(self):
        self.assertEqual((1090, 1070), self.mapping.to_screen(1090, 1070))
        self.assertEqual((705, 305), self.mapping.to_screen(705, 305))

    def test_boxes_pass_through(self):
        self.assertEqual((1, 296, 196, 544),
                         self.mapping.box_to_screen((1, 296, 196, 544)))
        self.assertEqual((1325, 865, 1475, 935),
                         self.mapping.box_to_screen((1325, 865, 1475, 935)))

    def test_module_helpers_pass_through(self):
        self.assertEqual((960, 850), dm.to_screen(960, 850, self.mapping))
        self.assertEqual((860, 810, 1060, 890),
                         dm.box_to_screen((860, 810, 1060, 890), self.mapping))

    def test_round_trip(self):
        for point in ((0, 0), (960, 540), (1895, 1060)):
            self.assertEqual(point, self.mapping.from_screen(
                *self.mapping.to_screen(*point)))

    def test_compatible_and_not_too_small(self):
        self.assertTrue(self.mapping.compatible())
        self.assertFalse(self.mapping.too_small())


class MeasuredOffsetTests(unittest.TestCase):
    """本机实测场景：客户区 1920×1080 @屏幕 (960,551)。"""

    def setUp(self):
        self.mapping = make_mapping(origin=MEASURED_ORIGIN)

    def test_scale_is_still_one(self):
        self.assertEqual(1.0, self.mapping.scale_x)
        self.assertEqual(1.0, self.mapping.scale_y)

    def test_state_probe_point_gets_the_offset(self):
        # get_screen.get_state() 的主判定点 (1090,1070)
        self.assertEqual((2050, 1621), self.mapping.to_screen(1090, 1070))

    def test_recommendation_roi_gets_the_offset(self):
        # ui_config.json 的 recommendation_roi
        self.assertEqual((961, 847, 1156, 1095),
                         self.mapping.box_to_screen((1, 296, 196, 544)))

    def test_post_game_button_gets_the_offset(self):
        # 只看"加偏移"的数学：框底 1486 很可能超出本机真实桌面高度
        # （本机 2560×1440），默认 clamp 会把它夹到屏幕内 —— 那是抓图安全行为，
        # 不属于这个用例要验的偏移。所以这里显式 clamp=False。
        self.assertEqual((2285, 1416, 2435, 1486),
                         self.mapping.box_to_screen((1325, 865, 1475, 935),
                                                    clamp=False))
        # 生产路径（默认 clamp）仍然必须把越界框夹进真实桌面，不能交给抓图抛异常。
        bounds = dm.desktop_bounds()
        clamped = self.mapping.box_to_screen((1325, 865, 1475, 935))
        self.assertEqual(
            (2285, 1416, 2435, min(1486, bounds[3])), clamped)
        self.assertLessEqual(clamped[3], bounds[3])

    def test_client_screen_box(self):
        self.assertEqual((960, 551, 2880, 1631), self.mapping.client_box)

    def test_debug_text_mentions_the_geometry(self):
        text = self.mapping.debug_text()
        self.assertIn("1920×1080", text)
        self.assertIn("(960,551)", text)
        self.assertIn("1.00", text)

    def test_mapping_info_is_serializable(self):
        import json
        info = dm.mapping_info(self.mapping)
        json.dumps(info)
        self.assertTrue(info["available"])
        self.assertEqual([960, 551], info["screen_origin"])
        self.assertEqual({"width": 1920, "height": 1080}, info["client"])


class ProportionalScalingTests(unittest.TestCase):
    """其他 16:9 客户区：等比换算。"""

    def test_1600x900(self):
        mapping = make_mapping(client=(0, 0, 1600, 900))
        self.assertAlmostEqual(1600 / 1920, mapping.scale_x, places=6)
        self.assertEqual((833, 750), mapping.to_screen(1000, 900))
        self.assertEqual((640, 371), mapping.to_screen(768, 445))

    def test_1280x720(self):
        mapping = make_mapping(client=(0, 0, 1280, 720))
        self.assertAlmostEqual(2 / 3, mapping.scale_x, places=6)
        self.assertEqual((640, 600), mapping.to_screen(960, 900))

    def test_3840x2160_is_double(self):
        mapping = make_mapping(client=(0, 0, 3840, 2160))
        self.assertEqual(2.0, mapping.scale_x)
        self.assertEqual((2180, 2140), mapping.to_screen(1090, 1070))
        self.assertEqual((2, 592, 392, 1088),
                         mapping.box_to_screen((1, 296, 196, 544)))

    def test_scaling_with_origin(self):
        mapping = make_mapping(client=(0, 0, 960, 540), origin=(100, 50))
        self.assertEqual(0.5, mapping.scale_x)
        self.assertEqual((600, 550), mapping.to_screen(1000, 1000))


class BoxRoundingTests(unittest.TestCase):
    def test_box_never_shrinks(self):
        # 小客户区下 1~2 像素的框如果按 round() 换算会被压成空框：
        # 左/上必须向下取整、右/下必须向上进一。
        mapping = make_mapping(client=(0, 0, 1000, 563))
        self.assertEqual((0, 0, 2, 2), mapping.box_to_screen((1, 1, 2, 2)))
        # 未换算时该框宽度为 1，换算后必须仍然 >= 1。
        left, top, right, bottom = mapping.box_to_screen((1, 1, 2, 2))
        self.assertGreaterEqual(right - left, 1)
        self.assertGreaterEqual(bottom - top, 1)

    def test_integer_scales_are_exact(self):
        mapping = make_mapping(client=(0, 0, 3840, 2160))
        self.assertEqual((2, 592, 392, 1088),
                         mapping.box_to_screen((1, 296, 196, 544)))

    def test_box_from_screen_round_trip(self):
        mapping = make_mapping(client=(0, 0, 1920, 1080), origin=(960, 551))
        original = (7, 200, 202, 500)
        screen = mapping.box_to_screen(original)
        self.assertEqual(original, mapping.box_from_screen(screen))


class CompatibilityTests(unittest.TestCase):
    def test_sixteen_by_nine_sizes_are_compatible(self):
        for width, height in ((1920, 1080), (1600, 900), (1280, 720),
                              (3840, 2160), (2560, 1440)):
            with self.subTest(size=(width, height)):
                mapping = make_mapping(client=(0, 0, width, height))
                self.assertTrue(mapping.compatible())
                self.assertFalse(mapping.too_small())

    def test_non_sixteen_by_nine_is_incompatible(self):
        for width, height in ((1920, 1200), (1024, 768), (1920, 1000)):
            with self.subTest(size=(width, height)):
                self.assertFalse(make_mapping(client=(0, 0, width, height))
                                 .compatible())

    def test_too_small_client(self):
        mapping = make_mapping(client=(0, 0, 900, 506))
        self.assertTrue(mapping.too_small())
        self.assertTrue(mapping.compatible())  # 比例没问题，只是太小

    def test_degenerate_client(self):
        mapping = make_mapping(client=(0, 0, 0, 0))
        self.assertFalse(mapping.compatible())
        self.assertTrue(mapping.too_small())


class NoneMappingTests(unittest.TestCase):
    """拿不到映射时，所有换算必须原样返回（= 改造前的行为）。"""

    def test_helpers_pass_through(self):
        self.assertEqual((1400, 900), dm.to_screen(1400, 900, None))
        self.assertEqual((1400, 900), dm.from_screen(1400, 900, None))
        self.assertEqual((1325, 865, 1475, 935),
                         dm.box_to_screen((1325, 865, 1475, 935), None))
        self.assertEqual((1325, 865, 1475, 935),
                         dm.box_from_screen((1325, 865, 1475, 935), None))

    def test_out_of_bounds_box_is_left_alone_without_mapping(self):
        # 没有映射时不做夹取：老测试里的越界 ROI 仍要能报"超出屏幕"。
        self.assertEqual((1800, 200, 2100, 500),
                         dm.box_to_screen((1800, 200, 2100, 500), None))

    def test_mapping_info_when_unavailable(self):
        info = dm.mapping_info(None)
        self.assertFalse(info["available"])
        self.assertIn("未知", info["text"])


class ClampTests(unittest.TestCase):
    def test_clamp_to_bounds(self):
        self.assertEqual((0, 0, 100, 100),
                         dm.clamp_to_desktop((-50, -50, 100, 100),
                                             size=(0, 0, 1920, 1080)))
        self.assertEqual((1820, 980, 1920, 1080),
                         dm.clamp_to_desktop((1820, 980, 2100, 1200),
                                             size=(0, 0, 1920, 1080)))

    def test_box_to_screen_clamps_when_mapping_exists(self):
        mapping = make_mapping(client=(0, 0, 1920, 1080), origin=(0, 0))
        with_bounds = dm.DisplayMapping(
            hwnd=1, client=(0, 0, 1920, 1080), screen_left=3000, screen_top=2000)
        clamped = with_bounds.box_to_screen((1, 296, 196, 544))
        self.assertGreaterEqual(clamped[0], 0)
        self.assertLessEqual(clamped[0], 3840)
        self.assertEqual((1, 296, 196, 544),
                         mapping.box_to_screen((1, 296, 196, 544)))


class DisplayMappingSamplingTests(unittest.TestCase):
    """display_mapping() 的采样/缓存逻辑（注入假读取器）。"""

    def setUp(self):
        dm.clear_mapping_cache()

    def tearDown(self):
        dm.clear_mapping_cache()

    def test_reads_rect_and_origin(self):
        mapping = dm.display_mapping(
            hwnd=42, rect_reader=lambda hwnd: (0, 0, 1920, 1080),
            origin_reader=lambda hwnd, x, y: (960, 551))
        self.assertIsNotNone(mapping)
        self.assertEqual((960, 551), mapping.origin)
        self.assertEqual((1920, 1080), (mapping.width, mapping.height))

    def test_missing_rect_returns_none(self):
        self.assertIsNone(dm.display_mapping(
            hwnd=42, rect_reader=lambda hwnd: None,
            origin_reader=lambda hwnd, x, y: (0, 0)))

    def test_zero_sized_rect_returns_none(self):
        # 最小化的窗口：客户区 0×0
        self.assertIsNone(dm.display_mapping(
            hwnd=42, rect_reader=lambda hwnd: (0, 0, 0, 0),
            origin_reader=lambda hwnd, x, y: (0, 0)))

    def test_origin_failure_returns_none(self):
        self.assertIsNone(dm.display_mapping(
            hwnd=42, rect_reader=lambda hwnd: (0, 0, 1920, 1080),
            origin_reader=lambda hwnd, x, y: None))

    def test_no_window_returns_none(self):
        # hwnd=0 / None 都表示"自动探测炉石窗口"；探测不到时必须返回 None。
        dm.clear_mapping_cache()
        with patch.object(dm, "hwnd_of", return_value=0):
            self.assertIsNone(dm.display_mapping(hwnd=0))
            self.assertIsNone(dm.display_mapping())

    def test_live_window_lookup(self):
        """本机真跑着炉石时，默认调用应当读到真实客户区几何。

        没有炉石的环境直接跳过（清缓存是必须的：这个用例会写进全局缓存）。
        """
        dm.clear_mapping_cache()
        try:
            mapping = dm.display_mapping()
        finally:
            dm.clear_mapping_cache()
        if mapping is None:
            self.skipTest("本机没有运行中的炉石窗口")
        self.assertGreater(mapping.width, 0)
        self.assertGreater(mapping.height, 0)
        self.assertEqual(mapping.reference, dm.REFERENCE_CLIENT)

    def test_injected_readers_bypass_the_cache(self):
        """注入假 reader 时既不读也不写缓存（换算单测不受本机窗口影响）。"""
        calls = {"n": 0}

        def rect_reader(hwnd):
            calls["n"] += 1
            return (0, 0, 1920, 1080)

        origin_reader = lambda hwnd, x, y: (100, 200)
        dm.display_mapping(hwnd=7, rect_reader=rect_reader,
                           origin_reader=origin_reader)
        dm.display_mapping(hwnd=7, rect_reader=rect_reader,
                           origin_reader=origin_reader)

        self.assertEqual(2, calls["n"], "注入 reader 时每次都真读")
        self.assertIsNone(dm._MAPPING_CACHE["mapping"],
                          "注入 reader 不应污染真实窗口的缓存")

    def test_reference_override(self):
        mapping = dm.display_mapping(
            hwnd=9, reference=(1600, 900),
            rect_reader=lambda hwnd: (0, 0, 1600, 900),
            origin_reader=lambda hwnd, x, y: (0, 0))
        self.assertEqual((1600, 900), tuple(mapping.reference))
        self.assertEqual(1.0, mapping.scale_x)


class ProbePixelTests(unittest.TestCase):
    """get_screen.probe_pixels：判定点在抓图里的索引位置。"""

    def setUp(self):
        # display_mapping() 的换算结果带缓存；本机真跑着炉石时会被真实窗口
        # 污染，所以每个用例前后都清一次。
        dm.clear_mapping_cache()

    def tearDown(self):
        dm.clear_mapping_cache()

    @staticmethod
    def frame(width, height):
        try:
            import numpy
            return numpy.zeros((height, width, 4), dtype="uint8")
        except Exception:
            return [[[0, 0, 0] for _ in range(width)] for _ in range(height)]

    def test_screen_probes_when_no_mapping(self):
        import get_screen
        frame = self.frame(1920, 1080)
        self.assertEqual(list(get_screen.STATE_PROBE_POINTS),
                         get_screen.probe_pixels(frame, None))

    def test_client_relative_probes_at_reference_size(self):
        import get_screen
        frame = self.frame(1920, 1080)
        mapping = make_mapping()
        self.assertEqual(list(get_screen.STATE_PROBE_POINTS),
                         get_screen.probe_pixels(frame, mapping))

    def test_client_relative_probes_scale_with_the_capture(self):
        """客户区 960×540 的判定点，索引到 1920×1080 的抓图上要乘 2。"""
        import get_screen
        mapping = make_mapping(client=(0, 0, 960, 540))
        frame = self.frame(1920, 1080)
        self.assertEqual([(2180, 2140), (1410, 610), (1720, 1920)],
                         get_screen.probe_pixels(frame, mapping))

    def test_capture_matching_the_client_area_is_identity(self):
        """抓图尺寸 == 客户区尺寸时（正常路径），索引就是判定点本身。"""
        import get_screen
        mapping = make_mapping(client=(0, 0, 960, 540))
        frame = self.frame(960, 540)
        self.assertEqual(list(get_screen.STATE_PROBE_POINTS),
                         get_screen.probe_pixels(frame, mapping))

    def test_every_client_probe_stays_inside_the_capture(self):
        """正常路径：抓图尺寸 == 客户区尺寸，三个判定点都必须落在图内。

        仅对客户区高度 >= 基准 1080 成立——基准判定点就是按 1920×1080 量的，
        更小的客户区里这些点本就在画面之外（见下一个用例）。
        """
        import get_screen
        for size in ((1920, 1080), (2560, 1440), (3840, 2160)):
            with self.subTest(size=size):
                mapping = make_mapping(client=(0, 0) + size)
                frame = self.frame(*size)
                for x, y in get_screen.probe_pixels(frame, mapping):
                    self.assertTrue(0 <= x < size[0] and 0 <= y < size[1])

    def test_out_of_frame_probe_is_safe(self):
        """客户区小于基准时，越界的判定点必须安全地读不到（返回 None），
        而不是抛 IndexError 打断状态机主循环。"""
        import get_screen
        mapping = make_mapping(client=(0, 0, 1280, 720))
        frame = self.frame(1280, 720)
        probe = get_screen.probe_pixels(frame, mapping)[0]
        self.assertIsNone(get_screen._pixel(frame, *probe))
        self.assertIsNone(get_screen._pixel(frame, 5000, 5000))
        self.assertIsNone(get_screen._pixel(frame, -1, 10))


class UiScaleTests(unittest.TestCase):
    def test_ui_scale_is_at_least_one(self):
        self.assertGreaterEqual(dm.ui_scale(), 1.0)


class RoiFrameInterpretationTests(unittest.TestCase):
    """ui_config.json 里的 ROI 口径解释（用户报告"框全跑偏"的根因之一）。

    老配置（或"炉石最小化时保存过"的假标记配置）里存的是**屏幕坐标**，直接当
    客户区相对坐标用会让所有框被整体偏移 + 缩放一次。`interpret_roi_box` 负责
    在读取时把口径判对，且必须与校准工具 `calibrate_roi` 完全一致。
    """

    # 本机实测：客户区 1920×1080 在屏幕 (958,320)；用户在 (0,0) 时的老配置值。
    ORIGIN = (958, 320)

    def _mapping(self):
        return make_mapping(origin=self.ORIGIN)

    def test_a_box_equal_to_the_code_default_is_client_relative(self):
        default = (7, 200, 202, 500)

        # 默认值本身就是客户区相对口径：即使它"看起来像屏幕坐标"也不换算。
        self.assertEqual(default, dm.interpret_roi_box(default, self._mapping(),
                                                       default=default))

    def test_declared_client_frame_is_trusted(self):
        box = (960, 551, 1208, 843)

        self.assertEqual(box, dm.interpret_roi_box(
            box, self._mapping(), declared_client=True, default=(7, 200, 202, 500)))

    def test_a_screen_box_inside_the_client_area_is_converted(self):
        box = (960, 551, 1208, 843)          # 窗口在 (958,320) 时的屏幕坐标

        client = dm.interpret_roi_box(box, self._mapping(),
                                      default=(7, 200, 202, 500))

        self.assertEqual((2, 231, 250, 523), client)
        # 换算回屏幕就是原值：升级后框不会突然位移，但从此跟着窗口走。
        self.assertEqual(box, self._mapping().box_to_screen(client))

    def test_a_box_outside_the_client_area_can_only_be_client_relative(self):
        box = (10, 20, 110, 120)             # 在客户区左边之外 → 不可能是屏幕坐标

        self.assertEqual(box, dm.interpret_roi_box(box, self._mapping(),
                                                   default=(7, 200, 202, 500)))

    def test_without_a_mapping_the_box_is_returned_as_is(self):
        box = (960, 551, 1208, 843)

        self.assertEqual(box, dm.interpret_roi_box(box, None,
                                                   default=(7, 200, 202, 500)))

    def test_a_box_sticking_out_of_the_client_area_is_left_alone(self):
        """框有一部分在客户区之外时，无法判定它是不是屏幕坐标 → 原样保留。

        这是刻意的保守选择：判错会把一个本来已经对好的框整体挪走，而"框挂在
        客户区外"（盒子面板是独立窗口）本身是合法用法。用户拖一下并按 S 保存
        即写死口径，之后不再有歧义。
        """
        box = (920, 300, 1000, 340)          # 左边超出客户区左边界 958

        self.assertEqual(box, dm.interpret_roi_box(box, self._mapping(),
                                                   default=(7, 200, 202, 500)))

    def test_roi_frame_is_credible_accepts_plausible_boxes(self):
        self.assertTrue(dm.roi_frame_is_credible(
            [(0, 0, 1920, 1080), (1230, 180, 1385, 225), (-40, -20, 42, 20)]))

    def test_roi_frame_is_credible_rejects_screen_looking_boxes(self):
        # 保存时窗口在 (960,551)：这些值作为客户区相对坐标是不可能的。
        self.assertFalse(dm.roi_frame_is_credible(
            [(2, 231, 250, 523), (2276, 1200, 2442, 1295)]))
        self.assertFalse(dm.roi_frame_is_credible([(0, 0, 1920, 1081)]))
        self.assertFalse(dm.roi_frame_is_credible([(0, -1081, 10, 10)]))
        self.assertFalse(dm.roi_frame_is_credible([("a", "b", "c", "d")]))

    def test_a_lie_marker_does_not_leak_into_shot_regions(self):
        """假标记（client）但值像屏幕坐标：解释结果必须还是"位置不变"。"""
        mapping = self._mapping()
        stored = (2276, 1200, 2442, 1295)
        credible = dm.roi_frame_is_credible([stored])

        client = dm.interpret_roi_box(stored, mapping,
                                      declared_client=credible, default=None)

        self.assertFalse(credible)
        self.assertEqual(stored, mapping.box_to_screen(client))


class FakeTracker:
    """假的窗口跟踪器（不挂 Win32 钩子）。"""

    def __init__(self, available=True, moved_at=None):
        self._available = available
        self.moved_at = moved_at
        self.polls = 0

    def available(self):
        return self._available

    def moved_since(self, timestamp):
        return (self.moved_at is not None
                and float(self.moved_at) > float(timestamp or 0.0))

    def poll(self, hwnd=None):
        self.polls += 1
        return False

    def stats(self):
        return {"available": self._available, "events": 0, "polls": self.polls,
                "last_move_at": self.moved_at or 0.0}


class LiveTrackingTests(unittest.TestCase):
    """事件驱动的缓存失效：窗口动了必须**立刻**重采样，不等 TTL。

    这里走的是**默认路径**（不注入 reader），所以用 patch 替换底层 Win32 读数，
    这样缓存与跟踪器逻辑都在真实分支上被覆盖。
    """

    def setUp(self):
        dm.clear_mapping_cache()
        dm.reset_capture_stats()
        self.origin = [960, 362]
        self.reads = [0]

        def rect_reader(handle):
            self.reads[0] += 1
            return (0, 0, 1920, 1080)

        def origin_reader(handle, x, y):
            return tuple(self.origin)

        self._patches = [
            patch.object(dm, "hwnd_of", return_value=1050772),
            patch.object(dm, "read_client_rect", side_effect=rect_reader),
            patch.object(dm, "client_to_screen", side_effect=origin_reader),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in self._patches:
            item.stop()
        dm.clear_mapping_cache()
        dm.reset_capture_stats()

    def _install(self, tracker):
        return patch.object(dm, "_tracker", return_value=tracker)

    def test_move_event_invalidates_cache_within_ttl(self):
        tracker = FakeTracker(available=True, moved_at=None)
        with self._install(tracker):
            first = dm.display_mapping()
            self.assertEqual((960, 362), first.origin)
            self.assertEqual(1, self.reads[0])

            # TTL 内、没有移动事件 → 命中缓存，不再读窗口
            again = dm.display_mapping()
            self.assertIs(first, again, "没有移动事件时应命中缓存")
            self.assertEqual(1, self.reads[0])

            # 窗口动了 → 立刻重采样（哪怕 TTL 没过期）
            self.origin = [444, 691]
            tracker.moved_at = dm.time.time() + 1.0   # 「刚刚动过」
            moved = dm.display_mapping()

            self.assertEqual((444, 691), moved.origin,
                             "移动事件后必须重新读取几何")
            self.assertEqual(2, self.reads[0])

    def test_silent_hook_still_falls_back_to_the_ttl(self):
        """钩子"挂上了但收不到事件"时，几何不能永久冻结。

        回归：以前这条分支是 `return moved_since(at)` —— 钩子可用但从不报事件
        （权限/跨进程/时钟口径不一致）时缓存永不失效，用户拖窗口时框和点击
        全都停在旧位置。现在必须有 TTL 兜底。
        """
        tracker = FakeTracker(available=True, moved_at=None)   # 永远"没动过"
        late = 1000.0 + dm._MAPPING_TTL_SECONDS + 1.0
        with self._install(tracker):
            first = dm.display_mapping(clock=lambda: 1000.0)
            self.origin = [444, 691]
            second = dm.display_mapping(clock=lambda: late)

        self.assertEqual((960, 362), first.origin)
        self.assertEqual((444, 691), second.origin,
                         "事件沉默时也必须由 TTL 兜底重新采样")

    def test_fallback_poll_is_used_without_hook(self):
        tracker = FakeTracker(available=False, moved_at=None)
        with self._install(tracker):
            dm.display_mapping()
            dm.display_mapping()

        self.assertGreaterEqual(tracker.polls, 1,
                               "没有事件钩子时应轮询兜底")

    def test_no_tracker_keeps_the_plain_ttl_cache(self):
        with self._install(None):
            first = dm.display_mapping()
            second = dm.display_mapping()

        self.assertIs(first, second)
        self.assertEqual(1, self.reads[0])

    def test_force_refresh_bypasses_the_cache(self):
        with self._install(FakeTracker(available=True)):
            dm.display_mapping()
            dm.refresh_mapping()

        self.assertEqual(2, self.reads[0])


class CaptureRegionTests(unittest.TestCase):
    """capture_region：抓图期间窗口移动则整帧作废重抓。"""

    def setUp(self):
        dm.clear_mapping_cache()
        dm.reset_capture_stats()

    def tearDown(self):
        dm.clear_mapping_cache()
        dm.reset_capture_stats()

    @staticmethod
    def _reader(origins):
        state = {"i": 0}

        def read(hwnd=None, force=False):
            index = min(state["i"], len(origins) - 1)
            state["i"] += 1
            return dm.DisplayMapping(hwnd=1, client=(0, 0, 1920, 1080),
                                     screen_left=origins[index][0],
                                     screen_top=origins[index][1])
        return read

    def test_stable_window_grabs_once(self):
        frames = []

        def grabber(region):
            frames.append(region)
            return "frame"

        frame, mapping = dm.capture_region(
            grabber=grabber, mapping_reader=self._reader([(960, 362)]))

        self.assertEqual("frame", frame)
        self.assertEqual((960, 362, 2880, 1442), mapping.client_box)
        self.assertEqual(1, len(frames))
        self.assertEqual(1, dm.tracker_stats()["frames"])
        self.assertEqual(0, dm.tracker_stats()["retries"])

    def test_moving_window_retries_then_uses_fresh_geometry(self):
        # 抓图期间窗口一直在动：每次都报告 moved_since
        moving = FakeTracker(available=True, moved_at=None)
        times = iter([100.0, 200.0, 300.0, 400.0, 500.0, 600.0])
        with __import__("unittest").mock.patch.object(
                dm, "_tracker", return_value=moving), \
                __import__("unittest").mock.patch.object(
                    dm.time, "time", side_effect=lambda: next(times, 999.0)):
            moving.moved_at = 250.0        # 永远 "动过"
            frames = []

            def grabber(region):
                frames.append(region)
                return "frame-%d" % len(frames)

            frame, mapping = dm.capture_region(
                grabber=grabber, retries=1,
                mapping_reader=self._reader([(960, 362), (444, 691),
                                             (500, 700)]))

        stats = dm.tracker_stats()
        self.assertIsNotNone(frame)
        self.assertEqual(1, stats["retries"], "应重试过一次")
        self.assertEqual(1, stats["stale"], "重试耗尽后应记为 stale 并重新对几何")
        # 返回的几何必须是最后那次读到的（与最终帧配套）
        self.assertEqual((500, 700), mapping.origin)

    def test_missing_window_returns_none_pair(self):
        frame, mapping = dm.capture_region(
            grabber=lambda region: "x", mapping_reader=lambda hwnd=None: None)

        self.assertIsNone(frame)
        self.assertIsNone(mapping)

    def test_grab_failure_returns_mapping_for_retry(self):
        frame, mapping = dm.capture_region(
            grabber=lambda region: None,
            mapping_reader=self._reader([(960, 362)]))

        self.assertIsNone(frame)
        self.assertIsNotNone(mapping, "抓图失败也要把几何给调用方")


class _StyleReader:
    """假 GetWindowLongW：只回样式值，`restype`/`argtypes` 可写。"""

    restype = None
    argtypes = None

    def __init__(self, style):
        self.style = style

    def __call__(self, hwnd, index):
        return self.style


class WindowModeTests(unittest.TestCase):
    """全屏 / 窗口化的判定：只认标题栏，不认尺寸。"""

    def test_caption_means_windowed(self):
        self.assertEqual("windowed", dm.classify_window_mode(dm.WS_CAPTION))
        self.assertEqual("windowed", dm.classify_window_mode(0x16CF0000))

    def test_popup_without_caption_means_fullscreen(self):
        self.assertEqual("fullscreen", dm.classify_window_mode(0x80000000))
        self.assertEqual("fullscreen", dm.classify_window_mode(0x86000000))

    def test_broken_style_is_unknown(self):
        self.assertEqual("unknown", dm.classify_window_mode(None))
        self.assertEqual("unknown", dm.classify_window_mode("x"))

    def test_window_mode_reads_the_window_style(self):
        import types

        fake = types.SimpleNamespace(user32=types.SimpleNamespace(
            GetWindowLongW=_StyleReader(dm.WS_CAPTION)))
        with patch.object(dm.ctypes, "windll", fake, create=True):
            self.assertEqual("windowed", dm.window_mode(hwnd=123))

    def test_window_mode_never_raises(self):
        """拿不到窗口/样式时安静返回 unknown（自检不能因为辅助调用炸掉）。"""
        import types

        fake = types.SimpleNamespace(user32=types.SimpleNamespace(
            GetWindowLongW=_StyleReader(None)))
        with patch.object(dm.ctypes, "windll", fake, create=True):
            self.assertEqual("unknown", dm.window_mode(hwnd=123))
        with patch("display_mapping.ctypes.windll", None, create=True):
            self.assertEqual("unknown", dm.window_mode(hwnd=123))


if __name__ == "__main__":
    unittest.main()
