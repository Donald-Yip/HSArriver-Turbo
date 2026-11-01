# -*- coding: utf-8 -*-
"""浮窗两窗合并日志 merged_log：识别轮播事件 + 日志流按时间合并（导出用）。

用户 2026-11-01：识别结果（结束回合/段位/开始按钮）只在浮窗上方轮播窗口显示，
不进日志流；导出时两个窗口的内容必须按时间合在一起。
"""

import time
import unittest

import action_feed
import merged_log


class MergedLogCase(unittest.TestCase):
    def setUp(self):
        action_feed.clear()
        action_feed.set_ocr_provider(None)
        merged_log.clear()

    def tearDown(self):
        action_feed.clear()
        action_feed.set_ocr_provider(None)
        merged_log.clear()


class DetectEventTests(MergedLogCase):
    def test_identical_rows_are_recorded_once(self):
        merged_log.record_detect_rows(["结束回合 · 命中 · 刚刚"])
        merged_log.record_detect_rows(["结束回合 · 命中 · 刚刚"])
        merged_log.record_detect_rows(["段位 · 传说 4 · 刚刚"])

        self.assertEqual(
            ["结束回合 · 命中 · 刚刚", "段位 · 传说 4 · 刚刚"],
            [text for _at, text in merged_log.detect_events()])

    def test_events_carry_their_own_timestamp(self):
        merged_log.record_detect_rows(["开始按钮 · 已出现"], at=1000.0)

        at, text = merged_log.detect_events()[0]

        self.assertAlmostEqual(1000.0, at)
        self.assertEqual("开始按钮 · 已出现", text)


class TimelineTests(MergedLogCase):
    def test_detect_and_feed_rows_are_merged_in_time_order(self):
        now = time.time()
        action_feed.push("[推荐] 打出1号位随从")
        action_feed.push("[执行] 打出1号位随从")
        merged_log.record_detect_rows(["结束回合 · 命中 · 刚刚"], at=now - 1)

        text = merged_log.timeline_text()

        self.assertIn("【识别】结束回合 · 命中", text)
        self.assertIn("[OCR]打出1号位随从", text)
        self.assertIn("[执行]打出1号位随从", text)
        self.assertLess(text.index("【识别】"), text.index("[OCR]"))

    def test_export_rows_skip_ui_only_rows(self):
        action_feed.push("[推荐] 打出1号位随从")
        action_feed.push("[执行] 打出1号位随从")

        texts = [text for _at, text in action_feed.export_rows()]

        self.assertEqual(["[OCR]打出1号位随从", "[执行]打出1号位随从"], texts)

    def test_export_rows_are_oldest_first(self):
        action_feed.push("[执行] 先发生的")
        time.sleep(0.01)
        action_feed.push("[执行] 后发生的")

        texts = [text for _at, text in action_feed.export_rows()]

        self.assertEqual(["[执行]先发生的", "[执行]后发生的"], texts)


if __name__ == "__main__":
    unittest.main()
