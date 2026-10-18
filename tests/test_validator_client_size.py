"""窗口化（客户区≠基准 1920×1080）时，证据新鲜度必须按**实时客户区**判，而不是基准。

历史实现 `_client_size_matches` 拿 `frame.client_size` 去比 `config.desktop_size`
（恒为 1920×1080）：窗口化缩到 1785×902 时永远不等 → 整条出牌流程被拒成
`desktop_size`。用户实测"缩放以后无法换牌/出牌"。这里锁死"跟实时客户区比"。
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from config import RecommendationConfig
from src.safety import recommendation_validator as rv
from src.safety.recommendation_validator import (
    ConsumedActionStore,
    RecommendationValidator,
)


class ClientSizeFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.config = RecommendationConfig()

    @staticmethod
    def _frame(client_size):
        return SimpleNamespace(client_size=client_size)

    def test_windowed_client_matching_live_is_fresh(self):
        with mock.patch.object(rv, "_live_client_size", return_value=(1785, 902)):
            self.assertTrue(
                rv._client_size_matches(self._frame((1785, 902)), self.config)
            )

    def test_client_differing_from_live_is_stale(self):
        with mock.patch.object(rv, "_live_client_size", return_value=(1785, 902)):
            self.assertFalse(
                rv._client_size_matches(self._frame((1920, 1080)), self.config)
            )

    def test_missing_client_size_skips_the_check(self):
        self.assertTrue(rv._client_size_matches(self._frame(None), self.config))

    def test_falls_back_to_reference_when_live_unknown(self):
        with mock.patch.object(rv, "_live_client_size", return_value=None):
            self.assertTrue(
                rv._client_size_matches(self._frame((1920, 1080)), self.config)
            )
            self.assertFalse(
                rv._client_size_matches(self._frame((1785, 902)), self.config)
            )


class WindowedValidateTests(unittest.TestCase):
    """整条 validate：窗口化客户区（与实时一致）不再被拒成 desktop_size。"""

    INSTRUCTION = "打出1号位随从"

    def _validate(self, client_size):
        validator = RecommendationValidator(RecommendationConfig())
        proposed = SimpleNamespace(
            turn_number=3,
            log_revision=7,
            ocr_confidence=0.92,
            semantic_confidence=1.0,
            frame_id="frame-1",
            normalized_instruction=self.INSTRUCTION,
        )
        adapted = SimpleNamespace(source_entity_id=1, target_entity_id=None)
        stable_frame = SimpleNamespace(frame_id="frame-1")
        current_frame = SimpleNamespace(
            frame_id="frame-1",
            dpi=96,
            window_handle=99,
            foreground=True,
            panel_visible=True,
            client_size=client_size,
        )
        current_ocr = SimpleNamespace(
            frame_id="frame-1", normalized_text=self.INSTRUCTION
        )
        state = SimpleNamespace(game_num_turns_in_play=3, is_my_turn=True)
        return validator.validate(
            proposed,
            adapted,
            stable_frame,
            current_frame,
            current_ocr,
            state,
            7,
            "PLAYER_TURN",
            ConsumedActionStore(),
            False,
        )

    def test_windowed_frame_is_accepted(self):
        with mock.patch.object(rv, "_live_client_size", return_value=(1785, 902)):
            result = self._validate((1785, 902))

        self.assertTrue(result.accepted, result.code)


if __name__ == "__main__":
    unittest.main()
