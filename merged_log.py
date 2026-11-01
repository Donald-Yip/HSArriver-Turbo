# -*- coding: utf-8 -*-
"""浮窗两个窗口的合并日志（识别轮播 + 日志流），导出时按时间排成一份文本。

背景（用户 2026-11-01）：浮窗上方新增「识别」轮播窗口（结束回合 / 段位 /
开始按钮），这些结果不再进下面的日志流；但导出给作者排查时，两边必须按时间
合在一起看，否则对不上「当时识别到什么 → 随后执行了什么」。

* 纯 stdlib，不 import Tk / 项目界面模块；
* 识别事件由 ``log_overlay`` 在轮播切换时调用 :func:`record_detect_rows` 记下；
* 日志流内容从 ``action_feed.export_rows()`` 取（块自带时间戳）；
* :func:`timeline_text` 把两边合并、按时间排序，输出 ``HH:MM:SS [标签] 正文``。
"""

from __future__ import annotations

import datetime
import threading
import time
from collections import deque

# 识别轮播事件上限：够复盘一整局，又不至于把内存吃掉。
MAX_DETECT_EVENTS = 2000
LOCK = threading.Lock()
_DETECT_EVENTS: deque = deque(maxlen=MAX_DETECT_EVENTS)


def clear() -> None:
    """清空识别事件（新的运行开始时调用）。"""
    with LOCK:
        _DETECT_EVENTS.clear()


def record_detect_rows(rows, at=None) -> None:
    """记录一轮识别轮播的内容（同一段文字连续重复只记一次）。"""
    stamp = time.time() if at is None else float(at)
    with LOCK:
        for text in rows or ():
            text = str(text or "").strip()
            if not text:
                continue
            if _DETECT_EVENTS and _DETECT_EVENTS[-1][1] == text:
                continue
            _DETECT_EVENTS.append((stamp, text))


def detect_events() -> list:
    """已记录的识别事件（按时间正序）。"""
    with LOCK:
        return list(_DETECT_EVENTS)


def _stamp(at) -> str:
    try:
        return datetime.datetime.fromtimestamp(float(at)).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "--:--:--"


def timeline_text() -> str:
    """两窗合并文本：``HH:MM:SS [识别] …`` 与 ``HH:MM:SS [OCR/执行/…] …`` 按时间排序。"""
    entries = []
    for at, text in detect_events():
        entries.append((float(at or 0.0), "识别", text))
    try:
        import action_feed
        for at, text in action_feed.export_rows():
            entries.append((float(at or 0.0), "", text))
    except Exception:
        pass
    entries.sort(key=lambda item: item[0])
    lines = ["# 浮窗合并日志（识别轮播 + 日志流，按时间排序）", ""]
    for at, kind, text in entries:
        if kind:
            lines.append(f"{_stamp(at)} 【{kind}】{text}")
        else:
            lines.append(f"{_stamp(at)} {text}")
    lines.append("")
    return "\n".join(lines)


__all__ = ["clear", "record_detect_rows", "detect_events", "timeline_text",
           "MAX_DETECT_EVENTS"]
