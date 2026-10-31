# -*- coding: utf-8 -*-
"""每局结束的内存观测：日志一行 RSS + 阈值回收 + 告警。

背景（用户反馈）：「脚本运行久了占用的内存越来越多」。本文件锁住
``memory_watch.note_game_end`` 的可观测性与回收语义：

1. 低占用只打一行 ``[SYS] 内存 RSS …（较基线 … / 峰值 …）``；
2. 超过回收阈值先调 ``on_recycle``（顺序在回收日志之前），再打回收后的读数；
3. 回收后仍超告警阈值，warn 建议重启控制台；
4. 读数异常 / psutil 缺失时静默降级，观测逻辑绝不打断对局循环。
"""

import unittest
from unittest import mock

import memory_watch


class FakeRss:
    """按脚本依次返回读数，用完后重复最后一个（供回收后复测使用）。"""

    def __init__(self, *values):
        self.values = list(values)
        self.calls = 0

    def __call__(self):
        index = min(self.calls, len(self.values) - 1)
        self.calls += 1
        return self.values[index]


def boom():
    raise RuntimeError("rss boom")


class MemoryNoteTests(unittest.TestCase):
    def setUp(self):
        memory_watch.reset()
        info_patcher = mock.patch.object(memory_watch.print_info, "info_print")
        self.info_print = info_patcher.start()
        self.addCleanup(info_patcher.stop)
        warn_patcher = mock.patch.object(memory_watch.print_info, "warn_print")
        self.warn_print = warn_patcher.start()
        self.addCleanup(warn_patcher.stop)

    def _infos(self):
        return [call.args[0] for call in self.info_print.call_args_list]

    def _warns(self):
        return [call.args[0] for call in self.warn_print.call_args_list]

    def test_low_memory_reports_baseline_and_peak(self):
        reader = FakeRss(540.0, 640.0, 612.0)

        for _ in range(3):
            memory_watch.note_game_end(rss_reader=reader)

        self.assertEqual(
            [
                "[SYS] 内存 RSS 540 MB（较基线 +0 MB / 峰值 540 MB）",
                "[SYS] 内存 RSS 640 MB（较基线 +100 MB / 峰值 640 MB）",
                "[SYS] 内存 RSS 612 MB（较基线 +72 MB / 峰值 640 MB）",
            ],
            self._infos(),
        )
        self.assertEqual([], self._warns())

    def test_recycle_calls_the_callback_before_the_recycle_log(self):
        events = []
        reader = FakeRss(500.0, 950.0, 480.0)
        self.info_print.side_effect = lambda message: events.append(
            ("info", message))

        def recycle():
            events.append(("recycle", None))

        memory_watch.note_game_end(rss_reader=reader)
        memory_watch.note_game_end(on_recycle=recycle, rss_reader=reader)

        self.assertEqual(
            [
                ("info", "[SYS] 内存 RSS 500 MB（较基线 +0 MB / 峰值 500 MB）"),
                ("info", "[SYS] 内存 RSS 950 MB（较基线 +450 MB / 峰值 950 MB）"),
                ("recycle", None),
                ("info", "[SYS] 内存回收后：RSS 950 → 480 MB"),
            ],
            events,
        )
        self.assertEqual([], self._warns())

    def test_recycle_without_callback_still_reports_the_new_reading(self):
        reader = FakeRss(1000.0, 800.0)

        memory_watch.note_game_end(rss_reader=reader)

        self.assertEqual(
            [
                "[SYS] 内存 RSS 1000 MB（较基线 +0 MB / 峰值 1000 MB）",
                "[SYS] 内存回收后：RSS 1000 → 800 MB",
            ],
            self._infos(),
        )

    def test_recycle_callback_failure_does_not_escape(self):
        memory_watch.reset()
        reader = FakeRss(500.0, 950.0, 480.0)
        memory_watch.note_game_end(rss_reader=reader)

        def broken_recycle():
            raise RuntimeError("recycle boom")

        memory_watch.note_game_end(on_recycle=broken_recycle, rss_reader=reader)

        self.assertIn("内存回收后：RSS 950 → 480 MB", self._infos()[-1])

    def test_warn_threshold_after_recycle_advises_restart(self):
        reader = FakeRss(500.0, 1500.0, 1450.0)
        memory_watch.note_game_end(rss_reader=reader)
        self.assertEqual([], self._warns())

        memory_watch.note_game_end(rss_reader=reader)

        self.assertEqual(
            ["[WARN] 内存占用偏高（1450 MB），建议重启控制台：hsctl serve restart"],
            self._warns(),
        )

    def test_rss_reader_failure_and_none_reading_are_silent(self):
        memory_watch.note_game_end(rss_reader=boom)
        memory_watch.note_game_end(rss_reader=lambda: None)

        self.assertEqual([], self._infos())
        self.assertEqual([], self._warns())

    def test_missing_psutil_degrades_to_none(self):
        with mock.patch.object(memory_watch, "psutil", None):
            self.assertIsNone(memory_watch.rss_mb())

    def test_reset_recomputes_the_baseline_and_peak_is_the_maximum(self):
        with mock.patch.object(memory_watch, "rss_mb", return_value=400.0):
            self.assertEqual(400.0, memory_watch.baseline_mb())
            self.assertAlmostEqual(400.0, memory_watch.peak_mb())

        with mock.patch.object(memory_watch, "rss_mb", return_value=900.0):
            # 已有基线时直接返回旧值，不重新采样。
            self.assertEqual(400.0, memory_watch.baseline_mb())
            memory_watch.reset()
            self.assertEqual(900.0, memory_watch.baseline_mb())
            self.assertAlmostEqual(900.0, memory_watch.peak_mb())

        memory_watch.reset()
        reader = FakeRss(500.0, 700.0, 600.0)
        for _ in range(3):
            memory_watch.note_game_end(rss_reader=reader)

        self.assertAlmostEqual(700.0, memory_watch.peak_mb())
        self.assertAlmostEqual(500.0, memory_watch.baseline_mb())

        memory_watch.reset()

        self.assertIsNone(memory_watch.peak_mb())
        with mock.patch.object(memory_watch, "rss_mb", return_value=900.0):
            self.assertEqual(900.0, memory_watch.baseline_mb())


class GameEndHookTests(unittest.TestCase):
    """FSM_action 的集成钩子：每局结束调 note_game_end，并给一个能重建引擎的回调。"""

    def test_hook_builds_a_working_recycle_and_survives_a_missing_reader(self):
        import FSM_action

        reloads = []
        captured = {}
        saved_reader = FSM_action.recommendation_reader
        try:
            FSM_action.recommendation_reader = mock.Mock(
                backend=mock.Mock(reload=lambda: reloads.append(True)))
            with mock.patch.object(
                    memory_watch, "note_game_end",
                    side_effect=lambda on_recycle=None: captured.update(
                        recycle=on_recycle)):
                FSM_action._note_memory_at_game_end()

            self.assertIsNotNone(captured.get("recycle"))
            captured["recycle"]()
            self.assertEqual([True], reloads)

            FSM_action.recommendation_reader = None
            with mock.patch.object(memory_watch, "note_game_end") as note:
                FSM_action._note_memory_at_game_end()
            self.assertIsNone(note.call_args.kwargs.get("on_recycle"))
        finally:
            FSM_action.recommendation_reader = saved_reader


if __name__ == "__main__":
    unittest.main()
