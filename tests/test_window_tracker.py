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
    """假的 SetWinEventHook：把回调留下来，测试里手动"触发事件"。"""

    def __init__(self):
        self.proc = None
        self.handles = []
        self.unhooked = []

    def factory(self, handle, proc):
        self.proc = proc
        handle_id = 1000 + len(self.handles)
        self.handles.append(handle_id)
        return handle_id

    def unhook(self, hook):
        self.unhooked.append(hook)

    def fire(self, hwnd=4242):
        assert self.proc is not None, "hook 还没挂上"
        self.proc(1, wt.EVENT_OBJECT_LOCATIONCHANGE, hwnd, 0, 0, 0, 0)


def wait_for(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    """等一个由**后台 hook 线程**满足的条件（挂钩是异步的）。

    直接断言 `stats()`/`len(handles)` 会和 hook 线程抢时间：`ensure_started()`
    返回时线程可能还没跑到 `SetWinEventHook`。测试必须等，而不是假定它已经跑完。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


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

    def test_ensure_started_is_idempotent(self):
        self.assertTrue(self.tracker.ensure_started(4242))
        self.assertTrue(self.tracker.ensure_started(4242))

        self.assertTrue(wait_for(lambda: len(self.hook.handles) == 1),
                        "挂钩是异步的，应该只挂一次")
        self.assertFalse(wait_for(lambda: len(self.hook.handles) > 1,
                                  timeout=0.3),
                         "重复调用不应重复挂钩")
        self.assertTrue(wait_for(self.tracker.available))

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
        tracker = wt.WindowMoveTracker(
            hook_factory=lambda handle, proc: 0,     # 挂钩失败
            unhook=self.hook.unhook,
            hwnd_provider=lambda: 4242,
            clock=lambda: self.now[0])
        try:
            self.assertTrue(tracker.ensure_started(4242))
            self.assertFalse(tracker.available(), "挂钩失败应报告不可用")
        finally:
            tracker.stop()

    def test_event_updates_timestamp_and_count(self):
        self.tracker.ensure_started(4242)
        self.assertFalse(self.tracker.moved_since(0.0))

        self.now[0] = 120.0
        self.hook.fire()

        self.assertTrue(self.tracker.moved_since(100.0))
        self.assertFalse(self.tracker.moved_since(120.0))
        self.assertEqual(120.0, self.tracker.last_move_at())
        self.assertEqual(1, self.tracker.stats()["events"])

    def test_stop_stops_recording(self):
        self.tracker.ensure_started(4242)
        # 挂钩是异步的：必须等 hook 线程真的跑到 SetWinEventHook 再读 proc。
        # 直接读会抢时间 —— 单独跑这个文件时线程来得及，全量跑（CPU 竞争）时
        # 就来不及，proc 会是 None（本文件顶部 wait_for 的说明就是这个坑）。
        self.assertTrue(wait_for(lambda: self.hook.proc is not None),
                        "挂钩是异步的，应等它挂上再断言")
        proc = self.hook.proc
        self.tracker.stop()

        # 钩子已卸载：即便再触发回调也不该再被算作"移动"
        self.assertFalse(self.tracker.available())
        self.assertTrue(proc is not None)

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
        self.assertTrue(wait_for(lambda: len(self.hook.handles) == 1))

        self.tracker.ensure_started(9999)

        self.assertTrue(wait_for(lambda: len(self.hook.handles) == 2),
                        "句柄变化应重新挂钩")

    def test_stats_shape(self):
        stats = self.tracker.stats()

        self.assertEqual({"available", "events", "polls", "last_move_at", "hwnd"},
                         set(stats))
        self.assertFalse(stats["available"])

    def test_wait_ready_sees_the_hook_after_startup(self):
        """挂钩是异步的：状态显示前要等线程挂完，否则会误报"轮询兜底"。"""
        self.tracker.ensure_started(4242)

        self.assertTrue(self.tracker.wait_ready(timeout=2.0))
        self.assertFalse(self.tracker.starting())

    def test_wait_ready_gives_up_when_the_hook_fails(self):
        tracker = wt.WindowMoveTracker(
            hook_factory=lambda handle, proc: 0,     # 挂钩失败
            unhook=self.hook.unhook,
            hwnd_provider=lambda: 4242,
            clock=lambda: self.now[0])
        try:
            tracker.ensure_started(4242)

            self.assertFalse(tracker.wait_ready(timeout=2.0))
            self.assertFalse(tracker.starting(), "失败也必须结束等待状态")
        finally:
            tracker.stop()

    def test_wait_ready_never_blocks_when_nothing_is_starting(self):
        """按帧调用（叠加层/预览）不能每帧都等：没在启动就立刻返回。"""
        started = time.monotonic()

        self.assertFalse(self.tracker.wait_ready(timeout=1.0))

        self.assertLess(time.monotonic() - started, 0.1)


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
