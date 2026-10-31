# -*- coding: utf-8 -*-
"""帧缓存有界 LRU：长跑进程不能按帧数线性吃内存。

背景：``StableRecommendationReader._cache`` 以前以 ``frame.exact_hash`` 为
key 无限缓存 ``OcrEvidence``，而 reader 是模块级单例（整个进程只建一次），
每个新面板帧 +4.2KB（实测 2 万条 80MB）。本文件锁住有界语义：

1. 缓存条数有上限（默认 ``MAX_CACHE_ENTRIES``），超限淘汰最久未用的；
2. 命中会 ``move_to_end``，最近用过的不会被下一次淘汰；
3. ``clear_cache()`` 能整块释放缓存；
4. 没有 ``exact_hash`` 的帧不进缓存；
5. 命中返回新的证据对象（frame_id 按当前帧重建）但内容一致。
"""

import unittest
from types import SimpleNamespace

import numpy as np

from src.ocr.stable_reader import StableRecommendationReader
from src.recommendation_models import OcrEvidence, OcrLine


def make_config():
    return SimpleNamespace(min_ocr_confidence=0.5,
                           ocr_small_window_max_upscale=2.0)


class RecordingBackend:
    """记录实际推理次数：命中缓存时不能再调到这里。"""

    def __init__(self):
        self.calls = []

    def recognize(self, _image, frame_id, preprocessing):
        self.calls.append(frame_id)
        return OcrEvidence(
            frame_id=frame_id,
            created_at=0.0,
            lines=(OcrLine("结束回合", 0.99),),
            normalized_text="结束回合",
            confidence=0.99,
            backend="recording",
            preprocessing=preprocessing,
        )


def make_frame(frame_id, frame_hash):
    return SimpleNamespace(
        frame_id=frame_id, exact_hash=frame_hash, client_size=None)


class BoundedFrameCacheTests(unittest.TestCase):
    def setUp(self):
        self.backend = RecordingBackend()
        self.roi = np.zeros((8, 8, 3), dtype=np.uint8)

    def _reader(self, max_cache_entries=None):
        kwargs = {}
        if max_cache_entries is not None:
            kwargs["max_cache_entries"] = max_cache_entries
        return StableRecommendationReader(
            make_config(), self.backend, **kwargs)

    def _read(self, reader, frame_id, frame_hash):
        return reader.read_frame(
            make_frame(frame_id, frame_hash), lambda _frame: self.roi)

    def test_lru_matrix(self):
        cases = (
            (
                "oldest-entry-evicted-past-the-limit",
                2,
                [("frame-0", "hash-0"), ("frame-1", "hash-1"),
                 ("frame-2", "hash-2"), ("frame-0", "hash-0")],
                ["hash-2", "hash-0"],
                ["frame-0", "frame-1", "frame-2", "frame-0"],
            ),
            (
                "hit-moves-the-key-to-the-newest-position",
                2,
                [("frame-a", "hash-a"), ("frame-b", "hash-b"),
                 ("frame-a-hit", "hash-a"), ("frame-c", "hash-c")],
                ["hash-a", "hash-c"],
                ["frame-a", "frame-b", "frame-c"],
            ),
        )
        for name, limit, reads, expected_keys, expected_calls in cases:
            with self.subTest(name):
                self.backend.calls.clear()
                reader = self._reader(limit)
                for frame_id, frame_hash in reads:
                    self._read(reader, frame_id, frame_hash)
                self.assertEqual(expected_keys, list(reader._cache))
                self.assertEqual(expected_calls, self.backend.calls)

    def test_clear_cache_drops_everything(self):
        reader = self._reader(2)
        self._read(reader, "frame-a", "hash-a")

        reader.clear_cache()

        self.assertEqual(0, len(reader._cache))
        self._read(reader, "frame-a", "hash-a")
        self.assertEqual(["frame-a", "frame-a"], self.backend.calls)

    def test_frames_without_exact_hash_are_not_cached(self):
        reader = self._reader()
        frame = SimpleNamespace(frame_id="frame-x", client_size=None)

        reader.read_frame(frame, lambda _frame: self.roi)

        self.assertEqual(0, len(reader._cache))

    def test_hit_returns_a_fresh_object_with_the_same_content(self):
        reader = self._reader()
        first = self._read(reader, "frame-1", "hash-1")
        second = self._read(reader, "frame-2", "hash-1")

        self.assertIsNot(first, second)
        self.assertEqual("frame-2", second.frame_id)  # frame_id 跟着当前帧
        self.assertEqual(first.normalized_text, second.normalized_text)
        self.assertEqual(first.confidence, second.confidence)
        self.assertEqual(first.lines, second.lines)
        self.assertEqual(first.backend, second.backend)
        self.assertEqual(first.preprocessing, second.preprocessing)
        # 命中没有触发第二次推理。
        self.assertEqual(["frame-1"], self.backend.calls)


if __name__ == "__main__":
    unittest.main()
