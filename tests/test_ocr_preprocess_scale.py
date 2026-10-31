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

    def test_candidate_set_is_versioned(self):
        candidates = dict(preprocess.iter_preprocess_recommendation(self.image))
        self.assertEqual(
            {"scaled_color_v1", "gray_clahe_v1", "light_text_binary_v1"},
            set(candidates),
        )

    def test_scale_matrix(self):
        # 额外放大倍数（None = 不额外放大）同时作用于彩色与二进制候选。
        cases = (
            ("default-scale", 1.0),
            ("extra-upscale", 2.0),
            ("none-means-no-extra-upscale", None),
        )
        for name, factor in cases:
            with self.subTest(name):
                candidates = dict(
                    preprocess.preprocess_recommendation(self.image, factor))
                h, w = self._expected(factor or 1.0)
                self.assertEqual((h, w), candidates["scaled_color_v1"].shape[:2])
                self.assertEqual(
                    (h, w), candidates["light_text_binary_v1"].shape[:2])


if __name__ == "__main__":
    unittest.main()
