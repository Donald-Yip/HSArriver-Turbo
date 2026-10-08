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
        self._closing = log_overlay._CLOSING.is_set()
        self._generation = log_overlay._GENERATION[0]
        self._on_halt = log_overlay._ON_HALT
        self._reporter = log_overlay._REPORTER
        log_overlay._STARTED[0] = False
        log_overlay._THREAD = None
        log_overlay._STOP.clear()
        log_overlay._CLOSING.clear()
        log_overlay._OPEN_PENDING.clear()

    def tearDown(self):
        log_overlay._STARTED[0] = self._started
        log_overlay._THREAD = self._thread
        log_overlay._GENERATION[0] = self._generation
        log_overlay._ON_HALT = self._on_halt
        log_overlay._REPORTER = self._reporter
        log_overlay._OPEN_PENDING.clear()
        if self._stop:
            log_overlay._STOP.set()
        else:
            log_overlay._STOP.clear()
        if self._closing:
            log_overlay._CLOSING.set()
        else:
            log_overlay._CLOSING.clear()

    def _factory(self, cls=_Thread):
        # 替身线程收尾时也要把 _STARTED 放掉，否则不像真实的浮窗线程。
        return _ThreadFactory(cls=cls, on_exit=_release_started)


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

    def test_reopening_is_queued_when_the_old_thread_hangs(self):
        """旧线程收尾超时 → 不开第二个 Tk，但**绝不静默丢弃**：排队等它。

        这是用户反馈「退出浮窗后从网页点开始，浮窗再也弹不出来」的另一条路径：
        老实现直接 return，网页却照样报"已就绪"，用户完全不知道发生了什么。
        """
        factory = self._factory(cls=_HangingThread)

        with patch.object(log_overlay.threading, "Thread", factory):
            log_overlay.start()
            log_overlay._STOP.set()
            status = log_overlay.start()

        self.assertEqual(log_overlay.OPEN_PENDING, status)
        # 第 1 个是浮窗线程，第 2 个是"等它结束再开窗"的排队线程。
        self.assertEqual(2, len(factory.created))
        self.assertIs(log_overlay._deferred_open_worker,
                      factory.created[1].target)
        self.assertTrue(log_overlay._OPEN_PENDING.is_set())


class ReopenRaceTests(OverlayStateTestCase):
    """「退出浮窗」→ 立刻从网页点开始：必须把浮窗重新开出来。"""

    def test_start_reports_already_open_and_started(self):
        factory = self._factory()

        with patch.object(log_overlay.threading, "Thread", factory):
            self.assertEqual(log_overlay.OPEN_STARTED, log_overlay.start())
            self.assertEqual(log_overlay.OPEN_ALREADY, log_overlay.start())

        self.assertEqual(1, len(factory.created))

    def test_exit_then_immediate_reopen_closes_the_old_window_and_reopens(self):
        """复现用户反馈：退出按钮按下（stop 还有 150ms 才到）就点网页开始。"""
        factory = self._factory()

        with patch.object(log_overlay.threading, "Thread", factory):
            log_overlay.start()
            first = factory.created[0]
            log_overlay.mark_closing()          # 退出浮窗按下，root.after 还没到点
            self.assertFalse(log_overlay.is_running())
            status = log_overlay.start()        # 网页立刻点「开始运行」

        self.assertEqual(log_overlay.OPEN_STARTED, status)
        self.assertEqual([log_overlay._SHUTDOWN_TIMEOUT], first.joined)
        self.assertEqual(2, len(factory.created))
        self.assertFalse(log_overlay._STOP.is_set())
        self.assertFalse(log_overlay._CLOSING.is_set())

    def test_a_late_stop_from_the_old_window_cannot_close_the_new_one(self):
        """旧窗口 150ms 后执行的收尾只关自己那一代，不许误杀刚重开的新窗口。"""
        factory = self._factory()

        with (patch.object(log_overlay.threading, "Thread", factory),
              patch.object(log_overlay, "stop") as stop_call):
            log_overlay.start()
            old_generation = log_overlay._GENERATION[0]
            log_overlay.mark_closing()
            log_overlay.start()                 # 重开成新一代
            stop_call.reset_mock()

            log_overlay._stop_generation(old_generation)

        stop_call.assert_not_called()
        self.assertEqual(old_generation + 1, log_overlay._GENERATION[0])

    def test_stop_generation_still_stops_its_own_window(self):
        with patch.object(log_overlay, "stop") as stop_call:
            log_overlay._stop_generation(log_overlay._GENERATION[0])

        stop_call.assert_called_once_with()


class DeferredOpenTests(OverlayStateTestCase):
    """排队重开：老线程一结束就自动开窗；一直不结束就如实报错。"""

    def test_deferred_open_opens_when_the_old_thread_is_gone(self):
        factory = _ThreadFactory(on_exit=_release_started)
        callback = lambda: None                    # noqa: E731

        with patch.object(log_overlay.threading, "Thread", factory):
            log_overlay._OPEN_PENDING.set()
            log_overlay._deferred_open_worker({"on_halt": callback},
                                              timeout=0.5, interval=0.01)

        self.assertFalse(log_overlay._OPEN_PENDING.is_set())
        self.assertEqual(1, len(factory.created))
        self.assertTrue(log_overlay._STARTED[0])
        self.assertIs(callback, log_overlay._ON_HALT)

    def test_deferred_open_reports_when_the_old_thread_never_finishes(self):
        messages = []
        log_overlay._STARTED[0] = True             # 老线程永远不结束

        log_overlay.set_logger(messages.append)
        try:
            log_overlay._OPEN_PENDING.set()
            log_overlay._deferred_open_worker({}, timeout=0.15, interval=0.01)
        finally:
            log_overlay.set_logger(self._reporter)

        self.assertFalse(log_overlay._OPEN_PENDING.is_set())
        self.assertTrue(any("还没结束" in msg for msg in messages), messages)


class ReportTests(OverlayStateTestCase):
    """浮窗内部异常要能被上层日志看到（否则用户只看到"点了没反应"）。"""

    def test_report_reaches_the_injected_logger(self):
        messages = []

        log_overlay.set_logger(messages.append)
        try:
            log_overlay._report("窗口没能打开")
        finally:
            log_overlay.set_logger(self._reporter)

        self.assertEqual(["窗口没能打开"], messages)

    def test_report_survives_a_broken_logger(self):
        def boom(_text):
            raise RuntimeError("日志回调坏了")

        log_overlay.set_logger(boom)
        try:
            log_overlay._report("x")               # 不许抛出去
        finally:
            log_overlay.set_logger(self._reporter)


class IsRunningTests(OverlayStateTestCase):
    def test_running_means_started_and_not_stopping(self):
        log_overlay._STARTED[0] = True

        self.assertTrue(log_overlay.is_running())

        log_overlay._STOP.set()

        self.assertFalse(log_overlay.is_running())    # 退出中 = 可以立刻重开

    def test_closing_window_is_not_running(self):
        """「退出浮窗」按下就立刻算没在跑（那 150ms 延时不能骗过网页）。"""
        log_overlay._STARTED[0] = True

        log_overlay.mark_closing()

        self.assertFalse(log_overlay.is_running())

    def test_stop_marks_the_window_as_closing(self):
        log_overlay._STARTED[0] = True

        # 别让 stop() 顺手去动真正注册过的校准窗口回调。
        with patch.object(log_overlay, "_ON_CALIBRATE_CLOSE", None):
            log_overlay.stop()

        self.assertTrue(log_overlay._CLOSING.is_set())
        self.assertFalse(log_overlay.is_running())

    def test_stopped_means_not_running(self):
        log_overlay._STARTED[0] = False

        self.assertFalse(log_overlay.is_running())


if __name__ == "__main__":
    unittest.main()
