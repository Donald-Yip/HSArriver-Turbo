# -*- coding: utf-8 -*-
"""window_tracker：窗口移动/缩放的实时感知（不真挂钩子、不真读窗口）。

用注入的假 hook / 假 reader 覆盖：
1. ensure_started 幂等、失败不抛异常；
2. 事件回调只更新时间戳与计数；
3. moved_since 语义；
4. hook 不可用时的 poll 兜底能发现几何变化；
5. stop 之后不再记录。
"""
from __future__ import annotations

import threading
import time
import unittest

import window_tracker as wt


class FakeHook:
    """假的 SetWinEventHook：用 Condition 同步挂钩，再手动触发事件。"""

    def __init__(self, succeeds=True):
        self.succeeds = succeeds
        self.procs = []
        self.handles = []
        self.unhooked = []
        self.attempts = 0
        self._condition = threading.Condition()

    def factory(self, handle, proc):
        with self._condition:
            self.attempts += 1
            if not self.succeeds:
                self._condition.notify_all()
                return 0
            self.procs.append(proc)
            handle_id = 1000 + self.attempts
            self.handles.append(handle_id)
            self._condition.notify_all()
            return handle_id

    def unhook(self, hook):
        with self._condition:
            self.unhooked.append(hook)
            self._condition.notify_all()

    @property
    def proc(self):
        return self.procs[-1] if self.procs else None

    def wait_for_attached(self, count=1, timeout=2.0):
        with self._condition:
            return self._condition.wait_for(
                lambda: len(self.handles) >= count, timeout)

    def wait_for_attempt(self, timeout=2.0):
        with self._condition:
            return self._condition.wait_for(lambda: self.attempts >= 1, timeout)

    def wait_for_unhook(self, count=1, timeout=2.0):
        with self._condition:
            return self._condition.wait_for(
                lambda: len(self.unhooked) >= count, timeout)

    def fire(self, hwnd=4242):
        proc = self.proc
        assert proc is not None, "hook 还没挂上"
        proc(1, wt.EVENT_OBJECT_LOCATIONCHANGE, hwnd, 0, 0, 0, 0)


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.hook = FakeHook()
        self.now = [100.0]
        self.tracker = wt.WindowMoveTracker(
            hook_factory=self.hook.factory,
            unhook=self.hook.unhook,
            hwnd_provider=lambda: 4242,
            clock=lambda: self.now[0])

    def tearDown(self):
        self.tracker.stop()

    def _wait_attached_and_ready(self, count=1):
        self.assertTrue(self.hook.wait_for_attached(count),
                        f"挂钩是异步的，应同步等到第 {count} 次挂上")
        self.assertTrue(self.tracker.wait_ready(timeout=2.0))

    def test_ensure_started_is_idempotent(self):
        self.assertTrue(self.tracker.ensure_started(4242))
        self._wait_attached_and_ready()

        self.assertTrue(self.tracker.ensure_started(4242))

        self.assertEqual(1, self.hook.attempts, "重复调用不应重复挂钩")
        self.assertEqual(1, len(self.hook.handles))
        self.assertTrue(self.tracker.available())

    def test_no_window_means_not_started(self):
        tracker = wt.WindowMoveTracker(hook_factory=self.hook.factory,
                                       unhook=self.hook.unhook,
                                       hwnd_provider=lambda: 0,
                                       clock=lambda: self.now[0])
        try:
            self.assertFalse(tracker.ensure_started())
            self.assertFalse(tracker.available())
        finally:
            tracker.stop()

    def test_hook_failure_degrades_without_raising(self):
        failed_hook = FakeHook(succeeds=False)
        tracker = wt.WindowMoveTracker(
            hook_factory=failed_hook.factory,
            unhook=failed_hook.unhook,
            hwnd_provider=lambda: 4242,
            clock=lambda: self.now[0])
        try:
            self.assertTrue(tracker.ensure_started(4242))
            self.assertTrue(failed_hook.wait_for_attempt())
            self.assertFalse(tracker.wait_ready(timeout=2.0),
                             "挂钩失败应报告不可用")
        finally:
            tracker.stop()

    def test_event_updates_timestamp_and_count(self):
        self.tracker.ensure_started(4242)
        self._wait_attached_and_ready()
        self.assertFalse(self.tracker.moved_since(0.0))

        self.now[0] = 120.0
        self.hook.fire()

        self.assertTrue(self.tracker.moved_since(100.0))
        self.assertFalse(self.tracker.moved_since(120.0))
        self.assertEqual(120.0, self.tracker.last_move_at())
        self.assertEqual(1, self.tracker.stats()["events"])

    def test_stop_stops_recording(self):
        self.tracker.ensure_started(4242)
        self._wait_attached_and_ready()
        self.tracker.stop()

        self.assertTrue(self.hook.wait_for_unhook())
        self.assertFalse(self.tracker.available())

    def test_stop_unhooks_synchronously_exactly_once(self):
        self.tracker.ensure_started(4242)
        self._wait_attached_and_ready()
        thread = self.tracker._thread

        self.tracker.stop()

        self.assertEqual([1001], self.hook.unhooked,
                         "stop 必须同步摘掉当前 hook")
        self.assertFalse(self.tracker.available())
        if thread is not None:
            thread.join(timeout=1.0)
            self.assertFalse(thread.is_alive())
        self.assertEqual([1001], self.hook.unhooked,
                         "线程 finally 不得再次摘 hook")

    def test_poll_detects_geometry_change(self):
        geometries = [(1920, 1080, 960, 362)]

        def reader(hwnd):
            return geometries[-1]

        tracker = wt.WindowMoveTracker(hook_factory=self.hook.factory,
                                       unhook=self.hook.unhook,
                                       hwnd_provider=lambda: 4242,
                                       poll_reader=reader,
                                       clock=lambda: self.now[0])
        try:
            self.assertFalse(tracker.poll(), "首次采样只记录基准，不算移动")
            self.assertFalse(tracker.moved_since(0.0))

            self.now[0] = 200.0
            geometries.append((1920, 1080, 444, 691))
            self.assertTrue(tracker.poll(), "几何变化应被识别为移动")
            self.assertTrue(tracker.moved_since(100.0))
            self.assertEqual(200.0, tracker.last_move_at())

            self.assertFalse(tracker.poll(), "几何没变就不该反复标记")
            # 只有"变化"那一次计数：首次采样是建立基准、最后一次无变化
            self.assertEqual(1, tracker.stats()["events"])
        finally:
            tracker.stop()

    def test_poll_without_reader_is_safe(self):
        tracker = wt.WindowMoveTracker(hook_factory=self.hook.factory,
                                       unhook=self.hook.unhook,
                                       hwnd_provider=lambda: 4242,
                                       poll_reader=lambda hwnd: None,
                                       clock=lambda: self.now[0])
        try:
            self.assertFalse(tracker.poll())
            self.assertFalse(tracker.poll())
        finally:
            tracker.stop()

    def test_rehook_when_handle_changes(self):
        self.tracker.ensure_started(4242)
        self._wait_attached_and_ready(1)

        self.tracker.ensure_started(9999)

        self.assertTrue(self.hook.wait_for_attached(2),
                        "句柄变化应重新挂钩")
        self.assertTrue(self.tracker.wait_ready(timeout=2.0))

    def test_stats_shape(self):
        stats = self.tracker.stats()

        self.assertEqual({"available", "events", "polls", "last_move_at", "hwnd"},
                         set(stats))
        self.assertFalse(stats["available"])

    def test_wait_ready_sees_the_hook_after_startup(self):
        """挂钩是异步的：状态显示前要等线程挂完，否则会误报"轮询兜底"。"""
        self.tracker.ensure_started(4242)
        self.assertTrue(self.hook.wait_for_attached())

        self.assertTrue(self.tracker.wait_ready(timeout=2.0))
        self.assertFalse(self.tracker.starting())

    def test_wait_ready_gives_up_when_the_hook_fails(self):
        failed_hook = FakeHook(succeeds=False)
        tracker = wt.WindowMoveTracker(
            hook_factory=failed_hook.factory,
            unhook=failed_hook.unhook,
            hwnd_provider=lambda: 4242,
            clock=lambda: self.now[0])
        try:
            tracker.ensure_started(4242)
            self.assertTrue(failed_hook.wait_for_attempt())

            self.assertFalse(tracker.wait_ready(timeout=2.0))
            self.assertFalse(tracker.starting(), "失败也必须结束等待状态")
        finally:
            tracker.stop()

    def test_wait_ready_never_blocks_when_nothing_is_starting(self):
        """按帧调用（叠加层/预览）不能每帧都等：没在启动就立刻返回。"""
        done = threading.Event()
        results = []

        def wait():
            results.append(self.tracker.wait_ready(timeout=1.0))
            done.set()

        thread = threading.Thread(target=wait, name="test-wait-ready", daemon=True)
        thread.start()

        self.assertTrue(done.wait(1.0), "wait_ready 在未启动时不应阻塞")
        self.assertEqual([False], results)


class ClockConsistencyTests(unittest.TestCase):
    """回归：跟踪器的默认时钟必须与 display_mapping **同源**。

    背景（用户报告"校准窗口不随炉石移动"的根因之一）：这里曾经默认
    `time.monotonic`（Windows 上是开机秒数，约 1e5~1e6），而 display_mapping
    记缓存时刻用的是 `time.time`（纪元秒，约 1.7e9）。`moved_since(t)` 是直接
    比大小而不是求差，于是恒为 False —— 钩子挂上了、事件也收到了，缓存却永远
    不失效，窗口一动几何就冻结在第一次采样上。
    """

    def test_default_clock_is_the_same_source_as_display_mapping(self):
        import display_mapping

        self.assertIs(wt.default_clock, display_mapping.time.time)
        tracker = wt.WindowMoveTracker(hwnd_provider=lambda: 0)

        self.assertIs(tracker._clock, display_mapping.time.time)

    def test_a_fresh_move_compares_correctly_against_the_wall_clock(self):
        """默认时钟下，"刚刚动过"必须能被过去的时间戳看到。"""
        tracker = wt.WindowMoveTracker(hwnd_provider=lambda: 0)
        tracker.note_move()

        self.assertTrue(tracker.moved_since(time.time() - 5.0))
        self.assertFalse(tracker.moved_since(time.time() + 5.0))


class ModuleSingletonTests(unittest.TestCase):
    def test_module_helpers_do_not_raise_without_window(self):
        # 本机有没有炉石都不该抛异常
        wt.ensure_started()
        wt.moved_since(0.0)
        wt.last_move_at()
        wt.stats()
        wt.poll()
        wt.stop()

    def test_stop_is_reentrant(self):
        wt.stop()
        wt.stop()


if __name__ == "__main__":
    unittest.main()
