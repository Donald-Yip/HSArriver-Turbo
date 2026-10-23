# -*- coding: utf-8 -*-
"""出错自动截图 + 反馈包导出。

要解决的问题
------------
线上跑脚本时，识别失败 / 重试 / 报错只在日志里留一行文字，作者要复现得靠用户
口述"卡在哪一步"。本模块在这些时刻**自动截一张炉石客户区**，并把本次运行的
错误文本 + 对应截图 + 完整结构化日志打包成一个 zip，用户直接发给作者即可。

设计
----
* 判定与截图逻辑是纯函数 + 可注入（``note_event`` / ``export_bundle``），
  **不 import web_ui / log_overlay**，截图能力由上层经 ``set_capture_provider``
  注册（仿 ``log_overlay.set_logger``），避免循环依赖，也方便单测。
* 触发：同一诊断码**连续重试 ≥ retry_threshold** 或出现 **ERROR/⚠️** 时截图；
  带节流（``throttle_seconds``）与上限（``max_shots_per_run``）。
* 截图在后台线程执行，绝不阻塞自动化主循环；任何失败都静默兜底。
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Union

try:
    import version as _version
except Exception:  # pragma: no cover
    _version = None

ROOT = Path(__file__).resolve().parent
# 反馈工作目录与产物都放 logs/ 下（script_logs 负责统计与清理）。
LOG_DIR = ROOT / "logs"
# 反馈包 / 工作目录前缀：清理时只认这一种，别误删用户自己放的东西。
FEEDBACK_PREFIX = "反馈_"
SHOTS_DIRNAME = "shots"
ZIP_SUFFIX = ".zip"
# 自动存档目录：``logs/出错记录_<YYYYMMDD>/``（按天合并，跨运行累积，清理日志才删）。
ARCHIVE_PREFIX = "出错记录_"
# zip 内归档子目录名（本次运行的错误文件夹都放这里）。
ARCHIVE_SUBDIR = "出错记录"
# 每个错误文件夹里的文件名。
ERROR_TEXT_NAME = "错误.txt"
ERROR_JSON_NAME = "错误.json"
SHOT_FILENAME = "截图.png"
# 反馈包名后缀：`<紧凑日期>-<盒子推荐>-错误反馈.zip`，清理时按这个后缀认。
FEEDBACK_NAME_SUFFIX = "错误反馈"
# 包名里“盒子推荐”取不到时的兜底词。
UNKNOWN_RECOMMENDATION = "未知推荐"
# 包名里推荐摘要最长多少个字（太长会顶爆文件名）。
MAX_RECO_NAME_LEN = 24
# 只有真正在回合内才自动截图（阶段标签来自 web_ui 的阶段监测）：
#   * 我方出牌 / 对手回合：对局进行中，截图有排查价值；
#   * 大厅、匹配、换牌、结算：只记录错误文本，不截图。
# 换牌阶段（"换牌"）按用户 2026-10-23 口径不算“回合内”。
CAPTURE_PHASES = ("我方出牌", "对手回合")
CAPTURE_SKIPPED_NOT_IN_TURN = "not_in_turn"
# 错误报告文件名（zip 内）。
REPORT_TEXT_NAME = "错误报告.txt"
REPORT_JSON_NAME = "errors.json"
ENV_NAME = "环境.txt"

_LOCK = threading.RLock()
_INCIDENTS: list = []
_SHOTS: list = []  # 本次运行已保存的截图（相对归档目录）
_ARCHIVES: list = []  # 本次运行已建的错误文件夹名（相对归档目录）
_STATE = {
    "stamp": None,
    "started_at": None,
    "streak_code": None,
    "streak": 0,
    "streak_captured": False,
    "last_capture_at": 0.0,
    "provider": None,
    "async": True,
    "env_provider": None,
    "opener": None,
}

_QUEUE: "list" = []
_QUEUE_CV = threading.Condition(_LOCK)
_WORKER: Optional[threading.Thread] = None
_STOP = threading.Event()


# ---------------------------------------------------------------- 配置读取
_SETTINGS_CACHE = {"at": 0.0, "data": None}
_SETTINGS_TTL = 3.0


def _settings() -> dict:
    """读 feedback 配置（带 3 秒缓存；config 不可用时回退到内置默认）。"""
    now = time.time()
    cached = _SETTINGS_CACHE["data"]
    if cached is not None and now - _SETTINGS_CACHE["at"] < _SETTINGS_TTL:
        return dict(cached)
    try:
        import config

        data = config.feedback_settings()
    except Exception:  # pragma: no cover
        data = {
            "enabled": True,
            "retry_threshold": 3,
            "capture_on_error": True,
            "throttle_seconds": 10.0,
            "max_shots_per_run": 30,
            "auto_export_on_stop": False,
        }
    _SETTINGS_CACHE["at"] = now
    _SETTINGS_CACHE["data"] = data
    return dict(data)


# ---------------------------------------------------------------- 会话
def _default_stamp(now: Optional[float]) -> str:
    ts = time.time() if now is None else float(now)
    return datetime.fromtimestamp(ts).strftime("%Y%m%d_%H%M%S")


def reset(session_stamp: Optional[str] = None, now: Optional[float] = None) -> str:
    """开始一轮反馈会话：清空事件、重试计数与截图。

    返回本次的运行标识（``YYYYmmdd_HHMMSS``）。错误存档按天合并到
    ``logs/出错记录_<YYYYMMDD>/``，文件夹名前缀就是它，用来区分“本次运行”。
    """
    with _LOCK:
        _INCIDENTS.clear()
        _SHOTS.clear()
        _ARCHIVES.clear()
        _QUEUE.clear()
        _STATE.update(
            {
                "stamp": session_stamp or _default_stamp(now),
                "started_at": time.time() if now is None else float(now),
                "streak_code": None,
                "streak": 0,
                "streak_captured": False,
                "last_capture_at": 0.0,
            }
        )
        return _STATE["stamp"]


def _ensure_session() -> str:
    with _LOCK:
        if _STATE["stamp"] is None:
            _STATE["stamp"] = _default_stamp(None)
            _STATE["started_at"] = time.time()
        return _STATE["stamp"]


def set_capture_provider(provider: Optional[Callable]) -> None:
    """注册截图函数 ``provider(dest_dir, name) -> Optional[str]``。

    返回保存后的路径（相对 zip 根的 ``shots/<name>.png`` 或绝对路径均可），
    拿不到就返回 None。传 None 关闭截图（仍记录错误文本）。
    """
    with _LOCK:
        _STATE["provider"] = provider


def set_env_provider(provider: Optional[Callable]) -> None:
    """注册环境信息补充函数 ``provider() -> dict``（版本/分辨率/自检等）。"""
    with _LOCK:
        _STATE["env_provider"] = provider


def set_opener(opener: Optional[Callable]) -> None:
    """注册“打开文件夹并选中文件”的函数 ``opener(path) -> bool``（导出后定位用）。"""
    with _LOCK:
        _STATE["opener"] = opener


def set_async(enabled: bool) -> None:
    """截图是否走后台线程（测试里设为 False 同步执行，便于断言）。"""
    with _LOCK:
        _STATE["async"] = bool(enabled)


def _ensure_worker() -> None:
    global _WORKER
    if _WORKER is not None and _WORKER.is_alive():
        return
    _STOP.clear()
    _WORKER = threading.Thread(
        target=_worker_loop, name="feedback-capture", daemon=True
    )
    _WORKER.start()


def _worker_loop() -> None:  # pragma: no cover - 线程体
    while not _STOP.is_set():
        with _QUEUE_CV:
            while not _QUEUE and not _STOP.is_set():
                _QUEUE_CV.wait(0.5)
            if _STOP.is_set() and not _QUEUE:
                return
            job = _QUEUE.pop(0) if _QUEUE else None
        if job is not None:
            _run_capture(*job)


def wait_idle(timeout: float = 3.0) -> bool:
    """等后台截图队列清空（测试/导出前调用）。返回是否已清空。"""
    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        with _LOCK:
            empty = not _QUEUE
        if empty:
            return True
        time.sleep(0.02)
    with _LOCK:
        return not _QUEUE


# ---------------------------------------------------------------- 事件
def _capture_allowed(ctx: Optional[dict]) -> bool:
    """当前阶段是否允许自动截图（只在回合内）。

    `ctx` 由 web_ui 传入，`phase` 是阶段监测写的中文标签。缺阶段/未知阶段
    一律不截图：宁可少一张图，也不在大厅/结算里截无关画面。
    """
    phase = str((ctx or {}).get("phase") or "").strip()
    return phase in CAPTURE_PHASES


def note_event(
    info: dict, ctx: Optional[dict] = None, now: Optional[float] = None
) -> Optional[dict]:
    """登记一条重试/报错事件，必要时自动截图。

    ``info`` 来自 ``battle_record.classify``（用其 ``kind`` / ``reason`` /
    ``text``）；``ctx`` 是当前回合上下文 ``{"turn", "phase", "instruction"}``，
    由调用方维护。返回本次新登记的事件（没登记则 None）。
    """
    settings = _settings()
    now = time.time() if now is None else float(now)
    ctx = ctx or {}
    capture_allowed = _capture_allowed(ctx)
    kind = str(info.get("kind") or "")
    if kind not in ("retry", "error", "warn"):
        return None

    with _LOCK:
        if not settings.get("enabled", True):
            return None
        _ensure_session()
        is_error = kind in ("error", "warn")
        if is_error:
            code = ""
            detail = info.get("text") or ""
            capture = bool(settings.get("capture_on_error", True))
            streak_at = 0
            should_record = True  # 报错一律记进反馈报告
        else:
            code = info.get("reason") or ""
            detail = code
            if code != _STATE["streak_code"]:
                _STATE["streak_code"] = code
                _STATE["streak"] = 1
                _STATE["streak_captured"] = False
            else:
                _STATE["streak"] += 1
            streak_at = _STATE["streak"]
            threshold = int(settings.get("retry_threshold", 3) or 3)
            capture = streak_at >= threshold and not _STATE["streak_captured"]
            if capture:
                _STATE["streak_captured"] = True
            # 同一段连续重试只在**达到阈值那一次**记一条（重试次数写进 retries），
            # 不再每次重试都记一条，避免报告被"重试×1、×2…"刷屏。
            should_record = capture
        if not should_record:
            return None
        if not capture_allowed:
            capture = False

        incident = {
            "index": len(_INCIDENTS) + 1,
            "time": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
            "turn": ctx.get("turn"),
            "phase": ctx.get("phase") or "",
            "type": "error" if is_error else "retry",
            "code": code,
            "retries": streak_at,
            "instruction": ctx.get("instruction") or "",
            "raw": list(ctx.get("raw") or []),
            "detail": detail,
            "screenshots": [],
            "folder": None,
            "capture_skipped": (None if capture_allowed
                                else CAPTURE_SKIPPED_NOT_IN_TURN),
        }
        _INCIDENTS.append(incident)

        # 立刻落盘：每个错误一个文件夹（截图 + 错误.txt + 错误.json），攒着不丢。
        created = _write_archive(incident, settings)
        if capture and created:
            _maybe_capture(incident, settings, now, force=is_error)
    return incident


def _write_archive(incident: dict, settings: dict) -> bool:
    """给一个错误建文件夹并写入 ``错误.txt`` / ``错误.json``（持锁调用）。

    达上限（``max_shots_per_run``，与截图上限同口径）就不再建，避免磁盘被
    极端刷屏场景灌爆。返回是否成功建了文件夹。
    """
    cap = int(settings.get("max_shots_per_run", 30) or 30)
    if len(_ARCHIVES) >= cap:
        return False
    folder = _archive_dir() / _archive_folder_name(incident)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ERROR_JSON_NAME).write_text(
            json.dumps(incident, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (folder / ERROR_TEXT_NAME).write_text(
            _incident_text(incident), encoding="utf-8"
        )
    except Exception:
        return False
    incident["folder"] = folder.name
    _ARCHIVES.append(folder.name)
    return True


def _maybe_capture(
    incident: dict, settings: dict, now: float, force: bool = False
) -> None:
    """满足节流/上限就调度一次截图（持锁调用）。

    ``force=True``（报错）时**跳过时间节流**——出错一定要留图；上限
    ``max_shots_per_run`` 仍作磁盘兜底。
    """
    provider = _STATE.get("provider")
    if provider is None:
        return
    throttle = float(settings.get("throttle_seconds", 10.0) or 0.0)
    if (
        not force
        and throttle
        and (now - float(_STATE.get("last_capture_at") or 0.0)) < throttle
    ):
        return
    _STATE["last_capture_at"] = now
    if not incident.get("folder"):
        return
    dest = _archive_dir() / incident["folder"]
    if _STATE.get("async", True):
        _ensure_worker()
        with _QUEUE_CV:
            _QUEUE.append((incident, dest, "截图", provider))
            _QUEUE_CV.notify()
    else:
        _run_capture(incident, dest, "截图", provider)


def _run_capture(incident, dest, name, provider) -> None:
    """真正调用截图 provider 并把结果记到事件上（异常一律吞掉）。"""
    try:
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        result = provider(dest, name)
    except Exception:
        return
    if not result:
        return
    if isinstance(result, dict):
        result = result.get("path") or result.get("relative")
    if not result:
        return
    rel = str(result).replace("\\", "/")
    try:
        rel = str(Path(result).relative_to(_archive_dir())).replace("\\", "/")
    except Exception:
        folder = incident.get("folder") or ""
        rel = f"{folder}/{Path(rel).name}" if folder else Path(rel).name
    added = False
    with _LOCK:
        if rel not in incident["screenshots"]:
            incident["screenshots"].append(rel)
            _SHOTS.append(rel)
            added = True
    if added:
        _rewrite_incident_files(incident)


def _rewrite_incident_files(incident: dict) -> None:
    """截图落盘后刷新该错误文件夹里的 ``错误.txt`` / ``错误.json``（补上截图信息）。"""
    folder = incident.get("folder")
    if not folder:
        return
    path = _archive_dir() / folder
    try:
        (path / ERROR_JSON_NAME).write_text(
            json.dumps(incident, ensure_ascii=False, indent=2), encoding="utf-8")
        (path / ERROR_TEXT_NAME).write_text(
            _incident_text(incident), encoding="utf-8")
    except Exception:
        pass


def _archive_folder_name(incident: dict) -> str:
    """``<运行标识>-<序号>_<回合/对局前>_<重试/报错>_<时刻>``。"""
    stamp = _run_stamp()
    turn = incident.get("turn")
    where = f"回合{turn}" if turn is not None else "对局前"
    kind = "报错" if incident.get("type") == "error" else "重试"
    moment = ""
    try:
        moment = datetime.strptime(
            str(incident.get("time") or ""), "%Y-%m-%d %H:%M:%S"
        ).strftime("%H%M%S")
    except ValueError:
        moment = ""
    parts = [f"{incident.get('index', 0):02d}", where, kind]
    if moment:
        parts.append(moment)
    # 运行标识与后面用 "-" 连接：导出时按 ``<运行标识>-`` 前缀筛出“本次运行”。
    return _sanitize(f"{stamp}-{'_'.join(parts)}")


def _run_stamp() -> str:
    with _LOCK:
        return _STATE.get("stamp") or _default_stamp(None)


def _sanitize(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", str(name)).strip("_") or "shot"


def _archive_dir() -> Path:
    """按天合并的出错存档目录：``logs/出错记录_<YYYYMMDD>/``。"""
    stamp = _run_stamp()
    day = stamp[:8] if len(stamp) >= 8 and stamp[:8].isdigit() else None
    if day is None:
        day = datetime.now().strftime("%Y%m%d")
    return LOG_DIR / f"{ARCHIVE_PREFIX}{day}"


def _incident_text(incident: dict) -> str:
    """单个错误的可读文本（每个错误文件夹里的 ``错误.txt``）。"""
    out = [f"# 错误记录 {incident.get('time', '')}"]
    where = str(incident.get("turn")) if incident.get("turn") is not None else "对局前"
    if incident.get("phase"):
        where += f"（{incident['phase']}）"
    out.append(f"时间    {incident.get('time', '')}")
    out.append(f"回合    {where}")
    if incident.get("type") == "retry":
        out.append(f"类型    重试 ×{incident.get('retries', 0)}")
        if incident.get("code"):
            out.append(f"诊断    {incident['code']}")
    else:
        out.append("类型    报错")
    if incident.get("instruction"):
        out.append(f"推荐    {incident['instruction']}")
    for raw in incident.get("raw") or []:
        out.append(f"原文    {raw}")
    if incident.get("detail"):
        out.append(f"详情    {incident['detail']}")
    if incident.get("screenshots"):
        out.append(f"截图    {SHOT_FILENAME}")
    elif incident.get("capture_skipped") == CAPTURE_SKIPPED_NOT_IN_TURN:
        out.append("截图    （非回合内，按设置不截图）")
    else:
        out.append("截图    （未截到）")
    return "\n".join(out) + "\n"


def mark_success() -> None:
    """一条链路成功（执行完成）后调用：重置连续重试计数。"""
    with _LOCK:
        _STATE["streak_code"] = None
        _STATE["streak"] = 0
        _STATE["streak_captured"] = False


# ---------------------------------------------------------------- 状态
def incidents() -> list:
    with _LOCK:
        return [
            dict(item, screenshots=list(item["screenshots"])) for item in _INCIDENTS
        ]


def shot_count() -> int:
    with _LOCK:
        return len(_SHOTS)


def status() -> dict:
    """给网页/浮窗看的快照。"""
    with _LOCK:
        return {
            "stamp": _STATE.get("stamp"),
            "incidents": len(_INCIDENTS),
            "retry_incidents": sum(1 for i in _INCIDENTS if i["type"] == "retry"),
            "error_incidents": sum(1 for i in _INCIDENTS if i["type"] == "error"),
            "screenshots": len(_SHOTS),
            "archives": len(_ARCHIVES),
            "streak": _STATE.get("streak") or 0,
            "streak_code": _STATE.get("streak_code"),
            "session_dir": str(_archive_dir()),
        }


# ---------------------------------------------------------------- 报告
def _meta() -> dict:
    with _LOCK:
        stamp = _STATE.get("stamp") or _default_stamp(None)
    app = ""
    if _version is not None:
        try:
            app = f"{_version.NAME} {_version.version_label()}"
        except Exception:
            app = ""
    return {
        "schema": 1,
        "session": stamp,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "app": app,
    }


def build_report_text(items, meta: Optional[dict] = None) -> str:
    meta = meta or _meta()
    retries = sum(1 for i in items if i["type"] == "retry")
    errors = sum(1 for i in items if i["type"] == "error")
    out = [f"# 错误反馈 {meta.get('date', '')}"]
    out.append(f"# 会话 {meta.get('session', '')} · 应用 {meta.get('app', '')}")
    out.append(f"# 共 {len(items)} 处异常（{retries} 重试 / {errors} 报错）")
    out.append("")
    if not items:
        out.append("（本次运行没有捕获到重试/报错。）")
        return "\n".join(out) + "\n"
    for item in items:
        title = f"重试 ×{item['retries']}" if item["type"] == "retry" else "报错"
        out.append(f"--- 异常 {item['index']} · {title} " + "-" * 20)
        out.append(f"  时间    {item['time']}")
        where = f"{item['turn']}" if item["turn"] is not None else "对局前"
        if item.get("phase"):
            where += f"（{item['phase']}）"
        out.append(f"  回合    {where}")
        if item["type"] == "retry":
            out.append(f"  诊断    {item['code']}")
        if item.get("instruction"):
            out.append(f"  推荐    {item['instruction']}")
        for raw in item.get("raw") or []:
            out.append(f"  原文    {raw}")
        if item["type"] == "error" and item.get("detail"):
            out.append(f"  详情    {item['detail']}")
        shots = item.get("screenshots") or []
        if shots:
            for shot in shots:
                out.append(f"  截图    {shot}")
        elif item.get("capture_skipped") == CAPTURE_SKIPPED_NOT_IN_TURN:
            out.append("  截图    （非回合内，按设置不截图）")
        else:
            out.append("  截图    （未截到，可能已截图节流/达上限或炉石不可见）")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def build_errors_obj(items, meta: Optional[dict] = None) -> dict:
    obj = dict(meta or _meta())
    obj.update(
        {
            "summary": {
                "incidents": len(items),
                "retry_incidents": sum(1 for i in items if i["type"] == "retry"),
                "error_incidents": sum(1 for i in items if i["type"] == "error"),
            },
            "incidents": list(items),
        }
    )
    return obj


# ---------------------------------------------------------------- 环境信息
def _build_env_text() -> str:
    lines = [f"# 环境信息 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"]
    try:
        lines.append(f"应用        {_meta().get('app', '')}")
    except Exception:
        pass
    lines.append(f"会话        {_STATE.get('stamp') or ''}")
    lines.append(f"Python      {sys.version.split()[0]} ({platform.machine()})")
    lines.append(f"系统        {platform.platform()}")
    provider = _STATE.get("env_provider")
    extra = {}
    if provider is not None:
        try:
            extra = provider() or {}
        except Exception:
            extra = {}
    for key, value in extra.items():
        lines.append(f"{str(key):<11}{value}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 导出
def _reco_summary(items) -> str:
    """包名里的“盒子推荐内容”：取首个异常当时的推荐，取不到用兜底词。"""
    for item in items:
        text = str(item.get("instruction") or "").strip()
        if text:
            text = re.sub(r"\s+", " ", text)
            text = re.sub(r'[\\/:*?"<>|]+', "", text).strip(" .")
            if text:
                return text[:MAX_RECO_NAME_LEN]
    return UNKNOWN_RECOMMENDATION


def _event_date(items) -> str:
    """包名的紧凑日期 ``YYYYMMDD``：取首个异常**当时**的日期（与日志同一时刻）。

    没有异常时回退到当前日期。
    """
    for item in items:
        stamp = str(item.get("time") or "")
        try:
            return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d")
        except ValueError:
            continue
    return datetime.now().strftime("%Y%m%d")


def feedback_zip_name(items=None, log_dir: Optional[Path] = None) -> str:
    """生成反馈包文件名：``<紧凑日期>-<盒子推荐>-错误反馈.zip``（自动避开重名）。"""
    items = incidents() if items is None else list(items)
    base = f"{_event_date(items)}-{_reco_summary(items)}-{FEEDBACK_NAME_SUFFIX}"
    directory = Path(log_dir or LOG_DIR)
    candidate = directory / f"{base}{ZIP_SUFFIX}"
    index = 2
    while candidate.exists():
        candidate = directory / f"{base}-{index}{ZIP_SUFFIX}"
        index += 1
    return candidate.name


def _open_in_explorer(path: Path) -> bool:
    """打开文件夹并选中该文件（复用 web_ui/script_logs 的 explorer /select 方式）。"""
    opener = _STATE.get("opener")
    if opener is not None:
        try:
            return bool(opener(str(path)))
        except Exception:
            return False
    try:
        if _is_windows():
            subprocess.Popen(["explorer", f"/select,{path}"])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])
        return True
    except Exception:
        return False


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def export_bundle(
    dst: Optional[Union[str, Path]] = None,
    now: Optional[float] = None,
    open_folder: bool = False,
) -> dict:
    """把本次运行的异常文本 + 截图 + 完整结构化日志打包成 zip。

    返回 ``{"ok", "path", "name", "incidents", "screenshots", "opened", "error"}``。
    ``open_folder=True`` 时打包成功后打开文件夹并选中该 zip（供浮窗/网页按钮定位）。
    任何异常都兜底成 ``ok=False``，绝不让导出把主流程带崩。
    """
    try:
        import battle_record
        import script_logs
    except Exception as exc:  # pragma: no cover
        return {
            "ok": False,
            "path": None,
            "name": None,
            "incidents": 0,
            "screenshots": 0,
            "opened": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    wait_idle()
    stamp = _ensure_session()
    items = incidents()
    meta = _meta()
    if dst is None:
        dst = LOG_DIR / feedback_zip_name(items)
    dst = Path(dst)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        blocks = battle_record.build_blocks(battle_record.records())
        jsonl_name = (
            script_logs.BATTLE_LOG_PREFIX + stamp + script_logs.BATTLE_RECORD_SUFFIX
        )
        txt_name = script_logs.BATTLE_LOG_PREFIX + stamp + script_logs.BATTLE_LOG_SUFFIX
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(REPORT_TEXT_NAME, build_report_text(items, meta))
            zf.writestr(
                REPORT_JSON_NAME,
                json.dumps(build_errors_obj(items, meta), ensure_ascii=False, indent=2),
            )
            zf.writestr(ENV_NAME, _build_env_text())
            zf.writestr(
                txt_name,
                battle_record.render_text(
                    blocks,
                    {"date": meta["date"], "session": stamp, "source": jsonl_name},
                ),
            )
            zf.writestr(
                jsonl_name,
                battle_record.render_jsonl(
                    blocks, session=stamp, app=meta.get("app", "")
                ),
            )
            for src, arcname in _collect_archive_files(stamp):
                zf.write(str(src), arcname=arcname)
        opened = _open_in_explorer(dst) if open_folder else False
        return {
            "ok": True,
            "path": str(dst),
            "name": dst.name,
            "incidents": len(items),
            "screenshots": shot_count(),
            "archives": len(_ARCHIVES),
            "opened": opened,
            "error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "path": str(dst),
            "name": dst.name,
            "incidents": len(items),
            "screenshots": shot_count(),
            "archives": len(_ARCHIVES),
            "opened": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _collect_archive_files(run_stamp: str) -> list:
    """收集**本次运行**的错误文件夹（``<运行标识>-*``）里的文件。

    返回 ``[(绝对路径, zip 内路径)]``，zip 内统一放在 ``出错记录/<文件夹>/...``。
    归档按天合并目录，所以必须用运行标识前缀筛出本次运行的那几份。
    """
    archive_dir = _archive_dir()
    if not archive_dir.is_dir():
        return []
    out = []
    try:
        entries = sorted(archive_dir.iterdir())
    except OSError:
        return []
    prefix = f"{run_stamp}-"
    for folder in entries:
        if not folder.is_dir() or not folder.name.startswith(prefix):
            continue
        try:
            files = sorted(folder.iterdir())
        except OSError:
            continue
        for item in files:
            if item.is_file():
                out.append((item, f"{ARCHIVE_SUBDIR}/{folder.name}/{item.name}"))
    return out


def shutdown() -> None:
    """停止后台线程（进程退出/测试收尾）。"""
    _STOP.set()
    with _QUEUE_CV:
        _QUEUE_CV.notify_all()
    worker = _WORKER
    if worker is not None and worker.is_alive():
        worker.join(timeout=1.0)


__all__ = [
    "reset",
    "note_event",
    "mark_success",
    "incidents",
    "status",
    "set_capture_provider",
    "set_env_provider",
    "set_opener",
    "set_async",
    "wait_idle",
    "export_bundle",
    "feedback_zip_name",
    "build_report_text",
    "build_errors_obj",
    "shutdown",
    "FEEDBACK_PREFIX",
    "FEEDBACK_NAME_SUFFIX",
    "SHOTS_DIRNAME",
    "ARCHIVE_PREFIX",
    "ARCHIVE_SUBDIR",
    "ERROR_TEXT_NAME",
    "ERROR_JSON_NAME",
    "SHOT_FILENAME",
    "ZIP_SUFFIX",
    "UNKNOWN_RECOMMENDATION",
    "REPORT_TEXT_NAME",
    "REPORT_JSON_NAME",
    "ENV_NAME",
]
