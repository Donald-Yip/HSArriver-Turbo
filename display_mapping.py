# -*- coding: utf-8 -*-
"""炉石「客户区坐标 ↔ 屏幕坐标」换算的唯一来源。

背景（为什么要这个模块）
------------------------------------------------------------------
脚本里所有坐标（`click.py` 的点击点、`get_screen.py` 的像素判定点、
`FSM_action.py` 的 OCR 区域、`screen_regions.py` 的区域框）都是按
**客户区 1920×1080** 量出来的，历史上炉石以全屏 1920×1080 运行、客户区
正好落在屏幕原点 (0,0)，于是"客户区坐标"与"屏幕坐标"数值相同，代码里
就直接混用，没有换取算。

一旦炉石窗口不在屏幕原点（例如 4K 桌面上窗口放在 (960,551)），所有坐标
就整体偏掉。本模块把这条换算显式化：

    origin  = ClientToScreen(hwnd, (0, 0))        # 客户区左上角的屏幕坐标
    scale_x = 客户区宽 / 1920 ,  scale_y = 客户区高 / 1080
    屏幕坐标 = origin + 客户区坐标 × (scale_x, scale_y)

客户区 = 1920×1080 且 origin = (0,0) 时换算为**恒等**，行为与改造前逐像素
相同（零回归）。

坐标口径（全项目统一，改代码时请遵守）
------------------------------------------------------------------
* **客户区相对坐标**：基准 = `REFERENCE_CLIENT`(1920×1080) 的炉石客户区。
  各模块里的硬编码常量、ui_config.json 里的 ROI，都是这个口径。
  *（注：ui_config.json 里的 ROI 是用户在屏幕上拖出来的，只在"校准时窗口
  位于原点"的前提下才与客户区坐标数值相同；这是历史语义，本模块不改格式，
  但 `from_screen()`/`box_from_screen()` 提供了显式换算供校准与调试使用。）*
* **屏幕坐标**：真正交给 `ImageGrab.grab(bbox=)`、`pynput`、
  `ClientToScreen`、`WindowFromPoint` 的坐标。
* 两者只在**出口**互换：点击出口（`click.left_click/right_click`）与截图
  出口（`get_screen.catch_screen` / `FSM_action._grab_region` /
  `screen_regions` 的预览与叠加层）。

设计约束
------------------------------------------------------------------
* 本模块**不 import win32**：`read_client_rect` / `client_to_screen` 内部惰性
  import，且支持注入假实现，因此换算数学可以用纯 Python 单测覆盖。
* 拿不到炉石窗口（没开 / 最小化 / 非 Windows）时，`display_mapping()` 返回
  `None`，所有换算函数**原样返回**，调用方行为与改造前一致。
* 换算按实际客户区比例做，客户区非 16:9 时 `scale_x != scale_y` 会把画面
  拉变形，所以用 `compatible()` / `too_small()` 让自检与调用方能够拒绝。

DPI 感知（**必须**，见 `dpi_aware()`）
------------------------------------------------------------------
Windows 对"未声明 DPI 感知"的进程会**虚拟化**窗口几何读数：系统缩放 150%
时，同一个 `GetClientRect` 会返回 `1280×720`（虚拟化）而不是真实的
`1920×1080`（物理像素）。而 `BitBlt` / `ImageGrab` 拿的是**物理像素**，
于是"读到的客户区尺寸"与"抓到的图"对不上 —— 裁剪框整体错位，模板匹配
必然失败，表现是"屏幕上明明有字，脚本却认不出界面"。

本机实测（4K 桌面 + 150% 缩放）：

| DPI 感知 | GetClientRect | ClientToScreen | 抓到的图 |
| --- | --- | --- | --- |
| 未声明 | 1280×720（错） | (455, 497)（错） | 1280×720，混入其它窗口内容 |
| 已声明 | 1920×1080（对） | (683, 746)（对） | 1920×1080，干净的炉石画面 |

所以**每个进程入口都必须先调 `dpi_aware()`**（`hsctl.py` / `web_ui.py` /
`calibrate_roi.py`）。它幂等、失败不抛异常，非 Windows 上是空操作。
"""
from __future__ import annotations

import ctypes
import math
import sys
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

Box = Tuple[int, int, int, int]
Point = Tuple[int, int]

# 基准客户区：所有硬编码坐标的口径。不要改（改了等于把所有实测值作废）。
REFERENCE_CLIENT = (1920, 1080)

# 客户区宽高比与基准（16:9）的允许偏差；超过就认为等比换算会明显拉伸。
ASPECT_TOLERANCE = 0.08

# 客户区小于基准的这个比例时拒绝运行：盒子面板是固定像素的独立窗口，
# 炉石窗口太小会把它挤出屏幕，OCR 区域也没法同时装下面板和游戏 UI。
MIN_CLIENT_SCALE = 0.5

# 换算结果缓存时长（秒）：自动化循环里每帧都要换算，别每次都问 Win32。
#
# **事件驱动优先**：window_tracker 订阅了窗口移动/缩放事件，缓存一旦"动过"就
# 立刻失效（见 display_mapping），所以有事件能力时这个 TTL 只是最后一道保险；
# 拿不到事件能力时（非 Windows / hook 失败）才真正按这个间隔轮询，此时收紧到
# 窗口跟踪器给出的兜底间隔，保证"窗口一动最多 80ms 就跟着走"。
_MAPPING_TTL_SECONDS = 0.25
_MAPPING_TTL_FALLBACK_SECONDS = 0.08

_MAPPING_CACHE: dict = {"at": 0.0, "mapping": None, "hwnd": 0}

# 抓图原子性统计（给自检 / 浮窗显示"实时跟踪在不在生效"）。
_CAPTURE_STATS: dict = {"frames": 0, "retries": 0, "stale": 0}


def _tracker():
    """窗口移动跟踪器（拿不到就返回 None，调用方退回纯 TTL 缓存）。"""
    try:
        import window_tracker
        return window_tracker
    except Exception:
        return None


def _cache_invalidated(now: float, *, use_tracker: bool = True) -> bool:
    """缓存是否应当视作失效（窗口动过 / 超时）。

    * 有事件能力：``moved_since(缓存时刻)`` 为真 → 立即失效（毫秒级跟随），
      否则退到 TTL（防止"钩子在但收不到事件"把几何永久冻结）；
    * 没有事件能力：按 `_MAPPING_TTL_FALLBACK_SECONDS` 轮询兜底。

    `use_tracker=False`（注入了假 reader，即测试环境）时完全不碰真实窗口，
    只用 TTL —— 保证换算数学的单测不受本机窗口状态影响。
    """
    cached = _MAPPING_CACHE["mapping"]
    if cached is None:
        return True
    at = float(_MAPPING_CACHE["at"])
    tracker = _tracker() if use_tracker else None
    if tracker is not None:
        try:
            if tracker.available():
                # 事件驱动：窗口一动就立刻失效（毫秒级跟随）。
                # **仍然保留 TTL 兜底**：钩子可能"挂上了但收不到事件"（权限、
                # 跨进程、时钟口径不一致都算），只信事件会让几何永远冻结在
                # 第一次采样上——这正是"拖动窗口时框不跟随"的根因。有 TTL 在，
                # 最坏情况也只是滞后 _MAPPING_TTL_SECONDS。
                if tracker.moved_since(at):
                    return True
                return (now - at) > _MAPPING_TTL_SECONDS
            # 无事件能力：轮询一次真实几何，既能发现移动也让计数可见
            tracker.poll()
            if tracker.moved_since(at):
                return True
            return (now - at) > _MAPPING_TTL_FALLBACK_SECONDS
        except Exception:
            pass
    return (now - at) > _MAPPING_TTL_SECONDS


def tracker_stats() -> dict:
    """窗口跟踪 + 抓图原子性的统计（自检/浮窗用；拿不到跟踪器时给退化信息）。

    顺便把跟踪器"武装"起来：自检 / 区域预览 / 浮窗都可能先于自动化主循环
    调用它，早一点挂上事件钩子，用户看到的才是真实状态。

    挂钩是**异步**的（`ensure_started()` 返回时线程可能还没跑到 SetWinEventHook），
    这里等它启动完再报告状态 —— 否则第一次显示必然是"轮询兜底"，用户会以为
    事件跟踪没生效。等待只发生在"正在启动"的那一瞬间（见 wait_ready）。
    """
    tracker = _tracker()
    if tracker is not None:
        try:
            tracker.ensure_started()
        except Exception:
            pass
        try:
            tracker.wait_ready()
        except Exception:
            pass
    try:
        info = tracker.stats() if tracker is not None else {}
    except Exception:
        info = {}
    result = {"available": bool(info.get("available")),
              "events": int(info.get("events") or 0),
              "polls": int(info.get("polls") or 0),
              "last_move_at": float(info.get("last_move_at") or 0.0)}
    result.update({k: int(v) for k, v in _CAPTURE_STATS.items()})
    result["text"] = (
        f"窗口跟踪：事件驱动（已捕获 {result['events']} 次）" if result["available"]
        else f"窗口跟踪：轮询兜底（已轮询 {result['polls']} 次）")
    return result


def reset_capture_stats() -> None:
    """清空抓图统计（测试用）。"""
    for key in _CAPTURE_STATS:
        _CAPTURE_STATS[key] = 0


def capture_region(hwnd: Optional[int] = None, *,
                   grabber: Optional[Callable] = None,
                   region=None,
                   region_is_client: bool = False,
                   retries: int = 2,
                   mapping_reader: Optional[Callable] = None) -> tuple:
    """**原子**抓取画面：返回 ``(frame, mapping)``。

    单帧 `ImageGrab` 约 100ms，这期间窗口若被拖动，抓到的画面与几何就对不上
    （"半旧半新"）。做法：

    1. 取一次新鲜 mapping（事件驱动缓存失效 → 基本就是当前几何）；
    2. 决定抓图框：默认抓**整个客户区**；给了 ``region`` 就抓那块
       （``region_is_client=True`` 表示 region 是客户区相对坐标，否则按屏幕坐标）；
    3. 抓图后复测：窗口又动过 → 这一帧作废重抓，最多 ``retries`` 次；
    4. 仍不稳定就用**最新**几何再抓一帧并返回（帧与几何一致即可用），
       同时把 ``stale`` 计数加一，便于现场发现"窗口在被反复拖动"。

    ``grabber`` 由调用方注入（例如 PIL 或 win32ui 版本），本模块保持不 import
    win32；``grabber(region)`` 需返回图像或 None。frame 为 ``None`` 表示抓图
    失败（调用方按现有逻辑兜底）。
    """
    from_reader = mapping_reader or display_mapping
    tracker = _tracker()

    def moved_since(ts: float) -> bool:
        try:
            return bool(tracker is not None and tracker.moved_since(ts))
        except Exception:
            return False

    def resolve_box(mapping):
        if region is None:
            return mapping.client_box
        if region_is_client:
            return box_to_screen(region, mapping)
        return tuple(int(v) for v in region)

    def grab_with_mapping(mapping):
        box = resolve_box(mapping)
        return grabber(box) if grabber is not None else None

    attempt = 0
    while True:
        mapping = from_reader(hwnd)
        if mapping is None:
            _CAPTURE_STATS["frames"] += 1
            return (None, None)
        started = time.time()
        frame = grab_with_mapping(mapping)
        _CAPTURE_STATS["frames"] += 1
        if frame is None:
            return (None, mapping)
        if not moved_since(started):
            return (frame, mapping)
        if attempt >= max(0, int(retries)):
            # 重试耗尽：用最新几何再抓一帧，保证返回的帧与几何自洽。
            _CAPTURE_STATS["stale"] += 1
            fresh = from_reader(hwnd, force=True) if _accepts_force(from_reader) \
                else from_reader(hwnd)
            if fresh is None:
                return (frame, mapping)
            again = grab_with_mapping(fresh)
            _CAPTURE_STATS["frames"] += 1
            return (again if again is not None else frame, fresh)
        _CAPTURE_STATS["retries"] += 1
        attempt += 1


def _accepts_force(reader: Callable) -> bool:
    """注入的 reader 是否支持 force 参数（真实 display_mapping 支持）。"""
    try:
        import inspect
        return "force" in inspect.signature(reader).parameters
    except Exception:
        return False


# ---------------------------------------------------------------- 数据结构
@dataclass(frozen=True)
class DisplayMapping:
    """一次采样得到的客户区几何（客户区相对坐标 ↔ 屏幕坐标）。"""

    hwnd: int
    client: Box                 # GetClientRect 原始值 (left, top, right, bottom)
    screen_left: int            # ClientToScreen(0,0).x
    screen_top: int             # ClientToScreen(0,0).y
    reference: Point = REFERENCE_CLIENT

    # -------------------------------------------------- 基本属性
    @property
    def width(self) -> int:
        return max(0, int(self.client[2]) - int(self.client[0]))

    @property
    def height(self) -> int:
        return max(0, int(self.client[3]) - int(self.client[1]))

    @property
    def scale_x(self) -> float:
        return self.width / float(self.reference[0] or 1)

    @property
    def scale_y(self) -> float:
        return self.height / float(self.reference[1] or 1)

    @property
    def origin(self) -> Point:
        return (int(self.screen_left), int(self.screen_top))

    @property
    def client_box(self) -> Box:
        """客户区在屏幕上的矩形（屏幕坐标）。"""
        return (int(self.screen_left), int(self.screen_top),
                int(self.screen_left) + self.width,
                int(self.screen_top) + self.height)

    # -------------------------------------------------- 坐标换算
    def to_screen(self, x: float, y: float) -> Point:
        """客户区相对坐标 → 屏幕坐标。"""
        return (int(self.screen_left) + int(round(float(x) * self.scale_x)),
                int(self.screen_top) + int(round(float(y) * self.scale_y)))

    def from_screen(self, x: float, y: float) -> Point:
        """屏幕坐标 → 客户区相对坐标（校准/调试用）。"""
        sx, sy = self.scale_x or 1.0, self.scale_y or 1.0
        return (int(round((float(x) - self.screen_left) / sx)),
                int(round((float(y) - self.screen_top) / sy)))

    def box_to_screen(self, box, *, clamp: bool = True) -> Box:
        """客户区相对矩形 → 屏幕矩形。

        左/上向下取整、右/下向上进一：缩放后**框只会变大不会缩水**（用
        round() 时小于 1 像素的框会被压成空框，OCR 就什么都读不到）。
        """
        left = int(math.floor(float(box[0]) * self.scale_x)) + int(self.screen_left)
        top = int(math.floor(float(box[1]) * self.scale_y)) + int(self.screen_top)
        right = int(math.ceil(float(box[2]) * self.scale_x)) + int(self.screen_left)
        bottom = int(math.ceil(float(box[3]) * self.scale_y)) + int(self.screen_top)
        out = _normalize_box((left, top, right, bottom))
        return clamp_to_desktop(out) if clamp else out

    def box_from_screen(self, box, *, clamp: bool = True) -> Box:
        """屏幕矩形 → 客户区相对矩形（与 box_to_screen 用同一套 floor/ceil）。"""
        sx, sy = self.scale_x or 1.0, self.scale_y or 1.0
        left = int(math.floor((float(box[0]) - self.screen_left) / sx))
        top = int(math.floor((float(box[1]) - self.screen_top) / sy))
        right = int(math.ceil((float(box[2]) - self.screen_left) / sx))
        bottom = int(math.ceil((float(box[3]) - self.screen_top) / sy))
        out = _normalize_box((left, top, right, bottom))
        if not clamp:
            return out
        return tuple(max(0, v) for v in out)  # 客户区坐标不会为负

    # -------------------------------------------------- 可用性判定
    def aspect(self) -> float:
        if self.height <= 0:
            return 0.0
        return self.width / float(self.height)

    def compatible(self) -> bool:
        """客户区是否为可等比换算的 16:9（含容差）。"""
        reference_aspect = (self.reference[0] / float(self.reference[1] or 1)) or 1.0
        try:
            aspect = self.aspect()
        except ZeroDivisionError:
            return False
        if aspect <= 0:
            return False
        return abs(aspect - reference_aspect) <= ASPECT_TOLERANCE * reference_aspect

    def too_small(self) -> bool:
        """客户区是否小到装不下固定像素的盒子面板。"""
        return min(self.scale_x, self.scale_y) < MIN_CLIENT_SCALE

    def debug_text(self) -> str:
        """一行可读描述，给日志 / 自检 / 叠加层提示条用。"""
        return (f"客户区 {self.width}×{self.height} @屏幕"
                f"({int(self.screen_left)},{int(self.screen_top)}) "
                f"缩放{self.scale_x:.2f}×{self.scale_y:.2f}")


# ---------------------------------------------------------------- 内部工具
def _normalize_box(box) -> Box:
    """把 (l,t,r,b) 规整成 整数 + l<=r、t<=b。"""
    left, top, right, bottom = (int(round(float(v))) for v in box)
    if left > right:
        left, right = right, left
    if top > bottom:
        top, bottom = bottom, top
    return (left, top, right, bottom)


def desktop_bounds() -> Box:
    """桌面（多显示器时的虚拟桌面）矩形，屏幕坐标。"""
    bounds = None
    try:
        user32 = ctypes.windll.user32
        bounds = (int(user32.GetSystemMetrics(76)), int(user32.GetSystemMetrics(77)),
                  int(user32.GetSystemMetrics(76)) + int(user32.GetSystemMetrics(78)),
                  int(user32.GetSystemMetrics(77)) + int(user32.GetSystemMetrics(79)))
    except Exception:
        bounds = None
    if bounds is None or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        try:
            user32 = ctypes.windll.user32
            bounds = (0, 0, int(user32.GetSystemMetrics(0)),
                      int(user32.GetSystemMetrics(1)))
        except Exception:
            bounds = (0, 0, 1920, 1080)
    return _normalize_box(bounds)


def clamp_to_desktop(box, size: Optional[Box] = None) -> Box:
    """把屏幕矩形夹进桌面范围，避免 ImageGrab.grab(bbox=) 因越界抛异常。"""
    bounds = tuple(size) if size is not None else desktop_bounds()
    left, top, right, bottom = _normalize_box(box)
    left = min(max(left, bounds[0]), bounds[2])
    top = min(max(top, bounds[1]), bounds[3])
    right = min(max(right, bounds[0]), bounds[2])
    bottom = min(max(bottom, bounds[1]), bounds[3])
    return _normalize_box((left, top, right, bottom))


# ---------------------------------------------------------------- Win32 读取
def hwnd_of(hwnd: Optional[int] = None) -> int:
    """取炉石窗口句柄（默认查一次；拿不到返回 0）。"""
    if hwnd:
        return int(hwnd)
    try:
        from get_screen import get_HS_hwnd
        return int(get_HS_hwnd())
    except Exception:
        return 0


def read_client_rect(hwnd: int = 0) -> Optional[Box]:
    """读客户区矩形（客户区相对坐标，恒以 (0,0) 开头）。失败返回 None。"""
    handle = hwnd_of(hwnd)
    if not handle:
        return None
    try:
        import win32gui
        box = win32gui.GetClientRect(handle)
    except Exception:
        return None
    try:
        rect = tuple(int(v) for v in box)
    except Exception:
        return None
    if len(rect) != 4:
        return None
    return rect


def client_to_screen(hwnd: int, x: int, y: int) -> Optional[Point]:
    """客户区坐标 → 屏幕坐标（Windows 官方映射，本模块唯一认定的原点来源）。"""
    handle = hwnd_of(hwnd)
    if not handle:
        return None
    try:
        import win32gui
        point = win32gui.ClientToScreen(handle, (int(x), int(y)))
    except Exception:
        return None
    try:
        return (int(point[0]), int(point[1]))
    except Exception:
        return None


# ---------------------------------------------------------------- 采样与缓存
def display_mapping(hwnd: Optional[int] = None, *, force: bool = False,
                    reference: Optional[Point] = None,
                    rect_reader: Optional[Callable] = None,
                    origin_reader: Optional[Callable] = None,
                    clock: Callable[[], float] = time.time,
                    ) -> Optional[DisplayMapping]:
    """取一次客户区几何；拿不到（炉石没开/最小化/非 Windows）时返回 None。

    * `hwnd=None`（默认）时先看缓存，避免自动化循环里每帧都找窗口；缓存失效
      （**窗口移动事件** 或超时，见 `_cache_invalidated`）或没有缓存才去查句柄。
    * `rect_reader` / `origin_reader` 是给测试用的注入口，可传假实现验证换算。
    """
    now = clock()
    # 注入了假 reader（测试/离线换算）时不读缓存、也不调窗口跟踪器：这样换算
    # 数学的单测完全不受"本机是否正跑着炉石"影响。
    injected = rect_reader is not None or origin_reader is not None
    if injected:
        return _sample(handle=hwnd_of(hwnd), reference=reference,
                       rect_reader=rect_reader, origin_reader=origin_reader,
                       now=now, cache=False)
    if hwnd is None and not force and not _cache_invalidated(now):
        cached = _MAPPING_CACHE["mapping"]
        if (cached is not None
                and (reference is None
                     or tuple(cached.reference) == tuple(reference))):
            return cached

    handle = hwnd_of(hwnd)
    if not handle:
        return None
    if (not force and not _cache_invalidated(now)
            and _MAPPING_CACHE["mapping"] is not None
            and _MAPPING_CACHE["hwnd"] == handle):
        cached = _MAPPING_CACHE["mapping"]
        if reference is None or tuple(cached.reference) == tuple(reference):
            return cached
    return _sample(handle=handle, reference=reference,
                   rect_reader=rect_reader, origin_reader=origin_reader,
                   now=now, cache=True)


def _sample(*, handle: Optional[int], reference: Optional[Point],
            rect_reader: Optional[Callable], origin_reader: Optional[Callable],
            now: float, cache: bool) -> Optional[DisplayMapping]:
    """真正读窗口几何。``cache=False``（注入了假 reader）时不写缓存。"""
    if not handle:
        return None
    reader = rect_reader or read_client_rect
    try:
        client = reader(handle)
    except Exception:
        client = None
    if not client:
        return None
    try:
        left, top, right, bottom = (int(v) for v in client)
    except Exception:
        return None
    if right <= left or bottom <= top:
        # 最小化的窗口客户区是 0×0：视为不可用，调用方走"原样返回"分支。
        return None

    origin = None
    if origin_reader is not None:
        try:
            origin = origin_reader(handle, left, top)
        except Exception:
            origin = None
    else:
        # GetClientRect 的 (left, top) 恒为 (0,0)；(left, top) 一起映射保证
        # 换算出错时兜底正确。
        origin = client_to_screen(handle, left, top)
    if origin is None:
        return None

    mapping = DisplayMapping(
        hwnd=int(handle), client=(left, top, right, bottom),
        screen_left=int(origin[0]), screen_top=int(origin[1]),
        reference=tuple(reference) if reference is not None else REFERENCE_CLIENT)

    if cache:
        _MAPPING_CACHE["at"] = now
        _MAPPING_CACHE["mapping"] = mapping
        _MAPPING_CACHE["hwnd"] = int(handle)
    return mapping


def refresh_mapping(hwnd: Optional[int] = None, **kwargs) -> Optional[DisplayMapping]:
    """强制丢弃缓存后重新采样（窗口刚移动/缩放后调用）。"""
    return display_mapping(hwnd, force=True, **kwargs)


def clear_mapping_cache() -> None:
    """清空缓存（测试与"窗口状态未知"时使用）。"""
    _MAPPING_CACHE["at"] = 0.0
    _MAPPING_CACHE["mapping"] = None
    _MAPPING_CACHE["hwnd"] = 0


# ---------------------------------------------------------------- 便捷换算
def to_screen(x, y, mapping: Optional[DisplayMapping] = None) -> Point:
    """客户区相对坐标 → 屏幕坐标；mapping 为 None 时原样返回。"""
    if mapping is None:
        return (int(round(float(x))), int(round(float(y))))
    return mapping.to_screen(x, y)


def from_screen(x, y, mapping: Optional[DisplayMapping] = None) -> Point:
    """屏幕坐标 → 客户区相对坐标；mapping 为 None 时原样返回。"""
    if mapping is None:
        return (int(round(float(x))), int(round(float(y))))
    return mapping.from_screen(x, y)


def box_to_screen(box, mapping: Optional[DisplayMapping] = None, *,
                  clamp: bool = True) -> Box:
    """客户区相对矩形 → 屏幕矩形；mapping 为 None 时原样返回（不夹取）。"""
    if mapping is None:
        return _normalize_box(box)
    return mapping.box_to_screen(box, clamp=clamp)


def box_from_screen(box, mapping: Optional[DisplayMapping] = None, *,
                    clamp: bool = True) -> Box:
    """屏幕矩形 → 客户区相对矩形；mapping 为 None 时原样返回。"""
    if mapping is None:
        return _normalize_box(box)
    return mapping.box_from_screen(box, clamp=clamp)


# ---------------------------------------------------------------- ROI 口径解释
def roi_frame_is_credible(boxes, reference: Point = REFERENCE_CLIENT) -> bool:
    """一组自称"客户区相对"的框，是否真的可能是客户区相对坐标。

    客户区相对坐标的基准是 `REFERENCE_CLIENT`(1920×1080)：**不管炉石窗口多大**，
    客户区内部永远映射到这个范围（缩放由本模块负责），所以任何一个坐标超出
    ``[-REF.x, REF.x] × [-REF.y, REF.y]`` 的框都不可能是真的客户区相对坐标
    （允许负值：盒子面板是独立窗口，可以挂在客户区左/上边之外）。

    返回 False 基本只有一个来源：**读不到客户区几何（炉石最小化 / 没开炉石）时
    保存过配置** —— 那次换算是恒等的，存下来的其实是屏幕坐标，标记却写了
    client。不识别出来，炉石一恢复显示，所有框都会被整体偏移 + 缩放一次，
    表现为"框全跑到屏幕外，还没法拖"。
    """
    try:
        limit_x, limit_y = int(reference[0]), int(reference[1])
    except Exception:
        return True
    for box in boxes:
        try:
            left, top, right, bottom = (int(v) for v in box)
        except Exception:
            return False
        if not (-limit_x <= left and -limit_y <= top
                and right <= limit_x and bottom <= limit_y):
            return False
    return True


def interpret_roi_box(box, mapping, *, declared_client: bool = False,
                      default=None) -> Box:
    """把一个 ui_config.json 里的 ROI 框解释成**客户区相对坐标**（唯一权威口径）。

    规则（`calibrate_roi` 与 `config` 必须完全一致，所以放在这里共用）：

    1. 数值等于**代码默认值** → 客户区相对：代码里的硬编码坐标就是这个口径，
       用户没校准过这一项时配置里根本不会出现它；
    2. ``declared_client=True``（文件写了 ``roi_frame="client"`` 且整份配置通过
       `roi_frame_is_credible`）→ 客户区相对；
    3. 拿不到客户区几何（`mapping is None`）→ 原样返回（此刻也无从换算）；
    4. 框**完整落在当前客户区的屏幕矩形内** → 它更像"在炉石窗口里量出来的屏幕
       坐标"（老配置的原始语义），换算成客户区相对，从而开始跟着窗口走；
    5. 其余 → 只能是客户区相对（窗口偏到一边时，屏幕坐标不可能落进客户区里）。
    """
    box = _normalize_box(box)
    if default is not None and box == _normalize_box(default):
        return box
    if declared_client or mapping is None:
        return box
    desktop = mapping.client_box
    inside = (desktop[0] <= box[0] and desktop[1] <= box[1]
              and box[2] <= desktop[2] and box[3] <= desktop[3])
    if not inside:
        return box
    return mapping.box_from_screen(box, clamp=False)


def ui_scale() -> float:
    """Windows 显示缩放倍数（100% → 1.0）。叠加层/浮窗按它等比放大。"""
    try:
        dpi = int(ctypes.windll.user32.GetDpiForSystem())
    except Exception:
        return 1.0
    if dpi <= 0:
        return 1.0
    return max(1.0, round(dpi / 96.0, 2))


def system_dpi() -> int:
    """系统 DPI（100% → 96）。读不到返回 0。"""
    try:
        return int(ctypes.windll.user32.GetDpiForSystem())
    except Exception:
        return 0


# 进程级 DPI 感知只声明一次；重复调用是空操作（SetProcessDpiAwareness 第二次
# 会返回 E_ACCESSDENIED，属正常，不当错误）。
_DPI_AWARE_DONE = False


def dpi_aware() -> bool:
    """声明本进程为 **per-monitor DPI 感知**，让窗口几何读数返回物理像素。

    **为什么必须有**：未声明的进程，Windows 会把 `GetClientRect` /
    `ClientToScreen` 的结果按系统缩放**虚拟化**（150% 时 1920×1080 报成
    1280×720），而 `BitBlt`/`ImageGrab` 抓的是物理像素 —— 读到的几何与抓到的
    图对不上，裁剪框整体错位，界面识别必然失败。详见模块 docstring「DPI 感知」。

    选用 `SetProcessDpiAwareness(2)` = `PROCESS_PER_MONITOR_DPI_AWARE`：
    多显示器且各屏缩放不同时也正确。优先走 shcore（Win8.1+），
    退回 `SetProcessDPIAware`（Vista+），再退回什么都不做（非 Windows）。

    幂等、**绝不抛异常**：拿不到就安静降级，不因为一个辅助调用打断主流程。
    返回是否成功声明（用于自检显示，不用于控制流程）。
    """
    global _DPI_AWARE_DONE
    if _DPI_AWARE_DONE:
        return True
    if not sys.platform.startswith("win"):
        _DPI_AWARE_DONE = True
        return False
    ok = False
    try:
        # 0x2 = PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        ok = True
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
            ok = True
        except Exception:
            ok = False
    _DPI_AWARE_DONE = ok
    return ok


def mapping_info(mapping: Optional[DisplayMapping]) -> dict:
    """把映射整理成可 JSON 序列化的信息（自检 / 区域预览 / 网页展示用）。"""
    dpi = system_dpi()
    if mapping is None:
        return {"available": False, "hwnd": 0, "dpi": dpi,
                "reference": list(REFERENCE_CLIENT),
                "text": "客户区未知（炉石未运行或窗口不可用）"}
    return {
        "available": True,
        "hwnd": int(mapping.hwnd),
        "client": {"width": mapping.width, "height": mapping.height},
        "screen_origin": [int(mapping.screen_left), int(mapping.screen_top)],
        "client_screen_box": list(mapping.client_box),
        "scale": [round(mapping.scale_x, 6), round(mapping.scale_y, 6)],
        "reference": list(mapping.reference),
        "compatible": bool(mapping.compatible()),
        "too_small": bool(mapping.too_small()),
        "dpi": dpi,
        "ui_scale": ui_scale(),
        "text": mapping.debug_text(),
    }
