# -*- coding: utf-8 -*-
"""上分停止条件 + 每局结束「等开始按钮 → 点按钮中心」流程。

回归背景：
  * 打完一局后的结算界面要点好几下才会出现底部「开始」按钮；脚本会一直点
    左上角待命点（(70,60)，不是屏幕正中），OCR 到「开始」后才收尾。
  * 收尾顺序：先关掉可能压在按钮上的断线弹窗，再点「开始」按钮**正中心**
    （默认 (1400,900)，框可校准），最后左键兜底回主界面。
  * 段位判定只看数字：读得到数字＝已上传说（数字即名次），空白＝没上传说。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import FSM_action
import web_ui


def make_config(**overrides):
    cfg = {"enabled": True, "mode": "legend", "legend_number": 1}
    cfg.update(overrides)
    return cfg


def lines(*pairs):
    return [SimpleNamespace(text=text, confidence=confidence)
            for text, confidence in pairs]


class ParseRankTextTests(unittest.TestCase):
    """段位判定：只看数字（有数字=传说，没数字=没上传说）。"""

    def test_only_digits_decide_legend(self):
        cases = (
            ("1234", 1234, True),        # 纯数字 = 传说名次
            ("传说 1234", 1234, True),    # 文本里还有别的字也不影响
            ("１２３", 123, True),        # 全角数字照样认
            ("传说", None, False),        # 只有文字 = 没上传说
            ("钻石 III", None, False),
            ("", None, False),
            ("   ", None, False),
            (None, None, False),
            ("999999", None, False),      # 超出 1..100000 不算数
        )
        for text, number, legend in cases:
            with self.subTest(text=text):
                parsed = FSM_action.parse_rank_text(text)

                self.assertEqual(number, parsed["number"])
                self.assertEqual(legend, parsed["legend"])
                self.assertEqual(text, parsed["raw"])


class BlankRegionTests(unittest.TestCase):
    """未上传说时段位位置是空白：靠亮像素个数判定，直接跳过 OCR。"""

    @staticmethod
    def _image(rows):
        import numpy as np
        return np.asarray(rows, dtype="uint8")

    def test_blank_detection_and_reader_shortcut(self):
        dark = self._image([[[20, 20, 20]] * 20] * 10)
        bright_row = [[20, 20, 20]] * 10 + [[240, 240, 240]] * 10
        bright = self._image([bright_row] * 10)
        calls = []

        self.assertTrue(FSM_action._region_is_blank(dark))
        self.assertTrue(FSM_action._region_is_blank(None))
        self.assertFalse(FSM_action._region_is_blank(bright))

        with (
            patch.object(FSM_action, "_grab_region", return_value=dark),
            patch.object(FSM_action, "_ocr_lines",
                         side_effect=lambda *a, **k: calls.append(a)),
        ):
            reading = FSM_action.read_rank_region()

        self.assertTrue(reading["empty"])
        self.assertIsNone(reading["number"])
        self.assertEqual([], calls)          # 空白不送 OCR


class ReadRankRegionTests(unittest.TestCase):
    """OCR 读段位：读得到就解析，读不到/没数字就不触发停止。"""

    def _read(self, ocr_lines):
        with (
            patch.object(FSM_action, "_grab_region", return_value=object()),
            patch.object(FSM_action, "_region_is_blank", return_value=False),
            patch.object(FSM_action, "_ocr_lines", return_value=ocr_lines),
        ):
            return FSM_action.read_rank_region()

    def test_reads_number_from_any_line_with_digits(self):
        cases = (
            (lines(("传说 4321", 0.9)), 4321, True),
            (lines(("继续", 0.9), ("88", 0.8)), 88, True),
            # 全是文字（没数字）：没上传说
            (lines(("钻石 III", 0.9)), None, False),
            (None, None, False),             # OCR 不可用
        )
        for ocr_lines, number, legend in cases:
            with self.subTest(number=number, legend=legend):
                reading = self._read(ocr_lines)

                self.assertEqual(number, reading["number"])
                self.assertEqual(legend, reading["legend"])
                self.assertEqual(number is None, reading["raw"] is None)

    def test_strict_needs_a_line_that_is_only_the_number(self):
        """严格模式：整行只有数字才算（用于"必须拦住点击"的那次检测）。"""
        with (
            patch.object(FSM_action, "_grab_region", return_value=object()),
            patch.object(FSM_action, "_region_is_blank", return_value=False),
            patch.object(FSM_action, "_ocr_lines",
                         return_value=lines(("传说 4321", 0.9))),
        ):
            self.assertEqual(4321, FSM_action.read_rank_region()["number"])
            self.assertIsNone(
                FSM_action.read_rank_region(strict=True)["number"])

        with (
            patch.object(FSM_action, "_grab_region", return_value=object()),
            patch.object(FSM_action, "_region_is_blank", return_value=False),
            patch.object(FSM_action, "_ocr_lines",
                         return_value=lines((" ４３２１ ", 0.9))),
        ):
            self.assertEqual(4321,
                             FSM_action.read_rank_region(strict=True)["number"])

    def test_min_confidence_drops_shaky_lines(self):
        with (
            patch.object(FSM_action, "_grab_region", return_value=object()),
            patch.object(FSM_action, "_region_is_blank", return_value=False),
            patch.object(FSM_action, "_ocr_lines",
                         return_value=lines(("4321", 0.3))),
        ):
            self.assertEqual(4321, FSM_action.read_rank_region()["number"])
            self.assertIsNone(
                FSM_action.read_rank_region(min_confidence=0.5)["number"])


class PureRankTextTests(unittest.TestCase):
    def test_only_bare_numbers_pass(self):
        cases = (
            ("4321", True),
            ("#4321", True),
            (" 43 ", True),
            ("４３２１", True),
            ("传说 4321", False),
            ("4321 名", False),
            ("", False),
            (None, False),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(expected, FSM_action.is_pure_rank_text(text))


class RankConditionTests(unittest.TestCase):
    def test_condition_matrix(self):
        cases = (
            # (reading, cfg, 期望命中)
            ({"number": 4321, "legend": True}, make_config(mode="legend"), True),
            ({"number": None, "legend": False},
             make_config(mode="legend"), False),
            ({"number": 99, "legend": True},
             make_config(mode="legend_number", legend_number=100), True),
            ({"number": 100, "legend": True},
             make_config(mode="legend_number", legend_number=100), True),
            ({"number": 101, "legend": True},
             make_config(mode="legend_number", legend_number=100), False),
            ({"number": 1, "legend": True}, make_config(enabled=False), False),
        )
        for reading, cfg, expected in cases:
            with self.subTest(reading=reading, mode=cfg["mode"],
                              enabled=cfg["enabled"]):
                self.assertEqual(expected, FSM_action._rank_condition_met(
                    reading, cfg))


class CheckRankStopTests(unittest.TestCase):
    """命中目标就请求「本局结束后停止」；读不到只提示、不误停。"""

    def setUp(self):
        self._saved = (FSM_action._rank_stop_triggered,
                       FSM_action._rank_stop_stop_reason,
                       FSM_action._rank_last_read,
                       FSM_action._rank_last_phase)
        FSM_action._rank_stop_triggered = False
        FSM_action._rank_stop_stop_reason = None
        FSM_action._rank_last_read = None
        FSM_action._rank_last_phase = None
        self.logs = []
        self.stop_requests = []

    def tearDown(self):
        (FSM_action._rank_stop_triggered, FSM_action._rank_stop_stop_reason,
         FSM_action._rank_last_read, FSM_action._rank_last_phase) = self._saved

    def _run(self, readings, cfg=None):
        """readings 用完后返回最后一个（模拟"屏幕一直是这样"）。"""
        sequence = list(readings)
        last = sequence[-1] if sequence else None

        def fake_read(**kwargs):
            return sequence.pop(0) if sequence else last

        with (
            patch.object(FSM_action, "rank_stop_settings",
                         return_value=dict(cfg or make_config())),
            patch.object(FSM_action, "read_rank_region", side_effect=fake_read),
            patch.object(FSM_action, "request_stop_after_game",
                         side_effect=lambda: self.stop_requests.append(1)),
            patch.object(FSM_action.manual_controller, "output",
                         side_effect=lambda msg: self.logs.append(msg)),
            patch.object(FSM_action.time, "sleep"),
        ):
            return FSM_action._check_rank_stop()

    def test_hit_requests_stop_after_current_game(self):
        hit = self._run([{"number": 4321, "legend": True, "raw": "4321"}])

        self.assertTrue(hit)
        self.assertEqual([1], self.stop_requests)
        self.assertTrue(FSM_action._rank_stop_triggered)
        self.assertIn("传说 4321", FSM_action._rank_stop_stop_reason)
        self.assertTrue(any("上分目标已达成" in msg for msg in self.logs))

    def test_miss_and_unreadable_never_stop(self):
        cases = (
            # 没到目标名次
            ([{"number": 5000, "legend": True, "raw": "5000"}],
             make_config(mode="legend_number", legend_number=100)),
            # 段位位置**一直**是空白（亮像素不够）= 真的没上传说
            ([{"number": None, "legend": False, "raw": None,
               "empty": True, "bright": 2}],
             None),
            # 截图失败（blank，截不到图）：重试几次仍失败
            ([{"number": None, "legend": False, "raw": None,
               "empty": False, "blank": True, "bright": -1}],
             None),
            # 有内容但 OCR 读不到：重试几次仍失败
            ([{"number": None, "legend": False, "raw": None,
               "empty": False, "blank": False, "bright": 40}],
             None),
        )
        for readings, cfg in cases:
            with self.subTest(readings=len(readings), mode=(cfg or {}).get("mode")):
                self.stop_requests.clear()
                self.logs.clear()
                hit = self._run(readings, cfg)

                self.assertFalse(hit)
                self.assertEqual([], self.stop_requests)
                self.assertFalse(FSM_action._rank_stop_triggered)
                self.assertTrue(self.logs)   # 失败必须留一条能看懂的原因

    def test_blank_reading_is_retried_then_hits(self):
        """刚进结算界面时名次还没画出来（读到空白）——必须重试，不能就此判未上传说。"""
        readings = [{"number": None, "legend": False, "raw": None,
                     "empty": True, "bright": 3}] * 2
        readings.append({"number": 4672, "legend": True, "raw": "4672",
                         "empty": False, "blank": False, "bright": 120})

        self.assertTrue(self._run(readings))
        self.assertEqual([1], self.stop_requests)
        self.assertEqual(4672, FSM_action._rank_last_read["number"])

    def test_unreadable_screen_is_retried_then_hits(self):
        readings = [{"number": None, "legend": False, "raw": None,
                     "empty": False, "blank": True, "bright": -1}] * 2
        readings.append({"number": 12, "legend": True, "raw": "12"})

        self.assertTrue(self._run(readings))
        self.assertEqual([1], self.stop_requests)

    def test_disabled_or_already_triggered_never_reads(self):
        calls = []

        with (
            patch.object(FSM_action, "rank_stop_settings",
                         return_value=make_config(enabled=False)),
            patch.object(FSM_action, "read_rank_region",
                         side_effect=lambda **kw: calls.append(1)),
        ):
            self.assertFalse(FSM_action._check_rank_stop())

        FSM_action._rank_stop_triggered = True
        with (
            patch.object(FSM_action, "rank_stop_settings",
                         return_value=make_config()),
            patch.object(FSM_action, "read_rank_region",
                         side_effect=lambda **kw: calls.append(1)),
        ):
            self.assertFalse(FSM_action._check_rank_stop())

        self.assertEqual([], calls)

    def test_check_passes_through_attempts_and_label(self):
        """调用方可以指定"读几次 + 这一步叫什么"，页面/日志据此回答检测了没有。"""
        seen = {}

        def fake_read(**kwargs):
            seen["kwargs"] = kwargs
            return {"number": 4321, "legend": True, "raw": "4321"}

        with (
            patch.object(FSM_action, "rank_stop_settings",
                         return_value=make_config()),
            patch.object(FSM_action, "read_rank_region",
                         side_effect=fake_read),
            patch.object(FSM_action, "request_stop_after_game"),
            patch.object(FSM_action.manual_controller, "output"),
            patch.object(FSM_action.time, "sleep"),
        ):
            FSM_action._check_rank_stop(attempts=2, wait=0.5,
                                        label="点「开始」前",
                                        min_confidence=0.5, confirm=True)

        self.assertEqual({"strict": False, "min_confidence": 0.5},
                         seen["kwargs"])
        self.assertEqual("点「开始」前", FSM_action._rank_last_phase)
        self.assertEqual("点「开始」前", FSM_action._rank_last_read and
                         "点「开始」前")
        self.assertIn("传说 4321", FSM_action._rank_stop_stop_reason)

    def test_confirm_reads_twice_and_rejects_a_one_off_misread(self):
        """复核失败（第二次读不到数字）就不能停——一次误识别不能停掉自动化。"""
        reads = iter([
            {"number": 4321, "legend": True, "raw": "4321"},   # 预读命中
            {"number": None, "legend": False, "raw": None},    # 复核没读到
        ])

        with (
            patch.object(FSM_action, "rank_stop_settings",
                         return_value=make_config()),
            patch.object(FSM_action, "read_rank_region",
                         side_effect=lambda **kw: next(reads)),
            patch.object(FSM_action, "request_stop_after_game",
                         side_effect=lambda: self.stop_requests.append(1)),
            patch.object(FSM_action.manual_controller, "output",
                         side_effect=lambda msg: self.logs.append(msg)),
            patch.object(FSM_action.time, "sleep"),
        ):
            hit = FSM_action._check_rank_stop(confirm=True)

        self.assertFalse(hit)
        self.assertEqual([], self.stop_requests)
        self.assertFalse(FSM_action._rank_stop_triggered)
        self.assertTrue(self.logs)

    def test_detection_state_reports_config_and_live_values(self):
        FSM_action._rank_last_read = {"number": 1234, "legend": True,
                                      "raw": "1234", "empty": False}
        FSM_action._rank_last_phase = "点「开始」前"
        FSM_action._rank_stop_triggered = True
        FSM_action._rank_stop_stop_reason = "已上传说到传说 1234 名"

        with patch.object(FSM_action, "rank_stop_settings",
                          return_value=make_config(mode="legend_number",
                                                   legend_number=2000)):
            state = FSM_action.rank_stop_detection_state()

        self.assertEqual(
            {"enabled": True, "mode": "legend_number", "legend_number": 2000,
             "last_number": 1234, "last_legend": True, "last_raw": "1234",
             "last_empty": False, "last_phase": "点「开始」前",
             "triggered": True,
             "stop_reason": "已上传说到传说 1234 名"},
            state)


class PostGameStartButtonTests(unittest.TestCase):
    """「开始」按钮：框可校准，点击点取框中心，检测有兜底区域。"""

    def test_calibrated_box_decides_the_click_point(self):
        cases = (
            # (post_game_start_roi, 期望点击点)
            (None, (1400, 900)),                        # 默认框中心
            ((1500, 850, 1600, 950), (1550, 900)),
            ((5, 5, 4, 4), (1400, 900)),                # 非法 → 回退默认
            ((1325, 865, 1480, 925), (1402, 895)),      # 拖过框 → 中心跟着变
        )
        for box, expected in cases:
            with self.subTest(box=box):
                config = SimpleNamespace(post_game_start_roi=box)
                with patch.object(FSM_action, "recommendation_config",
                                  config):
                    point = FSM_action.post_game_start_point()

                self.assertEqual(expected, point)

    def test_click_uses_the_centre(self):
        clicks = []
        config = SimpleNamespace(post_game_start_roi=(1500, 850, 1600, 950))

        with (
            patch.object(FSM_action, "recommendation_config", config),
            patch.object(FSM_action.click, "left_click",
                         side_effect=lambda *a: clicks.append(a)),
            patch.object(FSM_action, "info_print"),
        ):
            FSM_action.click_post_game_start()

        self.assertEqual([(1550, 900)], clicks)

    def test_start_keyword_and_fallback_region(self):
        detected = lines(("开始", 0.9))
        traditional = lines(("開始", 0.8))
        low_confidence = lines(("开始", 0.3))
        other = lines(("继续", 0.9))

        with patch.object(FSM_action, "_ocr_lines", return_value=detected):
            self.assertTrue(FSM_action.start_button_present())
        with patch.object(FSM_action, "_ocr_lines", return_value=traditional):
            self.assertTrue(FSM_action.start_button_present())
        for ocr in (low_confidence, other, None):
            with patch.object(FSM_action, "_ocr_lines", return_value=ocr):
                self.assertFalse(FSM_action.start_button_present())

        # 校准框读不到时用兜底区域再读一次
        attempted = []

        def fake_ocr(box, scale, tag):
            attempted.append(tuple(box))
            if len(attempted) == 1:
                return other
            return detected

        with patch.object(FSM_action, "_ocr_lines", side_effect=fake_ocr):
            self.assertTrue(FSM_action.start_button_present())

        self.assertEqual(2, len(attempted))
        self.assertEqual(tuple(FSM_action._POST_GAME_START_FALLBACK_ROI),
                         attempted[1])


class QuittingBattleTests(unittest.TestCase):
    """每局结束：点前两个点直到「开始」出现 → **先读段位** → 再决定点不点「开始」。"""

    def setUp(self):
        self._saved = (FSM_action.stop_after_current_game,
                       FSM_action.quitting_flag)
        FSM_action.stop_after_current_game = False
        FSM_action.quitting_flag = False
        self.clicks = []
        self.actions = []
        self.rank_checks = []
        self.logged = []

    def tearDown(self):
        (FSM_action.stop_after_current_game,
         FSM_action.quitting_flag) = self._saved

    def _run(self, states, start_present, max_cycles=None, rank_checks=True,
             rank_hit=False):
        """states：get_state 依次返回值；start_present：bool 列表。

        rank_checks=True 时桩掉 `_check_rank_stop`（记录每次调用的 label，
        `rank_hit` 决定预读那次是否命中）。
        """
        present = list(start_present)
        state_seq = list(states)

        def fake_state():
            return (state_seq.pop(0) if state_seq
                    else FSM_action.FSM_CHOOSING_HERO)

        def fake_rank_check(**kwargs):
            self.rank_checks.append(kwargs.get("label"))
            return bool(rank_hit and kwargs.get("label") == "点「开始」前")

        patches = [
            patch.object(FSM_action.time, "sleep"),
            patch.object(FSM_action, "print_out"),
            patch.object(FSM_action, "info_print",
                         side_effect=lambda msg: self.logged.append(msg)),
            patch.object(FSM_action, "start_button_present",
                         side_effect=lambda: present.pop(0) if present else False),
            patch.object(FSM_action, "click_post_game_point",
                         side_effect=lambda: self.clicks.append(1)),
            patch.object(FSM_action.get_screen, "get_state",
                         side_effect=fake_state),
            patch.object(FSM_action.click, "run_hearthstone_action",
                         side_effect=lambda action: self.actions.append(action)),
        ]
        if rank_checks:
            patches.append(patch.object(
                FSM_action, "_check_rank_stop", side_effect=fake_rank_check))
        if max_cycles is not None:
            patches.append(patch.object(FSM_action, "_POST_GAME_MAX_CYCLES",
                                        max_cycles))
        for patcher in patches:
            patcher.start()
        try:
            return FSM_action.QuittingBattle()
        finally:
            for patcher in reversed(patches):
                patcher.stop()

    def test_clicks_reset_point_then_finishes_once_button_appears(self):
        result = self._run([FSM_action.FSM_BATTLING] * 5, [False, False, True])

        self.assertEqual(FSM_action.FSM_CHOOSING_HERO, result)
        self.assertEqual(2, len(self.clicks))
        self.assertEqual(1, len(self.actions))
        # 检测到「开始」后：先读段位（点之前），再收尾，收尾后再兜底读一次
        self.assertEqual(["点「开始」前", "点「开始」后"], self.rank_checks)

    def test_rank_is_not_read_before_the_start_button_appears(self):
        """「开始」一直没出现（最后超时）：一次段位都不该读。"""
        result = self._run([FSM_action.FSM_BATTLING] * 8, [], max_cycles=3)

        self.assertEqual(FSM_action.FSM_ERROR, result)
        self.assertEqual([], self.rank_checks)

    def test_pre_click_hit_skips_the_start_click_entirely(self):
        """预读命中：**一个「开始」都不点**，直接返回并把自动化停在本局之后。"""
        result = self._run([FSM_action.FSM_BATTLING] * 5,
                           [False, False, True], rank_hit=True)

        self.assertEqual(FSM_action.FSM_CHOOSING_HERO, result)
        self.assertEqual(2, len(self.clicks))          # 前两个点照旧
        self.assertEqual([], self.actions)             # 收尾点击一个都没做
        self.assertEqual(["点「开始」前"], self.rank_checks)
        self.assertTrue(any("先读段位" in msg for msg in self.logged))
        self.assertTrue(any("不点「开始」" in msg for msg in self.logged))

    def test_finish_clicks_only_the_start_button_centre(self):
        """收尾只点第三个点（「开始」正中心）：不点右边、不重复点中间那两下。"""
        calls = []
        config = SimpleNamespace(post_game_start_roi=(1500, 850, 1600, 950))

        with (
            patch.object(FSM_action, "print_out"),
            patch.object(FSM_action, "info_print"),
            patch.object(FSM_action.time, "sleep"),
            patch.object(FSM_action, "recommendation_config", config),
            patch.object(FSM_action, "_check_rank_stop", return_value=False),
            patch.object(FSM_action, "start_button_present",
                         side_effect=[False, True]),
            patch.object(FSM_action, "click_post_game_point"),
            patch.object(FSM_action.get_screen, "get_state",
                         return_value=FSM_action.FSM_BATTLING),
            patch.object(FSM_action.click, "run_hearthstone_action",
                         side_effect=lambda fn: fn()),
            patch.object(FSM_action.click, "commit_error_report",
                         side_effect=lambda: calls.append("commit")),
            patch.object(FSM_action.click, "left_click",
                         side_effect=lambda x, y: calls.append((x, y))),
            patch.object(FSM_action.click, "test_click",
                         side_effect=lambda: calls.append("test")),
            patch.object(FSM_action.click, "cancel_click",
                         side_effect=lambda: calls.append("cancel")),
        ):
            FSM_action.QuittingBattle()

        self.assertEqual([(1550, 900)], calls)

    def test_screen_state_wins_and_cycles_are_capped(self):
        # 屏幕已经回到选职业：不点击、直接返回该状态
        self.assertEqual(
            FSM_action.FSM_CHOOSING_HERO,
            self._run([FSM_action.FSM_CHOOSING_HERO], []))
        self.assertEqual([], self.clicks)
        self.assertEqual([], self.actions)
        self.assertEqual([], self.rank_checks)

        # 一直没出现「开始」：到轮数上限走错误处理
        self.clicks.clear()
        self.assertEqual(FSM_action.FSM_ERROR,
                         self._run([FSM_action.FSM_BATTLING] * 8, [], 3))
        self.assertEqual(3, len(self.clicks))

    def test_stop_after_current_game_exits_immediately(self):
        FSM_action.stop_after_current_game = True

        with (
            patch.object(FSM_action, "print_out"),
            patch.object(FSM_action.time, "sleep"),
            patch.object(FSM_action.get_screen, "get_state",
                         return_value=FSM_action.FSM_BATTLING),
        ):
            with self.assertRaises(SystemExit):
                FSM_action.QuittingBattle()


class ChoosingHeroRankGateTests(unittest.TestCase):
    """排队前最后一道闸：选择套牌界面读到名次就不点「开始匹配」。"""

    def setUp(self):
        self._saved = (FSM_action.stop_after_current_game,
                       FSM_action.quitting_flag,
                       FSM_action.choose_hero_count)
        FSM_action.stop_after_current_game = False
        FSM_action.quitting_flag = False
        FSM_action.choose_hero_count = 0
        self.actions = []
        self.calls = []

    def tearDown(self):
        (FSM_action.stop_after_current_game, FSM_action.quitting_flag,
         FSM_action.choose_hero_count) = self._saved

    def _run(self, hit=None, explode=False):
        def fake_check(**kwargs):
            self.calls.append(kwargs)
            if explode:
                raise RuntimeError("boom")
            return bool(hit)

        with (
            patch.object(FSM_action, "print_out"),
            patch.object(FSM_action, "info_print"),
            patch.object(FSM_action, "warn_print"),
            patch.object(FSM_action.time, "sleep"),
            patch.object(FSM_action, "_check_rank_stop",
                         side_effect=fake_check),
            patch.object(FSM_action.click, "run_hearthstone_action",
                         side_effect=lambda action: self.actions.append(action)),
        ):
            return FSM_action.ChoosingHeroAction()

    def test_rank_hit_does_not_click_match_opponent(self):
        result = self._run(hit=True)

        self.assertEqual(FSM_action.FSM_CHOOSING_HERO, result)
        self.assertEqual([], self.actions)          # 一个「开始匹配」都没点
        self.assertEqual(["选择套牌界面"],
                         [c.get("label") for c in self.calls])
        self.assertEqual(FSM_action._CHOOSING_HERO_RANK_READ_ATTEMPTS,
                         self.calls[0].get("attempts"))

    def test_rank_miss_still_queues(self):
        result = self._run(hit=False)

        self.assertEqual(FSM_action.FSM_MATCHING, result)
        self.assertEqual(1, len(self.actions))
        self.assertEqual([FSM_action.click.match_opponent], self.actions)

    def test_detection_error_never_blocks_play(self):
        result = self._run(explode=True)

        self.assertEqual(FSM_action.FSM_MATCHING, result)
        self.assertEqual(1, len(self.actions))


class WebRankStopApiTests(unittest.TestCase):
    """Web 控制台：保存配置、状态暴露（不启服务器）。"""

    def setUp(self):
        self.saved = {}

        self._patches = [
            patch.object(web_ui, "load_config",
                         side_effect=lambda: dict(self.saved)),
            patch.object(web_ui, "save_config",
                         side_effect=lambda cfg: self.saved.update(cfg)),
            patch.object(web_ui, "_log"),
        ]
        for patcher in self._patches:
            patcher.start()
        self._saved_thread = web_ui.CTRL.automation_thread
        web_ui.CTRL.automation_thread = None

    def tearDown(self):
        web_ui.CTRL.automation_thread = self._saved_thread
        for patcher in self._patches:
            patcher.stop()

    def test_save_writes_the_section_and_validates_input(self):
        result = web_ui.api_save_rank_stop(
            {"enabled": True, "mode": "legend_number", "legend_number": 500})

        self.assertTrue(result["ok"])
        self.assertEqual({"enabled": True, "mode": "legend_number",
                          "legend_number": 500}, self.saved["rank_stop"])

        refused = (
            {"enabled": True, "mode": "exact"},
            {"enabled": True, "mode": "legend_number", "legend_number": "abc"},
        )
        for body in refused:
            with self.subTest(body=body):
                self.saved.clear()
                self.assertFalse(web_ui.api_save_rank_stop(body)["ok"])
                self.assertEqual({}, self.saved)

        web_ui.api_save_rank_stop(
            {"enabled": True, "mode": "legend_number", "legend_number": 0})
        self.assertEqual(1, self.saved["rank_stop"]["legend_number"])

    def test_defaults_and_refusal_while_running(self):
        cfg = web_ui._current_rank_stop()

        self.assertFalse(cfg["enabled"])
        self.assertEqual("legend", cfg["mode"])

        web_ui.CTRL.automation_thread = object()
        result = web_ui.api_save_rank_stop({"enabled": True})

        self.assertFalse(result["ok"])
        self.assertIn("运行中", result["error"])

    def test_state_prefers_the_state_machine_and_falls_back_to_config(self):
        fsm = SimpleNamespace(rank_stop_detection_state=lambda: {
            "enabled": True, "mode": "legend", "legend_number": 1,
            "last_number": 4321, "last_legend": True, "last_raw": "4321",
            "last_empty": False, "triggered": True, "stop_reason": "已上传说"})

        with patch.object(web_ui.CTRL, "fsm", fsm):
            state = web_ui._rank_stop_state()

        self.assertEqual(4321, state["last_number"])
        self.assertTrue(state["triggered"])

        with (
            patch.object(web_ui.CTRL, "fsm", None),
            patch.object(web_ui, "_current_rank_stop",
                         return_value={"enabled": True,
                                       "mode": "legend_number",
                                       "legend_number": 300}),
        ):
            fallback = web_ui._rank_stop_state()

        self.assertTrue(fallback["enabled"])
        self.assertEqual(300, fallback["legend_number"])
        self.assertIsNone(fallback["last_raw"])


if __name__ == "__main__":
    unittest.main()
