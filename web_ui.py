# -*- coding: utf-8 -*-
"""HSLegendArriver Web 控制台（零依赖后端）。

仅使用 Python 标准库：本地 HTTP 服务 + 自动化线程管理 + 定时调度 + 实时日志。
前端页面位于 web/index.html，启动后自动在浏览器打开。

用法:
    python web_ui.py [--no-browser]

功能:
    * 页面配置用户 ID 与炉石日志目录（写入 constants 并持久化到 ui_config.json）
    * 定时任务：开始时间不能早于当前时间；到达结束时间后，
      先等本局对战打完（状态机回到非对局状态）再自动终止自动化
    * 「开始对战」按钮立即启动，「本局结束后停止」「立即停止」手动控制
    * Ctrl+Q 全局热键立即停止
"""

from __future__ import annotations

# 兼容「只装 requirements.txt、没装项目包」的旧式部署：把仓库内 src/ 加进 sys.path，
# 这样 click.py / FSM_action.py 里的 `from hslegend...` 也能导入（等价 pip install -e .）。
import _srcpath  # noqa: F401

import sys


def _configure_stdio() -> None:
    """Force console streams to UTF-8 on Chinese Windows code pages."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


_configure_stdio()


def _ensure_module_alias() -> None:
    """`python web_ui.py` 启动时把本模块登记成 ``web_ui``（全进程唯一实例）。

    控制台平时以 ``python web_ui.py`` 启动，此时本模块在 ``sys.modules["__main__"]``。
    其它模块若按名字 ``import web_ui`` 会**再执行一遍本文件**，造出第二份
    CTRL/RESIDENT —— 「守护」与安全网因此永远看不到自动化线程；FSM 的
    ``sys.modules.get("web_ui")`` 也会恒为 None，导致守护「接管/让位」判定在
    生产环境完全失效。

    在模块体最前面补一个别名，后续任何 ``import web_ui`` / ``sys.modules["web_ui"]``
    都命中同一个实例。正常被 import（``__name__ == "web_ui"``）时是 no-op。
    """
    if __name__ == "__main__":
        try:
            sys.modules.setdefault("web_ui", sys.modules[__name__])
        except Exception:
            # 嵌入/非常规加载器下 sys.modules 可能不完整；别名失败不应阻止启动。
            pass


_ensure_module_alias()

import ctypes

try:
    import log_overlay
except Exception as exc:
    print(f"[overlay] 导入日志浮窗失败(已禁用): {type(exc).__name__}: {exc}")
    log_overlay = None
if log_overlay is not None:
    # 浮窗内部异常（tkinter 不可用、Tk 初始化失败……）也写进网页日志：
    # 否则"点了没反应"，用户和反馈都看不到原因。
    try:
        log_overlay.set_logger(lambda text: _log("WARN", text))
    except Exception:
        pass
import json
import os
import platform
import secrets
import subprocess
import threading
import time
import traceback
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 脚本自己的日志文件：统计体积 / 打开位置 / 一键清理（纯函数在 script_logs.py）。
import script_logs

# 浮窗「OCR / 执行」分块流：每行日志都先喂给它（归块在 action_feed 里做）。
import action_feed

# 版本号的唯一来源（网页顶栏、浮窗品牌行、CLI 都从这里取）。
import version
# 线上最新版本号（浮窗红色自查提示用）：后台线程 + 6h 缓存，导入不联网。
import update_check

# 结构化分块对战日志（每行一个回合块）+ 出错自动截图/反馈包。
import battle_record
import feedback

ROOT = Path(__file__).resolve().parent
# 对战日志目录与运行日志路径（日志浮窗的「日志」行、「打开日志/清理日志」都用它）。
LOG_DIR = script_logs.LOG_DIR
RUNTIME_LOG = script_logs.RUNTIME_LOG

# ---------------------------------------------------------------- 结构化对战日志
# 每条日志行都做两件事：收进 battle_record（按回合/推荐分块，落 JSONL）+ 更新
# “当前回合/阶段/推荐”上下文；命中重试/报错就交给 feedback（达阈值自动截图）。
_battle_ctx: dict = {}
_battle_appender = battle_record.AutoAppender()
_battle_flush_lock = threading.Lock()
_APP_LABEL = f"{version.NAME} {version.version_label()}"


def _battle_meta() -> dict:
    return battle_record.meta_obj(app=_APP_LABEL)


def _battle_record_path():
    """当前会话的结构化日志路径（还没开会话时返回 None）。"""
    stamp = battle_record.session_stamp()
    if not stamp:
        return None
    name = f"{script_logs.BATTLE_LOG_PREFIX}{stamp}{script_logs.BATTLE_RECORD_SUFFIX}"
    return script_logs.LOG_DIR / name


def _begin_battle_session() -> str:
    """新一轮运行开始：开新会话（决定日志文件名）并清空上次的反馈事件。"""
    battle_record.start_session()
    stamp = battle_record.session_stamp()
    feedback.reset(session_stamp=stamp)
    _battle_ctx.clear()
    try:
        _battle_appender.reset()
    except Exception:
        pass
    return stamp or ""


def _flush_battle_blocks() -> None:
    """回合结束：把已完成回合块自动追加进 JSONL（崩溃也不丢）。"""
    try:
        path = _battle_record_path()
        if path is None:
            return
        with _battle_flush_lock:
            blocks = battle_record.build_blocks(battle_record.records())
            _battle_appender.flush(path, blocks, meta=_battle_meta())
    except Exception:
        pass


def _record_battle(level: str, msg: str) -> None:
    """把一条日志行纳入结构化记录 + 出错反馈判定（任何异常都吞掉）。"""
    try:
        info = battle_record.classify(msg, level)
        battle_record.record(level, msg)
        kind = info.get("kind")
        if kind == "turn":
            _battle_ctx["turn"] = info.get("turn")
            _flush_battle_blocks()
        elif kind == "phase":
            _battle_ctx["phase"] = info.get("phase")
        elif kind == "recommendation":
            if info.get("instruction") != _battle_ctx.get("instruction"):
                feedback.mark_success()
            _battle_ctx["instruction"] = info.get("instruction")
            _battle_ctx["raw"] = []
        elif kind == "recommendation_raw":
            # 重试期间面板没变时同一条原文会被反复打出来，这里去重：
            # 错误.json 的 raw 只需要一份，不然几十次重试能把报告撑爆。
            text = info.get("text") or ""
            raw = _battle_ctx.setdefault("raw", [])
            if text and text not in raw:
                raw.append(text)
        if kind in ("retry", "error", "warn"):
            feedback.note_event(info, _battle_ctx)
    except Exception:
        pass


WEB_DIR = ROOT / "web"
CONFIG_PATH = ROOT / "ui_config.json"
# CLI / agent 用来定位控制台的端口文件：监听端口被占用时会自动顺延
# （8765→8785），写下来比让调用方猜端口/扫端口可靠（消费方见 hsctl.py）。
PORT_FILE = ROOT / "log" / "ui_port.txt"
# 站点/端口/日志缓冲来自 config.py（可通过环境变量覆盖，见 HS_HOST/HS_PORT/HS_LOG_BUFFER_SIZE）。
from config import (
    DEFAULT_AUTO_CONCEDE,
    DEFAULT_AUTO_SURRENDER,
    DEFAULT_HUMAN_LIKE,
    DEFAULT_RANK_STOP,
    DEFAULT_RESIDENT,
    RANK_STOP_ACTIONS,
    HOST,
    BASE_PORT,
    LOG_BUFFER_SIZE,
    _DEPRECATED_DELAY_KEYS,
    _USER_DELAY_KEYS,
    RecommendationConfig,
    box_fallback_settings,
    feedback_settings,
    joint_setting,
    max_search_setting,
    save_box_fallback_setting,
    save_joint_setting,
    save_max_search_setting,
    save_strategy_setting,
    strategy_label,
    strategy_options,
    strategy_setting,
)

# 环境自检（Python 3.12 / 依赖包 / 分辨率缩放）与原样截图区域框预览。
import card_catalog
import screen_regions
import selfcheck
import surrender_rules


# ---------------------------------------------------------------- 管理员检测
def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


IS_ADMIN = _is_admin()


# ---------------------------------------------------------------- 命令行告警横幅
ADMIN_WARNING_TITLE = "当前不是管理员权限，自动化鼠标/键盘操作可能失效"
ADMIN_WARNING_HINT = "请关闭本窗口，用【管理员身份】重新运行：python web_ui.py"


def _enable_console_ansi(stream=None) -> bool:
    """尝试开启 Windows 控制台的 ANSI（VT）转义：成功才用加粗/亮色。

    老 cmd / 输出被重定向到文件时不认 `\\x1b[...` 转义，会把它们打成乱码 ——
    所以先试着打开；打不开就退回纯文本（一样整框 + 重复三遍，只是没法加粗）。
    """
    stream = sys.stdout if stream is None else stream
    try:
        if not stream.isatty():
            return False
    except Exception:
        return False
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_virtual_terminal = 0x0004
        return bool(kernel32.SetConsoleMode(
            handle, mode.value | enable_virtual_terminal))
    except Exception:
        return False


def _print_admin_warning(stream=None, ansi=None, repeat: int = 3) -> None:
    """非管理员时在命令行里把这条告警放大、加粗，并**重复三遍**。

    为什么要这么吵：忽略它就表现为「脚本在跑，但点了没反应」（自动化点击被
    系统拦掉），是用户踩得最多的坑。控制台里没法改字号，只能靠整框 + 块字符
    + 加粗亮色 + 重复来保证一眼看到；不支持 ANSI 的老终端退回纯文本（不会
    把转义序列打成乱码）。
    """
    stream = sys.stdout if stream is None else stream
    if ansi is None:
        ansi = _enable_console_ansi(stream)
    bar = "█" * 64
    color = "\x1b[1;4;91m" if ansi else ""   # 加粗 + 下划线 + 亮红
    border = "\x1b[1;93m" if ansi else ""    # 加粗 + 亮黄
    reset = "\x1b[0m" if ansi else ""
    block = "\n".join((
        f"{border}{bar}{reset}",
        f"{border}██{reset}  {color}⚠️  {ADMIN_WARNING_TITLE}{reset}",
        f"{border}██{reset}      {color}{ADMIN_WARNING_HINT}{reset}",
        f"{border}{bar}{reset}",
    ))
    for index in range(max(1, int(repeat))):
        if index:
            stream.write("\n")
        stream.write(block + "\n")
    stream.flush()


# ---------------------------------------------------------------- 配置持久化
DEFAULT_CONFIG = {
    "name": "",
    "log_root": "",
    "schedule_start": None,
    "schedule_end": None,
    "auto_concede": dict(DEFAULT_AUTO_CONCEDE),
    # 卡组识别投降：和 auto_concede 并存，各自独立开关。
    "auto_surrender": dict(DEFAULT_AUTO_SURRENDER),
    "rank_stop": dict(DEFAULT_RANK_STOP),
    # 守护（原常驻）：统一的自愈配置段；liveness/watchdog 旧段已删除（旧文件里的
    # 键会被忽略，不会被当成配置误用）。
    "resident": dict(DEFAULT_RESIDENT),
}


def load_config() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf8"))
        if not isinstance(data, dict):
            return dict(DEFAULT_CONFIG)
        return {**DEFAULT_CONFIG, **data}
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_config(cfg: dict):
    try:
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf8"
        )
    except Exception as exc:
        _log("WARN", f"配置保存失败：{exc}")


# ---------------------------------------------------------------- 运行日志缓冲
_log_seq = 0
_log_buffer = deque(maxlen=LOG_BUFFER_SIZE)
_log_lock = threading.Lock()

# 运行日志的自动裁剪：文件超过 LOG_TRIM_BYTES 就只留最后 LOG_TRIM_KEEP_LINES 行。
# 老实现是"每次写日志都判断、超了就整文件读一遍再重写"——文件一旦涨到几十 MB，
# 每次 _log 都要读 50MB+，而且多线程并发重写还会互相打断，结果是**只涨不裁**
# （本机实测：ui_log_last.txt 涨到 56MB）。现在：热路径只比一次时间戳，最多
# 每 LOG_TRIM_MIN_INTERVAL 秒才真裁一次，并且只读文件尾部 + 原子替换。
LOG_TRIM_BYTES = 2 * 1024 * 1024
LOG_TRIM_KEEP_LINES = 800
LOG_TRIM_MIN_INTERVAL = 60.0
LOG_TRIM_KEEP_BYTES = 512 * 1024  # 只读尾部这么多字节，足够 800 行
_log_trim_lock = threading.Lock()
_log_trim_at = [0.0]


def _trim_runtime_log(path, now: float) -> bool:
    """把运行日志裁到最后 LOG_TRIM_KEEP_LINES 行（原子替换，返回是否裁了）。

    调用前必须确认已经过了节流间隔；失败一律静默（日志写不进去不该影响主流程）。
    """
    try:
        if path.stat().st_size <= LOG_TRIM_BYTES:
            return False
    except OSError:
        return False
    with _log_trim_lock:
        try:
            size = path.stat().st_size
            if size <= LOG_TRIM_BYTES:
                return False
            with open(path, "rb") as f:
                if size > LOG_TRIM_KEEP_BYTES:
                    f.seek(-LOG_TRIM_KEEP_BYTES, os.SEEK_END)
                tail = f.read()
            lines = tail.decode("utf-8", errors="replace").splitlines(keepends=True)[
                -LOG_TRIM_KEEP_LINES:
            ]
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("".join(lines))
            os.replace(tmp, path)  # 原子替换：读者不会看到半个文件
            _log_trim_at[0] = now
            return True
        except OSError:
            return False


def _log(level: str, msg: str):
    global _log_seq
    with _log_lock:
        _log_seq += 1
        _log_buffer.append(
            {
                "seq": _log_seq,
                "t": datetime.now().strftime("%H:%M:%S"),
                "level": level,
                "msg": str(msg),
            }
        )
    # 合并面板的分块流：**每一行**都先喂给 action_feed（要在下面 _overlay_key
    # 过滤之前：浮窗正文只显示关键行，但分块流需要完整上下文，包括被过滤掉的
    # 「动作 3s 未确认，重新执行…」这类行）。浮窗只负责把 feed 画出来。
    try:
        action_feed.push(str(msg), level)
    except Exception:
        pass
    # 统一推入日志浮窗（关键行），与自动化日志同通道、按时间先后。
    # 浮窗若出现异常绝不能让日志系统把主流程一起拖垮。
    if log_overlay is not None and _overlay_key(str(msg)):
        try:
            log_overlay.push(str(msg), level)
        except Exception:
            pass
    # 结构化分块日志 + 出错自动截图（重试/报错时）。
    _record_battle(level, str(msg))
    # 同时落盘：程序关闭/异常退出后仍可离线查看（诊断不依赖人工复制）。
    try:
        path = RUNTIME_LOG
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [{level}] {msg}\n")
        now = time.time()
        if now - _log_trim_at[0] >= LOG_TRIM_MIN_INTERVAL:
            _trim_runtime_log(path, now)
    except Exception:
        pass


def _overlay_key(line: str) -> bool:
    """这一行要不要进浮窗（浮窗地方小，只放跟挂机直接相关的）。

    **环境自检不进浮窗**（用户 2026-10-26 口径）：自检结论在网页「环境自检」
    卡片里看就够了；浮窗里那几行红字（尤其是「炉石客户区：读不到（炉石未
    运行）」）既占地方又容易被当成挂机故障。它含「炉石」二字，不单独排除的话
    会被下面的关键字匹配放进浮窗。
    """
    keys = (
        "回合",
        "延时",
        "轮到己方",
        "等待",
        "[推荐]",
        "[执行]",
        "识别换牌",
        "换牌",
        "留牌",
        "阶段",
        "对局结束",
        "未对局",
        "本局结束",
        "失败",
        "立即停止",
        # 段位目标（上分停止 / 控分，在选卡组界面读名次）也要能看到检测结果
        "段位",
        "上分",
        "控分",
        # 浮窗上的「打开日志 / 清理日志」结果要能看见
        "日志",
        # 「导出本次出错截屏与日志」的结果（路径）必须进浮窗，否则用户找不到包
        "反馈",
        "截屏",
        "出错",
        # 存活检测 / 昵称校验的告警必须进浮窗（issue 反馈看不到就白做）
        "炉石",
        "昵称",
        "用户 ID",
    )
    return ("[OCR]" not in line
            and "环境自检" not in line
            and any(k in line for k in keys))


# ---------------------------------------------------------------- 环境自检
# 自检结果缓存：页面刷新/轮询不会反复 import numpy、OCR 这些重依赖；
# 页面上的 [重新自检] 按钮传 force=True 才会重跑。
_selfcheck_state = {"result": None, "lock": threading.Lock()}


def run_selfcheck(force: bool = False) -> dict:
    """跑一遍环境自检（Python 版本、依赖包、分辨率/缩放/权限等运行环境）。"""
    with _selfcheck_state["lock"]:
        cached = _selfcheck_state["result"]
    if cached is not None and not force:
        return cached
    try:
        result = selfcheck.run_checks()
    except Exception as exc:  # 自检自己崩了也要给页面一个结论，不能白屏
        traceback.print_exc()
        result = {
            "ok": False,
            "failed": 1,
            "warned": 0,
            "passed": 0,
            "total": 0,
            "summary": f"自检执行失败：{type(exc).__name__}: {exc}",
            "python": f"{sys.version_info.major}.{sys.version_info.minor}."
            f"{sys.version_info.micro}",
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "items": [],
        }
    with _selfcheck_state["lock"]:
        _selfcheck_state["result"] = result
    _log_selfcheck(result)
    return result


def _log_selfcheck(result: dict):
    """自检结论写进控制台日志：❌/⚠️ 逐条提示，方便照着提示修。"""
    _log("SYS", f"环境自检：{result.get('summary', '')}")
    for item in result.get("items", []):
        status = item.get("status")
        if status == selfcheck.STATUS_OK:
            continue
        level = "ERROR" if status == selfcheck.STATUS_FAIL else "WARN"
        text = (
            f"环境自检 {selfcheck.STATUS_ICON.get(status, '')} "
            f"{item.get('label', '')}：{item.get('detail', '')}"
        )
        if item.get("hint"):
            text += f" —— {item['hint']}"
        _log(level, text)


def start_selfcheck_async():
    """脚本启动时后台跑一遍自检：第一次打开脚本就能看到依赖是否齐全。"""

    def _run():
        try:
            run_selfcheck(force=True)
        except Exception:
            pass

    thread = threading.Thread(target=_run, name="hs-selfcheck", daemon=True)
    thread.start()
    return thread


def selfcheck_summary() -> dict:
    """给 /api/status 的精简自检结论（完整明细走 /api/selfcheck）。"""
    with _selfcheck_state["lock"]:
        result = _selfcheck_state["result"]
    if not result:
        return {"checked": False}
    return {
        "checked": True,
        "ok": bool(result.get("ok")),
        "failed": int(result.get("failed", 0)),
        "warned": int(result.get("warned", 0)),
        "summary": result.get("summary", ""),
        "checked_at": result.get("checked_at"),
        "python": result.get("python"),
    }


def api_selfcheck(force: bool = True) -> dict:
    return {"ok": True, "result": run_selfcheck(force=force)}


def api_regions() -> dict:
    """截一张屏幕并画出所有截图区域框（只截图，不点击、不移动鼠标）。"""
    try:
        result = screen_regions.build_region_preview()
    except Exception as exc:
        traceback.print_exc()
        return {"ok": False, "error": f"截图失败：{type(exc).__name__}: {exc}"}
    failed = [
        c for c in result.get("checks", []) if c.get("status") == selfcheck.STATUS_FAIL
    ]
    if failed:
        _log(
            "WARN",
            "截图区域框："
            + "；".join(f"{c.get('label', '')} {c.get('detail', '')}" for c in failed),
        )
    else:
        mapping = result.get("mapping") or {}
        if mapping.get("available"):
            client = mapping.get("client") or {}
            _log(
                "SYS",
                f"截图区域框：客户区 {client.get('width')}×"
                f"{client.get('height')} @屏幕"
                f"{tuple(mapping.get('screen_origin', []))}，已标注 "
                f"{len(result.get('regions', []))} 个区域",
            )
        else:
            _log(
                "SYS",
                f"截图区域框：屏幕 {result['width']}×{result['height']}"
                f"（炉石未运行，客户区未知），已标注 "
                f"{len(result.get('regions', []))} 个区域",
            )
    return {"ok": True, "result": result}


# ---------------------------------------------------------------- 按需抓图（给 agent 看画面）
# 目的：让 agent / 用户能**实时**看到"脚本眼里的画面"。全程只读 —— 不点击、
# 不移动鼠标、不改任何游戏状态。截图在**控制台进程**里做（不是 CLI 里），
# 所以 CLI 是否提权都不影响；而且复用 display_mapping 的客户区几何换算，
# 与 OCR 看到的是同一个口径。
SCREENSHOT_DIR = ROOT / "tmp_screens"
SCREENSHOT_LATEST = SCREENSHOT_DIR / "agent_latest.png"
SCREENSHOT_KINDS = ("client", "full", "roi", "boxes")


def _pillow_region_grabber(box):
    """给 display_mapping.capture_region 用的抓图器：按屏幕框截一块。"""
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=tuple(int(v) for v in box), all_screens=False)


def _hs_client_capture_ready():
    """炉石窗口现在**能不能真的截到客户区**？返回 ``(ok, 说明)``。

    为什么需要这一步：炉石**最小化**时 ``get_HS_hwnd()`` 依然返回有效句柄
    （class=UnityWndClass、标题「炉石传说」），但客户区是空矩形 ``(0,0,0,0)``、
    客户区原点被 Windows 挪到 ``(-32000,-32000)``。这时若还照常截"客户区"，
    得到的是一块没有意义的画面，却会对外自称为"炉石客户区" —— 看图的 agent
    会被误导。所以这里先判定窗口真的可见、客户区非空，否则让调用方退化为整屏
    并在 note 里说清楚原因。
    """
    try:
        import display_mapping
    except Exception as exc:
        return False, f"坐标换算模块不可用：{type(exc).__name__}: {exc}"
    try:
        hwnd = display_mapping.hwnd_of()
        if not hwnd:
            return False, "读不到炉石窗口（炉石未运行）"
        rect = display_mapping.read_client_rect(hwnd)
        if not rect:
            return False, "读不到炉石客户区"
        left, top, right, bottom = (int(v) for v in rect)
        if right - left <= 0 or bottom - top <= 0:
            return (
                False,
                f"炉石窗口不可见或已最小化（客户区 {right - left}×{bottom - top}）",
            )
        origin = display_mapping.client_to_screen(hwnd, 0, 0)
        if origin and (origin[0] <= -30000 or origin[1] <= -30000):
            return False, f"炉石窗口被最小化到屏幕外（{tuple(origin)}）"
        return True, ""
    except Exception as exc:
        return False, f"读炉石窗口状态失败：{type(exc).__name__}: {exc}"


def capture_frame(kind: str = "client"):
    """抓当前一帧，返回 ``(PIL.Image | None, info: dict)``。

    kind:
      * ``client`` —— 只抓炉石**客户区**（默认；"看游戏界面"就用这个）
      * ``full``   —— 整屏（含桌面其它窗口，用来看盒子/浮窗的摆位）
      * ``roi``    —— 只抓盒子推荐面板 ROI（脚本 OCR 真正看到的那一小块）
      * ``boxes``  —— 整屏 + 所有校准区域框（与网页「校准区域」同一张图）

    读不到客户区（炉石没开/最小化/窗口不可用）时 ``client``/``roi`` 会退化成
    整屏并在 info 里说明原因 —— 让调用方至少能看到当前屏幕，而不是拿到一块
    没有意义的画面还以为在看游戏。
    """
    from PIL import Image, ImageGrab

    kind = str(kind or "client").strip().lower()
    if kind not in SCREENSHOT_KINDS:
        raise ValueError(
            f"不支持的抓图类型：{kind}（可选 " + " / ".join(SCREENSHOT_KINDS) + "）"
        )
    info = {"kind": kind}

    if kind == "boxes":
        import base64
        import io

        result = screen_regions.build_region_preview()
        encoded = str(result.get("image") or "").split(",", 1)[-1]
        image = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("RGB")
        info.update(
            {
                "mapping": result.get("mapping"),
                "checks": result.get("checks"),
                "note": "整屏 + 所有校准区域框（同网页「🎯 校准区域」）",
            }
        )
        # 这张图是**整屏**坐标：把客户区点换算成屏幕点的结果一起带出去，
        # 供 --points mode 在这张图上标绘（见 _resolve_client_points）。
        client_box = (result.get("mapping") or {}).get("client_screen_box")
        if client_box:
            origin_x, origin_y = int(client_box[0]), int(client_box[1])
            width = max(1, int(client_box[2]) - origin_x)
            height = max(1, int(client_box[3]) - origin_y)
            scale_x = width / 1920.0
            scale_y = height / 1080.0
            info["marks"] = {
                key: [
                    origin_x + int(round(point[0] * scale_x)),
                    origin_y + int(round(point[1] * scale_y)),
                ]
                for key, point in screen_regions.mode_click_points().items()
            }
        return image, info

    if kind == "full":
        image = ImageGrab.grab(all_screens=False)
        info["note"] = "整屏（主显示器），含桌面其它窗口"
        return image.convert("RGB"), info

    import display_mapping

    ready, why = _hs_client_capture_ready()
    if not ready:
        image = ImageGrab.grab(all_screens=False)
        info.update(
            {"fallback": "full", "reason": why, "note": f"{why}；已退化为整屏截图"}
        )
        return image.convert("RGB"), info

    if kind == "client":
        frame, mapping = display_mapping.capture_region(grabber=_pillow_region_grabber)
        if frame is not None:
            info.update(
                {
                    "mapping": display_mapping.mapping_info(mapping),
                    "client_box": list(mapping.client_box),
                    "note": "炉石客户区（脚本操作的正是这块）",
                }
            )
            return frame.convert("RGB"), info
        why = "客户区抓图返回空帧"

    else:  # roi：与 OCR 同口径 —— box_to_screen(config.recommendation_roi, mapping)
        roi = tuple(RecommendationConfig().recommendation_roi)
        frame, mapping = display_mapping.capture_region(
            region=roi, region_is_client=True, grabber=_pillow_region_grabber
        )
        if frame is not None:
            info.update(
                {
                    "roi": list(roi),
                    "mapping": display_mapping.mapping_info(mapping),
                    "box": list(
                        display_mapping.box_to_screen(roi, mapping, clamp=False)
                    ),
                    "note": "盒子推荐面板 ROI —— 脚本 OCR 真正看到的那一小块",
                }
            )
            return frame.convert("RGB"), info
        why = "推荐面板 ROI 抓图返回空帧"

    image = ImageGrab.grab(all_screens=False)
    info.update({"fallback": "full", "reason": why, "note": f"{why}；已退化为整屏截图"})
    return image.convert("RGB"), info


# ---------------------------------------------------------------- 出错反馈接线
def _feedback_capture(dest_dir, name):
    """feedback 的截图 provider：抓炉石客户区存成 PNG，返回保存路径。

    炉石不可见时 ``capture_frame`` 会退化成整屏并在 info 记原因（仍能拿到画面）。
    任何异常都返回 None，绝不拖垮自动化。
    """
    try:
        image, info = capture_frame("client")
    except Exception:
        return None
    if image is None:
        return None
    try:
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / f"{name}.png"
        image.save(str(path))
    except Exception:
        return None
    return str(path)


def _feedback_env():
    """feedback 反馈包「环境.txt」的补充信息（分辨率/DPI）。"""
    out = {}
    try:
        import display_mapping

        mapping = display_mapping.display_mapping()
        if mapping is not None and mapping.width > 0:
            out["分辨率"] = f"{int(mapping.width)}×{int(mapping.height)}（客户区）"
    except Exception:
        pass
    try:
        out["平台"] = f"{platform.system()} {platform.release()}"
    except Exception:
        pass
    return out


def _feedback_export_and_log(trigger: str = "", open_folder: bool = False) -> dict:
    """导出反馈包并把结果写进日志（浮窗/网页/CLI 共用）。

    ``open_folder=True``（手动导出）时打包成功后打开文件夹并选中 zip，
    用户不用自己找。停止时自动导出不传它，免得无人值守时弹窗。
    """
    result = feedback.export_bundle(open_folder=open_folder)
    prefix = f"（{trigger}）" if trigger else ""
    if result.get("ok"):
        tail = "，已为你打开所在文件夹" if result.get("opened") else ""
        _log(
            "SYS",
            f"已自动截取出错画面 {result.get('screenshots', 0)} 张；"
            f"反馈包{prefix}已存到：{result['path']}"
            f"（{result.get('incidents', 0)} 处异常{tail}）",
        )
    else:
        _log("WARN", f"导出反馈包失败{prefix}：{result.get('error')}")
    return result


feedback.set_capture_provider(_feedback_capture)
feedback.set_env_provider(_feedback_env)


def _target_frame_size() -> tuple:
    """标绘目标图当前会被抓成多大（客户区尺寸，拿不到就按基准 1920×1080）。

    标记按**比例**画，所以抓图退化（客户区→整屏）或后续 --max-width 缩放
    都不会让十字偏位；这个函数只用来算缩放比。
    """
    try:
        import display_mapping

        mapping = display_mapping.display_mapping()
        if mapping is not None and mapping.width > 0 and mapping.height > 0:
            return int(mapping.width), int(mapping.height)
    except Exception:
        pass
    return 1920, 1080


def _resolve_client_points(points: dict, info: dict) -> list:
    """把模式点击点解析成目标图上的像素点。

    ``info["marks"]`` 是**屏幕坐标**（客户区→屏幕的换算结果，boxes 分支会给）：
    这时目标图本身就是整屏（1:1），不需要再按客户区比例缩放，所以标成
    ``space="screen"``；否则返回的是"客户区相对坐标（1920×1080 基准）"，
    由调用方按目标图尺寸缩放。
    """
    marks = info.get("marks")
    if isinstance(marks, dict) and marks:
        out = []
        for key, point in points.items():
            pair = marks.get(key)
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                out.append((key, float(pair[0]), float(pair[1]), "screen"))
        if out:
            return out
    if info.get("fallback") == "full":
        try:
            import display_mapping

            mapping = display_mapping.display_mapping()
            if mapping is not None:
                return [
                    (
                        key,
                        mapping.to_screen(*point)[0],
                        mapping.to_screen(*point)[1],
                        "screen",
                    )
                    for key, point in points.items()
                ]
        except Exception:
            pass
    return [
        (key, float(point[0]), float(point[1]), "reference")
        for key, point in points.items()
    ]


def _draw_mode_points(image, info: dict, points: dict):
    """在抓到的帧上画模式切换十字标记（客户区坐标 → 图像坐标按比例换算）。

    只画图，不点击、不移动鼠标。返回可 JSON 化的标记清单（供 agent 直接判定）。
    """
    from PIL import ImageDraw

    target_w, target_h = _target_frame_size()
    scale_x = (image.width / float(target_w)) if target_w else 1.0
    scale_y = (image.height / float(target_h)) if target_h else 1.0
    draw = ImageDraw.Draw(image)
    font = screen_regions.label_font(16)
    resolved = _resolve_client_points(points, info)
    drawn = []
    for key, x, y, space in resolved:
        # 屏幕坐标的标记：目标图是整屏、1:1，不再缩放（否则会翻倍跑偏）。
        px = x if space == "screen" else x * scale_x
        py = y if space == "screen" else y * scale_y
        size = max(8, int(24 * (scale_x + scale_y) / 2))
        draw.line([(px - size, py), (px + size, py)], fill=(255, 0, 0), width=3)
        draw.line([(px, py - size), (px, py + size)], fill=(255, 0, 0), width=3)
        draw.ellipse([px - 3, py - 3, px + 3, py + 3], outline=(255, 255, 0), width=2)
        label = f"{screen_regions.MODE_POINT_LABEL.get(key, key)} {int(round(x))},{int(round(y))}"
        draw.text((px + size + 2, py + 2), label, fill=(255, 255, 0), font=font)
        drawn.append(
            {
                "key": key,
                "label": screen_regions.MODE_POINT_LABEL.get(key, key),
                "space": space,
                "client": [int(round(x)), int(round(y))],
                "image": [int(round(px)), int(round(py))],
            }
        )
    return drawn


def _save_frame(image, info: dict, out=None) -> dict:
    """把这一帧存成 PNG，并更新固定路径 ``agent_latest.png``。"""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    if out:
        path = Path(str(out))
        if not path.is_absolute():
            path = SCREENSHOT_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path = SCREENSHOT_DIR / f"{info.get('kind', 'shot')}_{stamp}.png"
    image.save(path, format="PNG")
    # 固定路径：agent 可以一直读同一个文件，不用每次解析时间戳。
    latest = None
    try:
        image.save(SCREENSHOT_LATEST, format="PNG")
        latest = str(SCREENSHOT_LATEST)
    except Exception as exc:
        _log("WARN", f"写入固定抓图路径失败：{exc}")
    payload = {
        "ok": True,
        "path": str(path),
        "latest": latest,
        "width": int(image.width),
        "height": int(image.height),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    payload.update({k: v for k, v in info.items() if k != "checks"})
    if info.get("checks"):
        payload["checks"] = info["checks"]
    return payload


def api_screenshot(body=None):
    """按需抓一帧并存成 PNG，返回路径（CLI/agent 用）。

    body: {"kind": client|full|roi|boxes, "max_width": int, "out": "文件名",
           "quiet": bool, "points": ""|"mode"}

    ``points="mode"`` 时在图上画「模式切换」四个点击点的十字标记（只画图，
    不点击、不移动鼠标）—— 用来核对坐标有没有对准盾牌按钮。
    """
    from PIL import Image

    body = body or {}
    try:
        image, info = capture_frame(body.get("kind") or "client")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        traceback.print_exc()
        return {"ok": False, "error": f"抓图失败：{type(exc).__name__}: {exc}"}
    if image is None:
        return {"ok": False, "error": "抓图失败：没有得到画面"}
    try:
        max_width = int(body.get("max_width") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "max_width 必须是整数。"}
    # 标记在缩放**之前**画：画在客户区/屏幕坐标上，之后等比缩放，十字不会偏。
    marks = str(body.get("points") or "").strip().lower()
    if marks and marks != "none":
        if "mode" in marks:
            try:
                info["mode_points"] = _draw_mode_points(
                    image, info, screen_regions.mode_click_points()
                )
                info["points_note"] = (
                    "已标绘模式切换点击点（红十字=实际点击位置，"
                    "客户区坐标 1920×1080 基准）"
                )
            except Exception as exc:
                info["points_error"] = f"{type(exc).__name__}: {exc}"
        else:
            return {"ok": False, "error": f"不支持的 points：{marks}（可选 mode）"}
    if max_width > 0 and image.width > max_width:
        ratio = max_width / float(image.width)
        image = image.resize(
            (max_width, max(1, int(image.height * ratio))), Image.LANCZOS
        )
        info["scaled_to_width"] = max_width
    payload = _save_frame(image, info, out=body.get("out"))
    if not body.get("quiet"):
        # 跟随抓图（--watch）会带上 quiet，免得把网页日志刷满。
        _log(
            "SYS",
            f"抓图：{Path(payload['path']).name}"
            f"（{payload['width']}×{payload['height']}，"
            f"{payload.get('kind')}）",
        )
    return payload


def api_feedback_export(body=None):
    """导出本次运行的错误反馈包（网页/CLI 手动触发），完成后打开文件夹定位 zip。"""
    return _feedback_export_and_log(trigger="手动导出", open_folder=True)


def api_feedback_status():
    """反馈状态快照：本次运行捕获的重试/报错数量与截图数。"""
    snapshot = feedback.status()
    snapshot["settings"] = feedback_settings()
    return snapshot


def take_logs_after(seq: int):
    with _log_lock:
        lines = [e for e in _log_buffer if e["seq"] > seq]
        return lines, _log_seq


# ---------------------------------------------------------------- stdout 捕获
_stdout_original = None


class _TeeStream:
    """把自动化线程的 print 输出同时送进 UI 日志缓冲。"""

    def __init__(self, original):
        self._original = original

    def write(self, text):
        try:
            self._original.write(text)
        except Exception:
            pass
        text = str(text)
        if text and text.strip():
            level = "INFO"
            for lv in ("ERROR", "WARN", "DEBUG", "SYS"):
                if f"{lv}]" in text:
                    level = lv
                    break
            for line in text.splitlines():
                if line.strip():
                    _log(level, line.rstrip())

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass

    def isatty(self):
        return getattr(self._original, "isatty", lambda: False)()

    def fileno(self):
        return getattr(self._original, "fileno", lambda: -1)()


def _stdout_capture_start():
    global _stdout_original
    if _stdout_original is not None:
        return
    _stdout_original = sys.stdout
    sys.stdout = _TeeStream(_stdout_original)


def _stdout_capture_stop():
    global _stdout_original
    if _stdout_original is None:
        return
    try:
        sys.stdout = _stdout_original
    finally:
        _stdout_original = None


# ---------------------------------------------------------------- 控制器
class _Controller:
    def __init__(self):
        self.lock = threading.RLock()
        self.fsm = None
        self.automation_thread = None
        self.scheduler_thread = None
        self.starting = False
        self.phase = "idle"  # idle | waiting | playing | stopping | finished
        self.stopped_by = None  # schedule | user_after_game | user_immediate | ctrlq
        self.schedule = {"start": None, "end": None}
        self.last_error = None
        self.last_summary = None
        self.hotkey_registered = False
        # 已“就绪”（点过「开始运行」：浮窗已开 + 炉石已切前台，但还没真正开始对战）
        self.prepared = False


CTRL = _Controller()


def _apply_constants(name: str, log_root: str):
    """把页面配置注入 constants，并同步到已导入模块的绑定副本。"""
    import constants.constants as cc

    if name:
        cc.YOUR_NAME = name
    if log_root:
        cc.HEARTHSTONE_LOG_ROOT = log_root
    try:
        import log_op
        import log_state

        log_op.HEARTHSTONE_LOG_ROOT = cc.HEARTHSTONE_LOG_ROOT
        log_state.HEARTHSTONE_LOG_ROOT = cc.HEARTHSTONE_LOG_ROOT
        log_state.MY_NAME = cc.YOUR_NAME
    except Exception:
        pass
    if CTRL.fsm is not None:
        CTRL.fsm.HEARTHSTONE_LOG_ROOT = cc.HEARTHSTONE_LOG_ROOT
        CTRL.fsm.log_state.HEARTHSTONE_LOG_ROOT = cc.HEARTHSTONE_LOG_ROOT
        CTRL.fsm.log_state.MY_NAME = cc.YOUR_NAME


def _persist_schedule(start_dt, end_dt):
    cfg = load_config()
    cfg["schedule_start"] = start_dt.isoformat(timespec="minutes") if start_dt else None
    cfg["schedule_end"] = end_dt.isoformat(timespec="minutes") if end_dt else None
    save_config(cfg)


# ---------------------------------------------------------------- 自动化线程
def _reset_score(fsm):
    """战绩清零：只有“重新开始”（网页/浮窗的「开始对战」）才调用。

    浮窗的「恢复」是暂停→续跑，必须保留已累计的场数/胜场/认输数，
    否则一按恢复战绩就归零（用户反馈）。
    """
    fsm.game_count = 0
    fsm.win_count = 0
    fsm.concede_count = 0


def _start_automation(reset_stats: bool = True):
    """启动自动化；reset_stats=False 表示浮窗「恢复」续跑，保留已有战绩。"""
    # 函数入口先通知常驻「马上要（重新）启动」：让暂停期间累积的日志年龄在
    # settle_seconds 宽限期内不参与卡死判定（否则手动开始后第一次 poll 就被
    # 当成日志卡死并触发恢复/装配，和用户抢鼠标）。
    _resident_note_automation_started()
    cfg = load_config()
    name = (cfg.get("name") or "").strip()
    log_root = (cfg.get("log_root") or "").strip()
    try:
        _apply_constants(name, log_root)
        import FSM_action as fsm

        CTRL.fsm = fsm
    except Exception as exc:
        traceback.print_exc()
        return False, f"自动化组件加载失败：{exc}"
    fsm = CTRL.fsm
    # 重置运行状态；每次“打开脚本/开始对战”时战绩清零，从 0 计；
    # 浮窗「恢复」续跑（reset_stats=False）不清零，从上次的场数/胜场继续累加。
    fsm.quitting_flag = False
    fsm.stop_after_current_game = False
    fsm.FSM_state = ""
    fsm.time_begin = 0.0
    if reset_stats:
        _reset_score(fsm)
    try:
        fsm.print_info_init()
        fsm.init()
    except Exception as exc:
        traceback.print_exc()
        return False, f"自动化初始化失败：{exc}"
    CTRL.last_error = None
    CTRL.last_summary = None
    CTRL.stopped_by = None
    CTRL.phase = "playing"
    CTRL.prepared = True
    if reset_stats:
        # 新一轮运行：开新会话（决定结构化日志文件名）+ 清空上次反馈事件。
        _begin_battle_session()
        _log("SYS", f"自动化启动：用户 {name}，日志目录 {log_root}（战绩从 0 开始）")
    else:
        _log(
            "SYS",
            f"自动化恢复：用户 {name}，日志目录 {log_root}"
            f"（战绩继续累计：已完成 {int(getattr(fsm, 'game_count', 0) or 0)} 场）",
        )
    if name and "#" not in name:
        _log(
            "WARN", "用户 ID 未包含 #编号，可能无法识别己方玩家，建议填写完整战网昵称。"
        )
    _stdout_capture_start()
    _register_hotkey()
    t = threading.Thread(
        target=_automation_worker, args=(fsm,), name="hs-automation", daemon=True
    )
    CTRL.automation_thread = t
    t.start()
    return True, "自动化已启动"


def _automation_worker(fsm):
    summary = {"games": 0, "wins": 0}
    _bind_overlay()
    # 自动化在后台线程运行；get_screen 等模块使用 win32com / win32ui（COM），
    # COM 要求线程级初始化，否则报“尚未调用 CoInitialize”并导致线程崩溃。
    com_ready = False
    try:
        import pythoncom

        pythoncom.CoInitialize()
        com_ready = True
    except Exception:
        _log("WARN", "COM 初始化失败（自动化可能仍可运行，若崩溃请检查 pywin32）。")
    try:
        fsm.AutoHS_automata()
    except SystemExit:
        pass
    except Exception:
        tb = traceback.format_exc()
        traceback.print_exc()
        _log("ERROR", "自动化线程异常退出，堆栈如下：")
        for line in tb.rstrip().splitlines():
            _log("ERROR", "  " + line)
        with CTRL.lock:
            CTRL.last_error = "自动化运行异常：请查看页面「实时日志」中的 ERROR 详情。"
    finally:
        _stdout_capture_stop()
        # 热键**不注销**：Ctrl+Q 要在空闲、手动进对战、等界面这些阶段都有效
        # （它由控制台启动时注册，生命周期跟控制台一致，不跟自动化走）。
        try:
            fsm.print_info_close()
        except Exception:
            pass
        with CTRL.lock:
            summary = {
                "games": int(fsm.game_count),
                "wins": int(fsm.win_count),
                "concedes": int(getattr(fsm, "concede_count", 0) or 0),
            }
            # 段位目标=「到达即停」时自动停止：状态机的 stop_after_current_game
            # 置位后线程自己退出，这里把它记成「上分达标」，让页面出横幅
            # （读的是 last_summary["stopped_by"]）。控分（action=keep）不置位这个
            # 标志——它只是反复投降压名次，不会让自动化停止。
            rank_reason = getattr(fsm, "_rank_stop_stop_reason", None)
            rank_reached = bool(
                getattr(fsm, "_rank_stop_triggered", False) and rank_reason
            )
            if rank_reached:
                CTRL.stopped_by = "rank_reached"
                CTRL.phase = "finished"
            CTRL.last_summary = {**summary, "stopped_by": CTRL.stopped_by}
            CTRL.automation_thread = None
            if CTRL.phase == "stopping" and CTRL.stopped_by == "schedule":
                CTRL.phase = "finished"
                CTRL.schedule = {"start": None, "end": None}
                _persist_schedule(None, None)
            elif not rank_reached:
                CTRL.phase = "idle"
        if summary["games"]:
            _log(
                "SYS",
                f"自动化结束：共完成 {summary['games']} 场对战，"
                f"赢 {summary['wins']} 场"
                f"{'，自动认输 ' + str(summary['concedes']) + ' 场' if summary['concedes'] else ''}。",
            )
        else:
            _log("SYS", "自动化结束。")
        # 收尾：补写最后一次回合到 JSONL；按配置可选自动导出反馈包。
        _flush_battle_blocks()
        try:
            if feedback_settings().get("auto_export_on_stop"):
                _feedback_export_and_log(trigger="停止时自动导出")
        except Exception:
            pass
        if com_ready:
            try:
                import pythoncom

                pythoncom.CoUninitialize()
            except Exception:
                pass


# ---------------------------------------------------------------- 热键
def _register_hotkey():
    """注册全局 Ctrl+Q 急停。

    **必须常驻**：以前只在自动化启动时注册、自动化结束就注销，导致手动
    「进入对战」正在连点画面中心时按 Ctrl+Q 毫无反应（用户 2026-10-17 明确
    要求「在任何地方 Ctrl+Q 都要能停」）。现在随控制台启动注册一次，不再随
    自动化生死。
    """
    try:
        import keyboard

        try:
            keyboard.remove_hotkey("ctrl+q")
        except Exception:
            pass
        keyboard.add_hotkey("ctrl+q", _on_ctrl_q)
        CTRL.hotkey_registered = True
        _log("SYS", "热键 Ctrl+Q 已注册（随时急停：自动化 / 手动进对战都停）。")
    except Exception as exc:
        CTRL.hotkey_registered = False
        _log("WARN", f"注册 Ctrl+Q 热键失败：{exc}（仍可用网页/hsctl cancel 取消）")


def _remove_hotkey():
    try:
        import keyboard

        keyboard.remove_hotkey("ctrl+q")
    except Exception:
        pass
    CTRL.hotkey_registered = False


def _on_ctrl_q():
    """Ctrl+Q：全局急停，任何时候按都有效。

    与 `hsctl cancel` / 网页取消走**同一条通道**（`api_cancel`）：叫停正在
    连点的「进入对战 / 等界面」、停掉自动化、清掉命令队列，日志统一记一条。
    """
    try:
        with CTRL.lock:
            CTRL.schedule = {"start": None, "end": None}
        _persist_schedule(None, None)
    except Exception:
        pass
    try:
        api_cancel({"source": "hotkey"})
    except Exception as exc:
        _log("ERROR", f"Ctrl+Q 取消失败：{exc}")


# ---------------------------------------------------------------- 定时调度
# 定时窗口到点后由常驻接管启动：最多等这么久（秒）让自动化线程真正起来，
# 再进入结束时间监控（常驻装配可能要拉起炉石→进对战→选模式→选卡组）。
RESIDENT_SCHEDULE_START_TIMEOUT_SECONDS = 600.0
RESIDENT_SCHEDULE_START_POLL_SECONDS = 0.5


def _resident_window_state() -> tuple:
    """读监督器当前状态与暂停原因（常驻未装配时返回空，按未开启处理）。"""
    supervisor = RESIDENT
    if supervisor is None:
        return "", None
    try:
        state = dict(supervisor.state() or {})
    except Exception:
        return "", None
    return str(state.get("state") or ""), state.get("paused_reason")


def _schedule_clear_plan() -> None:
    """清掉本次定时计划并落盘（跳过/无自动化可停时用，避免计划悬挂成 no-op）。"""
    with CTRL.lock:
        CTRL.phase = "idle"
        CTRL.schedule = {"start": None, "end": None}
    try:
        _persist_schedule(None, None)
    except Exception:
        traceback.print_exc()


def _schedule_skip(message: str, level: str = "WARN") -> bool:
    """到点但不启动：记日志 + 清计划落盘 + 阶段回 idle，统一返回 False。"""
    _log(level, message)
    _schedule_clear_plan()
    return False


def _schedule_delegate_to_resident(state: str) -> bool:
    """定时到点但由常驻接管启动：登记装配待办 + resume，等自动化真正跑起来。

    绝不在这里直接 ``_start_automation``：与常驻同时启动会开出两个自动化线程。
    等待上限 ``RESIDENT_SCHEDULE_START_TIMEOUT_SECONDS``；常驻中途转 paused/
    error 则尊重用户/限流决定，本次窗口不启动。
    """
    _log("SYS", f"定时启动：常驻状态为 {state}，交由常驻接管本次启动……")
    if state == "paused":
        # 上一个定时窗口结束留下的 stopped_by=schedule 是过期标记：监督器读到
        # 它会立刻再转 paused，arm 永远起不来。只清这一种标记；用户手动停止
        # 留下的 stopped_by 走不到这个分支（那是 paused != schedule）。
        with CTRL.lock:
            if CTRL.stopped_by == "schedule":
                CTRL.stopped_by = None
    _resident_set_arm_pending(True)
    _resident_resume()
    deadline = time.monotonic() + max(
        0.0, float(RESIDENT_SCHEDULE_START_TIMEOUT_SECONDS)
    )
    while True:
        with CTRL.lock:
            if CTRL.automation_thread is not None:
                _log("SYS", "定时启动：常驻已把自动化拉起来，继续监控结束时间。")
                return True
        current, _ = _resident_window_state()
        if current in ("paused", "paused_exhausted", "error",
                       "cancelled", "disabled"):
            return _schedule_skip(
                f"定时启动：常驻已转入 {current}，本次定时窗口不启动。")
        if time.monotonic() >= deadline:
            # 超时：监控线程要收尾了，不能让常驻在没人监控结束时才把自动化拉起来。
            _resident_set_arm_pending(False)
            _resident_request_pause("定时启动超时")
            return _schedule_skip(
                "定时启动：等待常驻拉起自动化超时"
                f"（{RESIDENT_SCHEDULE_START_TIMEOUT_SECONDS:.0f}s），本次不启动。",
                "ERROR",
            )
        time.sleep(max(0.0, float(RESIDENT_SCHEDULE_START_POLL_SECONDS)))


def _schedule_wait_automation_thread(start_dt):
    """窗口到点却没有自动化线程可停（常驻正在重启/恢复）时等它回来。

    返回 ``(status, thread)``：status 为 ``"found"`` / ``"cancelled"``（计划被
    取消或替换）/ ``"timeout"``（超过 ``RESIDENT_SCHEDULE_START_TIMEOUT_SECONDS``
    仍没有线程）。等待期间计划被取消立刻退出，不再动任何东西。
    """
    deadline = time.monotonic() + max(
        0.0, float(RESIDENT_SCHEDULE_START_TIMEOUT_SECONDS)
    )
    while True:
        with CTRL.lock:
            if CTRL.schedule.get("start") != start_dt:
                return "cancelled", None
            thread = CTRL.automation_thread
        if thread is not None:
            return "found", thread
        if time.monotonic() >= deadline:
            return "timeout", None
        time.sleep(max(0.0, float(RESIDENT_SCHEDULE_START_POLL_SECONDS)))


def _schedule_legacy_start() -> bool:
    """旧路径：定时到点直接启动自动化（常驻关闭 / dry_run / 非委派状态）。"""
    ok, msg = _start_automation()
    if not ok:
        with CTRL.lock:
            CTRL.phase = "idle"
            CTRL.schedule = {"start": None, "end": None}
            CTRL.last_error = msg
        _persist_schedule(None, None)
        _log("ERROR", f"定时启动失败：{msg}")
        return False
    return True


def _schedule_start_at_window() -> bool:
    """到点启动的四个分支（返回 False = 本次窗口不启动，调度线程收尾）。

    a. 自动化已在运行/正在启动 → 跳过启动（防双线程），继续结束监控；
    b. 常驻在等这个窗口（waiting_schedule/countdown）或上一定时窗口结束暂停
       → 委派常驻 arm + resume，等自动化起来；
    c. 常驻被用户暂停/急停/耗尽/error → 不启动，只告警；
    d. 其余（常驻关闭 / dry_run）→ 旧 ``_start_automation`` 路径。
    """
    _log("SYS", "到达计划开始时间，自动启动对战。")
    with CTRL.lock:
        running = CTRL.automation_thread is not None
        starting = bool(CTRL.starting)
    if running or starting:
        _log(
            "SYS",
            "定时启动：自动化已在运行/正在启动，跳过本次启动（不再开第二个线程）。",
        )
        return True
    settings = _resident_settings()
    resident_on = bool(settings.get("enabled"))
    dry_run = bool(settings.get("dry_run"))
    if not resident_on:
        return _schedule_legacy_start()
    state, paused_reason = _resident_window_state()
    if not dry_run:
        if (
            state in ("waiting_schedule", "countdown")
            or (state == "paused" and str(paused_reason or "") == "schedule")
        ):
            return _schedule_delegate_to_resident(state)
        if state in ("paused", "paused_exhausted", "error"):
            return _schedule_skip(
                "定时启动：常驻已暂停，本次定时窗口不启动；"
                "如需运行请 hsctl resident on。"
            )
    # dry_run（或常驻不在委派/暂停分支）统一走旧路径：dry_run 时常驻不派发，
    # 定时任务必须照旧自己启动，不能因为“常驻 paused”就整窗跳过。
    return _schedule_legacy_start()


def _schedule_worker(start_dt: datetime, end_dt):
    try:
        while datetime.now() < start_dt:
            with CTRL.lock:
                if CTRL.schedule.get("start") != start_dt:
                    return  # 计划被取消或替换
            time.sleep(0.5)
        if not _schedule_start_at_window():
            return
        if end_dt is None:
            return
        # 窗口内只按时间 + 计划取消/替换判断，**不看 automation_thread**：
        # 常驻自愈/重启时线程会短暂为 None，不能把它当成“窗口结束”而提前退出。
        while datetime.now() < end_dt:
            with CTRL.lock:
                if CTRL.schedule.get("start") != start_dt:
                    return
            time.sleep(0.5)
        _log("SYS", "到达计划结束时间：等待本局对战打完再停止。")
        with CTRL.lock:
            CTRL.phase = "stopping"
            CTRL.stopped_by = "schedule"
        status, _thread = _schedule_wait_automation_thread(start_dt)
        if status == "cancelled":
            return
        if status == "timeout":
            # 窗口内一次自动化都没拉起来：没有可停的线程，清计划避免悬挂 no-op。
            _schedule_clear_plan()
            _log(
                "WARN",
                "到达计划结束时间，但 "
                f"{RESIDENT_SCHEDULE_START_TIMEOUT_SECONDS:.0f}s 内没有自动化线程可停"
                "（常驻未拉起自动化）；已取消本次定时计划。",
            )
            return
        try:
            CTRL.fsm.request_stop_after_game()
        except Exception as exc:
            _log("ERROR", f"发送停止请求失败：{exc}")
        # 等待自动化线程自然退出（对局结束后）
        while True:
            with CTRL.lock:
                if CTRL.schedule.get("start") != start_dt:
                    return
                t = CTRL.automation_thread
            if t is None or not t.is_alive():
                break
            time.sleep(1)
    except Exception:
        traceback.print_exc()


# ---------------------------------------------------------------- API 实现
STATE_LABELS = {
    "": "待机",
    "Leave Hearth Stone": "炉石未运行",
    "Wait main menu": "等待主菜单",
    "Main Menu": "主菜单",
    "Choosing Hero": "选择职业",
    "Match Opponent": "匹配对手",
    "Choosing Card": "换牌阶段",
    "Battling": "对战中",
    "Quitting Battle": "对局结算",
    "ERROR": "状态异常",
}


# ------------------------------------------------- 热更新通道（CLI / agent 用）
# 网页上的「延时设置」「活人感」等卡片在自动化运行中是**禁止修改**的（保护性
# 设计：避免用户对局中途手滑改坏参数）。但 CLI / agent 需要实时控制能力，所以
# 这里给 API 开一条显式通道：请求体里带 "hot": true 才跳过那道守卫。
# 网页不带这个字段，行为与以前完全一致。
def _hot_requested(body) -> bool:
    """请求体里是否显式要求「运行中也改」（{"hot": true}）。"""
    try:
        return bool((body or {}).get("hot"))
    except Exception:
        return False


# 延时字段的生效时机（实测结论，别照抄成"全部立即生效"）：
#   * live      —— FSM_action 在调用点读 recommendation_config.<字段>，就地改写
#                  内存里的实例即可立刻生效；
#   * file      —— 调用方每次都新建 RecommendationConfig()（重新读 ui_config.json），
#                  所以只要落盘就立刻生效；
#   * next_start—— 在 initialize_recommendation_automation() 里被**按值**快照进
#                  MulliganFlow / RecommendationFlow 的构造参数，要等下一次
#                  「开始对战」（fsm.init()）才生效；
#   * restart   —— src/ocr/preprocess.py 在**模块导入期**就把值算成常量 _SCALE，
#                  连重开自动化都改不动，必须重启脚本进程。
# 注意：旧版「每张开局卡延时 / 每回合前延时」已废弃（R18 起我方回合就绪改判
# 右侧「结束回合」按钮的 OCR 出现），它们不在本表里，也不在 _USER_DELAY_KEYS
# 白名单里（见 config._DEPRECATED_DELAY_KEYS）。
_DELAY_LIVE_KEYS = frozenset(
    {
        "mulligan_ready_delay_seconds",
        "mulligan_retry_delay_seconds",
    }
)
_DELAY_FILE_KEYS = frozenset(
    {
        "discover_target_delay_seconds",
    }
)
_DELAY_NEXT_START_KEYS = frozenset(
    {
        "mulligan_post_ocr_delay_seconds",
        "post_action_delay_seconds",
        "draw_extra_delay_per_card_seconds",
    }
)
_DELAY_RESTART_KEYS = frozenset(
    {
        "ocr_preprocess_scale",
    }
)


def _delay_effect_scope(key: str) -> str:
    """某个延时字段改完什么时候生效（给 CLI 回话用）。"""
    if key in _DELAY_LIVE_KEYS or key in _DELAY_FILE_KEYS:
        return "live"
    if key in _DELAY_NEXT_START_KEYS:
        return "next_start"
    return "restart"


def _apply_delays_live(changes: dict) -> dict:
    """把延时改动**就地**写进正在运行的 RecommendationConfig。

    RecommendationConfig 是 frozen dataclass，正常 setattr 会抛
    FrozenInstanceError；这里用 object.__setattr__ 绕过冻结是**有意**的：
    FSM_action 与若干流程对象共享同一个实例，就地改写才能让它们当场看到新值
    （重新 new 一个再绑回 FSM_action.recommendation_config 只能改到模块全局，
    已经被流程对象按值/按引用拿走的旧实例不会跟着变）。

    返回 {字段: 生效时机}；没有正在运行的自动化时返回 {}（下次开始自然读盘）。
    """
    if not changes:
        return {}
    try:
        import FSM_action
    except Exception:
        return {}
    cfg = getattr(FSM_action, "recommendation_config", None)
    if cfg is None:
        return {}
    applied = {}
    for key, value in changes.items():
        try:
            object.__setattr__(cfg, key, value)
        except Exception as exc:
            _log("WARN", f"热更新 {key} 失败（重启脚本后生效）：{exc}")
            continue
        applied[key] = _delay_effect_scope(key)
    return applied


def api_save_config(body: dict):
    name = str(body.get("name") or "").strip()
    log_root = str(body.get("log_root") or "").strip()
    warnings = []
    if name and "#" not in name:
        warnings.append("建议填写完整战网昵称（含 #编号），否则可能无法识别己方玩家。")
    if log_root and not os.path.isdir(log_root):
        warnings.append(
            "日志目录不存在，请确认路径正确（通常为炉石安装目录下的 Logs 文件夹）。"
        )
    with CTRL.lock:
        if CTRL.automation_thread is not None and not _hot_requested(body):
            return {"ok": False, "error": "自动化运行中，请先停止后再修改配置。"}
        cfg = load_config()
        cfg["name"] = name
        cfg["log_root"] = log_root
        save_config(cfg)
        _apply_constants(name, log_root)
    _log(
        "SYS",
        f"配置已保存：用户 {name or '（未填写）'}，日志目录 {log_root or '（未填写）'}",
    )
    return {"ok": True, "message": "配置已保存", "warnings": warnings}


def api_save_concede(body):
    """保存自动投降配置（ui_config.json 的 auto_concede 段）。"""
    with CTRL.lock:
        if CTRL.automation_thread is not None and not _hot_requested(body):
            return {
                "ok": False,
                "error": "自动化运行中，请先停止后再修改自动投降配置。",
            }
        cfg = load_config()
        ac = dict(cfg.get("auto_concede") or DEFAULT_AUTO_CONCEDE)
        ac["enabled"] = bool(
            body.get("enabled", ac.get("enabled", DEFAULT_AUTO_CONCEDE["enabled"]))
        )
        try:
            threshold = float(
                body.get(
                    "threshold", ac.get("threshold", DEFAULT_AUTO_CONCEDE["threshold"])
                )
            )
        except (TypeError, ValueError):
            return {"ok": False, "error": "阈值必须为数字（0-100）。"}
        threshold = max(0.0, min(100.0, threshold))
        try:
            rounds = int(
                body.get("rounds", ac.get("rounds", DEFAULT_AUTO_CONCEDE["rounds"]))
            )
        except (TypeError, ValueError):
            return {"ok": False, "error": "连续回合数必须为整数（1-50）。"}
        rounds = max(1, min(50, rounds))
        ac["threshold"] = threshold
        ac["rounds"] = rounds
        cfg["auto_concede"] = ac
        save_config(cfg)
    _log(
        "SYS",
        f"自动投降配置已保存：{'开启' if ac['enabled'] else '关闭'}，"
        f"阈值 {threshold:.0f}%，连续 {rounds} 回合。",
    )
    return {
        "ok": True,
        "message": "自动投降配置已保存",
        "concede": {"enabled": ac["enabled"], "threshold": threshold, "rounds": rounds},
    }


# ---------------------------------------------------------------- 卡组识别投降
def api_card_search(query, limit=8):
    """卡牌自动补全：中文名包含匹配（本地 cards.json，不联网）。

    同名多版本（神秘访客 = REV_246 / CORE_REV_246）合并成一条，
    返回的 ``ids`` 里是全版本，网页只展示主 id。
    """
    text = (query or "").strip()
    try:
        limit = max(1, min(20, int(limit)))
    except (TypeError, ValueError):
        limit = 8
    catalog = card_catalog.default_catalog()
    if catalog is None:
        return {"ok": False, "error": f"卡牌库不可用：{card_catalog.catalog_error()}"}
    if not text:
        return {"ok": True, "query": text, "cards": []}
    hits = catalog.search(text, limit=limit)
    return {"ok": True, "query": text, "cards": [hit.to_dict() for hit in hits]}


def _current_log_state():
    fsm = CTRL.fsm
    return getattr(fsm, "log_state", None) if fsm is not None else None


def api_save_surrender_rules(body):
    """保存卡组识别投降配置（ui_config.json 的 auto_surrender 段）。

    **允许自动化运行中热保存**（改完立即生效），代价是可能当场命中：
    所以真会触发时返回 ``needs_confirm``，要网页带上 ``confirm: true`` 再来一次。
    校验不过（卡名找不到 / 结构写错）一律不写盘——保留上一份能用的配置。
    """
    raw = body.get("config")
    if not isinstance(raw, dict):
        raw = {key: body[key] for key in ("enabled", "aliases", "decks") if key in body}
    # 只切开关、没带 decks 时，沿用已保存的卡组库。
    if "decks" not in raw:
        saved = load_config().get("auto_surrender") or {}
        merged = dict(saved)
        merged.update(raw)
        raw = merged

    normalized = surrender_rules.normalize_config(raw)
    if not normalized["ok"]:
        return {
            "ok": False,
            "error": "配置有问题，未保存",
            "errors": normalized["errors"],
        }

    decks, compile_errors, corrected = surrender_rules.compile_config(
        normalized["config"]
    )
    if compile_errors:
        return {"ok": False, "error": "卡名有误，未保存", "errors": compile_errors}

    preview = surrender_rules.engine.preview(normalized["config"], _current_log_state())
    if preview.get("would_fire_now") and not body.get("confirm"):
        return {
            "ok": False,
            "needs_confirm": True,
            "error": "按这份配置，当前这一局会立刻投降。确认要保存吗？",
            "hit": preview.get("hit"),
            "config": normalized["config"],
            "warnings": normalized["warnings"],
            "corrected": corrected,
        }

    cfg = load_config()
    cfg["auto_surrender"] = normalized["config"]
    save_config(cfg)

    enabled = bool(normalized["config"].get("enabled"))
    _log(
        "SYS",
        f"卡组识别投降已保存：{'开启' if enabled else '关闭'}，{len(decks)} 套卡组。",
    )
    for item in corrected:
        _log("WARN", f"卡名已自动纠正：{item['from']} → {item['to']}")
    for warning in normalized["warnings"]:
        _log("WARN", f"卡组识别投降：{warning}")
    return {
        "ok": True,
        "message": "卡组识别投降已保存" + ("（已生效）" if enabled else ""),
        "config": normalized["config"],
        "warnings": normalized["warnings"],
        "corrected": corrected,
        "would_fire_now": bool(preview.get("would_fire_now")),
    }


def _surrender_state():
    """给 /api/status：当前这一局的实时进度（对方职业 / 每套卡组满足几项）。"""
    fsm = CTRL.fsm
    if fsm is None:
        return {"decks": [], "message": "自动化未启动"}
    try:
        return fsm.surrender_rule_state().get("state", {})
    except Exception as exc:
        return {"decks": [], "error": str(exc)}


# ------------------------------------------------------------------ 段位目标（可选）
def _current_rank_stop() -> dict:
    """返回当前生效的「段位目标」配置（默认值叠加 ui_config.json 的 rank_stop 段）。

    action="stop"：上分停止（名次达标即停）；
    action="keep"：控分（名次太靠前就开局投降压低）。
    """
    cfg = dict(DEFAULT_RANK_STOP)
    saved = load_config().get("rank_stop")
    if isinstance(saved, dict):
        for key in cfg:
            if saved.get(key) is not None:
                cfg[key] = saved[key]
    cfg["enabled"] = bool(cfg["enabled"])
    if cfg["mode"] not in ("legend", "legend_number"):
        cfg["mode"] = DEFAULT_RANK_STOP["mode"]
    if cfg["action"] not in RANK_STOP_ACTIONS:
        cfg["action"] = DEFAULT_RANK_STOP["action"]
    try:
        cfg["legend_number"] = int(cfg["legend_number"])
    except (TypeError, ValueError):
        cfg["legend_number"] = DEFAULT_RANK_STOP["legend_number"]
    cfg["legend_number"] = max(1, min(100000, cfg["legend_number"]))
    return cfg


def api_save_rank_stop(body):
    """保存「段位目标」（ui_config.json 的 rank_stop 段）。

    在选择套牌界面读一次名次，按 action 决定动作：
      stop：名次达标（数值 <= 目标）→ 本局结束后停止自动化；
      keep：名次太靠前（数值 <  目标）→ 开局即投降把名次压低，直到不超标。
    """
    with CTRL.lock:
        if CTRL.automation_thread is not None and not _hot_requested(body):
            return {"ok": False, "error": "自动化运行中，请先停止后再修改段位目标。"}
        cfg = load_config()
        rank = _current_rank_stop()
        rank["enabled"] = bool(body.get("enabled", rank["enabled"]))
        action = str(body.get("action") or rank["action"]).strip()
        if action not in RANK_STOP_ACTIONS:
            return {
                "ok": False,
                "error": "处理方式只能选「达到后停止」或「控分（不超过该名次）」。",
            }
        rank["action"] = action
        mode = str(body.get("mode") or rank["mode"]).strip()
        if mode not in ("legend", "legend_number"):
            return {
                "ok": False,
                "error": "停止方式只能选「上传说」或「上到传说某个名次」。",
            }
        rank["mode"] = mode
        try:
            number = int(body.get("legend_number", rank["legend_number"]))
        except (TypeError, ValueError):
            return {"ok": False, "error": "传说名次必须为整数（1-100000）。"}
        rank["legend_number"] = max(1, min(100000, number))
        cfg["rank_stop"] = rank
        save_config(cfg)
    if not rank["enabled"]:
        detail = "已关闭"
    elif rank["action"] == "keep":
        detail = f"控分：名次优于第 {rank['legend_number']} 名时开局投降（名次数值 < {rank['legend_number']}）"
    elif rank["mode"] == "legend":
        detail = "上传说即停"
    else:
        detail = f"上到传说第 {rank['legend_number']} 名或更好时停"
    _log("SYS", f"段位目标已保存：{detail}。")
    return {"ok": True, "message": "段位目标已保存", "rank_stop": rank}


def _rank_stop_state() -> dict:
    """段位目标的显示状态：自动化已加载时取状态机实时值，否则用配置兜底。"""
    with CTRL.lock:
        fsm = CTRL.fsm
    if fsm is not None and hasattr(fsm, "rank_stop_detection_state"):
        try:
            return fsm.rank_stop_detection_state()
        except Exception:
            pass
    cfg = _current_rank_stop()
    return {
        "enabled": cfg["enabled"],
        "mode": cfg["mode"],
        "action": cfg.get("action", "stop"),
        "legend_number": cfg["legend_number"],
        "last_number": None,
        "last_legend": None,
        "last_raw": None,
        "last_empty": False,
        "triggered": False,
        "stop_reason": None,
        "keep_count": 0,
    }


# ------------------------------------------------------------------ 活人感（可选）
def _current_human_like() -> dict:
    """返回当前生效的活人感配置（默认值叠加 ui_config.json 的 human_like 段）。"""
    cfg = dict(DEFAULT_HUMAN_LIKE)
    saved = load_config().get("human_like")
    if isinstance(saved, dict):
        for key in cfg:
            if saved.get(key) is not None:
                cfg[key] = saved[key]
    return cfg


def api_save_human_like(body):
    """保存活人感配置（ui_config.json 的 human_like 段）。"""
    with CTRL.lock:
        if CTRL.automation_thread is not None and not _hot_requested(body):
            return {"ok": False, "error": "自动化运行中，请先停止后再修改活人感配置。"}
        cfg = load_config()
        hl = _current_human_like()
        hl["enabled"] = bool(body.get("enabled", hl["enabled"]))
        # 「看卡牌」「看随从」两个独立开关：关掉看卡牌 = 连随机延时一起关，
        # 关掉看随从 = 不再对着新随从动鼠标。
        hl["hand_hover_enabled"] = bool(
            body.get("hand_hover_enabled", hl["hand_hover_enabled"])
        )
        hl["minion_hover_enabled"] = bool(
            body.get("minion_hover_enabled", hl["minion_hover_enabled"])
        )
        try:
            lo = float(body.get("post_delay_min", hl["post_delay_min"]))
            hi = float(body.get("post_delay_max", hl["post_delay_max"]))
            h_lo = float(body.get("hover_min", hl["hover_min"]))
            h_hi = float(body.get("hover_max", hl["hover_max"]))
            m_lo = float(body.get("minion_hover_min", hl["minion_hover_min"]))
            m_hi = float(body.get("minion_hover_max", hl["minion_hover_max"]))
        except (TypeError, ValueError):
            return {"ok": False, "error": "延时时长/悬停时长必须为数字。"}
        lo = max(0.0, min(60.0, lo))
        hi = max(lo, min(60.0, hi))
        h_lo = max(0.05, min(30.0, h_lo))
        h_hi = max(h_lo, min(30.0, h_hi))
        m_lo = max(0.05, min(30.0, m_lo))
        m_hi = max(m_lo, min(30.0, m_hi))
        hl.update(
            {
                "post_delay_min": lo,
                "post_delay_max": hi,
                "hover_min": h_lo,
                "hover_max": h_hi,
                "minion_hover_min": m_lo,
                "minion_hover_max": m_hi,
            }
        )
        hl.pop("hover_seconds", None)  # 旧的固定悬停字段不再使用
        cfg["human_like"] = hl
        save_config(cfg)
    hand_on = "开" if hl["hand_hover_enabled"] else "关"
    minion_on = "开" if hl["minion_hover_enabled"] else "关"
    _log(
        "SYS",
        f"活人感配置已保存：{'开启' if hl['enabled'] else '关闭'}"
        f"（看卡牌 {hand_on} · 随机延时 {lo:.1f}~{hi:.1f}s"
        f" · 悬停 {h_lo:.1f}~{h_hi:.1f}s；"
        f"看随从 {minion_on} · {m_lo:.1f}~{m_hi:.1f}s）。",
    )
    return {"ok": True, "message": "活人感配置已保存", "human_like": hl}


# ------------------------------------------------------------------ 生存信号（守护的安全网）
# 2026-11-01 三卡合并：「🩺 存活检测」的独立开关/配置段已删除。进程消失/日志停滞
# 的阈值统一来自「守护」（config.resident_settings 的 warn_seconds 与
# stale_in_game_seconds），检测本身见 src/safety/hearthstone_liveness.py，处置见
# FSM_action.check_hearthstone_liveness（守护动手时让位，否则告警+停止）。


# ------------------------------------------------------------------ 延时设置
# 各延时字段的边界与默认值（默认值取自 RecommendationConfig，即上游时序）。
# 只收 _USER_DELAY_KEYS 里的字段：废弃的固定回合开始延时不在其中（见
# config._DEPRECATED_DELAY_KEYS），网页 / CLI 也就点不到它们。
_DELAY_BOUNDS = {
    "mulligan_ready_delay_seconds": (0.0, 120.0),
    "mulligan_post_ocr_delay_seconds": (0.0, 30.0),
    "mulligan_retry_delay_seconds": (0.0, 60.0),
    "post_action_delay_seconds": (0.0, 10.0),
    "draw_extra_delay_per_card_seconds": (0.0, 20.0),
    "discover_target_delay_seconds": (0.0, 5.0),
    "ocr_preprocess_scale": (0.5, 4.0),
}


def _current_delays() -> dict:
    """返回当前生效的延时配置（默认值叠加 ui_config.json 的 delays 段）。"""
    return {key: getattr(RecommendationConfig(), key) for key in _USER_DELAY_KEYS}


def api_save_delays(body):
    """保存延时配置（ui_config.json 的 delays 段）。

    带 "hot": true 时允许在自动化运行中修改，并**就地**改写正在运行的
    RecommendationConfig（见 _apply_delays_live）。返回值的 effects 说明每个
    字段的实际生效时机：
        live       —— 立刻生效（内存实例已改写，或调用方每次重新读盘）
        next_start —— 下一次「开始对战」生效（启动时按值快照进流程对象）
        restart    —— 需重启脚本进程（OCR 预处理在模块导入期取常量）
    """
    hot = _hot_requested(body)
    with CTRL.lock:
        if CTRL.automation_thread is not None and not hot:
            return {"ok": False, "error": "自动化运行中，请先停止后再修改延时配置。"}
        cfg = load_config()
        # 顺手清掉旧配置里残留的废弃字段：它们不生效，留着只会让网页/CLI
        # 显示一个“调了也没用”的值（旧版固定回合开始延时，见
        # config._DEPRECATED_DELAY_KEYS）。
        delays = {
            key: value
            for key, value in (cfg.get("delays") or {}).items()
            if key not in _DEPRECATED_DELAY_KEYS
        }
        changes = {}
        for key in _USER_DELAY_KEYS:
            if key not in body:
                continue
            try:
                value = float(body[key])
            except (TypeError, ValueError):
                return {"ok": False, "error": f"{key} 必须为数字。"}
            lo, hi = _DELAY_BOUNDS[key]
            if not (lo <= value <= hi):
                return {"ok": False, "error": f"{key} 必须介于 {lo}–{hi}。"}
            delays[key] = value
            changes[key] = value
        cfg["delays"] = delays
        save_config(cfg)
        running = CTRL.automation_thread is not None
    # 就地热更新的返回值同时充当"生效时机表"（没在跑时为空，走 _delay_effect_scope）。
    live_applied = _apply_delays_live(changes) if (hot and running) else {}
    effects = {key: live_applied.get(key, _delay_effect_scope(key)) for key in changes}
    if changes:
        _log(
            "SYS",
            "延时配置已保存："
            + "、".join(
                f"{key}={value:g}（{effects[key]}）" for key, value in changes.items()
            )
            + "。",
        )
    else:
        _log("SYS", "延时配置已保存。")
    result = {"ok": True, "message": "延时配置已保存", "delays": _current_delays()}
    if effects:
        result["effects"] = effects
        if any(scope != "live" for scope in effects.values()):
            result["note"] = (
                "标 next_start 的字段要等下一次「开始对战」才生效；"
                "标 restart 的字段要重启脚本进程。"
            )
    return result


# ------------------------------------------------------------------ 策略选择
# 网页「基础配置 → 策略选择」：盒子AI（默认）/ ai/ 下自动发现的本地卡组。
# 三项设置都写 ui_config.json（strategy / joint / max_search 键）。策略与联合模式
# 在「开始对战」时被快照进推荐流程（每局重建），所以只对下一局生效；max_search
# 每次搜索前重新读盘，落盘后下一次搜索就用新值。运行中的保护与其它卡片一致：
# 默认禁止修改，CLI / agent 传 {"hot": true} 才放行。
def api_save_strategy(body):
    """保存策略选择（ui_config.json 的 strategy 键）。"""
    body = body or {}
    key = body.get("strategy")
    if not isinstance(key, str) or not key:
        return {"ok": False, "error": "请选择策略。"}
    with CTRL.lock:
        if CTRL.automation_thread is not None and not _hot_requested(body):
            return {
                "ok": False,
                "error": "自动化运行中，请先停止后再修改策略（改完下一局生效）。",
            }
        try:
            saved = save_strategy_setting(key)
        except KeyError:
            return {"ok": False, "error": f"未知策略：{key}"}
        label = strategy_label(saved)
    _log("SYS", f"策略选择已保存：{label}（下一局生效）。")
    return {
        "ok": True,
        "message": f"策略已保存：{label}（下一局生效）",
        "strategy": {"key": saved, "label": label},
    }


def api_save_joint(body):
    """保存联合模式开关（ui_config.json 的 joint 键，只有本地卡组时才生效）。"""
    body = body or {}
    with CTRL.lock:
        if CTRL.automation_thread is not None and not _hot_requested(body):
            return {
                "ok": False,
                "error": "自动化运行中，请先停止后再修改联合模式（改完下一局生效）。",
            }
        enabled = save_joint_setting(body.get("joint", joint_setting()))
    _log(
        "SYS",
        f"联合模式已{'开启' if enabled else '关闭'}"
        "（本地卡组时读盒子首步比较，下一局起生效）。",
    )
    return {
        "ok": True,
        "message": f"联合模式已{'开启' if enabled else '关闭'}（下一局生效）",
        "joint": enabled,
    }


def api_save_max_search(body):
    """保存最大搜索深度（ui_config.json 的 max_search 键，1..100000）。"""
    body = body or {}
    with CTRL.lock:
        if CTRL.automation_thread is not None and not _hot_requested(body):
            return {
                "ok": False,
                "error": "自动化运行中，请先停止后再修改最大搜索深度。",
            }
        try:
            depth = save_max_search_setting(
                body.get("max_search", max_search_setting())
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc) or "max_search 必须是整数。"}
    _log("SYS", f"最大搜索深度已保存：{depth}（下一次搜索起生效）。")
    return {
        "ok": True,
        "message": f"最大搜索深度已保存：{depth}",
        "max_search": depth,
    }


def api_save_box_fallback(body):
    """保存盒子推荐失效兜底（ui_config.json 的 box_fallback 键，R18K）。"""
    body = body or {}
    with CTRL.lock:
        if CTRL.automation_thread is not None and not _hot_requested(body):
            return {
                "ok": False,
                "error": "自动化运行中，请先停止后再修改盒子兜底（改完下一局生效）。",
            }
        current = box_fallback_settings()
        try:
            saved = save_box_fallback_setting(
                enabled=body.get("enabled", current["enabled"]),
                strategy=body.get("strategy", current["strategy"]),
                fail_streak=body.get("fail_streak", current["fail_streak"]),
                stall_seconds=body.get(
                    "stall_seconds", current["stall_seconds"]),
                mulligan_fail_streak=body.get(
                    "mulligan_fail_streak", current["mulligan_fail_streak"]),
            )
        except KeyError as exc:
            return {"ok": False, "error": str(exc)}
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc) or "盒子兜底配置不合法。"}
    label = strategy_label(saved["strategy"])
    state_text = "开启" if saved["enabled"] else "关闭"
    _log("SYS", f"盒子推荐失效兜底已{state_text}（本地「{label}」，下一局生效）。")
    return {
        "ok": True,
        "message": f"盒子兜底已{state_text}：本地「{label}」（下一局生效）",
        "box_fallback": {**saved, "strategy_label": label},
    }


def api_start(body: dict):
    body = body or {}
    # 有人显式启动了自动化（网页/浮窗/CLI）：常驻的“装配待办”已无意义，清掉，
    # 免得下次自动化停掉时又跑一遍进对战的装配流程。
    _resident_set_arm_pending(False)
    # 浮窗「恢复」续跑：keep_score=True 时保留已累计战绩（场数/胜场/认输数），
    # 只有“重新开始”才从 0 计。
    keep_score = bool(body.get("keep_score"))
    # 内部 key：常驻在定时窗口内启动自动化时传 True，表示**不要**清掉定时计划
    # （否则常驻刚被定时窗口拉起来就把窗口自己取消了）。默认 False 保持旧语义：
    # 网页/浮窗/CLI 的手动「开始」仍然取消尚未开始的定时任务。
    keep_schedule = bool(body.get("keep_schedule"))
    with CTRL.lock:
        if CTRL.automation_thread is not None:
            return {"ok": False, "error": "自动化已经在运行中。"}
        if CTRL.starting:
            return {"ok": False, "error": "自动化正在启动中，请稍候……"}
        cfg = load_config()
        name = (cfg.get("name") or "").strip()
        log_root = (cfg.get("log_root") or "").strip()
        if not name:
            return {"ok": False, "error": "请先填写用户 ID。"}
        if not log_root:
            return {"ok": False, "error": "请先填写炉石日志目录。"}
        if not os.path.isdir(log_root):
            return {"ok": False, "error": f"日志目录不存在：{log_root}"}
        # 手动开始会取消尚未开始的定时计划（常驻内部启动除外，见 keep_schedule）
        if not keep_schedule:
            CTRL.schedule = {"start": None, "end": None}
        # 从这一刻到 _start_automation 真正把自动化线程挂上之前，网页/浮窗要显示
        # 「启动中…」（以前这个标志从未置位，等于没有），并挡住并发的第二次点击。
        CTRL.starting = True
    # 立刻通知常驻“新一轮启动开始了”（不要等 boot 线程进 _start_automation）：
    # 把「starting 已生效、settle 还没刷新」这段极窄窗口也纳入宽限，避免暂停期间
    # 累积的大日志年龄在这一瞬间被判成卡死。
    _resident_note_automation_started()
    try:
        if not keep_schedule:
            _persist_schedule(None, None)
    except Exception:
        with CTRL.lock:
            CTRL.starting = False
        raise
    if keep_score:
        _log("SYS", "收到恢复请求，正在续跑自动化（战绩继续累计，不清零）……")
    else:
        _log("SYS", "收到启动请求，正在初始化自动化组件（首次启动可能较慢，请稍候）……")

    def _boot():
        try:
            ok, msg = _start_automation(reset_stats=not keep_score)
            if not ok:
                with CTRL.lock:
                    CTRL.last_error = msg
                _log("ERROR", msg)
            else:
                # 手动开始 = 用户明确要玩：解除「手动停止」标记并把常驻转回正常
                # 监督（不按错误恢复处理、不计恢复次数）。
                _resident_after_manual_start()
                if not keep_schedule:
                    # 手动开始：常驻已经进入启动宽限（note_automation_started），
                    # 暂停期间累积的日志空档不会被当成卡死。
                    _log(
                        "SYS",
                        "手动开始对战：常驻不会把暂停期间累积的日志空档算作卡死。",
                    )
        finally:
            # 成功/失败都必须清掉，否则网页会一直显示「启动中…」、后续开始也被挡住。
            with CTRL.lock:
                CTRL.starting = False

    try:
        threading.Thread(target=_boot, name="hs-boot", daemon=True).start()
    except Exception:
        with CTRL.lock:
            CTRL.starting = False
        raise
    if keep_score:
        return {"ok": True, "message": "正在恢复自动化（战绩继续累计），请稍候……"}
    return {"ok": True, "message": "正在启动自动化，请稍候……"}


def _bring_hearthstone_foreground():
    """把炉石切到最前台（未运行则先拉起战网）。

    仅在“开始运行/就绪”时按需调用；COM 需要线程级初始化。
    """
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except Exception:
        pass
    try:
        import click

        click.enter_HS()
    except Exception as exc:
        _log("WARN", f"切换到炉石前台失败：{exc}")


def _focus_hearthstone_if_present() -> bool:
    """炉石窗口已出现时把它切回前台（只聚焦，不点战网/不启动进程）。

    用于重启炉石后的等待循环：战网点完「开始游戏」后，游戏窗口可能停在
    战网或其它窗口底层，后续点击会点空。等待窗口/主界面期间反复调用这个。
    """
    try:
        import get_screen

        return bool(get_screen.focus_hearthstone_if_present())
    except Exception:
        return False


def api_prepare(body=None):
    """「开始运行」第一步：开日志浮窗 + 把炉石切到前台，但【不】开始自动对战。

    真正的对战由「开始对战」按钮（本接口返回 prepared=True 后按钮会变成它）
    或浮窗的 ▶ 开始对战 触发，避免点一下就开始打牌。

    浮窗"正在收尾"时 start() 会排队等上一轮结束再自动开出来（返回 pending）：
    这时文案说清楚"稍等 1–2 秒"，不再是"点了没反应"（用户反馈过：点「退出浮窗」
    后从网页点开始，浮窗再也弹不出来）。
    """
    with CTRL.lock:
        if CTRL.automation_thread is not None:
            return {"ok": False, "error": "自动化已经在运行中。"}
        if CTRL.starting:
            return {"ok": False, "error": "自动化正在启动中，请稍候……"}
        CTRL.prepared = True
    status = None
    if log_overlay is not None and not log_overlay.is_running():
        try:
            status = _bind_overlay()
        except Exception as exc:
            _log("WARN", f"开启日志浮窗失败：{exc}")
    threading.Thread(
        target=_bring_hearthstone_foreground, name="hs-prepare", daemon=True
    ).start()
    # 浮窗可能刚被判定"没起来"（校验线程同步返回时）：按真实状态回话，别谎报就绪。
    with CTRL.lock:
        prepared = bool(CTRL.prepared)
    if not prepared:
        _log(
            "WARN",
            "浮窗还没能打开：请再点一次「🪟 日志浮窗」或「🪄 开始运行（准备）」重试。",
        )
        return {
            "ok": True,
            "prepared": False,
            "message": "浮窗还没能打开：请再点一次「🪟 日志浮窗」或"
            "「🪄 开始运行（准备）」重试。",
        }
    if status == "pending":
        _log(
            "SYS",
            "日志浮窗正在重新打开（上一轮还在收尾），稍等 1–2 秒；"
            "同时正在把炉石切到前台。",
        )
        return {
            "ok": True,
            "prepared": True,
            "message": "浮窗正在重新打开（上一轮在收尾，稍等 1–2 秒就会出现），"
            "同时正在把炉石切到前台；再点一次「开始对战」才会真正开打。",
        }
    _log(
        "SYS",
        "已就绪：日志浮窗已开启、正在把炉石切到前台；未开始对战，"
        "确认无误后再点「开始对战」。",
    )
    return {
        "ok": True,
        "prepared": True,
        "message": "已就绪：浮窗已开、炉石已切前台（不会自动开始）。"
        "再点一次「开始对战」或浮窗的 ▶ 开始对战 才会真正开打。",
    }


def api_stop(body: dict):
    body = body or {}
    internal = bool(body.get("_resident_internal"))
    # 常驻/看门狗内部重启的 stop 只是重启步骤之一，**不是**用户取消定时：
    # 带 keep_schedule（或内部标记）时保留 CTRL.schedule 与磁盘配置，否则窗口内
    # 一次自愈/重启就把定时计划永久毁掉。用户手动 stop 不带该标记，行为不变。
    keep_schedule = bool(body.get("keep_schedule")) or internal
    mode = str(body.get("mode") or "now")
    # 手动停止 = 常驻暂停（恢复中只记 pause_after_recover，由监督器收尾时生效）。
    # 内部步骤（重启前的停自动化）带 _resident_internal 标记，不算用户喊停。
    if not internal:
        _resident_request_pause("user_stop")
        # 用户手动停止 = 常驻暂停，并**落盘**「手动停止」标记：之后重启控制台
        # 常驻保持暂停等手动开始，不会按错误恢复的流程自动装配/点鼠标。
        _resident_record_manual_stop(
            "用户停止（本局结束后）" if mode == "after_game" else "用户停止自动化"
        )
        # 常驻正在“开启装配”（进对战/选模式/选卡组）时也要停掉它的点击循环。
        if _RESIDENT_ARM_RUNNING:
            try:
                import battle_entry

                battle_entry.request_cancel()
            except Exception:
                pass
    if mode == "after_game":
        with CTRL.lock:
            if CTRL.automation_thread is None:
                return {"ok": False, "error": "当前没有正在运行的自动化。"}
            fsm = CTRL.fsm
            CTRL.phase = "stopping"
            if not internal:
                CTRL.stopped_by = "user_after_game"
            if CTRL.schedule.get("end") and not keep_schedule:
                CTRL.schedule = {"start": None, "end": None}
                _persist_schedule(None, None)
        try:
            fsm.request_stop_after_game()
        except Exception as exc:
            return {"ok": False, "error": f"发送停止请求失败：{exc}"}
        _log("WARN", "已请求：本局对战结束后停止。")
        return {"ok": True, "message": "本局对战结束后将自动停止"}
    with CTRL.lock:
        if CTRL.automation_thread is None:
            return {"ok": False, "error": "当前没有正在运行的自动化。"}
        fsm = CTRL.fsm
        CTRL.phase = "idle"
        # 内部 stop 只是重启步骤的前半段，**不能**写成用户停止（stopped_by 会让
        # 常驻恢复刚结束就立刻转 paused）。
        if not internal:
            CTRL.stopped_by = "user_immediate"
        if not keep_schedule:
            CTRL.schedule = {"start": None, "end": None}
    if not keep_schedule:
        _persist_schedule(None, None)
    try:
        fsm.request_immediate_stop()
    except Exception as exc:
        return {"ok": False, "error": f"发送停止指令失败：{exc}"}
    _log("WARN", "已发送立即停止指令。")
    return {"ok": True, "message": "正在停止……"}


def api_cancel(body=None) -> dict:
    """急停：把正在跑的**手动流程**也一起叫停（enter-battle / 重启后自动恢复）。

    `api_stop` 只管 FSM 自动化：自动化没在跑时它会直接回「当前没有正在运行的
    自动化」，而手动「进入对战」还在一遍遍点画面中心 —— 这就是用户说的
    「让它取消它不停」。所以取消要单独一条：置位 `battle_entry` 的全局急停
    开关，等待/点击循环下一轮（≤ interval，默认 0.5s）就退出，不再点任何坐标。

    同时尽量把能停的都停掉：FSM 自动化（立即停）、命令队列、排队中的重启。
    返回体里的 `cancelled` 只列**真正在跑**的部件；一个都没有时不置位急停
    开关（避免把取消信号留给下一次单击），只如实回「没有在跑的动作」。
    """
    import battle_entry

    cancelled = []
    notes = []
    # 急停同时让常驻停手：恢复中只记 pause_after_recover（收尾后生效），避免恢复
    # 链路跑完又把自动化拉起来。
    _resident_request_pause(str(CTRL.stopped_by or "user_cancel"))
    # 急停是用户手动叫停：落盘「手动停止」标记，跨控制台重启保留。
    _resident_record_manual_stop(
        "Ctrl+Q 急停"
        if str((body or {}).get("source") or "") == "hotkey"
        else "用户急停/取消"
    )
    with _ENTER_LOCK:
        entering = bool(_ENTER_STATE.get("running"))
    with _restart_lock:
        restarting = bool(_RESTART_STATE.get("running"))
    # 常驻“开启装配”（拉起炉石→进对战→模式→卡组）也在点坐标，急停要一并叫停。
    arming = bool(_RESIDENT_ARM_RUNNING)
    with CTRL.lock:
        fsm = CTRL.fsm
        automating = CTRL.automation_thread is not None
    with _QUEUE_LOCK:
        queued = bool(_QUEUE_STATE.get("running"))

    if entering or restarting or arming:
        # 只有真有循环在跑才置位，否则会给下一次单击留个"已取消"的坑。
        battle_entry.request_cancel()
        if entering:
            cancelled.append("进入对战（等待/点击循环）")
        if restarting:
            cancelled.append("重启炉石后的等待")
        if arming:
            cancelled.append("常驻开启装配")
    if automating and fsm is not None:
        try:
            CTRL.phase = "idle"
            CTRL.stopped_by = "user_cancel"
            fsm.request_immediate_stop()
            cancelled.append("自动化（立即停）")
        except Exception as exc:
            notes.append(f"停自动化失败：{exc}")
    if queued:
        with _QUEUE_LOCK:
            _QUEUE_STATE["running"] = False
            _QUEUE_STATE["message"] = "已被用户取消"
        cancelled.append("命令队列")

    if not cancelled:
        message = "当前没有正在跑的动作，无需取消。"
    else:
        message = (
            "已取消："
            + "、".join(cancelled)
            + "；正在点的动作会在 0.5 秒内停手，之后不再点任何坐标"
        )
        if notes:
            message += "（" + "；".join(notes) + "）"
    source = str((body or {}).get("source") or "")
    if source == "hotkey":
        message = "Ctrl+Q —— " + message
    _log("WARN", message)
    return {"ok": True, "cancelled": cancelled, "message": message}


def api_schedule(body: dict):
    start_text = str(body.get("start") or "").strip()
    end_text = str(body.get("end") or "").strip()
    try:
        start_dt = datetime.fromisoformat(start_text)
    except ValueError:
        return {"ok": False, "error": "开始时间格式不正确。"}
    end_dt = None
    if end_text:
        try:
            end_dt = datetime.fromisoformat(end_text)
        except ValueError:
            return {"ok": False, "error": "结束时间格式不正确。"}
    now = datetime.now()
    if start_dt < now:
        return {"ok": False, "error": "开始时间不能早于当前时间。"}
    if end_dt is not None and end_dt <= start_dt:
        return {"ok": False, "error": "结束时间必须晚于开始时间。"}
    with CTRL.lock:
        if CTRL.automation_thread is not None:
            return {"ok": False, "error": "自动化运行中，请先停止后再设置定时任务。"}
    with CTRL.lock:
        CTRL.schedule = {"start": start_dt, "end": end_dt}
        CTRL.phase = "waiting"
        CTRL.stopped_by = None
        CTRL.last_summary = None
    _persist_schedule(start_dt, end_dt)
    t = threading.Thread(
        target=_schedule_worker,
        args=(start_dt, end_dt),
        name="hs-scheduler",
        daemon=True,
    )
    CTRL.scheduler_thread = t
    t.start()
    end_text = (
        f"，{end_dt:%m-%d %H:%M} 结束" if end_dt else "，不设结束时间（运行至手动停止）"
    )
    _log("SYS", f"定时任务已设置：{start_dt:%m-%d %H:%M} 开始{end_text}。")
    return {"ok": True, "message": "定时任务已设置，请保持本程序运行。"}


# ---------------------------------------------------------------- 校准工具
# 网页按钮、浮窗「校准」按钮、命令行 python calibrate_roi.py 打开的都是同一个
# 校准窗口（calibrate_roi.py）。这里只维护「同一时刻最多一个」的会话句柄。
_CALIBRATION_PROC = None
CALIBRATION_HINT = (
    "拖框对齐后按 S 保存：1 推荐面板 · 2 换牌确认按钮 · "
    "3 AI胜率浮动条 · 4 结算开始按钮 · 5 段位数字 · "
    "6 主界面「传统对战」 · 7 选择套牌 9 个卡组"
    "（Tab 切换，Esc 退出）"
)


def _calibration_running() -> bool:
    proc = _CALIBRATION_PROC
    try:
        return proc is not None and proc.poll() is None
    except Exception:
        return False


def _start_calibration(target: str = "") -> dict:
    """启动校准窗口（已在跑就直接复用，不重复开窗）。

    `target` 可以指定一打开就停在哪个目标上（`calibrate_roi.py --target KEY`，
    例如 `main_menu_battle`），省得用户自己按 Tab 翻。
    """
    global _CALIBRATION_PROC
    if _calibration_running():
        return {
            "ok": True,
            "running": True,
            "message": f"校准窗口已在屏幕上：{CALIBRATION_HINT}",
        }
    script = ROOT / "calibrate_roi.py"
    if not script.exists():
        _log("WARN", f"校准工具缺失：{script}")
        return {"ok": False, "error": f"校准工具缺失：{script}"}
    command = [sys.executable, str(script)]
    if target:
        command += ["--target", str(target)]
    # 校准工具是 GUI 叠加层，但 sys.executable 是控制台子系统的 python.exe：
    # 不设标志的话每次点「校准」都会弹一个黑色 CMD 窗口。用 CREATE_NO_WINDOW
    # 让子进程不分配控制台窗口；同时把 stdout/stderr 重定向到日志文件——
    # 没有控制台句柄时 print 会炸，重定向到文件既避免黑框又保留调试输出。
    popen_kwargs = {"cwd": str(ROOT)}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            log_dir = ROOT / "log"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_handle = open(str(log_dir / "calibrate_roi.log"), "ab", buffering=0)
            popen_kwargs["stdout"] = log_handle
            popen_kwargs["stderr"] = subprocess.STDOUT
        except Exception:
            popen_kwargs["stdout"] = subprocess.DEVNULL
            popen_kwargs["stderr"] = subprocess.DEVNULL
    try:
        _CALIBRATION_PROC = subprocess.Popen(command, **popen_kwargs)
    except Exception as exc:
        _log("WARN", f"启动校准窗口失败：{exc}")
        return {"ok": False, "error": f"启动校准工具失败：{exc}"}
    _log("SYS", f"已启动校准窗口：{CALIBRATION_HINT}")
    return {
        "ok": True,
        "running": True,
        "message": f"校准窗口已启动：{CALIBRATION_HINT}",
    }


def _stop_calibration() -> dict:
    """关掉校准窗口（浮窗「校准」再点一次 / 关闭脚本时）。"""
    global _CALIBRATION_PROC
    proc, _CALIBRATION_PROC = _CALIBRATION_PROC, None
    if proc is None:
        return {"ok": True, "running": False, "message": "校准窗口未在运行"}
    try:
        if proc.poll() is None:
            proc.terminate()
    except Exception as exc:
        return {"ok": False, "error": f"关闭校准工具失败：{exc}"}
    return {"ok": True, "running": False, "message": "已关闭校准窗口"}


def _toggle_calibration() -> bool:
    """切换校准窗口，返回切换后是否正在显示（浮窗按钮的返回值）。"""
    if _calibration_running():
        _stop_calibration()
        return False
    _start_calibration()
    return _calibration_running()


def api_calibrate(body=None):
    """网页「🎯 校准区域」卡片 / CLI：启动同一个校准窗口。

    body 可选 `{"target": "main_menu_battle"}`：一打开就停在那个框上。
    """
    body = body or {}
    result = _start_calibration(str(body.get("target") or ""))
    if not result.get("ok"):
        return result
    return {"ok": True, "message": result.get("message", "")}


def _hearthstone_foreground_guard():
    """常驻守护：确保炉石主窗口在前台（复用脚本开始的置顶方式）。

    每 30 分钟检查一次当前前台窗口；若不是炉石主窗口，就用
    get_screen.move_window_foreground（同「开始对战」脚本开头的一致）
    把炉石切回最前台。只在启动 30 分钟后才做第一次校正，避免
    一打开 web 控制台就把炉石抢到前台（用户只想打开 web）。
    """
    interval = 30 * 60
    try:
        import pythoncom

        pythoncom.CoInitialize()  # move_window_foreground 用 WScript.Shell(COM)
    except Exception:
        pass
    try:
        import get_screen
        import win32gui
    except Exception as exc:
        _log("WARN", f"炉石前台守护不可用：{exc}")
        return
    while True:
        time.sleep(interval)
        try:
            hwnd = get_screen.get_HS_hwnd()
            if not hwnd:
                continue  # 炉石未运行，跳过
            if win32gui.GetForegroundWindow() == hwnd:
                continue  # 已在前台
            get_screen.move_window_foreground(hwnd)
            _log("SYS", "检测到炉石不在前台，已自动切换到前台。")
        except Exception:
            pass


# 常驻阶段监测线程维护的当前阶段标签（供浮窗“开始对战”按钮禁用判定）。
_current_stage = None


def _stage_label(ls):
    if getattr(ls, "game_entity_id", 0) == 0:
        return "未对局"
    if getattr(ls, "is_end", False):
        return "对局结束"
    try:
        turns = ls.game_num_turns_in_play
    except Exception:
        turns = 0
    if turns == 0:
        return "换牌"
    if ls.is_my_turn:
        return "我方出牌"
    return "对手回合"


def _stage_monitor_loop():
    """常驻阶段监测：独立读 Power.log，阶段变化打 -----xx阶段-----。"""
    global _current_stage
    try:
        from log_state import LogState, log_iter_func, update_state
    except Exception as exc:
        _log("WARN", f"阶段监测初始化失败：{exc}")
        return
    cfg = load_config()
    log_root = (cfg.get("log_root") or "").strip()
    if not log_root:
        return
    ls = LogState()
    try:
        li = log_iter_func(log_root)
    except Exception as exc:
        _log("WARN", f"阶段监测日志读取失败：{exc}")
        return
    last = None
    while True:
        try:
            c = next(li)
            if getattr(c, "log_type", "ERROR") == "ERROR":
                continue
            for line in c.message_list:
                try:
                    update_state(ls, line)
                except Exception:
                    pass
        except Exception:
            pass
        stage = _stage_label(ls)
        _current_stage = stage
        if stage != last:
            last = stage
            _log("SYS", f"-----{stage}阶段-----")
        time.sleep(0.3)


def _overlay_stop_after():
    """切换“本局结束后停止”：已设则取消，未设则请求本局结束后停。

    对局进行中随时可反悔（再点一次取消），无需重启脚本。
    """
    with CTRL.lock:
        fsm = CTRL.fsm
        active = (
            bool(getattr(fsm, "stop_after_current_game", False))
            if fsm is not None
            else False
        )
    if active:
        try:
            fsm.request_cancel_stop_after_game()
        except Exception as exc:
            _log("ERROR", f"取消「本局结束后停止」失败：{exc}")
    else:
        api_stop({"mode": "after_game"})


def _overlay_is_running():
    with CTRL.lock:
        return CTRL.automation_thread is not None


def _overlay_is_starting():
    """自动化是否正在启动中（已点开始、后台线程还没挂上）。

    浮窗据此在「开始对战」上显示「启动中…」：用户反馈点完开始后按钮先没变化、
    过一会才变「对局进行中」，中间这段看起来像没反应。
    """
    with CTRL.lock:
        return bool(CTRL.starting)


def _overlay_is_in_game():
    """对局是否已开始（换牌/出牌/对手回合），或自动化正在运行。

    供浮窗“开始对战”按钮禁用判定：对局进行中不允许再点开始，
    避免脚本被误触发而重复启动/复位。
    """
    with CTRL.lock:
        running = CTRL.automation_thread is not None
    stage = _current_stage
    if stage in ("换牌", "我方出牌", "对手回合"):
        return True
    return running


def _overlay_is_stop_after():
    with CTRL.lock:
        fsm = CTRL.fsm
        return (
            bool(getattr(fsm, "stop_after_current_game", False))
            if fsm is not None
            else False
        )


def _current_score():
    """自动化战绩唯一数据源 (场数, 胜场, 自动认输数)，供浮窗与 Web 页面共用。

    fsm 尚未初始化时按 0 处理，保证浮窗与 /api/status 口径一致。
    """
    with CTRL.lock:
        fsm = CTRL.fsm
    games = int(getattr(fsm, "game_count", 0) or 0) if fsm is not None else 0
    wins = int(getattr(fsm, "win_count", 0) or 0) if fsm is not None else 0
    concedes = int(getattr(fsm, "concede_count", 0) or 0) if fsm is not None else 0
    return games, wins, concedes


def _overlay_score():
    """当前自动化战绩 (场数, 胜场, 自动认输数)。供浮窗头部显示胜负情况。"""
    return _current_score()


def _overlay_human_like():
    """浮窗「活人感」状态行数据（是否开启 + 参数），由 log_overlay 渲染。"""
    try:
        hl = _current_human_like()
    except Exception:
        return None
    return hl


def _concede_detect_state():
    """自动投降检测的显示状态。

    自动化模块已加载时取实时值（最近读到的胜率 / 连续计数 / 最近检测回合），
    否则用配置兜底，保证页面在未运行时也能显示“开/关 + 阈值 + 连续 N 回合”。
    """
    with CTRL.lock:
        fsm = CTRL.fsm
    if fsm is not None and hasattr(fsm, "concede_detection_state"):
        try:
            return fsm.concede_detection_state()
        except Exception:
            pass
    cfg = dict(DEFAULT_AUTO_CONCEDE)
    saved = load_config().get("auto_concede")
    if isinstance(saved, dict):
        cfg.update({k: v for k, v in saved.items() if v is not None})
    return {
        "enabled": bool(cfg["enabled"]),
        "threshold": float(cfg["threshold"]),
        "rounds": int(cfg["rounds"]),
        "rate": None,
        "streak": 0,
        "checked_turn": None,
        "triggered": False,
    }


def _overlay_concede_detect():
    """浮窗「投降检测」状态行（与 human_like 同一套回调机制）。"""
    try:
        return _concede_detect_state()
    except Exception:
        return None


def _liveness_state() -> dict:
    """生存检测状态（供浮窗「炉石」行）：自动化已加载时取状态机实时判定，否则直接采样。

    阈值已并入「守护」配置（warn_seconds / stale_in_game_seconds），本函数只负责
    取信号与展示，不再有独立的「存活检测」开关。
    """
    with CTRL.lock:
        fsm = CTRL.fsm
    if fsm is not None and hasattr(fsm, "hearthstone_liveness_state"):
        try:
            return fsm.hearthstone_liveness_state()
        except Exception:
            pass
    try:
        from src.safety.hearthstone_liveness import default_monitor

        state = dict(default_monitor().sample(in_game=False))
    except Exception:
        state = {
            "enabled": True,
            "status": "unknown",
            "process": "unknown",
            "log_age": None,
            "alert": None,
        }
    state.setdefault("alert", None)
    return state


def _overlay_liveness():
    """浮窗「炉石」状态行。"""
    try:
        return _liveness_state()
    except Exception:
        return None


def _name_match_state() -> dict:
    """账号昵称校验状态（无法判断时 matched=None）。"""
    with CTRL.lock:
        fsm = CTRL.fsm
    if fsm is not None and hasattr(fsm, "player_name_state"):
        try:
            return fsm.player_name_state()
        except Exception:
            pass
    return {
        "config": (load_config().get("name") or "").strip(),
        "players": {},
        "matched": None,
    }


def _overlay_account():
    """浮窗「账号」状态行：配置的用户 ID 与日志玩家名是否匹配。"""
    try:
        return _name_match_state()
    except Exception:
        return None


def _overlay_ocr():
    """浮窗「OCR 扫描」框：各区域最近一次读到的文字（旁路数据，只读）。

    数据源是 legacy OCR 适配器写进 ``src.ocr.status`` 的记录；这里只做一层
    容错转发（记录还没产生 / 导入失败都返回 None，浮窗显示“等待 OCR 数据”），
    浮窗本身不认识 OCR 内部结构。
    """
    try:
        from src.ocr.status import snapshot

        return snapshot()
    except Exception:
        return None


def _overlay_ocr_phase():
    """浮窗「OCR 扫描」框空状态用：当前卡在哪一步 + Power.log 静默多久。

    数据源是 ``FSM_action.automation_phase_state()``（自动化未加载/异常返回
    None，浮窗退回原文案）。浮窗本身不认识 FSM 内部结构。
    """
    try:
        with CTRL.lock:
            fsm = CTRL.fsm
        if fsm is None or not hasattr(fsm, "automation_phase_state"):
            return None
        return fsm.automation_phase_state()
    except Exception:
        return None


def _overlay_exit():
    """浮窗“退出脚本”按钮：先停止自动化，再退出整个脚本进程。"""
    try:
        with CTRL.lock:
            running = CTRL.automation_thread is not None
        if running:
            api_stop({"mode": "now"})
    except Exception:
        pass
    try:
        _log("SYS", "用户从浮窗点击“退出脚本”。")
    except Exception:
        pass
    # 用户主动退出脚本：落盘「手动停止」标记（scope=console：看门狗也别拉起）。
    _resident_record_manual_stop("浮窗退出脚本", scope="console")
    # 屏幕上还开着校准窗口的话先关掉，别让它留在桌面上。
    try:
        _stop_calibration()
    except Exception:
        pass
    # 立即可靠结束进程（浮窗/自动化均为 daemon 线程，os._exit 直接终止）。
    os._exit(0)


def _overlay_halt():
    with CTRL.lock:
        running = CTRL.automation_thread is not None
    if running:
        api_stop({"mode": "now"})
    else:
        # 浮窗「恢复」= 暂停后继续：不清零胜负战绩，从上次的场数/胜场接着算。
        api_start({"keep_score": True})


def _bind_overlay():
    """绑定日志浮窗回调（开始/中止/本局结束后停止 + 状态查询）。

    供自动化线程第一次启动与网页“开启浮窗”共用，避免重复。
    返回 log_overlay.start() 的结果（already / started / pending），调用方
    据此判断"浮窗到底开没开"——以前 start() 静默失败，网页却照样报"已就绪"。
    """
    if log_overlay is None:
        return None
    status = log_overlay.start(
        on_start=lambda: api_start({}),
        on_halt=_overlay_halt,
        is_running=_overlay_is_running,
        is_starting=_overlay_is_starting,
        on_stop_after=_overlay_stop_after,
        is_stop_after=_overlay_is_stop_after,
        is_in_game=_overlay_is_in_game,
        score_callback=_overlay_score,
        human_like_callback=_overlay_human_like,
        concede_callback=_overlay_concede_detect,
        liveness_callback=_overlay_liveness,
        # 日志区下方的「OCR 扫描」框：各区域最近一次 OCR 读到的原文。
        ocr_callback=_overlay_ocr,
        # 面板空状态：当前卡在哪一步 + 日志静默多久（“为什么没在扫”）。
        phase_callback=_overlay_ocr_phase,
        account_callback=_overlay_account,
        # 「账号」行的眼睛按钮：是否显示昵称（默认显示），点一下互换并记住。
        account_visible_setting=_overlay_account_visible(),
        on_toggle_account=_overlay_save_account_visible,
        on_calibrate=_overlay_toggle_calibrate,
        # 关浮窗时把还开着的校准窗口一并关掉（网页/浮窗打开的是同一个窗口）。
        on_calibrate_close=_overlay_close_calibrate,
        # 「重启炉石」：中止进程 + 清空日志目录 + 重新拉起（确认弹窗在浮窗里）。
        on_restart=_overlay_restart_hearthstone,
        # 「日志」状态行 + 「打开日志 / 清理日志」两个按钮。
        logs_callback=_overlay_logs,
        on_open_logs=_overlay_open_logs,
        on_clear_logs=_overlay_clear_logs,
        # 「导出本次出错截屏与日志」：把本次运行的重试/报错 + 截图打包成 zip。
        on_export_feedback=_overlay_export_feedback,
        # 「窗口宽度」滑条：读炉石当前客户区宽度做初值；拖动按 16:9 改客户区。
        window_width_callback=_overlay_window_width,
        on_resize_window=_overlay_resize_window,
        # 顶部红色自查提示：本机版本 / 线上最新版本对比（只读缓存）。
        version_callback=_overlay_version_info,
        # 「退出浮窗」：只关窗口，脚本继续跑，网页可以再打开。
        on_exit_overlay=_overlay_exit_overlay,
        on_exit=_overlay_exit,
        # 浮窗「常驻」页：状态查询 / 开停常驻 / 保存设置（页面渲染在 log_overlay 里）。
        resident_callback=_overlay_resident,
        on_resident_toggle=_overlay_resident_toggle,
        on_resident_apply=_overlay_resident_apply,
    )
    _verify_overlay_opened_async(status)
    return status


# ---------------------------------------------------------------- 脚本日志（浮窗）
# 扫描结果缓存：浮窗每 350ms 取一次状态，不能每次都去扫 200 个文件。
LOG_SCAN_TTL = 5.0
# 体积阈值：决定浮窗「日志」行圆点/数值的颜色（绿 → 黄 → 红）。
LOG_WARN_BYTES = 100 * 1024 * 1024
LOG_DANGER_BYTES = 500 * 1024 * 1024
# 清理完成后，说明列显示"已清理 X"的时间（秒）。
LOG_CLEAR_FLASH_SECONDS = 10.0

_log_scan_cache = {"at": 0.0, "data": None}
_log_clear_state = {"at": 0.0, "bytes": 0}


def _script_logs_snapshot(force: bool = False) -> dict:
    """脚本日志快照（带 TTL 缓存）：体积、个数、上次清理结果、颜色阈值。"""
    now = time.time()
    cached = _log_scan_cache["data"]
    if force or cached is None or now - _log_scan_cache["at"] >= LOG_SCAN_TTL:
        data = script_logs.scan(LOG_DIR, RUNTIME_LOG)
        _log_scan_cache["data"] = data
        _log_scan_cache["at"] = now
        cached = data
    snapshot = dict(cached)
    snapshot["cleared_at"] = _log_clear_state["at"]
    snapshot["cleared_bytes"] = _log_clear_state["bytes"]
    snapshot["warn_bytes"] = LOG_WARN_BYTES
    snapshot["danger_bytes"] = LOG_DANGER_BYTES
    return snapshot


def _script_logs_invalidate() -> None:
    """清理后让缓存立刻失效，浮窗下一拍就显示新体积。"""
    _log_scan_cache["at"] = 0.0
    _log_scan_cache["data"] = None


def _overlay_logs():
    """浮窗「日志」状态行数据（出异常返回 None，浮窗显示"—"）。"""
    try:
        return _script_logs_snapshot()
    except Exception:
        return None


def _overlay_open_logs():
    """浮窗「打开日志」：Explorer 打开对战日志目录并选中最新一份。"""
    result = script_logs.open_location(LOG_DIR)
    paths = f"对战日志目录 {LOG_DIR}；运行日志 {RUNTIME_LOG}"
    if result.get("ok"):
        what = "最新一份对战日志" if result.get("opened") == "file" else "日志目录"
        _log("SYS", f"已打开{what}：{result.get('target')}（{paths}）")
    else:
        _log("WARN", f"打开日志位置失败：{result.get('error')}（{paths}）")
    return bool(result.get("ok"))


def _overlay_clear_logs():
    """浮窗「清理日志」（已确认）：后台删掉脚本自己写的日志，立刻返回不卡浮窗。"""
    threading.Thread(
        target=_clear_script_logs_worker, name="hs-clear-logs", daemon=True
    ).start()
    return True


def _overlay_export_feedback():
    """浮窗「导出本次出错截屏与日志」：后台导出，完成后打开文件夹定位 zip。"""
    threading.Thread(
        target=lambda: _feedback_export_and_log(trigger="浮窗导出", open_folder=True),
        name="hs-export-feedback",
        daemon=True,
    ).start()
    return {"ok": True}


def _clear_script_logs_worker() -> None:
    """删除 logs\\对战日志_*.txt + ui_log_last.txt，并把结果写进日志。"""
    before = _script_logs_snapshot(force=True)
    if not before.get("bytes") and not before.get("files"):
        _log("SYS", "已无可清理的脚本日志。")
        return
    result = script_logs.clear(
        LOG_DIR, RUNTIME_LOG, logger=lambda text: _log("SYS", text)
    )
    _script_logs_invalidate()
    _log_clear_state["at"] = time.time()
    _log_clear_state["bytes"] = int(result.get("freed_bytes") or 0)
    _log(
        "SYS",
        "已清理脚本日志：删除 "
        f"{result.get('removed', 0)} 个文件，释放 "
        f"{script_logs.format_size(result.get('freed_bytes'))}"
        f"（清理前 {script_logs.format_size(before.get('bytes'))}）。",
    )
    if result.get("failed"):
        names = "、".join(result["failed"][:5])
        _log(
            "WARN",
            f"有 {len(result['failed'])} 个日志文件删不掉"
            f"（可能正被其它程序打开）：{names}",
        )
    _script_logs_invalidate()


# 浮窗"说过已开"之后，最多等这么久确认它真的在跑（秒）。
OVERLAY_VERIFY_TIMEOUT = 3.0


def _verify_overlay_opened_async(status) -> None:
    """确认浮窗真的起来了：没起来就写 WARN 并退回"未就绪"（不谎报）。

    只对 log_overlay 明确的返回值（started / pending）做校验：测试里的假
    浮窗对象返回 None，不参与校验。
    """
    if log_overlay is None or status not in ("started", "pending"):
        return
    threading.Thread(
        target=_verify_overlay_opened,
        args=(status,),
        name="hs-overlay-verify",
        daemon=True,
    ).start()


def _verify_overlay_opened(status, timeout: float = OVERLAY_VERIFY_TIMEOUT) -> bool:
    """等浮窗真正在显示；超时就如实报错并把页面退回「开始运行（准备）」。

    至少检查一次（timeout=0 也要先看一眼当前状态）。
    """
    deadline = time.time() + float(timeout)
    while True:
        try:
            if log_overlay is not None and log_overlay.is_running():
                return True
        except Exception:
            break
        if time.time() >= deadline:
            break
        time.sleep(0.1)
    with CTRL.lock:
        CTRL.prepared = False
    _log(
        "WARN",
        "日志浮窗没能打开（上一轮浮窗可能还在收尾）："
        f"请再点一次「🪟 日志浮窗」或「🪄 开始运行（准备）」重试"
        f"（等了 {float(timeout):.0f} 秒还没看到浮窗）。",
    )
    return False


def _overlay_toggle_calibrate():
    """浮窗「校准」按钮：开/关屏幕上的校准窗口（与网页按钮同一个窗口）。

    窗口里三个截图区域都能拖、都能存；框以外的鼠标是穿透的，只用来对照着把
    盒子 UI 摆正。返回切换后是否显示。
    """
    return bool(_toggle_calibration())


def _overlay_close_calibrate():
    """关浮窗/退出脚本时收掉校准窗口（没开着就什么都不做）。"""
    if _calibration_running():
        _stop_calibration()


# ---------------------------------------------------------------- 重启炉石
# 停自动化最多等这么久；超时就放弃（不在不确定的状态下杀进程/删文件）。
AUTOMATION_STOP_TIMEOUT = 15.0


def _launch_hearthstone_via_battlenet():
    """走战网「开始游戏」拉起炉石（与脚本平时拉起炉石的方式一致）。"""
    import click

    return click.enter_HS()


def _hearthstone_window_ready() -> bool:
    """炉石游戏窗口是否已经出现——启动成功的真正判据。"""
    try:
        import get_screen

        return bool(get_screen.test_hs_available())
    except Exception:
        return False


def _battlenet_window_ready() -> bool:
    """战网窗口是否已经出现（战网没开时先拉起它，再点「开始游戏」）。"""
    try:
        import get_screen

        return bool(get_screen.get_battlenet_hwnd())
    except Exception:
        return False


def _overlay_restart_hearthstone():
    """浮窗「重启炉石」按钮（已确认过）：后台执行，立刻返回不卡浮窗。

    与网页/CLI 的「重启炉石」共用 ``_restart_lock`` / ``_RESTART_STATE``：已有
    重启在跑就直接拒绝，避免两次并发把刚拉起来的炉石又杀一遍。
    """
    with _restart_lock:
        if _RESTART_STATE.get("running"):
            _log("WARN", "浮窗想重启炉石，但上一次「重启炉石」还没结束，本次跳过。")
            return False
        _restart_begin("manual")
    try:
        import battle_entry

        battle_entry.clear_cancel()
    except Exception:
        pass
    try:
        threading.Thread(
            target=_restart_hearthstone_worker, name="hs-restart", daemon=True
        ).start()
    except Exception as exc:
        with _restart_lock:
            _RESTART_STATE["running"] = False
        _log("ERROR", f"浮窗重启炉石失败：{exc}")
        return False
    return True


def _overlay_window_width():
    """浮窗「窗口宽度」滑条初值：炉石当前客户区宽度（拿不到返回 None → 滑条禁用）。"""
    try:
        import hs_window

        size = hs_window.current_client_size()
        return int(size[0]) if size else None
    except Exception:
        return None


def _overlay_version_info():
    """浮窗红色自查提示用的版本快照（只读缓存，绝不联网）。

    浮窗每 350ms 调一次；联网检查在 update_check 的后台线程里，6 小时一次。
    """
    try:
        return update_check.status()
    except Exception:
        return None


def _overlay_resize_window(width):
    """浮窗「窗口宽度」滑条：按 16:9 设炉石客户区宽度；返回改后的 [w, h] 或 None。

    `hs_window.set_client_width` 会把宽度收进 960~3840 并对齐 16 的步进
    （高度正好是 9 的倍数 → 严格 16:9）；游戏把尺寸钳住时返回**实际**尺寸。
    """
    try:
        import hs_window

        size = hs_window.set_client_width(int(width))
        if size is None:
            _log("WARN", "调整炉石窗口失败：没找到炉石窗口。")
            return None
        _log("INFO", f"浮窗调整炉石窗口客户区：{size[0]}×{size[1]}（16:9）")
        return [int(size[0]), int(size[1])]
    except Exception as exc:
        _log("WARN", f"调整炉石窗口失败：{type(exc).__name__}: {exc}")
        return None


def _restart_hearthstone_worker():
    """手动「重启炉石」：停自动化 → 重置日志读取器 → 中止炉石 → 清空日志目录 → 重新拉起炉石。

    每一步都往网页/浮窗写日志；任何一步失败都立刻停下并说明原因，绝不在拿不准
    的状态下继续删文件。

    **重启的完成判据不是"进程起来了"，而是"认到主界面（「传统对战」可点）"**：
    炉石从启动到能操作还要过战网、登录、公告弹窗，只拉起进程就报成功会让后面的
    「进入对战」直接撞在加载画面上（用户 2026-10-17 明确要求）。

    手动重启期间守护监督器会被**临时暂停**（避免它和重启步骤抢着拉起自动化），
    重启成功或失败后都交给守护恢复/按配额自愈。自动恢复（重启+重进对战+重开
    自动化）由守护的恢复链路负责，不再有第二套"看门狗重启"入口。
    """
    # 手动重启会停掉自动化、且手动路径不自动恢复自动化；如果守护此刻在管事，
    # 重启完成后要由守护把自动化重新拉起来。**必须在停自动化之前捕获**：重启
    # 步骤里的 api_stop 会改 stopped_by，之后 _resident_will_act() 就不再为 True。
    was_resident = _resident_will_act()
    if was_resident:
        # 隔离监督器：重启步骤会把 automation_thread 清成 None；监督器若还在跑，
        # 1s 轮询会立刻派发启动/恢复，与重启步骤互相打断、白烧配额。
        _resident_pause_supervisor("手动重启")
    try:
        return _restart_hearthstone_worker_body()
    finally:
        # 成功/失败都恢复守护（失败时让它按配额自愈）；非管事保持原状。
        if was_resident:
            _resident_resume_after_restart()
        with _restart_lock:
            _RESTART_STATE["running"] = False


def _restart_hearthstone_worker_body() -> bool:
    """``_restart_hearthstone_worker`` 的重启步骤本体（不含守护隔离/收尾）。"""
    _restart_stage("restarting")
    ok = _restart_hearthstone_steps()
    if not ok:
        _restart_finish("restart_failed", False, "重启炉石失败（见日志）")
        return False
    return _restart_wait_main_menu()


def _restart_wait_main_menu() -> bool:
    """重启后等炉石回到主界面（「传统对战」可点）——手工重启的完成判据。

    ``battle_entry.wait_for_main_menu`` 认不出界面时只会点画面中心（安全点），
    不盲点按钮；到超时就如实报「没到主界面」，不谎报成功。
    """
    import battle_entry

    _restart_stage("wait_main_menu")
    _log("SYS", "炉石进程已拉起，等待回到主界面（「传统对战」可点）……")
    try:
        result = battle_entry.wait_for_main_menu(
            timeout=RESTART_MAIN_MENU_TIMEOUT_SECONDS,
            logger=lambda text: _log("SYS", text),
            foreground=_focus_hearthstone_if_present,
        )
    except Exception as exc:
        _log("ERROR", f"等待主界面出错：{exc}")
        _restart_finish("main_menu_error", False, f"等待主界面出错：{exc}")
        return False
    if result.get("ok"):
        _log("SYS", "已到达主界面（「传统对战」可点）。")
        _restart_finish("main_menu", True, "已重启并到达主界面（「传统对战」可点）")
        return True
    if result.get("reason") == "aborted":
        _log("WARN", "收到取消请求，已停止等待主界面（不再点任何坐标）。")
        _restart_finish("cancelled", False, "已取消等待主界面")
        return False
    detail = result.get("message") or result.get("state") or "没认出「传统对战」"
    text = (
        f"炉石已重启，但 {RESTART_MAIN_MENU_TIMEOUT_SECONDS:.0f}s 内没到主界面"
        f"（{detail}）"
    )
    _log("WARN", text + "；请手动切到主界面后再继续。")
    _restart_finish("main_menu_timeout", False, text)
    return False


def _restart_hearthstone_steps() -> bool:
    """重启炉石的纯步骤（不恢复自动化）。成功返回 True。

    这是 ``_restart_hearthstone_worker`` 的前半段，单独抽出来是为了让
    「中断重启」能在做完这一步后再决定要不要接着恢复自动化。
    """
    import hs_restart

    cfg = load_config()
    log_root = (cfg.get("log_root") or "").strip()
    info = hs_restart.resolve_paths(log_root)
    if not info["ok"]:
        _log("ERROR", f"重启炉石已取消：{info['error']}")
        return False

    _log("SYS", "开始重启炉石：停止自动化 → 中止进程 → 清空日志目录 → 重新启动……")
    with CTRL.lock:
        running = CTRL.automation_thread is not None
    if running:
        # 内部停止只是重启的步骤之一，**不是**用户在喊停：带标记让 api_stop 不要
        # 误把常驻置成 pause_after_recover（否则恢复成功后反而不自动续跑）；
        # 同时保留定时计划（看门狗/手动/常驻三种重启都不能毁掉窗口）。
        api_stop(
            {"mode": "now", "_resident_internal": True, "keep_schedule": True}
        )
        deadline = time.monotonic() + AUTOMATION_STOP_TIMEOUT
        stopped = False
        while time.monotonic() < deadline:
            with CTRL.lock:
                if CTRL.automation_thread is None:
                    stopped = True
                    break
            time.sleep(0.3)
        if not stopped:
            _log(
                "ERROR",
                f"重启炉石已取消：自动化没有在 "
                f"{AUTOMATION_STOP_TIMEOUT:.0f}s 内停下来。",
            )
            return False
        _log("SYS", "自动化已停止。")

    # 重建日志读取器：关掉本进程可能还开着的 Power.log 句柄（否则 Windows 上
    # 删不掉），顺带清空 LogState 里上一局残留的状态。
    try:
        fsm = CTRL.fsm
        if fsm is not None:
            fsm.init()
            _log("SYS", "已重置日志读取器（LogState / Power.log 句柄）。")
    except Exception as exc:
        _log("WARN", f"重置日志读取器失败（继续执行）：{exc}")

    killed = hs_restart.kill_hearthstone()
    if not killed.get("ok"):
        _log("ERROR", f"中止炉石未确认：{killed.get('error')}")
        if killed.get("running") is True:
            # 进程确确实实还在：既不能删它正在写的日志，也不能再起第二个实例。
            _log("ERROR", "炉石进程还在，已取消后续操作（未删日志、未重复启动）。")
            return False
        # 状态不明（或确认超时）时继续往下走：宁可多试一次把炉石拉回来，
        # 也不要让它停在“被关掉且没人重启”的状态里。
        _log("WARN", "进程状态不明确，继续尝试把炉石拉起来（避免停在关闭状态）。")
    else:
        _log("SYS", killed.get("message") or "炉石进程已退出。")

    cleared = hs_restart.clear_logs(
        info["log_root"], logger=lambda text: _log("SYS", text)
    )
    if not cleared.get("ok"):
        _log("WARN", "有日志条目删不掉：" + "；".join(cleared.get("errors") or []))
    kept = f"，保留 {', '.join(cleared['skipped'])}" if cleared["skipped"] else ""
    _log("SYS", f"日志目录已清空：删除 {cleared['removed']} 项{kept}。")

    # 优先走战网（脚本平时就是这么拉炉石的）；战网没开就先拉战网。
    # 直启 Hearthstone.exe 时进程会秒起秒退（没有战网登录态），所以成功判据是
    # 「游戏窗口出现」，不是「进程存在」。
    launched = hs_restart.launch_hearthstone(
        info["exe"],
        battlenet_exe=info.get("battlenet_exe"),
        launcher=_launch_hearthstone_via_battlenet,
        battlenet_ready=_battlenet_window_ready,
        window_ready=_hearthstone_window_ready,
        foreground=_focus_hearthstone_if_present,
        logger=lambda text: _log("SYS", text),
    )
    if not launched.get("ok"):
        _log(
            "ERROR",
            f"炉石没能自动启动：{launched.get('error')}；"
            "请手动打开战网/炉石后再点「开始对战」。",
        )
        return False
    # 启动成功后再置前一次（等待期间已经每约 2 秒置前，这里收个尾）。
    if _focus_hearthstone_if_present():
        _log("SYS", "已把炉石窗口切回前台。")
    _log("SYS", f"炉石已重新启动：{launched.get('message')}")
    return True


# 串行化「重启炉石」：并发触发两次会把刚拉起来的炉石再杀一遍。
_restart_lock = threading.Lock()
# 「重启炉石」的进度：CLI / agent 靠它判断"到底重启完了没"，不用自己 sleep 猜。
# stage 取值：restarting → wait_main_menu → main_menu（成功）；失败落 *_failed /
# main_menu_timeout / main_menu_error。ok=None 表示还没结束。
_RESTART_STATE = {
    "running": False,
    "source": None,
    "stage": None,
    "ok": None,
    "message": None,
    "started_at": None,
    "finished_at": None,
}
# 重启后等主界面的上限：炉石从进程启动到能点「传统对战」要过战网+登录+公告，
# 实测 30–90s，留到 180s（与 battle_entry.MAIN_MENU_TIMEOUT_SECONDS 一致）。
RESTART_MAIN_MENU_TIMEOUT_SECONDS = 180.0


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _restart_begin(source: str) -> None:
    """标记一次重启开始（调用方需已持有 _restart_lock 或确认没有并发）。"""
    _RESTART_STATE.update(
        {
            "running": True,
            "source": source,
            "stage": "restarting",
            "ok": None,
            "message": None,
            "started_at": _now_iso(),
            "finished_at": None,
        }
    )


def _restart_stage(stage: str) -> None:
    _RESTART_STATE["stage"] = stage


def _restart_finish(stage: str, ok: bool, message: str) -> None:
    _RESTART_STATE.update(
        {
            "running": False,
            "stage": stage,
            "ok": bool(ok),
            "message": message,
            "finished_at": _now_iso(),
        }
    )


def _restart_snapshot() -> dict:
    with _restart_lock:
        return dict(_RESTART_STATE)


# 「中断重启」看门狗已于 2026-11-01 并入「守护」（原常驻）：
# 自动恢复统一走守护的恢复链路（`_resident_recover_dispatch` →
# `_resident_recover_worker`）与手动「重启炉石」（`_restart_hearthstone_worker`），
# 不再有独立的配置/网页卡/API/CLI，也没有第二套"重启+重进对战"的入口。


def _as_bool(value) -> bool:
    """把网页/CLI 传进来的开关值统一成布尔。

    网页复选框给的是真布尔，CLI 给人打的是 ``on/off/1/0/true/false`` ——
    两种都要认，否则 `hsctl resident on` 会被当成 False。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    return text in ("1", "on", "true", "yes", "y", "开", "开启", "启用")


# ---------------------------------------------------------------- 守护（原常驻）装配
RESIDENT = None
_RESIDENT_LOCK = threading.Lock()
_RESIDENT_ALERT = {"text": None, "at": None}
# 提前告警（warn_seconds，原存活检测的告警档并入守护）：只提示、不动作。
_RESIDENT_WARN = {"text": None, "at": None}
_RESIDENT_ARM_LOCK = threading.Lock()
_RESIDENT_ARM_PENDING = False
_RESIDENT_ARM_RUNNING = False
# 常驻意图（手动停止/退出 vs 错误）：落盘在 log/resident_intent.json，**跨控制台
# 重启保留**。启动分类决定要不要自动接管：manual = 用户手动停的 → 保持暂停等
# 手动开始；error/first_run = 崩溃或首次 → 沿用自动装配（错误后自动开始）。
# 设计边界：读写/分类全在 src/safety/resident_intent.py，本文件只做接线。
_RESIDENT_INTENT_STATE = {
    "kind": "first_run", "manual_stop": False, "scope": "",
    "reason": "", "at": "",
}
_RESIDENT_INTENT_LOCK = threading.Lock()
# 只在**真实控制台进程**里写盘：测试 import web_ui 时 __main__ 是 unittest，
# 绝不能把测试里的 api_stop 落成真实标记（否则下次启动常驻会意外保持暂停）。
_RESIDENT_INTENT_PATH = None  # None = src.safety.resident_intent 的默认 log/ 路径
_RESIDENT_START_CAUSE = "manual"  # manual | autostart（本次启动的接管原因）
_RESIDENT_START_CAUSE_LOCK = threading.Lock()
# Windows 控制台关闭事件的回调：必须保引用，否则会被 GC 掉导致回调失效。
_CONSOLE_CTRL_HANDLER = None
# 与 status_snapshot 的 in_game 口径保持一致（换牌/对战中/结算都算“局内”）。
_RESIDENT_IN_GAME_STATES = ("Choosing Card", "Battling", "Quitting Battle")
# 恢复链路里的等待/重试时长：生产值按“炉石重启后日志重新写起来”的实测给，测试会调小。
RESIDENT_RECONNECT_PROBE_SECONDS = 10.0
RESIDENT_RECONNECT_PROBE_INTERVAL = 0.5
RESIDENT_SELECT_RETRY_DELAY_SECONDS = 2.0


def _resident_settings() -> dict:
    """读常驻配置（每次读 ui_config.json，改完即时生效）。模块缺失时按关闭处理。"""
    try:
        from config import resident_settings

        return dict(resident_settings() or {})
    except Exception:
        return {"enabled": False}


def _resident_enabled() -> bool:
    return bool(_resident_settings().get("enabled"))


# ---- 常驻意图：手动停止/退出 vs 错误（跨控制台重启保留）
def _resident_running_as_console() -> bool:
    """当前是不是**真的以控制台进程**在跑（`python web_ui.py`）。

    只有这种进程才允许把「手动停止」写到磁盘：测试 import web_ui 时
    ``__main__`` 是 unittest，写盘会污染真实标记，让下次启动的常驻莫名暂停。
    """
    try:
        main = sys.modules.get("__main__")
        main_file = getattr(main, "__file__", "") or ""
        return Path(main_file).resolve() == Path(__file__).resolve()
    except Exception:
        return False


def _resident_intent_module():
    """延迟 import：resident_intent 晚于本文件落盘时，import web_ui 不能失败。"""
    from src.safety import resident_intent

    return resident_intent


def _resident_intent_state() -> dict:
    """当前手动停止标记（内存缓存；启动时/写盘后更新）。"""
    with _RESIDENT_INTENT_LOCK:
        return dict(_RESIDENT_INTENT_STATE)


def _resident_refresh_intent() -> dict:
    """从磁盘重读一次手动停止标记（启动分类用）；读失败按 first_run 处理。"""
    global _RESIDENT_INTENT_STATE
    try:
        raw = _resident_intent_module().summary(_RESIDENT_INTENT_PATH)
    except Exception as exc:
        _log("WARN", f"常驻：读取手动停止标记失败（按首次启动处理）：{exc}")
        raw = {}
    kind = str((raw or {}).get("kind") or "first_run")
    if kind not in ("manual", "error", "first_run"):
        kind = "first_run"
    scope = str((raw or {}).get("scope") or "")
    if scope not in ("console", "automation"):
        scope = ""
    state = {
        "kind": kind,
        "manual_stop": bool((raw or {}).get("manual_stop")),
        "scope": scope,
        "reason": str((raw or {}).get("reason") or ""),
        "at": str((raw or {}).get("at") or ""),
    }
    with _RESIDENT_INTENT_LOCK:
        _RESIDENT_INTENT_STATE = state
    return dict(state)


def _resident_intent_writable() -> bool:
    return _RESIDENT_INTENT_PATH is not None or _resident_running_as_console()


def _resident_record_manual_stop(reason, scope="automation") -> None:
    """用户手动停止/退出：落盘「手动停止」标记（跨控制台重启保留）。

    之后重启控制台时 ``_bind_resident`` 会读到它并让常驻保持暂停，**不会**按
    错误恢复的流程自动装配/点鼠标；用户手动点「开始对战」或显式开启常驻时
    由 ``_resident_clear_manual_stop`` 清掉。

    ``scope`` 只影响看门狗：
    * ``automation``（默认）——只是停了自动化，控制台还开着；控制台若崩溃，
      看门狗仍应把它拉回来（常驻自己保持暂停）。
    * ``console``——用户退出了整个控制台（浮窗退出/关窗/Ctrl+C/serve stop），
      看门狗不该再把控制台拉起来。
    """
    global _RESIDENT_INTENT_STATE
    text = str(reason or "manual")
    # 非控制台进程（测试 import web_ui）不落盘也不改内存：否则一个测试里的
    # api_stop/main() 会把「手动停止」留在全局，后续测试的 api_start 会误判成
    # 手动开始并真去 resume 监督器（曾把全量测试卡死在 inline 假线程里）。
    if not _resident_intent_writable():
        return
    scope_text = "console" if scope == "console" else "automation"
    payload = {}
    try:
        payload = _resident_intent_module().record_manual_stop(
            text, scope=scope_text, path=_RESIDENT_INTENT_PATH
        )
    except Exception as exc:
        _log("WARN", f"常驻：写手动停止标记失败（继续执行）：{exc}")
    state = {
        "kind": "manual",
        "manual_stop": True,
        "scope": str(payload.get("scope") or scope_text),
        "reason": str(payload.get("reason") or text),
        "at": str(payload.get("at") or _now_iso()),
    }
    with _RESIDENT_INTENT_LOCK:
        _RESIDENT_INTENT_STATE = state
    _log("SYS", f"常驻：记录「手动停止」（{text}）；重启控制台后保持暂停，等你手动开始。")


def _resident_clear_manual_stop(reason="手动开始") -> bool:
    """解除手动停止标记（用户手动开始对战 / 显式开启常驻时调用）。

    用 ``record_running``（``manual_stop=false``）覆盖而不是删文件：这样之后
    控制台真崩了，下次启动仍能判成 ``error``（日志写「自启接管」），不会退化成
    ``first_run``；看门狗也照常能把它拉回来。
    """
    global _RESIDENT_INTENT_STATE
    if not _resident_intent_state().get("manual_stop"):
        return False
    if _resident_intent_writable():
        try:
            _resident_intent_module().record_running(
                f"manual_start:{reason}", path=_RESIDENT_INTENT_PATH
            )
        except Exception as exc:
            _log("WARN", f"常驻：解除手动停止标记失败（继续执行）：{exc}")
    with _RESIDENT_INTENT_LOCK:
        _RESIDENT_INTENT_STATE = {
            "kind": "first_run", "manual_stop": False, "scope": "",
            "reason": "", "at": "",
        }
    _log("SYS", f"常驻：已解除「手动停止」标记（{reason}），转入正常监督。")
    return True


def _resident_after_manual_start() -> None:
    """用户手动开始自动化后的收尾：解除手动停止标记并转入正常监督。

    **与「错误后自动开始」区分**：这里不装配、不重启炉石、不计恢复次数；只清
    标记 + resume 监督器（暂停期间累积的大日志年龄由启动宽限挡掉，不会被当成
    卡死）。这样「手动暂停 → 手动开始」就是一次普通开始，而不是错误恢复。

    先 ``set_start_cause("manual")`` 再解除标记：否则「error 启动后用户先手动开始」
    这种路径里 clear 返回 False（本来就没标记），start_cause 会一直停在 autostart，
    后续装配日志会误写「自启接管」。
    """
    _resident_set_start_cause("manual")
    if not _resident_clear_manual_stop("手动开始对战"):
        return
    # 只唤醒**已装配**的监督器：这里不创建新实例（避免测试/未启用常驻时白起线程）。
    supervisor = RESIDENT
    if supervisor is not None:
        try:
            supervisor.resume()
        except Exception as exc:
            _log("WARN", f"常驻：手动开始后恢复监督失败（忽略）：{exc}")
    _log("SYS", "常驻：检测到手动开始，转入正常监督（不按错误恢复处理）。")


def _resident_mark_running(reason="console_boot") -> None:
    """启动成功后写一份 ``manual_stop=false`` 的运行记录（不改变内存分类）。

    为什么需要：解除手动标记时文件会被改成「运行中」；但若控制台在启动早期就
    崩了（还没走到这里），下次启动只能按 ``first_run`` 处理。启动成功后写一份
    运行记录，崩溃时文件还在，下次启动就能明确判成 ``error``（看门狗据此拉起）。
    """
    if not _resident_intent_writable():
        return
    try:
        _resident_intent_module().record_running(
            reason, path=_RESIDENT_INTENT_PATH
        )
    except Exception as exc:
        _log("WARN", f"常驻：写运行标记失败（忽略）：{exc}")


def _resident_manual_hold() -> bool:
    """常驻是否因「上次是手动停止」而保持暂停（等用户手动开始）。"""
    return bool(_resident_intent_state().get("manual_stop"))



def _resident_start_cause() -> str:
    with _RESIDENT_START_CAUSE_LOCK:
        return str(_RESIDENT_START_CAUSE or "manual")


def _resident_set_start_cause(cause: str) -> None:
    global _RESIDENT_START_CAUSE
    with _RESIDENT_START_CAUSE_LOCK:
        _RESIDENT_START_CAUSE = "autostart" if cause == "autostart" else "manual"


def _resident_arm_label() -> str:
    """装配日志里的动作名：崩溃后自动接管叫「守护自启接管」，用户手动开启叫「守护开启」。"""
    return "守护自启接管" if _resident_start_cause() == "autostart" else "守护开启"


def _resident_monitor():
    from src.safety.hearthstone_liveness import default_monitor

    return default_monitor()


def _resident_note_automation_started() -> None:
    """告诉常驻监督器「自动化刚刚（重新）启动」——进入 settle 宽限期。

    暂停/空闲一段时间后 Power.log 的年龄会很大；手动「开始对战」后若不重置
    宽限，监督器第一次 poll 就会把这段空档当成「日志卡死」并触发恢复/装配，
    和用户的手动启动抢鼠标。R1 的 ``note_automation_started()`` 负责重置 settle；
    此处 getattr 兜底：R1 未落盘（旧版监督器）时静默跳过，绝不影响启动。
    """
    # 只通知**已装配**的监督器：未 bind 时常驻根本没有轮询在跑，无需通知；
    # 也避免在未启用常驻/测试进程里懒创建一个多余实例。
    supervisor = RESIDENT
    if supervisor is None:
        return
    note = getattr(supervisor, "note_automation_started", None)
    if not callable(note):
        return
    try:
        note()
    except Exception as exc:
        _log("WARN", f"常驻启动宽限登记失败（忽略）：{exc}")


def _resident_automation_running() -> bool:
    """注入给监督器的 automation_running：装配（进对战）期间也算“正在拉起”，
    否则监督器每个 check 周期都会重复派发一次启动、很快烧光启动配额。

    手动 api_start 的 boot 窗口（``CTRL.starting``）同样算在跑：这时自动化线程
    还没挂上，但用户正在启动，常驻绝不能把它当成“没运行”而抢着装配。
    """
    try:
        with CTRL.lock:
            if CTRL.automation_thread is not None:
                return True
            if bool(CTRL.starting):
                return True
    except Exception:
        pass
    return bool(_RESIDENT_ARM_RUNNING)


def _resident_log_age():
    """注入给监督器的 log_age：装配期间还没有可信的日志信号，返回 None。"""
    if _RESIDENT_ARM_RUNNING:
        return None
    return _resident_monitor().log_age()


def _resident_process_running():
    """注入给监督器的 process_running：装配期间（拉起战网/登录）炉石可能短暂不在，
    不能算“进程消失”，否则会和装配线程同时动手（一边启动一边重启）。"""
    if _RESIDENT_ARM_RUNNING:
        return True
    return _resident_monitor().process_running()


def _resident_in_game() -> bool:
    """FSM 当前是否在对局内（注入给监督器的 in_game 信号；fsm 为 None 时 False）。"""
    try:
        fsm = CTRL.fsm
        return bool(
            fsm is not None
            and getattr(fsm, "FSM_state", "") in _RESIDENT_IN_GAME_STATES
        )
    except Exception:
        return False


def _resident_fsm():
    """当前 FSM 模块（自动化还没跑过时按需 import，但绝不启动自动化）。"""
    if CTRL.fsm is not None:
        return CTRL.fsm
    try:
        import FSM_action as fsm
    except Exception:
        return None
    return fsm


def _resident_log_in_game(fsm=None) -> bool:
    """日志是否显示“正在局内”（重连判据）：有对局实体且日志未 COMPLETE。"""
    try:
        if fsm is None:
            fsm = _resident_fsm()
        if fsm is None:
            return False
        fsm.update_log_state()
        state = getattr(fsm, "log_state", None)
        if state is None:
            return False
        return int(getattr(state, "game_entity_id", 0) or 0) != 0 and not bool(
            state.is_end
        )
    except Exception:
        return False


def _resident_reset_log_reader(fsm) -> None:
    """重建日志读取器（清掉上一局/已删除日志的残留句柄与实体）。"""
    try:
        if fsm is not None:
            fsm.init()
            _log("SYS", "已重置日志读取器（守护恢复）。")
    except Exception as exc:
        _log("WARN", f"重置日志读取器失败（继续执行）：{exc}")


def _resident_alert(message, *extra) -> None:
    """监督器告警：写 ERROR 日志 + 记进 status 的 resident.alert（网页/CLI 看得到）。"""
    text = " ".join(str(part) for part in (message,) + extra if part)
    _RESIDENT_ALERT["text"] = text
    _RESIDENT_ALERT["at"] = _now_iso()
    try:
        _log("ERROR", f"守护告警：{text}")
    except Exception:
        pass


def _resident_warn(message, *extra) -> None:
    """守护的**提前告警**（原存活检测的告警档）：只写 WARN 日志，不停止、不重启。"""
    text = " ".join(str(part) for part in (message,) + extra if part)
    _RESIDENT_WARN["text"] = text
    _RESIDENT_WARN["at"] = _now_iso()
    try:
        _log("WARN", f"守护提前告警：{text}")
    except Exception:
        pass


def _resident_request_pause(reason) -> None:
    """手动停止/急停 → 常驻暂停；恢复中只记 pause_after_recover（监督器收尾时生效）。"""
    supervisor = RESIDENT
    if supervisor is None:
        return
    try:
        supervisor.request_pause(str(reason or "user_cancel"))
    except Exception as exc:
        _log("WARN", f"常驻暂停请求未生效（忽略）：{exc}")


def _resident_finish_recovery(ok: bool, message: str = "") -> None:
    supervisor = RESIDENT
    if supervisor is None:
        return
    try:
        if message:
            supervisor.note_recovered(bool(ok), message)
        else:
            supervisor.note_recovered(bool(ok))
    except TypeError:
        # 旧版监督器只收 ok 一个参数：降级调用，行为不变。
        try:
            supervisor.note_recovered(bool(ok))
        except Exception as exc:
            _log("WARN", f"守护恢复收尾失败（忽略）：{exc}")
    except Exception as exc:
        _log("WARN", f"守护恢复收尾失败（忽略）：{exc}")


# 本次控制台运行期间「常驻恢复成功」的累计次数（浮窗/日志展示）。
_RESIDENT_RECOVERY_COUNT = 0
_RESIDENT_RECOVERY_COUNT_LOCK = threading.Lock()


def _resident_note_recovery_success() -> int:
    """记录一次常驻恢复成功，返回本次运行累计次数。"""
    global _RESIDENT_RECOVERY_COUNT
    with _RESIDENT_RECOVERY_COUNT_LOCK:
        _RESIDENT_RECOVERY_COUNT += 1
        return _RESIDENT_RECOVERY_COUNT


# 常驻“已停手”的状态集合：这些状态下恢复/装配链路必须立刻中止。
_RESIDENT_IDLE_STATES = (
    "paused", "paused_exhausted", "error", "cancelled", "disabled",
)
# 用户主动停止/取消的来源（监督器 paused_reason / CTRL.stopped_by 的取值）。
# 手动重启收尾要恢复常驻时，命中这些原因必须保持暂停：手动停止优先级最高，
# 绝不能被“重启完成自动恢复”覆盖。
_RESIDENT_USER_STOP_REASONS = frozenset({
    "user_cancel",
    "user_stop",
    "user_immediate",
    "user_after_game",
    "rank_reached",
    "ctrlq",
    "cancelled",
})


def _resident_user_stop_reason():
    """当前是否有用户主动停止/取消信号；有则返回原因，否则 None。

    只看用户意图来源：battle_entry 的急停开关（Ctrl+Q/取消）与监督器的
    ``paused_reason``；这两个是 api_cancel/api_stop 真正写进去的地方。
    """
    try:
        import battle_entry

        if battle_entry.is_cancelled():
            return "cancelled"
    except Exception:
        pass
    supervisor = RESIDENT
    if supervisor is not None:
        try:
            reason = str((supervisor.state() or {}).get("paused_reason") or "")
        except Exception:
            reason = ""
        if reason in _RESIDENT_USER_STOP_REASONS:
            return reason
    return None



def _resident_stop_reason():
    """常驻动作前的统一守卫：用户暂停/取消/停止时返回原因，否则 None。

    三类信号都看：battle_entry 急停开关（Ctrl+Q/取消）、监督器是否已离开活动
    状态、CTRL.stopped_by（自动化被停止过）。恢复链路每个阶段动作前、arm 每个
    阶段动作前与 api_start 前都调它 —— 绝不违逆用户的停止/取消。
    """
    try:
        import battle_entry

        if battle_entry.is_cancelled():
            return "cancelled"
    except Exception:
        pass
    supervisor = RESIDENT
    if supervisor is not None:
        try:
            state = str((supervisor.state() or {}).get("state") or "")
        except Exception:
            state = ""
        if state in _RESIDENT_IDLE_STATES:
            return state
    try:
        with CTRL.lock:
            stopped = CTRL.stopped_by
    except Exception:
        stopped = CTRL.stopped_by
    if stopped:
        return "paused"
    return None


def _resident_pause_supervisor(reason: str) -> None:
    """让监督器停在 paused（恢复中则把 pause_after_recover 落地）。

    R1 语义：recovering 里 pause() 只登记待生效，必须 note_recovered() 收尾。
    这里显式收尾一次，保证“恢复被中止/装配失败”后 supervisor 就是 paused，
    后续 poll 不会再尝试拉起自动化。
    """
    supervisor = RESIDENT
    if supervisor is None:
        return
    try:
        supervisor.pause(str(reason or "守护已中止"))
        state = str((supervisor.state() or {}).get("state") or "")
        if state == "recovering":
            supervisor.note_recovered(True)
            # note_recovered(True) 会短暂记成 recovered：再 pause 一次，把 last_event/
            # message 落回“已暂停：…”的准确文案（状态仍是 paused，仅修正展示）。
            supervisor.pause(str(reason or "守护已中止"))
    except Exception as exc:
        _log("WARN", f"常驻暂停失败（忽略）：{exc}")


def _resident_abort_recovery(reason: str, message: str = "") -> bool:
    """恢复链路中止：不启动自动化、不继续点，并让常驻停在 paused（或保持关闭）。"""
    text = message or f"守护恢复已中止：{reason}"
    if reason == "disabled":
        _resident_finish_recovery(False)
        _resident_disable()
    else:
        _resident_pause_supervisor("守护恢复已取消")
    _log("WARN", text)
    _restart_finish(
        "cancelled" if reason in ("cancelled", "disabled") else "paused",
        False,
        text,
    )
    return False


def _resident_will_act() -> bool:
    """常驻此刻是否真的会动手（旧看门狗据此让位）。

    只看 enabled 不够：dry_run / 已暂停 / 配额耗尽时常驻不会做任何动作，旧
    看门狗应当照常工作，否则两边都不动。
    """
    supervisor = RESIDENT
    if supervisor is None:
        return False
    try:
        state = dict(supervisor.state() or {})
    except Exception:
        return False
    return bool(
        state.get("enabled")
        and not bool(state.get("dry_run"))
        and str(state.get("state") or "")
        in ("countdown", "starting", "running", "recovering")
    )


def _resident_schedule_hold():
    """定时窗口未开始时返回常驻待机原因（注入监督器的 ``hold_reason``）。

    计划被取消（start=None）或已到开始时间返回 None，监督器立刻恢复正常判定。
    只读 CTRL.schedule，不改任何状态；读不到按“不挂起”处理。
    """
    try:
        with CTRL.lock:
            start_dt = (CTRL.schedule or {}).get("start")
    except Exception:
        return None
    if start_dt is None:
        return None
    try:
        if datetime.now() < start_dt:
            return f"等待定时 {start_dt:%H:%M} 开始"
    except Exception:
        return None
    return None


def _resident_supervisor():
    """取（必要时创建）常驻监督器。只创建对象，不启动线程（线程由 _bind_resident 起）。"""
    global RESIDENT
    if RESIDENT is not None:
        return RESIDENT
    with _RESIDENT_LOCK:
        if RESIDENT is not None:
            return RESIDENT
        try:
            from src.safety.resident_supervisor import ResidentSupervisor
        except Exception as exc:
            _log("WARN", f"常驻判定模块不可用：{exc}")
            return None
        try:
            RESIDENT = ResidentSupervisor(
                settings=_resident_settings,
                log_age=_resident_log_age,
                in_game=_resident_in_game,
                process_running=_resident_process_running,
                automation_running=_resident_automation_running,
                stopped_by=lambda: CTRL.stopped_by,
                start_automation=_resident_start_automation,
                recover=_resident_recover_dispatch,
                alert=_resident_alert,
                # 提前告警（warn_seconds）：只提示，不动作、不占配额。
                warn=_resident_warn,
                # 定时窗口还没开始：守护进 waiting_schedule 待机，不派发启动/恢复。
                hold_reason=_resident_schedule_hold,
            )
        except Exception as exc:
            _log("WARN", f"常驻监督器初始化失败：{exc}")
            return None
        return RESIDENT


def _bind_resident():
    """启动常驻监督器线程（main 里调用一次；重复调用无害）。

    启动时先读一次「手动停止」标记（log/resident_intent.json），据此区分三种
    完全不同的起点，**绝不把用户手动退出当成错误**：

    * ``manual``：上次是用户手动停止/退出（停止按钮 / Ctrl+Q / 浮窗退出脚本 /
      关控制台窗口 / hsctl serve stop）→ 本次保持暂停，不登记装配、不点鼠标；
      等用户点「开始对战」或显式开启常驻再继续监督。
    * ``error``：上次控制台/自动化异常结束（崩溃、被杀）→ 按「错误后自动开始」
      走完整装配，日志用「守护自启接管」区分来源。
    * ``first_run``：从没记录过 → 沿用旧行为（配置开启即自动装配）。
    """
    supervisor = _resident_supervisor()
    if supervisor is None:
        _log("WARN", "常驻模式不可用（判定模块未就绪）。")
        return None
    intent = _resident_refresh_intent()
    kind = str(intent.get("kind") or "first_run")
    manual_hold = kind == "manual"
    _resident_set_start_cause("autostart" if kind == "error" else "manual")
    # 开机时配置就是“守护开启”且上次不是手动停止：倒计时结束后走一次完整装配
    # （拉起炉石→进对战），而不是只调 api_start（那时可能还停在主界面）。
    try:
        if (_resident_enabled() and CTRL.automation_thread is None
                and not manual_hold):
            _resident_set_arm_pending(True)
        elif manual_hold:
            _resident_set_arm_pending(False)
    except Exception:
        pass
    if manual_hold:
        # 手动退出后**不启动监督线程**：start_delay=0 时线程会在 pause 之前先判定
        # 一次「自动化没在跑」并派发装配（真会拉起炉石/点鼠标）。只把状态置成
        # paused 就够；用户手动开始/显式开启时 resume() 会自己把线程起起来。
        try:
            supervisor.pause("手动退出")
        except Exception as exc:
            _log("WARN", f"常驻：按手动退出保持暂停失败（忽略）：{exc}")
        reason = str(intent.get("reason") or "手动停止")
        at = str(intent.get("at") or "")
        when = f"，{at}" if at else ""
        _log(
            "SYS",
            f"常驻：上次为手动停止（{reason}{when}），本次保持暂停；"
            "点「开始对战」或「开启守护」后继续监督。",
        )
        return supervisor
    try:
        supervisor.start()
    except Exception as exc:
        _log("WARN", f"常驻监督器启动失败：{exc}")
        return None
    if kind == "error":
        try:
            takeover = bool(_resident_enabled()) and CTRL.automation_thread is None
        except Exception:
            takeover = bool(_resident_enabled())
        if takeover:
            _resident_mark_running("console_boot_after_error")
            _log(
                "SYS",
                "常驻：上次控制台非正常退出（错误/崩溃），"
                "按「错误后自动开始」接管，日志标记为「守护自启接管」。",
            )
        else:
            _resident_mark_running("console_boot")
            _log(
                "SYS",
                "常驻：上次控制台非正常退出；本次不需要自动接管"
                "（常驻关闭或自动化已在运行）。",
            )
    else:
        _resident_mark_running("console_boot")
    return supervisor


# ---- 开启守护时的首次装配（不杀进程）：拉起炉石 → 进对战 → 模式 → 卡组 → 开自动化
def _resident_set_arm_pending(pending: bool) -> None:
    global _RESIDENT_ARM_PENDING
    with _RESIDENT_ARM_LOCK:
        _RESIDENT_ARM_PENDING = bool(pending)


def _resident_start_automation() -> bool:
    """监督器要求“把自动化拉起来”。

    **禁止盲 api_start**：只有日志确认已在局内（自动化异常死亡/重连）才直接续跑；
    否则一律走完整装配（拉起炉石→进对战→模式→卡组→启动）。arm 选卡失败后
    pending 已清，若这里盲 api_start，FSM 可能在未知界面自己进对战、用没确认的
    卡组开打 —— 这正是要堵的缺口。
    """
    global _RESIDENT_ARM_PENDING
    # 双保险：上次是用户手动停止/退出时绝不接管（正常情况下 _bind_resident 根本不
    # 会启动监督线程，这里防的是 start_delay=0 等窄窗口里被误 resume/误调度）。
    if _resident_manual_hold():
        _log("SYS", "常驻：上次手动退出后尚未手动开始，先不接管。")
        return True
    # 手动/网页/浮窗正在启动（api_start 的 boot 窗口）：绝不抢启动、也别清装配
    # 待办 —— 用户的手动启动优先，常驻等它真正挂上线程后继续正常监督。
    try:
        with CTRL.lock:
            starting = bool(CTRL.starting)
    except Exception:
        starting = False
    if starting:
        _log("SYS", "常驻：检测到启动中，先不接管。")
        return True
    with _RESIDENT_ARM_LOCK:
        if _RESIDENT_ARM_RUNNING:
            # 装配线程还在跑（进对战可能要几分钟）：别重复派发、也别让监督器以为失败。
            return True
        arm_pending = _RESIDENT_ARM_PENDING
        _RESIDENT_ARM_PENDING = False
    if arm_pending:
        return _resident_arm_dispatch()
    try:
        in_game = bool(_resident_log_in_game())
    except Exception:
        in_game = False
    if in_game:
        stop = _resident_stop_reason()
        if stop:
            _log("WARN", f"常驻：收到停止/急停请求（{stop}），不在局内续跑自动化。")
            return False
        try:
            return bool(
                (
                    api_start({"keep_score": True, "keep_schedule": True}) or {}
                ).get("ok")
            )
        except Exception as exc:
            _resident_alert(f"自动重开自动化失败：{exc}")
            return False
    _log(
        "SYS",
        "常驻：自动化未运行且日志不在局内，走「进对战→模式→卡组」装配，不做盲启动。",
    )
    return _resident_arm_dispatch()


def _resident_arm_dispatch() -> bool:
    """后台跑一次“开启常驻”的装配（立刻返回是否派发成功）。"""
    global _RESIDENT_ARM_RUNNING
    with _RESIDENT_ARM_LOCK:
        if _RESIDENT_ARM_RUNNING:
            return True
        _RESIDENT_ARM_RUNNING = True
    cfg = _resident_settings()
    try:
        threading.Thread(
            target=_resident_arm_worker,
            args=(dict(cfg),),
            name="hs-resident-arm",
            daemon=True,
        ).start()
    except Exception as exc:
        with _RESIDENT_ARM_LOCK:
            _RESIDENT_ARM_RUNNING = False
        _resident_alert(f"守护开启失败：无法启动装配线程（{exc}）")
        return False
    return True


def _resident_launch_hearthstone() -> bool:
    """常驻开启时炉石没在运行：走与重启步骤相同的战网启动链路（不杀进程、不清日志）。"""
    try:
        import hs_restart

        cfg = load_config()
        info = hs_restart.resolve_paths((cfg.get("log_root") or "").strip())
        if not info.get("ok"):
            _log("ERROR", f"常驻拉起炉石失败：{info.get('error')}")
            return False
        launched = hs_restart.launch_hearthstone(
            info["exe"],
            battlenet_exe=info.get("battlenet_exe"),
            launcher=_launch_hearthstone_via_battlenet,
            battlenet_ready=_battlenet_window_ready,
            window_ready=_hearthstone_window_ready,
            foreground=_focus_hearthstone_if_present,
            logger=lambda text: _log("SYS", text),
        )
        if not launched.get("ok"):
            _log("ERROR", f"常驻拉起炉石失败：{launched.get('error')}")
            return False
        if _focus_hearthstone_if_present():
            _log("SYS", "已把炉石窗口切回前台。")
        _log("SYS", f"炉石已重新启动：{launched.get('message')}")
        return True
    except Exception as exc:
        _log("ERROR", f"常驻拉起炉石异常：{exc}")
        return False


def _resident_start_after_ready(label: str) -> bool:
    """装配最后一步：启动自动化（失败告警且**不假装成功**）。"""
    stop = _resident_stop_reason()
    if stop:
        _log("WARN", f"{label}：收到停止/急停请求（{stop}），不启动自动化。")
        return False
    if not _resident_enabled():
        _log("WARN", f"{label}：守护已被关闭，本次不启动自动化。")
        return False
    started = api_start({"keep_score": True, "keep_schedule": True})
    if not (started or {}).get("ok"):
        message = (started or {}).get("error") or "未知原因"
        with CTRL.lock:
            already_running = CTRL.automation_thread is not None
        if already_running:
            # 装配期间用户手动点了「开始对战」：这不是失败，直接转入监督。
            _log("SYS", f"{label}：自动化已在运行，转入常驻监督。")
            return True
        _log("ERROR", f"{label}：重新开始自动化失败：{message}")
        _resident_alert(f"{label}失败：自动化没能启动（{message}）。")
        return False
    _log("SYS", f"✅ {label}完成：自动化已开始，继续挂机。")
    return True


def _resident_select_mode_with_retry(mode: str) -> dict:
    """切换对战模式（失败/异常重试 1 次，合计 2 次）；返回最后一次结果。"""
    result = {"ok": False, "error": "切换模式失败"}
    for attempt in range(2):
        if attempt:
            time.sleep(max(0.0, float(RESIDENT_SELECT_RETRY_DELAY_SECONDS)))
            _log(
                "WARN",
                f"常驻：切换模式「{mode}」未成功，重试一次"
                f"（{result.get('error')}）。",
            )
        try:
            result = dict(_mode_click_inner(mode) or {})
        except Exception as exc:
            result = {"ok": False, "error": f"切换模式异常：{exc}"}
        if result.get("ok"):
            return result
    return result


def _resident_select_deck_with_retry(deck_index: int) -> dict:
    """选卡组（跳过/失败等 2s 后重试 1 次，合计 2 次）；返回最后一次结果。"""
    try:
        import battle_entry
    except Exception as exc:
        return {"ok": False, "error": f"battle_entry 不可用：{exc}"}
    result = {"ok": False, "error": "选卡组失败"}
    for attempt in range(2):
        if attempt:
            time.sleep(max(0.0, float(RESIDENT_SELECT_RETRY_DELAY_SECONDS)))
            _log(
                "WARN",
                f"常驻：选第 {deck_index} 个卡组未成功，重试一次"
                f"（{result.get('error')}）。",
            )
        try:
            result = dict(
                battle_entry.select_deck(
                    int(deck_index), logger=lambda text: _log("SYS", text)
                )
                or {}
            )
        except Exception as exc:
            result = {"ok": False, "error": f"选卡组异常：{exc}"}
        if result.get("ok") or result.get("cancelled"):
            # 取消是用户意图：不再 2s 后重试（重试只会再点一次卡组）。
            return result
    return result


def _resident_arm_worker(cfg: dict) -> bool:
    """开启常驻（或错误后自启接管）的装配：拉起炉石 → 进对战 → 模式 → 卡组 → 开自动化。

    ``label`` 区分来源：崩溃后自动接管叫「守护自启接管」，用户手动开启叫「守护开启」，
    日志/告警里一眼能看出这次是「错误后自动开始」还是「手动开始」。

    失败（含 Ctrl+Q 取消）**必须让常驻暂停**：否则 supervisor 下一拍会直接
    api_start 盲启动，FSM 可能在未知界面自己进对战、用没确认的卡组开打。
    """
    global _RESIDENT_ARM_RUNNING
    ok = False
    label = _resident_arm_label()
    try:
        import battle_entry

        battle_entry.clear_cancel()
        if not _hearthstone_window_ready():
            _log("SYS", f"{label}：炉石没在运行，先把它拉起来……")
            if not _resident_launch_hearthstone():
                _resident_alert(
                    f"{label}失败：炉石没能自动启动；本次不启动自动化。"
                )
                return False
        fsm = _resident_fsm()
        _resident_reset_log_reader(fsm)
        if _resident_log_in_game(fsm):
            if _resident_stop_reason():
                _log("WARN", f"{label}已中止：收到停止/急停请求，不会启动自动化。")
                return False
            _log("SYS", f"{label}：日志显示已在局内，直接续跑自动化。")
            ok = _resident_start_after_ready(label)
            return ok

        if _resident_stop_reason():
            _log("WARN", f"{label}已中止：收到停止/急停请求，不会进入对战。")
            return False
        _log("SYS", f"{label}：正在进入对战（主界面 → 选择套牌）……")
        result = battle_entry.enter_battle(
            deck_index=None,
            click_start=False,
            foreground=_focus_hearthstone_if_present,
            logger=lambda text: _log("SYS", text),
            # 装配全程可取消：Ctrl+Q / 停止按钮置位后下一轮轮询立刻停手。
            should_abort=lambda: _resident_stop_reason() is not None,
        )
        if _resident_log_in_game(fsm):
            if _resident_stop_reason():
                _log("WARN", f"{label}已中止：收到停止/急停请求，不会启动自动化。")
                return False
            _log("SYS", f"{label}：进入对战时日志显示已在局内，跳过模式/卡组。")
            ok = _resident_start_after_ready(label)
            return ok
        if not result.get("ok"):
            message = result.get("message") or "自动进入对战未完成"
            if result.get("stage") == "aborted" or _resident_stop_reason():
                _log("WARN", f"{label}已取消：{message}；不会启动自动化。")
                return False
            _resident_alert(f"{label}失败：{message}；本次不启动自动化。")
            return False

        if _resident_stop_reason():
            _log("WARN", f"{label}已中止：收到停止/急停请求，不再切模式。")
            return False
        mode = str(cfg.get("mode") or "standard")
        _log("SYS", f"{label}：选择对战模式 {mode}……")
        picked = _resident_select_mode_with_retry(mode)
        if not picked.get("ok"):
            if _resident_stop_reason():
                _log("WARN", f"{label}已取消：收到停止/急停请求，不会启动自动化。")
                return False
            _resident_alert(
                f"{label}失败：切换模式「{mode}」未成功"
                f"（{picked.get('error')}）；本次不启动自动化。"
            )
            return False

        deck_index = cfg.get("deck_index")
        if deck_index:
            if _resident_stop_reason():
                _log("WARN", f"{label}已中止：收到停止/急停请求，不再选卡组。")
                return False
            _log("SYS", f"{label}：选择第 {int(deck_index)} 个卡组……")
            picked_deck = _resident_select_deck_with_retry(int(deck_index))
            if not picked_deck.get("ok"):
                if _resident_stop_reason():
                    _log("WARN", f"{label}已取消：收到停止/急停请求，不会启动自动化。")
                    return False
                _resident_alert(
                    f"{label}失败：选第 {int(deck_index)} 个卡组未成功"
                    f"（{picked_deck.get('error')}）；本次不启动自动化。"
                )
                return False

        ok = _resident_start_after_ready(label)
        return ok
    except Exception as exc:
        traceback.print_exc()
        _resident_alert(f"{label}异常：{exc}；本次不启动自动化。")
        return False
    finally:
        # 先暂停、再放 ARM_RUNNING 标志：反过来会留下“标志已清、supervisor 立刻
        # 再派一次 arm”的窄窗口。
        if not ok:
            # 装配失败/被取消：让常驻暂停，绝不留给 supervisor 下一拍盲启动的机会。
            _resident_request_pause(f"{label}失败")
        with _RESIDENT_ARM_LOCK:
            _RESIDENT_ARM_RUNNING = False


# ---- 常驻恢复链路：重启步骤 → 重置日志 → 探测重连(≤10s) → 进对战 → 模式 → 卡组 → 开自动化
def _resident_begin_restart(source: str) -> bool:
    """标记一次重启恢复开始（与看门狗/手动重启共用 _RESTART_STATE 与串行锁）。"""
    with _restart_lock:
        if _RESTART_STATE.get("running"):
            return False
        _restart_begin(source)
        return True


def _resident_recover_dispatch(cfg=None, reason=None) -> bool:
    """监督器的 recover 回调：加锁防并发、起后台线程，立刻返回是否派发。

    兼容 R1 的两种调用形态：``recover(cfg, reason)`` 与 ``recover(reason)``。
    """
    if isinstance(cfg, str) and reason is None:
        cfg, reason = None, cfg
    if not isinstance(cfg, dict):
        cfg = _resident_settings()
    reason = str(reason or "resident")
    if not _resident_begin_restart("resident"):
        _log("WARN", "常驻想自动恢复，但上一次「重启炉石」还没结束，本次跳过。")
        return False
    threading.Thread(
        target=_resident_recover_worker,
        args=(dict(cfg), reason),
        name="hs-resident-recover",
        daemon=True,
    ).start()
    return True


def _resident_select_failed(message: str) -> bool:
    """选模式/卡组最终失败：告警 + 不启动自动化 + 收尾（返回 False 便于直接 return）。"""
    _resident_alert(message)
    _resident_finish_recovery(False)
    _restart_finish("select_failed", False, message)
    return False


def _resident_recover_worker(cfg: dict, reason: str) -> bool:
    """常驻恢复的完整链路。任何一步失败/被停止都停在安全状态：绝不启动自动化。

    取消信号的生命周期：开始时清一次上一轮残留；**此之后不清**，因为重启步骤
    可能跑 1–3 分钟，期间 Ctrl+Q 置的取消必须留到每个阶段动作前被检查到。
    """
    reason = str(reason or "resident")
    _log("SYS", f"常驻：检测到异常（{reason}），开始自动恢复……")
    try:
        import battle_entry

        battle_entry.clear_cancel()
        stop = _resident_stop_reason()
        if stop:
            return _resident_abort_recovery(stop, "常驻恢复开始前已是停止状态。")

        _restart_stage("restarting")
        if not _restart_hearthstone_steps():
            _resident_alert(
                "常驻恢复失败：重启炉石没成功；已停在安全状态，不会再点任何坐标。"
            )
            _resident_finish_recovery(False)
            _restart_finish("restart_failed", False, "常驻恢复：重启炉石失败（见日志）")
            return False

        # 重启步骤可能耗时 1–3 分钟：期间用户可能 Ctrl+Q/停止，必须复查（不能清掉）。
        stop = _resident_stop_reason()
        if stop:
            return _resident_abort_recovery(stop, "重启炉石期间收到停止/取消请求。")

        _restart_stage("waiting_session")
        fsm = _resident_fsm()
        _resident_reset_log_reader(fsm)

        in_game = False
        cancelled_early = False
        deadline = time.monotonic() + max(
            0.0, float(RESIDENT_RECONNECT_PROBE_SECONDS)
        )
        while True:
            if _resident_log_in_game(fsm):
                in_game = True
                break
            if battle_entry.is_cancelled():
                cancelled_early = True
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(max(0.0, float(RESIDENT_RECONNECT_PROBE_INTERVAL)))

        if cancelled_early:
            return _resident_abort_recovery(
                "cancelled", "常驻恢复已取消：收到手动急停请求。"
            )
        stop = _resident_stop_reason()
        if stop:
            return _resident_abort_recovery(stop, "常驻恢复在探测阶段被停止。")

        if not in_game:
            _restart_stage("entering")
            _log("SYS", "常驻恢复：日志还没显示在局内，尝试进入对战……")
            result = battle_entry.enter_battle(
                deck_index=None,
                click_start=False,
                foreground=_focus_hearthstone_if_present,
                logger=lambda text: _log("SYS", text),
                # 重连成功要立刻停手转续打：手动急停 或 日志显示已在局内，都中止点击。
                should_abort=lambda: battle_entry.is_cancelled()
                or _resident_log_in_game(fsm),
            )
            in_game = _resident_log_in_game(fsm)
            if not result.get("ok") and not in_game:
                if result.get("stage") == "aborted" or _resident_stop_reason():
                    return _resident_abort_recovery(
                        "cancelled", "常驻恢复已取消：收到手动急停请求。"
                    )
                message = result.get("message") or "自动进入对战未完成"
                _resident_alert(
                    f"常驻恢复失败：{message}；不会启动自动化，等待人工处理。"
                )
                _resident_finish_recovery(False)
                _restart_finish(
                    "recover_failed", False, f"常驻恢复：进入对战失败（{message}）"
                )
                return False

        stop = _resident_stop_reason()
        if stop:
            return _resident_abort_recovery(stop, "常驻恢复在进入对战后被停止。")

        if in_game:
            # 重连分支：已在局内就跳过模式/卡组，直接续跑（不点安全中心点）。
            _log("SYS", "常驻恢复：日志显示已在局内（重连成功），直接续跑自动化。")
        else:
            mode = str(cfg.get("mode") or "standard")
            _restart_stage("selecting_mode")
            stop = _resident_stop_reason()
            if stop:
                return _resident_abort_recovery(stop, "常驻恢复在切模式前被停止。")
            _log("SYS", f"常驻恢复：选择对战模式 {mode}……")
            picked = _resident_select_mode_with_retry(mode)
            if not picked.get("ok"):
                if _resident_stop_reason():
                    return _resident_abort_recovery(
                        "cancelled", "常驻恢复已取消：收到停止/急停请求。"
                    )
                return _resident_select_failed(
                    f"常驻恢复失败：切换模式「{mode}」未成功"
                    f"（{picked.get('error')}）；不会启动自动化。",
                )
            stop = _resident_stop_reason()
            if stop:
                return _resident_abort_recovery(stop, "常驻恢复在选模式后被停止。")
            deck_index = cfg.get("deck_index")
            if deck_index:
                _restart_stage("selecting_deck")
                stop = _resident_stop_reason()
                if stop:
                    return _resident_abort_recovery(stop, "常驻恢复在选卡组前被停止。")
                _log("SYS", f"常驻恢复：选择第 {int(deck_index)} 个卡组……")
                picked_deck = _resident_select_deck_with_retry(int(deck_index))
                if not picked_deck.get("ok"):
                    if _resident_stop_reason():
                        return _resident_abort_recovery(
                            "cancelled", "常驻恢复已取消：收到停止/急停请求。"
                        )
                    return _resident_select_failed(
                        f"常驻恢复失败：选第 {int(deck_index)} 个卡组未成功"
                        f"（{picked_deck.get('error')}）；不会启动自动化。",
                    )

        _restart_stage("starting")
        # api_start 前最后一道闸：用户暂停/取消/停止、常驻被关闭都绝不再启动。
        stop = _resident_stop_reason()
        if stop:
            return _resident_abort_recovery(stop, "常驻恢复在启动自动化前被停止。")
        if not _resident_enabled():
            _log("WARN", "常驻已被关闭：本次恢复不启动自动化。")
            _resident_finish_recovery(False)
            _resident_disable()
            _restart_finish("cancelled", False, "守护已关闭")
            return False
        started = api_start({"keep_score": True, "keep_schedule": True})
        if not (started or {}).get("ok"):
            message = (started or {}).get("error") or "未知原因"
            _resident_alert(f"常驻恢复失败：重新开始自动化失败：{message}")
            _resident_finish_recovery(False)
            _restart_finish(
                "recover_failed", False, f"常驻恢复：重新开始自动化失败：{message}"
            )
            return False

        count = _resident_note_recovery_success()
        # 次数放在最前面：浮窗「最近事件」只有约 18 个字符宽，放后面会被截掉。
        note = (f"常驻第 {count} 次恢复成功（本次运行累计）："
                f"自动化已重新开始，继续挂机。")
        _log("SYS", f"✅ {note}")
        _restart_finish("recovered", True, "常驻恢复：已重启并继续挂机")
        _resident_finish_recovery(True, note)
        return True
    except Exception as exc:
        traceback.print_exc()
        _resident_alert(f"常驻恢复异常：{exc}")
        _resident_finish_recovery(False)
        _restart_finish("exception", False, f"常驻恢复异常：{exc}")
        return False
    finally:
        with _restart_lock:
            _RESTART_STATE["running"] = False


# ---- 状态 / API / 浮窗回调
def _resident_state() -> dict:
    """常驻状态 = 监督器 state() + 重启恢复进度 + 最近告警（status 与浮窗共用）。"""
    supervisor = RESIDENT if RESIDENT is not None else _resident_supervisor()
    if supervisor is not None:
        try:
            state = dict(supervisor.state() or {})
        except Exception as exc:
            state = {
                "enabled": False,
                "state": "error",
                "message": f"常驻状态读取失败：{exc}",
            }
    else:
        settings = _resident_settings()
        state = {
            "enabled": bool(settings.get("enabled")),
            "state": "disabled",
            "mode": settings.get("mode", "standard"),
            "deck_index": settings.get("deck_index"),
            "message": "常驻判定模块未就绪",
        }
    snapshot = _restart_snapshot()
    state["recover_stage"] = snapshot.get("stage")
    state["recover_running"] = bool(snapshot.get("running"))
    state["recover_source"] = snapshot.get("source")
    state["recover_ok"] = snapshot.get("ok")
    state["recover_message"] = snapshot.get("message")
    state["alert"] = _RESIDENT_ALERT.get("text")
    state["alert_at"] = _RESIDENT_ALERT.get("at")
    # 提前告警（warn_seconds）：只提示、不动作；网页/浮窗状态行显示。
    # 监督器已复位（warned=False）时自动隐藏，避免恢复完成后网页/CLI 一直挂着
    # 旧的 ⚠️；没有 warned 字段的旧监督器（或测试替身）按"显示"处理。
    warned = state.get("warned")
    if warned is None or bool(warned):
        state["warning"] = _RESIDENT_WARN.get("text")
        state["warning_at"] = _RESIDENT_WARN.get("at")
    else:
        state["warning"] = None
        state["warning_at"] = None
    state["arm_pending"] = bool(_RESIDENT_ARM_PENDING)
    state["arm_running"] = bool(_RESIDENT_ARM_RUNNING)
    # 手动停止标记（跨控制台重启）：浮窗/网页/CLI 据此显示「上次是手动退出，
    # 本次保持暂停等手动开始」，和「错误后自动开始」区分开。
    manual = _resident_intent_state()
    state["manual_stop"] = bool(manual.get("manual_stop"))
    state["manual_stop_scope"] = manual.get("scope") or None
    state["manual_stop_reason"] = manual.get("reason") or None
    state["manual_stop_at"] = manual.get("at") or None
    state["start_cause"] = _resident_start_cause()
    # 「每次恢复成功」的累计次数（本次控制台运行）：浮窗常驻页/日志、网页、CLI 共用。
    state["recoveries_total"] = int(_RESIDENT_RECOVERY_COUNT)
    # 定时窗口作为常驻的外层约束：等待原因 + 当前计划起止时间，网页/浮窗/CLI
    # 都从 /api/status.resident 直接读，不用再拼顶层字段。
    state["waiting_reason"] = state.get("waiting_reason")
    try:
        with CTRL.lock:
            schedule = dict(CTRL.schedule or {})
    except Exception:
        schedule = {}
    state["schedule_start"] = (
        schedule["start"].isoformat(timespec="minutes")
        if schedule.get("start")
        else None
    )
    state["schedule_end"] = (
        schedule["end"].isoformat(timespec="minutes")
        if schedule.get("end")
        else None
    )
    return state


def _resident_busy_reason():
    """常驻倒计时/启动/装配/恢复中拒绝手动点按的提示；不忙时返回 None。

    只在**装配层**拦手：手动模式/卡组/进对战与常驻自己抢鼠标会互相打断。
    """
    if _RESIDENT_ARM_RUNNING:
        return "守护正在开启装配（进对战/选模式/选卡组）；请等它结束再手动操作。"
    supervisor = RESIDENT
    if supervisor is None:
        return None
    try:
        state = str((supervisor.state() or {}).get("state") or "")
    except Exception:
        return None
    if state == "countdown":
        return "守护正在倒计时；请等它开始后再手动操作。"
    if state == "starting":
        return "守护正在启动自动化；请稍候再手动操作。"
    if state == "recovering":
        return "守护正在自动恢复；请等它结束后再手动操作。"
    return None


def _resident_resume() -> None:
    supervisor = _resident_supervisor()
    if supervisor is None:
        return
    # 注意：**不在这里清 CTRL.stopped_by**。只有 api_resident 的显式开启意图才允许
    # 清停止标记；纯设置保存（浮窗「保存常驻设置」/hsctl set）不能把暂停的常驻
    # 重新拉起来。
    try:
        supervisor.resume()
    except Exception as exc:
        _log("WARN", f"常驻恢复启动失败：{exc}")


def _resident_resume_after_restart() -> bool:
    """手动重启炉石完成：把被这次重启打断的常驻恢复起来。

    只在重启开始前常驻确实在管事（``_resident_will_act()``）时才调用。清掉重启
    期间可能残留的停止标记、登记一次装配待办并 resume，让监督器重新把自动化
    拉起来。常驻已被关闭时不恢复（保持现状）。

    **手动停止优先级最高**：如果重启期间用户按了 Ctrl+Q/停止（取消开关已置位，
    或监督器 paused_reason 是用户停止来源），这里保持暂停、绝不恢复；否则在
    清停止标记/登记装配待办之前先清掉残留取消信号，避免装配一开头就被 abort。
    """
    if not _resident_enabled():
        _log("SYS", "手动重启完成；常驻未开启，不自动恢复。")
        return False
    stop = _resident_user_stop_reason()
    if stop:
        _log("WARN", f"手动重启被用户取消/停止（{stop}），常驻保持暂停。")
        return False
    try:
        import battle_entry

        battle_entry.clear_cancel()
    except Exception:
        pass
    try:
        with CTRL.lock:
            CTRL.stopped_by = None
    except Exception:
        pass
    _resident_set_arm_pending(True)
    _resident_resume()
    _log("SYS", "手动重启完成，守护已恢复。")
    return True


def _resident_disable() -> None:
    supervisor = _resident_supervisor()
    if supervisor is None:
        return
    try:
        supervisor.disable()
    except Exception as exc:
        _log("WARN", f"常驻关闭失败：{exc}")


def api_resident(body: dict = None) -> dict:
    """读取/保存「守护」（原常驻）配置（ui_config.json 的 resident 段）。

    body 为空 → 只读回当前配置与状态（网页读取走 /api/status）。
    支持字段：enabled / mode / deck_index / 各阈值（含 warn_seconds 提前告警）/
    dry_run；越界值由 config.normalize_resident_settings 收敛。enabled 从关→开
    且自动化在跑时拒绝。
    """
    body = body or {}
    try:
        from config import normalize_resident_settings
    except Exception as exc:
        return {"ok": False, "error": f"守护配置不可用：{exc}"}
    cfg = load_config()
    section = cfg.get("resident")
    section = dict(section) if isinstance(section, dict) else {}
    current = normalize_resident_settings(section)
    if not body:
        return {
            "ok": True,
            "resident": _resident_state(),
            "settings": current,
            "message": "守护状态与当前配置",
        }

    updated = dict(current)
    if body.get("enabled") is not None:
        updated["enabled"] = _as_bool(body.get("enabled"))
    if body.get("mode") not in (None, ""):
        updated["mode"] = str(body.get("mode"))
    if "deck_index" in body:
        raw = body.get("deck_index")
        if raw in (None, ""):
            updated["deck_index"] = None
        else:
            try:
                updated["deck_index"] = int(raw)
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "error": "deck_index 必须是 1-9 的整数（或留空）",
                }
    for key in (
        "stale_in_game_seconds",
        "stale_other_seconds",
        "warn_seconds",
        "window_seconds",
        "check_seconds",
        "settle_seconds",
        "start_delay_seconds",
    ):
        if body.get(key) in (None, ""):
            continue
        try:
            updated[key] = float(body[key])
        except (TypeError, ValueError):
            return {"ok": False, "error": f"{key} 必须是数字（秒）"}
    if body.get("max_restarts") not in (None, ""):
        try:
            updated["max_restarts"] = int(body["max_restarts"])
        except (TypeError, ValueError):
            return {"ok": False, "error": "max_restarts 必须是整数"}
    if body.get("dry_run") is not None:
        updated["dry_run"] = _as_bool(body.get("dry_run"))
    updated = normalize_resident_settings(updated)

    # 「开启并开始」意图：网页主按钮 / 浮窗一键恢复都会带 start=True。
    # 与「只保存设置」区分开：保存不启动，start 才会进入装配/开局；
    # 自动化正在运行时也能开启（只接管监督，不打断当前对局）。
    start_intent = bool(_as_bool(body.get("start"))) or (
        str(body.get("action") or "").strip().lower() in ("start", "arm")
    )
    if start_intent:
        updated["enabled"] = True
        updated = normalize_resident_settings(updated)

    turning_on = bool(updated["enabled"]) and not bool(current["enabled"])
    # 「显式开启意图」：body 里真的带了 enabled=true（浮窗一键恢复/CLI on）。
    # 浮窗「保存常驻设置」与 hsctl set 只带 mode/阈值，**不代表**用户要恢复运行；
    # 暂停中的常驻必须保持暂停（手动停止=暂停，重新开启才继续）。
    explicit_on = bool(updated["enabled"]) and (
        "enabled" in body and body.get("enabled") is not None
    )
    with CTRL.lock:
        automation_running = bool(
            CTRL.automation_thread is not None or CTRL.starting
        )
    if turning_on and not start_intent:
        if automation_running:
            return {"ok": False, "error": "先停止自动化再开启守护。"}

    # N11：先登记“装配待办”再落盘，避免 start_delay=0 时监督器在保存前/后
    # 抢跑看到“已开启但没待办”而做出错误派发。自动化正在跑时不派装配（会
    # 打断当前对局），只接管监督。
    if (turning_on or start_intent) and not automation_running:
        _resident_set_arm_pending(True)
    elif start_intent or turning_on:
        _resident_set_arm_pending(False)

    cfg["resident"] = dict(updated)
    save_config(cfg)

    if updated["enabled"]:
        if turning_on or explicit_on or start_intent:
            # B4：CTRL.stopped_by 不会自行清零；只有显式开启/恢复才清掉它并
            # resume（否则倒计时一结束就被判 paused、永远不 arm）。
            with CTRL.lock:
                CTRL.stopped_by = None
            # 显式开启常驻 = 用户手动开始的意图：解除跨重启的「手动停止」标记，
            # 并让装配日志按「常驻开启」（不是「自启接管」）记录。
            _resident_set_start_cause("manual")
            _resident_clear_manual_stop("显式开启常驻")
            _resident_resume()
            delay = float(updated.get("start_delay_seconds") or 0.0)
            if start_intent:
                if automation_running:
                    message = (
                        "守护已开启：当前对局由守护监督，本局结束后会自动重开；"
                        "点「暂停守护」可随时停手。"
                    )
                else:
                    message = (
                        f"守护已开启：{delay:.0f}s 后自动按"
                        f"「{updated['mode']} · 卡组 {updated['deck_index'] or '未指定'}」"
                        "进入对战并开始。"
                    )
            elif turning_on:
                message = (
                    f"守护已开启（{updated['mode']}，{delay:.0f}s 后自动装配）；"
                    "停止自动化即暂停。"
                )
            else:
                message = "守护已恢复（倒计时后自动接管）。"
        else:
            # 纯设置保存：不动运行态；暂停中保持暂停，不 resume、不清 stopped_by。
            state = str((_resident_state() or {}).get("state") or "")
            if state in ("paused", "paused_exhausted", "error"):
                message = "设置已保存（常驻仍暂停）。"
            else:
                message = "守护设置已保存。"
    else:
        _resident_set_arm_pending(False)
        _resident_disable()
        # 正在跑恢复/装配时关常驻：把在点的循环也停掉（不点任何坐标），
        # 收尾由各自 worker 走 cancelled 分支。
        try:
            with _restart_lock:
                recovering = bool(_RESTART_STATE.get("running"))
            if recovering or _RESIDENT_ARM_RUNNING:
                import battle_entry

                battle_entry.request_cancel()
        except Exception:
            pass
        message = "守护已关闭。"
    _log("SYS", message)
    return {
        "ok": True,
        "message": message,
        "resident": _resident_state(),
        "settings": updated,
    }


def _overlay_resident():
    """浮窗「常驻」页状态回调（出异常返回空 dict，浮窗显示占位）。"""
    try:
        return _resident_state()
    except Exception:
        return {}


def _overlay_resident_toggle():
    """浮窗「暂停常驻/开启常驻」按钮：返回一句话消息或 None。"""
    try:
        enabled = bool(_resident_state().get("enabled"))
        result = api_resident({"enabled": not enabled})
        if result.get("ok"):
            return result.get("message")
        return result.get("error") or "切换常驻失败"
    except Exception as exc:
        return f"切换常驻失败：{exc}"


def _overlay_resident_apply(payload):
    """浮窗「保存常驻设置」：接收 dict，返回 {"ok", "message"}。"""
    try:
        result = api_resident(dict(payload or {}))
        return {
            "ok": bool(result.get("ok")),
            "message": result.get("message") or result.get("error") or "",
        }
    except Exception as exc:
        return {"ok": False, "message": f"保存常驻设置失败：{exc}"}


# ---------------------------------------------------------------- 界面识别 / 一键进入对战
# 「现在在哪个界面」由 battle_entry 认：**只认文字**（灰度 NCC，在可校准框
# ±40px 内找「传统对战」/「选择套牌」的图案）。选卡组界面认「选择套牌」标题
# （始终在）而不是右下角「开始」（没选中卡组时不出现）。先看清楚再动手 ——
# 认不出来就什么都不点，超时退出并说明原因。
_UI_STATE_LABELS = {
    "main_menu": "主界面（认出「传统对战」）",
    "deck_select": "选择套牌界面（认出「选择套牌」标题）",
    "unknown": "没认出界面",
}
_ENTER_STATE = {
    "running": False,
    "stage": "",
    "ok": None,
    "message": "",
    "started_at": None,
    "finished_at": None,
    "steps": [],
}
_ENTER_LOCK = threading.Lock()


def _ui_info() -> dict:
    """界面识别的公共信息：四个框的位置、9 个点击点、当前卡组设置。"""
    import battle_entry

    return {
        "boxes": {
            "traditional": list(battle_entry.traditional_box()),
            "deck_title": list(battle_entry.deck_select_title_box()),
            "start": list(battle_entry.start_box()),
            "deck_grid": list(battle_entry.deck_grid_box()),
        },
        "slot_points": [list(point) for point in battle_entry.deck_slot_points()],
        "deck_index": battle_entry.battle_entry_settings().get("deck_index"),
        "templates": {
            kind: str(battle_entry.TEMPLATE_DIR / name)
            for kind, name in battle_entry.TEMPLATE_FILE.items()
        },
    }


def api_ui_state(frame_name: str = "") -> dict:
    """认出当前界面（只读）：state / 匹配分 / 认到的位置 / 9 格证据。

    `frame_name` 非空时顺带把这一帧存成 PNG（`tmp_screens/<name>`）——
    agent 可以直接读那张图核对"它到底看到了什么"。
    """
    import battle_entry

    result = {"ok": True, "enter_battle": dict(_ENTER_STATE)}
    result.update(_ui_info())
    try:
        frame, mapping = battle_entry.capture_client_frame()
    except Exception as exc:
        return {"ok": False, "error": f"抓图失败：{exc}", "ui": None, **result}
    if frame is None:
        return {
            "ok": False,
            "ui": None,
            **result,
            "error": "抓不到炉石客户区（炉石未运行或已最小化）",
        }
    ui = battle_entry.identify(frame)
    result["ui"] = ui
    result["state"] = ui.get("state")
    result["state_label"] = _UI_STATE_LABELS.get(ui.get("state"), "未知")
    if mapping is not None:
        import display_mapping

        result["mapping"] = display_mapping.mapping_info(mapping)
    if frame_name:
        try:
            import numpy as np
            from PIL import Image

            rgb = frame[:, :, :3][:, :, ::-1]  # BGRA/BGR → RGB
            image = Image.fromarray(np.ascontiguousarray(rgb))
            saved = _save_frame(image, {"kind": "ui_frame"}, out=frame_name)
            result["frame_path"] = saved.get("path")
            result["frame_latest"] = saved.get("latest")
        except Exception as exc:
            result["frame_error"] = str(exc)
    return result


def api_ui_learn(body=None) -> dict:
    """按当前（可校准）框从**当前画面**重裁识别模板。

    皮肤/补丁把字改了颜色或反色之后，匹配分会掉下来；站到对应界面调一次这里
    就能自愈，框坐标不用动。框里没有字（一片平坦）时拒绝学习并说明原因。
    """
    body = body or {}
    kind = str(body.get("kind") or "all")
    import battle_entry

    with _ENTER_LOCK:
        if _ENTER_STATE["running"]:
            return {"ok": False, "error": "正在进入对战，等它结束再重学模板。"}
    try:
        result = battle_entry.learn_template(kind)
    except Exception as exc:
        return {"ok": False, "error": f"重裁模板失败：{exc}"}
    if not result.get("ok"):
        _log("WARN", f"重裁模板未成功：{result.get('message')}")
        return {"ok": False, "error": result.get("message") or "没有学到模板", **result}
    _log("SYS", result["message"])
    return dict(result)


def api_save_deck(body) -> dict:
    """查看/清理旧的「自动选卡组」设置（`battle_entry.deck_index`）。

    用户 2026-10-23 口径：**自动化不再每局自动点卡组**（会覆盖手选）。
    选卡组只走显式遥控：
      * `deck-click N`：现在立即点第 N 格（自动化未运行时）；
      * `enter-battle --deck N`：进入对战时顺带选第 N 格。
    本接口只保留 `{"clear": true}` 用来清掉本机旧值；带 index 的保存请求
    一律拒绝并提示改用上面的命令。
    """
    body = body or {}
    if not body.get("clear"):
        return {
            "ok": False,
            "error": (
                "自动选卡组已停用（不会再每局自动点卡组）。"
                "要现在选：deck-click N；进入对战时选：enter-battle --deck N。"
            ),
        }
    from config import save_battle_entry_setting

    try:
        saved = save_battle_entry_setting("deck_index", None)
    except (KeyError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"清空旧卡组设置失败：{exc}"}
    value = saved.get("deck_index")
    message = (
        "已清空旧的自动选卡组设置（自动化本来就不再自动点卡组）。"
        "要选卡组请用 deck-click N / enter-battle --deck N。"
    )
    _log("SYS", message)
    return {"ok": True, "deck_index": value, "message": message}


def api_enter_battle_status() -> dict:
    """只看「进入对战」的进度（**不抓图**，所以 CLI 轮询它很便宜）。"""
    with _ENTER_LOCK:
        state = dict(_ENTER_STATE)
    return {"ok": True, "enter_battle": state}


def api_enter_battle(body=None) -> dict:
    """一键进入对战：拉起炉石 → 等主界面 → 点「传统对战」 → 选卡组 →（可选）点「开始」。

    自动化正在跑时直接拒绝：鼠标归 FSM 管，两边同时点会互相打断。
    同步做参数预检，之后丢后台线程跑（可能要等几十秒到几分钟），进度写日志，
    也可以从 `GET /api/ui_state` 的 `enter_battle` 字段查。
    """
    import battle_entry

    body = body or {}
    # 常驻倒计时/恢复中：两边同时点会互相打断，先拒绝手动进对战。
    resident_guard = _resident_busy_reason()
    if resident_guard:
        return {"ok": False, "error": resident_guard}
    if CTRL.automation_thread is not None or CTRL.starting:
        return {
            "ok": False,
            "error": "自动化已经在运行中；它会自己进对战，不需要手动进。",
        }
    # 新的一次手动流程开始：先清掉上一轮可能残留的取消信号，否则会立刻自停。
    battle_entry.clear_cancel()
    raw_index = body.get("deck_index")
    deck_index = None
    if raw_index not in ("", None):
        try:
            deck_index = int(raw_index)
        except (TypeError, ValueError):
            return {"ok": False, "error": "deck_index 必须是 1-9 的整数（或省略）"}
        if not 1 <= deck_index <= 9:
            return {"ok": False, "error": "deck_index 必须是 1-9（当前页只有 9 格）"}
    try:
        interval = float(body.get("interval") or battle_entry.CHECK_INTERVAL_SECONDS)
        timeout = float(body.get("timeout") or battle_entry.MAIN_MENU_TIMEOUT_SECONDS)
    except (TypeError, ValueError):
        return {"ok": False, "error": "interval / timeout 必须是数字（秒）"}
    if interval <= 0 or timeout <= 0:
        return {"ok": False, "error": "interval / timeout 必须大于 0"}
    click_start = bool(body.get("click_start"))
    with _ENTER_LOCK:
        if _ENTER_STATE["running"]:
            return {"ok": False, "error": "上一次「进入对战」还没结束，请稍候。"}
        _ENTER_STATE.update(
            {
                "running": True,
                "stage": "launch",
                "ok": None,
                "message": "正在进入对战……",
                "steps": [],
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "finished_at": None,
            }
        )

    def _worker():
        try:
            result = battle_entry.enter_battle(
                deck_index=deck_index,
                click_start=click_start,
                interval=interval,
                main_menu_timeout=timeout,
                foreground=_focus_hearthstone_if_present,
                logger=lambda text: _log("SYS", text),
            )
            for step in result.get("steps") or []:
                at = f" @{step['at']}" if step.get("at") else ""
                _log("SYS", f"进入对战·{step['stage']}：{step.get('detail') or ''}{at}")
            with _ENTER_LOCK:
                _ENTER_STATE.update(
                    {
                        "running": False,
                        "stage": result.get("stage", ""),
                        "ok": bool(result.get("ok")),
                        "message": result.get("message", ""),
                        "steps": result.get("steps") or [],
                        "finished_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
            _log(
                "SYS" if result.get("ok") else "WARN",
                f"进入对战{'完成' if result.get('ok') else '未完成'}："
                f"{result.get('message')}",
            )
        except Exception as exc:
            traceback.print_exc()
            with _ENTER_LOCK:
                _ENTER_STATE.update(
                    {
                        "running": False,
                        "stage": "error",
                        "ok": False,
                        "message": str(exc),
                        "finished_at": datetime.now().isoformat(timespec="seconds"),
                    }
                )
            _log("ERROR", f"进入对战失败：{exc}")

    _log(
        "SYS",
        "收到「进入对战」请求：拉起炉石 → 认主界面 → 点「传统对战」 → "
        + (f"选第 {deck_index} 个卡组 → " if deck_index else "")
        + ("点「开始」" if click_start else "停在选择套牌界面"),
    )
    threading.Thread(target=_worker, name="hs-enter-battle", daemon=True).start()
    return {
        "ok": True,
        "message": "已开始进入对战（进度见日志与 /api/ui_state 的 enter_battle）。",
        "enter_battle": dict(_ENTER_STATE),
    }


def api_deck_click(body=None) -> dict:
    """快速选卡组：确认当前在「选择套牌」界面后，**直接点**第 N 格（同步、秒级）。

    与 `enter_battle` 的区别：不做"等主界面→点传统对战"那套状态机，
    只做一次带保护的单击 —— `battle_entry.select_deck` 会先抓一帧确认这一格
    名牌上有白字（真的像个卡组）才点，否则一格都不点。
    自动化正在跑时拒绝：鼠标归 FSM 管，两边同时点会互相打断。
    """
    import battle_entry

    body = body or {}
    # 常驻倒计时/恢复中：两边同时点会互相打断，先拒绝手动点卡组。
    resident_guard = _resident_busy_reason()
    if resident_guard:
        return {"ok": False, "error": resident_guard}
    if CTRL.automation_thread is not None or CTRL.starting:
        return {
            "ok": False,
            "error": "自动化已经在运行中；它会自己选卡组，不需要手动点。",
        }
    with _ENTER_LOCK:
        if _ENTER_STATE["running"]:
            return {"ok": False, "error": "「进入对战」正在跑，等它结束再点卡组。"}
    try:
        index = int(body.get("index"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "index 必须是 1-9 的整数"}
    # 一次性点击：先清掉上一轮残留的取消信号，否则这一下会被急停开关挡掉。
    battle_entry.clear_cancel()
    result = battle_entry.select_deck(index, logger=lambda text: _log("SYS", text))
    if result.get("ok"):
        _log("SYS", result.get("message") or f"已快速点第 {index} 个卡组")
    return dict(result)


# 「手动切换对战模式」：点右上角 (1405,55) 打开模式菜单，再点狂野/标准/休闲之一。
_MODE_CLICK_LOCK = threading.Lock()

_MODE_ALIASES = {
    "wild": "wild",
    "standard": "standard",
    "casual": "casual",
    "狂野": "wild",
    "标准": "standard",
    "休闲": "casual",
}


def _mode_click_inner(mode: str) -> dict:
    """实际点击执行体（守卫①② + 点击）。api_mode_click 与队列动作共用。"""
    import click as hs_click
    from config import load_mode_click_points

    # 守卫①：FSM 在跑 / 正在启动 → 拒绝（鼠标归 FSM 管，两边同时点会互相打断）
    if CTRL.automation_thread is not None or CTRL.starting:
        return {"ok": False, "error": "自动化已经在运行中；先 stop 再切模式。"}
    # 守卫②：enter_battle 状态机在跑 → 拒绝
    with _ENTER_LOCK:
        if _ENTER_STATE["running"]:
            return {"ok": False, "error": "「进入对战」正在跑，等它结束再切模式。"}
    # 应用用户覆盖坐标（默认值打底），参数注入进 click_mode_option，不改全局常量
    points = load_mode_click_points()
    try:
        hs_click.click_mode_option(mode, points=points)
    except Exception as exc:
        return {"ok": False, "error": f"切换模式失败：{exc}"}
    message = f"已点击切换到 {mode}（请目视确认菜单确实弹出并切换成功）"
    _log("SYS", message)
    return {"ok": True, "mode": mode, "message": message}


def api_mode_click(body=None) -> dict:
    """手动切换对战模式：wild/standard/casual（或 狂野/标准/休闲）。

    同步、约 2-3 秒。前提：画面停在「选择套牌」界面、自动化未运行。
    坐标可在 ui_config.json 顶层 `mode_click` 段覆盖。
    """
    body = body or {}
    # 常驻倒计时/恢复中：两边同时点会互相打断，先拒绝手动切模式。
    resident_guard = _resident_busy_reason()
    if resident_guard:
        return {"ok": False, "error": resident_guard}
    # 守卫③：队列在跑 → 拒绝（队列内部调用走 _mode_click_inner，不受此限）
    with _QUEUE_LOCK:
        if _QUEUE_STATE["running"]:
            return {"ok": False, "error": "命令队列正在执行，等它跑完再切模式。"}
    mode = str(body.get("mode") or "").strip().lower()
    if mode not in _MODE_ALIASES:
        return {
            "ok": False,
            "error": "mode 必须是 wild/standard/casual（或 狂野/标准/休闲）",
        }
    mode = _MODE_ALIASES[mode]
    if not _MODE_CLICK_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "上一次模式切换还没完成，请稍候。"}
    try:
        return _mode_click_inner(mode)
    finally:
        _MODE_CLICK_LOCK.release()


# 「手动投降」：点右下角齿轮 → 等游戏菜单弹出 → 点中间红色「认输」。
# 与自动投降（低胜率触发）共用 click.click_setting/click_concede 两个点击原语。
# 前置条件：正在对战中。自动化**不需要停止** —— 这正是它存在的意义：
# 自动打的时候你想立刻认输，不用先 stop（stop 只会让 FSM 停下、不会点认输，
# 卡着的对局还在打）。但为避免和 FSM 抢鼠标，跟 FSM 共用同一个会话锁。
_MANUAL_CONCEDE_LOCK = threading.Lock()


def _manual_concede_inner() -> dict:
    """实际点击执行体（手动投降）。api 与队列动作共用。"""
    import click as hs_click

    if not _MANUAL_CONCEDE_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "上一次投降点击还没完成，请稍候。"}
    try:
        with hs_click.hearthstone_action_session():
            hs_click.click_setting()  # 齿轮 (1895, 1060)
            time.sleep(1.0)  # 等游戏菜单弹出
            hs_click.click_concede()  # 认输 (960, 380)
        time.sleep(1.0)
    except Exception as exc:
        return {"ok": False, "error": f"投降点击失败：{exc}"}
    finally:
        _MANUAL_CONCEDE_LOCK.release()
    message = "已发送投降（点齿轮 → 认输）；请目视确认对局是否结束"
    _log("SYS", message)
    return {"ok": True, "message": message}


def api_concede_now(body=None) -> dict:
    """手动投降：立刻点「齿轮 → 认输」。同步、约 3 秒。

    前提：正在对战中（不在对局里点了会弹设置菜单，不会有别的副作用）。
    """
    # 队列在跑 → 拒绝（队列内部调用走 _manual_concede_inner，不受此限）
    with _QUEUE_LOCK:
        if _QUEUE_STATE["running"]:
            return {"ok": False, "error": "命令队列正在执行，等它跑完再投降。"}
    with _ENTER_LOCK:
        if _ENTER_STATE["running"]:
            return {"ok": False, "error": "「进入对战」正在跑，等它结束再投降。"}
    return _manual_concede_inner()


# ================================================================ 待做命令队列
# 一次性提交多条命令，控制台后台线程按顺序执行。断线不影响；状态可查、可取消。
_QUEUE_LOCK = threading.Lock()
_QUEUE_STATE = {
    "running": False,
    "items": [],
    "current": -1,
    "submitted_at": None,
    "finished_at": None,
    "message": "",
}

# step 字符串语法："动作 [参数...]"，参数支持 `key=value` 和裸整数（当 index/秒数）。
# 例："stop mode=after_game" / "wait-stopped timeout=300" / "deck 1" / "deck-click 8"
#     / "prepare" / "start" / "delay 5"
_QUEUE_ACTIONS = {
    "stop",
    "wait_stopped",
    "deck",
    "deck_click",
    "prepare",
    "start",
    "delay",
    "mode",
    "concede_now",
}


def _queue_parse_step(text: str) -> dict:
    """把一条 step 字符串解析成 {"action":..., "args": {...}}；不认识就抛 ValueError。"""
    tokens = str(text).split()
    if not tokens:
        raise ValueError("空命令")
    action = tokens[0].strip("-").replace("-", "_")
    if action not in _QUEUE_ACTIONS:
        raise ValueError(
            f"不认识的动作 {tokens[0]!r}（支持：{'/'.join(sorted(_QUEUE_ACTIONS))}）"
        )
    args = {}
    for token in tokens[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            args[key.strip("-").replace("-", "_")] = value
        else:
            args["index"] = token if "index" not in args else args["index"]
    return {"action": action, "args": args}


def _queue_status_locked() -> dict:
    return {"ok": True, "queue": dict(_QUEUE_STATE)}


def _queue_wait(predicate, timeout: float, poll: float = 2.0) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(poll)
    return predicate()


def _queue_run_item(action: str, args: dict) -> dict:
    """执行一条命令：尽量复用现有 api_* 处理器，保证与网页/CLI 行为一致。"""
    if action == "stop":
        return api_stop({"mode": str(args.get("mode") or "now")})
    if action == "deck":
        body = (
            {"clear": True}
            if str(args.get("clear") or "").lower() in ("1", "true", "yes")
            else {"index": int(args.get("index"))}
        )
        return api_save_deck(body)
    if action == "deck_click":
        return api_deck_click({"index": int(args.get("index"))})
    if action == "mode":
        # 走 _mode_click_inner 而不是 api_mode_click：队列自己就是执行上下文，
        # 不应被守卫③（队列在跑）自我拒绝。
        mode = str(args.get("index") or args.get("mode") or "").strip().lower()
        if mode not in _MODE_ALIASES:
            return {
                "ok": False,
                "error": "mode 必须是 wild/standard/casual（或 狂野/标准/休闲）",
            }
        return _mode_click_inner(_MODE_ALIASES[mode])
    if action == "prepare":
        return api_prepare({})
    if action == "concede_now":
        # 走 _manual_concede_inner：队列自己就是执行上下文，不被队列守卫自我拒绝。
        return _manual_concede_inner()
    if action == "start":
        return api_start(
            {
                "keep_score": str(args.get("keep_score") or "").lower()
                in ("1", "true", "yes")
            }
        )
    if action == "delay":
        seconds = min(
            300.0, max(0.0, float(args.get("index") or args.get("seconds") or 5))
        )
        time.sleep(seconds)
        return {"ok": True, "message": f"已等待 {seconds:g} 秒"}
    if action == "wait_stopped":
        timeout = float(args.get("timeout") or 600)
        ok = _queue_wait(
            lambda: CTRL.automation_thread is None and not CTRL.starting, timeout
        )
        if ok:
            return {"ok": True, "message": "自动化已停止"}
        return {"ok": False, "error": f"等自动化停止超时（{timeout:g} 秒）"}
    return {"ok": False, "error": f"未实现的动作：{action}"}


def api_queue_submit(body=None) -> dict:
    """提交待做命令队列。body={"steps": ["stop mode=after_game", "wait-stopped", "deck 1", "start"]}。

    队列在后台线程里按顺序执行；某一步失败就停下，剩下的标记 skipped。
    提交时上一次队列还没跑完会被拒绝（先 `hsctl queue cancel` 或等它跑完）。
    """
    body = body or {}
    steps = body.get("steps")
    if not isinstance(steps, list) or not steps:
        return {"ok": False, "error": "steps 必须是非空字符串数组"}
    try:
        items = [_queue_parse_step(s) for s in steps]
    except ValueError as exc:
        return {"ok": False, "error": f"命令解析失败：{exc}"}
    with _QUEUE_LOCK:
        if _QUEUE_STATE["running"]:
            return {
                "ok": False,
                "error": "上一次队列还在跑（用 /api/queue 看进度，或 /api/queue/cancel 取消）。",
            }
        _QUEUE_STATE.update(
            {
                "running": True,
                "current": -1,
                "finished_at": None,
                "submitted_at": datetime.now().isoformat(timespec="seconds"),
                "message": f"共 {len(items)} 条，执行中……",
                "items": [dict(it, status="pending", result=None) for it in items],
            }
        )

    def _worker():
        for i, item in enumerate(_QUEUE_STATE["items"]):
            with _QUEUE_LOCK:
                if not _QUEUE_STATE["running"]:
                    item["status"] = "cancelled"
                    continue
                _QUEUE_STATE["current"] = i
                item["status"] = "running"
            action, args = item["action"], item["args"]
            _log(
                "SYS",
                f"队列[{i + 1}/{len(_QUEUE_STATE['items'])}] 执行 {action} {args or ''}",
            )
            try:
                result = _queue_run_item(action, args)
            except Exception as exc:
                result = {"ok": False, "error": f"{action} 执行异常：{exc}"}
            item["result"] = result
            item["status"] = "ok" if result.get("ok") else "failed"
            _log(
                "SYS" if result.get("ok") else "ERROR",
                f"队列[{i + 1}] {action}：{result.get('message') or result.get('error')}",
            )
            if not result.get("ok"):
                with _QUEUE_LOCK:
                    for rest in _QUEUE_STATE["items"][i + 1 :]:
                        rest["status"] = "skipped"
                    _QUEUE_STATE.update(
                        {
                            "running": False,
                            "current": -1,
                            "finished_at": datetime.now().isoformat(timespec="seconds"),
                            "message": f"第 {i + 1} 步失败：{result.get('error')}",
                        }
                    )
                return
        with _QUEUE_LOCK:
            _QUEUE_STATE.update(
                {
                    "running": False,
                    "current": -1,
                    "finished_at": datetime.now().isoformat(timespec="seconds"),
                    "message": "队列全部执行完成",
                }
            )

    threading.Thread(target=_worker, name="hs-queue", daemon=True).start()
    return {
        "ok": True,
        "message": f"已提交 {len(items)} 条命令，按顺序执行。",
        "queue": dict(_QUEUE_STATE),
    }


def api_queue_get(body=None) -> dict:
    with _QUEUE_LOCK:
        return _queue_status_locked()


def api_queue_cancel(body=None) -> dict:
    with _QUEUE_LOCK:
        if not _QUEUE_STATE["running"]:
            return {"ok": False, "error": "没有正在执行的队列。"}
        _QUEUE_STATE["running"] = False
        _QUEUE_STATE["message"] = "已被用户取消（当前步跑完后停止）"
    _log("WARN", "命令队列已被取消（取消在当前这一步跑完之后生效）。")
    return {"ok": True, "message": "已请求取消队列（当前步跑完后停止）"}


def api_restart_hearthstone(body=None):
    """CLI / agent 的「重启炉石」接口（复用浮窗按钮同一套流程）。

    必须显式 confirm=true：这一步会结束 Hearthstone.exe 并清空它的日志目录，
    正在打的对局会直接没了，所以不接受裸调用。

    先做**同步预检**（路径解析），让调用方立刻拿到 ok=false 和原因；预检通过才
    丢到后台线程跑，避免 CLI 卡在这里等几十秒。
    """
    body = body or {}
    if not body.get("confirm"):
        return {
            "ok": False,
            "error": "重启炉石会结束炉石进程并清空其日志目录，"
            '请显式确认：{"confirm": true}',
        }
    cfg = load_config()
    log_root = (cfg.get("log_root") or "").strip()
    try:
        import hs_restart

        info = hs_restart.resolve_paths(log_root)
    except Exception as exc:
        return {"ok": False, "error": f"重启炉石不可用：{exc}"}
    if not info.get("ok"):
        return {"ok": False, "error": info.get("error")}
    with _restart_lock:
        if _RESTART_STATE["running"]:
            return {"ok": False, "error": "上一次重启炉石还没结束，请稍候。"}
        _restart_begin("manual")
    # 新流程开始 → 清掉上一轮残留的取消信号（否则「等主界面」会立刻自停）。
    import battle_entry as _be

    _be.clear_cancel()

    def _worker():
        try:
            _restart_hearthstone_worker()
        except Exception as exc:
            traceback.print_exc()
            _log("ERROR", f"重启炉石异常：{exc}")
            _restart_finish("exception", False, f"重启炉石异常：{exc}")
        finally:
            with _restart_lock:
                _RESTART_STATE["running"] = False

    threading.Thread(target=_worker, name="hs-restart-api", daemon=True).start()
    return {
        "ok": True,
        "message": "已开始重启炉石（停止自动化 → 结束进程 → 清空日志目录 "
        "→ 重新启动 → 等主界面），进度见 /api/restart_hearthstone。",
    }


def _overlay_exit_overlay():
    """浮窗「退出浮窗」：只写一行日志说明发生了什么（脚本继续运行）。

    同时把「就绪」状态放掉：浮窗关掉之后再点网页大按钮，必须先走「开始运行
    （准备）」把浮窗重新开起来，而不是直接被当成「开始对战」一点就开打。
    """
    try:
        with CTRL.lock:
            CTRL.prepared = False
    except Exception:
        pass
    _log(
        "SYS",
        "浮窗已退出（脚本与自动化继续运行）。再次开始：网页点「开始运行（准备）」"
        "→再点「开始对战」；只想看日志就点「🪟 日志浮窗」。",
    )


def _overlay_account_visible() -> bool:
    """「账号」行当前是否显示昵称（默认显示）。"""
    try:
        from config import overlay_settings

        return bool(overlay_settings().get("show_account", True))
    except Exception:
        return True


def _overlay_save_account_visible(visible: bool) -> None:
    """把「账号」行的显示偏好写进 ui_config.json 的 overlay 段。"""
    try:
        from config import save_overlay_setting

        save_overlay_setting("show_account", bool(visible))
        _log(
            "SYS",
            "浮窗「账号」行已"
            + ("显示战网昵称。" if visible else "隐藏战网昵称（点眼睛按钮可恢复）。"),
        )
    except Exception as exc:
        _log("WARN", f"保存浮窗显示偏好失败：{exc}")


def api_toggle_overlay(body=None):
    """开/关右上角实时日志浮窗。

    就绪状态跟着浮窗走：关掉浮窗 = 回到未就绪（网页大按钮变回「开始运行（准备）」
    而不是「开始对战」，避免浮窗都没开就被一点直接开打）；重新开出来 = 就绪。
    """
    if log_overlay is None:
        return {"ok": False, "error": "日志浮窗模块不可用"}
    if log_overlay.is_running():
        log_overlay.stop()
        with CTRL.lock:
            CTRL.prepared = False
        return {
            "ok": True,
            "enabled": False,
            "message": "日志浮窗已关闭（已回到未就绪；点「开始运行」重新准备）",
        }
    status = _bind_overlay()
    with CTRL.lock:
        CTRL.prepared = True
    if status == "pending":
        return {
            "ok": True,
            "enabled": True,
            "message": "浮窗正在重新打开（上一轮在收尾，稍等 1–2 秒就会出现）",
        }
    return {
        "ok": True,
        "enabled": True,
        "message": "日志浮窗已开启（已就绪；点「开始对战」或浮窗的 ▶ 开始）",
    }


def api_cancel_schedule():
    with CTRL.lock:
        if CTRL.automation_thread is not None:
            return {"ok": False, "error": "自动化运行中；如需停止请使用停止按钮。"}
        CTRL.schedule = {"start": None, "end": None}
        CTRL.phase = "idle"
        CTRL.scheduler_thread = None
    _persist_schedule(None, None)
    _log("SYS", "定时任务已取消。")
    return {"ok": True, "message": "定时任务已取消"}


def check_log_dir(path: str):
    path = str(path or "").strip()
    if not path:
        return {"exists": False, "checked": False, "message": "请先输入日志目录。"}
    p = Path(path)
    if not p.is_dir():
        return {"exists": False, "checked": True, "message": "目录不存在，请检查路径。"}
    from power_log import find_latest_power_log, find_latest_session_dir

    session = find_latest_session_dir(p)
    plog = find_latest_power_log(p)
    result = {
        "exists": True,
        "checked": True,
        "session": session.name if session else None,
        "power_log": str(plog) if plog else None,
        "power_log_found": plog is not None,
    }
    if plog:
        result["message"] = "✅ 找到最新对局日志 Power.log"
    elif session:
        result["message"] = "找到会话目录，暂无 Power.log（进行一局对战后自动生成）"
    else:
        result["message"] = (
            "目录存在，但未发现炉石会话子目录（Hearthstone_时间戳 形式）"
        )
    return result


def status_snapshot():
    with CTRL.lock:
        cfg = load_config()
        fsm = CTRL.fsm
        running = CTRL.automation_thread is not None
        phase = CTRL.phase
        if (
            running
            and fsm is not None
            and fsm.stop_after_current_game
            and phase == "playing"
        ):
            phase = "stopping"
        state = fsm.FSM_state if fsm is not None else ""
        games = int(fsm.game_count) if fsm is not None else 0
        wins = int(fsm.win_count) if fsm is not None else 0
        concedes = int(getattr(fsm, "concede_count", 0) or 0) if fsm is not None else 0
        time_begin = (
            float(getattr(fsm, "time_begin", 0.0) or 0.0) if fsm is not None else 0.0
        )
        stop_after = bool(fsm.stop_after_current_game) if fsm is not None else False
        sched = CTRL.schedule
        start_iso = (
            sched["start"].isoformat(timespec="minutes") if sched.get("start") else None
        )
        end_iso = (
            sched["end"].isoformat(timespec="minutes") if sched.get("end") else None
        )
        err = CTRL.last_error
        summary = CTRL.last_summary
        stopped_by = CTRL.stopped_by
        hotkey_ok = CTRL.hotkey_registered
        starting = CTRL.starting
        prepared = CTRL.prepared
    elapsed = int(time.time() - time_begin) if time_begin > 0 else 0
    win_rate = round(wins * 100.0 / games, 1) if games else 0.0
    # 策略选择：网页「基础配置 → 策略选择」用这里回显（key 用于选中，label 用于提示）。
    strategy_key = strategy_setting()
    strategy_info = {"key": strategy_key, "label": strategy_label(strategy_key)}
    # 盒子推荐失效兜底：enabled / strategy / 阈值（网页「基础配置」回显）。
    box_fallback_info = box_fallback_settings()
    box_fallback_info["strategy_label"] = strategy_label(
        box_fallback_info.get("strategy", ""))
    # 浮窗是否真的开着：网页用它同步「🪟 日志浮窗（开/关）」按钮文字，
    # 也能解释「为什么大按钮回到了开始运行」。
    try:
        overlay_running = bool(log_overlay is not None and log_overlay.is_running())
    except Exception:
        overlay_running = False
    # 常驻状态（判定在 R1 的监督器里；这里只读快照 + 恢复进度 + 告警）。
    resident_state = _resident_state()
    return {
        "server_time": datetime.now().isoformat(timespec="seconds"),
        # 当前跑的是哪个版本：网页顶栏和浮窗品牌行都显示这个 label。
        # 版本号 = 发布日期（v2026.10.17），浮窗、网页、CLI 显示的是同一个串。
        "version": version.version_info(),
        "is_admin": IS_ADMIN,
        "running": running,
        "starting": starting,
        "prepared": prepared,
        "overlay_running": overlay_running,
        "phase": phase,
        "stopped_by": stopped_by,
        "stop_after_game": stop_after,
        "state": state,
        "state_label": STATE_LABELS.get(state, state or "待机"),
        "in_game": state in ("Choosing Card", "Battling", "Quitting Battle"),
        "games": games,
        "wins": wins,
        "concede_count": concedes,
        "win_rate": win_rate,
        "game_elapsed_sec": elapsed,
        "schedule_start": start_iso,
        "schedule_end": end_iso,
        "concede": cfg.get("auto_concede") or dict(DEFAULT_AUTO_CONCEDE),
        "concede_detect": _concede_detect_state(),
        "auto_surrender": cfg.get("auto_surrender") or dict(DEFAULT_AUTO_SURRENDER),
        "surrenders": _surrender_state(),
        "rank_stop": _rank_stop_state(),
        "human_like": _current_human_like(),
        # 生存信号（进程/日志）：阈值已并入「守护」，这里只保留浮窗状态行用的采样值，
        # 旧的 liveness/watchdog 配置与状态字段已删除。
        "liveness": _liveness_state(),
        # 守护（原常驻）：enabled/state/目标/阈值/提前告警/重启配额/恢复进度/最近告警。
        "resident": resident_state,
        "name_match": _name_match_state(),
        "delays": _current_delays(),
        # 策略选择 / 联合模式 / 最大搜索深度（本地卡组策略，R18）。
        "strategy": strategy_info,
        "strategy_options": strategy_options(),
        "joint": joint_setting(),
        "max_search": max_search_setting(),
        # 盒子推荐失效兜底（R18K）：盒子AI 连续失败/无进展时用本地策略接管。
        "box_fallback": box_fallback_info,
        "selfcheck": selfcheck_summary(),
        "config": {"name": cfg.get("name", ""), "log_root": cfg.get("log_root", "")},
        "last_error": err,
        "last_summary": summary,
        "hotkey_ok": hotkey_ok,
    }


# ---------------------------------------------------------------- HTTP 服务
class _ControlServer(ThreadingHTTPServer):
    """本地控制台服务器。

    **必须关掉 allow_reuse_address。** Python 的 HTTPServer 默认给它开
    SO_REUSEADDR，而 Windows 上 SO_REUSEADDR 的语义是"允许绑定**别人已占用**的
    端口"（与 Unix 完全不同）。结果是 main() 里"端口被占用就顺延到 8766"的逻辑
    形同虚设：第二个实例会**成功**绑到同一个端口，两个进程同时 LISTENING，
    请求随机落到其中一个 —— 网页可能打到 A 实例、浮窗属于 B 实例，而两者共用
    同一份 ui_config.json。现象是"命令发出去了但没反应/改了没生效"，极难排查。

    关掉之后，抢不到端口的实例会正常拿到 OSError 并顺延。

    注意：监听套接字在 Windows 上关闭即释放，不受 TIME_WAIT 影响，所以"停掉后
    立刻重启同一个端口"不会被这里挡住。
    """

    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    server_version = f"{version.NAME}/{version.VERSION}"

    def log_message(self, fmt, *args):
        pass  # 静默访问日志

    def _json(self, payload, code=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1024 * 1024:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf8"))
        except Exception:
            raise ValueError("请求体 JSON 解析失败")

    def do_GET(self):
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path in ("/", "/index.html"):
                html = (WEB_DIR / "index.html").read_text(encoding="utf8")
                data = html.encode("utf8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)
            elif path == "/api/status":
                self._json(status_snapshot())
            elif path == "/api/version":
                # 轻量接口：只回版本，网页顶栏/外部脚本想知道"跑的是哪一版"时
                # 不必把整份 status 拉下来。
                self._json(version.version_info())
            elif path == "/api/logs":
                q = parse_qs(parsed.query)
                try:
                    seq = int(q.get("seq", ["0"])[0])
                except (TypeError, ValueError):
                    seq = 0
                lines, max_seq = take_logs_after(seq)
                self._json({"seq": max_seq, "lines": lines})
            elif path == "/api/check_log":
                q = parse_qs(parsed.query)
                self._json(check_log_dir(q.get("path", [""])[0]))
            elif path == "/api/card_search":
                q = parse_qs(parsed.query)
                self._json(
                    api_card_search(q.get("q", [""])[0], q.get("limit", ["8"])[0])
                )
            elif path == "/api/selfcheck":
                self._json(api_selfcheck(force=False))
            elif path == "/api/regions":
                self._json(api_regions())
            elif path == "/api/ui_state":
                q = parse_qs(parsed.query)
                self._json(api_ui_state(frame_name=q.get("frame", [""])[0]))
            elif path == "/api/enter_battle":
                self._json(api_enter_battle_status())
            elif path == "/api/restart_hearthstone":
                self._json({"ok": True, "restart": _restart_snapshot()})
            elif path == "/api/queue":
                self._json(api_queue_get())
            else:
                self._json({"ok": False, "error": "未知接口"}, 404)
        except Exception as exc:
            self._json({"ok": False, "error": str(exc)}, 500)

    def do_POST(self):
        try:
            parsed = urlparse(self.path)
            body = {}
            try:
                body = self._read_json()
            except ValueError as exc:
                self._json({"ok": False, "error": str(exc)}, 400)
                return
            path = parsed.path
            if path == "/api/config":
                self._json(api_save_config(body))
            elif path == "/api/start":
                self._json(api_start(body))
            elif path == "/api/prepare":
                self._json(api_prepare(body))
            elif path == "/api/stop":
                self._json(api_stop(body))
            elif path == "/api/cancel":
                self._json(api_cancel(body))
            elif path == "/api/schedule":
                self._json(api_schedule(body))
            elif path == "/api/schedule/cancel":
                self._json(api_cancel_schedule())
            elif path == "/api/calibrate":
                self._json(api_calibrate(body))
            elif path == "/api/ui_learn":
                self._json(api_ui_learn(body))
            elif path == "/api/deck":
                self._json(api_save_deck(body))
            elif path == "/api/enter_battle":
                self._json(api_enter_battle(body))
            elif path == "/api/deck_click":
                self._json(api_deck_click(body))
            elif path == "/api/mode":
                self._json(api_mode_click(body))
            elif path == "/api/queue":
                self._json(api_queue_submit(body))
            elif path == "/api/queue/cancel":
                self._json(api_queue_cancel(body))
            elif path == "/api/restart_hearthstone":
                self._json(api_restart_hearthstone(body))
            elif path == "/api/screenshot":
                self._json(api_screenshot(body))
            elif path == "/api/feedback/export":
                self._json(api_feedback_export(body))
            elif path == "/api/feedback/status":
                self._json(api_feedback_status())
            elif path == "/api/overlay":
                self._json(api_toggle_overlay(body))
            elif path == "/api/concede":
                self._json(api_save_concede(body))
            elif path == "/api/surrender_rules":
                self._json(api_save_surrender_rules(body))
            elif path == "/api/concede_now":
                self._json(api_concede_now(body))
            elif path == "/api/rank_stop":
                self._json(api_save_rank_stop(body))
            elif path == "/api/human_like":
                self._json(api_save_human_like(body))
            elif path == "/api/resident":
                # 守护（原常驻）：POST 保存/开关；读取走 /api/status 的 resident 字段。
                self._json(api_resident(body))
            elif path == "/api/delays":
                self._json(api_save_delays(body))
            elif path == "/api/strategy":
                self._json(api_save_strategy(body))
            elif path == "/api/joint":
                self._json(api_save_joint(body))
            elif path == "/api/max_search":
                self._json(api_save_max_search(body))
            elif path == "/api/box_fallback":
                self._json(api_save_box_fallback(body))
            elif path == "/api/selfcheck":
                self._json(api_selfcheck(force=True))
            elif path == "/api/serve/stop":
                payload, code = api_serve_stop(body)
                self._json(payload, code)
            else:
                self._json({"ok": False, "error": "未知接口"}, 404)
        except Exception as exc:
            traceback.print_exc()
            self._json({"ok": False, "error": str(exc)}, 500)


# ---------------------------------------------------------------- 自停通道
# 为什么需要它：控制台常常是**提权**起来的（serve start --via-task / --elevate），
# 而 Windows 不允许非提权进程结束提权进程 —— `hsctl serve stop` 的 taskkill 会
# 拿到 Access denied，于是"脚本停不掉"，只能让人用管理员 shell 再关一次。
# 这里让控制台**自己**退：只要能在 127.0.0.1 上发一个带 token 的 POST，它就自己
# 收尾退出。因为发起者不需要任何特权，agent / 手机 / 普通 shell 都能用。
#
# 安全边界：token 随机生成、只写在本机端口文件里（log/ui_port.txt，随 log/ 不入库），
# 且接口只回 127.0.0.1 —— 网络上打进来的请求拿不到 token，也就停不掉。
_SERVE_TOKEN = secrets.token_urlsafe(24)


def api_serve_stop(body=None):
    """控制台自停：校验 token 后收尾退出。返回 (payload, http_code)。

    这是 `hsctl serve stop` 的首选通道（不需要提权）。真正的退出放在子线程里，
    因为要先把响应写回给调用方，再停自动化、清端口文件、退出进程。
    """
    body = body or {}
    token = str(body.get("token") or "")
    if not token or not secrets.compare_digest(token, _SERVE_TOKEN):
        _log("WARN", "收到自停请求但 token 不对，已拒绝。")
        return (
            {
                "ok": False,
                "error": "token 不对：自停只接受本机端口文件里的 token（log/ui_port.txt）",
            },
            403,
        )
    _log("SYS", "收到自停请求：停自动化 → 清理端口文件 → 退出控制台。")
    keep_intent = bool(body.get("keep_intent"))
    threading.Thread(
        target=_self_shutdown,
        args=(keep_intent,),
        name="hs-self-stop",
        daemon=True,
    ).start()
    return {"ok": True, "message": "已受理：控制台正在退出"}, 200


def _self_shutdown(keep_intent: bool = False) -> None:
    """自停收尾：先优雅停自动化，再清端口文件，最后退出进程。

    ``keep_intent=True`` 供 `hsctl serve restart` 使用：重启只是换一份代码，
    不是「用户不玩了」，所以不写「手动停止」标记（重启后常驻照常自动接管）。
    """
    try:
        if CTRL.automation_thread is not None:
            try:
                if keep_intent:
                    api_stop(
                        {"mode": "now", "_resident_internal": True,
                         "keep_schedule": True}
                    )
                else:
                    api_stop({"mode": "now"})
            except Exception:
                pass
            deadline = time.time() + 15
            while time.time() < deadline and CTRL.automation_thread is not None:
                time.sleep(0.2)
    except Exception:
        pass
    # 手动停止控制台（hsctl serve stop / 网页自停）：落盘标记，看门狗以后不自动拉起。
    # restart 传 keep_intent=True，不算手动退出。
    if not keep_intent:
        try:
            _resident_record_manual_stop(
                "控制台自停（hsctl serve stop）", scope="console"
            )
        except Exception:
            pass
    _clear_port_file()
    # 用 os._exit：这是"自己结束自己"，不需要（也拿不到）任何特权。
    os._exit(0)


# ---------------------------------------------------------------- 启动
def _install_console_ctrl_handler() -> bool:
    """关闭控制台窗口/控制台中断时，先落盘「手动停止」标记再让系统收尾。

    为什么需要：点窗口右上角 × 或用任务栏关闭，进程是被系统直接结束的，web_ui
    的收尾代码一行都不会跑。不在这里写标记，下次启动就会把「用户手动关窗」当成
    崩溃，按「错误后自动开始」装配/点鼠标——正是用户反馈的问题。
    """
    global _CONSOLE_CTRL_HANDLER
    if os.name != "nt" or _CONSOLE_CTRL_HANDLER is not None:
        return False
    try:
        from ctypes import wintypes

        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def _handler(event):
            # 1 = CTRL_BREAK_EVENT，2 = CTRL_CLOSE_EVENT（关窗口）。
            # CTRL_C 交给 Python 的 KeyboardInterrupt 分支处理，不在这里抢。
            if int(event) in (1, 2):
                try:
                    _resident_record_manual_stop(
                        "关闭控制台窗口" if int(event) == 2 else "控制台中断",
                        scope="console",
                    )
                except Exception:
                    pass
            return False  # 交回系统默认处理（该退出还是退出）

        _CONSOLE_CTRL_HANDLER = handler_type(_handler)
        if not ctypes.windll.kernel32.SetConsoleCtrlHandler(
            _CONSOLE_CTRL_HANDLER, True
        ):
            _CONSOLE_CTRL_HANDLER = None
            return False
        return True
    except Exception as exc:
        _log("WARN", f"注册控制台关闭回调失败（忽略）：{exc}")
        return False


def _write_port_file(port: int) -> None:
    """把实际监听端口写给 CLI / agent（原子替换，读到的永远是一份完整 JSON）。

    端口会因为占用而顺延（BASE_PORT..BASE_PORT+19），所以消费方不该硬编码 8765。
    `token` 供自停接口校验，`executable` 供 `hsctl doctor` 判断"控制台是不是跑在
    错误的解释器上"。
    """
    try:
        PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "port": int(port),
            "host": HOST,
            "url": f"http://{HOST}:{port}",
            "pid": os.getpid(),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "token": _SERVE_TOKEN,
            "executable": sys.executable,
        }
        tmp = PORT_FILE.with_name(PORT_FILE.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf8")
        os.replace(tmp, PORT_FILE)
    except Exception as exc:
        # 写不了不影响控制台本身，只是 CLI 要退化成扫端口。
        print(f"[port] 端口文件写入失败（CLI 将扫描端口）：{exc}")


def _clear_port_file() -> None:
    """退出时删掉端口文件，避免 CLI 打到一个已经死掉的端口。"""
    try:
        PORT_FILE.unlink()
    except Exception:
        pass


def _boot_resume_schedule():
    cfg = load_config()
    s, e = cfg.get("schedule_start"), cfg.get("schedule_end")
    if not s:
        return
    try:
        start_dt = datetime.fromisoformat(s)
        end_dt = datetime.fromisoformat(e) if e else None
    except Exception:
        return
    now = datetime.now()
    if now < start_dt:
        with CTRL.lock:
            CTRL.schedule = {"start": start_dt, "end": end_dt}
            CTRL.phase = "waiting"
        t = threading.Thread(
            target=_schedule_worker,
            args=(start_dt, end_dt),
            name="hs-scheduler",
            daemon=True,
        )
        CTRL.scheduler_thread = t
        t.start()
        _log("SYS", f"恢复未完成的定时任务：{start_dt:%m-%d %H:%M} 开始。")
    else:
        _persist_schedule(None, None)
        _log("WARN", "上次保存的定时任务已过期，已自动取消，请在页面重新设置。")


def main():
    os.chdir(ROOT)
    # 关闭控制台窗口（×/注销/中断）时先落盘「手动停止」标记：否则下次启动会把
    # 用户手动关窗当成崩溃，按「错误后自动开始」自动装配/点鼠标。
    if _resident_running_as_console():
        _install_console_ctrl_handler()
    # DPI 感知必须最先声明：未声明时 Windows 会把客户区几何按系统缩放虚拟化
    # （150% 下 1920×1080 报成 1280×720），而抓图是物理像素 —— 两者对不上会
    # 让所有 ROI 错位、界面识别失败。见 display_mapping 模块 docstring。
    try:
        from display_mapping import dpi_aware

        dpi_aware()
    except Exception:
        pass
    # 常驻阶段监测线程：停止自动化也继续检测当前阶段（随时可恢复）
    threading.Thread(target=_stage_monitor_loop, name="hs-stage", daemon=True).start()
    # 守护（原常驻）监督器：判定在 src/safety/resident_supervisor.py，
    # 这里起线程 + 注入真实信号。热键注册之前起，保证启动即能接管
    # 「自动化没在跑但守护开着」的续跑判定。
    _bind_resident()
    # 全局 Ctrl+Q 急停：随控制台常驻（不跟自动化的生死走），空闲/手动进对战
    # 期间也能一键叫停正在点的动作。
    _register_hotkey()
    # 常驻前台守护线程：每 30 分钟把炉石切回最前台
    threading.Thread(
        target=_hearthstone_foreground_guard, name="hs-fg-guard", daemon=True
    ).start()
    cfg = load_config()
    _apply_constants(cfg.get("name") or "", cfg.get("log_root") or "")
    _boot_resume_schedule()
    # 启动即自检：Python 版本 / 依赖包 / 分辨率和缩放（结果进日志与页面）。
    start_selfcheck_async()
    # 线上最新版本号：后台线程每 6 小时查一次（断网只显示“获取失败”，不影响运行）。
    update_check.start_periodic()

    server = None
    port = None
    for port in range(BASE_PORT, BASE_PORT + 20):
        try:
            server = _ControlServer((HOST, port), Handler)
            break
        except OSError:
            continue
    if server is None:
        print("无法绑定端口，请检查 8765-8785 是否被占用。")
        sys.exit(1)
    threading.Thread(target=server.serve_forever, name="hs-web", daemon=True).start()
    _write_port_file(port)

    url = f"http://{HOST}:{port}"
    print("=" * 64)
    print("  HSLegendArriver Web 控制台")
    print(f"  请在浏览器中打开: {url}")
    if not IS_ADMIN:
        # 非管理员 = 自动化点击会被系统拦掉（脚本在跑但点了没反应），
        # 控制台横幅放大加粗、重复三遍，避免一开窗就被忽略。
        _print_admin_warning()
    print("  随时按 Ctrl+Q 急停（自动化 / 手动进对战都停）；关闭本窗口即退出控制台。")
    print("=" * 64)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        # Ctrl+C 退出控制台也算「用户手动退出」：落盘标记，下次启动保持暂停。
        _resident_record_manual_stop("Ctrl+C 退出", scope="console")
        print("\n控制台已退出。")
    finally:
        _clear_port_file()


if __name__ == "__main__":
    main()
