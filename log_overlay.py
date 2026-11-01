# -*- coding: utf-8 -*-
"""Right-top live log overlay via tkinter (topmost, translucent, draggable).

start() launches a background thread with a tkinter window pinned to the
top-right corner. Its body is a **merged step feed**（「OCR / 执行」分块流，见
action_feed.py）：每块 = 一步动作，块内先 OCR 内容、后执行内容，新块置顶。
The window can be dragged by holding the left mouse button. Failures disable the
overlay and never crash the caller.

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
  * 导出本次出错截屏与日志  — zip this run's retries/errors + screenshots for the author
  * 重启炉石              — confirm, then kill Hearthstone + wipe logs + relaunch
  * 退出浮窗              — close only this window (script keeps running)
  * 退出脚本              — stop automation and exit the whole process
  * 装好没反应？           — open a self-check dialog (version / selfcheck / calibration)
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

# 浮窗正文的归块/渲染（一步动作一块，OCR → 执行）：纯 stdlib 的 legacy 展示模块。
# 这里只负责把 action_feed.render() 画出来，不在浮窗里再写一套归块逻辑。
import action_feed
import merged_log

# 脚本日志（对战日志 / 运行日志）的体积、命名与清理都在 script_logs.py 里；
# 浮窗只用它的 format_size 与 new_battle_log_path，避免两份实现打架。
import script_logs

# 版本号：浮窗品牌行右侧显示 v2026.10.17（网页顶栏显示的是同一个串）。
import version

_LOCK = threading.Lock()
# 旧版「日志正文」缓存：合并成分块流（action_feed）后不再参与渲染，
# 只为既有用例/调试保留；浮窗正文现在来自 action_feed.render()。
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
BG = "#121722"
PANEL = "#1a2130"
PANEL2 = "#232b3c"
LINE = "#2a3346"
TITLE_BG = "#0c1017"
TEXT = "#e9eef7"
DIM = "#9aa5b8"
GREEN = "#4fd18b"
ACCENT = "#5b9dff"
DANGER = "#ef6b6b"
WARN = "#e3a63f"
OK = "#2ea06b"
GOLD = "#e8b93b"
DISABLED = "#394050"
# 「退出浮窗」用中性灰：它不是危险操作（脚本继续跑），别和红色的「退出脚本」混淆。
NEUTRAL = "#3a4356"

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
    "export_feedback": (4, 0),
    "restart": (5, 0),
    "exit_overlay": (5, 1),
    "exit": (6, 0),
    "self_check": (7, 0),
}
# 需要横跨整行的按钮（单独一行）。
BTN_SPAN = {"stop_after": 2, "exit": 2, "export_feedback": 2, "self_check": 2}

# 浮窗尺寸：宽度按最长的一行文字/按钮定，高度 = 品牌行 + 状态面板（4 行）
# + 五行按钮（开始/中止、本局结束后停止、校准/保存日志、重启炉石/退出浮窗、
# 退出脚本）+ 日志区。
# 宽度保持用户要求的收窄值（292 → 263）：状态行说明放不下时靠 wraplength
# **换行**，而不是把窗口加宽（加宽过一次，用户反馈"整个浮窗又变宽了"）。
WINDOW_WIDTH = 263
# 654 - 17（删掉副标题）→ 637；再 +56 给新增的「日志」状态行与「打开日志 /
# 清理日志」按钮行；+30 给新增的「导出本次出错截屏与日志」按钮行，日志正文保持 9 行左右。
# 再 +49 给新增的「炉石窗口宽度」滑条行（标题/数值 + 横向 Scale）。
# 再 +30 给「装好没反应？」按钮行（点开是自查弹窗；不再占顶部红字的位置）。
# +78：日志正文与「OCR 扫描」框合并成一个「OCR / 执行」分块流（原来两个框
# 各占一块高度，合并后面积要稍大一些）。开窗时高度还会按
# min(设计值, 屏幕高 - 40px) 再夹一层，防止小屏/高缩放把底部按钮顶出屏幕。
WINDOW_HEIGHT = 940
# 炉石窗口宽度滑条（保持 16:9）。范围/步进的「真值」在 hs_window；这里的默认值
# 只是它不可用时的兜底（见 `_run` 里的滑条构建）。
SIZE_SLIDER_MIN = 960
SIZE_SLIDER_MAX = 3840
SIZE_SLIDER_STEP = 16
SIZE_ROW_TITLE = "炉石窗口"
# 滑条外观（自绘 Canvas）：设计高度、滑钮半径、轨道粗细与 flat dark 配色。
# 不用 tk.Scale 是因为它的滑钮是方块（丑），而且按下会连浮窗一起拖（见 _run）。
SLIDER_HEIGHT = 20
SLIDER_THUMB_RADIUS = 7
SLIDER_TRACK_HEIGHT = 6
SLIDER_TRACK_COLOR = "#333b4d"
SLIDER_FILL_COLOR = ACCENT
SLIDER_THUMB_COLOR = "#e8ecf2"


def slider_sync_action(enabled: bool, value, current, step: int) -> str:
    """「炉石窗口宽度」滑条这一拍该做什么（纯函数，方便单测）。

    返回 ``"none"`` / ``"enable"`` / ``"disable"`` / ``"follow"``。

    以前只在**开浮窗那一刻**判断一次炉石在不在：先开浮窗、后启动炉石，滑条就
    永远灰着（用户 2026-10-26 反馈「炉石已经打开了，这个还是灰的」）。刷新循环
    每拍调用这个判定，炉石出现/退出都能来回切换。
    """
    if not current:
        # 读不到客户区：已经灰着就保持（别反复重绘闪烁），还亮着就置灰说明。
        return "none" if not enabled else "disable"
    if not enabled:
        return "enable"
    try:
        changed = abs(int(value) - int(current)) >= max(1, int(step))
    except (TypeError, ValueError):
        changed = False
    return "follow" if changed else "none"

# 最小化（折叠成标题条）：浮窗是 WS_EX_TOOLWINDOW，没有任务栏条目，
# 真 iconify() 之后用户没有任何入口还原，所以折叠成标题条 + 「展开」按钮。
MINIMIZED_HEIGHT = 34
# 标题行右侧的小按钮：只用文字（拿块状字形当图标会像多了一个下划线）。
MINIMIZE_TEXT = "最小化"
RESTORE_TEXT = "展开"

# ---- 「OCR / 执行」合并面板（日志正文 + OCR 扫描合成一个分块流） ----------
# 每块 = 一步动作：块内先是 OCR 内容（OCR 快照 + 盒子推荐），再是执行内容；
# 新块置顶，块间一条 ─ 分隔线。归块/渲染全在 action_feed.py，这里只负责画。
FEED_PANEL_TITLE = "OCR / 执行"
FEED_HIDE_TEXT = "隐藏"
FEED_SHOW_TEXT = "显示"
FEED_FONT_SIZE = 9
# 卡片左侧竖条：bar = ┃ 字符（细线）；block = 底色方块（更粗）；none = 不画。
# 三种都是真实可用的显示模式（用户 2026-11-01 要看实际 demo 选一个）。
FEED_BAR_MODE = "block"
FEED_BAR_GLYPH = "┃"
FEED_BAR_GAP_PX = 1  # 竖线与文字之间留的像素（用户要求"紧贴左边"）
# tag → 颜色（全部取自浮窗现有调色板；head/alert 另外加粗）。
#   match/warn/text = OCR 原文的绿 / 橙 / 白；exec = 执行内容；dim = 次要信息。
#   card_* = R19 卡片式排版：meta（时间·阶段）/ action（这一步做了什么）/
#   note（次要）/ ocr（OCR 摘要）/ exp（可点「原文」）/ raw（展开的原文）。
FEED_TAG_COLORS = {
    "head": TEXT,  # 块头（HH:MM:SS · 阶段，加粗）
    "match": GREEN,  # OCR 命中（结束回合框读到「结束回合」等）
    "warn": WARN,  # OCR 警告（读到但不是想要的内容）
    "text": TEXT,  # OCR 原文
    "exec": TEXT,  # 执行内容
    "dim": DIM,  # 次要信息 / 分隔线
    "alert": DANGER,  # 告警 / 报错（红色加粗）
    "card_meta": DIM,  # 卡片头（HH:MM · 阶段）
    "card_action": TEXT,  # 动作（执行/推荐）
    "card_note": DIM,  # 次要动作行
    "card_alert": DANGER,  # 告警卡片（红色加粗）
    "card_ocr": ACCENT,  # 【OCR】行（蓝，和白色的【执行】区分开）
    "card_exp": ACCENT,  # 「原文 (N)」展开入口
    "card_raw": DIM,  # 展开后的 OCR 原文
    "card_more": DIM,  # 「＋N 行（滚动查看）」
    "card_sep": LINE,  # 块与块之间的浅色虚线
}
# 卡片左侧竖条：action_feed 只给颜色名，这里映射到调色板（含 S1 新加的 LINE）。
FEED_BAR_COLORS = {
    "gold": GOLD,  # 我方出牌
    "warn": WARN,  # 对手回合
    "danger": DANGER,  # 告警
    "accent": ACCENT,  # 换牌
    "dim": LINE,  # 未知阶段 / 对局结束
}
# 加粗的 tag（其它只配前景色；FakeWidget 的 tag_config 是覆盖写，别分两次配）。
FEED_BOLD_TAGS = ("head", "alert", "card_alert")
# ocr_panel_lines() 仍然负责「OCR 段」的排版（区域块 + 完整原文），
# 下面是它的排版常量（R18E 落地，合并面板后继续复用）。
# 多久没再扫到就把这个区域藏起来（秒）。我方回合的探针是 2s 一次，12s = 连丢 6 次。
OCR_PANEL_TTL_SECONDS = 12.0
# 安全上限：真的出现几十行时只画前这么多行，末尾补一行提示（正常远用不到）。
OCR_PANEL_RENDER_LIMIT = 60
OCR_PANEL_EMPTY_TEMPLATE = "（最近 {ttl:g}s 没有 OCR 扫描）"
# 空状态带上“当前卡在哪一步”（web_ui 注入的 phase_callback → FSM_action 诊断码）。
OCR_PANEL_EMPTY_PHASE_TEMPLATE = "（最近 {ttl:g}s 没有 OCR 扫描 · 当前：{label}）"
# 日志静默超过这个秒数（Power.log 没有新内容）＝真的可能卡住，空状态转橙色。
OCR_PANEL_LOG_IDLE_WARN_SECONDS = 90.0
OCR_PANEL_LOG_IDLE_TEMPLATE = " · 日志 {seconds:.0f}s 未更新（可能卡住）"
OCR_PANEL_OVERFLOW_TEMPLATE = "…还有 {count} 行（可滚动查看）"
OCR_PANEL_NO_TEXT = "（没读到字）"
# 原文行的缩进（竖线当“引用”标记，和区域标题区分开）。
OCR_PANEL_INDENT = "  ▏"
# 固定顺序：结束回合框永远第一（我方回合就绪判据），其余按这个优先级排，
# **不按时间跳动** —— 否则每 2s 一次扫描会让整块上下乱窜。
OCR_PANEL_ORDER = ("结束回合框", "推荐", "胜率", "开始按钮", "段位", "换牌确认")
# tag → 面板里显示的区域名（推荐的三套预处理变体合并成一块）。
OCR_TAG_GROUP = {
    "turn_end": "结束回合框",
    "start": "开始按钮",
    "rank": "段位",
    "confirm": "换牌确认",
    "winrate": "胜率",
    "scaled_color_v1": "推荐",
    "gray_clahe_v1": "推荐",
    "light_text_binary_v1": "推荐",
}
# 颜色 tag → 色值（Text 里按 tag 上色；head 额外加粗）。
OCR_LINE_TAGS = {"match": GREEN, "warn": WARN, "text": TEXT, "dim": DIM}

# ---- 识别轮播窗口（用户 2026-11-01：结束回合/段位/开始按钮单独在上面轮播） ----
DETECT_TITLE = "识别"
DETECT_EMPTY_TEXT = "（暂无识别结果）"
DETECT_ROTATE_SECONDS = 3.0
# 这些区域只在「识别」轮播窗口显示，不再进下面的日志流。
DETECT_TAG_LABELS = {
    "turn_end": "结束回合",
    "rank": "段位",
    "start": "开始按钮",
    "confirm": "换牌确认",
}


# ---- DPI 缩放 -------------------------------------------------------------
# 上面这些宽高/行高/内边距都是按 96 DPI（100% 缩放）写死的「设计值」；字体却是
# 按「磅」定义的，Tk 会随系统 DPI 自动放大。高缩放屏（如 4K + 150%）下字变大、
# 窗口不变 → 内容挤成一团（用户反馈的「浮窗在不同分辨率下被压缩」）。
# UI_SCALE 在开窗时按 Tk 报告的屏幕 DPI 算出（96 DPI = 1.0），几何一律经 px()
# 换算；100% 屏下 px() 原样返回，与旧版逐像素一致。测试锁的基线常量
# （263/693/34）保持「设计值」口径不变。
UI_SCALE = 1.0


def compute_ui_scale(root) -> float:
    """按 Tk 报告的屏幕 DPI 算几何缩放系数（96 DPI = 1.0，夹在 1.0~3.0）。"""
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:
        return 1.0
    if not dpi or dpi <= 0:
        return 1.0
    return max(1.0, min(3.0, dpi / 96.0))


def px(value, scale=None) -> int:
    """把 96 DPI 基准的「设计像素」换算成当前 DPI 的实际像素（至少 1px）。

    用 +0.5 截断（不是银行家舍入）：394.5 这类半像素统一进位，150% 缩放下
    263 → 395，与"按比例放大"的直觉一致。
    """
    s = UI_SCALE if scale is None else scale
    try:
        return max(1, int(int(value) * float(s) + 0.5))
    except Exception:
        return 1


def _marker_pad(scale=None) -> tuple[int, int]:
    """● 圆点的左右内边距（按当前 DPI 缩放后的 pack padx）。"""
    return (px(_MARKER_PAD[0], scale), px(_MARKER_PAD[1], scale))


def _eye_pad(scale=None) -> tuple[int, int]:
    """眼睛按钮的左右内边距（按当前 DPI 缩放后的 pack padx）。"""
    return (px(_EYE_PAD[0], scale), px(_EYE_PAD[1], scale))


# 状态行内布局（左→右）：● 名称 数值 说明 …（「账号」行最右还有眼睛按钮）。
# 说明列能用的宽度 = 窗口宽 - 这一行其它列，实测按控件真实宽度算（见 _status_row），
# 所以缩放/字体不同也不会算歪。
_MARKER_PAD = (10, 4)  # ● 的左右内边距
_CELL_GAP = 6  # 名称 / 数值 / 说明之间的间距
_DETAIL_PAD = 10  # 说明列右侧内边距
_EYE_W, _EYE_PAD = 24, (2, 8)  # 眼睛按钮与它的内边距（只有「账号」行）
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
# 名字取自 version.py，别在这里再写死一份（改了名字只改那一处）。
BRAND_NAME = version.NAME
# 品牌行右侧的版本小字：v2026.10.17（版本号 = 发布日期，不带 git 短哈希）。
VERSION_TEXT = version.version_label()
VERSION_FONT = ("Consolas", 8)
# 「装好没反应？」按钮 + 自查弹窗：回应装好却不动的新用户，按顺序
# ① 群里是否最新（本机/最新版本号）② 自检是否全部通过 ③ 是否校准过。
SELF_CHECK_BUTTON_TEXT = "装好没反应？"
SELF_CHECK_TITLE = "装好没反应？按顺序自查"
# 状态圆点：功能开着 = 绿，关着 = 红，状态未知 = 灰。
MARKER_ON = GREEN
MARKER_OFF = DANGER
MARKER_UNKNOWN = DIM


def detail_wrap_px(width: int, used_px: int, min_px: int = _DETAIL_MIN_PX) -> int:
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
        return {
            "marker": MARKER_UNKNOWN,
            "value": "—",
            "value_color": DIM,
            "detail": "",
        }
    try:
        total = int(info.get("bytes") or 0)
        files = int(info.get("files") or 0)
    except (TypeError, ValueError):
        return {
            "marker": MARKER_UNKNOWN,
            "value": "—",
            "value_color": DIM,
            "detail": "",
        }
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
    return {
        "marker": color,
        "value": script_logs.format_size(total),
        "value_color": color,
        "detail": detail,
    }


def ocr_panel_lines(entries, now=None, ttl=None, limit=None, phase=None) -> list:
    """把 ``src.ocr.status.snapshot()`` 变成「OCR 扫描」框要画的行。

    纯函数（不依赖 Tk，方便单测）。返回 ``[{"text", "tag", "head"}]``：

    * ``tag``：颜色 tag 名（``match`` / ``warn`` / ``text`` / ``dim``，见
      ``OCR_LINE_TAGS``）；``head=True`` 的区域标题行在浮窗里加粗；
    * 每个区域一块：1 行标题 + 识别出的每一行原文各占一行（**不截断**，换行/滚动
      交给 Text）；
    * 只保留 ``ttl`` 秒内扫到的区域（过期区域不显示，换牌确认这类不会一直挂着）；
    * 同一区域的多个预处理变体合并成一块，取有文字的最新一条；
    * 顺序固定（``OCR_PANEL_ORDER``），不按时间跳动 —— 结束回合框永远第一行；
    * 一条新鲜记录都没有时给一行“空状态”：带上 ``phase``（当前卡在哪一步）与
      Power.log 静默时长，说明“为什么没在扫”（R18H，用户 2026-10-31 反馈
      “卡住时没有 OCR 扫描”）。
    """
    ttl = OCR_PANEL_TTL_SECONDS if ttl is None else float(ttl)
    limit = OCR_PANEL_RENDER_LIMIT if limit is None else int(limit)
    current = time.time() if now is None else float(now)
    items = [item for item in (entries or ()) if isinstance(item, dict)]
    fresh = [item for item in items if _ocr_fresh(item, current, ttl)]
    if not fresh:
        return [_ocr_empty_row(phase, ttl)]
    lines = []
    for label, entry in _ocr_blocks(fresh):
        lines.extend(_ocr_block_lines(label, entry))
    if limit > 0 and len(lines) > limit:
        hidden = len(lines) - (limit - 1)
        lines = lines[: max(0, limit - 1)]
        lines.append({"text": OCR_PANEL_OVERFLOW_TEMPLATE.format(count=hidden),
                      "tag": "dim", "head": False})
    return lines


def _ocr_empty_row(phase, ttl: float = OCR_PANEL_TTL_SECONDS) -> dict:
    """没有新鲜扫描时的空状态行：说明当前卡在哪一步、日志是否静默。

    ``phase`` 来自 web_ui 注入的 ``phase_callback``（``FSM_action.
    automation_phase_state()``）：``{"label", "log_idle_s"}``；缺失就退回原来的
    文案。日志静默 ≥90s（超过对手烧绳）才转橙色 —— 那时才是“真的可能卡住”。
    """
    label = ""
    idle = None
    if isinstance(phase, dict):
        label = str(phase.get("label") or "").strip()
        try:
            value = phase.get("log_idle_s")
            idle = None if value is None else float(value)
        except (TypeError, ValueError):
            idle = None
    if label:
        text = OCR_PANEL_EMPTY_PHASE_TEMPLATE.format(ttl=ttl, label=label)
    else:
        text = OCR_PANEL_EMPTY_TEMPLATE.format(ttl=ttl)
    tag = "dim"
    if idle is not None and idle >= OCR_PANEL_LOG_IDLE_WARN_SECONDS:
        text += OCR_PANEL_LOG_IDLE_TEMPLATE.format(seconds=idle)
        tag = "warn"
    return {"text": text, "tag": tag, "head": False}


def detect_items(entries, now=None, ttl=None) -> list:
    """识别轮播内容：``["结束回合 · 命中 · 最近 1s", "段位 · 传说 · 4s 前", ...]``。

    只取 DETECT_TAG_LABELS 里的区域（结束回合 / 段位 / 开始按钮 / 换牌确认），
    顺序固定、不随时间乱跳；过期记录显示「未见」。
    """
    ttl = OCR_PANEL_TTL_SECONDS if ttl is None else float(ttl)
    current = time.time() if now is None else float(now)
    fresh = {}
    for item in entries or ():
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag") or "")
        label = DETECT_TAG_LABELS.get(tag)
        if label is None:
            continue
        try:
            at = float(item.get("at") or 0.0)
        except (TypeError, ValueError):
            at = 0.0
        if at and current - at > ttl:
            continue
        if label not in fresh or at >= fresh[label][0]:
            fresh[label] = (at, item)
    rows = []
    for tag, label in DETECT_TAG_LABELS.items():
        if label not in fresh:
            continue
        at, item = fresh[label]
        text = " ".join(
            str(line[0] or "").strip()
            for line in (item.get("lines") or ())
            if str(line[0] or "").strip())
        age = "刚刚" if not at or current - at < 1 else f"{current - at:.0f}s 前"
        if tag == "turn_end":
            status = "命中「结束回合」" if "结束回合" in text else ("未见「结束回合」" if text else "没读到字")
        elif tag == "start":
            status = "已出现" if ("开始" in text or text) else "未出现"
        elif tag == "confirm":
            status = text or "未见「确认」"
        else:
            status = text or "没读到字"
        rows.append(f"{label} · {status} · {age}")
    return rows


def ocr_panel_status(entries, now=None, ttl=None) -> str:
    """标题行右侧的新鲜度：「最近 2s 前」/「没有扫描」。

    只显示一个数字（最新那次扫描距今几秒），每拍刷新；不参与正文指纹，
    所以不会因为秒数变化把整个面板重建一遍。
    """
    ttl = OCR_PANEL_TTL_SECONDS if ttl is None else float(ttl)
    current = time.time() if now is None else float(now)
    ages = []
    for item in entries or ():
        if not isinstance(item, dict) or not _ocr_fresh(item, current, ttl):
            continue
        try:
            ages.append(current - float(item.get("at") or current))
        except (TypeError, ValueError):
            continue
    if not ages:
        return "没有扫描"
    age = max(0.0, min(ages))
    if age < 1.0:
        return "刚刚"
    return f"最近 {age:.0f}s 前"


def _ocr_snapshot_rows(entries=None, phase=None) -> list:
    """OCR 快照提供器：把当刻 OCR 结果交给 action_feed（块建立时取一次）。

    正常由 action_feed 回调，自己从 ``_OCR``/``_PHASE`` 读（web_ui 注入）；
    两个参数只给单测直接调用时用（不传就读模块回调）。返回 ocr_panel_lines()
    的原始行（``[{"text", "tag", "head"}]``），归块的 tag 转换在 action_feed 里。
    """
    if entries is None and phase is None:
        entries = None
        if _OCR is not None:
            try:
                entries = _OCR()
            except Exception:
                entries = None
        if _PHASE is not None:
            try:
                phase = _PHASE()
            except Exception:
                phase = None
    # 识别区域（结束回合/段位/开始按钮/换牌确认）只在「识别」轮播窗口显示。
    if entries:
        entries = [item for item in entries
                   if DETECT_TAG_LABELS.get(str((item or {}).get("tag") or "")) is None]
    return ocr_panel_lines(entries, phase=phase)


def _ocr_fresh(entry: dict, now: float, ttl: float) -> bool:
    """这条记录是不是 ttl 秒内扫到的（没有时间戳的记录按新鲜处理）。"""
    try:
        at = float(entry.get("at") or 0.0)
    except (TypeError, ValueError):
        return False
    return at <= 0.0 or (now - at) <= ttl


def _ocr_label(entry: dict) -> str:
    """区域显示名：认识的 tag 用中文名，不认识的截断 tag 原样显示。"""
    tag = str(entry.get("tag") or "?")
    return OCR_TAG_GROUP.get(tag) or tag[:8]


def _ocr_blocks(entries) -> list:
    """按显示区域合并后排序，返回 ``[(label, entry)]``。"""
    chosen = {}
    for entry in entries:
        label = _ocr_label(entry)
        previous = chosen.get(label)
        if previous is None or _ocr_better(entry, previous):
            chosen[label] = entry
    return sorted(chosen.items(), key=lambda item: _ocr_order_key(*item))


def _ocr_better(candidate: dict, previous: dict) -> bool:
    """同一区域取哪条：有文字的优先，其次更新的。"""
    candidate_has = int(candidate.get("line_count") or 0) > 0
    previous_has = int(previous.get("line_count") or 0) > 0
    if candidate_has != previous_has:
        return candidate_has
    try:
        return float(candidate.get("at") or 0.0) >= float(previous.get("at") or 0.0)
    except (TypeError, ValueError):
        return False


def _ocr_order_key(label: str, entry: dict):
    """固定优先级（OCR_PANEL_ORDER）在前，同优先级按最近扫描时间倒序。"""
    try:
        index = OCR_PANEL_ORDER.index(label)
    except ValueError:
        index = len(OCR_PANEL_ORDER)
    try:
        at = float(entry.get("at") or 0.0)
    except (TypeError, ValueError):
        at = 0.0
    return (index, -at)


def _ocr_block_lines(label: str, entry: dict) -> list:
    """一个区域块：1 行标题 + 每条原文 1 行（完整文本，不截断）。"""
    try:
        count = int(entry.get("line_count") or 0)
        confidence = float(entry.get("confidence") or 0.0)
    except (TypeError, ValueError):
        count, confidence = 0, 0.0
    try:
        repeat = int(entry.get("count") or 1)
    except (TypeError, ValueError):
        repeat = 1
    header = f"● {label} {count}行 {confidence:.2f}"
    if repeat > 1:
        header += f" ×{repeat}"
    lines = [{"text": header, "tag": _ocr_block_tag(entry, count), "head": True}]
    texts = []
    for item in entry.get("lines") or ():
        try:
            text = str(item[0] or "").strip()
        except (TypeError, IndexError, ValueError):
            text = ""
        if text:
            texts.append(text)
    if not texts:
        lines.append({"text": OCR_PANEL_INDENT + OCR_PANEL_NO_TEXT,
                      "tag": "dim", "head": False})
        return lines
    for text in texts:
        lines.append({"text": OCR_PANEL_INDENT + text,
                      "tag": "text", "head": False})
    return lines


def _ocr_block_tag(entry: dict, count: int) -> str:
    """标题行颜色：结束回合框读到/没读到「结束回合」分开，其余白色/灰色。"""
    if count <= 0:
        return "dim"
    if _ocr_label(entry) == "结束回合框":
        return "match" if "结束回合" in str(entry.get("text") or "") else "warn"
    return "text"


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


def self_check_text(info=None) -> str:
    """「装好没反应？」弹窗里的自查步骤（纯文本，方便单测）。

    info 来自 web_ui 的 version_callback（update_check.status() 的快照）：
        state: latest / update_available / local_newer / remote_missing /
               unknown / checking
        local_label / latest_label：形如 v2026.10.24
    info 缺失时只显示本机版本，并把“最新版本”标成获取失败——绝不假装最新。
    """
    info = info if isinstance(info, dict) else {}
    local = str(info.get("local_label") or VERSION_TEXT)
    latest = info.get("latest_label")
    state = str(info.get("state") or "unknown")
    if state == "update_available" and latest:
        first = f"① 群里文件不是最新：本机 {local} → 最新 {latest}。去群里换最新压缩包。"
    elif state == "latest":
        first = f"① 群里文件已是最新：{local}。"
    elif state == "local_newer" and latest:
        first = f"① 本机 {local}（线上还是 {latest}）—— 本地比线上新，确认群里发的是哪版。"
    elif state == "remote_missing":
        first = f"① 群里文件是否最新：本机 {local}（线上还没发布版本号）。"
    elif info.get("checking"):
        first = f"① 正在获取线上最新版本…（本机 {local}）"
    else:
        first = f"① 群里文件是否最新：本机 {local}（最新版本获取失败，可先按版本号对一下群文件）。"
    return "\n".join((
        first,
        "② 网页控制台「环境自检」是否全部通过；未通过按提示修。",
        "③ 是否点过浮窗「校准」并保存（拖好框按 S）。",
        "",
        "三项都过了还不动：浮窗「导出本次出错截屏与日志」，把 zip 发出来。",
    ))


def minimized_geometry(
    width: int,
    height: int,
    x: int,
    y: int,
    collapsed: bool,
    min_height: int = MINIMIZED_HEIGHT,
) -> str:
    """折叠/展开时的窗口几何串（折叠只剩标题条那么高）。

    min_height 默认取设计值 MINIMIZED_HEIGHT（测试按 96 DPI 基准断言）；
    开窗时按 DPI 缩放过，实际调用会传 px(MINIMIZED_HEIGHT)。
    """
    size = min_height if collapsed else height
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
_ALERT_MARKERS = ("⚠️", "[ERROR]", "ERROR]", "不匹配", "已退出", "无响应", "疑似卡死")


def _is_alert_line(line: str) -> bool:
    return any(marker in line for marker in _ALERT_MARKERS)


_DELAY = None
# 旧版「日志正文」的滑动窗口缓存：合并成分块流（action_feed）后**不再参与渲染**，
# 只为兼容既有用例/调试查看保留（_window_fingerprint 同理）。
# 浮窗正文现在显示的是 action_feed 的块流，完整日志仍看网页 / ui_log_last.txt。
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
            "label": label,
            "desc": desc,
            "total": total,
            "started": time.time(),
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
    return (len(lines), lines[0] if lines else None, lines[-1] if lines else None)


def push(line: str, _level: str = "INFO") -> None:
    """浮窗侧收行：保存日志 / 延时进度条 / 我方回合切前台。

    浮窗正文的「OCR / 执行」分块流由 ``web_ui._log()`` 直接喂给 action_feed
    （在浮窗过滤之前），所以这里**不再把行塞进正文渲染**；保留 `_FULL_LINES`
    （保存完整日志）、延时进度条与「轮到己方」时把炉石切前台这几件事。
    """
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
            return  # 延时行只驱动进度条，不进正文
        # _LINES 只为兼容旧用例/调试保留（合并面板后不再参与渲染）。
        _LINES.append((line, _turn_start(line)))
        if len(_LINES) > _MAX_LINES:
            # _LINES 是 deque，不支持切片删除，改用 popleft 丢弃最旧行。
            # 仅影响这份兼容缓存；保存仍用 _FULL_LINES 完整写出。
            while len(_LINES) > _MAX_LINES:
                _LINES.popleft()
    if is_turn_start:
        # 我方回合开始：把炉石唤回前台，避免 OCR 被其他窗口挡住。
        _raise_hearthstone()


def _save_log() -> str:
    """把当前对战日志写入 logs/ 子目录，返回可读 TXT 路径。

    结构化数据（每行一个回合块）是唯一数据源：优先用 battle_record 的记录渲染
    TXT 并落一份 .jsonl；拿不到结构化记录时回退到旧的 ``_FULL_LINES`` 文本，
    保证任何情况下「保存日志」都能出一份东西。
    目录与命名统一由 script_logs 提供（浮窗的「日志」体积行、「清理日志」按钮
    认的就是同一批文件名，不能各写一份）。
    """
    stamp = None
    try:
        import battle_record

        stamp = battle_record.session_stamp()
        when = _parse_stamp(stamp) if stamp else datetime.datetime.now()
        path = script_logs.new_battle_log_path(when)
        path.parent.mkdir(parents=True, exist_ok=True)
        blocks = battle_record.build_blocks(battle_record.records())
        if blocks:
            jsonl_path = script_logs.new_battle_record_path(when)
            battle_record.write_jsonl(
                jsonl_path,
                blocks,
                session=battle_record.session_id(),
                app=f"{version.NAME} {version.version_label()}",
            )
            battle_record.write_text(
                path,
                blocks,
                meta={
                    "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "session": battle_record.session_id(),
                    "source": jsonl_path.name,
                },
            )
            return str(path)
    except Exception:
        pass  # 结构化渲染失败 → 回退到下面的纯文本兜底

    when = _parse_stamp(stamp) if stamp else datetime.datetime.now()
    path = script_logs.new_battle_log_path(when)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        lines = list(_FULL_LINES)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 对战日志 {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n")
        f.write("\n".join(lines))
        if lines:
            f.write("\n")
    return str(path)


def _parse_stamp(stamp):
    """把 ``YYYYmmdd_HHMMSS`` 解析成 datetime；解析不了返回 None。"""
    try:
        return datetime.datetime.strptime(str(stamp), "%Y%m%d_%H%M%S")
    except (TypeError, ValueError):
        return None


_STOP = threading.Event()


_ON_START = None
_ON_HALT = None
_IS_RUNNING = None
_ON_STOP_AFTER = None
_IS_STOP_AFTER = None
_IS_IN_GAME = None
# 「开始」按下后、自动化线程挂上之前的启动状态：按钮显示「启动中…」并禁用，
# 否则这段时间（首次启动要导入组件 + 初始化）看起来像点了没反应。
_IS_STARTING = None
_SCORE = None
_ON_EXIT = None
_HUMAN_LIKE = None
# 「装好没反应？」弹窗的版本数据回调（web_ui 传入 update_check.status 的快照；
# 只读缓存，绝不在这里联网，点按钮时才取一次）。
_VERSION_INFO = None
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
# 「OCR 扫描」框：各区域最近一次 OCR 结果（web_ui 传入 → src.ocr.status.snapshot()）。
_OCR = None
# 「OCR 扫描」框空状态用：当前卡在哪一步 + 日志静默多久（web_ui 传入
# → FSM_action.automation_phase_state()）。没有就退回原文案。
_PHASE = None
# 「打开日志」/「清理日志」按钮（web_ui 传入；清理前浮窗先弹确认）。
_ON_OPEN_LOGS = None
_ON_CLEAR_LOGS = None
# 「导出本次出错截屏与日志」按钮：把本次运行的重试/报错 + 截图打包成 zip（web_ui 传入）。
_ON_EXPORT_FEEDBACK = None
# 「退出浮窗」按钮：只关这个窗口，脚本继续跑（web_ui 传入，用来写一行网页日志）。
_ON_EXIT_OVERLAY = None
# 「窗口宽度」滑条：读炉石当前客户区宽度做初值（web_ui 传入，拿不到 → None）。
_WINDOW_WIDTH = None
# 滑条拖动结束：按 16:9 设炉石客户区宽度（web_ui 传入；返回 [w, h] 或 None）。
_ON_RESIZE_WINDOW = None
# ---- 常驻模式（R3 页签） ----------------------------------------------
# 与 web_ui 的三个约定（都必须异常安全；web_ui 未升级时用下面的安全默认值，
# 保证浮窗照常能开，只是常驻页显示「未接入控制台」）：
#   _RESIDENT            () -> dict                 常驻状态（/api/status 的 resident 段）
#   _ON_RESIDENT_TOGGLE  () -> str|None             暂停/开启常驻，返回一条提示
#   _ON_RESIDENT_APPLY   (dict) -> {"ok","message"} 保存常驻设置
_RESIDENT = None
_ON_RESIDENT_TOGGLE = None
_ON_RESIDENT_APPLY = None
# 页签标题（纯文字，不用 tk.Button —— 现有按钮集合/网格测试钉死了按钮集合）。
STATUS_PAGE_TITLE = "状态"
RESIDENT_PAGE_TITLE = "常驻"
# 共享标题行（「自动化日志」+ 最小化）随页签切换文案：状态页保留原名，
# 常驻页改成「常驻设置」，否则切过去还写着“自动化日志”很别扭。
STATUS_HEADER_TEXT = "自动化日志"
RESIDENT_HEADER_TEXT = "常驻设置"
# 监督器处于这些状态时算“正在干活”：按钮显示「暂停常驻」；其余（disabled /
# paused / paused_exhausted / error / cancelled）显示「开启常驻」并允许一键恢复。
# waiting_schedule（等待定时窗口）也算“正在干活”：常驻在值守，只是还没到窗口。
RESIDENT_ACTIVE_STATES = (
    "countdown", "starting", "running", "recovering", "waiting_schedule",
)


def _resident_unavailable() -> dict:
    return {}


def _resident_toggle_unavailable():
    return None


def _resident_apply_unavailable(_body=None) -> dict:
    return {"ok": False, "message": "常驻模式未接入控制台（web_ui.py 可能是旧版本）"}


# ---- 常驻页文本（纯函数，便于单测；不依赖 Tk） --------------------------
RESIDENT_MODE_LABELS = {"standard": "标准", "wild": "狂野", "casual": "休闲"}
# 状态卡 10 种状态的（文案, 颜色, 是否显示「暂停常驻」）：
#   只有正在干活（等待定时窗口/倒计时/启动/运行/恢复）才允许“暂停”；暂停/耗尽/出错
#   显示「开启常驻」，点击一次恢复。
_RESIDENT_STATE_VIEW = {
    "disabled": ("未开启", DIM, False),
    "waiting_schedule": ("等待定时窗口", ACCENT, True),
    "countdown": (None, ACCENT, True),  # 文案带剩余秒数，见 resident_status_view
    "starting": ("启动中", ACCENT, True),
    "running": ("运行中", GREEN, True),
    "recovering": ("恢复中…", ACCENT, True),
    "paused": ("已暂停", DIM, False),
    "paused_exhausted": ("配额耗尽", DANGER, False),
    "error": ("出错", DANGER, False),
    "cancelled": ("已取消", DIM, False),
}


def _resident_int(value, default=0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def resident_status_view(rs) -> dict:
    """常驻状态卡：``{dot, state_text, fg, can_pause}``（10 种状态 + 未开启）。"""
    rs = rs if isinstance(rs, dict) else {}
    if not bool(rs.get("enabled")):
        return {"dot": DIM, "state_text": "未开启", "fg": DIM, "can_pause": False}
    state = str(rs.get("state") or "")
    text, fg, can_pause = _RESIDENT_STATE_VIEW.get(
        state, (state or "—", DIM, False)
    )
    if state == "countdown":
        # 监督器没有单独的剩余秒数字段：优先用前端算好的 countdown_left，
        # 否则退回配置的启动延迟（展示一档即可，不参与判定）。
        left = rs.get("countdown_left")
        if left is None:
            left = rs.get("start_delay_seconds")
        text = f"倒计时 {max(0, _resident_int(left))}s"
    elif state == "waiting_schedule":
        # 等待定时窗口：状态行第二行显示控制台带的原因（如「等待定时 23:00 开始」）。
        reason = " ".join(str(rs.get("waiting_reason") or "").split())
        if reason:
            text = f"{text}\n{reason}"
    return {"dot": fg, "state_text": text, "fg": fg, "can_pause": can_pause}


def resident_quota_text(rs) -> str:
    """配额：``已用 / 上限``（例如 ``0 / 3``）。"""
    rs = rs if isinstance(rs, dict) else {}
    used = max(0, _resident_int(rs.get("restarts_in_window")))
    limit = max(0, _resident_int(rs.get("max_restarts")))
    return f"{used} / {limit}"


def resident_target_text(rs) -> str:
    """目标：``狂野 · 卡组 2`` / ``标准 · 卡组 未指定``。"""
    rs = rs if isinstance(rs, dict) else {}
    mode = str(rs.get("mode") or "standard")
    label = RESIDENT_MODE_LABELS.get(mode, mode)
    deck = rs.get("deck_index")
    deck_text = f"卡组 {_resident_int(deck)}" if deck else "卡组 未指定"
    return f"{label} · {deck_text}"


def resident_log_text(rs) -> str:
    """日志：``8s 前 · 对局中``（没有日志时间就 ``—``）。"""
    rs = rs if isinstance(rs, dict) else {}
    age = rs.get("log_age")
    if age is None:
        age_text = "—"
    else:
        age_text = f"{max(0.0, float(age)):.0f}s 前"
    scene = "对局中" if rs.get("in_game") else "其他界面"
    return f"{age_text} · {scene}"


def _resident_hhmm(value) -> str:
    """把 ``last_restart_at``（ISO 串或 epoch 秒）转成 ``HH:MM``；坏值返回空串。"""
    if value in (None, ""):
        return ""
    try:
        if isinstance(value, (int, float)):
            stamp = datetime.datetime.fromtimestamp(float(value))
        else:
            text = str(value).strip().replace("Z", "+00:00")
            stamp = datetime.datetime.fromisoformat(text)
        return stamp.strftime("%H:%M")
    except Exception:
        return ""


def resident_event_text(rs, limit: int = 18) -> str:
    """最近事件：``HH:MM``（有 last_restart_at 时）+ message/paused_reason。

    压成单行并截断到约 ``limit`` 字符（默认 18，避免出现“…重新…”这种半截）；
    画到固定宽度时 UI 还会再用 ``fit_text`` 按真实字体像素宽度截一次。
    """
    rs = rs if isinstance(rs, dict) else {}
    text = rs.get("message") or rs.get("paused_reason") or "—"
    text = " ".join(str(text).split())
    stamp = _resident_hhmm(rs.get("last_restart_at"))
    if stamp:
        text = f"{stamp} {text}"
    limit = _resident_int(limit, 0)
    if limit > 0 and len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def resident_rule_lines(rs) -> list:
    """「自动恢复规则」大卡的 6 行说明（数值随当前设置变化）。"""
    rs = rs if isinstance(rs, dict) else {}
    game = max(1, _resident_int(rs.get("stale_in_game_seconds"), 180))
    other = max(1, _resident_int(rs.get("stale_other_seconds"), 300))
    window = max(1, _resident_int(rs.get("window_seconds"), 600) // 60)
    limit = max(1, _resident_int(rs.get("max_restarts"), 3))
    return [
        f"· 对局中日志 {game}s 不动 → 重启炉石",
        f"· 其它界面 {other}s 不动 → 重启炉石",
        "· 能重连就续打，否则重选模式+卡组",
        "· 选模式/卡组失败 → 停住并告警，不盲开局",
        "· 手动停止（stop/Ctrl+Q）＝ 暂停常驻",
        f"· {window} 分钟最多 {limit} 次，超限停并告警",
    ]


RESIDENT_PAGE_MIN_HEIGHT = 560


def resident_window_height(needed, max_height, min_height=RESIDENT_PAGE_MIN_HEIGHT):
    """常驻页窗口高度：按内容需要多高就多高（夹在 [min_height, max_height]）。

    状态页内容多、需要整窗 940；常驻页是设置面板，若固定 940 会在下半屏留
    一大片空黑。切页时按内容收放窗口，视觉上更紧凑。
    """
    try:
        target = int(float(needed))
        top = int(float(max_height))
        bottom = int(float(min_height))
    except (TypeError, ValueError):
        return max_height
    if bottom > top:
        bottom = top
    return max(bottom, min(top, target))


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
        return {
            "marker": MARKER_UNKNOWN,
            "value": "—",
            "value_color": DIM,
            "detail": "",
        }
    if not info.get("enabled"):
        return {"marker": MARKER_OFF, "value": "关", "value_color": DIM, "detail": ""}
    hand_on = bool(info.get("hand_hover_enabled", True))
    minion_on = bool(info.get("minion_hover_enabled", True))
    if not hand_on and not minion_on:
        detail = "两个悬停都已关"
    elif not hand_on:
        m_lo, m_hi = info.get("minion_hover_min"), info.get("minion_hover_max")
        detail = (
            f"看随从 {m_lo:g}~{m_hi:g}s"
            if m_lo is not None and m_hi is not None
            else "仅看随从"
        )
    else:
        lo, hi = info.get("post_delay_min"), info.get("post_delay_max")
        h_lo, h_hi = info.get("hover_min"), info.get("hover_max")
        detail = f"{lo:g}~{hi:g}s" if lo is not None and hi is not None else ""
        if h_lo is not None and h_hi is not None:
            detail += (" · " if detail else "") + f"悬停 {h_lo:g}~{h_hi:g}s"
    return {"marker": MARKER_ON, "value": "开", "value_color": GREEN, "detail": detail}


def concede_detect_row(info) -> dict:
    """浮窗“自动投降检测”状态行：{'marker','value','value_color','detail'}。

    info 形如 {"enabled", "threshold", "rounds", "rate", "streak",
    "checked_turn", "triggered"}；rate=None 表示该回合没读到胜率。
    """
    if info is None:
        return {
            "marker": MARKER_UNKNOWN,
            "value": "—",
            "value_color": DIM,
            "detail": "",
        }
    if not info.get("enabled"):
        return {"marker": MARKER_OFF, "value": "关", "value_color": DIM, "detail": ""}
    rounds = info.get("rounds") or 0
    streak = info.get("streak") or 0
    if info.get("triggered"):
        return {
            "marker": MARKER_ON,
            "value": "已触发认输",
            "value_color": DANGER,
            "detail": f"连续 {streak}/{rounds}",
        }
    rate = info.get("rate")
    if rate is None:
        turn = info.get("checked_turn")
        where = f"第 {turn} 回合" if turn else "本回合"
        return {
            "marker": MARKER_ON,
            "value": "未读到",
            "value_color": WARN,
            "detail": f"{where} · 连续 {streak}/{rounds}",
        }
    threshold = info.get("threshold")
    below = threshold is not None and float(rate) < float(threshold)
    if threshold is None:
        detail = f"连续 {streak}/{rounds}"
    elif below:
        detail = f"低于阈值 {float(threshold):.0f}% · 连续 {streak}/{rounds}"
    else:
        detail = f"阈值 {float(threshold):.0f}% · 连续 {streak}/{rounds}"
    return {
        "marker": MARKER_ON,
        "value": f"{float(rate):.0f}%",
        "value_color": WARN if below else TEXT,
        "detail": detail,
    }


def hearthstone_row(info) -> dict:
    """浮窗「炉石」存活状态行：{'marker','value','value_color','detail'}。

    info 来自 src/safety/hearthstone_liveness.py 的 sample()：
        status: ok | warning | stale | gone | idle | disabled | unknown
    """
    if info is None:
        return {
            "marker": MARKER_UNKNOWN,
            "value": "—",
            "value_color": DIM,
            "detail": "",
        }
    status = info.get("status")
    age = info.get("log_age")
    if status == "disabled":
        return {"marker": MARKER_OFF, "value": "关", "value_color": DIM, "detail": ""}
    if status == "gone":
        return {
            "marker": MARKER_OFF,
            "value": "已退出",
            "value_color": DANGER,
            "detail": "已自动停止",
        }
    if status == "stale":
        return {
            "marker": MARKER_OFF,
            "value": "无响应",
            "value_color": DANGER,
            "detail": f"日志停滞 {age:.0f}s" if age is not None else "",
        }
    if status == "warning":
        return {
            "marker": MARKER_ON,
            "value": "疑似卡死",
            "value_color": WARN,
            "detail": f"日志停滞 {age:.0f}s" if age is not None else "",
        }
    if status == "idle":
        return {
            "marker": MARKER_UNKNOWN,
            "value": "未运行",
            "value_color": WARN,
            "detail": "",
        }
    if status == "ok":
        # 只有对局中才关心 Power.log 的新鲜度（主菜单/匹配阶段本来就安静）。
        detail = ""
        if info.get("in_game") and age is not None:
            detail = f"日志 {age:.0f}s 前"
        return {
            "marker": MARKER_ON,
            "value": "运行中",
            "value_color": GREEN,
            "detail": detail,
        }
    return {"marker": MARKER_UNKNOWN, "value": "—", "value_color": DIM, "detail": ""}


def _clip(text, limit: int = _DETAIL_LIMIT) -> str:
    """截断过长的说明文字（列宽固定，超出会被窗口边缘裁掉）。"""
    text = str(text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def account_row(info, show_account: bool = True) -> dict:
    """浮窗「账号」行：配置的用户 ID 与日志玩家名是否匹配。

    info 来自 log_state.player_name_check()：matched=None 表示还没读到双方
    玩家名（无法判断）。不匹配时脚本会把整局当对手回合而不出牌，所以这里用
    红色标出来，并把日志里出现的真实昵称显示出来方便照抄。

    ``show_account=False``（点了眼睛按钮）时不显示昵称，只保留匹配与否。
    """
    if info is None or info.get("matched") is None:
        return {
            "marker": MARKER_UNKNOWN,
            "value": "—",
            "value_color": DIM,
            "detail": "",
        }
    if not show_account:
        return {
            "marker": MARKER_ON if info.get("matched") else MARKER_OFF,
            "value": "匹配" if info.get("matched") else "不匹配",
            "value_color": GREEN if info.get("matched") else DANGER,
            "detail": _ACCOUNT_HIDDEN_TEXT,
        }
    names = sorted(info.get("players", {}).values())
    if info.get("matched"):
        return {
            "marker": MARKER_ON,
            "value": "匹配",
            "value_color": GREEN,
            "detail": _clip(names[0] if names else info.get("config")),
        }
    return {
        "marker": MARKER_OFF,
        "value": "不匹配",
        "value_color": DANGER,
        "detail": _clip(names[0] if names else info.get("config")),
    }


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
        return  # 没有上一轮；或从浮窗线程自己调用（不能自 join）
    try:
        alive = bool(thread.is_alive())
    except Exception:
        alive = False  # 不是真线程（测试替身）：直接放掉即可
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
        threading.Thread(
            target=_deferred_open_worker,
            args=(dict(args),),
            name="hs-overlay-open",
            daemon=True,
        ).start()
    except Exception as exc:
        _OPEN_PENDING.clear()
        _report(f"浮窗重开排队失败：{type(exc).__name__}: {exc}")
        return False
    return True


def _deferred_open_worker(
    args: dict, timeout: float = OPEN_WAIT_SECONDS, interval: float = 0.1
) -> None:
    """等上一轮浮窗线程收尾，再把它重新开出来。"""
    try:
        deadline = time.time() + float(timeout)
        while _STARTED[0] and time.time() < deadline:
            time.sleep(interval)
        if _STARTED[0]:
            _report(
                f"上一轮浮窗线程超过 {float(timeout):.0f}s 还没结束，"
                "这次没能把浮窗开回来；请稍后再点「🪟 日志浮窗」重试。"
            )
            return
        start(**args)
    finally:
        _OPEN_PENDING.clear()


def start(
    on_start=None,
    on_halt=None,
    is_running=None,
    on_stop_after=None,
    is_stop_after=None,
    is_in_game=None,
    is_starting=None,
    score_callback=None,
    on_exit=None,
    human_like_callback=None,
    concede_callback=None,
    liveness_callback=None,
    ocr_callback=None,
    phase_callback=None,
    account_callback=None,
    account_visible_setting=None,
    on_toggle_account=None,
    on_calibrate=None,
    on_calibrate_close=None,
    on_restart=None,
    logs_callback=None,
    on_open_logs=None,
    on_clear_logs=None,
    on_export_feedback=None,
    on_exit_overlay=None,
    window_width_callback=None,
    on_resize_window=None,
    version_callback=None,
    resident_callback=_resident_unavailable,
    on_resident_toggle=_resident_toggle_unavailable,
    on_resident_apply=_resident_apply_unavailable,
) -> str:
    """开（或重开）浮窗，返回 OPEN_ALREADY / OPEN_STARTED / OPEN_PENDING。

    三种结果都要让调用方**知道**：以前 start() 遇到"已经开着"或"上一轮还没
    收尾"就静默 return，网页却照样报"已就绪"，于是浮窗没出来用户也得不到
    任何提示（用户反馈："退出浮窗以后，再从 web 点开始就不会弹出浮窗了"）。
    """
    global _ON_START, _ON_HALT, _IS_RUNNING, _ON_STOP_AFTER, _IS_STOP_AFTER
    global _IS_IN_GAME, _IS_STARTING, _SCORE, _ON_EXIT, _HUMAN_LIKE, _CONCEDE_DETECT
    global _LIVENESS, _ACCOUNT, _ON_TOGGLE_ACCOUNT, _ON_CALIBRATE
    global _ON_CALIBRATE_CLOSE, _ON_RESTART, _ON_EXIT_OVERLAY, _THREAD
    global _LOGS, _ON_OPEN_LOGS, _ON_CLEAR_LOGS, _ON_EXPORT_FEEDBACK
    global _OCR
    global _PHASE
    global _WINDOW_WIDTH, _ON_RESIZE_WINDOW, _VERSION_INFO
    global _RESIDENT, _ON_RESIDENT_TOGGLE, _ON_RESIDENT_APPLY
    # 排队重开时要原样复用这一轮的绑定（start() 会重写全部回调，不能只重开窗口）。
    args = {
        "on_start": on_start,
        "on_halt": on_halt,
        "is_running": is_running,
        "on_stop_after": on_stop_after,
        "is_stop_after": is_stop_after,
        "is_in_game": is_in_game,
        "is_starting": is_starting,
        "score_callback": score_callback,
        "on_exit": on_exit,
        "human_like_callback": human_like_callback,
        "concede_callback": concede_callback,
        "liveness_callback": liveness_callback,
        "ocr_callback": ocr_callback,
        "phase_callback": phase_callback,
        "account_callback": account_callback,
        "account_visible_setting": account_visible_setting,
        "on_toggle_account": on_toggle_account,
        "on_calibrate": on_calibrate,
        "on_calibrate_close": on_calibrate_close,
        "on_restart": on_restart,
        "logs_callback": logs_callback,
        "on_open_logs": on_open_logs,
        "on_clear_logs": on_clear_logs,
        "on_export_feedback": on_export_feedback,
        "on_exit_overlay": on_exit_overlay,
        "window_width_callback": window_width_callback,
        "on_resize_window": on_resize_window,
        "version_callback": version_callback,
        "resident_callback": resident_callback,
        "on_resident_toggle": on_resident_toggle,
        "on_resident_apply": on_resident_apply,
    }
    with _START_LOCK:
        if _STARTED[0] and not _STOP.is_set() and not _CLOSING.is_set():
            return OPEN_ALREADY  # 真的开着：不重开
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
        _IS_STARTING = is_starting
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
        _OCR = ocr_callback
        _PHASE = phase_callback
        _ON_OPEN_LOGS = on_open_logs
        _ON_CLEAR_LOGS = on_clear_logs
        _ON_EXPORT_FEEDBACK = on_export_feedback
        _ON_EXIT_OVERLAY = on_exit_overlay
        _WINDOW_WIDTH = window_width_callback
        _ON_RESIZE_WINDOW = on_resize_window
        _VERSION_INFO = version_callback
        _RESIDENT = resident_callback
        _ON_RESIDENT_TOGGLE = on_resident_toggle
        _ON_RESIDENT_APPLY = on_resident_apply
        if account_visible_setting is not None:
            _ACCOUNT_VISIBLE[0] = bool(account_visible_setting)
        _STOP.clear()
        _STARTED[0] = True
        # 新的一代窗口：旧窗口里排队/延迟的动作（见 _stop_generation）从此失效。
        _GENERATION[0] += 1
        # 线程入口是 _run_overlay_thread（它包住 _run，好在帧释放后回收 Tk 对象）；
        # 单独提出来是为了让测试仍能 patch _run 而不真的开窗。
        thread = threading.Thread(
            target=_run_overlay_thread, name="hs-log-overlay", daemon=True
        )
        _THREAD = thread
        thread.start()
    # 合并面板：把「块建立时取一次 OCR 快照」的提供器接上（_run 里还会再设一次，
    # 因为浮窗测试直接调 _run 而不走 start()）。
    try:
        action_feed.set_ocr_provider(_ocr_snapshot_rows)
    except Exception:
        pass
    return OPEN_STARTED


def stop() -> None:
    """Signal the overlay thread to close its window."""
    _CLOSING.set()
    _STOP.set()
    # 浮窗关了就把分块流清空：下次开窗从新的一局/新的步骤开始看。
    try:
        action_feed.clear()
    except Exception:
        pass
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
    return bool(_STARTED[0]) and not _STOP.is_set() and not _CLOSING.is_set()


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
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)  # VK_MENU 按下
        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)  # VK_MENU 抬起
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
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
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
    "对局进行中会直接判负；重启后需再点「开始对战」。"
)
CONFIRM_RESTART_OK = "确认执行"
CONFIRM_CLEAR_TITLE = "清空脚本日志？（含出错存档）"
CONFIRM_CLEAR_OK = "确认清空"


def confirm_clear_detail(
    files,
    battle_size,
    runtime_size,
    total_size,
    feedback_count=0,
    feedback_size=None,
) -> str:
    """「清理日志」确认弹窗的正文（体积/数量都由浮窗实时算出来）。

    显式列出**出错存档（截图/错误日志）**会被删掉，并给出“删除后无法恢复、
    建议先导出”的告警——不然用户一键清理就把没导出的错误证据清了。
    """
    lines = [
        "将删除 logs 里的：",
        f"· {int(files)} 个对战日志（{battle_size}）",
        f"· 运行日志 ui_log_last.txt（{runtime_size}）",
    ]
    count = int(feedback_count or 0)
    if count:
        lines.append(f"· 出错存档（截图/错误日志）{count} 份（{feedback_size or '—'}）")
    lines.append(f"共 {total_size}。")
    if count:
        lines.append(
            "⚠️ 出错存档删除后无法恢复：本次及历史未导出的出错截图/错误日志都会"
            "一起没掉，建议先点「导出本次出错截屏与日志」再清理。"
        )
    else:
        lines.append("日志是排查问题的依据，删除后无法恢复；对局与设置不受影响。")
    return "\n".join(lines)


def _confirm_dialog(
    root,
    title: str,
    detail: str,
    confirm_text: str,
    confirm_bg=DANGER,
    height=None,
) -> bool:
    """浮窗样式的确认小窗：确认返回 True，取消/Esc/关闭返回 False。

    故意不用 tkinter.messagebox：炉石全屏独占时系统对话框可能被压在游戏后面，
    这里用与浮窗完全相同的 -topmost + overrideredirect 机制，一定看得见。
    ``height`` 可调高（清理日志正文较长，需要更多空间）。
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
        width, height = px(344), px(height or 208)
        dialog.overrideredirect(True)
        dialog.attributes("-topmost", True)
        dialog.configure(bg=BG)
        root.update_idletasks()
        x = root.winfo_x() + (root.winfo_width() - width) // 2
        y = root.winfo_y() + (root.winfo_height() - height) // 2
        x = max(px(8), min(x, root.winfo_screenwidth() - width - px(8)))
        y = max(px(8), min(y, root.winfo_screenheight() - height - px(8)))
        dialog.geometry(f"{width}x{height}+{x}+{y}")

        tk.Frame(dialog, bg=confirm_bg, height=px(3)).pack(fill="x")
        tk.Label(
            dialog,
            text=title,
            bg=BG,
            fg=confirm_bg,
            font=("Microsoft YaHei", 11, "bold"),
            anchor="w",
        ).pack(fill="x", padx=px(12), pady=(px(10), px(4)))
        tk.Label(
            dialog,
            text=detail,
            bg=BG,
            fg=TEXT,
            justify="left",
            font=("Microsoft YaHei", 9),
            wraplength=width - px(28),
        ).pack(fill="x", padx=px(12))

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
        row.pack(fill="x", padx=px(12), pady=(px(12), px(12)), side="bottom")
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=1)
        tk.Button(
            row,
            text=confirm_text,
            bg=confirm_bg,
            fg="#ffffff",
            activebackground=confirm_bg,
            activeforeground="#ffffff",
            bd=0,
            font=("Microsoft YaHei", 10, "bold"),
            cursor="hand2",
            command=lambda: _close(True),
        ).grid(row=0, column=0, sticky="ew", padx=(0, px(4)), ipady=px(6))
        tk.Button(
            row,
            text="取消",
            bg=NEUTRAL,
            fg=TEXT,
            activebackground=NEUTRAL,
            activeforeground=TEXT,
            bd=0,
            font=("Microsoft YaHei", 10),
            cursor="hand2",
            command=lambda: _close(False),
        ).grid(row=0, column=1, sticky="ew", padx=(px(4), 0), ipady=px(6))
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


def _info_dialog(root, title: str, detail: str, height=None) -> None:
    """浮窗样式的信息小窗（只有一个「知道了」）。

    和 `_confirm_dialog` 同一套 topmost + overrideredirect 机制：炉石全屏时
    系统 messagebox 可能被游戏压在后面，这个一定看得见。
    """
    try:
        import tkinter as tk
    except Exception:
        return
    try:
        dialog = tk.Toplevel(root)
    except Exception:
        return
    try:
        width, height = px(360), px(height or 300)
        dialog.overrideredirect(True)
        dialog.attributes("-topmost", True)
        dialog.configure(bg=BG)
        root.update_idletasks()
        x = root.winfo_x() + (root.winfo_width() - width) // 2
        y = root.winfo_y() + (root.winfo_height() - height) // 2
        x = max(px(8), min(x, root.winfo_screenwidth() - width - px(8)))
        y = max(px(8), min(y, root.winfo_screenheight() - height - px(8)))
        dialog.geometry(f"{width}x{height}+{x}+{y}")

        tk.Frame(dialog, bg=ACCENT, height=px(3)).pack(fill="x")
        tk.Label(
            dialog,
            text=title,
            bg=BG,
            fg=ACCENT,
            font=("Microsoft YaHei", 11, "bold"),
            anchor="w",
        ).pack(fill="x", padx=px(12), pady=(px(10), px(4)))
        tk.Label(
            dialog,
            text=detail,
            bg=BG,
            fg=TEXT,
            justify="left",
            font=("Microsoft YaHei", 9),
            wraplength=width - px(28),
        ).pack(fill="x", padx=px(12))

        def _close(_event=None):
            try:
                dialog.grab_release()
            except Exception:
                pass
            try:
                dialog.destroy()
            except Exception:
                pass

        row = tk.Frame(dialog, bg=BG)
        row.pack(fill="x", padx=px(12), pady=(px(12), px(12)), side="bottom")
        tk.Button(
            row,
            text="知道了",
            bg=ACCENT,
            fg="#ffffff",
            activebackground=ACCENT,
            activeforeground="#ffffff",
            bd=0,
            font=("Microsoft YaHei", 10, "bold"),
            cursor="hand2",
            command=_close,
        ).pack(fill="x", ipady=px(6))
        dialog.bind("<Escape>", _close)
        dialog.bind("<Return>", _close)

        dialog.grab_set()
        dialog.focus_force()
        dialog.wait_window()
    except Exception:
        try:
            dialog.destroy()
        except Exception:
            pass


def _confirm_restart(root) -> bool:
    """「重启炉石」的确认小窗：确认返回 True。"""
    return _confirm_dialog(
        root, CONFIRM_RESTART_TITLE, CONFIRM_RESTART_DETAIL, CONFIRM_RESTART_OK
    )


def _confirm_clear_logs(root, info) -> bool:
    """「清理日志」的确认小窗：把要删的体积/数量（含出错存档）与危害摆出来。"""
    info = info or {}
    files = int(info.get("battle_files") or 0)
    feedback_count = int(info.get("feedback_files") or 0)
    detail = confirm_clear_detail(
        files,
        script_logs.format_size(info.get("battle_bytes")),
        script_logs.format_size(info.get("runtime_bytes")),
        script_logs.format_size(info.get("bytes")),
        feedback_count=feedback_count,
        feedback_size=script_logs.format_size(info.get("feedback_bytes")),
    )
    # 含出错存档时用红色 + 更高的弹窗（正文行数多），强调危害。
    return _confirm_dialog(
        root,
        CONFIRM_CLEAR_TITLE,
        detail,
        CONFIRM_CLEAR_OK,
        confirm_bg=DANGER if feedback_count else WARN,
        height=248 if feedback_count else 208,
    )


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
        # 合并面板的 OCR 快照提供器：块建立时 action_feed 回调它取当刻扫描结果。
        # （start() 里也设一次；测试直接调 _run 时不走 start，所以这里必须设。）
        try:
            action_feed.set_ocr_provider(_ocr_snapshot_rows)
        except Exception:
            pass
        # 几何随 DPI 缩放（96 DPI = 1.0）：字体 Tk 本来就按 DPI 放大，几何不跟
        # 的话高缩放屏下内容会被压扁（用户反馈的浮窗压缩）。100% 屏 = 旧行为。
        global UI_SCALE
        UI_SCALE = compute_ui_scale(root)
        try:
            # 把 Tk 的「磅 → 像素」比例也钉在同一个 DPI 上（pixels/point =
            # DPI/72），保证字体与几何永远按同一个系数缩放。
            root.tk.call("tk", "scaling", UI_SCALE * 96.0 / 72.0)
        except Exception:
            pass
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        # 高度：品牌行 + 状态面板（5 行）+ 六行按钮都要放得下，剩下全部留给
        # 「OCR / 执行」分块流（见 WINDOW_HEIGHT 注释）。设计高度还要夹到
        # 屏幕内：小屏 / 高缩放下浮窗不能被顶出屏幕（底部按钮会点不到）。
        W = px(WINDOW_WIDTH)
        H = min(px(WINDOW_HEIGHT), sh - px(40))
        x = sw - W - px(12)
        y = px(12)
        root.geometry(f"{W}x{H}+{x}+{y}")
        root.configure(bg=TITLE_BG)
        _disable_overlay_activation(root)

        # 最小化 = 折叠成标题条：内容全在 content 里，折叠时藏起来只留 mini_bar。
        # （浮窗是 WS_EX_TOOLWINDOW，真 iconify() 之后没有任务栏条目可以还原。）
        collapsed = [False]
        content = tk.Frame(root, bg=TITLE_BG)
        content.pack(fill="both", expand=True)
        mini_bar = tk.Frame(root, bg=TITLE_BG, height=px(MINIMIZED_HEIGHT))
        mini_bar.pack_propagate(False)
        tk.Label(
            mini_bar,
            text=BRAND_NAME,
            bg=TITLE_BG,
            fg=GOLD,
            font=("Georgia", 12, "bold"),
        ).pack(side="left", padx=(px(10), 0))
        # 「展开」做成一个小小的面板按钮：只有文字、悬停提亮，不再靠特殊字形。
        restore_btn = tk.Label(
            mini_bar,
            text=RESTORE_TEXT,
            bg=PANEL,
            fg=DIM,
            font=("Microsoft YaHei", 8),
            cursor="hand2",
            padx=px(8),
            pady=px(2),
        )
        restore_btn.pack(side="right", padx=(0, px(8)), pady=px(6))
        restore_btn.bind(
            "<Enter>", lambda _e: restore_btn.config(fg=TEXT, bg=_hover(PANEL))
        )
        restore_btn.bind("<Leave>", lambda _e: restore_btn.config(fg=DIM, bg=PANEL))
        # 折叠成标题条时也留版本号：pack 顺序决定位置，必须在「展开」之后 pack，
        # 才会排在它左边。
        tk.Label(
            mini_bar, text=VERSION_TEXT, bg=TITLE_BG, fg=DIM, font=VERSION_FONT
        ).pack(side="right", padx=(0, px(8)))

        def _fit_page_height(name):
            """按当前页内容高度调整窗口（常驻页比状态页矮，避免下半屏空黑）。"""
            if collapsed[0]:
                return
            measure = getattr(content, "winfo_reqheight", None)
            if measure is None:  # fake harness / 非 Tk 环境
                return
            try:
                root.update_idletasks()
                needed = int(measure()) + px(2)
            except Exception:
                return
            try:
                target = resident_window_height(needed, H) if name == "resident" else H
                root.geometry(f"{W}x{int(target)}+{root.winfo_x()}+{root.winfo_y()}")
            except Exception:
                pass

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
            root.geometry(
                minimized_geometry(
                    W,
                    H,
                    root.winfo_x(),
                    root.winfo_y(),
                    collapsed=collapsed[0],
                    min_height=px(MINIMIZED_HEIGHT),
                )
            )
            if not collapsed[0]:
                try:
                    _fit_page_height(tabs_state["active"])
                except Exception:
                    pass

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
                parent,
                text=text_,
                bg=bg,
                fg="white",
                font=("Microsoft YaHei", BTN_FONT_SIZE, "bold"),
                relief="flat",
                bd=0,
                pady=px(BTN_PADY),
                cursor="hand2",
                activebackground=bg,
                activeforeground="white",
                command=command,
            )
            btn.bind("<Enter>", lambda _e, b=bg: btn.config(bg=_hover(b)))
            btn.bind("<Leave>", lambda _e, b=bg: btn.config(bg=b))
            return btn

        # ---- 品牌行：本项目大名 + 右侧版本号（放在“自动化日志”标题之前） ----
        # 副标题「炉石传说 · 自动对战」按用户要求删除（那一行高度留给日志区）。
        brand = tk.Frame(content, bg=TITLE_BG)
        brand.pack(fill="x")
        # 品牌名**左对齐**、版本号右对齐，挤在同一行里。
        # 不能再用「品牌名居中 + 版本号 place 到右下角」：263px 的宽度里居中的
        # 名字尾部正好压在版本号上（实测版本串把名字压成 "HSLegendAr"）。
        # 也不能加高一行放版本，浮窗高度是按行数精算的（见 WINDOW_HEIGHT）。
        tk.Label(
            brand, text=BRAND_NAME, bg=TITLE_BG, fg=GOLD, font=("Georgia", 13, "bold")
        ).pack(side="left", padx=(px(10), 0), pady=(px(9), px(7)))
        tk.Label(brand, text=VERSION_TEXT, bg=TITLE_BG, fg=DIM, font=VERSION_FONT).pack(
            side="right", padx=(0, px(10))
        )
        tk.Frame(content, bg=GOLD, height=1).pack(fill="x", padx=px(10))
        tk.Frame(content, bg=PANEL, height=1).pack(fill="x")

        # ---- 页签：状态 / 常驻 ------------------------------------------
        # 浮窗高度按行精算，页签行会吃掉约 24px；状态页内容整体装进 page_status，
        # 默认页的控件与行为保持原样（现有测试钉死了按钮集合/网格/状态行）。
        # 「常驻」页**首次点开才创建控件**：默认页的控件树与数量不变。
        tabs = tk.Frame(content, bg=TITLE_BG)
        tabs.pack(fill="x")
        tabs_state = {"active": "status"}
        page_resident = [None]  # 惰性创建；None = 还没点开过

        def _tab_label(text_, active=False):
            lab = tk.Label(
                tabs,
                text=text_,
                bg=PANEL if active else TITLE_BG,
                fg=TEXT if active else DIM,
                font=("Microsoft YaHei", 9, "bold"),
                cursor="hand2",
                padx=px(10),
                pady=px(2),
            )
            lab.pack(side="left", padx=(px(8), 0), pady=(px(4), px(2)))
            return lab

        status_tab = _tab_label(STATUS_PAGE_TITLE, active=True)
        resident_tab = _tab_label(RESIDENT_PAGE_TITLE)

        # ---- header（共享行：页签之下、两页之上） ------------------------
        head = tk.Frame(content, bg=TITLE_BG)
        head.pack(fill="x")
        dot = tk.Label(head, text="●", bg=TITLE_BG, fg=GREEN, font=("Segoe UI", 10))
        dot.pack(side="left", padx=(px(10), px(4)), pady=px(8))
        title = tk.Label(
            head,
            text=STATUS_HEADER_TEXT,
            bg=TITLE_BG,
            fg=TEXT,
            font=("Microsoft YaHei", 10, "bold"),
        )
        title.pack(side="left", pady=px(8))
        # 标题行右侧的「最小化」：折叠成标题条，再点标题条上的「展开」还原。
        # 做成一枚小小的面板按钮（悬停提亮），只用文字，不再拿块状字形当图标。
        mini_btn = tk.Label(
            head,
            text=MINIMIZE_TEXT,
            bg=PANEL,
            fg=DIM,
            font=("Microsoft YaHei", 8),
            cursor="hand2",
            padx=px(8),
            pady=px(2),
        )
        mini_btn.pack(side="right", padx=(0, px(10)), pady=px(6))
        mini_btn.bind("<Button-1>", _on_minimize)
        mini_btn.bind("<Enter>", lambda _e: mini_btn.config(fg=TEXT, bg=_hover(PANEL)))
        mini_btn.bind("<Leave>", lambda _e: mini_btn.config(fg=DIM, bg=PANEL))
        tk.Frame(content, bg=PANEL, height=1).pack(fill="x")

        # 两个页面都直接挂在 content 下（不套中间容器）：折叠时整块 content
        # 被 pack_forget，页面自然跟着消失；也保持「标题行 → 上两级 = content」
        # 的既有控件层级（测试按这个层级找 content）。
        page_status = tk.Frame(content, bg=TITLE_BG)
        page_status.pack(fill="both", expand=True)

        def _show_page(name):
            """切换页签：状态页原样保留，常驻页首次点开时才建控件。"""
            if name == tabs_state["active"]:
                return
            if name == "resident" and page_resident[0] is None:
                page_resident[0] = _build_resident_page()
            page_status.pack_forget()
            if page_resident[0] is not None:
                page_resident[0].pack_forget()
            tabs_state["active"] = name
            if name == "resident" and page_resident[0] is not None:
                page_resident[0].pack(fill="both", expand=True)
                _refresh_resident()
            else:
                page_status.pack(fill="both", expand=True)
            _set(
                status_tab,
                fg=TEXT if name == "status" else DIM,
                bg=PANEL if name == "status" else TITLE_BG,
            )
            _set(
                resident_tab,
                fg=TEXT if name == "resident" else DIM,
                bg=PANEL if name == "resident" else TITLE_BG,
            )
            # 标题行跟着页签改文案（层级/最小化按钮都不动）。
            _set(
                title,
                text=RESIDENT_HEADER_TEXT if name == "resident" else STATUS_HEADER_TEXT,
            )
            if name == "status":
                # 常驻页的圆点会被刷成状态色；切回状态页恢复绿色。
                _set(dot, fg=GREEN)
            # 常驻页是设置面板、内容比状态页矮：切页后按内容收放窗口高度，
            # 避免固定 940 时下半屏留一大片空黑（真机截图反馈）。
            try:
                _fit_page_height(name)
            except Exception:
                pass

        status_tab.bind("<Button-1>", lambda _e: _show_page("status"))
        resident_tab.bind("<Button-1>", lambda _e: _show_page("resident"))

        # ---- 战绩行 -------------------------------------------------
        score_label = tk.Label(
            page_status,
            text="战绩： —",
            bg=TITLE_BG,
            fg=DIM,
            font=("Microsoft YaHei", 9),
            anchor="w",
        )
        score_label.pack(fill="x", padx=px(8), pady=(px(6), 0))
        # ---- 状态面板：活人感 / 投降检测 / 炉石 / 账号 ----------------
        # 每行一个独立 Frame（**不用**共享 grid 列）：grid 会把每一列按"所有行里
        # 最宽的那个单元格"定宽，于是「投降检测」那行的长说明会把「账号」行的
        # 昵称一起顶出窗口（用户反馈：投降检测/炉石后面的字显示不全）。行内用
        # pack，说明列配 wraplength：宽度不够就换行，绝不裁字。
        status = tk.Frame(page_status, bg=PANEL)
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
                eye_box = tk.Frame(row, bg=PANEL, width=px(_EYE_W), height=px(18))
                eye_box.pack(side="right", padx=_eye_pad(), pady=1)
                eye_box.pack_propagate(False)
                eye_px = px(_EYE_W) + sum(_eye_pad())
            marker = tk.Label(row, text="●", bg=PANEL, fg=DIM, font=("Segoe UI", 7))
            marker.pack(side="left", padx=_marker_pad(), pady=1)
            name = tk.Label(
                row, text=name_text, bg=PANEL, fg=DIM, font=("Microsoft YaHei", 8)
            )
            name.pack(side="left", pady=1)
            value = tk.Label(
                row, text="—", bg=PANEL, fg=TEXT, font=("Microsoft YaHei", 8, "bold")
            )
            value.pack(side="left", padx=(px(_CELL_GAP), 0), pady=1)
            detail = tk.Label(
                row,
                text="",
                bg=PANEL,
                fg=DIM,
                font=("Microsoft YaHei", 8),
                justify="left",
                anchor="w",
                wraplength=px(_DETAIL_MIN_PX),
            )
            detail.pack(
                side="left",
                fill="x",
                expand=True,
                padx=(px(_CELL_GAP), px(_DETAIL_PAD)),
                pady=1,
            )
            # 这一行"圆点 + 名称 + 数值 + 间距"实际占掉的宽度（用来算说明列）。
            used_px = (
                marker.winfo_reqwidth()
                + sum(_marker_pad())
                + name.winfo_reqwidth()
                + 2 * px(_CELL_GAP)
                + px(_DETAIL_PAD)
                + eye_px
            )
            return marker, value, detail, eye_box, used_px

        def _set_detail(detail, used_px, value_text, info, fg=DIM):
            """说明文字：按这一行剩余宽度换行（放不下换行，绝不被窗口边缘切掉）。"""
            wrap_px = detail_wrap_px(
                px(WINDOW_WIDTH), used_px + value_font.measure(str(value_text))
            )
            _set(
                detail,
                text=fit_text(info, wrap_px * _DETAIL_MAX_LINES, detail_font.measure),
                wraplength=wrap_px,
                fg=fg,
            )

        hl_marker, hl_value, hl_detail, _, hl_used = _status_row("活人感")
        cd_marker, cd_value, cd_detail, _, cd_used = _status_row("投降检测")
        lv_marker, lv_value, lv_detail, _, lv_used = _status_row("炉石")
        ac_marker, ac_value, ac_detail, eye_box, ac_used = _status_row(
            "账号", with_eye=True
        )
        lg_marker, lg_value, lg_detail, _, lg_used = _status_row("日志")

        # 图标只有一个 👁，状态靠颜色区分：绿色 = 显示中，白色 = 已隐藏。
        initial_visible = account_visible()
        eye_btn = tk.Label(
            eye_box,
            text=EYE_ICON,
            bg=PANEL,
            fg=EYE_COLOR_ON if initial_visible else EYE_COLOR_OFF,
            font=("Segoe UI Emoji", 10),
            cursor="hand2",
        )
        eye_btn.place(relx=0.5, rely=0.5, anchor="center")

        def _refresh_eye():
            visible = account_visible()
            _set(eye_btn, text=EYE_ICON, fg=EYE_COLOR_ON if visible else EYE_COLOR_OFF)

        def _on_eye(_event=None):
            toggle_account_visibility()
            _refresh_eye()
            return "break"

        eye_btn.bind("<Button-1>", _on_eye)
        tk.Frame(page_status, bg=TITLE_BG, height=1).pack(fill="x")

        # ---- 炉石窗口宽度滑条（保持 16:9）-----------------------------
        # 拖动过程中只更新数值文字，**松开鼠标**才真正调 Win32 改窗口
        # （否则每帧都 SetWindowPos 会卡）。范围/步进取 hs_window 的真值。
        try:
            import hs_window as _hsw

            _sw_min = int(_hsw.MIN_16_9_WIDTH)
            _sw_max = int(_hsw.MAX_16_9_WIDTH)
            _sw_step = int(_hsw.STEP_16_9)
        except Exception:
            _sw_min, _sw_max, _sw_step = (
                SIZE_SLIDER_MIN,
                SIZE_SLIDER_MAX,
                SIZE_SLIDER_STEP,
            )

        size_head = tk.Frame(page_status, bg=PANEL)
        size_head.pack(fill="x", padx=px(10), pady=(px(5), 0))
        tk.Label(
            size_head,
            text=SIZE_ROW_TITLE,
            bg=PANEL,
            fg=DIM,
            font=("Microsoft YaHei", 8),
        ).pack(side="left")
        size_value = tk.Label(
            size_head,
            text="—",
            bg=PANEL,
            fg=TEXT,
            font=("Microsoft YaHei", 8, "bold"),
        )
        size_value.pack(side="right")

        size_row = tk.Frame(page_status, bg=PANEL)
        size_row.pack(fill="x", padx=px(10), pady=(0, px(4)))

        def _fmt_size(width):
            try:
                w = int(round(float(width)))
                return f"{w}×{int(round(w * 9 / 16.0))}"
            except Exception:
                return "—"

        # 自绘 Canvas 滑条：tk.Scale 的滑钮是方块（丑），而且它的按下事件会继续
        # 冒泡到 root 上绑的「拖窗」handler（按滑钮 → 整个浮窗跟着动）。自绘既能
        # 做成圆角轨道 + 圆形滑钮，也能在 widget 级绑定里 return "break" 把事件
        # 吃掉，彻底杜绝误拖窗口。
        track_width = max(20, px(WINDOW_WIDTH - 40))
        slider_canvas = tk.Canvas(
            size_row,
            height=px(SLIDER_HEIGHT),
            width=track_width,
            bg=PANEL,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        slider_canvas.pack(fill="x")
        slider = {"value": _sw_min, "dragging": False, "enabled": True}

        def _ratio(value):
            span = float(_sw_max - _sw_min) or 1.0
            return (float(value) - _sw_min) / span

        def _value_at(x):
            width = slider_canvas.winfo_width() or track_width
            pad = px(SLIDER_THUMB_RADIUS) + px(2)
            usable = max(1.0, width - 2 * pad)
            raw = _sw_min + (x - pad) / usable * (_sw_max - _sw_min)
            steps = int(round((raw - _sw_min) / float(_sw_step)))
            return max(_sw_min, min(_sw_max, _sw_min + steps * _sw_step))

        def _draw_slider():
            canvas = slider_canvas
            canvas.delete("all")
            width = canvas.winfo_width() or track_width
            height = px(SLIDER_HEIGHT)
            cy = height // 2
            radius = px(SLIDER_THUMB_RADIUS)
            pad = radius + px(2)
            x0, x1 = pad, max(pad + 1, width - pad)
            thumb_x = x0 + _ratio(slider["value"]) * (x1 - x0)
            track = px(SLIDER_TRACK_HEIGHT)
            fill = SLIDER_FILL_COLOR if slider["enabled"] else DISABLED
            thumb = SLIDER_THUMB_COLOR if slider["enabled"] else "#7c8391"
            canvas.create_line(
                x0,
                cy,
                x1,
                cy,
                width=track,
                capstyle="round",
                fill=SLIDER_TRACK_COLOR,
            )
            if thumb_x > x0:
                canvas.create_line(
                    x0, cy, thumb_x, cy, width=track, capstyle="round", fill=fill
                )
            canvas.create_oval(
                thumb_x - radius,
                cy - radius,
                thumb_x + radius,
                cy + radius,
                fill=thumb,
                outline=fill,
                width=px(2),
            )

        def _on_slide(value):
            size_value.config(text=_fmt_size(value))

        def _set_value(width):
            try:
                value = int(round(float(width)))
            except Exception:
                return
            slider["value"] = max(_sw_min, min(_sw_max, value))
            _draw_slider()
            _on_slide(slider["value"])

        def _apply_width(_event=None):
            slider["dragging"] = False
            if _ON_RESIZE_WINDOW is None or not slider["enabled"]:
                return
            try:
                result = _ON_RESIZE_WINDOW(int(slider["value"]))
            except Exception:
                result = None
            if result:
                slider["value"] = max(_sw_min, min(_sw_max, int(result[0])))
                _draw_slider()
                size_value.config(text=f"{int(result[0])}×{int(result[1])}")

        def _on_slider_press(event):
            if not slider["enabled"]:
                return "break"
            slider["dragging"] = True
            _set_value(_value_at(event.x))
            return "break"  # 别冒泡到 root 的拖窗 handler

        def _on_slider_move(event):
            if slider["enabled"] and slider["dragging"]:
                _set_value(_value_at(event.x))
            return "break"

        def _on_slider_release(_event=None):
            _apply_width()
            return "break"

        slider_canvas.bind("<Configure>", lambda _e: _draw_slider())
        slider_canvas.bind("<Button-1>", _on_slider_press)
        slider_canvas.bind("<B1-Motion>", _on_slider_move)
        slider_canvas.bind("<ButtonRelease-1>", _on_slider_release)

        try:
            initial_width = _WINDOW_WIDTH() if _WINDOW_WIDTH is not None else None
        except Exception:
            initial_width = None
        if initial_width:
            _set_value(initial_width)
        else:
            # 炉石没开 / 读不到：滑条置灰并说明，别让用户拖了个没反应。
            slider["enabled"] = False
            size_value.config(text="炉石未运行")
            _draw_slider()

        def _sync_size_slider():
            """刷新循环里同步滑条：炉石启动/退出、窗口被外部改动都要跟上。

            以前这里只更新数值、从不再判断「能不能用」：先开浮窗后启动炉石，
            滑条会一直灰着拖不动（用户 2026-10-26 反馈）。现在每拍按
            `slider_sync_action()` 在 可用/置灰 之间切换。
            """
            if slider["dragging"] or _WINDOW_WIDTH is None:
                return
            try:
                current = _WINDOW_WIDTH()
            except Exception:
                current = None
            action = slider_sync_action(
                slider["enabled"], slider["value"], current, _sw_step)
            if action == "enable":
                slider["enabled"] = True
                _set_value(current)
            elif action == "disable":
                slider["enabled"] = False
                size_value.config(text="炉石未运行")
                _draw_slider()
            elif action == "follow":
                _set_value(current)

        # ---- buttons ---------------------------------------------------
        # 一行两个，「本局结束后停止」单独一行；字号/内边距都比原来小，
        # 省下的高度全部留给日志区（见 BTN_LAYOUT / BTN_FONT_SIZE）。
        btn_frame = tk.Frame(page_status, bg=BG)
        btn_frame.pack(fill="x", padx=px(8), pady=(px(8), 0))
        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        def _place(btn, key):
            row, column = BTN_LAYOUT[key]
            span = BTN_SPAN.get(key, 1)
            left = 0 if column == 0 else px(3)
            right = 0 if column + span >= 2 else px(3)
            btn.grid(
                row=row,
                column=column,
                columnspan=span,
                sticky="ew",
                padx=(left, right),
                pady=px(2),
            )

        def _call_start():
            try:
                # 对局已开始时按钮应处于禁用态；这里再兜底一次，避免误触发。
                if _IS_IN_GAME is not None and _IS_IN_GAME():
                    return
                if _ON_START is not None:
                    _ON_START()
                # 立即反馈，不等下一次 350ms 刷新：首次启动要导入组件/初始化，
                # 这段空窗里按钮若不变化，看起来就像"点了没反应"。
                if _IS_STARTING is not None and _IS_STARTING():
                    _set(
                        start_btn,
                        state="disabled",
                        text="启动中…",
                        bg=DISABLED,
                        activebackground=DISABLED,
                        disabledforeground=DIM,
                        cursor="arrow",
                    )
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
                _set(
                    stop_after_btn,
                    text="本局结束后停止（点击取消）",
                    bg=OK,
                    activebackground=OK,
                )
            else:
                _set(
                    stop_after_btn,
                    text="本局结束后停止",
                    bg=WARN,
                    activebackground=WARN,
                )

        def _call_stop_after():
            try:
                if _ON_STOP_AFTER is not None:
                    _ON_STOP_AFTER()
            finally:
                _raise_hearthstone()
                _set_stop_after_state()

        stop_after_btn = _make_btn(btn_frame, "本局结束后停止", WARN, _call_stop_after)
        _place(stop_after_btn, "stop_after")

        def _call_save():
            try:
                path = _save_log()
                push(f"[SYS] 对战日志已保存：{path}")
                save_btn.config(text="已保存", bg=OK)
                root.after(2000, lambda: save_btn.config(text="保存日志", bg=OK))
            except Exception as exc:
                push(f"[SYS] 保存对战日志失败：{exc}")
                save_btn.config(text="保存失败", bg=DANGER)
                root.after(2000, lambda: save_btn.config(text="保存日志", bg=OK))
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
                push(
                    "[SYS] 校准窗口已打开：1 推荐面板 / 2 换牌确认 / 3 AI胜率"
                    "（Tab 切换，拖框对齐后按 S 保存，Esc 或再点「校准」关闭）"
                )
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

        open_logs_btn = _make_btn(btn_frame, "打开日志", NEUTRAL, _call_open_logs)
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

        clear_logs_btn = _make_btn(btn_frame, "清理日志", WARN, _call_clear_logs)
        _place(clear_logs_btn, "clear_logs")

        def _call_export_feedback():
            """导出本次出错截屏与日志：打包成 zip 并发给作者，完成后定位到文件。"""
            push("[SYS] 正在导出本次出错截屏与日志……")
            try:
                if _ON_EXPORT_FEEDBACK is not None:
                    result = _ON_EXPORT_FEEDBACK()
                    if isinstance(result, dict) and not result.get("ok", True):
                        push(f"[SYS] 导出失败：{result.get('error')}")
            except Exception as exc:
                push(f"[SYS] 导出失败：{exc}")
            finally:
                _raise_hearthstone()

        export_feedback_btn = _make_btn(
            btn_frame, "导出本次出错截屏与日志", ACCENT, _call_export_feedback
        )
        _place(export_feedback_btn, "export_feedback")

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

        exit_overlay_btn = _make_btn(btn_frame, "退出浮窗", NEUTRAL, _call_exit_overlay)
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

        def _call_self_check():
            """「装好没反应？」：弹一个自查小窗，把版本/自检/校准顺序列出来。"""
            try:
                try:
                    info = _VERSION_INFO() if _VERSION_INFO is not None else None
                except Exception:
                    info = None
                _info_dialog(root, SELF_CHECK_TITLE, self_check_text(info))
            finally:
                _raise_hearthstone()

        self_check_btn = _make_btn(
            btn_frame, SELF_CHECK_BUTTON_TEXT, WARN, _call_self_check)
        _place(self_check_btn, "self_check")

        # ---- delay progress (bottom; 先占底部，日志区填剩余空间) ------
        delay_frame = tk.Frame(page_status, bg=BG)
        delay_frame.pack(side="bottom", fill="x", padx=px(8), pady=(0, px(8)))
        delay_canvas = tk.Canvas(
            delay_frame, height=px(8), bg=PANEL, highlightthickness=0
        )
        delay_canvas.pack(fill="x", pady=(0, px(2)))
        delay_label = tk.Label(
            delay_frame,
            text="延时：无",
            bg=BG,
            fg=DIM,
            font=("Microsoft YaHei", 8),
            anchor="w",
        )
        delay_label.pack(fill="x")

        # ---- 「OCR / 执行」合并面板（分块流，只读，实时刷新） -------------
        # 用户 2026-10-31 要求：把「日志正文」和「OCR 扫描」两个框合并成一个，
        # 块内顺序【OCR 内容 → 执行内容】，新块置顶。归块/渲染在 action_feed.py，
        # 这里只负责把 action_feed.render() 画出来（一个 Text + 滚动条）。
        # ---- 识别轮播（结束回合 / 段位 / 开始按钮）：只在上面显示，不进日志流 ----
        detect_frame = tk.Frame(page_status, bg=BG)
        detect_frame.pack(fill="x", padx=px(8), pady=(px(2), 0))
        tk.Label(detect_frame, text=DETECT_TITLE, bg=BG, fg=DIM,
                 font=("Microsoft YaHei", 8, "bold"), anchor="w").pack(side="left")
        detect_value = tk.Label(
            detect_frame, text=DETECT_EMPTY_TEXT, bg=BG, fg=TEXT,
            font=("Microsoft YaHei", FEED_FONT_SIZE), anchor="w")
        detect_value.pack(side="left", fill="x", expand=True, padx=(px(6), 0))
        detect_pos = tk.Label(detect_frame, text="", bg=BG, fg=DIM,
                              font=("Microsoft YaHei", 8), anchor="e")
        detect_pos.pack(side="right")
        detect_frame.pack(fill="x", padx=px(8), pady=(px(4), px(6)))
        feed_frame = tk.Frame(page_status, bg=BG)
        feed_frame.pack(fill="both", expand=True, padx=px(8), pady=(px(6), px(8)))
        feed_head = tk.Frame(feed_frame, bg=BG)
        feed_head.pack(fill="x")
        tk.Label(
            feed_head,
            text=FEED_PANEL_TITLE,
            bg=BG,
            fg=DIM,
            font=("Microsoft YaHei", 8, "bold"),
            anchor="w",
        ).pack(side="left")
        # 标题行右侧「隐藏/显示」：只折叠正文，标题和按钮留着。
        feed_toggle = tk.Label(
            feed_head,
            text=FEED_HIDE_TEXT,
            bg=PANEL,
            fg=DIM,
            font=("Microsoft YaHei", 8),
            cursor="hand2",
            padx=px(6),
            pady=px(1),
        )
        feed_toggle.pack(side="right")
        tk.Frame(feed_frame, bg=PANEL, height=1).pack(fill="x", pady=(px(2), 0))
        feed_body = tk.Frame(feed_frame, bg=BG)
        feed_body.pack(fill="both", expand=True, pady=(px(2), 0))
        # wrap="char"：中文长句按字符换行，不被窗口边缘裁掉。
        feed_text = tk.Text(
            feed_body,
            bg=BG,
            fg=TEXT,
            font=("Microsoft YaHei", FEED_FONT_SIZE),
            bd=0,
            highlightthickness=0,
            wrap="char",
            width=1,
            padx=px(1),
            pady=px(2),
            spacing1=px(1),
            spacing3=px(1),
            state="disabled",
        )
        feed_text.pack(side="left", fill="both", expand=True)
        # 滚动条：默认白条太丑（用户 2026-11-01），改成细窄深色条。
        feed_scroll = tk.Scrollbar(
            feed_body,
            orient="vertical",
            command=feed_text.yview,
            width=px(7),
            bg=NEUTRAL,
            troughcolor=BG,
            activebackground=DIM,
            bd=0,
            relief="flat",
            highlightthickness=0,
            elementborderwidth=0,
        )
        feed_scroll.pack(side="right", fill="y", padx=(px(2), 0))
        feed_text.config(yscrollcommand=feed_scroll.set)
        feed_regular_font = tkfont.Font(family="Microsoft YaHei", size=FEED_FONT_SIZE)
        feed_bold_font = tkfont.Font(
            family="Microsoft YaHei", size=FEED_FONT_SIZE, weight="bold")
        # 颜色全部取自浮窗现有调色板（见 FEED_TAG_COLORS）；块头 / 告警加粗。
        # （加粗 tag 一次配好 foreground + font：FakeWidget 的 tag_config
        # 是覆盖写而不是合并，分两次配会把颜色丢掉。）
        # 每块（一个【标签】行）单独一段竖条，块与块之间留 2px 空隙；
        # 时间行上面留 9px，把三张卡片分开。
        spacing = {
            "card_meta": {"spacing1": px(10)},
            "card_ocr": {"spacing1": px(4)},
            "card_action": {"spacing1": px(4)},
            "card_note": {"spacing1": px(4)},
            "card_alert": {"spacing1": px(4)},
            "card_raw": {"spacing1": px(2)},
            "card_more": {"spacing1": px(4)},
            "card_exp": {"spacing1": px(6), "spacing3": px(4)},
            "card_sep": {"spacing1": px(2), "spacing3": px(3)},
        }
        for tag_name, color in FEED_TAG_COLORS.items():
            if tag_name in FEED_BOLD_TAGS:
                continue
            feed_text.tag_config(tag_name, foreground=color,
                                 **spacing.get(tag_name, {}))
        for tag_name in FEED_BOLD_TAGS:
            feed_text.tag_config(
                tag_name,
                foreground=FEED_TAG_COLORS[tag_name],
                font=("Microsoft YaHei", FEED_FONT_SIZE, "bold"),
                **spacing.get(tag_name, {}),
            )
        # 卡片左侧竖条（一个 ┃ 字符按阶段上色）与卡片间距。
        for bar_name, color in FEED_BAR_COLORS.items():
            feed_text.tag_config("bar_" + bar_name, foreground=color)
            feed_text.tag_config(
                "bar_block_" + bar_name, background=color, foreground=color)
        # 阶段分隔：居中金色加粗（用户 2026-11-01 要求「对局回合开始居中金标」）。
        feed_text.tag_config(
            "card_stage",
            foreground=GOLD,
            font=("Microsoft YaHei", FEED_FONT_SIZE, "bold"),
            justify="center",
            spacing1=px(9),
            spacing3=px(3),
        )
        feed_expanded = [True]
        feed_shown = [None]  # 已渲染块流的内容指纹
        bound_exp = set()  # 已绑过点击的「原文」tag（避免每次重建重复 tag_bind）
        # 布局自检只做一次：内容请求高度超过窗口高度时正文会被压缩/重叠，
        # 以后有人再加行就能在运行日志里看到（R18H）。
        layout_checked = [False]

        def _toggle_feed(_event=None):
            """折叠/展开合并面板（只藏正文，标题行和「显示」按钮留着）。"""
            feed_expanded[0] = not feed_expanded[0]
            if feed_expanded[0]:
                feed_body.pack(fill="both", expand=True, pady=(px(2), 0))
                _set(feed_toggle, text=FEED_HIDE_TEXT, fg=DIM)
                # 折叠期间没有重建：展开后强制重画一次。
                feed_shown[0] = None
            else:
                feed_body.pack_forget()
                _set(feed_toggle, text=FEED_SHOW_TEXT, fg=TEXT)
            return "break"

        feed_toggle.bind("<Button-1>", _toggle_feed)
        feed_toggle.bind("<Enter>", lambda _e: _set(feed_toggle, bg=_hover(PANEL)))
        feed_toggle.bind("<Leave>", lambda _e: _set(feed_toggle, bg=PANEL))

        def _toggle_card_expand(block_id):
            """点卡片里的「原文 (N)」→ 展开/收起这一块的 OCR 原文。"""
            try:
                action_feed.toggle_expand(block_id)
            except Exception:
                return "break"
            # 下一次刷新按新指纹整体重建（展开状态存在 action_feed 里）。
            feed_shown[0] = None
            return "break"

        # ---- 常驻页（首次点「常驻」页签才创建控件） ----------------------
        # 视觉重排（R3-ui2）：状态卡 →「对战设置」分区 → 三段式模式控件 →
        # 3 个 stepper → 按钮行 → 单行提示。所有值都单行 + 省略号，绝不折行。
        # 控件值先存在 resident_values 里，点「保存设置」时一次性交给
        # _ON_RESIDENT_APPLY；轮询只在用户没改动（!resident_dirty）时回填。
        resident_widgets = {}
        resident_values = {
            "mode": "standard",
            "deck": 0,
            "stale_game": 180,
            "stale_other": 300,
            "toggle_bg": ACCENT,
        }
        resident_dirty = [False]
        resident_mode_labels = {}
        resident_value_font = tkfont.Font(family="Microsoft YaHei", size=9)
        resident_state_font = tkfont.Font(
            family="Microsoft YaHei", size=10, weight="bold"
        )
        resident_hint_font = tkfont.Font(family="Microsoft YaHei", size=8)

        def _resident_fit(text, max_px):
            return fit_text(str(text), max_px, resident_value_font.measure)

        def _set_resident_hint(message, fg=DIM):
            widget = resident_widgets.get("hint")
            if widget is None:
                return
            _set(
                widget,
                text=fit_text(
                    str(message), px(WINDOW_WIDTH - 24), resident_hint_font.measure
                ),
                fg=fg,
            )

        def _refresh_rule_lines(rs):
            """说明卡 4 行小字随当前设置更新（单行 + fit_text 截断）。"""
            for label, line in zip(
                resident_widgets.get("rules") or [], resident_rule_lines(rs)
            ):
                _set(
                    label,
                    text=fit_text(
                        line, px(WINDOW_WIDTH - 44), resident_hint_font.measure
                    ),
                )

        def _render_number(key):
            widget = resident_widgets.get(key + "_value")
            if widget is None:
                return
            value = int(resident_values.get(key) or 0)
            if key == "deck":
                text = f"第 {value} 套" if value else "未指定"
            else:
                # 数值与单位连在一起（"180s"），不再拆成两段。
                text = f"{value}s"
            _set(widget, text=text)

        def _highlight_mode(mode):
            for key, lab in resident_mode_labels.items():
                active = key == mode
                _set(lab, bg=ACCENT if active else PANEL, fg=BG if active else DIM)

        def _select_mode(key):
            if key not in RESIDENT_MODE_LABELS:
                return
            resident_values["mode"] = key
            resident_dirty[0] = True
            _highlight_mode(key)

        def _build_number_row(parent, title, key, step, low, high):
            """一行 stepper：左标签 + 右侧固定宽簇 [−][数值][+]。

            −/+ 是 PANEL2 底色的可见方钮（悬停提亮），数值区固定 66×24px、
            居中，文本连续（"180s" / "第 2 套" / "未指定"）。
            """
            row = tk.Frame(parent, bg=TITLE_BG)
            row.pack(fill="x", pady=px(4))
            tk.Label(
                row,
                text=title,
                bg=TITLE_BG,
                fg=DIM,
                font=("Microsoft YaHei", 9),
                width=6,
                anchor="w",
            ).pack(side="left")
            cluster = tk.Frame(row, bg=TITLE_BG)
            cluster.pack(side="right")

            def _square_button(text_):
                btn = tk.Label(
                    cluster,
                    text=text_,
                    bg=PANEL2,
                    fg=DIM,
                    cursor="hand2",
                    font=("Microsoft YaHei", 10, "bold"),
                    width=2,
                    pady=px(1),
                )
                btn.bind(
                    "<Enter>", lambda _e, b=btn: _set(b, bg=_hover(PANEL2), fg=TEXT)
                )
                btn.bind("<Leave>", lambda _e, b=btn: _set(b, bg=PANEL2, fg=DIM))
                return btn

            minus = _square_button("−")
            minus.pack(side="left", padx=(0, px(3)))
            value_box = tk.Frame(cluster, bg=PANEL2, width=px(66), height=px(24))
            value_box.pack_propagate(False)
            value_box.pack(side="left")
            value_widget = tk.Label(
                value_box,
                text="",
                bg=PANEL2,
                fg=TEXT,
                font=("Microsoft YaHei", 9, "bold"),
                anchor="center",
            )
            value_widget.pack(fill="both", expand=True)
            plus = _square_button("+")
            plus.pack(side="left", padx=(px(3), 0))

            resident_widgets[key + "_value"] = value_widget
            resident_widgets[key + "_minus"] = minus
            resident_widgets[key + "_plus"] = plus

            def _nudge(delta, k=key, lo=low, hi=high):
                current = int(resident_values.get(k) or 0)
                resident_values[k] = max(lo, min(hi, current + delta))
                resident_dirty[0] = True
                _render_number(k)

            plus.bind("<Button-1>", lambda _e: _nudge(step))
            minus.bind("<Button-1>", lambda _e: _nudge(-step))
            return value_widget
        def _call_resident_apply():
            body = {
                "mode": resident_values["mode"],
                "deck_index": resident_values["deck"] or None,
                "stale_in_game_seconds": resident_values["stale_game"],
                "stale_other_seconds": resident_values["stale_other"],
            }
            result = None
            try:
                if _ON_RESIDENT_APPLY is not None:
                    result = _ON_RESIDENT_APPLY(dict(body))
            except Exception as exc:
                result = {"ok": False, "message": f"保存失败：{exc}"}
            if isinstance(result, dict):
                ok = bool(result.get("ok", True))
                message = result.get("message") or (
                    "常驻设置已保存" if ok else "保存失败"
                )
                _set_resident_hint(message, GREEN if ok else DANGER)
                if ok:
                    resident_dirty[0] = False
            else:
                _set_resident_hint("常驻设置已保存", GREEN)
                resident_dirty[0] = False
            _refresh_resident()
            _raise_hearthstone()

        def _call_resident_toggle():
            """暂停/恢复常驻；**已暂停/出错时一次点击直接恢复**。

            背景：监督器暂停时配置里的 enabled 仍是 True，而 web_ui 注入的
            on_resident_toggle 是“按 enabled 取反”。暂停态直接调它只会先「关闭」，
            用户得点两次才回来（R5 nit11b）。所以恢复方向改走同样已注入的
            apply 回调（body ``{"enabled": True}``）：仍是一次点击、一次后端调用，
            接口无需新增。
            """
            rs = resident_values.get("last_state") or {}
            state = str(rs.get("state") or "")
            resume = bool(rs.get("enabled")) and state not in RESIDENT_ACTIVE_STATES
            message = None
            try:
                if resume:
                    if _ON_RESIDENT_APPLY is not None:
                        result = _ON_RESIDENT_APPLY({"enabled": True})
                        if isinstance(result, dict):
                            ok = bool(result.get("ok"))
                            message = result.get("message") or (
                                "已恢复常驻" if ok else "恢复常驻失败"
                            )
                        else:
                            message = "已恢复常驻"
                elif _ON_RESIDENT_TOGGLE is not None:
                    message = _ON_RESIDENT_TOGGLE()
            except Exception as exc:
                message = f"操作失败：{exc}"
            if message:
                _set_resident_hint(message, TEXT)
            _refresh_resident()
            _raise_hearthstone()

        def _refresh_resident():
            """把 _RESIDENT() 的状态画到常驻页（只在页签可见时更新）。

            文本全部来自模块级纯函数（resident_status_view / quota / target /
            log / event），这里只负责 fit_text 截断与控件赋值。
            """
            if page_resident[0] is None or tabs_state["active"] != "resident":
                return
            try:
                rs = _RESIDENT() if _RESIDENT is not None else None
            except Exception:
                rs = None
            if not isinstance(rs, dict):
                rs = {}
            if not rs:
                # 控制台没提供状态：只更新状态卡/按钮，不清空用户正在编辑的值。
                _set(
                    resident_widgets["state"],
                    text="（控制台未提供常驻状态）",
                    fg=DIM,
                )
                _set(resident_widgets["quota"], text="— / —")
                for key in ("target", "log", "event"):
                    _set(resident_widgets[key], text="—")
                _set(resident_widgets["state_dot"], fg=DIM)
                _set(resident_widgets["head_dot"], fg=DIM)
                resident_values["toggle_bg"] = ACCENT
                _set(
                    resident_widgets["toggle"],
                    text="开启常驻",
                    bg=ACCENT,
                    activebackground=ACCENT,
                )
                _refresh_rule_lines({})
                return
            view = resident_status_view(rs)
            # 状态文案可能有两行（waiting_schedule 第二行是等待原因）：逐行裁宽，
            # 避免把换行符当成普通字符裁掉。
            _set(
                resident_widgets["state"],
                text="\n".join(
                    fit_text(line, px(130), resident_state_font.measure)
                    for line in str(view["state_text"]).splitlines()
                ),
                fg=view["fg"],
            )
            _set(resident_widgets["quota"], text=resident_quota_text(rs))
            _set(
                resident_widgets["target"],
                text=_resident_fit(resident_target_text(rs), px(180)),
            )
            _set(
                resident_widgets["log"],
                text=_resident_fit(resident_log_text(rs), px(180)),
            )
            _set(
                resident_widgets["event"],
                text=_resident_fit(resident_event_text(rs), px(180)),
            )
            _set(resident_widgets["state_dot"], fg=view["dot"])
            _set(resident_widgets["head_dot"], fg=view["dot"])
            _refresh_rule_lines(rs)
            # 只有“正在干活”（倒计时/启动/运行/恢复）才给「暂停常驻」；
            # 暂停/耗尽/出错时显示「开启常驻」——点击一次即恢复（见 toggle 回调）。
            resident_values["last_state"] = dict(rs)
            can_pause = bool(view["can_pause"])
            toggle_bg = DANGER if can_pause else ACCENT
            resident_values["toggle_bg"] = toggle_bg
            _set(
                resident_widgets["toggle"],
                text="暂停常驻" if can_pause else "开启常驻",
                bg=toggle_bg,
                activebackground=toggle_bg,
            )
            if resident_dirty[0]:
                return
            mode = str(rs.get("mode") or "standard")
            deck = rs.get("deck_index")
            resident_values["mode"] = mode
            resident_values["deck"] = int(deck) if deck else 0
            resident_values["stale_game"] = max(
                60, min(3600, _resident_int(rs.get("stale_in_game_seconds"), 180))
            )
            resident_values["stale_other"] = max(
                60, min(3600, _resident_int(rs.get("stale_other_seconds"), 300))
            )
            _highlight_mode(mode)
            _render_number("deck")
            _render_number("stale_game")
            _render_number("stale_other")

        def _section_title(parent, title):
            row = tk.Frame(parent, bg=TITLE_BG)
            row.pack(fill="x", padx=px(12), pady=(px(6), px(5)))
            tk.Label(
                row,
                text=title,
                bg=TITLE_BG,
                fg=DIM,
                font=("Microsoft YaHei", 8, "bold"),
                anchor="w",
            ).pack(side="left")
            tk.Frame(parent, bg=LINE, height=1).pack(fill="x")

        def _build_resident_page():
            page = tk.Frame(content, bg=TITLE_BG)
            body = tk.Frame(page, bg=TITLE_BG)
            body.pack(fill="both", expand=True, padx=px(12))

            # ---- 状态卡：hero（● 状态 + 配额）+ 目标/日志/最近事件 ----------
            card = tk.Frame(body, bg=PANEL)
            card.pack(fill="x", pady=(px(10), px(12)))
            hero = tk.Frame(card, bg=PANEL)
            hero.pack(fill="x", padx=px(12), pady=(px(11), px(1)))
            state_dot = tk.Label(
                hero, text="●", bg=PANEL, fg=DIM, font=("Segoe UI", 10)
            )
            state_dot.pack(side="left")
            state_value = tk.Label(
                hero,
                text="未开启",
                bg=PANEL,
                fg=DIM,
                font=("Microsoft YaHei", 11, "bold"),
                anchor="w",
            )
            state_value.pack(side="left", padx=(px(7), 0))
            quota = tk.Label(
                hero,
                text="0 / 0",
                bg=PANEL2,
                fg=DIM,
                font=("Microsoft YaHei", 9),
                padx=px(7),
                pady=px(1),
            )
            quota.pack(side="right")
            resident_widgets["state"] = state_value
            resident_widgets["state_dot"] = state_dot
            resident_widgets["quota"] = quota
            # 共享标题行的 ●：切到常驻页时随状态变色（状态页仍保持绿色）。
            resident_widgets["head_dot"] = dot
            for key, title in (
                ("target", "目标"),
                ("log", "日志"),
                ("event", "最近事件"),
            ):
                row = tk.Frame(card, bg=PANEL)
                row.pack(fill="x", padx=px(12), pady=px(3))
                tk.Label(
                    row,
                    text=title,
                    bg=PANEL,
                    fg=DIM,
                    font=("Microsoft YaHei", 8),
                    width=5,
                    anchor="w",
                ).pack(side="left")
                value = tk.Label(
                    row,
                    text="—",
                    bg=PANEL,
                    fg=TEXT,
                    font=("Microsoft YaHei", 9),
                    anchor="w",
                    justify="left",
                )
                value.pack(side="left", fill="x", expand=True)
                resident_widgets[key] = value
            tk.Frame(card, bg=PANEL, height=px(8)).pack(fill="x")

            # ---- 对战设置 -------------------------------------------------
            _section_title(body, "对战设置")
            mode_box = tk.Frame(body, bg=LINE)
            mode_box.pack(fill="x")
            for column, (key, text_) in enumerate(
                (("standard", "标准"), ("wild", "狂野"), ("casual", "休闲"))
            ):
                lab = tk.Label(
                    mode_box,
                    text=text_,
                    bg=PANEL,
                    fg=DIM,
                    cursor="hand2",
                    font=("Microsoft YaHei", 9, "bold"),
                    pady=px(6),
                )
                lab.grid(
                    row=0,
                    column=column,
                    sticky="ew",
                    padx=(px(1) if column else 0, 0),
                )
                lab.bind("<Button-1>", lambda _e, k=key: _select_mode(k))
                mode_box.columnconfigure(column, weight=1)
                resident_mode_labels[key] = lab

            steppers = tk.Frame(body, bg=TITLE_BG)
            steppers.pack(fill="x", padx=px(12), pady=(px(8), px(2)))
            _build_number_row(steppers, "卡组", "deck", 1, 0, 9)
            _build_number_row(steppers, "对局中", "stale_game", 30, 60, 3600)
            _build_number_row(steppers, "其他界面", "stale_other", 60, 60, 3600)

            # ---- 自动恢复说明（紧凑列表，不再是大块空卡） ------------------
            _section_title(body, "自动恢复")
            rules = tk.Frame(body, bg=TITLE_BG)
            rules.pack(fill="x", padx=px(12))
            rule_labels = []
            for line in resident_rule_lines({}):
                lab = tk.Label(
                    rules,
                    text=line,
                    bg=TITLE_BG,
                    fg=DIM,
                    font=("Microsoft YaHei", 8),
                    anchor="w",
                    justify="left",
                )
                lab.pack(fill="x", pady=px(2))
                rule_labels.append(lab)
            resident_widgets["rules"] = rule_labels

            # ---- 底部按钮行 + 提示行 ---------------------------------------
            tk.Frame(body, bg=TITLE_BG).pack(fill="both", expand=True)
            btn_row = tk.Frame(body, bg=TITLE_BG)
            btn_row.pack(fill="x", pady=(px(12), 0))
            btn_row.columnconfigure(0, weight=1)
            btn_row.columnconfigure(1, weight=1)
            apply_btn = _make_btn(btn_row, "保存设置", PANEL2, _call_resident_apply)
            apply_btn.config(fg=TEXT)
            apply_btn.grid(row=0, column=0, sticky="ew", padx=(0, px(4)))
            toggle_btn = _make_btn(btn_row, "开启常驻", ACCENT, _call_resident_toggle)
            toggle_btn.grid(row=0, column=1, sticky="ew", padx=(px(4), 0))
            # _make_btn 的悬停色绑的是创建时的颜色；开关按钮颜色会随状态变，
            # 这里覆盖一次让悬停/离开跟随当前 bg。
            toggle_btn.bind(
                "<Enter>",
                lambda _e: _set(
                    toggle_btn,
                    bg=_hover(resident_values.get("toggle_bg") or ACCENT),
                ),
            )
            toggle_btn.bind(
                "<Leave>",
                lambda _e: _set(
                    toggle_btn, bg=resident_values.get("toggle_bg") or ACCENT
                ),
            )
            resident_widgets["apply"] = apply_btn
            resident_widgets["toggle"] = toggle_btn

            hint = tk.Label(
                body,
                text="保存后立即生效",
                bg=TITLE_BG,
                fg=DIM,
                font=("Microsoft YaHei", 8),
                anchor="w",
            )
            hint.pack(fill="x", padx=px(12), pady=(px(8), px(8)))
            resident_widgets["hint"] = hint

            _highlight_mode(resident_values["mode"])
            _render_number("deck")
            _render_number("stale_game")
            _render_number("stale_other")
            return page
        # ---- drag ------------------------------------------------------
        # 整窗拖动绑在 root 上；但点滑条时事件会冒泡到这里，若不排除就会「按滑钮
        # 整个浮窗跟着动」。滑条自己在 widget 级绑定里 return "break" 已经吃掉事件，
        # 这里再兜一层（事件来源是滑条 canvas 就绝不拖窗）。
        _drag = {"x": 0, "y": 0, "active": False}

        def _from_slider(event):
            return getattr(event, "widget", None) is slider_canvas

        def _start_drag(event):
            if _from_slider(event):
                _drag["active"] = False
                return
            _drag["active"] = True
            _drag["x"], _drag["y"] = event.x, event.y

        def _on_drag(event):
            if not _drag["active"] or _from_slider(event):
                return
            nx = root.winfo_x() + event.x - _drag["x"]
            ny = root.winfo_y() + event.y - _drag["y"]
            root.geometry(f"+{nx}+{ny}")

        root.bind("<Button-1>", _start_drag)
        root.bind("<B1-Motion>", _on_drag)

        delay_bar = [None]  # 已画出的进度条比例（None = 当前没画）

        def _feed_content_width() -> int:
            """正文可用宽度（px）：真 Tk 量控件，伪 Tk/未布局时按窗口设计宽兜底。"""
            try:
                width = int(feed_text.winfo_width())
            except Exception:
                width = 0
            if width < 60:
                width = px(WINDOW_WIDTH - 46)
            return max(px(40), width - px(3))

        def _wrap_measured(text, font, width: int) -> list:
            """按像素折行：比 Text 的 wrap="char" 精确，保证每个视觉行都能挂竖条。

            末行只剩 1 个字时，从上一行借 1 个字过来 —— 中文不会出现「位」孤零零
            占一行的观感（用户 2026-11-01 反馈的缩进/折行问题）。
            """
            lines, cur, cur_w = [], [], 0.0
            for char in str(text):
                advance = float(font.measure(char))
                if cur and cur_w + advance > width:
                    lines.append("".join(cur))
                    cur, cur_w = [], 0.0
                cur.append(char)
                cur_w += advance
            lines.append("".join(cur))
            if len(lines) > 1 and len(lines[-1]) == 1 and len(lines[-2]) > 2:
                lines[-1] = lines[-2][-1] + lines[-1]
                lines[-2] = lines[-2][:-1]
            # 行首禁则：标点不收行首（挂到上一行行尾，允许 1 字溢出）。
            for index in range(1, len(lines)):
                if lines[index] and lines[index][0] in "，。、；：！？）」』】,.;:!?)":
                    lines[index - 1] += lines[index][0]
                    lines[index] = lines[index][1:]
            lines = [line for line in lines if line]
            return lines

        def _refresh():
            global _DELAY
            if _STOP.is_set():
                root.destroy()
                _STARTED[0] = False
                return
            if collapsed[0]:
                # 最小化时正文看不见：不重画（省 CPU），展开后靠
                # feed_shown[0]=None 整体重建，中间进来的块一行都不会丢。
                feed_shown[0] = None
                root.after(_REFRESH_MS, _update)
                return
            _sync_size_slider()
            if not layout_checked[0]:
                layout_checked[0] = True
                # 只在真 Tk 下能量（fake harness 没有 winfo_reqheight）。
                measure = getattr(root, "winfo_reqheight", None)
                if measure is not None:
                    try:
                        needed = int(measure())
                        if needed > H + 4:
                            _report(
                                f"浮窗内容超出窗口高度（需要 {needed}px > {H}px），"
                                "正文区会被压缩；请减小 action_feed 的块行数上限"
                                "或加高窗口。")
                    except Exception:
                        pass
            # 识别轮播：每 DETECT_ROTATE_SECONDS 换一条（结束回合/段位/开始按钮）。
            try:
                detect_entries = _OCR() if _OCR is not None else None
                detect_rows = detect_items(detect_entries)
            except Exception:
                detect_rows = []
            if detect_rows:
                try:
                    merged_log.record_detect_rows(detect_rows)
                except Exception:
                    pass
                detect_index = int(time.time() / DETECT_ROTATE_SECONDS) % len(detect_rows)
                _set(detect_value, text=detect_rows[detect_index])
                _set(detect_pos, text=(f"{detect_index + 1}/{len(detect_rows)}"
                                       if len(detect_rows) > 1 else ""))
            else:
                _set(detect_value, text=DETECT_EMPTY_TEXT)
                _set(detect_pos, text="")
            # 分块流：归块/渲染都在 action_feed，这里只按内容指纹整体重建，
            # 重建前后保存/恢复滚动位置（新块在最前，默认 0.0 = 贴顶看最新块）。
            if feed_expanded[0]:
                rows = action_feed.render()
                feed_fp = tuple(rows)
                if feed_fp != feed_shown[0]:
                    try:
                        position = feed_text.yview()[0]
                    except Exception:
                        position = 0.0
                    feed_text.config(state="normal")
                    feed_text.delete("1.0", "end")
                    exp_tags = set()
                    bar_mode = str(FEED_BAR_MODE or "bar")
                    content_w = _feed_content_width()
                    for row in rows:
                        row_text, row_tag = row[0], row[1]
                        bar_name = row[2] if len(row) > 2 else None
                        if isinstance(row_tag, tuple):
                            tags = row_tag
                            exp_tags.add(row_tag[1])
                        else:
                            tags = (row_tag,)
                        if row_tag == "card_sep":
                            dash_w = float(feed_regular_font.measure("┈")) or 8.0
                            count = max(1, int(content_w / dash_w))
                            feed_text.insert("end", "┈" * count + "\n", tags)
                            continue
                        if row_tag == "card_stage":
                            # 阶段行：居中金色，不带竖条。
                            feed_text.insert("end", row_text + "\n", tags)
                            continue
                        font = (feed_bold_font
                                if isinstance(row_tag, str) and row_tag in FEED_BOLD_TAGS
                                else feed_regular_font)
                        bar_px = 0
                        if bar_name and bar_mode != "none":
                            bar_px = int(font.measure(FEED_BAR_GLYPH)) + px(FEED_BAR_GAP_PX)
                        width = max(px(40), content_w - bar_px)
                        # 自己按像素折行：每个**视觉行**都画竖条，长日志的竖线才连贯。
                        for segment in _wrap_measured(row_text, font, width):
                            if bar_px:
                                if bar_mode == "block":
                                    feed_text.insert(
                                        "end", " ", (f"bar_block_{bar_name}",))
                                else:
                                    feed_text.insert(
                                        "end", FEED_BAR_GLYPH, (f"bar_{bar_name}",))
                                if FEED_BAR_GAP_PX:
                                    feed_text.insert("end", " ", tags)
                            feed_text.insert("end", segment + "\n", tags)
                    feed_text.config(state="disabled")
                    # 展开入口只绑一次：tag_bind 每绑一次都会新建 CallWrapper，
                    # 全表重绑会让 tag/回调随「重建次数 × 历史块数」无界增长。
                    try:
                        for tag_name in exp_tags - bound_exp:
                            try:
                                block_id = int(str(tag_name)[4:])
                            except ValueError:
                                continue
                            feed_text.tag_bind(
                                tag_name,
                                "<Button-1>",
                                lambda _e, bid=block_id: _toggle_card_expand(bid),
                            )
                        for tag_name in bound_exp - exp_tags:
                            feed_text.tag_delete(tag_name)
                    except Exception:
                        pass
                    bound_exp.clear()
                    bound_exp.update(exp_tags)
                    try:
                        feed_text.yview_moveto(position)
                    except Exception:
                        pass
                    feed_shown[0] = feed_fp
            if _IS_RUNNING is not None:
                if _IS_RUNNING():
                    _set(halt_btn, text="中止", bg=DANGER, activebackground=DANGER)
                else:
                    _set(halt_btn, text="恢复", bg=OK, activebackground=OK)
            starting = bool(_IS_STARTING()) if _IS_STARTING is not None else False
            if starting and not (_IS_RUNNING is not None and _IS_RUNNING()):
                # 点完「开始」到自动化线程挂上之间：给个「启动中…」，别让用户以为
                # 点了没反应（首次启动要导入组件 + 初始化，会有一小段空窗）。
                _set(
                    start_btn,
                    state="disabled",
                    text="启动中…",
                    bg=DISABLED,
                    activebackground=DISABLED,
                    disabledforeground=DIM,
                    cursor="arrow",
                )
            elif _IS_IN_GAME is not None and _IS_IN_GAME():
                _set(
                    start_btn,
                    state="disabled",
                    text="对局进行中",
                    bg=DISABLED,
                    activebackground=DISABLED,
                    disabledforeground=DIM,
                    cursor="arrow",
                )
            else:
                _set(
                    start_btn,
                    state="normal",
                    text="开始对战",
                    bg=ACCENT,
                    activebackground=ACCENT,
                    disabledforeground=DIM,
                    cursor="hand2",
                )
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
                    _set(
                        score_label,
                        text=f"战绩： 胜 {wins} · 负 {losses} · "
                        f"胜率 {rate_txt}{concede_txt}",
                        fg=TEXT,
                    )
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
                _set_detail(
                    ac_detail,
                    ac_used,
                    row["value"],
                    row["detail"],
                    fg=TEXT if hidden else DIM,
                )
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
                    _set(
                        delay_label,
                        text=f"{delay['desc']}（{remaining:.0f}/{total:.0f}s）",
                        fg=TEXT,
                    )
                    # 进度条每 350ms 重画一次会明显闪烁：进度变化小于 2% 时不重画。
                    if delay_bar[0] is None or abs(frac - delay_bar[0]) >= 0.02:
                        w = max(delay_canvas.winfo_width(), 1)
                        delay_canvas.delete("all")
                        delay_canvas.create_rectangle(
                            0, 0, w * frac, 8, fill=ACCENT, outline=""
                        )
                        delay_bar[0] = frac
            else:
                _set(delay_label, text="延时：无", fg=DIM)
                if delay_bar[0] is not None:
                    delay_canvas.delete("all")
                    delay_bar[0] = None
            _set_stop_after_state()
            _refresh_resident()
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
