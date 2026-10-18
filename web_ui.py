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
import sys
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

# 版本号的唯一来源（网页顶栏、浮窗品牌行、CLI 都从这里取）。
import version

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
            _battle_ctx.setdefault("raw", []).append(info.get("text"))
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
    DEFAULT_LIVENESS,
    DEFAULT_RANK_STOP,
    RANK_STOP_ACTIONS,
    HOST,
    BASE_PORT,
    LOG_BUFFER_SIZE,
    _USER_DELAY_KEYS,
    RecommendationConfig,
    feedback_settings,
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


# ---------------------------------------------------------------- 配置持久化
DEFAULT_CONFIG = {
    "name": "",
    "log_root": "",
    "schedule_start": None,
    "schedule_end": None,
    "auto_concede": dict(DEFAULT_AUTO_CONCEDE),
    # 卡组识别投降：和 auto_concede 并存，各自独立开关。
    "auto_surrender": dict(DEFAULT_AUTO_SURRENDER),
    "liveness": dict(DEFAULT_LIVENESS),
    "rank_stop": dict(DEFAULT_RANK_STOP),
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
    return ("[OCR]" not in line) and any(k in line for k in keys)


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
def _schedule_worker(start_dt: datetime, end_dt):
    try:
        while datetime.now() < start_dt:
            with CTRL.lock:
                if CTRL.schedule.get("start") != start_dt:
                    return  # 计划被取消或替换
            time.sleep(0.5)
        _log("SYS", "到达计划开始时间，自动启动对战。")
        ok, msg = _start_automation()
        if not ok:
            with CTRL.lock:
                CTRL.phase = "idle"
                CTRL.schedule = {"start": None, "end": None}
                CTRL.last_error = msg
            _persist_schedule(None, None)
            _log("ERROR", f"定时启动失败：{msg}")
            return
        if end_dt is None:
            return
        while datetime.now() < end_dt:
            with CTRL.lock:
                if (
                    CTRL.schedule.get("start") != start_dt
                    or CTRL.automation_thread is None
                ):
                    return
            time.sleep(0.5)
        _log("SYS", "到达计划结束时间：等待本局对战打完再停止。")
        with CTRL.lock:
            CTRL.phase = "stopping"
            CTRL.stopped_by = "schedule"
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
_DELAY_LIVE_KEYS = frozenset(
    {
        "mulligan_ready_delay_seconds",
        "mulligan_retry_delay_seconds",
        "pre_action_delay_seconds",
        "first_turn_per_card_delay_seconds",
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


# ------------------------------------------------------------------ 炉石存活检测
def _current_liveness() -> dict:
    """返回当前生效的存活检测配置（默认值叠加 ui_config.json 的 liveness 段）。"""
    cfg = dict(DEFAULT_LIVENESS)
    saved = load_config().get("liveness")
    if isinstance(saved, dict):
        for key in cfg:
            if saved.get(key) is not None:
                cfg[key] = saved[key]
    return cfg


def api_save_liveness(body):
    """保存存活检测配置（ui_config.json 的 liveness 段）。

    这是安全兜底功能：炉石闪退/卡死后脚本不能一直对着失效画面空转
    （issue 反馈空转近 3 小时），所以默认开启，但允许用户关闭。
    """
    with CTRL.lock:
        if CTRL.automation_thread is not None and not _hot_requested(body):
            return {
                "ok": False,
                "error": "自动化运行中，请先停止后再修改存活检测配置。",
            }
        cfg = load_config()
        lv = _current_liveness()
        lv["enabled"] = bool(body.get("enabled", lv["enabled"]))
        try:
            warn = float(
                body.get("log_stale_warn_seconds", lv["log_stale_warn_seconds"])
            )
            stop = float(
                body.get("log_stale_stop_seconds", lv["log_stale_stop_seconds"])
            )
        except (TypeError, ValueError):
            return {"ok": False, "error": "停滞阈值必须为数字（秒）。"}
        warn = max(1.0, min(3600.0, warn))
        stop = max(warn, min(7200.0, stop))
        lv["log_stale_warn_seconds"] = warn
        lv["log_stale_stop_seconds"] = stop
        cfg["liveness"] = lv
        save_config(cfg)
    _log(
        "SYS",
        f"存活检测配置已保存：{'开启' if lv['enabled'] else '关闭'}"
        f"（Power.log 停滞 {warn:.0f}s 告警 / {stop:.0f}s 自动停止）。",
    )
    return {"ok": True, "message": "存活检测配置已保存", "liveness": lv}


# ------------------------------------------------------------------ 延时设置
# 各延时字段的边界与默认值（默认值取自 RecommendationConfig，即上游时序）。
_DELAY_BOUNDS = {
    "mulligan_ready_delay_seconds": (0.0, 120.0),
    "mulligan_post_ocr_delay_seconds": (0.0, 30.0),
    "mulligan_retry_delay_seconds": (0.0, 60.0),
    "first_turn_per_card_delay_seconds": (0.0, 20.0),
    "pre_action_delay_seconds": (0.0, 60.0),
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
        delays = dict(cfg.get("delays") or {})
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


def api_start(body: dict):
    body = body or {}
    # 浮窗「恢复」续跑：keep_score=True 时保留已累计战绩（场数/胜场/认输数），
    # 只有“重新开始”才从 0 计。
    keep_score = bool(body.get("keep_score"))
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
        # 手动开始会取消尚未开始的定时计划
        CTRL.schedule = {"start": None, "end": None}
        # 从这一刻到 _start_automation 真正把自动化线程挂上之前，网页/浮窗要显示
        # 「启动中…」（以前这个标志从未置位，等于没有），并挡住并发的第二次点击。
        CTRL.starting = True
    try:
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
    mode = str(body.get("mode") or "now")
    if mode == "after_game":
        with CTRL.lock:
            if CTRL.automation_thread is None:
                return {"ok": False, "error": "当前没有正在运行的自动化。"}
            fsm = CTRL.fsm
            CTRL.phase = "stopping"
            CTRL.stopped_by = "user_after_game"
            if CTRL.schedule.get("end"):
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
        CTRL.stopped_by = "user_immediate"
        CTRL.schedule = {"start": None, "end": None}
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
    with _ENTER_LOCK:
        entering = bool(_ENTER_STATE.get("running"))
    with _restart_lock:
        restarting = bool(_RESTART_STATE.get("running"))
    with CTRL.lock:
        fsm = CTRL.fsm
        automating = CTRL.automation_thread is not None
    with _QUEUE_LOCK:
        queued = bool(_QUEUE_STATE.get("running"))

    if entering or restarting:
        # 只有真有循环在跑才置位，否则会给下一次单击留个"已取消"的坑。
        battle_entry.request_cancel()
        if entering:
            cancelled.append("进入对战（等待/点击循环）")
        if restarting:
            cancelled.append("重启炉石后的等待")
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
    """炉石存活检测状态：自动化已加载时取状态机的实时判定，否则直接采样。

    直接采样让“还没开始对战”时浮窗/网页也能显示炉石进程在不在。
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
            "enabled": bool(_current_liveness().get("enabled")),
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
        # 「退出浮窗」：只关窗口，脚本继续跑，网页可以再打开。
        on_exit_overlay=_overlay_exit_overlay,
        on_exit=_overlay_exit,
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
    """浮窗「重启炉石」按钮（已确认过）：后台执行，立刻返回不卡浮窗。"""
    threading.Thread(
        target=_restart_hearthstone_worker, name="hs-restart", daemon=True
    ).start()
    return True


def _overlay_window_width():
    """浮窗「窗口宽度」滑条初值：炉石当前客户区宽度（拿不到返回 None → 滑条禁用）。"""
    try:
        import hs_window

        size = hs_window.current_client_size()
        return int(size[0]) if size else None
    except Exception:
        return None


def _overlay_resize_window(width):
    """浮窗「窗口宽度」滑条：按 16:9 设炉石客户区宽度；返回改后的 [w, h] 或 None。

    `hs_window.set_client_width` 会把宽度收进 720~3840 并对齐 16 的步进
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


def _restart_hearthstone_worker(resume=False, source="manual"):
    """停自动化 → 重置日志读取器 → 中止炉石 → 清空日志目录 → 重新拉起炉石。

    每一步都往网页/浮窗写日志；任何一步失败都立刻停下并说明原因，绝不在拿不准
    的状态下继续删文件。

    **重启的完成判据不是"进程起来了"，而是"认到主界面（「传统对战」可点）"**：
    炉石从启动到能操作还要过战网、登录、公告弹窗，只拉起进程就报成功会让后面的
    「进入对战」直接撞在加载画面上（用户 2026-10-17 明确要求）。

    ``resume=True`` 时（看门狗自动重启）再多走两步：**进对战 → 重新开始自动化**，
    真正做到"日志卡住就自己爬回来继续打"。
    ``source`` 只用于日志文案（``watchdog`` / ``manual``）。
    """
    _restart_stage("restarting")
    ok = _restart_hearthstone_steps()
    if not ok:
        if source == "watchdog":
            _log("ERROR", "看门狗重启未成功；已放弃本次自动恢复，等待人工处理。")
        _restart_finish("restart_failed", False, "重启炉石失败（见日志）")
        return False
    if not resume:
        return _restart_wait_main_menu()

    # ---- 恢复：等主界面 → 进入对战 → 重开自动化 ----
    import battle_entry

    _restart_stage("recovering")
    _log("SYS", "重启完成，开始自动恢复：等待主界面 → 进入对战 → 重新开始……")
    try:
        result = battle_entry.enter_battle(
            deck_index=None, click_start=False, logger=lambda text: _log("SYS", text)
        )
    except Exception as exc:
        _log("ERROR", f"自动进入对战失败：{exc}；请手动「进入传统对战」后再点开始。")
        _restart_finish("recover_failed", False, f"自动进入对战失败：{exc}")
        return False
    if not result.get("ok"):
        if result.get("stage") == "aborted":
            _log("WARN", "收到取消请求，已停在自动恢复这一步；不会重新开始自动化。")
            _restart_finish("cancelled", False, "已取消自动恢复")
            return False
        _log(
            "WARN",
            f"自动进入对战未完成：{result.get('message')}"
            "（可能已在选套牌界面；继续尝试开始自动化）",
        )
    else:
        _log("SYS", "已进入对战界面。")

    started = api_start({"keep_score": True})
    if not started.get("ok"):
        _log("ERROR", f"重启后重新开始自动化失败：{started.get('error')}")
        _restart_finish(
            "recover_failed", False, f"重启后重新开始自动化失败：{started.get('error')}"
        )
        return False
    _log("SYS", "✅ 重启并恢复成功：自动化已重新开始，继续挂机。")
    _restart_finish("recovered", True, "已重启并恢复自动化（继续挂机）")
    try:
        from src.safety.restart_watchdog import default_watchdog

        default_watchdog().note_restarted()
    except Exception:
        pass
    return True


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
        api_stop({"mode": "now"})
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
        logger=lambda text: _log("SYS", text),
    )
    if not launched.get("ok"):
        _log(
            "ERROR",
            f"炉石没能自动启动：{launched.get('error')}；"
            "请手动打开战网/炉石后再点「开始对战」。",
        )
        return False
    _log("SYS", f"炉石已重新启动：{launched.get('message')}")
    return True


def _restart_hearthstone_and_resume():
    """看门狗调用的入口：后台跑「重启 + 恢复」，立刻返回是否成功**派发**。

    返回 False 表示没能派发（比如上一次重启还在跑），此时看门狗应当放弃并告警，
    不要每 15s 又派一次。
    """
    with _restart_lock:
        if _RESTART_STATE.get("running"):
            _log("WARN", "看门狗想重启，但上一次「重启炉石」还没结束，本次跳过。")
            return False
        _restart_begin("watchdog")

    def _worker():
        try:
            _restart_hearthstone_worker(resume=True, source="watchdog")
        except Exception as exc:
            traceback.print_exc()
            _log("ERROR", f"看门狗自动重启异常：{exc}")
            _restart_finish("exception", False, f"看门狗自动重启异常：{exc}")
        finally:
            with _restart_lock:
                _RESTART_STATE["running"] = False

    threading.Thread(target=_worker, name="hs-watchdog-restart", daemon=True).start()
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


def _bind_watchdog_restarter() -> None:
    """给「中断重启」看门狗接上真正的重启动作。

    看门狗模块本身不认识 web_ui（避免循环 import），所以由这里注入。
    注入时机在启动时调用一次即可；重复调用无害（只是覆盖同一个函数）。
    """
    try:
        from src.safety.restart_watchdog import default_watchdog

        default_watchdog()._restarter = lambda cfg: _restart_hearthstone_and_resume()
    except Exception as exc:
        print(f"[watchdog] 注入重启通道失败（中断重启将不可用）：{exc}")


def _as_bool(value) -> bool:
    """把网页/CLI 传进来的开关值统一成布尔。

    网页复选框给的是真布尔，CLI 给人打的是 ``on/off/1/0/true/false`` ——
    两种都要认，否则 `hsctl watchdog on` 会被当成 False。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    return text in ("1", "on", "true", "yes", "y", "开", "开启", "启用")


def api_watchdog(body: dict = None) -> dict:
    """读取/保存「中断重启」配置。

    参数（都可省略，省略即保留当前值）：
      * ``enabled``        开关（"on"/"off"/true/false/1/0 都认）
      * ``stale_seconds``  日志停滞多少秒算卡住（默认 300 = 5 分钟）
      * ``max_restarts``   滑动窗口内最多重启几次（默认 3）
      * ``window_seconds`` 滑动窗口长度（默认 600 = 10 分钟）
    """
    from src.safety.restart_watchdog import default_watchdog

    wd = default_watchdog()
    current = dict(wd.settings())

    if body:
        raw_enabled = body.get("enabled")
        if raw_enabled is not None:
            current["enabled"] = _as_bool(raw_enabled)
        for key in ("stale_seconds", "window_seconds"):
            if body.get(key) not in (None, ""):
                try:
                    current[key] = max(30.0, float(body[key]))
                except (TypeError, ValueError):
                    return {"ok": False, "error": f"{key} 必须是数字（秒）"}
        if body.get("max_restarts") not in (None, ""):
            try:
                current["max_restarts"] = max(1, int(body["max_restarts"]))
            except (TypeError, ValueError):
                return {"ok": False, "error": "max_restarts 必须是正整数"}
        # 开关从关→开时，重置限流计数：上一轮"已经重启过 3 次"不该继续压着它。
        turning_on = current["enabled"] and not wd.settings().get("enabled")
        try:
            cfg = load_config()
            cfg["watchdog"] = current
            save_config(cfg)
        except Exception as exc:
            return {"ok": False, "error": f"保存失败：{exc}"}
        if turning_on:
            wd.reset()
        _log(
            "SYS",
            "「中断重启」已"
            + ("开启" if current["enabled"] else "关闭")
            + f"（停滞 {current['stale_seconds']:.0f}s 触发，"
            f"{current['window_seconds'] / 60:.0f} 分钟内最多 "
            f"{current['max_restarts']} 次）",
        )
    return {"ok": True, "watchdog": wd.state()}


def _watchdog_state() -> dict:
    try:
        from src.safety.restart_watchdog import default_watchdog

        state = default_watchdog().state()
    except Exception as exc:
        state = {"enabled": False, "status": "error", "message": str(exc)}
    state["restart_running"] = bool(_RESTART_STATE.get("running"))
    return state


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
    """设置"自动选择的卡组序号"（ui_config.json 的 `battle_entry.deck_index`）。

    每次开局前都会重新读盘，所以改完下一局就生效，不用重启脚本。
    `{"clear": true}` = 关掉自动选卡组（保持你手选的卡组）。
    """
    body = body or {}
    from config import save_battle_entry_setting

    index = None if body.get("clear") else body.get("index")
    try:
        saved = save_battle_entry_setting("deck_index", index)
    except (KeyError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": f"保存卡组序号失败：{exc}"}
    value = saved.get("deck_index")
    if value is None:
        message = "已关闭自动选卡组（保持你手选的卡组）"
    else:
        import battle_entry

        x, y = battle_entry.deck_slot_point(int(value))
        message = (
            f"已设置：每局自动选第 {value} 个卡组"
            f"（点击点 {x},{y}；只按当前页的 9 格算）"
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
    # 浮窗是否真的开着：网页用它同步「🪟 日志浮窗（开/关）」按钮文字，
    # 也能解释「为什么大按钮回到了开始运行」。
    try:
        overlay_running = bool(log_overlay is not None and log_overlay.is_running())
    except Exception:
        overlay_running = False
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
        "liveness": {**_liveness_state(), **_current_liveness()},
        "liveness_alert": _liveness_state().get("alert"),
        "watchdog": _watchdog_state(),
        "name_match": _name_match_state(),
        "delays": _current_delays(),
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
            elif path == "/api/liveness":
                self._json(api_save_liveness(body))
            elif path == "/api/watchdog":
                self._json(api_watchdog(body))
            elif path == "/api/delays":
                self._json(api_save_delays(body))
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
    threading.Thread(target=_self_shutdown, name="hs-self-stop", daemon=True).start()
    return {"ok": True, "message": "已受理：控制台正在退出"}, 200


def _self_shutdown() -> None:
    """自停收尾：先优雅停自动化，再清端口文件，最后退出进程。"""
    try:
        if CTRL.automation_thread is not None:
            try:
                api_stop({"mode": "now"})
            except Exception:
                pass
            deadline = time.time() + 15
            while time.time() < deadline and CTRL.automation_thread is not None:
                time.sleep(0.2)
    except Exception:
        pass
    _clear_port_file()
    # 用 os._exit：这是"自己结束自己"，不需要（也拿不到）任何特权。
    os._exit(0)


# ---------------------------------------------------------------- 启动
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
    # 「中断重启」看门狗的重启通道：把 web_ui 的重启动作注入给看门狗模块
    # （看门狗本身不认识 web_ui，靠这里接线，避免循环 import）。
    _bind_watchdog_restarter()
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
        print("  [!] 当前不是管理员权限，自动化鼠标/键盘操作可能失效，")
    print("      建议以管理员身份重新运行本程序。")
    print("  随时按 Ctrl+Q 急停（自动化 / 手动进对战都停）；关闭本窗口即退出控制台。")
    print("=" * 64)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n控制台已退出。")
    finally:
        _clear_port_file()


if __name__ == "__main__":
    main()
