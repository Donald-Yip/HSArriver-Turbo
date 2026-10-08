"""鼠标复位点：复位必须落在安全位置，且只移动、不点击。

回归背景：旧的复位点 (480, 540) 每局结束后停在牌组选择界面第二行第一个卡组上，
复位后偶发误选中卡组；用户最终指定屏幕左上角 (70, 60) 作为待命点。
"""

import unittest
from unittest.mock import patch

import click as hearthstone_click

RESET = (70, 60)


class RecordingMouse:
    """只记录事件、不真的移动系统鼠标。"""

    def __init__(self):
        self.events = []

    @property
    def position(self):
        return None

    @position.setter
    def position(self, value):
        self.events.append(("position", value))

    def press(self, button):
        self.events.append(("press", button))

    def release(self, button):
        self.events.append(("release", button))


class MouseResetPositionTests(unittest.TestCase):
    def test_reset_position_is_the_safe_spot(self):
        self.assertEqual(RESET, hearthstone_click.MOUSE_RESET_POS)

    def test_center_mouse_only_moves_the_pointer(self):
        mouse = RecordingMouse()

        with patch.object(hearthstone_click, "Controller", return_value=mouse):
            hearthstone_click.center_mouse()

        self.assertEqual([("position", RESET)], mouse.events)

    def test_park_mouse_moves_without_clicking(self):
        mouse = RecordingMouse()

        with (
            patch.object(hearthstone_click, "Controller", return_value=mouse),
            patch.object(hearthstone_click.time, "sleep"),
        ):
            hearthstone_click.park_mouse()

        self.assertEqual([("position", RESET)], mouse.events)

    def test_action_session_parks_at_the_safe_spot(self):
        mouse = RecordingMouse()

        with (
            patch.object(hearthstone_click, "Controller", return_value=mouse),
            patch.object(hearthstone_click.time, "sleep"),
        ):
            with hearthstone_click.hearthstone_action_session():
                pass

        self.assertEqual([("position", RESET)], mouse.events)


class PostGameClickPointTests(unittest.TestCase):
    """每局结束的推进点击：**屏幕中间那两个点**，不点右边。

    回归背景：结算界面要点好几下才会出现「开始」按钮。用户口径是每轮点中间那两下
    ——`(1100,820)`（奇怪的错误提示）+ `(960,650)`（断线时取消），也就是
    `commit_error_report()` 用的两个位置；右边那些辅助点会被右上角日志浮窗吃掉，
    也可能误点到下一屏。第三个点（「开始」按钮正中心）留给收尾。
    """

    def test_two_middle_points_are_clicked_each_round(self):
        import FSM_action

        calls = []

        with (
            patch.object(hearthstone_click, "left_click",
                         side_effect=lambda *a: calls.append(a)),
            patch.object(hearthstone_click, "cancel_click",
                         side_effect=lambda: calls.append("cancel")),
            patch.object(hearthstone_click, "test_click",
                         side_effect=lambda: calls.append("test")),
        ):
            FSM_action.click_post_game_point()

        self.assertEqual([(1100, 820), (960, 650)], calls)
        self.assertEqual(((1100, 820), (960, 650)),
                         hearthstone_click.ERROR_REPORT_POINTS)


if __name__ == "__main__":
    unittest.main()
