"""非 100% 缩放（4K/150% → DPI 144）不再拦掉推荐识别与执行。

进程声明 DPI 感知后（`display_mapping.dpi_aware()`），`GetDpiForSystem()`
返回的是**真实值**（150% → 144），而配置里的基准 `desktop_dpi` 是 96。
历史实现把「两者不等」直接判失败：

* `DesktopCapture.from_array` → `CaptureEnvironmentError(dpi_mismatch:144)`
* `RecommendationValidator.validate` → `reject("dpi")`

于是本机（4K/150%）整条自动化停摆：换牌推荐每 5s 重试一次，日志刷
「换牌推荐暂不可执行，继续重试：CaptureEnvironmentError:dpi_mismatch:144」
直到超时。但坐标本来就按**物理像素**换算（display_mapping 拿真实客户区做
缩放），缩放只影响界面控件大小，不影响「截到的图」与「要点的坐标」的对应，
所以这两处门槛都该去掉——DPI 只留作记录/诊断。

本文件锁死这个行为，防止门槛被再次加回来。
"""

import unittest
from types import SimpleNamespace

import numpy as np

from config import RecommendationConfig
from src.capture.desktop_capture import DesktopCapture
from src.recommendation_models import FrameEvidence
from src.safety.recommendation_validator import (
    ConsumedActionStore,
    RecommendationValidator,
)


def _panel_pixels(height: int, width: int) -> np.ndarray:
    """造一帧「盒子面板可见」的 ROI 图：暗红标题栏 + 有像素方差。

    条件来自 `DesktopCapture._panel_is_visible`：整体 std ≥ 8.0，且顶部 45 行
    里「暗红（r>g*1.25 且 r>b*1.15 且 r<140）」占比 > 8%。
    """
    rng = np.random.default_rng(20261013)
    pixels = rng.integers(40, 90, size=(height, width, 3), dtype=np.uint8)
    header_rows = min(45, height)
    pixels[:header_rows, :, 0] = 30   # B
    pixels[:header_rows, :, 1] = 30   # G
    pixels[:header_rows, :, 2] = 110  # R → 暗红
    return pixels


class DesktopCaptureDpiTests(unittest.TestCase):
    def setUp(self):
        self.config = RecommendationConfig()          # desktop_dpi = 96
        self.capture = DesktopCapture(
            self.config, screen_grabber=lambda: self._frame)
        left, top, right, bottom = self.config.recommendation_roi
        self._frame = _panel_pixels(bottom - top, right - left)

    def test_roi_frame_at_144_dpi_is_not_rejected(self):
        """ROI 抓图 + 系统 144 DPI（配置基准 96）：必须放行，DPI 只作记录。"""
        evidence = self.capture.from_array(
            self._frame, validate_environment=True, dpi=144)

        self.assertIsInstance(evidence, FrameEvidence)
        self.assertEqual(144, evidence.dpi)
        self.assertTrue(evidence.panel_visible)

    def test_very_high_dpi_is_also_allowed(self):
        """200% 缩放（192 DPI）同样只是记录，不是错误。"""
        evidence = self.capture.from_array(
            self._frame, validate_environment=True, dpi=192)

        self.assertEqual(192, evidence.dpi)

    def test_capture_passes_with_a_144_dpi_reader(self):
        """整条 capture() 路径（进程检查/窗口/前台）在 144 DPI 下不再抛异常。"""
        capture = DesktopCapture(
            self.config,
            screen_grabber=lambda: self._frame,
            dpi_reader=lambda: 144,
            process_checker=lambda: True,
            window_reader=lambda: (1234, True, None),
            clock=lambda: 0.0)

        evidence = capture.capture()

        self.assertEqual(144, evidence.dpi)

    def test_missing_dpi_reader_falls_back_to_config_base(self):
        """读不到 DPI（None）时退回配置基准值，行为与改造前一致。"""
        evidence = self.capture.from_array(
            self._frame, validate_environment=True, dpi=None)

        self.assertEqual(self.config.desktop_dpi, evidence.dpi)


class RecommendationValidatorDpiTests(unittest.TestCase):
    INSTRUCTION = "打出1号位随从"

    def setUp(self):
        self.validator = RecommendationValidator(RecommendationConfig())

    def _validate(self, dpi: int):
        proposed = SimpleNamespace(
            turn_number=3, log_revision=7, ocr_confidence=0.92,
            semantic_confidence=1.0, frame_id="frame-1",
            normalized_instruction=self.INSTRUCTION)
        adapted = SimpleNamespace(source_entity_id=1, target_entity_id=None)
        stable_frame = SimpleNamespace(frame_id="frame-1")
        current_frame = SimpleNamespace(
            frame_id="frame-1", dpi=dpi, window_handle=99, foreground=True,
            panel_visible=True, client_size=None)
        current_ocr = SimpleNamespace(
            frame_id="frame-1", normalized_text=self.INSTRUCTION)
        state = SimpleNamespace(game_num_turns_in_play=3, is_my_turn=True)
        return self.validator.validate(
            proposed, adapted, stable_frame, current_frame, current_ocr,
            state, 7, "PLAYER_TURN", ConsumedActionStore(), False)

    def test_144_dpi_frame_is_accepted(self):
        result = self._validate(144)

        self.assertTrue(result.accepted, result.code)

    def test_dpi_is_never_the_reject_code(self):
        """即便 DPI 与配置基准不同，拒绝码里也不该出现 "dpi"。"""
        for dpi in (96, 120, 144, 192):
            with self.subTest(dpi=dpi):
                result = self._validate(dpi)
                self.assertNotEqual("dpi", result.code)


if __name__ == "__main__":
    unittest.main()
