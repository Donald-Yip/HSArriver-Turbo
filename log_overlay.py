# -*- coding: utf-8 -*-
"""Right-top live log overlay via tkinter (topmost, translucent, draggable).

start() launches a background thread with a tkinter window pinned to the
top-right corner. It streams the latest automation log lines; lines that mark
the own-turn start are highlighted green. The window can be dragged by holding
the left mouse button. Failures disable the overlay and never crash the caller.

start() 返回 OPEN_ALREADY / OPEN_STARTED / OPEN_PENDING，调用方（web_ui）据此
判断浮窗到底开没开；网页/浮窗重开在收尾竞争、上一轮线程超时等情况下都**不会
再静默失败**（见 mark_closing / _defer_open / _stop_generation）。

Buttons:
  * 开始对战              — start automation
  * 中止 / 恢复           — toggle stop/resume (state-aware)
  * 本局结束后停止         — toggle; cancel anytime before the match ends
  * 校准                  — open/close the on-screen calibration window
  * 保存日志              — dump the battle log to a file
  * 打开日志 / 清理日志    — open the log files in Explorer / delete them
  * 重启炉石              — confirm, then kill Hearthstone + wipe logs + relaunch
  * 退出浮窗              — close only this window (script keeps running)
  * 退出脚本              — stop automation and exit the whole process
  * 最小化 / 展开          — collapse to a title bar / restore (标题行右侧)
Button labels are **plain text on purpose**: the old ▶/⏹/⏸/💾/♻/🚪/📊 glyphs
have no glyphs in Microsoft YaHei / Segoe UI and rendered as tofu boxes.
Every button hands the foreground back to Hearthstone afterwards.
"""
from __future__ import annotations

import datetime
import gc
import os
import re
import threading
import time
from collections import deque

# 脚本日志（对战日志 / 运行日志）的体积、命名与清理都在 script_logs.py 里；
# 浮窗只用它的 format_size 与 new_battle_log_path，避免两份实现打架。
import script_logs

_LOCK = threading.Lock()
_LINES: deque = deque(maxlen=2000)
_STARTED = [False]
_REFRESH_MS = 350
# 当前浮窗线程：start() 时用来等上一轮彻底收尾，避免两个 Tk 解释器交叠
# （见 _join_previous_thread 的注释）。
_THREAD = None
# 等上一轮结束时最多等多久（秒）。窗口销毁后线程只剩回收动作，正常远小于这个值。
_SHUTDOWN_TIMEOUT = 3.0
# 上一轮收尾超时后，排队等它结束的兜底上限（秒）。超过就如实报错，不再重试。
OPEN_WAIT_SECONDS = 10.0
# start() 的三种结果：已经开着 / 新窗口线程已起 / 排队等上一轮收尾后再开。
OPEN_ALREADY = "already"
OPEN_STARTED = "started"
OPEN_PENDING = "pending"
# 「退出浮窗」按下后**立刻**置位：网页在这之后的任何时刻点「开始运行」都要
# 重新把浮窗开出来，而不是被当成"浮窗还开着"（见 mark_closing 的注释）。
_CLOSING = threading.Event()
# start() 的检查与建线程必须成一个原子动作：网页的 /api/prepare 是并发处理的，
# 双击按钮时两个请求同时进来不能各建一个 Tk 解释器。
_START_LOCK = threading.Lock()
# 已经有一轮"等上一轮收尾再开窗"在排队，避免重复排队。
_OPEN_PENDING = threading.Event()
# 浮窗内部异常的日志回调（web_ui 注册后，"日志浮窗禁用: XXX" 会进网页日志）。
_REPORTER = None
# 每一代窗口一个编号：旧窗口延迟执行的收尾（root.after(150, stop)）只能关掉
# 它自己那一代，绝不能把刚被网页重开出来的新窗口一起关掉。
_GENERATION = [0]

# ---- flat dark palette ---------------------------------------------------
BG = "#171b24"
PANEL = "#202634"
TITLE_BG = "#10141c"
TEXT = "#e8ecf2"
DIM = "#8b93a3"
GREEN = "#5fd68a"
ACCENT = "#4aa3ff"
DANGER = "#e05e4b"
WARN = "#d98a2e"
OK = "#2ea06b"
GOLD = "#e8b93b"
DISABLED = "#394050"
# 「退出浮窗」用中性灰：它不是危险操作（脚本继续跑），别和红色的「退出脚本」混淆。
NEUTRAL = "#4a5364"

# 浮窗整体不透明度（0.94 = 轻微半透明，既能看到底下的游戏，又不影响阅读）。
# 单独提出来是为了可测/可调（截图脚本会临时设为 1.0 以免把桌面图标叠进图里）。
ALPHA = 0.94

# 状态面板右侧“说明”列最多显示多少个字符：列宽固定，太长会被窗口边缘裁掉，
# 宁可截断加省略号（完整内容在网页/日志里都能看到）。
_DETAIL_LIMIT = 16
# 点了眼睛按钮后「账号」行显示的文字（隐藏昵称，保留匹配与否）。
_ACCOUNT_HIDDEN_TEXT = "已隐藏"
# 眼睛按钮：同一个图标，靠颜色区分状态（不加叉号，更干净）。
#   绿色 = 正在显示账号昵称；白色 = 已隐藏账号昵称。
EYE_ICON = "👁"
EYE_COLOR_ON = GREEN
EYE_COLOR_OFF = TEXT

# ---- 按钮布局 ----------------------------------------------------------
# 一行两个，「本局结束后停止」单独占一行（避免和「中止」挨着被误点）；
# 按钮字号/内边距都比原来小，省下来的高度留给日志区。
BTN_FONT_SIZE = 9
BTN_PADY = 4
BTN_LAYOUT = {
    "start": (0, 0),
    "halt": (0, 1),
    "stop_after": (1, 0),
    "calibrate": (2, 0),
    "save": (2, 1),
    "open_logs": (3, 0),
    "clear_logs": (3, 1),
    "restart": (4, 0),
    "exit_overlay": (4, 1),
    "exit": (5, 0),
}
# 需要横跨整行的按钮（单独一行）。
BTN_SPAN = {"stop_after": 2, "exit": 2}

# 浮窗尺寸：宽度按最长的一行文字/按钮定，高度 = 品牌行 + 状态面板（4 行）
# + 五行按钮（开始/中止、本局结束后停止、校准/保存日志、重启炉石/退出浮窗、
# 退出脚本）+ 日志区。
# 宽度保持用户要求的收窄值（292 → 263）：状态行说明放不下时靠 wraplength
# **换行**，而不是把窗口加宽（加宽过一次，用户反馈"整个浮窗又变宽了"）。
WINDOW_WIDTH = 263
# 654 - 17（删掉副标题）→ 637；再 +56 给新增的「日志」状态行与「打开日志 /
# 清理日志」按钮行，日志正文仍保持 9 行左右。
WINDOW_HEIGHT = 693
# 最小化（折叠成标题条）：浮窗是 WS_EX_TOOLWINDOW，没有任务栏条目，
# 真 iconify() 之后用户没有任何入口还原，所以折叠成标题条 + 「展开」按钮。
MINIMIZED_HEIGHT = 34
# 标题行右侧的小按钮：只用文字（拿块状字形当图标会像多了一个下划线）。
MINIMIZE_TEXT = "最小化"
RESTORE_TEXT = "展开"

# 状态行内布局（左→右）：● 名称 数值 说明 …（「账号」行最右还有眼睛按钮）。
# 说明列能用的宽度 = 窗口宽 - 这一行其它列，实测按控件真实宽度算（见 _status_row），
# 所以缩放/字体不同也不会算歪。
_MARKER_PAD = (10, 4)     # ● 的左右内边距
_CELL_GAP = 6             # 名称 / 数值 / 说明之间的间距
_DETAIL_PAD = 10          # 说明列右侧内边距
_EYE_W, _EYE_PAD = 24, (2, 8)   # 眼睛按钮与它的内边距（只有「账号」行）
# 说明列最窄也要留这么多，不然换行会碎成一列单字。
_DETAIL_MIN_PX = 60
# 说明最多占几行：超过就截断加省略号，避免极端长文把日志区挤没。
_DETAIL_MAX_LINES = 3

# 「日志」状态行：清理完成后这 10 秒内，说明列显示"已清理 X"。
LOG_CLEAR_FLASH_SECONDS = 10.0
# 体积阈值（与 web_ui.LOG_WARN_BYTES / LOG_DANGER_BYTES 一致，兜底用）。
LOG_WARN_BYTES = 100 * 1024 * 1024
LOG_DANGER_BYTES = 500 * 1024 * 1024

# 浮窗顶部品牌行：本项目大名（放在“自动化日志”标题之前）。
# 副标题「炉石传说 · 自动对战」已按用户要求删除。
BRAND_NAME = "HSLegendArriver"
# 状态圆点：功能开着 = 绿，关着 = 红，状态未知 = 灰。
MARKER_ON = GREEN
MARKER_OFF = DANGER
MARKER_UNKNOWN = DIM


def detail_wrap_px(width: int, used_px: int,
                   min_px: int = _DETAIL_MIN_PX) -> int:
    """状态行「说明」列的可用宽度：整行宽度减去"圆点+名称+数值+间距"。

    used_px 由调用方按控件真实宽度算出（tkinter 量得到），这里只是把
    "至少留 min_px" 这条规则单独拎出来，方便脱离 Tk 测试。
    """
    return max(int(min_px), int(width) - int(used_px))


def logs_row(info, now=None) -> dict:
    """浮窗「日志」状态行：{'marker', 'value', 'value_color', 'detail'}。

    info 来自 web_ui._overlay_logs()（script_logs.scan() 的快照）：
        bytes / files / battle_bytes / runtime_bytes / warn_bytes /
        danger_bytes / cleared_at / cleared_bytes
    体积越大圆点越"热"：<100MB 绿、<500MB 黄、≥500MB 红（阈值由 web_ui 传进来，
    这里只兜底）。刚清理完的 10 秒内，说明列显示"已清理 X"，让用户看到结果。
    """
    if info is None:
        return {"marker": MARKER_UNKNOWN, "value": "—", "value_color": DIM,
                "detail": ""}
    try:
        total = int(info.get("bytes") or 0)
        files = int(info.get("files") or 0)
    except (TypeError, ValueError):
        return {"marker": MARKER_UNKNOWN, "value": "—", "value_color": DIM,
                "detail": ""}
    try:
        warn = int(info.get("warn_bytes") or LOG_WARN_BYTES)
        danger = int(info.get("danger_bytes") or LOG_DANGER_BYTES)
    except (TypeError, ValueError):
        warn, danger = LOG_WARN_BYTES, LOG_DANGER_BYTES
    if total >= danger:
        color = DANGER
    elif total >= warn:
        color = WARN
    else:
        color = GREEN
    try:
        cleared_at = float(info.get("cleared_at") or 0.0)
        cleared_bytes = int(info.get("cleared_bytes") or 0)
    except (TypeError, ValueError):
        cleared_at, cleared_bytes = 0.0, 0
    current = time.time() if now is None else float(now)
    if cleared_at and current - cleared_at < LOG_CLEAR_FLASH_SECONDS:
        detail = f"已清理 {script_logs.format_size(cleared_bytes)}"
    else:
        detail = f"{files} 个文件"
    return {"marker": color, "value": script_logs.format_size(total),
            "value_color": color, "detail": detail}


def fit_text(text, max_px: int, measure, ellipsis: str = "…") -> str:
    """把说明文字裁进给定位宽（超出加省略号，不再被窗口边缘切掉）。

    measure 是"这段文字有多宽"的函数（Tk 传 tkinter.font.Font.measure，
    测试里可以直接给一个按字符数算的假函数），这样这条逻辑不依赖 Tk。
    """
    text = str(text or "").strip()
    if not text:
        return ""
    try:
        if measure(text) <= max_px:
            return text
    except Exception:
        return text
    try:
        ellipsis_px = measure(ellipsis)
    except Exception:
        ellipsis_px = 0
    kept = ""
    for char in text:
        try:
            width = measure(kept + char)
        except Exception:
            return text
        if width + ellipsis_px > max_px:
            break
        kept += char
    # 文字本身可能已经带省略号（account_row 的 _clip）：别叠成两个。
    kept = kept.rstrip("…").rstrip()
    return f"{kept}{ellipsis}" if kept else ellipsis


def minimized_geometry(width: int, height: int, x: int, y: int,
                       collapsed: bool) -> str:
    """折叠/展开时的窗口几何串（折叠只剩标题条那么高）。"""
    size = MINIMIZED_HEIGHT if collapsed else height
    return f"{width}x{size}+{x}+{y}"


def _turn_start(line: str) -> bool:
    return ("回合" in line and "延时" in line) or ("轮到己方" in line)


# ---- 内部异常上报 ------------------------------------------------------
# 浮窗自己开不出来时（tkinter 不可用、Tk 初始化异常……）必须留下痕迹：
# 以前只 print 到控制台，用户看不到就以为"点了没反应"。web_ui 会用
# set_logger() 把这里的文字接进网页日志（ui_log_last.txt）。
def set_logger(callback) -> None:
    """注册浮窗内部异常的日志回调（传 None 可取消）。"""
    global _REPORTER
    _REPORTER = callback


def _report(text: str) -> None:
    """浮窗内部异常：打控制台 + 交给上层日志（没注册回调时只打控制台）。"""
    try:
        print(f"[overlay] {text}")
    except Exception:
        pass
    callback = _REPORTER
    if callback is None:
        return
    try:
        callback(str(text))
    except Exception:
        pass


# 需要“醒目”显示在浮窗里的日志行：存活检测/昵称不匹配等必须马上被看见的告警。
_ALERT_MARKERS = ("⚠️", "[ERROR]", "ERROR]", "不匹配", "已退出", "无响应",
                  "疑似卡死")


def _is_alert_line(line: str) -> bool:
    return any(marker in line for marker in _ALERT_MARKERS)


# ---- 日志正文按标签着色 ------------------------------------------------
# 只用浮窗现有调色板里的颜色（GREEN/ACCENT/GOLD/WARN/DANGER/TEXT/DIM），
# 不引入新颜色：一眼看出这一行是“盒子意见 / 实际操作 / 系统提示 / 警告 / 报错”。
# 注：ERROR 级日志走 alert（红色加粗），所以这里不再单列 error。
LOG_TAG_COLORS = {
    "alert": DANGER,   # ⚠️ 告警 / [ERROR] 报错（加粗，优先级最高）
    "warn": WARN,      # [WARN] 警告
    "turn": GREEN,     # 我方回合开始
    "reco": ACCENT,    # [推荐] 盒子建议
    "exec": GOLD,      # [执行] 实际操作
    "sys": TEXT,       # [SYS] 系统/阶段/延时
    "dim": DIM,        # 等待对手、INFO 等次要信息
}


def log_line_tag(line: str) -> str:
    """给一条日志行挑显示标签（对应 LOG_TAG_COLORS 里的键）。

    优先级：告警/报错 > WARN > 我方回合 > [推荐] > [执行] > [SYS] > 次要。
    ERROR 与 ⚠️ 都归到 alert（红色加粗），因为这两类都是“必须马上看见”。
    """
    text = str(line)
    if _is_alert_line(text):
        return "alert"
    if "WARN]" in text or text.startswith("[WARN"):
        return "warn"
    if _turn_start(text):
        return "turn"
    if text.lstrip().startswith("[推荐]"):
        return "reco"
    if text.lstrip().startswith("[执行]"):
        return "exec"
    if "[SYS]" in text or "SYS]" in text:
        return "sys"
    return "dim"



_DELAY = None
# 浮窗只显示最近 _MAX_LINES 行（超出丢弃最旧行，仅影响显示）。
_MAX_LINES = 500
# 完整正文日志缓存：不做行数丢弃，供“保存日志”写出全部历史。
_FULL_LINES = []
_delay_start_re = re.compile(r"(?:延时|等待)\s*(\d+(?:\.\d+)?)\s*s?\s*后")
_delay_end_markers = ("延时结束", "延时完毕")


def _delay_desc(line: str) -> str:
    """从延时日志行里提炼一句人类可读的说明（供进度条下方显示）。"""
    text = _delay_start_re.sub("", line)
    text = text.replace("[SYS]", "").replace("……", "").replace("…", "")
    text = text.replace(".", "").strip().strip("：:，, ")
    return text or "延时"


def _update_delay_from_line(line: str) -> bool:
    """从日志行识别延时起点/终点，驱动浮窗底部延时进度条。

    返回 True 表示本行是延时信息（已被进度条消费，不再进正文日志）。
    """
    global _DELAY
    start = _delay_start_re.search(line)
    if start:
        total = float(start.group(1))
        if "换牌重试" in line:
            label = "换牌重试"
        elif "换牌" in line:
            label = "换牌延时"
        elif "回合" in line:
            label = "回合延时"
        else:
            label = "延时"
        desc = _delay_desc(line)
        _DELAY = {
            "label": label, "desc": desc,
            "total": total, "started": time.time(),
        }
        # <1s 的短延时（如操作后 0.5s）进度条一闪而过，仍保留在正文日志
        # 以便看清；长延时只驱动进度条、不进正文。
        return total < 1.0
    if any(marker in line for marker in _delay_end_markers):
        _DELAY = None
        return True
    return False


def _window_fingerprint(lines):
    """浮窗待渲染窗口的内容指纹：(条数, 首行, 末行)。

    只要新行入队(条数增)或旧行被滑出(首行变)，指纹就变；满 _MAX_LINES 后
    每进一行必然滑出一行，条数不变但首/末行都变，指纹仍会变化——这保证了
    基于指纹的“整体重建”不会像基于计数下标的增量渲染那样在 500 行后停刷。
    """
    return (len(lines),
            lines[0] if lines else None,
            lines[-1] if lines else None)


def push(line: str, _level: str = "INFO") -> None:
    if not _STARTED[0]:
        return
    line = str(line).rstrip()
    if not line.strip():
        return
    is_turn_start = "轮到己方" in line
    with _LOCK:
        delay_only = _update_delay_from_line(line)
        # 完整日志缓存：所有日志行（含延时行）都保留，供保存完整写出。
        _FULL_LINES.append(line)
        if delay_only:
            return  # 延时行只驱动进度条，不写浮窗正文
        # _LINES 只保留最近 _MAX_LINES 行供浮窗显示（不影响完整缓存）。
        _LINES.append((line, _turn_start(line)))
        if len(_LINES) > _MAX_LINES:
            # _LINES 是 deque，不支持切片删除，改用 popleft 丢弃最旧行。
            # 仅影响浮窗显示；保存仍用 _FULL_LINES 完整写出。
            while len(_LINES) > _MAX_LINES:
                _LINES.popleft()
    if is_turn_start:
        # 我方回合开始：把炉石唤回前台，避免 OCR 被其他窗口挡住。
        _raise_hearthstone()


def _save_log() -> str:
    """把当前对战日志写入 logs/ 子目录，返回保存路径。

    目录与命名统一由 script_logs 提供（浮窗的「日志」体积行、「清理日志」按钮
    认的就是同一批文件名，不能各写一份）。
    """
    path = script_logs.new_battle_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        lines = list(_FULL_LINES)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 对战日志 {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n")
        f.write("\n".join(lines))
        if lines:
            f.write("\n")
    return str(path)


_STOP = threading.Event()


_ON_START = None
_ON_HALT = None
_IS_RUNNING = None
_ON_STOP_AFTER = None
_IS_STOP_AFTER = None
_IS_IN_GAME = None
_SCORE = None
_ON_EXIT = None
_HUMAN_LIKE = None
_CONCEDE_DETECT = None
_LIVENESS = None
_ACCOUNT = None
# 「账号」行是否显示昵称（点眼睛按钮切换；由 start() 用保存的偏好初始化）。
_ACCOUNT_VISIBLE = [True]
_ON_TOGGLE_ACCOUNT = None
# 「校准」按钮：开/关屏幕上的校准窗口（web_ui 传入，网页按钮同一个窗口）。
_ON_CALIBRATE = None
# 「校准」窗口的收尾回调：关浮窗时把还开着的校准窗口一并关掉。
_ON_CALIBRATE_CLOSE = None
# 「重启炉石」按钮：中止进程 → 清空日志目录 → 重新拉起（web_ui 传入）。
# 确认弹窗在浮窗里完成，确认后才调这个回调。
_ON_RESTART = None
# 「日志」状态行：脚本日志的体积/个数（web_ui 传入，带缓存）。
_LOGS = None
# 「打开日志」/「清理日志」按钮（web_ui 传入；清理前浮窗先弹确认）。
_ON_OPEN_LOGS = None
_ON_CLEAR_LOGS = None
# 「退出浮窗」按钮：只关这个窗口，脚本继续跑（web_ui 传入，用来写一行网页日志）。
_ON_EXIT_OVERLAY = None


def account_visible() -> bool:
    return bool(_ACCOUNT_VISIBLE[0])


def toggle_account_visibility() -> bool:
    """眼睛按钮：切换「账号」行昵称的显示，并把选择交给上层持久化。

    返回切换后的状态（True = 显示昵称）。持久化失败只影响“记住偏好”，
    不影响本次切换本身。
    """
    _ACCOUNT_VISIBLE[0] = not _ACCOUNT_VISIBLE[0]
    if _ON_TOGGLE_ACCOUNT is not None:
        try:
            _ON_TOGGLE_ACCOUNT(_ACCOUNT_VISIBLE[0])
        except Exception:
            pass
    return _ACCOUNT_VISIBLE[0]


def human_like_row(info) -> dict:
    """浮窗“活人感”状态行：{'marker','value','value_color','detail'}。

    info 形如 {"enabled", "hand_hover_enabled", "minion_hover_enabled",
    "post_delay_min", "post_delay_max", "hover_min", "hover_max",
    "minion_hover_min", "minion_hover_max"}；None / 取不到时显示“—”，
    表示无法确认。detail 只显示当前**真正生效**的参数，避免关掉「看卡牌」
    之后还挂着 0.5~3s 的随机延时让人以为还在用：
      * 两个悬停都关 → “两个悬停都已关”；
      * 只开「看随从」→ “看随从 0.2~1.5s”（没有随机延时）；
      * 开了「看卡牌」→ 保持原来的 “0.5~3s · 悬停 0.2~1s”（不加长，
        浮窗只有 263px 宽）。
    """
    if info is None:
        return {"marker": MARKER_UNKNOWN, "value": "—", "value_color": DIM,
                "detail": ""}
    if not info.get("enabled"):
        return {"marker": MARKER_OFF, "value": "关", "value_color": DIM,
                "detail": ""}
    hand_on = bool(info.get("hand_hover_enabled", True))
    minion_on = bool(info.get("minion_hover_enabled", True))
    if not hand_on and not minion_on:
        detail = "两个悬停都已关"
    elif not hand_on:
        m_lo, m_hi = info.get("minion_hover_min"), info.get("minion_hover_max")
        detail = (f"看随从 {m_lo:g}~{m_hi:g}s"
                  if m_lo is not None and m_hi is not None else "仅看随从")
    else:
        lo, hi = info.get("post_delay_min"), info.get("post_delay_max")
        h_lo, h_hi = info.get("hover_min"), info.get("hover_max")
        detail = f"{lo:g}~{hi:g}s" if lo is not None and hi is not None else ""
        if h_lo is not None and h_hi is not None:
            detail += (" · " if detail else "") + f"悬停 {h_lo:g}~{h_hi:g}s"
    return {"marker": MARKER_ON, "value": "开", "value_color": GREEN,
            "detail": detail}


def concede_detect_row(info) -> dict:
    """浮窗“自动投降检测”状态行：{'marker','value','value_color','detail'}。

    info 形如 {"enabled", "threshold", "rounds", "rate", "streak",
    "checked_turn", "triggered"}；rate=None 表示该回合没读到胜率。
    """
    if info is None:
        return {"marker": MARKER_UNKNOWN, "value": "—", "value_color": DIM,
                "detail": ""}
    if not info.get("enabled"):
        return {"marker": MARKER_OFF, "value": "关", "value_color": DIM,
                "detail": ""}
    rounds = info.get("rounds") or 0
    streak = info.get("streak") or 0
    if info.get("triggered"):
        return {"marker": MARKER_ON, "value": "已触发认输", "value_color": DANGER,
                "detail": f"连续 {streak}/{rounds}"}
    rate = info.get("rate")
    if rate is None:
        turn = info.get("checked_turn")
        where = f"第 {turn} 回合" if turn else "本回合"
        return {"marker": MARKER_ON, "value": "未读到", "value_color": WARN,
                "detail": f"{where} · 连续 {streak}/{rounds}"}
    threshold = info.get("threshold")
    below = threshold is not None and float(rate) < float(threshold)
    if threshold is None:
        detail = f"连续 {streak}/{rounds}"
    elif below:
        detail = f"低于阈值 {float(threshold):.0f}% · 连续 {streak}/{rounds}"
    else:
        detail = f"阈值 {float(threshold):.0f}% · 连续 {streak}/{rounds}"
    return {"marker": MARKER_ON,
            "value": f"{float(rate):.0f}%",
            "value_color": WARN if below else TEXT,
            "detail": detail}


def hearthstone_row(info) -> dict:
    """浮窗「炉石」存活状态行：{'marker','value','value_color','detail'}。

    info 来自 src/safety/hearthstone_liveness.py 的 sample()：
        status: ok | warning | stale | gone | idle | disabled | unknown
    """
    if info is None:
        return {"marker": MARKER_UNKNOWN, "value": "—", "value_color": DIM,
                "detail": ""}
    status = info.get("status")
    age = info.get("log_age")
    if status == "disabled":
        return {"marker": MARKER_OFF, "value": "关", "value_color": DIM,
                "detail": ""}
    if status == "gone":
        return {"marker": MARKER_OFF, "value": "已退出", "value_color": DANGER,
                "detail": "已自动停止"}
    if status == "stale":
        return {"marker": MARKER_OFF, "value": "无响应", "value_color": DANGER,
                "detail": f"日志停滞 {age:.0f}s" if age is not None else ""}
    if status == "warning":
        return {"marker": MARKER_ON, "value": "疑似卡死", "value_color": WARN,
                "detail": f"日志停滞 {age:.0f}s" if age is not None else ""}
    if status == "idle":
        return {"marker": MARKER_UNKNOWN, "value": "未运行", "value_color": WARN,
                "detail": ""}
    if status == "ok":
        # 只有对局中才关心 Power.log 的新鲜度（主菜单/匹配阶段本来就安静）。
        detail = ""
        if info.get("in_game") and age is not None:
            detail = f"日志 {age:.0f}s 前"
        return {"marker": MARKER_ON, "value": "运行中", "value_color": GREEN,
                "detail": detail}
    return {"marker": MARKER_UNKNOWN, "value": "—", "value_color": DIM,
            "detail": ""}


def _clip(text, limit: int = _DETAIL_LIMIT) -> str:
    """截断过长的说明文字（列宽固定，超出会被窗口边缘裁掉）。"""
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def account_row(info, show_account: bool = True) -> dict:
    """浮窗「账号」行：配置的用户 ID 与日志玩家名是否匹配。

    info 来自 log_state.player_name_check()：matched=None 表示还没读到双方
    玩家名（无法判断）。不匹配时脚本会把整局当对手回合而不出牌，所以这里用
    红色标出来，并把日志里出现的真实昵称显示出来方便照抄。

    ``show_account=False``（点了眼睛按钮）时不显示昵称，只保留匹配与否。
    """
    if info is None or info.get("matched") is None:
        return {"marker": MARKER_UNKNOWN, "value": "—", "value_color": DIM,
                "detail": ""}
    if not show_account:
        return {"marker": MARKER_ON if info.get("matched") else MARKER_OFF,
                "value": "匹配" if info.get("matched") else "不匹配",
                "value_color": GREEN if info.get("matched") else DANGER,
                "detail": _ACCOUNT_HIDDEN_TEXT}
    names = sorted(info.get("players", {}).values())
    if info.get("matched"):
        return {"marker": MARKER_ON, "value": "匹配", "value_color": GREEN,
                "detail": _clip(names[0] if names else info.get("config"))}
    return {"marker": MARKER_OFF, "value": "不匹配", "value_color": DANGER,
            "detail": _clip(names[0] if names else info.get("config"))}


def _join_previous_thread(timeout: float = _SHUTDOWN_TIMEOUT) -> None:
    """等上一轮浮窗线程彻底收尾（含在它自己线程里回收 Tk 对象）。

    Tk 解释器必须在**创建它的线程**里销毁，否则 Tcl 会打印
    “Tcl_AsyncDelete: async handler deleted by the wrong thread”——这也是
    “急停后点恢复”最容易撞上的报错：窗口刚 destroy、旧解释器还没回收，
    新线程一分配对象触发 GC，就替别的线程把解释器回收了。
    这里宁可多等几百毫秒，也不要让两个 Tk 解释器交叠。
    """
    global _THREAD
    thread = _THREAD
    if thread is None or thread is threading.current_thread():
        return                      # 没有上一轮；或从浮窗线程自己调用（不能自 join）
    try:
        alive = bool(thread.is_alive())
    except Exception:
        alive = False               # 不是真线程（测试替身）：直接放掉即可
    if alive:
        try:
            thread.join(timeout)
            alive = bool(thread.is_alive())
        except Exception:
            alive = False
    if not alive:
        _THREAD = None


def _stop_generation(generation) -> None:
    """旧窗口的延迟收尾：只有"这一代"窗口还在时才真的收尾。

    「退出浮窗」点下后会 root.after(150, stop)：如果这期间网页已经把浮窗重新
    开出来了（新的 generation），这个迟到 150ms 的 stop 绝不能把新窗口关掉。
    """
    if generation != _GENERATION[0]:
        return
    stop()


def mark_closing() -> None:
    """「退出浮窗」按钮按下时立刻置位，不等 root.after(150, stop)。

    那 150ms 是留给"已退出浮窗"这行日志先画出来的，但**状态**必须立刻变：
    否则这段时间里网页点「开始运行（准备）」时 is_running() 还是 True，
    api_prepare 会当成"浮窗已经开着"而跳过重开 —— 用户反馈的
    「退出浮窗后再点开始就再也弹不出浮窗」就是这么来的。
    """
    _CLOSING.set()


def _defer_open(args: dict) -> bool:
    """上一轮浮窗线程还没收尾：排队等它结束后自动开窗（返回是否新排队）。

    start() 的老实现遇到这种情况是**静默 return**：网页以为浮窗开了，其实
    什么都没发生，而且之后每一次点击都会继续被吞掉。现在改成排队 + 超时
    如实报错，至少用户知道发生了什么、该重试。
    """
    if _OPEN_PENDING.is_set():
        return False
    _OPEN_PENDING.set()
    try:
        threading.Thread(target=_deferred_open_worker, args=(dict(args),),
                         name="hs-overlay-open", daemon=True).start()
    except Exception as exc:
        _OPEN_PENDING.clear()
        _report(f"浮窗重开排队失败：{type(exc).__name__}: {exc}")
        return False
    return True


def _deferred_open_worker(args: dict, timeout: float = OPEN_WAIT_SECONDS,
                          interval: float = 0.1) -> None:
    """等上一轮浮窗线程收尾，再把它重新开出来。"""
    try:
        deadline = time.time() + float(timeout)
        while _STARTED[0] and time.time() < deadline:
            time.sleep(interval)
        if _STARTED[0]:
            _report(f"上一轮浮窗线程超过 {float(timeout):.0f}s 还没结束，"
                    "这次没能把浮窗开回来；请稍后再点「🪟 日志浮窗」重试。")
            return
        start(**args)
    finally:
        _OPEN_PENDING.clear()


def start(on_start=None, on_halt=None, is_running=None,
          on_stop_after=None, is_stop_after=None,
          is_in_game=None, score_callback=None, on_exit=None,
          human_like_callback=None, concede_callback=None,
          liveness_callback=None, account_callback=None,
          account_visible_setting=None, on_toggle_account=None,
          on_calibrate=None, on_calibrate_close=None, on_restart=None,
          logs_callback=None, on_open_logs=None, on_clear_logs=None,
          on_exit_overlay=None) -> str:
    """开（或重开）浮窗，返回 OPEN_ALREADY / OPEN_STARTED / OPEN_PENDING。

    三种结果都要让调用方**知道**：以前 start() 遇到"已经开着"或"上一轮还没
    收尾"就静默 return，网页却照样报"已就绪"，于是浮窗没出来用户也得不到
    任何提示（用户反馈："退出浮窗以后，再从 web 点开始就不会弹出浮窗了"）。
    """
    global _ON_START, _ON_HALT, _IS_RUNNING, _ON_STOP_AFTER, _IS_STOP_AFTER
    global _IS_IN_GAME, _SCORE, _ON_EXIT, _HUMAN_LIKE, _CONCEDE_DETECT
    global _LIVENESS, _ACCOUNT, _ON_TOGGLE_ACCOUNT, _ON_CALIBRATE
    global _ON_CALIBRATE_CLOSE, _ON_RESTART, _ON_EXIT_OVERLAY, _THREAD
    global _LOGS, _ON_OPEN_LOGS, _ON_CLEAR_LOGS
    # 排队重开时要原样复用这一轮的绑定（start() 会重写全部回调，不能只重开窗口）。
    args = {
        "on_start": on_start, "on_halt": on_halt, "is_running": is_running,
        "on_stop_after": on_stop_after, "is_stop_after": is_stop_after,
        "is_in_game": is_in_game, "score_callback": score_callback,
        "on_exit": on_exit, "human_like_callback": human_like_callback,
        "concede_callback": concede_callback,
        "liveness_callback": liveness_callback,
        "account_callback": account_callback,
        "account_visible_setting": account_visible_setting,
        "on_toggle_account": on_toggle_account,
        "on_calibrate": on_calibrate,
        "on_calibrate_close": on_calibrate_close,
        "on_restart": on_restart,
        "logs_callback": logs_callback,
        "on_open_logs": on_open_logs,
        "on_clear_logs": on_clear_logs,
        "on_exit_overlay": on_exit_overlay,
    }
    with _START_LOCK:
        if _STARTED[0] and not _STOP.is_set() and not _CLOSING.is_set():
            return OPEN_ALREADY         # 真的开着：不重开
        if _STARTED[0] and not _STOP.is_set():
            # 「退出浮窗」按下了，但它的 root.after(150, stop) 还没到：
            # 立刻收尾再来，别把这次请求当成"浮窗还开着"吞掉。
            stop()
        _join_previous_thread()
        if _STARTED[0]:
            # 收尾超时：宁可排队等，也不要同时存在两个 Tk 解释器，更不能静默丢弃。
            _defer_open(args)
            return OPEN_PENDING
        _CLOSING.clear()
        _ON_START = on_start
        _ON_HALT = on_halt
        _IS_RUNNING = is_running
        _ON_STOP_AFTER = on_stop_after
        _IS_STOP_AFTER = is_stop_after
        _IS_IN_GAME = is_in_game
        _SCORE = score_callback
        _ON_EXIT = on_exit
        _HUMAN_LIKE = human_like_callback
        _CONCEDE_DETECT = concede_callback
        _LIVENESS = liveness_callback
        _ACCOUNT = account_callback
        _ON_TOGGLE_ACCOUNT = on_toggle_account
        _ON_CALIBRATE = on_calibrate
        _ON_CALIBRATE_CLOSE = on_calibrate_close
        _ON_RESTART = on_restart
        _LOGS = logs_callback
        _ON_OPEN_LOGS = on_open_logs
        _ON_CLEAR_LOGS = on_clear_logs
        _ON_EXIT_OVERLAY = on_exit_overlay
        if account_visible_setting is not None:
            _ACCOUNT_VISIBLE[0] = bool(account_visible_setting)
        _STOP.clear()
        _STARTED[0] = True
        # 新的一代窗口：旧窗口里排队/延迟的动作（见 _stop_generation）从此失效。
        _GENERATION[0] += 1
        # 线程入口是 _run_overlay_thread（它包住 _run，好在帧释放后回收 Tk 对象）；
        # 单独提出来是为了让测试仍能 patch _run 而不真的开窗。
        thread = threading.Thread(target=_run_overlay_thread,
                                  name="hs-log-overlay", daemon=True)
        _THREAD = thread
        thread.start()
    return OPEN_STARTED


def stop() -> None:
    """Signal the overlay thread to close its window."""
    _CLOSING.set()
    _STOP.set()
    # 屏幕上还叠着「校准」区域框时一并收掉，别让框留在桌面上。
    try:
        import region_overlay
        region_overlay.hide()
    except Exception:
        pass
    # 还开着校准窗口（网页按钮/浮窗按钮打开的是同一个窗口）时一并关掉。
    try:
        if _ON_CALIBRATE_CLOSE is not None:
            _ON_CALIBRATE_CLOSE()
    except Exception:
        pass


def is_running() -> bool:
    """浮窗是否真的在显示。

    「已经在退出中」（_STOP/_CLOSING 已置位、窗口还没销毁）算**没在跑**：
    这样网页点「开始运行」能立刻重开，而不是被当成"还开着"而无反应。
    重开时 start() 会先等上一轮线程收尾（必要时排队），不会出现两个 Tk
    解释器交叠，也不会静默失败。
    """
    return (bool(_STARTED[0]) and not _STOP.is_set()
            and not _CLOSING.is_set())



def _raise_hearthstone() -> None:
    """Return the foreground window to the Hearthstone main window.

    等效于“鼠标真点一下炉石”：先模拟按下/松开 Alt 绕过 Windows 前台锁，
    再 ShowWindow + SetForegroundWindow + BringWindowToTop。失败静默。
    """
    try:
        import ctypes
        import win32gui
        import win32con
    except Exception:
        return
    target = [None]

    def _enum(hwnd, _unused):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            return True
        low = title.lower()
        if low and ("hearthstone" in low or "炉石" in low):
            target[0] = hwnd
            return False  # stop at the first matching main window
        return True

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:
        return
    hwnd = target[0]
    if not hwnd:
        return
    # 模拟 Alt 键，授予本进程“可切换到前台”的权限（等同用户按了一次键）。
    try:
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)   # VK_MENU 按下
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)   # VK_MENU 抬起
    except Exception:
        pass
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    try:
        win32gui.BringWindowToTop(hwnd)
    except Exception:
        pass


def _disable_overlay_activation(root) -> None:
    """给浮窗加 WS_EX_NOACTIVATE/TOOLWINDOW：点它不抢炉石前台。

    普通 tkinter 窗口被点击会获得焦点，把炉石顶出“前台”，导致 OCR 的
    hearthstone_not_foreground 检查失败、自动对战停摆。加上这两个扩展样式后，
    浮窗像 HUD 一样不参与激活，鼠标点击仍可触发按钮。
    """
    try:
        import ctypes
    except Exception:
        return
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) \
            or root.winfo_id()
        GWL_EXSTYLE = -20
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_NOACTIVATE = 0x08000000
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass


# 确认弹窗的文案（抽成常量：一是方便测试锁住关键信息，二是两个危险操作共用
# 同一套弹窗实现，别各写一份）。
CONFIRM_RESTART_TITLE = "⚠  中止炉石并清空日志？"
CONFIRM_RESTART_DETAIL = (
    "将依次执行：停止自动化 → 强制结束 Hearthstone.exe → "
    "清空日志目录内容（保留 Log.config）→ 重新启动炉石。\n"
    "对局进行中会直接判负；重启后需再点「开始对战」。")
CONFIRM_RESTART_OK = "确认执行"
CONFIRM_CLEAR_TITLE = "清空脚本日志？"
CONFIRM_CLEAR_OK = "确认清空"


def confirm_clear_detail(files, battle_size, runtime_size, total_size) -> str:
    """「清理日志」确认弹窗的正文（体积/数量都由浮窗实时算出来）。"""
    return (f"将删除 logs 里的 {int(files)} 个对战日志（{battle_size}）"
            f"和运行日志 ui_log_last.txt（{runtime_size}），共 {total_size}。\n"
            "日志是排查问题的依据，删除后无法恢复；对局与设置不受影响。")


def _confirm_dialog(root, title: str, detail: str, confirm_text: str,
                    confirm_bg=DANGER) -> bool:
    """浮窗样式的确认小窗：确认返回 True，取消/Esc/关闭返回 False。

    故意不用 tkinter.messagebox：炉石全屏独占时系统对话框可能被压在游戏后面，
    这里用与浮窗完全相同的 -topmost + overrideredirect 机制，一定看得见。
    """
    try:
        import tkinter as tk
    except Exception:
        return False
    outcome = {"ok": False}
    try:
        dialog = tk.Toplevel(root)
    except Exception:
        return False
    try:
        width, height = 344, 208
        dialog.overrideredirect(True)
        dialog.attributes("-topmost", True)
        dialog.configure(bg=BG)
        root.update_idletasks()
        x = root.winfo_x() + (root.winfo_width() - width) // 2
        y = root.winfo_y() + (root.winfo_height() - height) // 2
        x = max(8, min(x, root.winfo_screenwidth() - width - 8))
        y = max(8, min(y, root.winfo_screenheight() - height - 8))
        dialog.geometry(f"{width}x{height}+{x}+{y}")

        tk.Frame(dialog, bg=confirm_bg, height=3).pack(fill="x")
        tk.Label(dialog, text=title, bg=BG, fg=confirm_bg,
                 font=("Microsoft YaHei", 11, "bold"),
                 anchor="w").pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(dialog, text=detail, bg=BG, fg=TEXT, justify="left",
                 font=("Microsoft YaHei", 9),
                 wraplength=width - 28).pack(fill="x", padx=12)

        def _close(confirm: bool):
            outcome["ok"] = bool(confirm)
            try:
                dialog.grab_release()
            except Exception:
                pass
            try:
                dialog.destroy()
            except Exception:
                pass

        row = tk.Frame(dialog, bg=BG)
        row.pack(fill="x", padx=12, pady=(12, 12), side="bottom")
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=1)
        tk.Button(row, text=confirm_text, bg=confirm_bg, fg="#ffffff",
                  activebackground=confirm_bg, activeforeground="#ffffff", bd=0,
                  font=("Microsoft YaHei", 10, "bold"), cursor="hand2",
                  command=lambda: _close(True)
                  ).grid(row=0, column=0, sticky="ew", padx=(0, 4), ipady=6)
        tk.Button(row, text="取消", bg=NEUTRAL, fg=TEXT,
                  activebackground=NEUTRAL, activeforeground=TEXT, bd=0,
                  font=("Microsoft YaHei", 10), cursor="hand2",
                  command=lambda: _close(False)
                  ).grid(row=0, column=1, sticky="ew", padx=(4, 0), ipady=6)
        dialog.bind("<Escape>", lambda _event: _close(False))
        dialog.bind("<Return>", lambda _event: _close(True))

        dialog.grab_set()
        dialog.focus_force()
        dialog.wait_window()
    except Exception:
        try:
            dialog.destroy()
        except Exception:
            pass
        return False
    return bool(outcome["ok"])


def _confirm_restart(root) -> bool:
    """「重启炉石」的确认小窗：确认返回 True。"""
    return _confirm_dialog(root, CONFIRM_RESTART_TITLE, CONFIRM_RESTART_DETAIL,
                           CONFIRM_RESTART_OK)


def _confirm_clear_logs(root, info) -> bool:
    """「清理日志」的确认小窗：把要删的体积/数量摆出来，确认返回 True。"""
    info = info or {}
    files = int(info.get("battle_files") or 0)
    detail = confirm_clear_detail(
        files,
        script_logs.format_size(info.get("battle_bytes")),
        script_logs.format_size(info.get("runtime_bytes")),
        script_logs.format_size(info.get("bytes")))
    return _confirm_dialog(root, CONFIRM_CLEAR_TITLE, detail,
                           CONFIRM_CLEAR_OK, confirm_bg=WARN)


def _run() -> None:
    try:
        import tkinter as tk
        import tkinter.font as tkfont
    except Exception as exc:
        _report(f"tkinter 不可用: {type(exc).__name__}: {exc}")
        _STARTED[0] = False
        return

    try:
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", ALPHA)
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        # 高度：品牌行 + 状态面板（5 行）+ 六行按钮都要放得下，日志区还要有
        # 9 行左右（删掉副标题、加日志行/按钮行后的尺寸见 WINDOW_HEIGHT 注释）。
        W, H = WINDOW_WIDTH, WINDOW_HEIGHT
        x = sw - W - 12
        y = 12
        root.geometry(f"{W}x{H}+{x}+{y}")
        root.configure(bg=TITLE_BG)
        _disable_overlay_activation(root)

        # 最小化 = 折叠成标题条：内容全在 content 里，折叠时藏起来只留 mini_bar。
        # （浮窗是 WS_EX_TOOLWINDOW，真 iconify() 之后没有任务栏条目可以还原。）
        collapsed = [False]
        content = tk.Frame(root, bg=TITLE_BG)
        content.pack(fill="both", expand=True)
        mini_bar = tk.Frame(root, bg=TITLE_BG, height=MINIMIZED_HEIGHT)
        mini_bar.pack_propagate(False)
        tk.Label(mini_bar, text=BRAND_NAME, bg=TITLE_BG, fg=GOLD,
                 font=("Georgia", 12, "bold")).pack(side="left", padx=(10, 0))
        # 「展开」做成一个小小的面板按钮：只有文字、悬停提亮，不再靠特殊字形。
        restore_btn = tk.Label(mini_bar, text=RESTORE_TEXT, bg=PANEL, fg=DIM,
                               font=("Microsoft YaHei", 8), cursor="hand2",
                               padx=8, pady=2)
        restore_btn.pack(side="right", padx=(0, 8), pady=6)
        restore_btn.bind("<Enter>", lambda _e: restore_btn.config(
            fg=TEXT, bg=_hover(PANEL)))
        restore_btn.bind("<Leave>", lambda _e: restore_btn.config(
            fg=DIM, bg=PANEL))

        def _set_collapsed(flag):
            """折叠/展开浮窗（同时把窗口几何改成标题条那么高）。"""
            collapsed[0] = bool(flag)
            if collapsed[0]:
                content.pack_forget()
                mini_bar.pack(fill="x")
            else:
                mini_bar.pack_forget()
                content.pack(fill="both", expand=True)
            # 用**当前位置**（不是启动位置）：用户拖动过浮窗以后，最小化/展开
            # 不该把它弹回右上角。
            root.geometry(minimized_geometry(W, H, root.winfo_x(),
                                             root.winfo_y(),
                                             collapsed=collapsed[0]))

        def _on_minimize(_event=None):
            _set_collapsed(not collapsed[0])
            return "break"

        restore_btn.bind("<Button-1>", _on_minimize)

        def _hover(c):
            # lighten a hex color for hover feedback
            try:
                r = min(255, int(c[1:3], 16) + 22)
                g = min(255, int(c[3:5], 16) + 22)
                b = min(255, int(c[5:7], 16) + 22)
                return f"#{r:02x}{g:02x}{b:02x}"
            except Exception:
                return c

        def _set(widget, **options):
            """只在值真的变化时才更新控件。

            刷新循环每 350ms 跑一次；无条件 config() 会让 Tk 每轮都重绘控件，
            浮窗持续闪烁（截屏也容易抓到画到一半的帧）。这里先比一次现值，
            没变就一个像素都不动。
            """
            changed = {}
            for key, value in options.items():
                try:
                    if str(widget.cget(key)) == str(value):
                        continue
                except Exception:
                    pass
                changed[key] = value
            if changed:
                widget.config(**changed)

        def _make_btn(parent, text_, bg, command):
            btn = tk.Button(
                parent, text=text_, bg=bg, fg="white",
                font=("Microsoft YaHei", BTN_FONT_SIZE, "bold"),
                relief="flat", bd=0, pady=BTN_PADY, cursor="hand2",
                activebackground=bg, activeforeground="white",
                command=command)
            btn.bind(
                "<Enter>", lambda _e, b=bg: btn.config(bg=_hover(b)))
            btn.bind("<Leave>", lambda _e, b=bg: btn.config(bg=b))
            return btn

        # ---- 品牌行：本项目大名（放在“自动化日志”标题之前） ------------
        # 副标题「炉石传说 · 自动对战」按用户要求删除（那一行高度留给日志区）。
        brand = tk.Frame(content, bg=TITLE_BG)
        brand.pack(fill="x")
        tk.Label(brand, text=BRAND_NAME, bg=TITLE_BG, fg=GOLD,
                 font=("Georgia", 13, "bold")).pack(pady=(9, 7))
        tk.Frame(content, bg=GOLD, height=1).pack(fill="x", padx=10)
        tk.Frame(content, bg=PANEL, height=1).pack(fill="x")

        # ---- header ----------------------------------------------------
        head = tk.Frame(content, bg=TITLE_BG)
        head.pack(fill="x")
        dot = tk.Label(head, text="●", bg=TITLE_BG, fg=GREEN,
                       font=("Segoe UI", 10))
        dot.pack(side="left", padx=(10, 4), pady=8)
        title = tk.Label(head, text="自动化日志", bg=TITLE_BG, fg=TEXT,
                         font=("Microsoft YaHei", 10, "bold"))
        title.pack(side="left", pady=8)
        # 标题行右侧的「最小化」：折叠成标题条，再点标题条上的「展开」还原。
        # 做成一枚小小的面板按钮（悬停提亮），只用文字，不再拿块状字形当图标。
        mini_btn = tk.Label(head, text=MINIMIZE_TEXT, bg=PANEL, fg=DIM,
                            font=("Microsoft YaHei", 8), cursor="hand2",
                            padx=8, pady=2)
        mini_btn.pack(side="right", padx=(0, 10), pady=6)
        mini_btn.bind("<Button-1>", _on_minimize)
        mini_btn.bind("<Enter>", lambda _e: mini_btn.config(
            fg=TEXT, bg=_hover(PANEL)))
        mini_btn.bind("<Leave>", lambda _e: mini_btn.config(
            fg=DIM, bg=PANEL))
        tk.Frame(content, bg=PANEL, height=1).pack(fill="x")

        # ---- 战绩行 -------------------------------------------------
        score_label = tk.Label(content, text="战绩： —", bg=TITLE_BG, fg=DIM,
                               font=("Microsoft YaHei", 9), anchor="w")
        score_label.pack(fill="x", padx=8, pady=(6, 0))
        # ---- 状态面板：活人感 / 投降检测 / 炉石 / 账号 ----------------
        # 每行一个独立 Frame（**不用**共享 grid 列）：grid 会把每一列按"所有行里
        # 最宽的那个单元格"定宽，于是「投降检测」那行的长说明会把「账号」行的
        # 昵称一起顶出窗口（用户反馈：投降检测/炉石后面的字显示不全）。行内用
        # pack，说明列配 wraplength：宽度不够就换行，绝不裁字。
        status = tk.Frame(content, bg=PANEL)
        status.pack(fill="x")
        # 文字宽度都按真实字体量（中文和数字差很多，缩放不同也不怕）。
        detail_font = tkfont.Font(family="Microsoft YaHei", size=8)
        value_font = tkfont.Font(family="Microsoft YaHei", size=8, weight="bold")

        def _status_row(name_text, with_eye=False):
            """一行状态：● 名称 数值 说明（从左往右顺排，说明占满剩余宽度）。

            说明列**不能**右对齐：那样名称和数值之间会空出一大块（用户反馈
            "中间这个太宽了"）。说明紧跟在数值后面，整行的剩余宽度都给它，
            放不下时按 wraplength 换行。
            """
            row = tk.Frame(status, bg=PANEL)
            row.pack(fill="x")
            # side="right" 的 pack 先占最右（只有账号行有眼睛按钮）。
            eye_box = None
            eye_px = 0
            if with_eye:
                # 「账号」行最右侧的眼睛按钮：点一下在“显示昵称 / 隐藏昵称”之间
                # 互换（隐藏状态会记住），适合截图/录屏/开直播。
                # 固定尺寸的小容器能让整行布局绝对稳定（Tk 重排偶尔留重影）。
                eye_box = tk.Frame(row, bg=PANEL, width=_EYE_W, height=18)
                eye_box.pack(side="right", padx=_EYE_PAD, pady=1)
                eye_box.pack_propagate(False)
                eye_px = _EYE_W + sum(_EYE_PAD)
            marker = tk.Label(row, text="●", bg=PANEL, fg=DIM,
                              font=("Segoe UI", 7))
            marker.pack(side="left", padx=_MARKER_PAD, pady=1)
            name = tk.Label(row, text=name_text, bg=PANEL, fg=DIM,
                            font=("Microsoft YaHei", 8))
            name.pack(side="left", pady=1)
            value = tk.Label(row, text="—", bg=PANEL, fg=TEXT,
                             font=("Microsoft YaHei", 8, "bold"))
            value.pack(side="left", padx=(_CELL_GAP, 0), pady=1)
            detail = tk.Label(row, text="", bg=PANEL, fg=DIM,
                              font=("Microsoft YaHei", 8), justify="left",
                              anchor="w", wraplength=_DETAIL_MIN_PX)
            detail.pack(side="left", fill="x", expand=True,
                        padx=(_CELL_GAP, _DETAIL_PAD), pady=1)
            # 这一行"圆点 + 名称 + 数值 + 间距"实际占掉的宽度（用来算说明列）。
            used_px = (marker.winfo_reqwidth() + sum(_MARKER_PAD)
                       + name.winfo_reqwidth()
                       + 2 * _CELL_GAP + _DETAIL_PAD + eye_px)
            return marker, value, detail, eye_box, used_px

        def _set_detail(detail, used_px, value_text, info, fg=DIM):
            """说明文字：按这一行剩余宽度换行（放不下换行，绝不被窗口边缘切掉）。"""
            wrap_px = detail_wrap_px(
                WINDOW_WIDTH, used_px + value_font.measure(str(value_text)))
            _set(detail, text=fit_text(info, wrap_px * _DETAIL_MAX_LINES,
                                       detail_font.measure),
                 wraplength=wrap_px, fg=fg)

        hl_marker, hl_value, hl_detail, _, hl_used = _status_row("活人感")
        cd_marker, cd_value, cd_detail, _, cd_used = _status_row("投降检测")
        lv_marker, lv_value, lv_detail, _, lv_used = _status_row("炉石")
        ac_marker, ac_value, ac_detail, eye_box, ac_used = _status_row(
            "账号", with_eye=True)
        lg_marker, lg_value, lg_detail, _, lg_used = _status_row("日志")

        # 图标只有一个 👁，状态靠颜色区分：绿色 = 显示中，白色 = 已隐藏。
        initial_visible = account_visible()
        eye_btn = tk.Label(
            eye_box, text=EYE_ICON, bg=PANEL,
            fg=EYE_COLOR_ON if initial_visible else EYE_COLOR_OFF,
            font=("Segoe UI Emoji", 10), cursor="hand2")
        eye_btn.place(relx=0.5, rely=0.5, anchor="center")

        def _refresh_eye():
            visible = account_visible()
            _set(eye_btn, text=EYE_ICON,
                 fg=EYE_COLOR_ON if visible else EYE_COLOR_OFF)

        def _on_eye(_event=None):
            toggle_account_visibility()
            _refresh_eye()
            return "break"

        eye_btn.bind("<Button-1>", _on_eye)
        tk.Frame(content, bg=TITLE_BG, height=1).pack(fill="x")

        # ---- buttons ---------------------------------------------------
        # 一行两个，「本局结束后停止」单独一行；字号/内边距都比原来小，
        # 省下的高度全部留给日志区（见 BTN_LAYOUT / BTN_FONT_SIZE）。
        btn_frame = tk.Frame(content, bg=BG)
        btn_frame.pack(fill="x", padx=8, pady=(8, 0))
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        def _place(btn, key):
            row, column = BTN_LAYOUT[key]
            span = BTN_SPAN.get(key, 1)
            left = 0 if column == 0 else 3
            right = 0 if column + span >= 2 else 3
            btn.grid(row=row, column=column, columnspan=span, sticky="ew",
                     padx=(left, right), pady=2)

        def _call_start():
            try:
                # 对局已开始时按钮应处于禁用态；这里再兜底一次，避免误触发。
                if _IS_IN_GAME is not None and _IS_IN_GAME():
                    return
                if _ON_START is not None:
                    _ON_START()
            finally:
                _raise_hearthstone()

        start_btn = _make_btn(btn_frame, "开始对战", ACCENT, _call_start)
        _place(start_btn, "start")

        def _call_halt():
            try:
                if _ON_HALT is not None:
                    _ON_HALT()
            finally:
                _raise_hearthstone()

        halt_btn = _make_btn(btn_frame, "中止", DANGER, _call_halt)
        _place(halt_btn, "halt")

        def _set_stop_after_state():
            active = bool(_IS_STOP_AFTER() if _IS_STOP_AFTER is not None else False)
            if active:
                _set(stop_after_btn,
                     text="本局结束后停止（点击取消）",
                     bg=OK, activebackground=OK)
            else:
                _set(stop_after_btn,
                     text="本局结束后停止", bg=WARN, activebackground=WARN)

        def _call_stop_after():
            try:
                if _ON_STOP_AFTER is not None:
                    _ON_STOP_AFTER()
            finally:
                _raise_hearthstone()
                _set_stop_after_state()

        stop_after_btn = _make_btn(btn_frame, "本局结束后停止", WARN,
                                   _call_stop_after)
        _place(stop_after_btn, "stop_after")

        def _call_save():
            try:
                path = _save_log()
                push(f"[SYS] 对战日志已保存：{path}")
                save_btn.config(text="已保存", bg=OK)
                root.after(2000, lambda: save_btn.config(
                    text="保存日志", bg=OK))
            except Exception as exc:
                push(f"[SYS] 保存对战日志失败：{exc}")
                save_btn.config(text="保存失败", bg=DANGER)
                root.after(2000, lambda: save_btn.config(
                    text="保存日志", bg=OK))
            finally:
                _raise_hearthstone()

        save_btn = _make_btn(btn_frame, "保存日志", OK, _call_save)
        _place(save_btn, "save")

        def _call_calibrate():
            """开/关屏幕上的校准窗口（三个截图区域都能拖、都能存），再点一次收起。"""
            visible = None
            try:
                if _ON_CALIBRATE is not None:
                    visible = _ON_CALIBRATE()
            except Exception as exc:
                push(f"[SYS] 打开校准窗口失败：{exc}")
                return
            finally:
                # 校准窗口是置顶的，但点浮窗按钮本身会把浮窗带到前台，
                # 顺手把炉石切回前台，方便对着游戏画面调盒子。
                _raise_hearthstone()
            if visible:
                push("[SYS] 校准窗口已打开：1 推荐面板 / 2 换牌确认 / 3 AI胜率"
                     "（Tab 切换，拖框对齐后按 S 保存，Esc 或再点「校准」关闭）")
            else:
                push("[SYS] 已关闭校准窗口。")

        calibrate_btn = _make_btn(btn_frame, "校准", ACCENT, _call_calibrate)
        _place(calibrate_btn, "calibrate")

        def _call_open_logs():
            """打开日志位置：Explorer 里选中最新一份对战日志（没有就打开目录）。"""
            try:
                if _ON_OPEN_LOGS is not None:
                    _ON_OPEN_LOGS()
            finally:
                _raise_hearthstone()

        open_logs_btn = _make_btn(btn_frame, "打开日志", NEUTRAL,
                                  _call_open_logs)
        _place(open_logs_btn, "open_logs")

        def _call_clear_logs():
            """清理脚本日志：先确认（把体积/数量摆清楚），确认后交给 web_ui 后台删。

            只清脚本自己写的日志（logs 里的对战日志 + 运行日志），不碰炉石自己的
            Logs 目录——那是「重启炉石」的事。
            """
            info = None
            try:
                if _LOGS is not None:
                    info = _LOGS()
            except Exception:
                info = None
            if not (info or {}).get("files"):
                push("[SYS] 已无可清理的脚本日志。")
                _raise_hearthstone()
                return
            if not _confirm_clear_logs(root, info):
                push("[SYS] 已取消清理脚本日志。")
                _raise_hearthstone()
                return
            try:
                if _ON_CLEAR_LOGS is not None:
                    _ON_CLEAR_LOGS()
            except Exception as exc:
                push(f"[SYS] 清理脚本日志失败：{exc}")
                return
            finally:
                _raise_hearthstone()

        clear_logs_btn = _make_btn(btn_frame, "清理日志", WARN,
                                   _call_clear_logs)
        _place(clear_logs_btn, "clear_logs")

        def _call_restart():
            """重启炉石：先弹确认，确认后交给 web_ui 后台跑（不阻塞浮窗）。

            用途：日志状态和游戏实际对不上（明明没在游戏却显示在换牌）时，
            把炉石彻底重启、日志目录清空，状态回到干净起点。
            """
            if not _confirm_restart(root):
                push("[SYS] 已取消重启炉石。")
                _raise_hearthstone()
                return
            try:
                if _ON_RESTART is not None:
                    _ON_RESTART()
            except Exception as exc:
                push(f"[SYS] 重启炉石失败：{exc}")
                return
            finally:
                _raise_hearthstone()

        restart_btn = _make_btn(btn_frame, "重启炉石", WARN, _call_restart)
        _place(restart_btn, "restart")

        # 这一代窗口的编号：延迟收尾只能关掉自己（见 _stop_generation）。
        generation = _GENERATION[0]

        def _call_exit_overlay():
            """退出浮窗：只关这个窗口，脚本/自动化/日志照常跑。

            与「退出脚本」区分：那个会停自动化并 os._exit(0) 结束整个进程。
            想再打开就回网页点「🪟 日志浮窗」。
            """
            try:
                if _ON_EXIT_OVERLAY is not None:
                    _ON_EXIT_OVERLAY()
            except Exception:
                pass
            # 先声明"正在收尾"（is_running() 立刻变 False）：这 150ms 里网页
            # 点「开始运行」就不会被当成"浮窗还开着"而静默跳过重开。
            mark_closing()
            # 留一拍，让上面那行“已退出浮窗”先画出来再关窗；而且只关自己这一代。
            root.after(150, lambda: _stop_generation(generation))

        exit_overlay_btn = _make_btn(btn_frame, "退出浮窗", NEUTRAL,
                                     _call_exit_overlay)
        _place(exit_overlay_btn, "exit_overlay")

        def _call_exit():
            try:
                if _ON_EXIT is not None:
                    _ON_EXIT()
                    return
            finally:
                _raise_hearthstone()
            # 没有绑定退出回调时，关闭浮窗本身（兜底）。
            stop()

        exit_btn = _make_btn(btn_frame, "退出脚本", DANGER, _call_exit)
        _place(exit_btn, "exit")

        # ---- delay progress (bottom; 先占底部，日志区填剩余空间) ------
        delay_frame = tk.Frame(content, bg=BG)
        delay_frame.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        delay_canvas = tk.Canvas(delay_frame, height=8, bg=PANEL,
                                 highlightthickness=0)
        delay_canvas.pack(fill="x", pady=(0, 2))
        delay_label = tk.Label(delay_frame, text="延时：无", bg=BG, fg=DIM,
                               font=("Microsoft YaHei", 8), anchor="w")
        delay_label.pack(fill="x")

        # ---- log body --------------------------------------------------
        body = tk.Frame(content, bg=BG)
        body.pack(fill="both", expand=True, padx=8, pady=(6, 8))
        text = tk.Text(body, bg=BG, fg=TEXT, font=("Microsoft YaHei", 9),
                       bd=0, highlightthickness=0, wrap="word",
                       height=12, padx=2, pady=2, spacing1=2, spacing3=2)
        text.pack(side="left", fill="both", expand=True)
        scroll = tk.Scrollbar(body, orient="vertical", command=text.yview,
                              width=10)
        scroll.pack(side="right", fill="y")
        text.config(yscrollcommand=scroll.set)
        # 正文按标签着色：盒子意见、实际操作、系统提示、警告、报错各一色，
        # 颜色全部取自浮窗现有调色板（见 LOG_TAG_COLORS / log_line_tag）。
        for tag_name, color in LOG_TAG_COLORS.items():
            if tag_name == "alert":
                continue
            text.tag_config(tag_name, foreground=color)
        # 存活检测/昵称不匹配这类“必须马上看见”的告警行：红色加粗。
        text.tag_config("alert", foreground=LOG_TAG_COLORS["alert"],
                        font=("Microsoft YaHei", 9, "bold"))

        # ---- drag ------------------------------------------------------
        _drag = {"x": 0, "y": 0}

        def _start_drag(event):
            _drag["x"], _drag["y"] = event.x, event.y

        def _on_drag(event):
            nx = root.winfo_x() + event.x - _drag["x"]
            ny = root.winfo_y() + event.y - _drag["y"]
            root.geometry(f"+{nx}+{ny}")

        root.bind("<Button-1>", _start_drag)
        root.bind("<B1-Motion>", _on_drag)

        shown = [None]  # (条数, 首行, 末行) —— 当前已渲染窗口的指纹
        delay_bar = [None]  # 已画出的进度条比例（None = 当前没画）

        def _refresh():
            global _DELAY
            if _STOP.is_set():
                root.destroy()
                _STARTED[0] = False
                return
            if collapsed[0]:
                # 最小化时正文看不见：不重画（省 CPU），展开后靠 shown[0]=None
                # 整体重建，中间进来的日志一行都不会丢。
                shown[0] = None
                root.after(_REFRESH_MS, _update)
                return
            with _LOCK:
                lines = list(_LINES)
            pos = text.yview()
            at_bottom = pos[1] >= 0.999
            # 增量渲染的坑：_LINES 满 _MAX_LINES 后每进一行就从头部滑出一行，
            # 长度恒为 500；用“已插入计数”当下标会让新行永远落在已渲染区间内，
            # 一旦超过 500 行浮窗就再也不刷新。改为按“窗口内容指纹”判断：
            # 只要条数或首/末行任一变了（有新行入、或有旧行被滑出），就整体重建。
            fp = _window_fingerprint(lines)
            changed = fp != shown[0]
            if changed:
                text.delete("1.0", "end")
                for ln, _turn in lines:
                    text.insert("end", ln + "\n", log_line_tag(ln))
                shown[0] = fp
            if _IS_RUNNING is not None:
                if _IS_RUNNING():
                    _set(halt_btn, text="中止", bg=DANGER,
                         activebackground=DANGER)
                else:
                    _set(halt_btn, text="恢复", bg=OK, activebackground=OK)
            if _IS_IN_GAME is not None and _IS_IN_GAME():
                _set(start_btn, state="disabled", text="对局进行中",
                     bg=DISABLED, activebackground=DISABLED,
                     disabledforeground=DIM, cursor="arrow")
            else:
                _set(start_btn, state="normal", text="开始对战",
                     bg=ACCENT, activebackground=ACCENT,
                     disabledforeground=DIM, cursor="hand2")
            if _SCORE is not None:
                score = _SCORE()
                if score is not None:
                    # score 三元组：(场数, 胜场, 自动认输数)。兼容旧的二元组。
                    if len(score) >= 3:
                        games, wins, concedes = score
                    else:
                        games, wins = score
                        concedes = 0
                    # 负 = 总完成局 - 胜（含自动认输，因为自动认输也算一局输）；
                    # 认输数单独列出作参考。
                    losses = max(games - wins, 0)
                    rate = (wins / games * 100) if games else 0.0
                    rate_txt = f"{rate:.1f}%" if games else "--"
                    concede_txt = f" · 认输 {concedes}" if concedes else ""
                    _set(score_label,
                         text=f"战绩： 胜 {wins} · 负 {losses} · "
                              f"胜率 {rate_txt}{concede_txt}", fg=TEXT)
                else:
                    _set(score_label, text="战绩： —", fg=DIM)
            if _HUMAN_LIKE is not None:
                try:
                    hl_info = _HUMAN_LIKE()
                except Exception:
                    hl_info = None
                row = human_like_row(hl_info)
                _set(hl_marker, fg=row["marker"])
                _set(hl_value, text=row["value"], fg=row["value_color"])
                _set_detail(hl_detail, hl_used, row["value"], row["detail"])
            if _CONCEDE_DETECT is not None:
                try:
                    cd_info = _CONCEDE_DETECT()
                except Exception:
                    cd_info = None
                row = concede_detect_row(cd_info)
                _set(cd_marker, fg=row["marker"])
                _set(cd_value, text=row["value"], fg=row["value_color"])
                _set_detail(cd_detail, cd_used, row["value"], row["detail"])
            if _LIVENESS is not None:
                try:
                    lv_info = _LIVENESS()
                except Exception:
                    lv_info = None
                row = hearthstone_row(lv_info)
                _set(lv_marker, fg=row["marker"])
                _set(lv_value, text=row["value"], fg=row["value_color"])
                _set_detail(lv_detail, lv_used, row["value"], row["detail"])
            if _ACCOUNT is not None:
                try:
                    ac_info = _ACCOUNT()
                except Exception:
                    ac_info = None
                row = account_row(ac_info, show_account=account_visible())
                hidden = row["detail"] == _ACCOUNT_HIDDEN_TEXT
                _set(ac_marker, fg=row["marker"])
                _set(ac_value, text=row["value"], fg=row["value_color"])
                # 隐藏时「已隐藏」用白色，和白色的眼睛保持同一套语义。
                _set_detail(ac_detail, ac_used, row["value"], row["detail"],
                            fg=TEXT if hidden else DIM)
                _refresh_eye()
            if _LOGS is not None:
                try:
                    lg_info = _LOGS()
                except Exception:
                    lg_info = None
                row = logs_row(lg_info)
                _set(lg_marker, fg=row["marker"])
                _set(lg_value, text=row["value"], fg=row["value_color"])
                _set_detail(lg_detail, lg_used, row["value"], row["detail"])
            with _LOCK:
                delay = dict(_DELAY) if _DELAY is not None else None
            if delay is not None:
                now = time.time()
                elapsed = now - delay["started"]
                total = max(delay["total"], 0.001)
                frac = min(max(elapsed / total, 0.0), 1.0)
                remaining = max(total - elapsed, 0.0)
                if remaining <= 0:
                    # 延时已结束：主动清空，避免 “0/0.5s” 这类短延时残留。
                    with _LOCK:
                        _DELAY = None
                    _set(delay_label, text="延时：无", fg=DIM)
                    if delay_bar[0] is not None:
                        delay_canvas.delete("all")
                        delay_bar[0] = None
                else:
                    _set(delay_label,
                         text=f"{delay['desc']}（{remaining:.0f}/{total:.0f}s）",
                         fg=TEXT)
                    # 进度条每 350ms 重画一次会明显闪烁：进度变化小于 2% 时不重画。
                    if delay_bar[0] is None or abs(frac - delay_bar[0]) >= 0.02:
                        w = max(delay_canvas.winfo_width(), 1)
                        delay_canvas.delete("all")
                        delay_canvas.create_rectangle(
                            0, 0, w * frac, 8, fill=ACCENT, outline="")
                        delay_bar[0] = frac
            else:
                _set(delay_label, text="延时：无", fg=DIM)
                if delay_bar[0] is not None:
                    delay_canvas.delete("all")
                    delay_bar[0] = None
            _set_stop_after_state()
            if at_bottom and changed and lines:
                text.see("end")
            root.after(_REFRESH_MS, _update)

        def _update():
            try:
                _refresh()
            except Exception as exc:
                # 单次刷新异常不杀死浮窗：打印并继续下一轮调度。
                print(f"[overlay] 刷新异常(继续): {type(exc).__name__}: {exc}")
                try:
                    root.after(_REFRESH_MS, _update)
                except Exception:
                    pass

        _update()
        root.mainloop()
    except Exception as exc:
        _report(f"日志浮窗禁用（窗口没能打开）: {type(exc).__name__}: {exc}")
        try:
            root.destroy()
        except Exception:
            pass


def _run_overlay_thread() -> None:
    """浮窗线程入口：跑真正的窗口逻辑，再**在窗口帧释放之后**回收 Tk 对象。

    为什么回收不能写在 _run() 里：tkinter 的 Tk 实例、控件和回调闭包互相引用成环，
    引用计数永远不归零，只能靠 GC 回收；而 _run() 自己的帧（一堆控件局部变量 +
    嵌套函数）在它返回之前一直钉着整张图，在帧里 gc.collect() 什么都回收不掉
    （实测：关闭浮窗后仍能扫到 1 个 Tk 实例 + 26 个 tkinter 控件）。等 _run()
    返回、帧被释放之后再回收，才真正回收在**创建解释器的这个线程**里。
    否则下一次 GC 若发生在别的线程（急停后点「恢复」最容易：新线程一分配对象就
    触发 GC），Tcl 会直接 panic：
        Tcl_AsyncDelete: async handler deleted by the wrong thread
    """
    try:
        _run()
    finally:
        try:
            gc.collect()
        except Exception:
            pass
        _STARTED[0] = False
        # 窗口没了就不再是"收尾中"：否则下次 start() 会以为要等旧线程而排队。
        _CLOSING.clear()
