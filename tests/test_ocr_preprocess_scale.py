import unittest

import numpy as np

from src.ocr import preprocess


class PreprocessScaleTests(unittest.TestCase):
    def setUp(self):
        self.image = np.zeros((10, 20, 3), dtype=np.uint8)

    def _expected(self, factor):
        h = int(round(10 * preprocess._SCALE * factor))
        w = int(round(20 * preprocess._SCALE * factor))
        return (h, w)

    def test_default_scale_unchanged(self):
        candidates = dict(preprocess.iter_preprocess_recommendation(self.image))
        self.assertEqual(
            {"scaled_color_v1", "gray_clahe_v1", "light_text_binary_v1"},
            set(candidates),
        )
        h, w = self._expected(1.0)
        self.assertEqual((h, w), candidates["scaled_color_v1"].shape[:2])

    def test_extra_scale_enlarges_output(self):
        candidates = dict(preprocess.iter_preprocess_recommendation(self.image, 2.0))
        h, w = self._expected(2.0)
        self.assertEqual((h, w), candidates["scaled_color_v1"].shape[:2])
        # 二进制候选也跟着同倍放大
        self.assertEqual((h, w), candidates["light_text_binary_v1"].shape[:2])

    def test_none_scale_is_treated_as_no_extra_upscale(self):
        candidates = dict(preprocess.preprocess_recommendation(self.image, None))
        h, w = self._expected(1.0)
        self.assertEqual((h, w), candidates["scaled_color_v1"].shape[:2])


if __name__ == "__main__":
    unittest.main()
