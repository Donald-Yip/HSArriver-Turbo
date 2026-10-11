# -*- coding: utf-8 -*-
"""炉石窗口「实时位置跟踪」：窗口一动，坐标换算缓存立刻失效。

为什么需要它
------------------------------------------------------------------
`display_mapping` 把"客户区相对坐标"换算成屏幕坐标，靠的是
`GetClientRect` + `ClientToScreen`。这套换算本身是准的，但**采样有延迟**：
之前只有 250ms 的 TTL 缓存，用户把炉石窗口拖到别处后，最多 250ms 内的截图与
点击还在用旧几何；而抓一帧要 ~100ms，抓图期间窗口移动还会产生"半旧半新"的帧。

本模块负责"**什么时候动了**"，做成事件驱动：

* 用 ``SetWinEventHook(EVENT_OBJECT_LOCATIONCHANGE)`` 订阅炉石窗口的移动/缩放，
  回调只更新一个时间戳（O(1)、不做任何 Win32 读操作，避免重入）；
* `display_mapping` 每次取几何时问一句 ``moved_since(缓存时间)``，为真就绕过
  TTL 立刻重读 → 滞后从 250ms 降到"一次事件"；
* 挂钩失败（非 Windows / 权限 / 异常）时 `available()` 返回 False，调用方改用
  短间隔 `poll()` 轮询兜底 —— 功能不消失，只是精度降级。

线程与生命周期
------------------------------------------------------------------
托盘/主线程不动，hook 在一条 daemon 线程里跑自己的消息循环（``GetMessageW``
+ ``DispatchMessageW``）；`ensure_started()` 幂等，进程退出时线程随 daemon 结束。
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable, Optional

# ---------------------------------------------------------------- Win32 常量
EVENT_SYSTEM_MOVESIZEEND = 0x000B
EVENT_OBJECT_LOCATIONCHANGE = 0x800B
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002
OBJID_WINDOW = 0

# hook 不可用时的轮询间隔：窗口移动后最多滞后这么久（毫秒级成本）。
FALLBACK_POLL_SECONDS = 0.08

# `GetMessageW` 的返回值：0 = WM_QUIT，-1 = 出错。
_WM_QUIT = 0
_ERROR = -1

_WIN_EVENT_PROC = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG,
    wintypes.LONG, wintypes.DWORD, wintypes.DWORD)


class _Win32:
    """惰性拿到的 user32 函数（非 Windows 环境下 import 本模块也不能炸）。"""

    def __init__(self) -> None:
        self.user32 = ctypes.windll.user32
        self.user32.SetWinEventHook.argtypes = [
            wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE,
            _WIN_EVENT_PROC, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
        self.user32.SetWinEventHook.restype = wintypes.HANDLE
        self.user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        self.user32.UnhookWinEvent.restype = wintypes.BOOL
        self.user32.GetMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                            wintypes.UINT, wintypes.UINT]
        self.user32.GetMessageW.restype = ctypes.c_int
        self.user32.TranslateMessage.argtypes = [ctypes.c_void_p]
        self.user32.DispatchMessageW.argtypes = [ctypes.c_void_p]
        self.user32.PostThreadMessageW.argtypes = [wintypes.DWORD,
                                                   wintypes.UINT,
                                                   wintypes.WPARAM,
                                                   wintypes.LPARAM]
        self.user32.PostThreadMessageW.restype = wintypes.BOOL
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                         ctypes.c_void_p]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD


def _thread_message():
    """`GetMessageW` 用的 MSG 结构（与 region_overlay.MSG 同构）。"""
    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                    ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
                    ("time", wintypes.DWORD), ("pt_x", wintypes.LONG),
                    ("pt_y", wintypes.LONG)]
    return MSG()


def _window_pid(hwnd: int) -> int:
    """窗口所属进程 id（拿不到返回 0 = 不过滤）。"""
    try:
        pid = wintypes.DWORD(0)
        ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd),
                                                      ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


# 默认时钟：**必须与 display_mapping 的默认时钟同源**（`time.time`）。
# `moved_since(t)` 是拿本模块记的时间戳与调用方给的时间戳直接比大小（不是求差），
# 口径不一致就会恒为 False（详见 WindowMoveTracker 的类文档）。
default_clock = time.time


class WindowMoveTracker:
    """炉石窗口移动/缩放的实时跟踪器（单实例够用，见模块级函数）。

    **时钟口径**：`moved_since(t)` 是拿本跟踪器记的时间戳与调用方给的时间戳
    **直接比大小**（不是求差），所以两边的时钟必须是同一个来源
    （`display_mapping` 用的是 `time.time`）。曾经这里默认 `time.monotonic`，
    而 Windows 的 monotonic 是开机秒数（约 1e5~1e6）——永远小于 `time.time()`
    的纪元秒（约 1.7e9），于是 `moved_since()` 恒为 False：钩子挂上了、事件也
    收到了，缓存却永远不失效，窗口拖动时框/点击全都不跟随。
    """

    def __init__(self,
                 hook_factory: Optional[Callable] = None,
                 unhook: Optional[Callable] = None,
                 hwnd_provider: Optional[Callable[[], int]] = None,
                 poll_reader: Optional[Callable[[int], Optional[tuple]]] = None,
                 clock: Callable[[], float] = default_clock,
                 ) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._thread_id = 0
        self._hook = None
        self._hwnd = 0
        self._moved_at = 0.0
        self._events = 0
        self._polls = 0
        self._last_geometry = None
        self._warned = False
        # 钩子线程是否还在启动中（挂钩是异步的：`ensure_started()` 返回时，
        # 线程可能还没跑到 SetWinEventHook）。只用于统计显示时短暂等待，
        # 避免把"还在启动"误报成"钩子不可用"。
        self._starting = False

        # 可注入（测试用假实现，避免真挂钩子 / 真读窗口）
        self._hook_factory = hook_factory
        self._unhook = unhook
        self._hwnd_provider = hwnd_provider
        self._poll_reader = poll_reader
        self._hook_proc = _WIN_EVENT_PROC(self._on_win_event)  # 全局引用防回收

    # -------------------------------------------------- 状态查询
    def available(self) -> bool:
        """事件钩子是否在跑（False = 调用方应改用 poll 兜底）。"""
        with self._lock:
            return self._hook is not None

    def starting(self) -> bool:
        """钩子线程是否仍在启动中（还没挂上、也还没失败）。"""
        with self._lock:
            return bool(self._starting)

    def wait_ready(self, timeout: float = 0.5) -> bool:
        """等钩子线程完成启动；返回钩子是否可用。

        只对**正在启动**的线程等待：钩子确定不可用（启动失败）时立刻返回，
        所以按帧调用（叠加层 / 区域预览）不会每帧都卡住 —— 只有启动那一瞬间
        （最多 timeout）会等一下。
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        while self.starting() and time.monotonic() < deadline:
            time.sleep(0.005)
        return self.available()

    def last_move_at(self) -> float:
        with self._lock:
            return self._moved_at

    def moved_since(self, timestamp: float) -> bool:
        """自 timestamp 之后窗口是否移动/缩放过。"""
        with self._lock:
            return self._moved_at > float(timestamp or 0.0)

    def stats(self) -> dict:
        with self._lock:
            return {"available": self._hook is not None,
                    "events": self._events,
                    "polls": self._polls,
                    "last_move_at": self._moved_at,
                    "hwnd": self._hwnd}

    # -------------------------------------------------- 事件回调
    def _on_win_event(self, hook, event, hwnd, obj_id, child_id,
                      thread_id, when) -> None:
        """WinEvent 回调：**只更新时间戳与计数**，绝不在这里读窗口（防重入）。"""
        with self._lock:
            self._hwnd = self._hwnd or int(hwnd or 0)
            self._moved_at = self._clock()
            self._events += 1

    def note_move(self, when: Optional[float] = None) -> None:
        """手动标记"动过了"（给 poll 兜底与测试用）。"""
        with self._lock:
            self._moved_at = self._clock() if when is None else float(when)
            self._events += 1

    # -------------------------------------------------- 轮询兜底
    def poll(self, hwnd: Optional[int] = None) -> bool:
        """主动读一次窗口几何，发现变化就标记（hook 不可用时用）。

        成本是两次 Win32 调用（微秒级），可以放心按帧调用。
        """
        handle = int(hwnd or 0) or self._target_hwnd()
        if not handle:
            return False
        reader = self._poll_reader or self._default_poll_reader(handle)
        with self._lock:
            self._polls += 1
        try:
            geometry = reader(handle)
        except Exception:
            return False
        with self._lock:
            changed = (geometry is not None
                       and self._last_geometry is not None
                       and geometry != self._last_geometry)
            self._last_geometry = geometry
            if changed:
                self._moved_at = self._clock()
                self._events += 1
            return bool(changed)

    @staticmethod
    def _default_poll_reader(handle: int) -> Callable[[int], Optional[tuple]]:
        def read(hwnd: int) -> Optional[tuple]:
            try:
                import win32gui
                rect = win32gui.GetClientRect(hwnd)
                origin = win32gui.ClientToScreen(hwnd, (rect[0], rect[1]))
                return (int(rect[2]) - int(rect[0]), int(rect[3]) - int(rect[1]),
                        int(origin[0]), int(origin[1]))
            except Exception:
                return None
        return read

    # -------------------------------------------------- 生命周期
    def _target_hwnd(self) -> int:
        if self._hwnd_provider is not None:
            try:
                return int(self._hwnd_provider() or 0)
            except Exception:
                return 0
        try:
            from get_screen import get_HS_hwnd
            return int(get_HS_hwnd())
        except Exception:
            return 0

    def ensure_started(self, hwnd: Optional[int] = None) -> bool:
        """幂等启动事件钩子；失败返回 False（调用方退化为 poll）。

        句柄变化（炉石重启）时会重新挂钩。
        """
        handle = int(hwnd or 0) or self._target_hwnd()
        if not handle:
            return False
        with self._lock:
            if self._hook is not None and self._hwnd == handle:
                return True
            self._hwnd = handle
        if self._thread is not None and self._thread.is_alive():
            self._rehook(handle)
            return self.available()
        self._stop.clear()
        self._starting = True
        self._thread = threading.Thread(target=self._run, args=(handle,),
                                        name="hs-window-tracker", daemon=True)
        self._thread.start()
        return True

    def _rehook(self, handle: int) -> None:
        """句柄变了：让 hook 线程重新挂一次（通过重启线程实现，简单可靠）。"""
        thread_id = self._thread_id
        try:
            self._stop.set()
            if thread_id:
                ctypes.windll.user32.PostThreadMessageW(
                    wintypes.DWORD(thread_id), 0x0012, 0, 0)  # WM_QUIT
        except Exception:
            pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout=0.5)
        self._stop.clear()
        self._starting = True
        self._thread = threading.Thread(target=self._run, args=(handle,),
                                        name="hs-window-tracker", daemon=True)
        self._thread.start()

    def _run(self, handle: int) -> None:
        try:
            self._thread_id = int(ctypes.windll.kernel32.GetCurrentThreadId())
        except Exception:
            self._thread_id = 0
        hook = None
        try:
            factory = self._hook_factory or self._default_hook_factory
            hook = factory(handle, self._hook_proc)
        except Exception:
            hook = None
        # 启动结束（成功或失败都要落到 False，否则统计显示会一直"等"）。
        with self._lock:
            self._starting = False
            self._hook = hook or None
        if not hook:
            self._warn()
            return
        msg = _thread_message()
        try:
            while not self._stop.is_set():
                got = ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None,
                                                       0, 0)
                if got == _ERROR or got == _WM_QUIT:
                    break
                ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            try:
                (self._unhook or ctypes.windll.user32.UnhookWinEvent)(hook)
            except Exception:
                pass
            with self._lock:
                self._hook = None
            self._thread_id = 0

    def _default_hook_factory(self, handle: int, proc) -> Optional[int]:
        pid = _window_pid(handle)
        user32 = ctypes.windll.user32
        # EVENT_OBJECT_LOCATIONCHANGE：窗口移动/缩放都会来
        # EVENT_SYSTEM_MOVESIZEEND  ：拖动结束（有些环境只在结束时给）
        for event in (EVENT_OBJECT_LOCATIONCHANGE, EVENT_SYSTEM_MOVESIZEEND):
            hook = None
            try:
                if pid:
                    hook = user32.SetWinEventHook(event, event, None, proc,
                                                  pid, 0, WINEVENT_OUTOFCONTEXT)
                if not hook:
                    hook = user32.SetWinEventHook(event, event, None, proc, 0, 0,
                                                  WINEVENT_OUTOFCONTEXT)
            except Exception:
                hook = None
            if hook:
                return hook
        return None

    def _warn(self) -> None:
        with self._lock:
            if self._warned:
                return
            self._warned = True
        print("[跟踪] 窗口移动事件钩子不可用，改用轮询兜底"
              f"（{FALLBACK_POLL_SECONDS * 1000:.0f}ms）")

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self._starting = False      # 别让 wait_ready 白等
        thread_id = self._thread_id
        try:
            if thread_id:
                ctypes.windll.user32.PostThreadMessageW(
                    wintypes.DWORD(thread_id), 0x0012, 0, 0)
        except Exception:
            pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.5)
        self._thread = None
        with self._lock:
            self._hook = None

    def reset(self) -> None:
        """清空时间戳/计数（测试用）。"""
        with self._lock:
            self._moved_at = 0.0
            self._events = 0
            self._polls = 0
            self._last_geometry = None


# ---------------------------------------------------------------- 模块级单例
_DEFAULT = WindowMoveTracker()


def default_tracker() -> WindowMoveTracker:
    return _DEFAULT


def ensure_started(hwnd: Optional[int] = None) -> bool:
    return _DEFAULT.ensure_started(hwnd)


def available() -> bool:
    return _DEFAULT.available()


def starting() -> bool:
    return _DEFAULT.starting()


def wait_ready(timeout: float = 0.5) -> bool:
    return _DEFAULT.wait_ready(timeout)


def moved_since(timestamp: float) -> bool:
    return _DEFAULT.moved_since(timestamp)


def last_move_at() -> float:
    return _DEFAULT.last_move_at()


def poll(hwnd: Optional[int] = None) -> bool:
    return _DEFAULT.poll(hwnd)


def stats() -> dict:
    return _DEFAULT.stats()


def stop() -> None:
    _DEFAULT.stop()
