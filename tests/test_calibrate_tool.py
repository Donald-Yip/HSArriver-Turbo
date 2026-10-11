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
import config
import display_mapping
import screen_regions
from PIL import Image, ImageDraw
from types import SimpleNamespace


def make_temp_dir() -> Path:
    """建一个普通权限的临时目录。

    不用 tempfile.TemporaryDirectory：它会把目录设成 0700，在受限环境里
    反而写不进去。这里自己 mkdir 再自己删。
    """
    path = Path(tempfile.gettempdir()) / f"hs_calib_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_config(**overrides):
    """代码默认值组成的假配置（**不读真实 ui_config.json**）。

    单测绝不能依赖用户本机此刻的 ui_config.json：那份文件会随校准变化，
    否则测试结果就跟着用户拖过的框一起漂。
    """
    base = {item["config_key"]: tuple(item["default"])
            for item in screen_regions.CALIBRATION_TARGETS}
    base.update(dict(desktop_size=(1920, 1080), desktop_dpi=96))
    base.update(overrides)
    return SimpleNamespace(**base)


def make_session(**kwargs):
    """建一个**不读真实窗口/真实配置**的校准会话。

    * `mapping_provider=lambda: None`：默认拿不到客户区几何（恒等换算），
      单测不会受"本机此刻有没有开着炉石"影响；
    * `config_provider=default_config`：用代码默认值，不读真实 ui_config.json。

    **坐标口径**：`calibration_targets()` 给的值已经是客户区相对坐标（老配置的
    屏幕坐标由 config/display_mapping 在读取时解释好），校准工具自己不解释。
    """
    kwargs.setdefault("mapping_provider", lambda: None)
    kwargs.setdefault("config_provider", default_config)
    session = calibrate_roi.CalibrationSession(
        selftest=True, ocr_enabled=False, **kwargs)
    session.width, session.height = 1920, 1080
    return session


class TargetTests(unittest.TestCase):
    def test_text_target_can_be_resized_thin_but_others_still_clamp(self):
        """「传统对战」是 259×27 的**文字**框：它的缩放下限必须小于 60。

        统一的 MIN_SIZE(60) 会让右下角手柄根本拖不出这么薄的框（只剩手改配置
        文件一条路），所以注册表允许单个目标声明 min_size；别的目标仍守 60。
        """
        session = make_session(start_key="main_menu_battle")
        self.assertLess(session._active_min_size(), calibrate_roi.MIN_SIZE)

        session.active["box"] = [800, 300, 1100, 340]
        session.drag = ("resize",)
        session._on_mouse_move(1100, 324, 1920, 1080)
        self.assertEqual(24, session.active["box"][3] - session.active["box"][1])

        other = make_session(start_key="rank")
        other.active["box"] = [1200, 170, 1400, 230]
        other.drag = ("resize",)
        other._on_mouse_move(1400, 174, 1920, 1080)
        self.assertEqual(calibrate_roi.MIN_SIZE,
                         other.active["box"][3] - other.active["box"][1])

    def test_every_registered_target_is_editable(self):
        session = make_session()

        self.assertEqual(len(screen_regions.CALIBRATION_TARGETS),
                         len(session.targets))
        self.assertEqual("recommendation", session.active["key"])
        self.assertGreaterEqual(len(session.targets), 5)
        for target in session.targets:
            self.assertEqual(4, len(target["box"]))
        keys = [target["config_key"] for target in session.targets]
        self.assertIn("post_game_start_roi", keys)
        # 用户反馈"段位数字无法校准"：它必须是可拖的目标之一。
        self.assertIn("post_game_rank_roi", keys)

    def test_the_rank_number_box_is_calibratable(self):
        session = make_session(start_key="rank")
        target = session.active

        self.assertEqual("post_game_rank_roi", target["config_key"])
        self.assertEqual(screen_regions.POST_GAME_RANK_REGION,
                         tuple(target["box"]))
        self.assertTrue(target["hint"])

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

    def test_finds_the_center_cross_for_whole_box_dragging(self):
        """框正中央要有「整体拖动」十字标：抓它就能挪整个框。

        用户反馈拖边缘移动整体很难抓（框一大四条边离得远），所以十字标是
        "整体拖动"的主抓手，必须命中。
        """
        box = self.session.active["box"]
        cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2

        self.assertEqual(("cross", None),
                         self.session._hit_test(cx, cy, self.WIDTH,
                                                self.HEIGHT))

    def test_center_cross_drags_the_whole_box(self):
        """十字标按下再移动 = 整体平移（和拖边缘同一个 drag 状态）。"""
        box = self.session.active["box"]
        cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
        self.session._on_mouse_down(cx, cy, self.WIDTH, self.HEIGHT)
        self.assertEqual("move", self.session.drag[0])

        before_w = box[2] - box[0]
        self.session._on_mouse_move(cx + 40, cy + 25, self.WIDTH,
                                    self.HEIGHT)
        moved = self.session.active["box"]
        self.assertEqual(before_w, moved[2] - moved[0], "整体拖动不该改变宽度")
        self.assertEqual(box[0] + 40, moved[0])
        self.assertEqual(box[1] + 25, moved[1])

    def test_the_cross_shrinks_inside_a_very_thin_box(self):
        """「传统对战」这种又薄又宽的框：十字标要缩进框内，且仍能抓到。"""
        self.session.targets[0]["box"] = [100, 300, 500, 320]   # 400×20
        rect = self.session._move_cross_rect()

        self.assertGreaterEqual(rect[1], 300)
        self.assertLessEqual(rect[3], 320)
        self.assertLessEqual(rect[2] - rect[0], 20)     # 不超过框的短边
        cx, cy = (rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2
        self.assertEqual(("cross", None),
                         self.session._hit_test(cx, cy, self.WIDTH,
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


class CardDragTests(unittest.TestCase):
    """目标条本身要能被拖开。

    卡片默认压在屏幕正中，正好挡住"正中那个框"时就完全没法对齐了（用户反馈），
    所以要能整块挪走。挪动只活在本次会话，不写进 ui_config.json。
    """

    WIDTH, HEIGHT = 2560, 1440

    def setUp(self):
        self.session = make_session()
        self.session.width, self.session.height = self.WIDTH, self.HEIGHT

    def _title_point(self):
        """标题带上的一点（卡片内、又不属于任何目标行/按钮）。"""
        left, top = self.session._card_rect(self.WIDTH, self.HEIGHT)[:2]
        return left + 60, top + 10

    def test_the_title_band_is_a_drag_handle(self):
        x, y = self._title_point()

        self.assertEqual(("card", None),
                         self.session._hit_test(x, y, self.WIDTH,
                                                self.HEIGHT))

    def test_dragging_the_card_moves_it_and_keeps_its_size(self):
        base = self.session._card_rect(self.WIDTH, self.HEIGHT)
        x, y = self._title_point()
        self.session._on_mouse_down(x, y, self.WIDTH, self.HEIGHT)
        self.assertEqual("card", self.session.drag[0])

        self.session._on_mouse_move(x - 400, y + 120, self.WIDTH, self.HEIGHT)
        moved = self.session._card_rect(self.WIDTH, self.HEIGHT)

        self.assertEqual(base[2] - base[0], moved[2] - moved[0], "拖动不改尺寸")
        self.assertEqual(base[3] - base[1], moved[3] - moved[1], "拖动不改高")
        self.assertEqual((base[0] - 400, base[1] + 120), moved[:2])

    def test_dragging_the_card_does_not_touch_any_region_box(self):
        """拖卡片是纯界面行为：12 个 ROI 框一个都不许动。"""
        before = [list(t["box"]) for t in self.session.targets]
        x, y = self._title_point()
        self.session._on_mouse_down(x, y, self.WIDTH, self.HEIGHT)
        self.session._on_mouse_move(x + 200, y - 180, self.WIDTH, self.HEIGHT)

        self.assertEqual(before, [list(t["box"]) for t in self.session.targets])

    def test_the_card_is_clamped_into_the_screen(self):
        base = self.session._card_rect(self.WIDTH, self.HEIGHT)
        x, y = self._title_point()
        self.session._on_mouse_down(x, y, self.WIDTH, self.HEIGHT)

        self.session._on_mouse_move(-5000, -5000, self.WIDTH, self.HEIGHT)
        self.assertEqual((0, 0), self.session._card_rect(self.WIDTH,
                                                         self.HEIGHT)[:2])

        self.session._on_mouse_move(99999, 99999, self.WIDTH, self.HEIGHT)
        card = self.session._card_rect(self.WIDTH, self.HEIGHT)
        self.assertEqual(self.WIDTH, card[2])
        self.assertEqual(self.HEIGHT, card[3])
        self.assertGreater(base[0], 0, "这个用例的前提是卡片本来不在左上角")

    def test_pulling_back_from_the_screen_edge_responds_immediately(self):
        """贴边后往回拖要立刻跟手。

        曾经的写法是把"指针相对按下的位移"累加进 card_offset：卡片一旦被边缘
        夹住，指针继续往外走时累加量还在涨，往回拖就得先空拖掉那段"隐形行程"，
        表现是卡片黏在边上不动。现在改成每帧用指针当前位置重算，就不该有黏滞。
        """
        x, y = self._title_point()
        grab_dx, grab_dy = 60, 10
        self.session._on_mouse_down(x, y, self.WIDTH, self.HEIGHT)

        self.session._on_mouse_move(-5000, -5000, self.WIDTH, self.HEIGHT)
        self.assertEqual((0, 0), self.session._card_rect(self.WIDTH,
                                                         self.HEIGHT)[:2])

        # 只往回挪一点点，卡片就必须同幅度地跟出来
        self.session._on_mouse_move(x - 500, y - 300, self.WIDTH, self.HEIGHT)
        self.assertEqual((x - 500 - grab_dx, y - 300 - grab_dy),
                         self.session._card_rect(self.WIDTH, self.HEIGHT)[:2])

    def test_target_rows_and_buttons_win_over_the_card_drag(self):
        """卡片几乎被目标行铺满：按下必须优先选中那一行，不能变成拖卡片。"""
        rect, index = self.session._chip_rects(self.WIDTH, self.HEIGHT)[2]
        self.assertEqual(("target", 2),
                         self.session._hit_test(rect[0] + 40, rect[1] + 8,
                                                self.WIDTH, self.HEIGHT))

        save = self.session._save_rect(self.WIDTH, self.HEIGHT)
        self.assertEqual(("save", None),
                         self.session._hit_test((save[0] + save[2]) // 2,
                                                (save[1] + save[3]) // 2,
                                                self.WIDTH, self.HEIGHT))
        exit_ = self.session._exit_rect(self.WIDTH, self.HEIGHT)
        self.assertEqual(("exit", None),
                         self.session._hit_test((exit_[0] + exit_[2]) // 2,
                                                (exit_[1] + exit_[3]) // 2,
                                                self.WIDTH, self.HEIGHT))

    def test_the_gap_between_rows_is_still_draggable(self):
        """行与行之间的缝不是按钮也不是行：抓它也算拖卡片。"""
        (_, top, _, _) = self.session._card_rect(self.WIDTH, self.HEIGHT)
        left = self.session._card_rect(self.WIDTH, self.HEIGHT)[0]
        first = self.session._chip_rects(self.WIDTH, self.HEIGHT)[0][0]
        first_row_bottom = first[3]
        second = self.session._chip_rects(self.WIDTH, self.HEIGHT)[1][0]
        gap_y = (first_row_bottom + second[1]) // 2

        self.assertEqual(("card", None),
                         self.session._hit_test(left + 60, gap_y,
                                                self.WIDTH, self.HEIGHT))

    def test_the_offset_is_not_persisted(self):
        """拖动只在本次会话有效：ui_config.json 里不许出现界面状态。"""
        left, top = self.session._card_rect(self.WIDTH, self.HEIGHT)[:2]
        self.session._on_mouse_down(left + 60, top + 10, self.WIDTH,
                                    self.HEIGHT)
        self.session._on_mouse_move(left - 300, top + 90, self.WIDTH,
                                    self.HEIGHT)
        self.assertNotEqual([0, 0], self.session.card_offset)

        fresh = make_session()
        fresh.width, fresh.height = self.WIDTH, self.HEIGHT
        self.assertEqual([0, 0], fresh.card_offset)
        self.assertEqual(calibrate_roi.card_rect(self.WIDTH, self.HEIGHT),
                         fresh._card_rect(self.WIDTH, self.HEIGHT))


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

    def test_save_without_a_client_area_never_claims_the_client_frame(self):
        """炉石最小化/没开时保存：必须诚实写 screen，不能谎报 client。

        谎报的后果很严重：下次炉石一显示，所有框都会被整体偏移 + 缩放一次
        （用户就是这么踩坑的：标记 client、值是屏幕坐标）。
        """
        session = make_session(mapping_provider=lambda: None)
        session.targets[0]["box"] = [960, 551, 1208, 843]
        with patch.object(calibrate_roi, "_user_config_path",
                          return_value=self.path):
            self.assertTrue(session.save())

        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual("screen", data["roi_frame"])
        self.assertEqual([960, 551, 1208, 843], data["recommendation_roi"])

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
        # 正中央的目标条必须画出来（不透明）；远离目标条的右侧保持透明
        card = calibrate_roi.card_rect(900, 700)
        self.assertGreater(layer.getpixel(((card[0] + card[2]) // 2,
                                           (card[1] + card[3]) // 2))[3], 0)
        self.assertEqual(0, layer.getpixel((899, card[1] - 40))[3])

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

    def test_render_draws_only_the_active_target(self):
        """屏幕上只画**当前目标**那一个框：十几个框全铺出来根本看不清拖哪个。

        （用户反馈：框框只有在被选择到才显示，不然屏幕上全是框框。）
        取样点必须避开目标条与预览面板 —— 它们是本来就该画的两块 UI。
        """
        session = make_session()
        session.targets[0]["box"] = [100, 60, 300, 160]      # 当前目标
        # 非当前目标放左下角：避开正中央的目标条与右上的预览面板。
        session.targets[1]["box"] = [80, 800, 400, 950]
        session.active_index = 0
        size = (1920, 1080)
        session.width, session.height = size

        card = calibrate_roi.card_rect(*size, session._card_height())
        preview = session._preview_rect(size[0])

        def _edge_hits(box):
            left, top, right, bottom = box
            probes = [(x, top) for x in range(left, right, 5)]
            probes += [(x, bottom) for x in range(left, right, 5)]
            probes += [(left, y) for y in range(top, bottom, 5)]
            probes = [p for p in probes
                      if not (card[0] <= p[0] <= card[2]
                              and card[1] <= p[1] <= card[3])
                      and not (preview[0] <= p[0] <= preview[2]
                               and preview[1] <= p[1] <= preview[3])]
            return probes

        layer = session.render(*size)

        active_probes = _edge_hits(session.targets[0]["box"])
        self.assertTrue(active_probes, "取样点被 UI 面板吃光了")
        self.assertTrue(any(layer.getpixel(p)[3] > 0 for p in active_probes),
                        "当前目标应当被画出来")
        other_probes = _edge_hits(session.targets[1]["box"])
        self.assertTrue(other_probes, "取样点被 UI 面板吃光了")
        self.assertFalse(any(layer.getpixel(p)[3] > 0 for p in other_probes),
                         "非当前目标不该出现在屏幕上")

    def test_preview_config_follows_the_in_progress_boxes(self):
        """拖框过程中 `_preview_config()` 必须跟上（它喂给 panel_state）。"""
        session = make_session()
        session.targets[2]["box"] = [110, 8, 270, 48]

        # 真拖一次推荐面板（不是直接改 box）：屏幕框与权威的 client_box 一起更新。
        session.active_index = 0
        session.targets[0]["box"] = [11, 22, 233, 344]
        session.drag = ("move", 0, 0)
        session._on_mouse_move(11, 22, 1920, 1080)

        config = session._preview_config()

        self.assertEqual((11, 22, 233, 344), config.recommendation_roi)
        self.assertEqual((110, 8, 270, 48), config.ai_win_rate_roi)
        self.assertEqual((95, 0, 300, 60), config.ai_win_rate_wide_roi)

    def test_preview_config_carries_the_rank_box(self):
        session = make_session(start_key="rank")
        rank = next(t for t in session.targets if t["key"] == "rank")
        session.active_index = session.targets.index(rank)
        rank["box"] = [1200, 170, 1400, 230]
        session.drag = ("move", 0, 0)
        session._on_mouse_move(1200, 170, 1920, 1080)

        self.assertEqual((1200, 170, 1400, 230),
                         session._preview_config().post_game_rank_roi)


class ActiveTargetMappingTests(unittest.TestCase):
    """回归：**当前目标的框必须和其他框画在同一位置**，且两条路径都跟着窗口走。

    背景（用户报告）：切换目标时，其他校准框的位置都会变。原因是当前目标由
    `_draw_active_target` 单独绘制（直接用框坐标），而其他框经 display_mapping
    换算；两条路径坐标系不同，一切换目标就"看起来是其他框在跳"。

    核心不变式（本类锁的就是它）：
        细框路径（`_preview_config` 的客户区相对值 → box_to_screen）
            == 当前目标路径使用的屏幕框
    只要这条成立，切换目标时不会有任何框移动。
    """

    ORIGIN = (960, 362)
    SIZE = (3840, 2160)

    @staticmethod
    def mapped_session(origin=ORIGIN):
        mapping = display_mapping.DisplayMapping(
            hwnd=1, client=(0, 0, 1920, 1080),
            screen_left=origin[0], screen_top=origin[1])
        return make_session(mapping_provider=lambda: mapping)

    def _blank(self):
        return Image.new("RGBA", self.SIZE, (0, 0, 0, 0))

    def test_both_paths_agree_for_every_target(self):
        """两条绘制路径必须落在同一批像素上（全部目标）。"""
        session = self.mapped_session()
        mapping = session._mapping()
        session._sync_boxes(mapping)
        cfg = session._preview_config(mapping)

        for index, target in enumerate(session.targets):
            with self.subTest(target=target["key"]):
                session.active_index = index
                box = tuple(int(v) for v in target["box"])
                drawn = display_mapping.box_to_screen(
                    cfg.hs_target_boxes[target["key"]], mapping)
                self.assertEqual(box, drawn,
                                 "两条路径的换算落点不一致（切换目标就会跳）")

    def test_synced_boxes_are_the_client_box_plus_the_window_origin(self):
        """屏幕框 = 客户区相对框 + 客户区原点（换算只发生一次）。"""
        session = self.mapped_session()
        ox, oy = self.ORIGIN
        session._sync_boxes(session._mapping())

        for target in session.targets:
            with self.subTest(target=target["key"]):
                client = tuple(int(v) for v in target["client_box"])
                screen = tuple(int(v) for v in target["box"])
                self.assertEqual((client[0] + ox, client[1] + oy,
                                  client[2] + ox, client[3] + oy), screen)

    def test_active_target_is_drawn_at_its_screen_box(self):
        """当前目标描边必须落在它的**屏幕框**上（不能再多算一次窗口偏移）。"""
        session = self.mapped_session()
        session.active_index = 1
        mapping = session._mapping()
        session._sync_boxes(mapping)
        target = session.active
        ox, oy = self.ORIGIN
        box = tuple(int(v) for v in target["box"])
        canvas = self._blank()
        session._draw_active_target(ImageDraw.Draw(canvas), *self.SIZE,
                                    mapping=mapping)
        color = calibrate_roi._rgba(target["color"])

        found = any(canvas.getpixel((x, y))[:3] == color[:3]
                    for x in range(box[0], min(box[0] + 4, box[2]) + 1)
                    for y in range(box[1], min(box[1] + 4, box[3]) + 1))
        self.assertTrue(found, f"框 {box} 左上角没有 {color[:3]} 描边")
        # 屏幕框 = 客户区相对框 + 原点；客户区相对坐标那一点必须是空的
        # （说明没有把窗口偏移算两遍）。
        client = tuple(int(v) for v in target["client_box"])
        self.assertEqual((client[0] + ox, client[1] + oy), (box[0], box[1]))
        self.assertEqual(0, canvas.getpixel((client[0], client[1]))[3],
                         "框被多算了一次窗口偏移")

    def test_active_box_interior_stays_transparent(self):
        """当前目标只描边 + 手柄，框内仍是透明的（要能看见底下的游戏画面）。

        正中央的「整体拖动」十字标由 `render()` 在最后单独画（它必须压在目标条
        上面，否则框落在屏幕正中时会被盖住 —— 见 `test_..._cross`），所以直接
        调 `_draw_active_target` 时框内整片都该是空的。
        """
        session = self.mapped_session()
        session.active_index = 0
        session._sync_boxes(session._mapping())
        canvas = self._blank()
        session._draw_active_target(ImageDraw.Draw(canvas), *self.SIZE,
                                    mapping=session._mapping())

        box = tuple(int(v) for v in session.active["box"])
        center = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
        self.assertEqual(0, canvas.getpixel(center)[3])

    def test_render_draws_the_center_cross_on_top_of_the_card(self):
        """十字标必须画在目标条**之上**：框正好落在屏幕正中时也要看得见。

        注意命中优先级：**目标行/按钮 > 十字标**。目标条自己也要能点（切目标、
        保存、退出），所以卡片盖住的地方点击仍归卡片 —— 这只是"看得见"的修复，
        不改变谁先接收点击。
        """
        session = make_session()
        size = (1920, 1080)
        session.width, session.height = size
        # 把当前目标摆到屏幕正中，和居中画出来的目标条完全重叠。
        card = calibrate_roi.card_rect(*size, session._card_height())
        session.targets[0]["box"] = list(card)
        session.active_index = 0

        layer = session.render(*size)
        rect = session._move_cross_rect()

        # 十字标方块里应有白色箭头（底色是深色衬底），说明它画在卡片之上了。
        arrow_pixels = [layer.getpixel((x, y))
                        for x in range(rect[0], rect[2] + 1, 2)
                        for y in range(rect[1], rect[3] + 1, 2)]
        self.assertTrue(any(px[3] > 0 and px[0] > 200 and px[1] > 200
                            for px in arrow_pixels),
                        "框中心的十字标被目标条盖住了")

    def test_target_rows_win_the_click_over_the_center_cross(self):
        """卡片上的点击归卡片：十字标再显眼也不能抢走目标行的点击。"""
        session = make_session()
        size = (1920, 1080)
        session.width, session.height = size
        card = calibrate_roi.card_rect(*size, session._card_height())
        session.targets[0]["box"] = list(card)
        session.active_index = 0
        cx, cy = (card[0] + card[2]) // 2, (card[1] + card[3]) // 2

        kind, index = session._hit_test(cx, cy, *size)
        self.assertEqual("target", kind)
        self.assertEqual(7, index)      # 正中央恰好压在第 7 行上

    def test_no_mapping_draws_at_the_raw_box(self):
        """拿不到客户区几何时是恒等换算：框仍画在它的坐标上。"""
        session = make_session(mapping_provider=lambda: None)
        session.active_index = 0
        session._sync_boxes(None)
        canvas = self._blank()
        session._draw_active_target(ImageDraw.Draw(canvas), *self.SIZE,
                                    mapping=session._mapping())

        box = tuple(int(v) for v in session.active["box"])
        self.assertGreater(canvas.getpixel((box[0], box[1]))[3], 0)
        self.assertEqual(box, tuple(int(v) for v in session.active["client_box"]))

    def test_client_box_line_reports_the_client_relative_box(self):
        session = self.mapped_session()
        session.active_index = 0
        session._sync_boxes(session._mapping())
        target = session.active

        self.assertEqual("客户区：7,200 → 202,500",
                         session._client_box_line(target))

    def test_client_box_line_says_same_when_no_mapping(self):
        session = make_session(mapping_provider=lambda: None)
        session.active_index = 0
        session._sync_boxes(None)

        self.assertEqual("客户区：与上面相同",
                         session._client_box_line(session.active))

    def test_client_line_reuses_the_injected_mapping(self):
        session = self.mapped_session()

        line = session._client_line(session._mapping())

        self.assertIn("1920×1080", line)
        self.assertIn(f"({self.ORIGIN[0]},{self.ORIGIN[1]})", line)
        self.assertIn("S 保存", line)


class WindowFollowTests(unittest.TestCase):
    """回归：**炉石窗口一动，所有框必须立刻跟着走**（用户报告的核心问题）。

    以前 `_normalize_boxes()` 在打开校准窗口时把框一次性换算成屏幕坐标，之后
    再也不换算：窗口一挪，框就停在原地（用户看到的就是"校准窗口不跟着炉石
    移动"）。现在的口径是"客户区相对坐标为准、每帧推导屏幕坐标"。
    """

    CLIENT = (0, 0, 1920, 1080)

    def setUp(self):
        self.origin = [960, 362]
        self.client = self.CLIENT
        session = make_session(mapping_provider=self._mapping)
        # 真实的 4K 桌面：窗口挪到右下角时框也仍在屏幕内（不被 _clamp 收边）。
        session.width, session.height = 3840, 2160
        self.session = session

    def _mapping(self):
        return display_mapping.DisplayMapping(
            hwnd=1, client=self.client,
            screen_left=self.origin[0], screen_top=self.origin[1])

    def _boxes(self):
        return [tuple(int(v) for v in t["box"]) for t in self.session.targets]

    def test_boxes_follow_the_window_origin(self):
        self.session._sync_boxes(self._mapping())
        before = self._boxes()

        self.origin = [444, 691]                    # 用户把炉石拖走了
        self.session._sync_boxes(self._mapping())

        self.assertEqual(len(screen_regions.CALIBRATION_TARGETS), len(before))
        for old, new in zip(before, self._boxes()):
            self.assertEqual((old[0] - 960 + 444, old[1] - 362 + 691,
                              old[2] - 960 + 444, old[3] - 362 + 691), new)
        # 客户区相对坐标是权威值，窗口移动不会改变它（不需要重新校准）。
        self.assertEqual((7, 200, 202, 500),
                         tuple(self.session.targets[0]["client_box"]))

    def test_boxes_follow_a_resized_window(self):
        self.session._sync_boxes(self._mapping())
        client_box = tuple(self.session.targets[0]["client_box"])

        self.client = (0, 0, 3840, 2160)            # 窗口被放大到 2 倍
        self.session._sync_boxes(self._mapping())

        screen = tuple(self.session.targets[0]["box"])
        self.assertEqual((client_box[0] * 2 + 960, client_box[1] * 2 + 362,
                          client_box[2] * 2 + 960, client_box[3] * 2 + 362),
                         screen)
        self.assertEqual(client_box,
                         tuple(self.session.targets[0]["client_box"]))

    def test_sync_is_a_noop_while_the_geometry_is_unchanged(self):
        self.session._sync_boxes(self._mapping())
        self.session.dirty = False

        self.session._sync_boxes(self._mapping())

        self.assertFalse(self.session.dirty, "几何没变就不该重新推导/标脏")

    def test_a_minimized_window_keeps_the_last_known_position(self):
        """炉石最小化（拿不到几何）时不许把框塌到屏幕左上角。"""
        self.session._sync_boxes(self._mapping())
        before = self._boxes()

        self.session._sync_boxes(None)

        self.assertEqual(before, self._boxes())

    def test_dragging_writes_the_client_box_back(self):
        """拖框后权威值必须跟着更新，否则窗口一动就被"拉回去"。"""
        self.session._sync_boxes(self._mapping())
        target = self.session.targets[0]
        target["box"] = [1000, 600, 1200, 800]
        self.session.drag = ("move", 0, 0)
        self.session._on_mouse_move(1000, 600, 3840, 2160)

        self.assertEqual([40, 238, 240, 438],
                         [int(v) for v in target["client_box"]])
        # 窗口再移动时，用的是刚拖出来的客户区相对坐标。
        self.origin = [0, 0]
        self.session._sync_boxes(self._mapping())
        self.assertEqual((40, 238, 240, 438), tuple(target["box"]))

    def test_rendering_after_a_move_draws_the_moved_boxes(self):
        size = (3840, 2160)
        self.session._sync_boxes(self._mapping())
        self.origin = [444, 691]
        layer = self.session.render(*size)

        target = self.session.active
        box = tuple(int(v) for v in target["box"])
        self.assertEqual(target["client_box"][0] + 444, box[0])
        self.assertGreater(layer.getpixel((box[0], box[1] + 5))[3], 0)

    def test_save_after_a_move_writes_the_unchanged_client_box(self):
        tmp = make_temp_dir()
        path = tmp / "ui_config.json"
        try:
            self.session._sync_boxes(self._mapping())
            self.origin = [444, 691]
            with patch.object(calibrate_roi, "_user_config_path",
                              return_value=path):
                self.assertTrue(self.session.save())
            data = json.loads(path.read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual("client", data["roi_frame"])
        self.assertEqual([7, 200, 202, 500], data["recommendation_roi"])


class RoiFrameTests(unittest.TestCase):
    """ui_config.json 的 ROI 口径：校准工具**只消费**已解释好的客户区相对坐标。

    口径解释（老配置的屏幕坐标、"谎报 client"的坏配置……）统一由
    `config.RecommendationConfig` → `display_mapping.interpret_roi_box` 负责
    （见 tests/test_roi_config_frame.py）。**校准工具本身绝不再解释一次**：
    同一份数据解释两次＝把窗口偏移算两遍，框会整体错位。
    """

    ORIGIN = (960, 362)

    def setUp(self):
        self.tmp = make_temp_dir()
        self.path = self.tmp / "ui_config.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, data: dict):
        self.path.write_text(json.dumps(data, ensure_ascii=False),
                             encoding="utf-8")

    def _context(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(calibrate_roi, "_user_config_path",
                                         return_value=self.path))
        return stack

    def _session(self, cfg):
        mapping = display_mapping.DisplayMapping(
            hwnd=1, client=(0, 0, 1920, 1080),
            screen_left=self.ORIGIN[0], screen_top=self.ORIGIN[1])
        return make_session(mapping_provider=lambda: mapping,
                            config_provider=lambda: cfg)

    @staticmethod
    def _cfg(**overrides):
        base = dict(desktop_size=(1920, 1080), desktop_dpi=96,
                    recommendation_roi=(1, 296, 196, 544),
                    mulligan_confirm_roi=(860, 810, 1060, 890),
                    ai_win_rate_roi=(110, 8, 270, 48),
                    ai_win_rate_wide_roi=(95, 0, 300, 60),
                    post_game_start_roi=(1325, 865, 1475, 935),
                    post_game_rank_roi=(1230, 180, 1385, 225))
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_marker_defaults_to_screen_for_legacy_config(self):
        self._write({})
        with self._context():
            self.assertEqual("screen", calibrate_roi._stored_roi_frame())

    def test_marker_reads_client(self):
        self._write({"roi_frame": "client"})
        with self._context():
            self.assertEqual("client", calibrate_roi._stored_roi_frame())

    def test_client_frame_box_stays_client_relative_and_follows_the_window(self):
        """存的是客户区相对值，屏幕位置每帧由它推导 —— 这就是"框跟着窗口走"。"""
        self._write({"roi_frame": "client",
                     "recommendation_roi": [1, 296, 196, 544]})
        ox, oy = self.ORIGIN
        with self._context():
            session = self._session(self._cfg(
                recommendation_roi=(1, 296, 196, 544)))
            session._sync_boxes(session._mapping())

        self.assertEqual((1, 296, 196, 544),
                         tuple(session.targets[0]["client_box"]))
        self.assertEqual((1 + ox, 296 + oy, 196 + ox, 544 + oy),
                         tuple(session.targets[0]["box"]))

    def test_config_values_are_used_verbatim(self):
        """回归：工具不再自己解释口径（否则同一个偏移会被算两遍）。

        `config` 已经把"屏幕坐标 → 客户区相对"换算完了（拿到的就是客户区相对
        坐标）；工具哪怕觉得这个值"看着像屏幕坐标"也必须原样用。
        """
        self._write({"recommendation_roi": [960, 551, 1208, 843]})
        ox, oy = self.ORIGIN
        with self._context():
            session = self._session(self._cfg(
                recommendation_roi=(2, 231, 250, 523)))
            session._sync_boxes(session._mapping())

        self.assertEqual((2, 231, 250, 523),
                         tuple(session.targets[0]["client_box"]))
        self.assertEqual((2 + ox, 231 + oy, 250 + ox, 523 + oy),
                         tuple(session.targets[0]["box"]))

    def test_boxes_follow_the_window_after_a_move(self):
        """窗口移动后屏幕框跟着走；客户区相对坐标不变（不用重新校准）。"""
        ox, oy = self.ORIGIN
        with self._context():
            session = self._session(self._cfg())
            session._sync_boxes(session._mapping())
            self.assertEqual((1 + ox, 296 + oy, 196 + ox, 544 + oy),
                             tuple(session.targets[0]["box"]))

            moved = display_mapping.DisplayMapping(
                hwnd=1, client=(0, 0, 1920, 1080),
                screen_left=444, screen_top=691)
            session._sync_boxes(moved)

        self.assertEqual((1, 296, 196, 544),
                         tuple(session.targets[0]["client_box"]))
        self.assertEqual((445, 987, 640, 1235),
                         tuple(session.targets[0]["box"]))

    def test_save_writes_client_frame_and_marker(self):
        """保存：box 归一成客户区相对坐标 + 写入 roi_frame 标记。"""
        self._write({})
        ox, oy = self.ORIGIN
        with self._context():
            session = self._session(self._cfg())
            session.targets[0]["box"] = [ox + 1, oy + 296, ox + 196, oy + 544]
            self.assertTrue(session.save())
            data = json.loads(self.path.read_text("utf-8"))

        self.assertEqual("client", data["roi_frame"])
        self.assertEqual([1, 296, 196, 544], list(data["recommendation_roi"]))

    def test_save_includes_the_rank_box(self):
        """段位框也要跟着写进 ui_config.json（用户要能校准它）。"""
        self._write({})
        with self._context():
            session = self._session(self._cfg())
            # 按 key 找，不按位置找：注册表里后面还会加目标（界面识别那两个）。
            index = next(i for i, t in enumerate(session.targets)
                         if t["key"] == "rank")
            rank = session.targets[index]
            self.assertEqual("post_game_rank_roi", rank["config_key"])
            # 真拖一次（而不是直接改 box）：拖完屏幕框与权威的 client_box 同步。
            session.active_index = index
            rank["box"] = [1200, 170, 1400, 230]
            session.drag = ("move", 0, 0)
            session._on_mouse_move(1200, 170, 1920, 1080)
            self.assertTrue(session.save())
            data = json.loads(self.path.read_text("utf-8"))

        self.assertEqual([240, -192, 440, -132],
                         list(data["post_game_rank_roi"]))


class LateClientAreaTests(unittest.TestCase):
    """打开校准窗口时炉石还没显示 → 几何可读之后补读一次配置。

    那一刻 `config` 读不到客户区几何，没法把老配置里的屏幕坐标换算成客户区相对
    坐标，只能原样返回；等炉石显示出来（几何可读）再读一次，框才会落到正确位置，
    而不是被当成客户区坐标整体偏掉一次。
    """

    ORIGIN = (960, 362)
    SCREEN_BOX = (960, 551, 1208, 843)      # 老配置里存的屏幕坐标
    CLIENT_BOX = (2, 231, 250, 523)         # 等价换算成客户区相对

    def _mapping(self):
        return display_mapping.DisplayMapping(
            hwnd=1, client=(0, 0, 1920, 1080),
            screen_left=self.ORIGIN[0], screen_top=self.ORIGIN[1])

    def _session(self, configs):
        """configs：每次读配置依次返回的（假）RecommendationConfig。"""
        state = {"calls": 0}

        def provider():
            index = min(state["calls"], len(configs) - 1)
            state["calls"] += 1
            return configs[index]

        session = calibrate_roi.CalibrationSession(
            selftest=True, ocr_enabled=False, config_provider=provider,
            mapping_provider=lambda: None)
        session.width, session.height = 3840, 2160
        session._config_calls = lambda: state["calls"]
        return session

    def test_targets_are_reread_once_the_client_area_shows_up(self):
        session = self._session([
            default_config(recommendation_roi=self.SCREEN_BOX),
            default_config(recommendation_roi=self.CLIENT_BOX),
        ])
        self.assertTrue(session._reload_pending, "首次读配置时拿不到几何")

        session._sync_boxes(None)
        self.assertEqual(self.SCREEN_BOX, tuple(session.targets[0]["box"]))

        mapping = self._mapping()          # 炉石显示出来了
        session._sync_boxes(mapping)

        self.assertFalse(session._reload_pending)
        self.assertEqual(self.CLIENT_BOX,
                         tuple(session.targets[0]["client_box"]))
        ox, oy = self.ORIGIN
        self.assertEqual((self.CLIENT_BOX[0] + ox, self.CLIENT_BOX[1] + oy,
                          self.CLIENT_BOX[2] + ox, self.CLIENT_BOX[3] + oy),
                         tuple(session.targets[0]["box"]))

    def test_the_reread_happens_only_once(self):
        session = self._session([default_config(), default_config()])
        mapping = self._mapping()

        session._sync_boxes(mapping)
        calls_after_first = session._config_calls()
        session._sync_boxes(mapping)

        self.assertEqual(calls_after_first, session._config_calls(),
                         "几何没变时不该反复读配置")

    def test_user_edits_win_over_the_late_reread(self):
        """用户已经拖过框：绝不能再拿配置覆盖他的调整。"""
        session = self._session([default_config(), default_config()])
        session.active_index = 0
        session.drag = ("move", 0, 0)
        session._on_mouse_move(500, 400, 3840, 2160)
        self.assertTrue(session._edited)

        session._sync_boxes(self._mapping())

        # 拖出来的客户区相对坐标被保留（只叠加了窗口原点），没有被配置覆盖。
        self.assertEqual([500, 400, 695, 700],
                         [int(v) for v in session.targets[0]["client_box"]])
        ox, oy = self.ORIGIN
        self.assertEqual([500 + ox, 400 + oy, 695 + ox, 700 + oy],
                         [int(v) for v in session.targets[0]["box"]])
        self.assertEqual(1, session._config_calls(), "不许再读一次配置")

    def test_a_session_with_geometry_never_rereads(self):
        mapping = self._mapping()
        session = calibrate_roi.CalibrationSession(
            selftest=True, ocr_enabled=False, config_provider=default_config,
            mapping_provider=lambda: mapping)
        session.width, session.height = 3840, 2160

        self.assertFalse(session._reload_pending)


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
