# -*- coding: utf-8 -*-
"""结构化分块对战日志：行分类 / 分块 / 渲染 / 自动落盘。"""

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path

import battle_record as br


def _rec(ts, level, text):
    return {"t": ts, "level": level, "text": text}


BASE = 1_700_000_000.0


def sample_records():
    return [
        _rec(BASE + 0, "SYS", "[SYS] 回合 2 延时结束，开始本轮推荐读取。"),
        _rec(BASE + 1, "INFO", "[推荐] 打出1号位随从"),
        _rec(BASE + 1.5, "INFO", "[推荐] 原文：PlayerTurnChoice: 出1号随从"),
        _rec(BASE + 2, "SYS", "当前推荐暂不可执行，继续重试：recommendation_changed"),
        _rec(BASE + 3, "SYS", "当前推荐暂不可执行，继续重试：recommendation_changed"),
        _rec(BASE + 4, "INFO", "[推荐] 打出1号位随从"),  # 指令不变 → 并进同一小块
        _rec(BASE + 5, "INFO", "[执行] 开始点击。"),
        _rec(BASE + 6, "SYS", "[SYS] 操作结束，延时 1.0s 后继续"),
        _rec(BASE + 20, "SYS", "[SYS] 回合 3 延时结束，开始本轮推荐读取。"),
        _rec(BASE + 21, "INFO", "[推荐] 使用英雄技能"),
        _rec(BASE + 22, "INFO", "[执行] 开始点击。"),
        _rec(BASE + 23, "ERROR", "[ERROR] 点击穿透窗校验失败：invalid_entity"),
    ]


class ClassifyTests(unittest.TestCase):
    def test_turn_header(self):
        info = br.classify("[SYS] 回合 7 延时结束，开始本轮推荐读取。", "SYS")
        self.assertEqual("turn", info["kind"])
        self.assertEqual(7, info["turn"])

    def test_phase_header(self):
        info = br.classify("-----匹配阶段-----", "SYS")
        self.assertEqual("phase", info["kind"])
        self.assertEqual("匹配", info["phase"])

    def test_recommendation_and_raw(self):
        self.assertEqual(
            "打出1号位随从", br.classify("[推荐] 打出1号位随从")["instruction"]
        )
        info = br.classify("[推荐] 原文：PlayerTurnChoice: 出1号随从")
        self.assertEqual("recommendation_raw", info["kind"])
        self.assertEqual("PlayerTurnChoice: 出1号随从", info["text"])

    def test_execution(self):
        self.assertEqual("execution", br.classify("[执行] 开始点击。")["kind"])

    def test_retry_reason(self):
        info = br.classify("当前推荐暂不可执行，继续重试：recommendation_changed")
        self.assertEqual("retry", info["kind"])
        self.assertEqual("recommendation_changed", info["reason"])

    def test_error_and_warn(self):
        self.assertEqual("error", br.classify("[ERROR] boom", "ERROR")["kind"])
        self.assertEqual("error", br.classify("⚠️ 炉石已退出", "WARN")["kind"])
        self.assertEqual("warn", br.classify("[WARN] 注意", "WARN")["kind"])

    def test_plain_line_is_info(self):
        self.assertEqual("info", br.classify("随便一行")["kind"])


class BuildBlocksTests(unittest.TestCase):
    def setUp(self):
        self.blocks = br.build_blocks(sample_records())

    def test_two_turns(self):
        self.assertEqual([2, 3], [b["turn"] for b in self.blocks])

    def test_same_instruction_merges_into_one_step(self):
        turn2 = self.blocks[0]
        self.assertEqual(1, len(turn2["steps"]))
        step = turn2["steps"][0]
        self.assertEqual("打出1号位随从", step["instruction"])
        self.assertEqual(["PlayerTurnChoice: 出1号随从"], step["raw"])

    def test_retry_count_and_reasons(self):
        step = self.blocks[0]["steps"][0]
        self.assertEqual(2, step["retries"])
        self.assertEqual({"recommendation_changed": 2}, dict(step["retry_reasons"]))
        self.assertEqual("executed", step["status"])

    def test_error_status_beats_execution(self):
        step = self.blocks[1]["steps"][0]
        self.assertEqual("error", step["status"])
        self.assertIn("invalid_entity", step["detail"])

    def test_duration(self):
        # 回合 2 小块：从首次读到推荐(+1)到最后一条(+6) = 5s。
        self.assertEqual(5.0, self.blocks[0]["steps"][0]["duration"])
        # 大块耗时 = 首末事件时间差（回合 2：+0 → +6）。
        self.assertEqual(6.0, self.blocks[0]["duration"])

    def test_summary(self):
        self.assertEqual(
            {"steps": 1, "retries": 2, "errors": 0, "duration_seconds": 6.0},
            self.blocks[0]["summary"],
        )
        self.assertEqual(1, self.blocks[1]["summary"]["errors"])

    def test_retry_events_dedup(self):
        step = self.blocks[0]["steps"][0]
        self.assertEqual(1, len([e for e in step["events"] if "重试" in e]))

    def test_phase_labels_pre_game_block(self):
        recs = [
            _rec(BASE, "SYS", "-----匹配阶段-----"),
            _rec(BASE + 1, "SYS", "[SYS] 已点「传统对战」"),
        ]
        blocks = br.build_blocks(recs)
        self.assertEqual(1, len(blocks))
        self.assertIsNone(blocks[0]["turn"])
        self.assertEqual("匹配", blocks[0]["phase"])


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.blocks = br.build_blocks(sample_records())

    def test_jsonl_is_one_meta_plus_one_block_per_line(self):
        text = br.render_jsonl(self.blocks, session="s1", app="app")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        self.assertEqual(3, len(lines))
        meta = json.loads(lines[0])
        self.assertEqual("meta", meta["kind"])
        self.assertEqual("s1", meta["session"])
        first = json.loads(lines[1])
        self.assertEqual(2, first["turn"])
        self.assertEqual("executed", first["steps"][0]["status"])
        self.assertEqual(
            [{"reason": "recommendation_changed", "count": 2}],
            first["steps"][0]["retry_reasons"],
        )

    def test_text_contains_step_separator_and_fields(self):
        text = br.render_text(
            self.blocks, {"date": "2026-10-18 12:00:00", "session": "s1"}
        )
        self.assertIn("回合 2", text)
        self.assertIn("--- 步骤 1/1", text)
        self.assertIn("推荐    打出1号位随从", text)
        self.assertIn("重试    2 次", text)
        self.assertIn("状态    已执行", text)
        self.assertIn("报错", text)

    def test_partial_flag_in_block_to_obj(self):
        obj = br.block_to_obj(self.blocks[0], partial=True)
        self.assertTrue(obj["partial"])


class SessionTests(unittest.TestCase):
    def test_start_session_sets_stamp_and_clears(self):
        br.record("INFO", "hello")
        stamp = br.start_session(session_id="sess", now=BASE)
        self.assertEqual("sess", stamp)
        from datetime import datetime

        expected = datetime.fromtimestamp(BASE).strftime("%Y%m%d_%H%M%S")
        self.assertEqual(expected, br.session_stamp())
        self.assertEqual([], br.records())


class AutoAppenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.gettempdir())) / (
            f"hs_battle_record_{os.getpid()}_{uuid.uuid4().hex[:8]}"
        )
        self.tmp.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_flush_writes_completed_turns_once(self):
        blocks = br.build_blocks(sample_records())
        path = self.tmp / "rec.jsonl"
        app = br.AutoAppender()
        self.assertEqual(1, app.flush(path, blocks, meta=br.meta_obj(session="s")))
        # 再 flush 一次：已写过的（回合 2）不重复，最后一块（回合 3）不写。
        self.assertEqual(0, app.flush(path, blocks))
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
        self.assertEqual(2, len(lines))  # meta + 回合 2
        self.assertEqual(2, json.loads(lines[1])["turn"])


if __name__ == "__main__":
    unittest.main()
