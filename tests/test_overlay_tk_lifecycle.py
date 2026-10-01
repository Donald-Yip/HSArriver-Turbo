# -*- coding: utf-8 -*-
"""浮窗的 Tk 生命周期：Tcl 解释器必须死在创建它的那个线程里。

背景（用户反馈）：急停之后点「恢复」，控制台出现
    Tcl_AsyncDelete: async handler deleted by the wrong thread

原因：tkinter 的 Tk 实例、控件和回调闭包互相引用成环，引用计数永远不会归零，
只能靠 GC 回收。窗口 destroy 之后如果还把这个环留给别的线程回收（恢复时新线程
一分配对象就可能触发 GC），Tcl 就会在错误的线程里删掉解释器。
所以 _run() 必须在本线程里断开引用 + 立刻 gc.collect()，start() 也必须等上一轮
线程真正结束再建新的 Tk。
"""

import inspect
import unittest
from unittest.mock import patch

import log_overlay


class _Thread:
    """threading.Thread 的替身：可控 is_alive / 记录 join。

    on_exit 模拟浮窗线程收尾时把 _STARTED[0] 放掉（真实 _run 的 finally 就是这样）。
    """

    def __init__(self, on_exit=None, **_ignored):
        self.joined = []
        self.started = False
        self.target = None
        self.on_exit = on_exit
        self._alive = True

    def start(self):
        self.started = True

    def is_alive(self):
        return self._alive

    def join(self, timeout=None):
        self.joined.append(timeout)
        self._alive = False
        if self.on_exit is not None:
            self.on_exit()


class _HangingThread(_Thread):
    """join 之后依然存活：模拟收尾超时。"""

    def join(self, timeout=None):
        self.joined.append(timeout)


class _ThreadFactory:
    def __init__(self, cls=_Thread, on_exit=None):
        self.cls = cls
        self.on_exit = on_exit
        self.created = []

    def __call__(self, target=None, name=None, daemon=None, **_ignored):
        thread = self.cls(on_exit=self.on_exit)
        thread.target = target
        self.created.append(thread)
        return thread


def _release_started():
    log_overlay._STARTED[0] = False


class OverlayStateTestCase(unittest.TestCase):
    """保存/恢复 log_overlay 的模块级状态，避免用例互相污染。"""

    def setUp(self):
        self._started = log_overlay._STARTED[0]
        self._thread = log_overlay._THREAD
        self._stop = log_overlay._STOP.is_set()
        log_overlay._STARTED[0] = False
        log_overlay._THREAD = None
        log_overlay._STOP.clear()

    def tearDown(self):
        log_overlay._STARTED[0] = self._started
        log_overlay._THREAD = self._thread
        if self._stop:
            log_overlay._STOP.set()
        else:
            log_overlay._STOP.clear()


class RunTeardownSourceTests(unittest.TestCase):
    """Tk 回收必须在 _run 的帧释放之后（回收顺序：先 _run 返回，再 gc，再放 _STARTED）。

    真开一个 Tk 窗口来做单测太重，这里按仓库里既有的 inspect.getsource 风格
    锁住结构；行为部分由下面的 start()/join 用例覆盖。
    """

    def setUp(self):
        self.source = inspect.getsource(log_overlay._run_overlay_thread)

    def test_wraps_run_in_a_finally_block(self):
        self.assertIn("_run()", self.source)
        self.assertIn("finally:", self.source)
        self.assertIn("gc.collect()", self.source)
        self.assertIn("_STARTED[0] = False", self.source)

    def test_collects_before_releasing_started(self):
        self.assertLess(self.source.index("gc.collect()"),
                        self.source.index("_STARTED[0] = False"))

    def test_frame_must_be_released_before_collecting(self):
        """回收必须包在 _run() 外面：_run 的帧钉着整张 Tk 图，帧内回收等于没回收。"""
        run_source = inspect.getsource(log_overlay._run)
        self.assertNotIn("gc.collect()", run_source)

    def test_start_threads_the_wrapper_not_run(self):
        source = inspect.getsource(log_overlay.start)
        self.assertIn("target=_run_overlay_thread", source)

    def test_gc_is_imported(self):
        self.assertIn("gc", dir(log_overlay))


class JoinPreviousThreadTests(OverlayStateTestCase):
    def test_no_previous_thread_is_a_noop(self):
        log_overlay._join_previous_thread()

        self.assertIsNone(log_overlay._THREAD)

    def test_live_thread_is_joined_and_released(self):
        thread = _Thread()
        log_overlay._THREAD = thread

        log_overlay._join_previous_thread()

        self.assertEqual([log_overlay._SHUTDOWN_TIMEOUT], thread.joined)
        self.assertIsNone(log_overlay._THREAD)

    def test_thread_double_without_is_alive_is_released(self):
        """测试替身没有 is_alive() 时不能炸（有些老替身就长这样）。"""

        class _Bare:
            def join(self, timeout=None):
                raise AssertionError("不该调用 join")

        log_overlay._THREAD = _Bare()

        log_overlay._join_previous_thread()

        self.assertIsNone(log_overlay._THREAD)

    def test_current_thread_is_never_joined(self):
        """从浮窗线程自己调用时不能自 join（会死锁）。"""
        calls = []
        self_ref = _Thread()
        self_ref.join = lambda timeout=None: calls.append(timeout)

        with patch.object(log_overlay.threading, "current_thread",
                          return_value=self_ref):
            log_overlay._THREAD = self_ref
            log_overlay._join_previous_thread()

        self.assertEqual([], calls)
        self.assertIs(self_ref, log_overlay._THREAD)


class StartLifecycleTests(OverlayStateTestCase):
    def _factory(self, cls=_Thread):
        # 替身线程收尾时也要把 _STARTED 放掉，否则不像真实的浮窗线程。
        return _ThreadFactory(cls=cls, on_exit=_release_started)

    def test_start_records_the_thread(self):
        factory = self._factory()

        with patch.object(log_overlay.threading, "Thread", factory):
            log_overlay.start()

        self.assertEqual(1, len(factory.created))
        self.assertTrue(factory.created[0].started)
        self.assertIs(factory.created[0], log_overlay._THREAD)
        self.assertTrue(log_overlay._STARTED[0])

    def test_start_is_a_noop_while_running(self):
        factory = self._factory()

        with patch.object(log_overlay.threading, "Thread", factory):
            log_overlay.start()
            log_overlay.start()

        self.assertEqual(1, len(factory.created))

    def test_reopening_waits_for_the_previous_thread(self):
        """正在退出时点重开：必须先等旧线程收尾，再建新的 Tk。"""
        factory = self._factory()

        with patch.object(log_overlay.threading, "Thread", factory):
            log_overlay.start()
            first = factory.created[0]
            log_overlay._STOP.set()          # 窗口正在销毁
            log_overlay.start()              # 立刻重开

        self.assertEqual([log_overlay._SHUTDOWN_TIMEOUT], first.joined)
        self.assertEqual(2, len(factory.created))
        self.assertIs(factory.created[1], log_overlay._THREAD)
        self.assertFalse(log_overlay._STOP.is_set())

    def test_reopening_is_skipped_when_the_old_thread_hangs(self):
        """旧线程收尾超时 → 宁可不开窗，也不要两个 Tk 解释器并存。"""
        factory = self._factory(cls=_HangingThread)

        with patch.object(log_overlay.threading, "Thread", factory):
            log_overlay.start()
            log_overlay._STOP.set()
            log_overlay.start()

        self.assertEqual(1, len(factory.created))


class IsRunningTests(OverlayStateTestCase):
    def test_running_means_started_and_not_stopping(self):
        log_overlay._STARTED[0] = True

        self.assertTrue(log_overlay.is_running())

        log_overlay._STOP.set()

        self.assertFalse(log_overlay.is_running())    # 退出中 = 可以立刻重开

    def test_stopped_means_not_running(self):
        log_overlay._STARTED[0] = False

        self.assertFalse(log_overlay.is_running())


if __name__ == "__main__":
    unittest.main()
