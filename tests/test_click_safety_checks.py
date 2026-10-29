import unittest
import threading
from unittest.mock import patch

import click as hearthstone_click
from hslegend.adapters.legacy.actions import ActionCancelled
from manual_controller import ClickExecutor, EndTurnAction, ManualController


class RecordingMouse:
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


class ClickSafetyContractTests(unittest.TestCase):
    def test_manual_action_reports_stop_without_cleanup_click(self):
        class Clicks:
            def __init__(self):
                self.cleanup = 0

            def end_turn(self):
                raise ActionCancelled("stopped")

            def cancel_click(self):
                self.cleanup += 1

        clicks = Clicks()
        controller = ManualController(
            output_func=lambda _message: None,
            executor=ClickExecutor(click_module=clicks),
        )
        state = type("State", (), {"is_my_turn": True,
                                   "game_num_turns_in_play": 3})()

        result = controller.execute(EndTurnAction(turn_number=3), state)

        self.assertFalse(result.executed)
        self.assertTrue(result.cancelled)
        self.assertEqual(0, clicks.cleanup)

    def test_global_stop_prevents_a_click_before_it_is_sent(self):
        event = threading.Event()
        event.set()
        with (
            patch.object(hearthstone_click, "get_HS_hwnd", return_value=101),
            patch.object(hearthstone_click, "Controller") as controller,
            hearthstone_click.action_cancellation(event),
        ):
            with self.assertRaises(ActionCancelled):
                hearthstone_click.click_button(
                    960, 650, hearthstone_click.Button.left)

        controller.assert_not_called()

    def test_global_stop_between_two_coordinate_clicks_skips_the_second(self):
        event = threading.Event()

        def choose_card(_index, _count):
            event.set()

        with (
            patch.object(hearthstone_click, "choose_card", side_effect=choose_card),
            patch.object(hearthstone_click, "left_click") as second_click,
            hearthstone_click.action_cancellation(event),
        ):
            with self.assertRaises(ActionCancelled):
                hearthstone_click.choose_and_use_spell(0, 3)

        second_click.assert_not_called()

    def test_unknown_hearthstone_window_is_not_clicked(self):
        with (
            patch.object(hearthstone_click, "get_HS_hwnd", return_value=0),
            patch.object(hearthstone_click, "Controller") as controller,
            patch.object(hearthstone_click, "rand_sleep"),
        ):
            clicked = hearthstone_click.click_button(
                960, 650, hearthstone_click.Button.left)

        self.assertFalse(clicked)
        controller.assert_not_called()

    def test_unverified_hearthstone_point_is_not_clicked(self):
        with (
            patch.object(hearthstone_click, "get_HS_hwnd", return_value=101),
            patch.object(
                hearthstone_click,
                "point_targets_hearthstone",
                return_value=False,
            ),
            patch.object(hearthstone_click, "Controller") as controller,
            patch.object(hearthstone_click, "rand_sleep"),
        ):
            clicked = hearthstone_click.click_button(
                960, 650, hearthstone_click.Button.left)

        self.assertFalse(clicked)
        controller.assert_not_called()

    def test_unknown_coordinates_are_not_clicked(self):
        with (
            patch.object(hearthstone_click, "get_HS_hwnd", return_value=101),
            patch.object(hearthstone_click, "Controller") as controller,
            patch.object(hearthstone_click, "rand_sleep"),
        ):
            clicked = hearthstone_click.click_button(
                None, "unknown", hearthstone_click.Button.left)

        self.assertFalse(clicked)
        controller.assert_not_called()

    def test_background_window_without_a_verified_activation_point_is_not_clicked(self):
        mouse = RecordingMouse()

        with (
            patch.object(hearthstone_click, "get_HS_hwnd", return_value=101),
            patch.object(
                hearthstone_click.win32gui,
                "GetForegroundWindow",
                return_value=202,
            ),
            patch.object(
                hearthstone_click,
                "point_targets_hearthstone",
                side_effect=(True, False),
            ),
            patch.object(
                hearthstone_click,
                "side_point",
                return_value=(1800, 500),
            ),
            patch.object(hearthstone_click, "Controller", return_value=mouse),
            patch.object(hearthstone_click, "rand_sleep"),
        ):
            clicked = hearthstone_click.click_button(
                960, 650, hearthstone_click.Button.left)

        self.assertFalse(clicked)
        self.assertEqual([], mouse.events)

    def test_verified_background_window_activates_then_clicks_target(self):
        mouse = RecordingMouse()

        with (
            patch.object(hearthstone_click, "get_HS_hwnd", return_value=101),
            patch.object(
                hearthstone_click.win32gui,
                "GetForegroundWindow",
                return_value=202,
            ),
            patch.object(
                hearthstone_click,
                "point_targets_hearthstone",
                return_value=True,
            ),
            patch.object(
                hearthstone_click,
                "side_point",
                return_value=(1800, 500),
            ),
            patch.object(hearthstone_click, "Controller", return_value=mouse),
            patch.object(hearthstone_click, "rand_sleep"),
        ):
            clicked = hearthstone_click.click_button(
                960, 650, hearthstone_click.Button.left)

        self.assertTrue(clicked)
        self.assertEqual([
            ("position", (1800, 500)),
            ("press", hearthstone_click.Button.right),
            ("release", hearthstone_click.Button.right),
            ("position", (960, 650)),
            ("press", hearthstone_click.Button.left),
            ("release", hearthstone_click.Button.left),
        ], mouse.events)
    def test_verified_foreground_hearthstone_point_is_clicked(self):
        mouse = RecordingMouse()

        with (
            patch.object(hearthstone_click, "get_HS_hwnd", return_value=101),
            patch.object(
                hearthstone_click.win32gui,
                "GetForegroundWindow",
                return_value=101,
            ),
            patch.object(
                hearthstone_click,
                "point_targets_hearthstone",
                return_value=True,
            ),
            patch.object(hearthstone_click, "Controller", return_value=mouse),
            patch.object(hearthstone_click, "rand_sleep"),
        ):
            clicked = hearthstone_click.click_button(
                960, 650, hearthstone_click.Button.left)

        self.assertTrue(clicked)
        self.assertEqual([
            ("position", (960, 650)),
            ("press", hearthstone_click.Button.left),
            ("release", hearthstone_click.Button.left),
        ], mouse.events)


if __name__ == "__main__":
    unittest.main()
