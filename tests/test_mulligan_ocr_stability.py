# -*- coding: utf-8 -*-
"""换牌 OCR「not stable」回归：抖动容忍 + 按“结论指纹”判稳 + 失败留痕。

背景（用户 2026-10-26 反馈）：换牌经常无法提交，日志一直刷
`recommendation_not_stable`。直接原因是「出牌推荐」和「换牌留牌」拆成两块
单独校准的区域后，校准窗口的 OCR 预览只跟着第 1 项走（第 2 项等于盲拖，
见 `calibrate_roi.OCR_PREVIEW_TARGET_KEYS`），框一偏 OCR 就在几帧之间抖。
本文件锁住运行时的两道兜底：

1. `read()` 不再只认“连续两帧完全相同”：同一结论在本次读取里出现
   `stable_frames` 次、且最后一帧就是它，也算稳（容忍 A/B/A 抖动）；
2. 换牌 reader 用 `stability_key`（解析出的号位集合）判稳：OCR 把同一句留牌
   建议读成两行/漏一行时，只要号位没变就照常换牌。
"""

import unittest
from types import SimpleNamespace

import numpy as np

import FSM_action
from src.ocr.stable_reader import OcrRejectedError, StableRecommendationReader
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_models import OcrEvidence, OcrLine


def make_config(**overrides):
    base = dict(max_attempts=4, stable_frames=2, min_ocr_confidence=0.70,
                retry_interval_seconds=0.0, ocr_small_window_max_upscale=2.0)
    base.update(overrides)
    return SimpleNamespace(**base)


def make_frame(index):
    return SimpleNamespace(
        frame_id=f"frame-{index}", exact_hash=f"hash-{index}",
        client_size=None)


class ScriptedBackend:
    """每调用一次 `recognize` 就吐出脚本里下一条文本。"""

    def __init__(self, texts, confidence=0.95, line_confidence=None):
        self.texts = list(texts)
        self.confidence = confidence
        self.line_confidence = (
            confidence if line_confidence is None else line_confidence)
        self.calls = []

    def recognize(self, _image, frame_id, preprocessing):
        text = self.texts[len(self.calls) % len(self.texts)]
        self.calls.append(text)
        lines = tuple(OcrLine(line, self.line_confidence)
                      for line in text.splitlines() if line.strip())
        return OcrEvidence(
            frame_id=frame_id, created_at=0.0, lines=lines,
            normalized_text=text, confidence=self.confidence,
            backend="scripted", preprocessing=preprocessing)


def read_with(backend, config=None, stability_key=None):
    reader = StableRecommendationReader(
        config or make_config(), backend, sleep=lambda _seconds: None,
        stability_key=stability_key)
    frames = iter(make_frame(index) for index in range(10))
    roi = np.zeros((8, 8, 3), dtype=np.uint8)
    return reader.read(lambda: next(frames), lambda _frame: roi)


class JitterToleranceTests(unittest.TestCase):
    def test_read_is_stable_across_jitter(self):
        # 默认按原文判稳：A/B/A 看“本次读取里 A 出现过 2 次且最后一帧是 A”；
        # 连续相同则第二帧就短路；给了 stability_key 时号位与原文各判各的。
        keys = iter([("mulligan", (1,)), ("mulligan", (2,)),
                     ("mulligan", (1,))])
        cases = (
            ("alternating-text-accepted",
             ["替换1号位卡牌", "替换2号位卡牌", "替换1号位卡牌"],
             lambda: None, "替换1号位卡牌", "frame-2", 3),
            ("consecutive-identical-short-circuit",
             ["替换1号位卡牌", "替换1号位卡牌"],
             lambda: None, "替换1号位卡牌", "frame-1", 2),
            ("changing-slots-last-one-wins",
             ["替换1号位卡牌", "替换2号位卡牌", "替换1号位卡牌"],
             lambda: (lambda _evidence: next(keys)),
             "替换1号位卡牌", "frame-2", 3),
        )
        for name, texts, key_factory, expected_text, expected_frame, calls in cases:
            with self.subTest(name):
                backend = ScriptedBackend(texts)
                evidence = read_with(backend, stability_key=key_factory())
                self.assertEqual(expected_text, evidence.normalized_text)
                self.assertEqual(expected_frame, evidence.frame_id)
                self.assertEqual(calls, len(backend.calls))

    def test_same_key_with_different_texts_is_accepted(self):
        """文本抖动（多/少一行）但结论（号位集合）不变 → 第二帧就接受。"""
        backend = ScriptedBackend(["替换1号位卡牌",
                                   "替换1号位卡牌\n替换2号位卡牌"])
        key = lambda _evidence: ("mulligan", (1, 2))  # noqa: E731

        evidence = read_with(backend, stability_key=key)

        self.assertEqual(2, len(backend.calls))
        self.assertEqual("frame-1", evidence.frame_id)

    def test_four_distinct_readings_still_reject_with_attempt_details(self):
        backend = ScriptedBackend(["甲", "乙", "丙", "丁"])

        with self.assertRaises(OcrRejectedError) as caught:
            read_with(backend)

        self.assertEqual("recommendation_not_stable", str(caught.exception))
        self.assertEqual(4, len(caught.exception.attempts))
        self.assertEqual("甲", caught.exception.attempts[0][0])
        self.assertEqual(0.95, caught.exception.attempts[0][1])

    def test_low_confidence_frames_are_never_accepted_but_are_recorded(self):
        # 整图置信 0.95（过了预处理的门槛），但“动作行”那一行只有 0.50
        # （`_action_evidence` 取相关行的最低置信度）→ 仍然不许用，但要留原文。
        backend = ScriptedBackend(["替换1号位卡牌"], line_confidence=0.50)

        with self.assertRaises(OcrRejectedError) as caught:
            read_with(backend)

        self.assertEqual(4, len(caught.exception.attempts))
        self.assertEqual("替换1号位卡牌", caught.exception.attempts[0][0])
        self.assertEqual(0.50, caught.exception.attempts[0][1])

    def test_empty_text_is_rejected_and_marked_unreadable(self):
        backend = ScriptedBackend([""])

        with self.assertRaises(OcrRejectedError) as caught:
            read_with(backend)

        self.assertTrue(all(item is None for item in caught.exception.attempts))

    def test_key_returning_none_is_treated_as_unreadable(self):
        backend = ScriptedBackend(["替换1号位卡牌"])

        with self.assertRaises(OcrRejectedError):
            read_with(backend, stability_key=lambda _evidence: None)


class MulliganStabilityKeyTests(unittest.TestCase):
    """FSM_action 给换牌 reader 的结论指纹：按解析出的号位集合。"""

    def setUp(self):
        self.original = FSM_action.recommendation_parser
        FSM_action.recommendation_parser = RecommendationParser()

    def tearDown(self):
        FSM_action.recommendation_parser = self.original

    @staticmethod
    def evidence(text):
        lines = tuple(OcrLine(line, 0.99)
                      for line in text.splitlines() if line.strip())
        return OcrEvidence(
            frame_id="frame", created_at=0.0, lines=lines,
            normalized_text=text, confidence=0.99,
            backend="test", preprocessing="test")

    def test_key_is_the_slot_set_not_the_raw_text(self):
        key = FSM_action._mulligan_stability_key(
            self.evidence("打法参考A\n替换2号位卡牌\n替换3号位卡牌"))

        self.assertEqual(("mulligan", (2, 3)), key)

    def test_key_edge_cases_matrix(self):
        cases = (
            ("keep-all-maps-to-the-empty-slot-set",
             "打法参考A\n保留全部卡牌", ("mulligan", ())),
            # 解析不出动作时退回原文，交给上层报 recommendation_is_not_mulligan。
            ("unparsable-text-falls-back-to-raw-text",
             "这不是留牌建议", ("text", "这不是留牌建议")),
        )
        for name, text, expected in cases:
            with self.subTest(name):
                self.assertEqual(
                    expected,
                    FSM_action._mulligan_stability_key(self.evidence(text)))


if __name__ == "__main__":
    unittest.main()
