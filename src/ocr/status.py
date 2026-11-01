# -*- coding: utf-8 -*-
"""最近一次 OCR 扫描结果：浮窗「OCR 扫描」框与排障用的旁路数据。

用户 2026-10-31 反馈「老是检测不到结束回合」，可日志里只有
``[OCR] 图 572x364 行 2 置信 0.91 (turn_end)`` —— 看不到到底读到了什么字，
只能靠猜。这里把每次 OCR 的输入尺寸、识别文本和置信度记下来，浮窗
（log_overlay 的「OCR 扫描」框）按区域显示最新一次，用户和 agent 直接肉眼
可查；诊断日志里也会带上「结束回合」框读到的原文。

分层（**legacy 侧**，不进 src/hslegend）
------------------------------------------------------------------
生产方是 legacy 的 OCR 适配器（``src/ocr/paddle_adapter``），消费方是 root
层的日志浮窗（经 ``web_ui`` 注入的回调读取）。它不参与对局决策、也不是应用
端口，只是给排障用的旁路数据：新分层包不 import 本模块，本模块也不 import
hslegend，依赖方向保持 legacy → legacy，不新增跨层边。
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict

# 同时记住多少个区域（浮窗只显示最近扫到的几个，多留几个方便切换看）。
MAX_TAGS = 8
# 单行识别文本的保留长度：OCR 偶尔把整块面板读成一行长文，别让它撑爆浮窗。
# 浮窗会按原文完整显示（自动换行），这里只是安全上限，不是显示截断。
MAX_TEXT = 200

_LOCK = threading.Lock()
# tag → 最新一次记录；OrderedDict 按最近更新排序（超出 MAX_TAGS 淘汰最旧的）。
_ENTRIES: "OrderedDict[str, dict]" = OrderedDict()


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    """把一段 OCR 文本压成单行并截断（换行/多空格折成一个空格）。"""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def record(tag, size, lines, elapsed_ms=None, at=None) -> dict:
    """记下一次 OCR 扫描，返回落库的记录（同区域同内容只累加次数）。

    参数
    ----
    tag: 区域/预处理名（``turn_end`` / ``start`` / ``scaled_color_v1`` …）。
    size: 送给 OCR 的图片尺寸 ``(宽, 高)``。
    lines: ``[(文本, 置信度), …]``；也接受带 ``text``/``confidence`` 的对象。
    elapsed_ms: 本次识别耗时（毫秒，仅显示用）。
    at: 记录时间戳（默认取当前时间，测试可注入）。
    """
    tag = str(tag or "ocr")
    try:
        width, height = (int(size[0]), int(size[1]))
    except (TypeError, IndexError, ValueError):
        width, height = 0, 0
    pairs = []
    for item in lines or ():
        try:
            text, confidence = item
        except (TypeError, ValueError):
            text = getattr(item, "text", "")
            confidence = getattr(item, "confidence", 0.0)
        pairs.append((_clip(text), float(confidence or 0.0)))
    texts = [text for text, _confidence in pairs if text]
    entry = {
        "tag": tag,
        "width": width,
        "height": height,
        "line_count": len(pairs),
        "confidence": min((conf for _text, conf in pairs), default=0.0),
        "text": " | ".join(texts),
        "lines": tuple(pairs),
        "ms": int(elapsed_ms or 0),
        "at": time.time() if at is None else float(at),
        "count": 1,
    }
    with _LOCK:
        previous = _ENTRIES.get(tag)
        if previous is not None and previous.get("text") == entry["text"]:
            # 同一区域连续读到同一段文字（我方回合每 2s 复查一次）：累加次数，
            # 浮窗显示 ×N，一眼看出“一直读到的都是这个”。
            entry["count"] = int(previous.get("count") or 1) + 1
        _ENTRIES[tag] = entry
        _ENTRIES.move_to_end(tag)
        while len(_ENTRIES) > MAX_TAGS:
            _ENTRIES.popitem(last=False)
    return dict(entry)


def snapshot() -> list:
    """各区域最近一次记录（最近更新的在前），浅拷贝，调用方可放心读。"""
    with _LOCK:
        return [dict(entry) for entry in reversed(_ENTRIES.values())]


def clear() -> None:
    """清空记录（开新一局/测试隔离用）。"""
    with _LOCK:
        _ENTRIES.clear()
