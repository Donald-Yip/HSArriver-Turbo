# -*- coding: utf-8 -*-
"""浮窗「OCR / 执行」合并面板：OCR 记录存储 + OCR 段排版 + 浮窗/网页接线。

用户 2026-10-31 反馈：日志里只有 ``[OCR] 图 572x364 行 2 置信 0.91 (turn_end)``，
看不出到底读到了什么字；后要求「显示完整扫描内容、换牌确认不要一直放在上面」，
再后来要求把「日志正文」和「OCR 扫描」两个框合成一个分块流。这里覆盖四件事：
1. ``src.ocr.status`` 记录每次 OCR 的尺寸/逐行文本/置信度（同区域同内容累计次数）；
2. ``log_overlay.ocr_panel_lines`` 按区域分块渲染（合并面板里作为 OCR 段）：
   标题行 + 每条原文各占一行、完整不截断；过期（> TTL）区域隐藏；变体合并；
3. ``log_overlay.ocr_panel_status`` 给标题行提供「最近 Ns 前」；
4. ``web_ui`` 把快照作为 ``ocr_callback`` 注入浮窗，OCR 适配器负责写入；
   浮窗的合并面板由 action_feed 归块渲染（单 Text、新块置顶、可折叠）。
"""

import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

import action_feed
import log_overlay
import web_ui
from src.ocr import status as ocr_status
from tests.helpers.fake_tk import overlay_harness


def _entry(tag="turn_end", lines=("结束回合",), at=0.0, count=1,
           confidence=0.9, label=None):
    """构造一条 ``src.ocr.status.snapshot()`` 记录（at=0 表示“没有时间戳”）。"""
    pairs = tuple((text, confidence) for text in lines)
    return {
        "tag": tag,
        "text": " | ".join(text for text in lines if text),
        "lines": pairs,
        "count": count,
        "confidence": confidence,
        "line_count": len(pairs),
        "width": 572,
        "height": 364,
        "ms": 100,
        "at": at,
    }


def _texts(lines):
    return [row["text"] for row in lines]


class OcrStatusStoreTests(unittest.TestCase):
    def setUp(self):
        ocr_status.clear()

    def tearDown(self):
        ocr_status.clear()

    def test_records_each_region_and_returns_newest_first(self):
        ocr_status.record(
            "turn_end", (572, 364),
            [("对手回合", 0.91), ("1:15", 0.80)], elapsed_ms=120, at=1.0)
        ocr_status.record("start", (600, 200), [("开始", 0.99)],
                          elapsed_ms=50, at=2.0)

        items = ocr_status.snapshot()

        self.assertEqual(["start", "turn_end"], [item["tag"] for item in items])
        turn_end = items[1]
        self.assertEqual(2, turn_end["line_count"])
        self.assertAlmostEqual(0.80, turn_end["confidence"])
        self.assertEqual("对手回合 | 1:15", turn_end["text"])
        self.assertEqual((572, 364), (turn_end["width"], turn_end["height"]))
        self.assertEqual(1, turn_end["count"])

    def test_repeat_counter_and_empty_reads_are_recorded(self):
        cases = (
            ("identical-reads-count-up", [("对手回合", 0.9)],
             [("对手回合", 0.9)]),
            ("changed-text-resets-the-counter", [("对手回合", 0.9)],
             [("结束回合", 0.9)]),
            ("empty-read-is-recorded-without-text", [], None),
        )
        for name, first, second in cases:
            with self.subTest(name):
                ocr_status.clear()
                if second is None:
                    entry = ocr_status.record("turn_end", (10, 10), first)
                    self.assertEqual(0, entry["line_count"])
                    self.assertEqual("", entry["text"])
                    self.assertEqual(0.0, entry["confidence"])
                    continue
                ocr_status.record("turn_end", (572, 364), first, at=1.0)
                ocr_status.record("turn_end", (572, 364), second, at=2.0)
                expected = 2 if first == second else 1
                self.assertEqual(expected, ocr_status.snapshot()[0]["count"])

    def test_only_the_newest_regions_are_kept(self):
        for index in range(ocr_status.MAX_TAGS + 2):
            ocr_status.record(f"tag{index}", (10, 10), [("x", 0.5)])

        tags = [item["tag"] for item in ocr_status.snapshot()]

        self.assertEqual(ocr_status.MAX_TAGS, len(tags))
        self.assertNotIn("tag0", tags)
        self.assertIn(f"tag{ocr_status.MAX_TAGS + 1}", tags)

    def test_long_line_is_clipped_to_the_safety_limit(self):
        ocr_status.record("turn_end", (10, 10), [("结束" * 300, 0.9)])

        text = ocr_status.snapshot()[0]["text"]

        self.assertLessEqual(len(text), ocr_status.MAX_TEXT)
        self.assertTrue(text.endswith("…"))

    def test_clear_forgets_everything(self):
        ocr_status.record("turn_end", (10, 10), [("结束回合", 0.9)])
        ocr_status.clear()

        self.assertEqual([], ocr_status.snapshot())


class OcrPanelLinesTests(unittest.TestCase):
    def test_snapshots_without_fresh_text_show_the_hint(self):
        now = 1000.0
        cases = (
            ("missing-snapshot", None, None),
            ("broken-entries", ["bad", 42, None], None),
            ("every-entry-stale", [_entry(tag="turn_end", at=now - 60.0)], None),
            ("broken-phase-payload", None, "bad payload"),
        )
        for name, entries, phase in cases:
            with self.subTest(name):
                lines = log_overlay.ocr_panel_lines(
                    entries, now=now, phase=phase)
                self.assertEqual(1, len(lines))
                self.assertIn("没有 OCR 扫描", lines[0]["text"])
                self.assertEqual("dim", lines[0]["tag"])

    def test_region_blocks_render_a_header_and_every_line(self):
        now = 1000.0
        cases = (
            ("header-and-lines",
             [_entry(tag="turn_end", lines=("01:07", "结束回合"), at=now)],
             ["● 结束回合框 2行 0.90", "  ▏01:07", "  ▏结束回合"],
             ["match", "text", "text"]),
            ("fresh-empty-read",
             [_entry(tag="confirm", lines=(), at=now)],
             ["● 换牌确认 0行 0.90",
              log_overlay.OCR_PANEL_INDENT + log_overlay.OCR_PANEL_NO_TEXT],
             ["dim", "dim"]),
            ("repeat-counter-in-header",
             [_entry(count=5, at=now)],
             ["● 结束回合框 1行 0.90 ×5", "  ▏结束回合"],
             ["match", "text"]),
            ("unknown-tag-falls-back",
             [_entry(tag="custom_region", at=now)],
             ["● custom_r 1行 0.90", "  ▏结束回合"],
             ["text", "text"]),
        )
        for name, entries, expected_texts, expected_tags in cases:
            with self.subTest(name):
                lines = log_overlay.ocr_panel_lines(entries, now=now)
                self.assertEqual(expected_texts, _texts(lines))
                self.assertEqual(expected_tags,
                                 [line["tag"] for line in lines])
                self.assertTrue(lines[0]["head"])
                self.assertFalse(any(line["head"] for line in lines[1:]))

    def test_full_text_is_not_truncated(self):
        long_line = "灵魂之匣放置于我方1号位并抽一张牌" * 2

        lines = log_overlay.ocr_panel_lines([_entry(lines=(long_line,))])

        self.assertEqual(log_overlay.OCR_PANEL_INDENT + long_line, lines[1]["text"])
        self.assertNotIn("…", lines[1]["text"])

    def test_region_older_than_ttl_is_hidden(self):
        now = 1000.0
        lines = log_overlay.ocr_panel_lines(
            [
                _entry(tag="turn_end", at=now - 1.0),
                _entry(tag="confirm", lines=(), at=now - 60.0),
            ],
            now=now)

        self.assertEqual(["● 结束回合框 1行 0.90", "  ▏结束回合"], _texts(lines))

    def test_recommendation_variants_are_merged_into_one_block(self):
        now = 1000.0
        lines = log_overlay.ocr_panel_lines(
            [
                # 三个预处理变体：只有 gray_clahe_v1 读到了字。
                _entry(tag="scaled_color_v1", lines=(), at=now - 1.0),
                _entry(tag="gray_clahe_v1", lines=("打出2号位法术",), at=now - 2.0),
                _entry(tag="light_text_binary_v1", lines=(), at=now - 3.0),
            ],
            now=now)

        headers = [line["text"] for line in lines if line["head"]]
        self.assertEqual(["● 推荐 1行 0.90"], headers)
        self.assertIn(log_overlay.OCR_PANEL_INDENT + "打出2号位法术", _texts(lines))

    def test_end_turn_box_is_always_first_and_order_is_stable(self):
        now = 1000.0
        lines = log_overlay.ocr_panel_lines(
            [
                _entry(tag="winrate", lines=("AI胜率 95%",), at=now - 0.1),
                _entry(tag="turn_end", lines=("结束回合",), at=now - 5.0),
            ],
            now=now)

        self.assertTrue(lines[0]["text"].startswith("● 结束回合框"))
        self.assertTrue(any(text.startswith("● 胜率") for text in _texts(lines)))

    def test_end_turn_mismatch_is_warn_and_other_regions_are_text(self):
        now = 1000.0
        mismatch = log_overlay.ocr_panel_lines(
            [_entry(tag="turn_end", lines=("对手回合",), at=now - 1.0)], now=now)
        winrate = log_overlay.ocr_panel_lines(
            [_entry(tag="winrate", lines=("AI胜率 95%",), at=now - 1.0)], now=now)

        self.assertEqual("warn", mismatch[0]["tag"])
        self.assertEqual("text", winrate[0]["tag"])

    def test_render_limit_appends_an_overflow_hint(self):
        lines = log_overlay.ocr_panel_lines(
            [_entry(lines=tuple(f"第{index}行" for index in range(10)))],
            limit=5)

        self.assertEqual(5, len(lines))
        self.assertIn("还有", lines[-1]["text"])
        self.assertEqual("dim", lines[-1]["tag"])


class OcrPanelStatusTests(unittest.TestCase):
    def test_newest_scan_age_is_reported(self):
        now = 1000.0
        cases = (
            ("seconds", [_entry(at=now - 3.0),
                         _entry(tag="winrate", at=now - 8.0)], "最近 3s 前"),
            ("just-now", [_entry(at=now - 0.4)], "刚刚"),
        )
        for name, entries, expected in cases:
            with self.subTest(name):
                self.assertEqual(
                    expected, log_overlay.ocr_panel_status(entries, now=now))

    def test_no_fresh_scan_is_reported(self):
        now = 1000.0

        status = log_overlay.ocr_panel_status(
            [_entry(at=now - 60.0)], now=now)

        self.assertEqual("没有扫描", status)

    def test_missing_entries_are_reported_as_no_scan(self):
        self.assertEqual("没有扫描", log_overlay.ocr_panel_status(None))


class OcrPanelEmptyStateTests(unittest.TestCase):
    """没有新鲜扫描时，面板要说明“为什么没在扫”（R18H）。"""

    def test_phase_label_explains_why_nothing_is_fresh(self):
        cases = (
            ("fresh", {"label": "等待对手操作", "log_idle_s": 3.0}, "dim"),
            ("stale-log", {"label": "等待对手操作", "log_idle_s": 95.0}, "warn"),
            ("short-idle", {"label": "等待对手操作", "log_idle_s": 5.0}, "dim"),
        )
        for name, phase, expected_tag in cases:
            with self.subTest(name):
                lines = log_overlay.ocr_panel_lines(None, phase=phase)
                text = lines[0]["text"]
                self.assertEqual(expected_tag, lines[0]["tag"])
                self.assertIn("当前：等待对手操作", text)
                if expected_tag == "warn":
                    self.assertIn("日志 95s 未更新", text)
                    self.assertIn("可能卡住", text)
                else:
                    self.assertNotIn("未更新", text)

    def test_missing_phase_keeps_the_old_wording(self):
        lines = log_overlay.ocr_panel_lines(None, phase=None)

        self.assertEqual(
            log_overlay.OCR_PANEL_EMPTY_TEMPLATE.format(ttl=12.0),
            lines[0]["text"])
        self.assertEqual("dim", lines[0]["tag"])


class MergedFeedPanelCase(unittest.TestCase):
    """合并面板（action_feed 分块流）的公共夹具：每次用例前后清空块流。"""

    def setUp(self):
        action_feed.clear()
        action_feed.set_ocr_provider(None)

    def tearDown(self):
        action_feed.clear()
        action_feed.set_ocr_provider(None)

    @staticmethod
    def _feed_text(ui):
        """合并面板只有一个 Text（wrap="char"），日志正文不再单独存在。"""
        texts = ui.widgets_of("text")
        assert len(texts) == 1, f"expected one merged Text, got {len(texts)}"
        return texts[0]

    @staticmethod
    def _refresh(ui):
        callback = [cb for _delay, cb in ui.root.after_calls if cb][-1]
        callback()

    @staticmethod
    def _inserted(widget):
        return "".join(op[2] for op in widget.operations if op[0] == "insert")

    @staticmethod
    def _delete_count(widget):
        return sum(1 for op in widget.operations if op[0] == "delete")


class OverlayMergedFeedPanelTests(MergedFeedPanelCase):
    def test_a_block_renders_actions_then_ocr_summary(self):
        # 用「推荐」区域：结束回合/段位/开始按钮现在只在上方识别轮播窗口出现。
        entries = [_entry(tag="scaled_color_v1", lines=("01:07", "打出一号位随从"))]

        with overlay_harness(_OCR=lambda: entries) as ui:
            action_feed.push("[推荐] 打出1号位随从")
            action_feed.push("[执行] 打出1号位随从「鱼人招潮者」")
            self._refresh(ui)
            inserted = self._inserted(self._feed_text(ui))

        # 卡片式（R19）：每行显式标出来源 ——[OCR]读到什么、[执行]做了什么；
        # OCR 原文折进可点的「原文 (N)」；不再有 8 字缩进和 46 字分隔线。
        self.assertIn("[OCR]打出1号位随从", inserted)
        self.assertIn("[执行]打出1号位随从「鱼人招潮者」", inserted)
        self.assertIn("原文 (2)", inserted)
        self.assertIn("┈", inserted)  # 块与块之间的浅色虚线
        self.assertNotIn("[推荐]", inserted)
        self.assertNotIn("▏01:07", inserted)  # 原文默认不出现在卡片里
        self.assertLess(inserted.index("[OCR]"), inserted.index("[执行]"))
        self.assertLess(inserted.index("[执行]"), inserted.index("原文 (2)"))

    def test_newest_block_is_rendered_on_top(self):
        with overlay_harness(_OCR=lambda: []) as ui:
            action_feed.push("[推荐] 第一步")
            action_feed.push("[执行] 点击第一次")
            action_feed.push("[推荐] 第二步")
            action_feed.push("[执行] 点击第二次")
            self._refresh(ui)
            inserted = self._inserted(self._feed_text(ui))

        self.assertLess(inserted.index("第二步"), inserted.index("第一步"))
        self.assertNotIn(action_feed.SEPARATOR, inserted)

    def test_clicking_the_expander_reveals_and_hides_the_raw_ocr_lines(self):
        entries = [_entry(tag="scaled_color_v1", lines=("01:07", "打出一号位随从"))]

        with overlay_harness(_OCR=lambda: entries) as ui:
            action_feed.push("[执行] 打出1号位随从")
            self._refresh(ui)
            widget = self._feed_text(ui)
            exp_tags = [tag for (tag, _seq) in widget.tag_bindings]
            self.assertTrue(exp_tags, "「原文 (N)」没有绑定点击")

            widget.fire_tag(exp_tags[0])
            self._refresh(ui)
            self.assertIn("01:07", self._inserted(widget))
            self.assertIn("收起原文", self._inserted(widget))

            widget.fire_tag(exp_tags[0])
            self._refresh(ui)
            self.assertIn("原文 (2)", self._inserted(widget))

    def test_expander_tags_are_not_rebound_on_every_rebuild(self):
        entries = [_entry(lines=("01:07",))]

        with overlay_harness(_OCR=lambda: entries) as ui:
            action_feed.push("[执行] 打出1号位随从")
            self._refresh(ui)
            widget = self._feed_text(ui)
            first = dict(widget.tag_bindings)
            self._refresh(ui)
            self._refresh(ui)
            self.assertEqual(list(first), list(widget.tag_bindings))

    def test_block_header_carries_time_and_phase(self):
        with overlay_harness(_OCR=lambda: []) as ui:
            action_feed.push("-----我方出牌阶段-----")
            action_feed.push("[推荐] 打出1号位随从")
            self._refresh(ui)
            inserted = self._inserted(self._feed_text(ui))

        # 阶段居中金标 + 卡头只留时间（不再「· 我方出牌」重复一遍）。
        self.assertIn("我方出牌", inserted)
        self.assertNotIn("· 我方出牌", inserted)

    def test_identical_data_does_not_rebuild_the_panel(self):
        with overlay_harness(_OCR=lambda: [_entry()]) as ui:
            action_feed.push("[推荐] 打出1号位随从")
            self._refresh(ui)
            widget = self._feed_text(ui)
            before = self._delete_count(widget)
            self._refresh(ui)

        self.assertEqual(before, self._delete_count(widget))

    def test_rebuild_keeps_the_scroll_position_from_the_top(self):
        with overlay_harness(_OCR=lambda: [_entry()]) as ui:
            action_feed.push("[推荐] 打出1号位随从")
            self._refresh(ui)
            widget = self._feed_text(ui)

        self.assertIn(("yview_moveto", 0.0), widget.operations)

    def test_broken_ocr_callback_is_swallowed(self):
        def boom():
            raise RuntimeError("boom")

        with overlay_harness(_OCR=boom) as ui:
            action_feed.push("[推荐] 打出1号位随从")
            self._refresh(ui)
            inserted = self._inserted(self._feed_text(ui))

        self.assertIn("打出1号位随从", inserted)
        self.assertIn("没有 OCR 扫描", inserted)

    def test_phase_callback_reaches_the_ocr_empty_state(self):
        with overlay_harness(
            _OCR=lambda: [],
            _PHASE=lambda: {"label": "等待对手操作", "log_idle_s": 3.0},
        ) as ui:
            action_feed.push("[推荐] 打出1号位随从")
            self._refresh(ui)
            inserted = self._inserted(self._feed_text(ui))

        self.assertIn("当前：等待对手操作", inserted)

    def test_hide_toggle_collapses_and_restores_the_merged_panel(self):
        with overlay_harness(_OCR=lambda: [_entry()]) as ui:
            widget = self._feed_text(ui)
            body = widget.parent
            toggle = ui.one("label", text=log_overlay.FEED_HIDE_TEXT)
            self._refresh(ui)

            self.assertTrue(body.packed)
            toggle.fire("<Button-1>")
            self.assertFalse(body.packed)
            self.assertEqual(log_overlay.FEED_SHOW_TEXT,
                             toggle.options["text"])

            toggle.fire("<Button-1>")
            self.assertTrue(body.packed)
            before = self._delete_count(widget)
            action_feed.push("[推荐] 打出1号位随从")
            self._refresh(ui)
            self.assertGreater(self._delete_count(widget), before)


class WebOcrWiringTests(unittest.TestCase):
    def setUp(self):
        ocr_status.clear()

    def tearDown(self):
        ocr_status.clear()

    def test_bind_overlay_passes_the_ocr_callback(self):
        bound = {}
        overlay = SimpleNamespace(
            start=lambda **kwargs: bound.update(kwargs),
            is_running=lambda: False)
        with (
            mock.patch.object(web_ui, "log_overlay", overlay),
            mock.patch.object(web_ui, "_verify_overlay_opened_async"),
        ):
            web_ui._bind_overlay()

        self.assertIn("ocr_callback", bound)
        self.assertIs(bound["ocr_callback"], web_ui._overlay_ocr)

    def test_ocr_callback_returns_the_snapshot(self):
        ocr_status.record("turn_end", (10, 10), [("结束回合", 0.9)])

        snapshot = web_ui._overlay_ocr()

        self.assertEqual("turn_end", snapshot[0]["tag"])
        self.assertEqual("结束回合", snapshot[0]["text"])

    def test_ocr_callback_swallows_errors(self):
        with mock.patch("src.ocr.status.snapshot",
                        side_effect=RuntimeError("boom")):
            self.assertIsNone(web_ui._overlay_ocr())


class PaddleAdapterRecordingTests(unittest.TestCase):
    """OCR 适配器是唯一写入方：识别一次就要在面板数据里留一条。"""

    def setUp(self):
        ocr_status.clear()

    def tearDown(self):
        ocr_status.clear()

    def test_recognize_records_the_scan_for_the_panel(self):
        from src.ocr.paddle_adapter import PaddleOcrAdapter

        class Engine:
            def ocr(self, _image, cls=False):
                return [[[[[0, 0], [10, 0], [10, 10], [0, 10]],
                          ("结束回合", 0.99)]]]

        adapter = PaddleOcrAdapter(engine=Engine(), clock=lambda: 1.0)
        image = np.zeros((364, 572, 3), dtype=np.uint8)

        adapter.recognize(image, "frame-1", "turn_end")

        entry = ocr_status.snapshot()[0]
        self.assertEqual("turn_end", entry["tag"])
        self.assertEqual("结束回合", entry["text"])
        self.assertEqual((572, 364), (entry["width"], entry["height"]))


if __name__ == "__main__":
    unittest.main()
