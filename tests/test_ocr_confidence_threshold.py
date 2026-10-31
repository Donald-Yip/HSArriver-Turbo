import unittest
from types import SimpleNamespace

import numpy as np

from src.ocr import preprocess
from src.ocr.stable_reader import OcrRejectedError, StableRecommendationReader
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_config import RecommendationConfig
from src.recommendation_models import OcrEvidence, OcrLine


class FixedConfidenceBackend:
    def recognize(self, _image, frame_id, preprocessing):
        lines = (
            OcrLine("打法参考A", 0.70),
            OcrLine("保留全部卡牌", 0.70),
        )
        return OcrEvidence(
            frame_id=frame_id,
            created_at=0.0,
            lines=lines,
            normalized_text="打法参考A\n保留全部卡牌",
            confidence=0.70,
            backend="fixed-confidence",
            preprocessing=preprocessing,
        )


class OcrConfidenceThresholdTests(unittest.TestCase):
    def test_two_matching_recommendations_at_point_seven_are_accepted(self):
        frame_ids = iter(("frame-1", "frame-2", "frame-3"))
        reader = StableRecommendationReader(
            RecommendationConfig(),
            FixedConfidenceBackend(),
            sleep=lambda _seconds: None,
            text_normalizer=RecommendationParser.normalize_action_text,
            required_headers=("打法参考A", "打法参考Ａ"),
        )

        evidence = reader.read(
            lambda: SimpleNamespace(frame_id=next(frame_ids)),
            lambda _frame: np.zeros((8, 8, 3), dtype=np.uint8),
        )

        self.assertEqual("保留全部卡牌", evidence.normalized_text)
        self.assertEqual(0.70, evidence.confidence)


class ShapeRecordingBackend:
    def __init__(self):
        self.shapes = []

    def recognize(self, image, frame_id, preprocessing):
        self.shapes.append(tuple(image.shape))
        return OcrEvidence(
            frame_id=frame_id,
            created_at=0.0,
            lines=(),
            normalized_text="",
            confidence=0.0,
            backend="shape",
            preprocessing=preprocessing,
        )


class ClientUpscaleTests(unittest.TestCase):
    """客户区比基准小时，OCR 输入要按比例放大补水（见 _client_upscale）。"""

    def _first_shape(self, client_size):
        backend = ShapeRecordingBackend()
        reader = StableRecommendationReader(
            RecommendationConfig(), backend, sleep=lambda _seconds: None
        )
        roi = np.zeros((10, 20, 3), dtype=np.uint8)
        frame = SimpleNamespace(frame_id="f", client_size=client_size)
        with self.assertRaises(OcrRejectedError):
            reader.read_frame(frame, lambda _frame: roi)
        return backend.shapes[0][:2]

    def test_client_scale_matrix(self):
        cases = (
            ("missing-client-size", None, 1.0),
            ("reference-client", (1920, 1080), 1.0),
            ("half-size-client", (960, 540), 2.0),
        )
        for name, client_size, factor in cases:
            with self.subTest(name):
                expected = (
                    int(round(10 * preprocess._SCALE * factor)),
                    int(round(20 * preprocess._SCALE * factor)),
                )
                self.assertEqual(expected, self._first_shape(client_size))

    def test_upscale_is_capped_by_config(self):
        config = RecommendationConfig()
        backend = ShapeRecordingBackend()
        reader = StableRecommendationReader(
            config, backend, sleep=lambda _seconds: None
        )
        roi = np.zeros((10, 20, 3), dtype=np.uint8)
        frame = SimpleNamespace(frame_id="f", client_size=(480, 270))
        with self.assertRaises(OcrRejectedError):
            reader.read_frame(frame, lambda _frame: roi)
        capped = config.ocr_small_window_max_upscale
        self.assertEqual(
            int(round(10 * preprocess._SCALE * capped)), backend.shapes[0][0]
        )


if __name__ == "__main__":
    unittest.main()
