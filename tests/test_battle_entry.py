# -*- coding: utf-8 -*-
"""界面识别（battle_entry）的离线测试。

全部用**合成帧**：把自带的模板贴到空白画面上，或者画一片"名牌白字"的卡组格。
不碰真实屏幕、不碰鼠标、不读真机配置 —— 判定用的那几个实测数字（1.0000 / 0.3105 /
0.461 / 0.0000）都钉在断言里。

**只有 6 条**（每条对应一个"会让脚本点错东西"的具体失效模式）：
① 只认文字 + 空白画面绝不含糊；② 框错地方只会找不到、不会认错；
③ 框偏 ±40px 仍能认（这才叫可校准）；④ 9 个卡组点的几何；
⑤ 单格保护（不像卡组就一格都不点）；⑥ 流程安全（超时只点中心、不盲点）。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import cv2
import numpy as np

import battle_entry as be


# ---------------------------------------------------------------- 合成帧工具
def blank_frame(value: int = 120) -> np.ndarray:
    """一张空白 BGR 画面（基准尺寸）。"""
    return np.full(
        (be.REFERENCE_CLIENT[1], be.REFERENCE_CLIENT[0], 3), int(value), dtype=np.uint8
    )


def template_gray(kind: str) -> np.ndarray:
    image, reason = be.load_template(kind)
    assert image is not None, f"模板不可用：{reason}"
    return image


def paste(frame: np.ndarray, gray: np.ndarray, x: int, y: int) -> np.ndarray:
    """把灰度模板贴到画面上（三通道同值，等价于灰度图）。"""
    height, width = gray.shape
    frame[y : y + height, x : x + width, :] = gray[:, :, None]
    return frame


def paint_deck_plates(frame: np.ndarray, config=None) -> np.ndarray:
    """在 9 个卡组格的"名牌带"上画**暗色名牌 + 白字**（让每格都像真的卡组）。

    真机上名牌是暗色半透明条（带内平均亮度只有 51~86）+ 白字，不是一整条白 ——
    所以这里也照这个画：先铺暗底，再点几块白字。一整条白（例如白屏）必须**不算** 卡组。
    """
    for left, top, right, bottom in be.deck_bounds(config):
        center_y = (top + bottom) // 2
        y0 = max(0, center_y - be.DECK_PLATE_BAND)
        y1 = min(frame.shape[0], center_y + be.DECK_PLATE_BAND)
        frame[y0:y1, left:right, :] = 60  # 暗色名牌
        middle = (y0 + y1) // 2
        frame[middle - 8 : middle + 8, left + 40 : left + 100, :] = 245  # 白字
        frame[middle - 8 : middle + 8, left + 120 : left + 160, :] = 245
    return frame


def main_menu_frame(config=None) -> np.ndarray:
    """主界面：把「传统对战」模板贴在它自己的框位置上。"""
    left, top, _right, _bottom = be.traditional_box(config)
    return paste(blank_frame(), template_gray("traditional"), left, top)


def deck_select_frame(config=None, *, with_start: bool = True) -> np.ndarray:
    """选择套牌界面：贴上「选择套牌」标题（可选「开始」）+ 9 个卡组格都有名牌白字。

    `with_start=False` 模拟**还没选中卡组**（「开始」不出现）—— 这正是用户
    2026-10-11 反馈的死循环场景：只认「开始」的话这里会被判成 unknown，
    反复点画面中心直到超时。改用标题识别后，有没有「开始」都能认出来。
    """
    frame = blank_frame()
    left, top, _right, _bottom = be.deck_select_title_box(config)
    paste(frame, template_gray("deck_select_title"), left, top)
    if with_start:
        left, top, _right, _bottom = be.start_box(config)
        paste(frame, template_gray("start"), left, top)
    return paint_deck_plates(frame, config)


def bright_textured_frame() -> np.ndarray:
    """亮、但有"纹理"的画面 —— 模仿真机上"炉石还没画出来"的那块白屏。

    真机实测：那种白屏 std 有 29~63（骗得过"太平"那道检查），却一个深色像素都没有。
    这里用横条纹造出同样的性质（std≈15、最暗 225）。
    """
    frame = blank_frame(255)
    frame[::4, :, :] = 225
    return frame


def make_config(**overrides):
    base = dict(
        main_menu_battle_roi=be.MAIN_MENU_BATTLE_DEFAULT,
        deck_select_title_roi=be.DECK_SELECT_TITLE_DEFAULT,
        deck_grid_roi=be.DECK_GRID_DEFAULT,
        post_game_start_roi=be.START_BOX_DEFAULT,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeClock:
    """确定性时钟：sleep 就是把时间往前推，避免测试真的等 3 秒。"""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept += float(seconds)
        self.now += float(seconds)


class Recorder:
    """记录每一次点击的坐标（用来断言"有没有乱点"）。"""

    def __init__(self) -> None:
        self.clicks = []

    def __call__(self, x, y) -> None:
        self.clicks.append((int(x), int(y)))


def sequence_capture(frames):
    state = {"index": 0}

    def capture(config=None):
        index = min(state["index"], len(frames) - 1)
        state["index"] += 1
        return (frames[index], None)

    return capture


# ================================================================ ① 只认文字
class TextOnlyRecognitionTests(unittest.TestCase):
    def test_recognizes_both_screens_and_never_guesses_on_a_blank_one(self):
        config = make_config()

        main = be.identify(main_menu_frame(config), config)
        self.assertEqual("main_menu", main["state"])
        self.assertGreaterEqual(main["traditional"]["score"], 0.99)
        # 点的是**认到的文字位置**（实测 (961,330)），不是别的什么
        self.assertEqual([961, 330], main["traditional"]["point"])
        # 「开始」在主界面上不该被认出来（实测 0.1625）
        self.assertLess(main["start"]["score"], be.NCC_MIN)

        deck = be.identify(deck_select_frame(config), config)
        self.assertEqual("deck_select", deck["state"])
        self.assertGreaterEqual(deck["deck_title"]["score"], 0.99)
        self.assertGreaterEqual(deck["start"]["score"], 0.99)
        self.assertEqual(9, deck["deck"]["slots_found"])

        # catch_screen 给的是 BGRA 四通道：必须和 BGR 走同一条判定路径
        bgra = cv2.cvtColor(main_menu_frame(config), cv2.COLOR_BGR2BGRA)
        self.assertEqual("main_menu", be.identify(bgra, config)["state"])

        # 空白/黑屏（游戏还没画出来）：分数都必须是 0.0000 ——
        # 判定通过就会去点按钮，所以"拿不准"必须是**不点**。
        for value in (120, 0, 255):
            blank = be.identify(blank_frame(value), config)
            self.assertEqual("unknown", blank["state"])
            self.assertEqual(0.0, blank["traditional"]["score"])
            self.assertEqual(0.0, blank["deck_title"]["score"])
            self.assertEqual(0.0, blank["start"]["score"])

    def test_deck_select_is_recognized_without_the_start_button(self):
        """没选中卡组时「开始」不出现，也必须认出选卡组界面（用户反馈的死循环）。

        旧逻辑只认「开始」，这种画面会被判成 unknown → 反复点画面中心直到超时。
        改用「选择套牌」标题后，标题在就认得出。
        """
        config = make_config()
        frame = deck_select_frame(config, with_start=False)

        ui = be.identify(frame, config)
        self.assertEqual("deck_select", ui["state"])
        self.assertGreaterEqual(ui["deck_title"]["score"], 0.99)
        self.assertLess(ui["start"]["score"], be.NCC_MIN)  # 确实没有「开始」

    def test_learn_refuses_a_frame_that_is_not_drawn_and_keeps_the_template(self):
        """画面还没画出来（白屏）时不能重裁模板，更不能覆盖掉能用的那个。

        真机实测：那种白屏的 std 有 29~63（骗得过"太平"那道检查），但深色像素
        只有 0.00%~0.24%（真界面 8.1%）—— 用它把关，白屏一律拒绝。
        """
        path = be.TEMPLATE_DIR / be.TEMPLATE_FILE["traditional"]
        before = path.read_bytes()

        result = be.learn_template(
            "all", frame=bright_textured_frame(), config=make_config()
        )

        self.assertFalse(result["ok"])
        self.assertEqual({}, result["learned"])
        self.assertEqual(
            {"traditional", "deck_select_title", "start"}, set(result["refused"])
        )
        self.assertIn("深色", result["message"])
        self.assertEqual(before, path.read_bytes(), "绝不该覆盖掉能用的模板")


# ================================================================ ② 框错地方
class WrongPlaceTests(unittest.TestCase):
    def test_text_outside_the_window_is_not_main_menu(self):
        """模板是"身份"：不在框 ±40px 里就是**找不到**（不点），不会认错东西。

        模拟"框被拖到了旁边的「酒馆战棋」上"：把「传统对战」的文字放在酒馆战棋
        那一行（下方 77px，正好在 ±40 窗口之外）。
        """
        config = make_config()
        left, top, _right, _bottom = be.traditional_box(config)
        frame = paste(blank_frame(), template_gray("traditional"), left, top + 77)

        ui = be.identify(frame, config)
        self.assertEqual("unknown", ui["state"])
        self.assertLess(ui["traditional"]["score"], be.NCC_MIN)


# ================================================================ ③ 校准容差
class CalibrationToleranceTests(unittest.TestCase):
    def test_box_can_be_off_by_up_to_the_search_radius(self):
        """框拖得不那么准也能认（±40px 窗口）—— 这是"可校准"的实际含义。"""
        frame = main_menu_frame(make_config())
        base = be.MAIN_MENU_BATTLE_DEFAULT
        for shift in ((0, 0), (20, 0), (0, 20), (40, 0), (0, 40)):
            config = make_config(
                main_menu_battle_roi=(
                    base[0] + shift[0],
                    base[1] + shift[1],
                    base[2] + shift[0],
                    base[3] + shift[1],
                )
            )
            ui = be.identify(frame, config)
            self.assertEqual("main_menu", ui["state"], f"偏移 {shift} 应该仍能认到")
            self.assertEqual(
                [961, 330],
                ui["traditional"]["point"],
                "认到的位置应始终是文字真正所在处",
            )

        # 拖出窗口之外（400px）就该什么都找不到，而不是硬认
        far = make_config(
            main_menu_battle_roi=(base[0] + 400, base[1], base[2] + 400, base[3])
        )
        self.assertEqual("unknown", be.identify(frame, far)["state"])


# ================================================================ ④ 卡组几何
class DeckGeometryTests(unittest.TestCase):
    def test_nine_slots_match_the_measured_positions(self):
        config = make_config()
        points = be.deck_slot_points(config)
        self.assertEqual(9, len(points))
        self.assertEqual(
            [
                (486, 318),
                (727, 318),
                (968, 318),
                (486, 534),
                (727, 534),
                (968, 534),
                (486, 750),
                (727, 750),
                (968, 750),
            ],
            points,
        )
        # 与实测（列 486/729/968、行 318/534/750）逐点对比，偏差 ≤3px
        measured = [
            (486, 318),
            (729, 318),
            (968, 318),
            (486, 534),
            (729, 534),
            (968, 534),
            (486, 750),
            (729, 750),
            (968, 750),
        ]
        for (x, y), (mx, my) in zip(points, measured):
            self.assertLessEqual(abs(x - mx), 3)
            self.assertLessEqual(abs(y - my), 3)

        # 序号越界要立刻报错，而不是点到别的格
        for bad in (0, 10, -1):
            with self.assertRaises(ValueError):
                be.deck_slot_point(bad, config)

        # 用户把整个网格框挪了 → 9 个点跟着走
        moved = make_config(deck_grid_roi=(466, 310, 1189, 958))
        self.assertEqual((586, 418), be.deck_slot_point(1, moved))


# ================================================================ ⑤ 单格保护
class DeckSlotGuardTests(unittest.TestCase):
    def test_only_clicks_a_slot_that_looks_like_a_deck(self):
        config = make_config()

        # 空白画面（例如结算界面）：9 格都没有名牌文字 → 一格都不点
        clicker = Recorder()
        for blank in (blank_frame(), blank_frame(255)):
            result = be.select_deck(
                8,
                capture=lambda config=None, f=blank: (f, None),
                click_at_fn=clicker,
                config=config,
            )
            self.assertFalse(result["ok"])
            self.assertTrue(result["skipped"])
        # **白屏也算**：真机上实测到过"炉石还没画出来"的白屏让 9 格全都"有名牌白字"
        # （白白的一片本来就满足"亮且低饱和"），所以名牌必须是暗色条。
        self.assertEqual([0] * 9, be.plate_pixels(blank_frame(255), config))
        self.assertEqual([], clicker.clicks, "不该有任何点击")

        # 真的有卡组时，点的是那一格的中心
        clicker = Recorder()
        frame = deck_select_frame(config)
        hit = be.select_deck(
            8,
            capture=lambda config=None: (frame, None),
            click_at_fn=clicker,
            config=config,
        )
        self.assertTrue(hit["ok"])
        self.assertEqual([(727, 750)], clicker.clicks)

    def test_configured_deck_retries_while_the_plates_are_not_drawn(self):
        """去掉「进界面先等 2 秒」后：名牌还没画出来要短重试，而不是直接不选。"""
        config = make_config()
        clicker, clock = Recorder(), FakeClock()
        result = be.select_deck_if_configured(
            settings={"deck_index": 8},
            capture=sequence_capture([blank_frame(), deck_select_frame(config)]),
            click_at_fn=clicker,
            sleep=clock.sleep,
            config=config,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(0.3, clock.slept)
        self.assertEqual([(727, 750)], clicker.clicks)

    def test_configured_deck_gives_up_after_the_short_retries(self):
        """一直画不出来：重试 3 次后按原逻辑跳过，一次都不点。"""
        clicker, clock = Recorder(), FakeClock()
        result = be.select_deck_if_configured(
            settings={"deck_index": 8},
            capture=lambda config=None: (blank_frame(), None),
            click_at_fn=clicker,
            sleep=clock.sleep,
            config=make_config(),
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["skipped"])
        self.assertEqual([], clicker.clicks)
        self.assertEqual(0.6, clock.slept)


# ================================================================ ⑥ 流程不乱点
class FlowSafetyTests(unittest.TestCase):
    def test_timeout_clicks_only_the_safe_center_point(self):
        """认不出界面时只能点"安全中心点"，超时就退出 —— 绝不盲点「传统对战」。"""
        clicker = Recorder()
        clock = FakeClock()
        result = be.enter_battle(
            window_present=lambda: True,
            capture=lambda config=None: (blank_frame(), None),
            click_at_fn=clicker,
            sleep=clock.sleep,
            clock=clock,
            interval=3.0,
            main_menu_timeout=6.0,
            config=make_config(),
        )

        self.assertFalse(result["ok"])
        self.assertEqual("timeout_main_menu", result["stage"])
        for point in clicker.clicks:
            self.assertEqual(
                be.CENTER_DISMISS_POINT, point, "超时路径里只允许点安全中心点"
            )
        # 两次"点中心 + 等 3 秒"，第三次判定时到超时
        self.assertEqual(2, len(clicker.clicks))
        self.assertEqual(6.0, clock.slept)
        stages = [step["stage"] for step in result["steps"]]
        self.assertNotIn("click_traditional", stages)
        self.assertNotIn("select_deck", stages)

    def test_happy_path_and_abort(self):
        config = make_config()
        # 第一次判定就是主界面 → 一次中心点都不该点；认到文字→点它，再点第 8 格
        clicker, clock = Recorder(), FakeClock()
        result = be.enter_battle(
            deck_index=8,
            click_start=False,
            window_present=lambda: True,
            # 多一帧：enter_battle 会先抓一帧判断"是不是已经站在选套牌界面"
            # （是就直接从选卡组接手，不再等主界面也不会点画面中心）。
            capture=sequence_capture(
                [
                    main_menu_frame(config),
                    main_menu_frame(config),
                    deck_select_frame(config),
                ]
            ),
            click_at_fn=clicker,
            sleep=clock.sleep,
            clock=clock,
            interval=3.0,
            main_menu_timeout=30.0,
            deck_select_timeout=30.0,
            config=config,
        )
        self.assertTrue(result["ok"])
        self.assertEqual("done", result["stage"])
        self.assertEqual([(961, 330), (727, 750)], clicker.clicks)
        self.assertEqual(0.0, clock.slept)
        self.assertEqual(
            [
                "launch",
                "wait_main_menu",
                "click_traditional",
                "wait_deck_select",
                "select_deck",
            ],
            [step["stage"] for step in result["steps"]],
        )

        # 收到停止请求：一次都不能点
        clicker = Recorder()
        aborted = be.enter_battle(
            window_present=lambda: True,
            capture=lambda config=None: (main_menu_frame(), None),
            click_at_fn=clicker,
            sleep=FakeClock().sleep,
            clock=FakeClock(),
            should_abort=lambda: True,
            config=config,
        )
        self.assertFalse(aborted["ok"])
        self.assertEqual("aborted", aborted["stage"])
        self.assertEqual([], clicker.clicks, "收到停止请求后一次都不该点")

    def test_polling_is_fast_but_center_clicks_stay_slow(self):
        """0.5s 轮询：界面画出来 1 秒内就走；但点中心仍只每 3 秒最多一下。"""
        config = make_config()
        clicker, clock = Recorder(), FakeClock()
        result = be.wait_for_main_menu(
            timeout=10.0,
            interval=0.5,
            capture=sequence_capture(
                [blank_frame(), blank_frame(), main_menu_frame(config)]
            ),
            click_at_fn=clicker,
            sleep=clock.sleep,
            clock=clock,
            config=config,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(1.0, clock.slept, "不能再等满一个 3 秒轮询")
        # t=0 空白 → 点一次中心；t=0.5 还没到 3 秒 → 不点
        self.assertEqual([be.CENTER_DISMISS_POINT], clicker.clicks)

    def test_timeout_keeps_the_three_second_click_cadence(self):
        """0.5s 轮询下超时路径也不连点：2 秒里只点 1 下中心。"""
        clicker, clock = Recorder(), FakeClock()
        result = be.wait_for_main_menu(
            timeout=2.0,
            interval=0.5,
            capture=lambda config=None: (blank_frame(), None),
            click_at_fn=clicker,
            sleep=clock.sleep,
            clock=clock,
            config=make_config(),
        )

        self.assertFalse(result["ok"])
        self.assertEqual("timeout_main_menu", result["reason"])
        self.assertEqual([be.CENTER_DISMISS_POINT], clicker.clicks)
        self.assertEqual(2.0, clock.slept)


class ToReferenceInterpolationTests(unittest.TestCase):
    def test_upscale_uses_a_smooth_filter_not_nearest(self):
        """客户区小于基准时 to_reference 是**放大**：INTER_AREA 放大≈最近邻（块状），
        必须换成 INTER_CUBIC，否则小窗口下模板匹配分数明显掉。"""
        checker = np.zeros((2, 2, 3), dtype=np.uint8)
        checker[0, 1] = (255, 255, 255)
        checker[1, 0] = (255, 255, 255)

        out = be.to_reference(checker)

        self.assertEqual((1080, 1920, 3), out.shape)
        nearest = cv2.resize(
            checker, be.REFERENCE_CLIENT, interpolation=cv2.INTER_NEAREST
        )
        self.assertFalse(np.array_equal(out, nearest))
        # 最近邻只会有 0/255 两种值；平滑插值在黑白格交界处会给出中间灰。
        center = int(out[540, 960, 0])
        self.assertGreater(center, 0)
        self.assertLess(center, 255)

    def test_reference_sized_frame_is_returned_unchanged(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertIs(frame, be.to_reference(frame))


if __name__ == "__main__":
    unittest.main()
