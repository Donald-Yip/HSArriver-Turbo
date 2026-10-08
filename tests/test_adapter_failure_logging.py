# -*- coding: utf-8 -*-
"""适配失败必须留痕。

线上实测：`choose_one_name_not_unique` 连续重试了 3090 次、最长空转 1 分半，
但日志里**一条推荐原文都没有**——因为 `[推荐]` 那行原来打在适配之后，适配一抛错
就什么都看不到，事后根本无法判断"盒子到底写了什么"。

这里锁住：适配抛错时，归一化指令和 OCR 原文都要先打出来。
"""

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from src.flow.recommendation_flow import FlowStepStatus, RecommendationFlow
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_models import FrameEvidence, OcrEvidence
from src.safety.recommendation_validator import RecommendationValidator


def _frame(frame_id, exact_hash="before"):
    return FrameEvidence(
        frame_id=frame_id, captured_at=0.0, desktop_size=(1920, 1080), dpi=96,
        window_handle=1, foreground=True,
        recommendation_roi=(7, 32, 278, 970), exact_hash=exact_hash,
        perceptual_hash="0" * 64, panel_visible=True)


def _evidence(frame_id, text):
    return OcrEvidence(frame_id=frame_id, created_at=0.0, lines=(),
                       normalized_text=text, confidence=0.95,
                       backend="test", preprocessing="test")


RAW_PANEL = ("打法参考B\n结束回合\n"
             "打法参考A\n打出2号位法术\n暮光侵扰\n目标是对方1号位\n死亡侍僧\n"
             "选择卡牌\n操纵荆棘")


class StaticCapture:
    def __init__(self):
        self.frames = iter((_frame("stable"), _frame("current"),
                            _frame("after", "after")))

    def capture(self, ocr_panel_ok=False):
        return next(self.frames)

    @staticmethod
    def crop_recommendation(current_frame):
        return current_frame


class StaticReader:
    @staticmethod
    def read(frame_supplier, _roi):
        return _evidence(frame_supplier().frame_id, RAW_PANEL)

    @staticmethod
    def read_frame(current_frame, _roi):
        return _evidence(current_frame.frame_id, RAW_PANEL)


class ExplodingAdapter:
    """模拟适配层抛错（线上就是 RecommendationStateError 这一类）。"""

    def __call__(self, proposed, state):
        raise ValueError("boom: adapter rejected")


class FailureLoggingTests(unittest.TestCase):
    def run_step(self):
        active = SimpleNamespace(is_my_turn=True, game_num_turns_in_play=3)
        states = iter(((active, 10), (active, 10), (active, 11)))
        flow = RecommendationFlow(
            capture=StaticCapture(), reader=StaticReader(),
            parser=RecommendationParser(),
            state_supplier=lambda: next(states),
            adapter=ExplodingAdapter(),
            validator=RecommendationValidator(),
            controller=SimpleNamespace(output=print),
            sleep=lambda _s: None)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = flow.run_player_step() if hasattr(flow, "run_player_step") \
                else flow.run_player_turn_step()
        return result, buffer.getvalue()

    def test_recommendation_and_raw_ocr_are_logged_before_a_failed_adapt(self):
        result, output = self.run_step()
        self.assertEqual(FlowStepStatus.RETRY, result.status)
        self.assertIn("[推荐]", output)
        # 归一化指令（动作 + 目标 + 选择卡牌 + 选项名）
        self.assertIn("操纵荆棘", output)
        # OCR 原文（用来判断盒子把参考A还是参考B读进来了）
        self.assertIn("[推荐] 原文：", output)
        self.assertIn("暮光侵扰", output)

    def test_logging_happens_even_though_the_adapter_raised(self):
        result, output = self.run_step()
        self.assertIn("boom: adapter rejected", result.diagnostics)
        self.assertNotEqual("", output.strip())


if __name__ == "__main__":
    unittest.main()
