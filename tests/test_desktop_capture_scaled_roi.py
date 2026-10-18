"""客户区非基准（1920×1080）时，ROI 抓图会被按比例缩小。

用户实测：窗口 1785×902 时换牌一直重试
`CaptureEnvironmentError:client_size_mismatch:180x229!=1785x902`。

根因：`DesktopCapture._is_roi_frame` 只认基准尺寸的 ROI 帧，缩放后的 ROI 帧被
误判成"整帧"，于是整帧新鲜度校验拿 ROI 尺寸去比客户区尺寸而报错。这里锁死
"缩放后的 ROI 帧也要被认成 ROI 帧"。
"""

import unittest
from unittest import mock

import numpy as np

from config import RecommendationConfig
from display_mapping import DisplayMapping, box_to_screen
from src.capture.desktop_capture import DesktopCapture


def _panel_pixels(height: int, width: int) -> np.ndarray:
    """造一帧「盒子面板可见」的 ROI 图（暗红标题栏 + 有像素方差）。"""
    rng = np.random.default_rng(20261013)
    pixels = rng.integers(40, 90, size=(height, width, 3), dtype=np.uint8)
    header = min(45, height)
    pixels[:header, :, 0] = 30  # B
    pixels[:header, :, 1] = 30  # G
    pixels[:header, :, 2] = 110  # R → 暗红
    return pixels


class ScaledRoiFrameTests(unittest.TestCase):
    def setUp(self):
        self.config = RecommendationConfig()

    def _scaled_roi(self, client):
        mapping = DisplayMapping(
            hwnd=1,
            client=(0, 0, client[0], client[1]),
            screen_left=0,
            screen_top=0,
            reference=(1920, 1080),
        )
        left, top, right, bottom = box_to_screen(
            self.config.recommendation_roi, mapping
        )
        return _panel_pixels(bottom - top, right - left), mapping

    def test_scaled_roi_frame_is_recognized_as_roi(self):
        pixels, mapping = self._scaled_roi((960, 540))
        capture = DesktopCapture(self.config, screen_grabber=lambda: pixels)

        with mock.patch("display_mapping.display_mapping", return_value=mapping):
            self.assertTrue(capture._is_roi_frame(pixels.shape[1], pixels.shape[0]))

    def test_scaled_roi_capture_does_not_raise_client_size_mismatch(self):
        """整条 from_array：缩放后的 ROI 帧 + 真实客户区尺寸，必须放行。"""
        pixels, mapping = self._scaled_roi((960, 540))
        capture = DesktopCapture(self.config, screen_grabber=lambda: pixels)

        with mock.patch("display_mapping.display_mapping", return_value=mapping):
            evidence = capture.from_array(
                pixels, validate_environment=True, dpi=96, client_size=(960, 540)
            )

        self.assertTrue(evidence.panel_visible)
        self.assertEqual((960, 540), evidence.client_size)

    def test_baseline_roi_frame_still_recognized_without_live_window(self):
        """拿不到实时窗口（离线/单测）时，基准尺寸的 ROI 帧仍要认。"""
        height = self.config.recommendation_roi[3] - self.config.recommendation_roi[1]
        width = self.config.recommendation_roi[2] - self.config.recommendation_roi[0]
        capture = DesktopCapture(self.config, screen_grabber=lambda: None)

        with mock.patch("display_mapping.display_mapping", return_value=None):
            self.assertTrue(capture._is_roi_frame(width, height))


if __name__ == "__main__":
    unittest.main()
