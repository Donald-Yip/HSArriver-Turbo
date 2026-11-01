# -*- coding: utf-8 -*-
"""常驻意图记录：区分「手动停止/退出」与「错误」，并跨控制台重启保留。

为什么需要它
------------------------------------------------------------------
常驻模式有两种完全不同的起点：

* **手动停止/手动退出**：用户自己点了停止、按了 Ctrl+Q、点了浮窗「退出脚本」、
  关了控制台窗口、或者用 ``hsctl serve stop`` 收的尾。这时用户**不想再被自动
  接管**，重新打开控制台后必须等他手动点「开始对战」/「开启常驻」。
* **错误/崩溃**：日志卡死、炉石进程消失、控制台自己崩了。这时常驻（和看门狗）
  应该把自动化/控制台拉回来——这才是无人值守的意义。

控制台一退出，内存里的状态就没了（``CTRL.stopped_by`` 只是进程内标记），下次
启动分不清「上次是我自己关的」还是「上次是崩了」。结果就是：手动退出 → 重新
打开脚本 → 常驻按错误恢复的流程自动点一遍，和用户的手动操作抢鼠标。

本模块只做一件事：把「手动停止」这个意图**落盘**（``log/resident_intent.json``），
提供读/写/清除/分类四个操作。判断与动作仍由 ``resident_supervisor`` / ``web_ui``
负责，本模块不 import 任何项目模块（只用标准库），因此可以脱离 Windows/炉石单测。

判定口径
------------------------------------------------------------------
* 文件不存在 → ``first_run``（从没记录过，例如首次使用；按旧行为自动接管）。
* 文件里 ``manual_stop=true`` → ``manual``：上次是用户主动停的，重启后保持暂停。
* 其它（文件存在但 ``manual_stop`` 非真 / 读到坏数据）→ ``error``：按错误处理。

写入时机（调用方负责，本模块不判断语义）：用户停止自动化、Ctrl+Q 急停、关闭
常驻、浮窗「退出脚本」、关闭控制台窗口、``hsctl serve stop``。清除时机：用户
手动点「开始对战」或显式「开启常驻」。

写盘是**尽力而为**：任何 I/O 异常都吞掉（返回空结果），绝不能因为写不了一个
标记文件把常驻线程/控制台退出流程带崩。读盘同理：文件坏了当作 ``first_run``。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

# 仓库根：src/safety/resident_intent.py → parents[2]
ROOT = Path(__file__).resolve().parents[2]
# 本机运行态，随 log/ 一起不入库（见 .gitignore）。
DEFAULT_PATH = ROOT / "log" / "resident_intent.json"

STATE_VERSION = 1
# reason 只用于展示/排查，限制长度避免把日志撑爆。
MAX_REASON_LENGTH = 120
# 「手动停止」分两种范围，决定看门狗能不能自动拉起控制台：
#   * console    = 用户退出了整个控制台（浮窗退出/关窗口/Ctrl+C/serve stop）；
#                  看门狗必须**不**拉起，否则又把人拉回「我不玩了」的状态。
#   * automation = 用户只是停了自动化（控制台还开着）；控制台若崩溃，看门狗
#                  仍然应该把控制台拉回来（只是常驻保持暂停等手动开始）。
MANUAL_SCOPES = ("console", "automation")
DEFAULT_SCOPE = "console"

_WRITE_LOCK = threading.RLock()


def _now_iso(now=None) -> str:
    """当前本地时间（秒级 ISO），便于人和日志对照；``now`` 可注入固定时间。"""
    if now is None:
        return datetime.now().isoformat(timespec="seconds")
    if isinstance(now, str):
        return now
    try:
        return now.isoformat(timespec="seconds")
    except Exception:
        return str(now)


def _resolve(path=None) -> Path:
    return Path(path) if path is not None else DEFAULT_PATH


def _clean_reason(reason) -> str:
    text = str(reason or "").strip()
    if len(text) > MAX_REASON_LENGTH:
        text = text[:MAX_REASON_LENGTH]
    return text or "manual"


def read(path=None) -> dict:
    """读常驻意图记录；文件不存在/内容损坏一律返回空 dict（不抛异常）。"""
    target = _resolve(path)
    try:
        raw = target.read_text(encoding="utf8")
    except Exception:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _write_atomic(payload: dict, path=None) -> dict:
    """原子写 payload（同目录临时文件 + os.replace）；失败只吞掉不抛异常。"""
    target = _resolve(path)
    with _WRITE_LOCK:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf8"
            )
            os.replace(tmp, target)
        except Exception:
            # 写盘失败不影响调用方（退出/停止/启动）；下次启动最多按 first_run 处理。
            pass
    return payload


def record_manual_stop(reason="manual", *, scope=DEFAULT_SCOPE, at=None,
                       path=None, pid=None) -> dict:
    """记录「用户手动停止/退出」。返回写入的 payload（写失败时返回 payload 但不落盘）。

    ``scope`` 区分严重程度（见 ``MANUAL_SCOPES``）：
    * ``console``：用户退出了整个控制台 → 看门狗不自动拉起；
    * ``automation``：只是停了自动化、控制台还开着 → 看门狗仍应在崩溃后拉起控制台
      （常驻本身会因该标记保持暂停，等手动开始）。

    原子写：先写同目录临时文件再 ``os.replace``，避免进程在写一半时被杀留下半截
    JSON，让下次启动误判成 ``error``（会把用户手动退出当成崩溃自动拉起）。
    """
    scope_text = str(scope or DEFAULT_SCOPE).strip().lower()
    if scope_text not in MANUAL_SCOPES:
        scope_text = DEFAULT_SCOPE
    return _write_atomic(
        {
            "version": STATE_VERSION,
            "manual_stop": True,
            "scope": scope_text,
            "reason": _clean_reason(reason),
            "at": _now_iso(at),
            "pid": int(os.getpid() if pid is None else pid),
        },
        path,
    )


def record_running(reason="running", *, at=None, path=None, pid=None) -> dict:
    """记录「控制台正常在跑」（非手动停止）。让之后的一次崩溃能被分类成 error。

    为什么需要：``clear()`` 会把整份文件删掉，若当时控制台崩了，下次启动读到
    「没有文件」只能按 ``first_run`` 处理（自动装配但日志不叫「自启接管」）。
    启动成功后写一份 ``manual_stop=false`` 的记录，崩溃时文件还在，下次启动就能
    明确判成 ``error``，看门狗也能据此自动拉起。
    """
    return _write_atomic(
        {
            "version": STATE_VERSION,
            "manual_stop": False,
            "reason": _clean_reason(reason),
            "at": _now_iso(at),
            "pid": int(os.getpid() if pid is None else pid),
        },
        path,
    )


def clear(path=None) -> bool:
    """清除「手动停止」标记（用户手动开始/显式开启常驻时调用）。"""
    target = _resolve(path)
    with _WRITE_LOCK:
        try:
            tmp = target.with_name(target.name + ".tmp")
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        try:
            target.unlink()
            return True
        except FileNotFoundError:
            return True
        except Exception:
            return False


def manual_stop_active(path=None) -> bool:
    """当前是否处于「用户手动停止」状态（常驻据此保持暂停）。"""
    data = read(path)
    return bool(data.get("manual_stop"))


def autostart_blocked(path=None) -> bool:
    """看门狗是否应当**跳过**自动拉起控制台。

    只有「用户退出了整个控制台」才挡；只是停了自动化（控制台还开着）不挡——
    那种情况下控制台崩溃后仍应被拉回来，常驻自己按 manual_stop 保持暂停即可。
    """
    data = read(path)
    if not data.get("manual_stop"):
        return False
    return str(data.get("scope") or DEFAULT_SCOPE).lower() == "console"


def summary(path=None) -> dict:
    """给网页/浮窗/CLI 用的一行摘要：``kind`` + 范围 + 原因 + 时间。"""
    data = read(path)
    kind = "first_run"
    if data:
        kind = "manual" if data.get("manual_stop") else "error"
    scope = str(data.get("scope") or "") if data.get("manual_stop") else ""
    if scope not in MANUAL_SCOPES:
        scope = DEFAULT_SCOPE if data.get("manual_stop") else ""
    return {
        "kind": kind,
        "manual_stop": bool(data.get("manual_stop")),
        "scope": scope,
        "reason": str(data.get("reason") or ""),
        "at": str(data.get("at") or ""),
    }


def classify_boot(path=None) -> str:
    """控制台启动时的分类：``manual`` / ``error`` / ``first_run``。"""
    return str(summary(path).get("kind") or "first_run")
