# -*- coding: utf-8 -*-
"""结构化分块对战日志：把扁平的自动化日志行整理成「回合大块 + 步骤小块」。

背景
----
浮窗「保存日志」原来只是把 ``log_overlay._FULL_LINES`` 原样倒成一份
``logs\\对战日志_<时间戳>.txt``，几百行平铺，出了问题要人工一行行翻：
哪一回合、盒子推荐了什么、脚本执行成功没有、重试了几次、报没报错——全得自己读。

本模块把同样的日志行整理成**结构化数据**（唯一数据源，JSONL，每行一个回合块）：

* **回合大块**：``turn`` + ``phase``（匹配/换牌/对局/结算）+ 起止时间 + 汇总
  （步数 / 重试 / 报错 / 耗时）；
* **步骤小块**：一条推荐指令 = 一个小块；**指令不变就一直并进同一块**，重试只累计
  次数（不刷屏）；指令变了才新开块。小块带执行情况与耗时。

``render_text`` 再把 JSONL 渲染成人看的 TXT（TXT 是产物，两者永不脱节）。
设计沿用 hs_restart / script_logs 的路子：**核心是纯函数**（``classify`` /
``build_blocks`` / ``render_*``），文件写入与编排交给调用方，副作用可注入，方便单测。
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

SCHEMA_VERSION = 1
KIND_META = "meta"
KIND_TURN = "turn"

# 内存里保留多少条原始记录（够一次对局/一次导出即可；超出丢最旧）。
MAX_RECORDS = 20000
# 每个 step 的 events 最多保留多少条（去重之后仍超上限就截断）。
MAX_EVENTS_PER_STEP = 50

# 状态取值（``status``）：报错优先级最高，其次执行成功/重试/观察。
STATUS_ERROR = "error"
STATUS_EXECUTED = "executed"
STATUS_RETRY = "retry"
STATUS_INFO = "info"

# 「我方回合 N 开始读推荐」——唯一可靠的回合起点标志。
#   [SYS] 回合 3 延时结束，开始本轮推荐读取。
_TURN_RE = re.compile(r"回合\s*(\d+)\s*延时结束")
# 阶段标题：web_ui 写的 "-----匹配阶段-----"（两边各若干短横线）。
_PHASE_RE = re.compile(r"-{2,}\s*([^\s-]+?)阶段\s*-{2,}")
# 重试诊断码："……继续重试：recommendation_changed"。
_RETRY_RE = re.compile(r"重试[：:]\s*(.+?)\s*$")

# 该行是不是「重试」类日志（换牌重试/暂不可执行继续重试/重新点击）。
_RETRY_MARKERS = ("继续重试", "换牌重试", "重新点击", "retry:")

_LOCK = threading.RLock()
_RECORDS: deque = deque(maxlen=MAX_RECORDS)
_SESSION = {"id": "unknown-session", "stamp": None, "started_at": None}


def _now() -> float:
    return time.time()


def _full_time(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _short_time(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


# ---------------------------------------------------------------- 记录 / 会话
def start_session(session_id: Optional[str] = None, now: Optional[float] = None) -> str:
    """开始一轮运行：清空记录并生成会话时间戳（决定日志文件名）。

    返回会话 id（拿不到就 ``"unknown-session"``）。幂等：重复调用会重新计时。
    """
    ts = _now() if now is None else float(now)
    sid = (
        str(session_id)
        if session_id
        else datetime.fromtimestamp(ts).strftime("session-%Y%m%d_%H%M%S")
    )
    with _LOCK:
        _RECORDS.clear()
        _SESSION["id"] = sid
        _SESSION["stamp"] = datetime.fromtimestamp(ts).strftime("%Y%m%d_%H%M%S")
        _SESSION["started_at"] = ts
    return sid


def reset(session_id: Optional[str] = None) -> None:
    """兼容别名：清空记录并开新会话。"""
    start_session(session_id)


def session_id() -> str:
    with _LOCK:
        return _SESSION["id"]


def session_stamp() -> Optional[str]:
    with _LOCK:
        return _SESSION["stamp"]


def session_started_at() -> Optional[float]:
    with _LOCK:
        return _SESSION["started_at"]


def record(level: str, msg: str, ts: Optional[float] = None) -> None:
    """把一条日志行（含时间）收进内存记录。任何异常都不得外溢。"""
    text = str(msg or "").rstrip()
    if not text.strip():
        return
    with _LOCK:
        _RECORDS.append(
            {
                "t": _now() if ts is None else float(ts),
                "level": str(level or "INFO"),
                "text": text,
            }
        )


def records() -> list:
    """返回当前记录的浅拷贝快照。"""
    with _LOCK:
        return list(_RECORDS)


# ---------------------------------------------------------------- 行分类
def classify(text: str, level: str = "INFO") -> dict:
    """把一条日志行归类成结构信息（纯函数，方便单测）。

    返回 ``{"kind": ..., ...}``，kind 取值：
      * ``turn``            回合起点（带 ``turn``）
      * ``phase``           阶段标题（带 ``phase``）
      * ``recommendation``  盒子推荐指令（带 ``instruction``）
      * ``recommendation_raw`` 推荐原文（带 ``text``）
      * ``execution``       执行动作（带 ``text``）
      * ``retry``           重试（带 ``reason``）
      * ``error``           报错/告警（带 ``text``，触发截图）
      * ``info``            其它
    """
    text = str(text or "").strip()
    lvl = str(level or "").upper()
    if not text:
        return {"kind": "info", "text": ""}

    m = _PHASE_RE.search(text)
    if m:
        return {"kind": "phase", "phase": m.group(1)}

    if lvl == "ERROR" or "⚠️" in text or text.startswith("[ERROR") or "ERROR]" in text:
        return {"kind": "error", "text": _strip_tags(text)}

    if text.startswith("[WARN") or "WARN]" in text:
        return {"kind": "warn", "text": _strip_tags(text)}

    m = _TURN_RE.search(text)
    if m:
        return {"kind": "turn", "turn": int(m.group(1))}

    stripped = text.lstrip()
    if stripped.startswith("[推荐]"):
        body = stripped[len("[推荐]") :].strip()
        if body.startswith("原文："):
            return {"kind": "recommendation_raw", "text": body[len("原文：") :].strip()}
        return {"kind": "recommendation", "instruction": body}

    if stripped.startswith("[执行]"):
        return {"kind": "execution", "text": stripped[len("[执行]") :].strip()}

    for marker in _RETRY_MARKERS:
        if marker in text:
            m = _RETRY_RE.search(text)
            reason = m.group(1) if m else _strip_tags(text)
            return {"kind": "retry", "reason": reason}
    return {"kind": "info", "text": text}


def _strip_tags(text: str) -> str:
    """去掉 ``[ERROR]`` / ``[WARN]`` / ``⚠️`` 等前缀，留下正文。"""
    out = str(text or "").strip()
    for tag in ("[ERROR]", "[WARN]", "[SYS]", "[INFO]", "⚠️"):
        out = out.replace(tag, "")
    return out.strip()


# ---------------------------------------------------------------- 分块
def build_blocks(records_list, phase: Optional[str] = None) -> list:
    """把扁平行划分成「回合大块 + 步骤小块」。

    规则：
      * 收到回合起点 → 关掉当前块，开一个带 ``turn`` 的新大块；
      * 收到阶段标题 → 若当前块已有内容就关掉、开一个 ``turn=None`` 的阶段块；
      * ``[推荐] <指令>`` → 指令不变则并进当前小块（只更新时间），变了才新开小块；
      * 重试并入当前小块并累计次数；报错写进当前小块并置 ``error``；
      * 其余行归入当前小块/大块的 ``events``。
    空块（既无步骤也无事件）会被丢弃。phase 是“还没遇到阶段标题时的默认阶段”。
    """
    blocks: list = []
    current: Optional[dict] = None
    current_phase = phase or ""
    last_ts = None

    def close_block():
        nonlocal current
        if current is not None and (current["steps"] or current["events"]):
            _finalize_block(current)
            blocks.append(current)
        current = None

    def ensure_block(ts):
        nonlocal current
        if current is None:
            current = _new_block(None, current_phase, ts)
        return current

    def ensure_step(block, ts):
        if not block["steps"] or block["steps"][-1].get("_closed"):
            block["steps"].append(_new_step(len(block["steps"]) + 1, ts))
        step = block["steps"][-1]
        if not step["events"] and step["start_ts"] is None:
            step["start_ts"] = ts
        return step

    def close_step(step):
        if step is not None:
            step["_closed"] = True

    for rec in records_list:
        ts = float(rec.get("t") or 0.0)
        info = classify(rec.get("text", ""), rec.get("level", "INFO"))
        kind = info["kind"]
        last_ts = ts

        if kind == "phase":
            if current is not None and (current["steps"] or current["events"]):
                close_block()
            current_phase = info["phase"]
            block = ensure_block(ts)
            block["phase"] = current_phase
            continue

        if kind == "turn":
            close_block()
            current = _new_block(info["turn"], current_phase, ts)
            continue

        if kind == "recommendation":
            block = ensure_block(ts)
            instruction = info.get("instruction") or ""
            if (
                block["steps"]
                and not block["steps"][-1]["_closed"]
                and block["steps"][-1]["instruction"] == instruction
            ):
                step = block["steps"][-1]
            else:
                close_step(block["steps"][-1] if block["steps"] else None)
                step = _new_step(len(block["steps"]) + 1, ts)
                step["instruction"] = instruction
                block["steps"].append(step)
            step["end_ts"] = ts
            continue

        if kind == "recommendation_raw":
            block = ensure_block(ts)
            step = ensure_step(block, ts)
            raw = info.get("text") or ""
            if raw and raw not in step["raw"]:
                step["raw"].append(raw)
            step["end_ts"] = ts
            continue

        if kind == "execution":
            block = ensure_block(ts)
            step = ensure_step(block, ts)
            step["execution"] = info.get("text") or ""
            step["_has_exec"] = True
            step["end_ts"] = ts
            continue

        if kind == "retry":
            block = ensure_block(ts)
            step = ensure_step(block, ts)
            step["retries"] += 1
            reason = info.get("reason") or ""
            step["retry_reasons"][reason] = step["retry_reasons"].get(reason, 0) + 1
            if reason:
                step["detail"] = reason
            _add_event(step, _clean_line(rec.get("text", "")))
            step["end_ts"] = ts
            continue

        if kind in ("error", "warn"):
            block = ensure_block(ts)
            step = ensure_step(block, ts)
            step["_error"] = True
            detail = info.get("text") or _clean_line(rec.get("text", ""))
            if detail:
                step["detail"] = detail
            _add_event(step, _clean_line(rec.get("text", "")))
            step["end_ts"] = ts
            continue

        # info：归入当前小块，没有小块就归入大块。
        block = ensure_block(ts)
        line = _clean_line(rec.get("text", ""))
        if block["steps"] and not block["steps"][-1]["_closed"]:
            _add_event(block["steps"][-1], line)
            block["steps"][-1]["end_ts"] = ts
        else:
            _add_event(block, line)
        block["end_ts"] = ts

    close_block()
    if last_ts is not None and blocks:
        blocks[-1]["end_ts"] = blocks[-1]["end_ts"] or last_ts
    return blocks


def _new_block(turn, phase, ts):
    return {
        "turn": turn,
        "phase": phase or "",
        "start_ts": ts,
        "end_ts": None,
        "steps": [],
        "events": [],
    }


def _new_step(index, ts):
    return {
        "index": index,
        "start_ts": ts,
        "end_ts": ts,
        "instruction": None,
        "raw": [],
        "execution": None,
        "retries": 0,
        "retry_reasons": OrderedDict(),
        "detail": "",
        "events": [],
        "_error": False,
        "_has_exec": False,
        "_closed": False,
    }


def _add_event(container, line):
    if not line:
        return
    events = container["events"]
    if line in events:  # 去重：重试刷屏只留一条
        return
    if len(events) >= MAX_EVENTS_PER_STEP:
        return
    events.append(line)


def _clean_line(text: str) -> str:
    return str(text or "").rstrip()


def _finalize_block(block):
    """结算每个小块的状态/耗时，并汇总成大块 summary。"""
    total_retries = 0
    total_errors = 0
    for step in block["steps"]:
        step["status"] = _step_status(step)
        total_retries += step["retries"]
        if step["status"] == STATUS_ERROR:
            total_errors += 1
        start = step["start_ts"] or block["start_ts"] or 0.0
        end = step["end_ts"] or start
        step["duration"] = round(max(0.0, end - start), 1)
        if block["end_ts"] is None or end > block["end_ts"]:
            block["end_ts"] = end
    start = block["start_ts"] or 0.0
    end = block["end_ts"] or start
    block["duration"] = round(max(0.0, end - start), 1)
    block["summary"] = {
        "steps": len(block["steps"]),
        "retries": total_retries,
        "errors": total_errors,
        "duration_seconds": block["duration"],
    }


def _step_status(step) -> str:
    if step.get("_error"):
        return STATUS_ERROR
    if step.get("_has_exec"):
        return STATUS_EXECUTED
    if step["retries"] > 0:
        return STATUS_RETRY
    return STATUS_INFO


# ---------------------------------------------------------------- 序列化
def block_to_obj(block: dict, partial: bool = False) -> dict:
    """把内部大块转成写进 JSONL 的对象。"""
    steps = []
    for step in block["steps"]:
        steps.append(
            {
                "index": step["index"],
                "start_time": _full_time(step["start_ts"]),
                "end_time": _full_time(step["end_ts"]),
                "duration_seconds": step.get("duration", 0.0),
                "recommendation": (
                    {"instruction": step["instruction"], "raw": list(step["raw"])}
                    if step["instruction"] is not None
                    else None
                ),
                "execution": step["execution"],
                "status": step.get("status", STATUS_INFO),
                "retries": step["retries"],
                "retry_reasons": [
                    {"reason": reason, "count": count}
                    for reason, count in step["retry_reasons"].items()
                ],
                "detail": step["detail"],
                "events": list(step["events"]),
            }
        )
    return {
        "schema": SCHEMA_VERSION,
        "kind": KIND_TURN,
        "phase": block["phase"],
        "turn": block["turn"],
        "partial": bool(partial),
        "start_time": _full_time(block["start_ts"]),
        "end_time": _full_time(block["end_ts"]),
        "summary": block.get("summary", {}),
        "events": list(block["events"]),
        "steps": steps,
    }


def meta_obj(
    session: Optional[str] = None, date: Optional[str] = None, app: str = ""
) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "kind": KIND_META,
        "session": session or session_id(),
        "date": date or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "app": app,
    }


def render_jsonl(
    blocks, session: Optional[str] = None, app: str = "", meta: bool = True
) -> str:
    """渲染成 JSONL：元数据头 1 行 + 每个回合块 1 行。"""
    lines = []
    if meta:
        lines.append(
            json.dumps(
                meta_obj(session=session, app=app),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    for block in blocks:
        lines.append(
            json.dumps(block_to_obj(block), ensure_ascii=False, separators=(",", ":"))
        )
    return "\n".join(lines) + ("\n" if lines else "")


def render_text(blocks, meta=None) -> str:
    """渲染成人可读 TXT（由 JSONL 生成，两者口径一致）。"""
    meta = meta or {}
    out = [
        "# 对战日志 "
        + (meta.get("date") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    ]
    if meta.get("source"):
        out.append(
            "# 数据源：" + str(meta["source"]) + "（唯一数据源，本文件为可读渲染）"
        )
    if meta.get("session"):
        out.append("# 会话 " + str(meta["session"]))
    out.append("")
    if not blocks:
        out.append("（本次运行没有可归档的对局日志。）")
        return "\n".join(out) + "\n"
    for block in blocks:
        out.extend(_render_block(block))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _render_block(block) -> list:
    summary = block.get("summary", {})
    header = f"════════════════════ {_block_title(block)} ════════════════════════"
    start = _short_time(block["start_ts"])
    end = _short_time(block["end_ts"]) if block["end_ts"] else "…"
    line = (
        f"  开始 {start} · 结束 {end} · 总耗时 "
        f"{summary.get('duration_seconds', block.get('duration', 0.0)):.1f}s "
        f"· {summary.get('steps', 0)} 步 / 重试 {summary.get('retries', 0)} "
        f"/ 报错 {summary.get('errors', 0)}"
    )
    rows = [header, line, ""]
    for step in block["steps"]:
        rows.append(f"  --- 步骤 {step['index']}/{summary.get('steps', 0)} " + "-" * 37)
        rows.append(
            f"  时间    {_short_time(step['start_ts'])}"
            + (
                f" → {_short_time(step['end_ts'])}"
                if step["end_ts"] != step["start_ts"]
                else ""
            )
        )
        rows.append(f"  耗时    {step.get('duration', 0.0):.1f}s")
        if step["instruction"] is not None:
            rows.append(f"  推荐    {step['instruction']}")
        for raw in step["raw"]:
            rows.append(f"  原文    {raw}")
        if step["retries"]:
            reasons = "，".join(
                f"{reason} ×{count}" for reason, count in step["retry_reasons"].items()
            )
            rows.append(
                f"  重试    {step['retries']} 次"
                + (f"（{reasons}）" if reasons else "")
            )
        if step["execution"]:
            rows.append(f"  执行    {step['execution']}")
        rows.append(f"  状态    {_status_label(step)}")
        for event in step["events"]:
            rows.append(f"  信息    {event}")
        rows.append("")
    for event in block["events"]:
        rows.append(f"  信息    {event}")
    return rows


def _block_title(block) -> str:
    phase = block.get("phase") or ""
    if block["turn"] is None:
        return f"对局前 · {phase}" if phase else "对局前"
    return f"回合 {block['turn']}" + (f" · {phase}" if phase else "")


def _status_label(step) -> str:
    status = step.get("status", STATUS_INFO)
    if status == STATUS_ERROR:
        return f"报错（{step['detail']}）" if step["detail"] else "报错"
    if status == STATUS_EXECUTED:
        return "已执行"
    if status == STATUS_RETRY:
        return f"重试中（{step['detail']}）" if step["detail"] else "重试中"
    return "进行中"


# ---------------------------------------------------------------- 文件写入
def write_jsonl(path, blocks, session: Optional[str] = None, app: str = "") -> str:
    """整份写出 JSONL（覆盖）。返回路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = render_jsonl(blocks, session=session, app=app)
    path.write_text(text, encoding="utf-8")
    return str(path)


def write_text(path, blocks, meta=None) -> str:
    """整份写出可读 TXT（覆盖）。返回路径。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_text(blocks, meta=meta), encoding="utf-8")
    return str(path)


class AutoAppender:
    """回合结束时自动把**已完成**的回合块追加进 JSONL（崩溃也不丢）。

    只追加“不是最后一个”的块（最后一个可能还在进行中）。用
    ``(turn, start_time)`` 去重，重复调用不会写重。路径变化（新会话）时自动重置。
    """

    def __init__(self, writer: Optional[Callable[[Path, str], None]] = None):
        self._written: set = set()
        self._path: Optional[Path] = None
        self._header_done = False
        self._writer = writer or self._default_writer

    @staticmethod
    def _default_writer(path: Path, line: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)

    def flush(self, path, blocks, meta=None) -> int:
        """把已完成的回合块追加进 ``path``，返回新写入的块数。"""
        path = Path(path)
        if self._path != path:
            self._path = path
            self._written = set()
            self._header_done = path.exists() and path.stat().st_size > 0
        written = 0
        for block in blocks[:-1] if blocks else []:
            obj = block_to_obj(block)
            key = (obj.get("turn"), obj.get("start_time"))
            if key in self._written:
                continue
            if not self._header_done:
                self._writer(
                    path,
                    json.dumps(
                        meta or meta_obj(), ensure_ascii=False, separators=(",", ":")
                    )
                    + "\n",
                )
                self._header_done = True
            self._writer(
                path, json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
            )
            self._written.add(key)
            written += 1
        return written

    def reset(self) -> None:
        self._written = set()
        self._path = None
        self._header_done = False
