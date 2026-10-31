# -*- coding: utf-8 -*-
"""浮窗分块流 action_feed：归块规则 + render 前缀/分隔线 + 上限。

用户 2026-10-31 要求把浮窗的「日志正文」和「OCR 扫描」合并成一个分块流：
每块 = 一步动作，块内顺序【OCR 内容 → 执行内容】，新块置顶，块间分隔线清晰。
归块/渲染全在 action_feed.py（纯 stdlib），这里全部脱离 Tk 单测。
"""

import re
import unittest

import action_feed

_TIME_HEAD_RE = re.compile(r"^\d{2}:\d{2}:\d{2}( · .+)?$")


def _snapshot_row(text, tag="text", head=False):
    """构造 ocr_panel_lines() 的一行（OCR 快照提供器返回的形状）。"""
    return {"text": text, "tag": tag, "head": head}


def _texts(rows):
    return [text for text, _tag in rows]


class ActionFeedCase(unittest.TestCase):
    def setUp(self):
        action_feed.clear()
        action_feed.set_ocr_provider(None)

    def tearDown(self):
        action_feed.clear()
        action_feed.set_ocr_provider(None)


class RecommendationBlockTests(ActionFeedCase):
    def test_recommendation_and_execution_share_one_block(self):
        action_feed.push("[推荐] 打出2号位法术")
        action_feed.push("[执行] 打出2号位法术「灵魂之匣」→ 我方1号位")

        blocks = action_feed.snapshot()

        self.assertEqual(1, len(blocks))
        self.assertEqual([("[推荐] 打出2号位法术", "text")], blocks[0]["ocr"])
        self.assertEqual(
            [("[执行] 打出2号位法术「灵魂之匣」→ 我方1号位", "exec")],
            blocks[0]["exec"])

    def test_second_action_creates_a_new_block_on_top(self):
        action_feed.push("[推荐] 打出1号位随从")
        action_feed.push("[执行] 打出1号位随从")
        action_feed.push("[推荐] 使用英雄技能")
        action_feed.push("[执行] 使用英雄技能「生命分流」")

        blocks = action_feed.snapshot()

        self.assertEqual(2, len(blocks))
        self.assertEqual("[推荐] 使用英雄技能", blocks[0]["ocr"][0][0])
        self.assertIn("生命分流", blocks[0]["exec"][0][0])
        self.assertEqual("[推荐] 打出1号位随从", blocks[1]["ocr"][0][0])
        self.assertGreaterEqual(blocks[0]["at"], blocks[1]["at"])

    def test_continuations_stay_in_the_ocr_segment(self):
        cases = (
            ("裸行续行", ["[推荐] 使用英雄技能", "生命分流"], 2),
            ("带标签续行", ["[推荐] 打出2号位法术",
                            "[推荐] 原文：PlayCard 灵魂之匣",
                            "[推荐] 目标是已方1号位",
                            "[推荐] 放置于我方1号位"], 4),
            ("重复推荐同块", ["[推荐] 打出1号位随从",
                              "[推荐] 打出1号位随从"], 2),
        )
        for name, lines, expected_rows in cases:
            with self.subTest(case=name):
                action_feed.clear()
                for line in lines:
                    action_feed.push(line)
                blocks = action_feed.snapshot()
                self.assertEqual(1, len(blocks))
                self.assertEqual(expected_rows, len(blocks[0]["ocr"]))
                self.assertEqual([], blocks[0]["exec"])

    def test_execution_without_a_recommendation_opens_an_exec_only_block(self):
        action_feed.push("[执行] 结束回合（点右侧按钮）")

        block = action_feed.snapshot()[0]

        self.assertEqual([], block["ocr"])
        self.assertEqual([("[执行] 结束回合（点右侧按钮）", "exec")], block["exec"])


class EndTurnObservationTests(ActionFeedCase):
    def test_wait_line_is_an_ocr_row_kept_only_once(self):
        line = "[SYS] 尚未检测到「结束回合」（OCR 读到：对手回合），继续等待。"
        for _ in range(3):
            action_feed.push(line)

        block = action_feed.snapshot()[0]

        self.assertEqual(1, len(block["ocr"]))
        self.assertEqual([], block["exec"])

    def test_wait_line_after_execution_starts_a_new_block(self):
        action_feed.push("[推荐] 结束回合")
        action_feed.push("[执行] 结束回合（点右侧按钮）")
        action_feed.push(
            "[SYS] 尚未检测到「结束回合」（OCR 读到：对手回合），继续等待。")

        blocks = action_feed.snapshot()

        self.assertEqual(2, len(blocks))
        self.assertEqual([], blocks[0]["exec"])
        self.assertEqual(1, len(blocks[0]["ocr"]))
        self.assertEqual(1, len(blocks[1]["exec"]))


class ExecutionSegmentTests(ActionFeedCase):
    def test_delay_draw_and_retry_lines_are_execution_rows(self):
        lines = (
            "操作结束，延时 1.2s 后继续。",
            "[SYS] 活人感 延时 2.4s 后（手牌区随机悬停等待）",
            "[SYS] 延时结束",
            "抽到 3 张牌。",
            "[SYS] 已检测到「结束回合」，回合结束。",
            "[SYS] 动作 3s 未确认，重新执行。",
        )
        for line in lines:
            action_feed.push(line)

        block = action_feed.snapshot()[0]

        self.assertEqual(len(lines), len(block["exec"]))
        for _text, tag in block["exec"]:
            self.assertEqual("exec", tag)

    def test_alerts_and_unknown_lines_get_the_right_tag(self):
        action_feed.push("[WARN] 盒子面板暂不可读，继续重试。")
        action_feed.push("[ERROR] 自动化线程异常退出")
        action_feed.push("⚠️ 炉石已退出：Hearthstone.exe 进程消失")
        action_feed.push("[SYS] 普通信息", "WARN")  # level 也是告警来源
        action_feed.push("等待对手操作。")

        tags = [tag for _text, tag in action_feed.snapshot()[0]["exec"]]

        self.assertEqual(["alert"] * 4 + ["dim"], tags)


class PhaseMarkerTests(ActionFeedCase):
    def test_phase_marker_labels_the_next_block(self):
        cases = {
            "-----我方出牌阶段-----": "我方出牌",
            "-----对手回合阶段-----": "对手回合",
            "-----换牌阶段-----": "换牌",
            "-----对局结束阶段-----": "对局结束",
        }
        for marker, expected in cases.items():
            with self.subTest(marker=marker):
                action_feed.clear()
                action_feed.push(marker)
                action_feed.push("[推荐] 测试")
                self.assertEqual(expected, action_feed.snapshot()[0]["phase"])

    def test_phase_marker_closes_the_current_block(self):
        action_feed.push("-----我方出牌阶段-----")
        action_feed.push("[推荐] 打出1号位随从")
        action_feed.push("[执行] 打出1号位随从")
        action_feed.push("-----对手回合阶段-----")
        action_feed.push("[推荐] 结束回合")

        blocks = action_feed.snapshot()

        self.assertEqual(2, len(blocks))
        self.assertEqual("对手回合", blocks[0]["phase"])
        self.assertEqual("我方出牌", blocks[1]["phase"])
        self.assertEqual(1, len(blocks[1]["ocr"]))  # 旧块没被继续追加


class IgnoredLineTests(ActionFeedCase):
    def test_noise_lines_do_not_create_blocks(self):
        for line in (
            "[OCR] 图 273x347 行 0 置信 0.00 (turn_end)",
            "[SYS] 内存 RSS 612 MB（较基线 +72 MB / 峰值 640 MB）",
            "[SYS] 自动投降检测：AI胜率 15.4%",
            "[SYS] 环境自检：全部通过",
            "[SYS] 本局算法：本地策略",
            "[SYS] cards.json 已加载",
            "[SYS] 进入对战",
            "[SYS] 热键 Ctrl+Q 已注册",
            "[SYS] 自动化启动",
            "[SYS] 自动化结束",
            "[SYS] 已打开最新一份对战日志：C:\\logs",
        ):
            action_feed.push(line)

        self.assertEqual([], action_feed.snapshot())


class OcrSnapshotTests(ActionFeedCase):
    def test_a_new_block_captures_the_snapshot_once_and_swallows_errors(self):
        calls = []

        def provider():
            calls.append(1)
            return [
                _snapshot_row("● 结束回合框 1行 0.99", "match", head=True),
                _snapshot_row("  ▏结束回合", "text"),
            ]

        action_feed.set_ocr_provider(provider)
        action_feed.push("[推荐] 打出1号位随从")
        action_feed.push("[推荐] 原文：PlayCard 灵魂之匣")  # 同一块，不再取快照

        block = action_feed.snapshot()[0]

        self.assertEqual(4, len(block["ocr"]))  # 快照 2 行 + 推荐 / 续行各 1 行
        self.assertEqual("● 结束回合框 1行 0.99", block["ocr"][0][0])
        self.assertEqual("head", block["ocr"][0][1])
        self.assertEqual("text", block["ocr"][1][1])
        self.assertEqual(1, len(calls))

        def boom():
            raise RuntimeError("boom")

        action_feed.clear()
        action_feed.set_ocr_provider(boom)
        action_feed.push("[执行] 打出1号位随从")

        self.assertEqual(1, len(action_feed.snapshot()))
        self.assertEqual(
            [("[执行] 打出1号位随从", "exec")],
            action_feed.snapshot()[0]["exec"])


class RenderTests(ActionFeedCase):
    def test_block_header_has_a_clock_and_a_phase(self):
        action_feed.push("-----我方出牌阶段-----")
        action_feed.push("[推荐] 打出1号位随从")

        head = action_feed.render()[0]

        self.assertEqual("head", head[1])
        self.assertRegex(head[0], _TIME_HEAD_RE)
        self.assertIn("· 我方出牌", head[0])

    def test_segments_use_prefixes_and_blocks_are_separated(self):
        action_feed.push("-----我方出牌阶段-----")
        action_feed.push("[推荐] 打出1号位随从")
        action_feed.push("[推荐] 原文：PlayCard")
        action_feed.push("[执行] 打出1号位随从")
        action_feed.push("操作结束，延时 0.5s 后继续。")

        texts = _texts(action_feed.render())

        self.assertTrue(texts[1].startswith("  OCR   "))
        self.assertTrue(texts[2].startswith(" " * 8))
        self.assertTrue(texts[3].startswith("  执行  "))
        self.assertTrue(texts[4].startswith(" " * 8))

        action_feed.clear()
        action_feed.push("[推荐] 第一步")
        action_feed.push("[执行] 第一步")
        action_feed.push("[推荐] 第二步")

        rows = action_feed.render()
        texts = _texts(rows)

        self.assertEqual(action_feed.SEPARATOR, "─" * 46)
        self.assertIn(action_feed.SEPARATOR, texts)
        separator_index = texts.index(action_feed.SEPARATOR)
        self.assertLess(texts.index("  OCR   [推荐] 第二步"), separator_index)
        self.assertGreater(
            texts.index("  执行  [执行] 第一步"), separator_index)
        self.assertEqual("dim", rows[separator_index][1])


class LimitTests(ActionFeedCase):
    def test_ocr_and_exec_segments_are_capped_with_an_overflow_hint(self):
        rows = [_snapshot_row(f"OCR 行 {index}") for index in range(20)]
        action_feed.set_ocr_provider(lambda: rows)

        # 只执行、不推荐：块建立时取 20 行快照，保留 12 行、藏掉 8 行。
        action_feed.push("[执行] 打出1号位随从")
        rendered = _texts(action_feed.render())

        ocr_rows = [text for text in rendered if "OCR 行" in text]
        self.assertEqual(action_feed.MAX_OCR_LINES, len(ocr_rows))
        self.assertTrue(any("还有 8 行" in text for text in rendered))

        action_feed.clear()
        action_feed.set_ocr_provider(None)
        for index in range(20):
            action_feed.push(f"[执行] 第{index}次点击")

        rendered = _texts(action_feed.render())

        exec_rows = [text for text in rendered if "次点击" in text]
        self.assertEqual(action_feed.MAX_EXEC_LINES, len(exec_rows))
        self.assertTrue(any("还有 8 行" in text for text in rendered))

    def test_block_limit_keeps_only_the_newest_blocks(self):
        for index in range(action_feed.MAX_BLOCKS + 5):
            action_feed.push(f"[推荐] 动作 {index}")
            action_feed.push(f"[执行] 动作 {index}")

        blocks = action_feed.snapshot()

        self.assertEqual(action_feed.MAX_BLOCKS, len(blocks))
        # 最新的动作在最前，最旧的几块被丢掉了。
        self.assertEqual("[推荐] 动作 64", blocks[0]["ocr"][0][0])
        self.assertNotIn(
            "[推荐] 动作 0", [text for block in blocks for text, _t in block["ocr"]])


class ClearTests(ActionFeedCase):
    def test_clear_forgets_everything_and_snapshot_is_a_detached_copy(self):
        action_feed.push("-----我方出牌阶段-----")
        action_feed.push("[推荐] 打出1号位随从")
        action_feed.push("[执行] 打出1号位随从")

        action_feed.clear()

        self.assertEqual([], action_feed.snapshot())
        self.assertEqual([], action_feed.render())
        action_feed.push("[推荐] 新的动作")
        self.assertEqual("", action_feed.snapshot()[0]["phase"])

        blocks = action_feed.snapshot()
        blocks[0]["ocr"].append(("被外部改掉", "text"))
        blocks.clear()

        self.assertEqual(1, len(action_feed.snapshot()))
        self.assertEqual(1, len(action_feed.snapshot()[0]["ocr"]))


if __name__ == "__main__":
    unittest.main()
