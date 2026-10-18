# -*- coding: utf-8 -*-
"""改炉石窗口**客户区尺寸**的唯一实现（DPI 感知、默认保持 16:9）。

谁在用：日志浮窗上的「窗口宽度」滑条（走 web_ui 回调）与
`tools/set_hs_window.py` 都调这里，避免各写一份差不多的 Win32 代码。

两个必须记住的坑（本模块已处理）：

* **DPI 感知**。没声明 DPI 感知的进程，Windows 会把 `GetClientRect` /
  `SetWindowPos` 的坐标按系统缩放虚拟化：150% 下你写 960×540，实际是
  1440×810。所以每次调用前都 `dpi_aware()`（幂等），全程用**物理像素**。
* **客户区 ≠ 窗口外框**。`SetWindowPos` 收的是外框尺寸，`AdjustWindowRectEx`
  才能把目标客户区加上标题栏/边框；改完复读 `GetClientRect` 复验。

实测尺寸边界（本机 4K + 150%，炉石窗口化）：游戏把客户区钳在**最小 400×400**，
但**项目支持的最小客户区是 960×540**（=基准 1920×1080 的一半）：再小固定像素的
盒子面板就挤不下了。所以滑条下限取 **960**（960×540），最大取屏幕宽 **3840**
（3840×2160）。宽度步进 16 → 高度正好是 9 的整数倍，比例精确 16:9。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Optional, Tuple

Size = Tuple[int, int]

# 项目口径的基准客户区（所有坐标都按它标定）。
REFERENCE: Size = (1920, 1080)
ASPECT: Size = (16, 9)

# 16:9 滑条的取值范围与步进。下限 960（=960×540，基准的一半）：再小盒子面板挤不下。
MIN_16_9_WIDTH = 960
MAX_16_9_WIDTH = 3840
STEP_16_9 = 16

# 炉石窗口的最小客户区（游戏自己钳的，低于它会被弹回）。
MIN_CLIENT_WIDTH = 400
MIN_CLIENT_HEIGHT = 400

GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_NOMOVE = 0x0002


def height_for_width(width: int) -> int:
    """16:9 下与给定宽度配对的高度（四舍五入到整数）。"""
    return int(round(int(width) * ASPECT[1] / float(ASPECT[0])))


def clamp_width(width: int) -> int:
    """把宽度收进滑条范围并对齐步进（保证 16:9 精确、不低于最小高度）。"""
    try:
        value = int(width)
    except Exception:
        return MIN_16_9_WIDTH
    value = max(MIN_16_9_WIDTH, min(MAX_16_9_WIDTH, value))
    steps = (value - MIN_16_9_WIDTH) // STEP_16_9
    return MIN_16_9_WIDTH + steps * STEP_16_9


def _hwnd(hwnd: Optional[int] = None) -> int:
    if hwnd:
        return int(hwnd)
    try:
        from get_screen import get_HS_hwnd

        return int(get_HS_hwnd() or 0)
    except Exception:
        return 0


def hearthstone_hwnd() -> int:
    """炉石窗口句柄（没开 / 找不到返回 0）。"""
    return _hwnd()


def _dpi_aware() -> None:
    try:
        from display_mapping import dpi_aware

        dpi_aware()
    except Exception:
        pass


def current_client_size(hwnd: Optional[int] = None) -> Optional[Size]:
    """当前客户区 (宽, 高)（物理像素）。找不到炉石窗口返回 None。"""
    _dpi_aware()
    handle = _hwnd(hwnd)
    if not handle:
        return None
    try:
        import win32gui

        left, top, right, bottom = win32gui.GetClientRect(handle)
        return int(right) - int(left), int(bottom) - int(top)
    except Exception:
        return None


def outer_size_for(hwnd: int, client: Size) -> Size:
    """目标客户区 → 需要设置的窗口外框尺寸（含标题栏/边框）。"""
    user32 = ctypes.windll.user32
    user32.GetWindowLongW.restype = ctypes.c_long
    style = user32.GetWindowLongW(ctypes.c_void_p(int(hwnd)), GWL_STYLE)
    ex_style = user32.GetWindowLongW(ctypes.c_void_p(int(hwnd)), -20)
    rect = wintypes.RECT(0, 0, int(client[0]), int(client[1]))
    user32.AdjustWindowRectEx(ctypes.byref(rect), style, False, ex_style)
    return int(rect.right - rect.left), int(rect.bottom - rect.top)


def has_thickframe(hwnd: int) -> bool:
    """窗口能不能被外部改尺寸（全屏/无边时没有 WS_THICKFRAME）。"""
    try:
        user32 = ctypes.windll.user32
        user32.GetWindowLongW.restype = ctypes.c_long
        style = user32.GetWindowLongW(ctypes.c_void_p(int(hwnd)), GWL_STYLE)
        return bool(int(style) & WS_THICKFRAME)
    except Exception:
        return True


def set_client_size(
    width: int,
    height: Optional[int] = None,
    *,
    hwnd: Optional[int] = None,
    move: Optional[Size] = None,
) -> Optional[Size]:
    """把客户区设成 ``(width, height)``；``height`` 缺省按 16:9 算。

    ``move`` 给了就同时把窗口外框左上角移到该屏幕坐标，否则保持原位。
    返回改后**实际**的客户区（游戏可能钳到最小值），找不到窗口返回 None。
    """
    _dpi_aware()
    handle = _hwnd(hwnd)
    if not handle:
        return None
    target = (
        int(width),
        int(height) if height is not None else height_for_width(width),
    )
    flags = SWP_NOZORDER | SWP_NOACTIVATE
    x = y = 0
    if move is None:
        flags |= SWP_NOMOVE
    else:
        x, y = int(move[0]), int(move[1])
    outer = outer_size_for(handle, target)
    try:
        import win32gui

        win32gui.SetWindowPos(handle, 0, x, y, outer[0], outer[1], flags)
        win32gui.UpdateWindow(handle)
    except Exception:
        return None
    return current_client_size(handle)


def set_client_width(width: int, *, hwnd: Optional[int] = None) -> Optional[Size]:
    """按 16:9 设宽度（浮窗滑条用的入口）；宽度会被 clamp_width 收进范围。"""
    return set_client_size(clamp_width(width), hwnd=hwnd)
