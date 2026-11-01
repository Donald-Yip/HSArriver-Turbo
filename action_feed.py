# -*- coding: utf-8 -*-
"""浮窗分块流：把日志行归成「一步动作一块」的块流（纯 stdlib，可单测）。

用户 2026-10-31 要求把浮窗原来的「日志正文」和「OCR 扫描」两个框合并成一个：
每块 = 一步动作，块内顺序固定为 **【OCR 内容 → 执行内容】**，新块置顶，
块间有分隔线。归块规则全在这个模块里（不依赖 Tk / 项目其它模块），
``log_overlay`` 只负责把 :func:`render` 的结果画出来。

块结构::

    {"at": 时间戳, "phase": "我方出牌"/"对手回合"/"换牌"/"对局结束"/"",
     "ocr": [(text, tag)], "exec": [(text, tag)]}

``tag`` 直接是 Text 控件的颜色 tag 名（``head``/``match``/``warn``/``text``/
``dim``/``exec``/``alert``），由 ``log_overlay`` 配好颜色。

一条动作的典型日志（同一块，先 OCR 后执行）::

    [推荐] 打出2号位法术
    [推荐] 原文：PlayCard ...
    灵魂之匣
    [执行] 打出2号位法术「灵魂之匣」→ 我方1号位
    [执行] 结果：点击完成
"""

from __future__ import annotations

import datetime
import re
import threading
import time
from collections import deque

# 块流上限：保留最近多少块动作（够回看，又不至于把内存吃掉）。
MAX_BLOCKS = 60
# 每块 OCR / 执行段各最多保留多少行（超出丢弃并在段末补「…还有 N 行」）。
MAX_OCR_LINES = 12
MAX_EXEC_LINES = 12

# 渲染前缀：OCR 段首行 / 执行段首行 / 两段的续行缩进（列宽都是 8）。
SEPARATOR = "─" * 46
OCR_PREFIX = "  OCR   "
EXEC_PREFIX = "  执行  "
CONT_PREFIX = " " * 8
OVERFLOW_TEMPLATE = "…（还有 {count} 行）"
# R19 卡片式：展开后最多列多少行原文，其余折成「＋N 行」。
MAX_EXPANDED_RAW_LINES = 6
# 每张卡最多几行「【OCR】…」（推荐原文一行 + 关键的结束回合判据一行）。
MAX_OCR_TEXT_ROWS = 2
# 行首显式来源标注（用户 2026-11-01：一个【】就是一块，竖线分段）。
OCR_LABEL = "[OCR]"
EXEC_LABEL = "[执行]"
NOTE_LABEL = "[提示]"
WARN_LABEL = "[警告]"
ERROR_LABEL = "[错误]"
RAW_INDENT = " "  # 展开的 OCR 原文只缩进 1 个半角空格
# 块与块之间的浅色虚线分隔（用户 2026-11-01：每块像单独一个矩形、竖线不能连成一条）。
SEPARATOR_TAG = "card_sep"
SEPARATOR_ROW = ("", SEPARATOR_TAG, None)
# 行内二次时间戳（Tee / print_info 各加一层留下的）：「[23:54:11 INFO] …」。
_INLINE_TIME_RE = re.compile(r"^\[\d{1,2}:\d{2}:\d{2}\s+[^\]]+\]\s*")
# OCR 面板行：标题（「● 区域 N行 0.90」）与原文（「  ▏…」）。
_OCR_HEAD_PREFIX = "● "
_OCR_TEXT_INDENT = "  ▏"

# 阶段标记 → 块头里显示的名字（标记原文形如 -----我方出牌阶段-----）。
PHASE_LABELS = {
    "我方出牌阶段": "我方出牌",
    "对手回合阶段": "对手回合",
    "换牌阶段": "换牌",
    "对局结束阶段": "对局结束",
}

# 这些日志行不进块：OCR 推理细节、内存/自检/热键等与「这一步做了什么」无关。
IGNORE_TOKENS = (
    "内存 RSS",
    "自动投降检测",
    "环境自检",
    "本局算法",
    "cards.json",
    "进入对战",
    "热键",
    "自动化启动",
    "自动化结束",
    "已打开最新一份对战日志",
)

# 告警/报错：整行红色加粗（和浮窗原来的语义一致）。
ALERT_MARKERS = ("⚠️", "[ERROR]", "ERROR]", "[WARN]", "WARN]")

# 可选的时间前缀（"[12:04:52 SYS] ..."）：只看标签、不把时间当标签。
_TIME_TAG_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\s+([^\[\]]+)\]")
# 行首的 "[标签]"（有标签的行不再当成推荐续行的“裸行”）。
_TAG_PREFIX_RE = re.compile(r"^\[[^\[\]]+\]")
# 阶段标记：两边各若干短横线。
_PHASE_RE = re.compile(r"^-{3,}\s*(.+?)\s*-{3,}$")
_RECO_PREFIX = "[推荐]"
# "[推荐]" 后面跟这些词的是续行（不是一条新推荐）。
_RECO_CONTINUATIONS = ("原文", "目标", "放置")

_LOCK = threading.Lock()
# 新块在最前（index 0），满 MAX_BLOCKS 时自动丢弃最旧块。
_BLOCKS: deque = deque(maxlen=MAX_BLOCKS)
# OCR 快照提供器：log_overlay 注入，返回 ocr_panel_lines() 的行。
_PROVIDER = None
# 下一块用的阶段名（由阶段标记设置）。
_PHASE = ""
# 当前块是否还开着（阶段标记会关掉它，下一行再开新块）。
_OPEN = False
# 上一行是不是推荐（裸行跟在这种行后面就算推荐续行）。
_IN_RECO = False
# 上一条进 OCR 段的「尚未检测到结束回合」原文（连续重复只留一条）。
_LAST_OCR_TEXT = None
# 卡片展开状态（块 id 集合）与块 id 发生器：展开/收起是纯显示状态。
_EXPANDED: set = set()
_NEXT_ID = [0]


# ---------------------------------------------------------------- 对外 API
def set_ocr_provider(provider) -> None:
    """注入 OCR 快照提供器（callable 返回 ``ocr_panel_lines()`` 的行）。

    只有**新块建立时**才会调用一次：同一块内不再刷新快照，保证
    「OCR 内容 → 执行内容」这组信息是同一时刻的。
    """
    global _PROVIDER
    with _LOCK:
        _PROVIDER = provider if callable(provider) else None


def set_phase(label) -> None:
    """设置下一块使用的阶段名（阶段标记内部也走这里）。"""
    global _PHASE
    with _LOCK:
        _PHASE = str(label or "").strip()


def clear() -> None:
    """清空块流（浮窗关闭时调用；provider 保留，下次开窗还会用）。"""
    global _BLOCKS, _PHASE, _OPEN, _IN_RECO, _LAST_OCR_TEXT, _NEXT_ID
    with _LOCK:
        _BLOCKS = deque(maxlen=MAX_BLOCKS)
        _PHASE = ""
        _OPEN = False
        _IN_RECO = False
        _LAST_OCR_TEXT = None
        _EXPANDED.clear()
        _NEXT_ID = [0]


def toggle_expand(block_id) -> bool:
    """切换某张卡片的「原文」展开状态，返回切换后是否展开。"""
    try:
        bid = int(block_id)
    except (TypeError, ValueError):
        return False
    with _LOCK:
        if bid in _EXPANDED:
            _EXPANDED.discard(bid)
            return False
        _EXPANDED.add(bid)
        return True


def snapshot() -> list:
    """返回块列表（新→旧），字段与块结构一致（拷贝，外部改不到内部状态）。"""
    with _LOCK:
        return [
            {
                "at": block["at"],
                "phase": block["phase"],
                "id": block.get("id"),
                "ocr": list(block["ocr"]),
                "exec": list(block["exec"]),
            }
            for block in _BLOCKS
        ]


def render() -> list:
    """把块流渲染成卡片行：``[(text, tags, bar_color)]``（新块在最前）。

    R19 卡片式排版（用户 2026-11-01 口径）：每块 = 一张卡片
    * 行 1：``HH:MM · 阶段``（灰，卡片间距由 tag 的 spacing1 给）；
    * ``【OCR】…``：这一步**读到了什么**（推荐原文 / 结束回合框的主行），蓝字；
    * ``【执行】…``：这一步**做了什么**（亮色；告警报错红色加粗，连续重复 ``×N``）；
    * 「活人感 / 延时 1.4s」这类过程噪音不进卡片；
    * 行尾 ``原文 (N)`` 可点展开 —— 展开后最多 6 行原始 OCR 行 + ``＋N 行``。

    ``bar_color`` 是卡片左侧竖条的调色板名（``gold``/``warn``/``danger``/
    ``accent``/``dim``），由 ``log_overlay`` 画成 ``┃``；渲染层不碰 Tk。
    """
    with _LOCK:
        rows = []
        last_phase = None
        for block in _BLOCKS:
            phase = str(block.get("phase") or "")
            # 阶段变化时插一条居中金色阶段行（用户 2026-11-01：回合开始要居中标识）。
            if phase and phase != last_phase:
                rows.append((phase, "card_stage", None))
                last_phase = phase
            rows.extend(_card_rows(block))
        return rows


def export_rows() -> list:
    """导出用：``[(at, "[OCR] …")]``，按时间正序，只含日志流里看得见的行。

    与 ``render()`` 同源（同一个 ``_card_rows``），保证导出文本和浮窗所见一致；
    跳过时间行 / 虚线 / 「原文」展开入口这些纯界面元素。
    """
    with _LOCK:
        rows = []
        for block in reversed(_BLOCKS):
            try:
                at = float(block.get("at") or 0.0)
            except (TypeError, ValueError):
                at = 0.0
            for row in _card_rows(block):
                text, tag = row[0], row[1]
                if tag in ("card_meta", SEPARATOR_TAG, "card_raw", "card_more"):
                    continue
                if isinstance(tag, tuple):
                    continue
                rows.append((at, str(text)))
        return rows


def _card_rows(block: dict) -> list:
    """一张卡片 → 若干渲染行。

    用户 2026-11-01 口径：**一个【标签】就是一块** —— 每块单独一行、单独一段
    左侧竖条，块与块之间留空隙；标签分【OCR】/【执行】/【提示】/【警告】/【错误】。
    「活人感 / 延时 1.4s」这类过程噪音不进卡片。
    """
    bar = _phase_bar(block)
    rows = []
    # 时间行单独一行（不带竖条，只做分组）；阶段名在居中金色阶段行里。
    try:
        stamp = datetime.datetime.fromtimestamp(float(block["at"])).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        stamp = "--:--"
    rows.append((stamp, "card_meta", None))

    reco, primary, diag, raw = _split_ocr(block)
    ocr_bar = "danger" if _ocr_warned(block) else bar
    ocr_shown = 0
    for text in reco[:MAX_OCR_TEXT_ROWS]:
        rows.append((OCR_LABEL + text, "card_ocr", ocr_bar))
        rows.append(SEPARATOR_ROW)
        ocr_shown += 1
    if not ocr_shown and primary:
        rows.append((OCR_LABEL + primary, "card_ocr", ocr_bar))
        rows.append(SEPARATOR_ROW)
        ocr_shown = 1
    if diag:
        rows.append((OCR_LABEL + diag, "card_ocr", "warn"))
        rows.append(SEPARATOR_ROW)
        ocr_shown += 1

    # 执行/提示/告警：只合并同一类别的连续重复（连按 Ctrl+Q → ×3）。
    lines = []
    for text, tag in block.get("exec") or ():
        display = _display_text(str(text))
        if not display or _is_noise_row(display):
            continue
        label, kind, bar_color = _exec_label(display, str(tag), bar)
        if lines and lines[-1][0] == label and lines[-1][1] == display:
            lines[-1][2] += 1
            continue
        lines.append([label, display, 1, kind, bar_color])
    for label, text, count, kind, bar_color in lines:
        body = label + text + (f" ×{count}" if count > 1 else "")
        rows.append((body, kind, bar_color))
        rows.append(SEPARATOR_ROW)
    dropped = int(block["_more"].get("exec") or 0)
    if dropped:
        rows.append((NOTE_LABEL + f"…还有 {dropped} 行", "card_more", bar))

    if raw or (int(block["_more"].get("ocr") or 0) and not ocr_shown):
        extra = int(block["_more"].get("ocr") or 0)
        total = len(raw) + extra
        expanded = block.get("id") in _EXPANDED
        hint = "收起原文" if expanded else f"› 原文 ({total})"
        rows.append((hint, ("card_exp", f"exp_{block.get('id')}"), None))
        if expanded:
            for text in raw[:MAX_EXPANDED_RAW_LINES]:
                rows.append((RAW_INDENT + text, "card_raw", "dim"))
            more = total - min(len(raw), MAX_EXPANDED_RAW_LINES)
            if more > 0:
                rows.append((RAW_INDENT + f"＋{more} 行（滚动查看）", "card_more", "dim"))
    return rows


def _exec_label(text: str, tag: str, bar: str) -> tuple:
    """执行段一行 → ``(标签, 颜色 tag, 竖条颜色)``。

    「已检测到「结束回合」」本质是 OCR 检测结果，标成【OCR】而不是【执行】；
    带错误字样的告警走【错误】，其余告警走【警告】，普通信息走【提示】。
    """
    if tag == "alert":
        lowered = text.lower()
        if any(word in lowered for word in ("错误", "异常", "失败", "error", "exception")):
            return ERROR_LABEL, "card_alert", "danger"
        return WARN_LABEL, "card_alert", "danger"
    if tag == "exec":
        if "已检测到" in text and "结束回合" in text:
            return OCR_LABEL, "card_ocr", bar
        if text.startswith("结果") or "执行结果" in text:
            return NOTE_LABEL, "card_note", bar
        return EXEC_LABEL, "card_action", bar
    return NOTE_LABEL, "card_note", bar


def _is_noise_row(text: str) -> bool:
    """不进日志流的行。

    * 过程噪音：「活人感 延时 1.4s」「延时结束」这类只影响节奏的描述；
    * 识别结果：「已检测到/尚未检测到「结束回合」」改到上方的识别轮播窗口显示。
    """
    if "活人感" in text or "延时" in text:
        return True
    return ("尚未检测到" in text
            or ("已检测到" in text and "结束回合" in text))


def _split_ocr(block: dict) -> tuple:
    """OCR 段 → ``(推荐原文行, 区域主行, 未就绪诊断, 全部原文行)``。

    区域块按 ``OCR_PANEL_ORDER`` 排好（结束回合框永远第一）；主行取该区域
    第一条「不是纯数字/倒计时」的原文 —— 用户要看到的是 ``结束回合``，
    不是 ``2 行 1.00`` 这种统计。
    """
    reco, groups, diag = [], [], ""
    current = None
    for text, tag in block.get("ocr") or ():
        text = str(text or "").strip()
        if not text:
            continue
        if text.startswith(_OCR_HEAD_PREFIX):
            current = [_ocr_head_text(text), []]
            groups.append(current)
        elif text.startswith(_OCR_TEXT_INDENT.strip()):
            if current is not None:
                current[1].append(text.lstrip(" ▏").strip())
        elif "尚未检测到" in text:
            # 识别结果不进日志流，交给上方的识别轮播窗口。
            current = None
        else:
            display = _display_text(text)
            if display.startswith("原文"):
                continue  # 技术续行（PlayCard …）只在展开原文里看
            reco.append(display)
    primary = ""
    for _label, lines in groups:
        for line in lines:
            if line and not re.fullmatch(r"[\d:：.．\s]+", line):
                primary = line
                break
        if primary:
            break
    if not primary and groups:
        primary = groups[0][0]
    raw = [line for _label, lines in groups for line in lines]
    return reco, primary, diag, raw


def _display_text(text: str) -> str:
    """去掉浮窗里没用的前缀，只留「这一步做了什么」。

    * ``[SYS] [SYS] xxx`` → ``xxx``（Tee 与调用方各加了一层）；
    * ``[执行] …`` / ``[推荐] …`` → 去掉标记，动作本身才是正文；
    * 行内二次时间戳 ``[23:54:11 INFO] …`` → 去掉（卡片头已有时间；
      秒级时间在 ui_log_last.txt 里）。
    """
    result = str(text or "").strip()
    while True:
        stripped = _TAG_PREFIX_RE.sub("", result).strip()
        if stripped == result:
            break
        result = stripped
    match = _INLINE_TIME_RE.match(result)
    if match:
        result = result[match.end():].strip()
    return result



def push(line, level: str = "INFO") -> None:
    """喂一行日志，按归块规则放进当前块 / 新块。"""
    global _PHASE, _OPEN, _IN_RECO, _LAST_OCR_TEXT
    text = str(line).rstrip()
    if not text.strip():
        return
    normalized = _normalize(text)
    with _LOCK:
        if _is_ignored(normalized):
            return
        phase = _phase_of(normalized)
        if phase is not None:
            # 阶段标记本身不显示：关掉当前块，下一块用新的阶段名。
            _OPEN = False
            _IN_RECO = False
            _LAST_OCR_TEXT = None
            _PHASE = phase
            return
        wait_text = _end_turn_wait_text(normalized)
        if wait_text is not None:
            if wait_text == _LAST_OCR_TEXT:
                return  # 同一段「尚未检测到」连续刷屏只留一条
            _LAST_OCR_TEXT = wait_text
            _IN_RECO = False
            block = _current()
            if block is not None and block["exec"]:
                block = _new_block()  # 当前块已经执行过：这是新一轮观察
            elif block is None:
                block = _new_block()
            _append_line(block, "ocr", text, "text")
            return
        _LAST_OCR_TEXT = None
        reco = _recommendation_kind(normalized)
        if reco == "main":
            _IN_RECO = True
            block = _current()
            if block is not None and block["exec"]:
                block = _new_block()  # 上一个动作已经执行：新推荐 = 新块置顶
            elif block is None:
                block = _new_block()
            _append_line(block, "ocr", text, "text")
            return
        if reco == "cont":
            _IN_RECO = True
            block = _current() or _new_block()
            _append_line(block, "ocr", text, "text")
            return
        if _IN_RECO and not _TAG_PREFIX_RE.match(normalized):
            # 紧跟在推荐后面的裸行（例如卡名「生命分流」）算推荐续行。
            block = _current() or _new_block()
            _append_line(block, "ocr", text, "text")
            return
        _IN_RECO = False
        tag = _exec_tag(normalized, level)
        block = _current() or _new_block()
        _append_line(block, "exec", text, tag)


# ---------------------------------------------------------------- 归块规则
def _normalize(text: str) -> str:
    """去掉可选的 "[HH:MM:SS 标签]" 时间前缀，只留 "[标签] 正文" 便于匹配。"""
    stripped = text.strip()
    match = _TIME_TAG_RE.match(stripped)
    if match:
        return f"[{match.group(1).strip()}]{stripped[match.end():]}"
    return stripped


def _is_ignored(normalized: str) -> bool:
    if normalized.startswith("[OCR]"):
        return True
    return any(token in normalized for token in IGNORE_TOKENS)


def _phase_of(normalized: str):
    """阶段标记 → 阶段名；不是阶段标记返回 None（未知阶段返回空串）。"""
    match = _PHASE_RE.match(normalized)
    if match is None:
        return None
    return PHASE_LABELS.get(match.group(1).strip(), "")


def _end_turn_wait_text(normalized: str):
    """「[SYS] 尚未检测到「结束回合」（OCR 读到：…）」→ 返回原文，否则 None。"""
    if "尚未检测到" in normalized and "结束回合" in normalized:
        return normalized
    return None


def _recommendation_kind(normalized: str):
    """``[推荐]`` 行：``"main"`` 主推荐 / ``"cont"`` 续行 / None 不是推荐。"""
    if not normalized.startswith(_RECO_PREFIX):
        return None
    rest = normalized[len(_RECO_PREFIX):].lstrip()
    if rest.startswith(_RECO_CONTINUATIONS):
        return "cont"
    return "main"


def _exec_tag(normalized: str, level) -> str:
    """执行段的颜色 tag：告警 > 明确的执行/系统动作 > 其它灰字。"""
    if _is_alert(normalized, level):
        return "alert"
    if _is_exec_line(normalized):
        return "exec"
    return "dim"


def _is_alert(normalized: str, level) -> bool:
    if str(level or "").upper() in ("WARN", "ERROR"):
        return True
    return any(marker in normalized for marker in ALERT_MARKERS)


def _is_exec_line(normalized: str) -> bool:
    """是不是「执行/延时/检测到」这类明确的动作行（tag=exec，白字）。"""
    if (normalized.startswith("[执行]")
            or "执行结果" in normalized
            or "操作结束" in normalized):
        return True
    if "活人感" in normalized and "延时" in normalized:
        return True
    if "延时结束" in normalized:
        return True
    if "抽到" in normalized and "张牌" in normalized:
        return True
    if "已检测到" in normalized and "结束回合" in normalized:
        return True
    if "未确认" in normalized and "重新执行" in normalized:
        return True
    return False


# ---------------------------------------------------------------- 块内部
def _current():
    """当前可追加的块；被阶段标记关掉时返回 None（下一行开新块）。"""
    if not _OPEN or not _BLOCKS:
        return None
    return _BLOCKS[0]


def _new_block() -> dict:
    """新块置顶；建立时取一次 OCR 快照放进 OCR 段。"""
    global _OPEN
    block = {
        "at": time.time(),
        "phase": _PHASE,
        "id": _NEXT_ID[0],
        "ocr": [],
        "exec": [],
        # 每段被丢掉的行数（渲染时在段末补一行「…还有 N 行」）。
        "_more": {"ocr": 0, "exec": 0},
    }
    _NEXT_ID[0] += 1
    _BLOCKS.appendleft(block)
    _OPEN = True
    # 被 MAX_BLOCKS 挤掉的块，其展开状态一起清掉（否则 id 集合无界增长）。
    _EXPANDED.intersection_update(item["id"] for item in _BLOCKS)
    for text, tag in _snapshot_rows():
        _append_line(block, "ocr", text, tag)
    return block


def _append_line(block: dict, segment: str, text: str, tag: str) -> None:
    """往块的一段追加一行；超过上限就丢弃并记数（保证块大小有界）。"""
    bucket = block[segment]
    limit = MAX_OCR_LINES if segment == "ocr" else MAX_EXEC_LINES
    if len(bucket) < limit:
        bucket.append((str(text), str(tag)))
    else:
        block["_more"][segment] = block["_more"].get(segment, 0) + 1


def _snapshot_rows() -> list:
    """取一次 OCR 快照（provider 为空/异常就返回空，绝不影响日志主流程）。"""
    provider = _PROVIDER
    if provider is None:
        return []
    try:
        rows = provider()
    except Exception:
        return []
    result = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        tag = str(row.get("tag") or "text")
        # 区域标题行默认用 head（加粗），但 OCR 告警/命中要保留自己的颜色语义。
        if row.get("head") and tag not in ("warn", "match"):
            tag = "head"
        result.append((text, tag))
    return result


def _block_head(block: dict) -> str:
    """旧版块头（只给调试/兼容用；卡片头由 ``_card_rows`` 画）。"""
    try:
        stamp = datetime.datetime.fromtimestamp(float(block["at"])).strftime(
            "%H:%M:%S")
    except (TypeError, ValueError, OSError):
        stamp = "--:--:--"
    phase = str(block.get("phase") or "")
    return f"{stamp} · {phase}" if phase else stamp


# ---------------------------------------------------------------- 卡片渲染
def _phase_bar(block: dict) -> str:
    """阶段 → 卡片左侧竖条颜色名（调色板在 log_overlay）。"""
    phase = str(block.get("phase") or "")
    if "我方" in phase:
        return "gold"
    if "对手" in phase:
        return "warn"
    if "换牌" in phase:
        return "accent"
    return "dim"


def _ocr_warned(block: dict) -> bool:
    """OCR 段里有没有「读到但不是想要的内容」的告警标题。"""
    for text, tag in block.get("ocr") or ():
        if str(text or "").startswith(_OCR_HEAD_PREFIX) and str(tag) == "warn":
            return True
    return False


def _ocr_head_text(text: str) -> str:
    """「● 结束回合框 12行 0.90 ×2」→「结束回合框 12 行 0.90 ×2」。"""
    body = text[len(_OCR_HEAD_PREFIX):].strip()
    return re.sub(r"(\d+)\s*行", r"\1 行", body, count=1)


def _inline_ocr_dump(text: str) -> list:
    """从诊断行里把「（OCR 读到：N 行，前 2 行：a / b）」拆成原文行（展开态用）。"""
    match = re.search(r"（OCR 读到[：:](.*?)）", text)
    if not match:
        return []
    body = match.group(1)
    # 去掉 FSM_action 新加的「8 行，前 2 行：」前缀，只留原文。
    body = re.sub(r"^\s*\d+\s*行[，,、]?\s*(?:前\s*\d+\s*行[：:])?", "", body)
    parts = [part.strip() for part in body.split("/")]
    return [part for part in parts if part]
