import unittest
from unittest.mock import patch

import click as hearthstone_click


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
