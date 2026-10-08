# -*- coding: utf-8 -*-
"""脚本自己写的日志文件：统计体积 / 打开位置 / 一键清理。

「脚本运行很久会有大量的日志积存」——积存来自两处，都在这里统一处理：

  * ``logs\\对战日志_<时间戳>.txt``：浮窗「保存日志」写下的对局日志，
    一份 1MB 上下，攒久了就是几百 MB（本机实测：193 个 / 214 MB）；
  * ``ui_log_last.txt``：``web_ui._log()`` 写的运行日志（也接住自动化的
    stdout），本应自动裁到 2MB（见 web_ui 的 LOG_TRIM_*）。

**故意不碰**的东西：

  * ``log\\*.txt``（sys/error/warn/debug/info）：``print_info.py`` 启动时用
    ``open(..., "w")`` 打开并**常驻句柄**，运行期间删不掉，而且每次启动都会
    重写，本来就不会积存；
  * 炉石自己的 ``Logs`` 目录：那是「重启炉石」的清理范围（hs_restart.clear_logs），
    绝不能在用户没确认重启的时候一起删掉。

设计沿用 hs_restart.py 的路子：**本模块只做纯函数，编排交给 web_ui**，
外部副作用（open / subprocess）都可注入，方便单测。
"""
from __future__ import annotations

import datetime
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent
# 对战日志目录（log_overlay 的「保存日志」写这里）。
LOG_DIR = ROOT / "logs"
# 运行日志（web_ui._log 写这里；边跑边删没问题，下一次写会自动重建）。
RUNTIME_LOG = ROOT / "ui_log_last.txt"
# 「保存日志」的文件名前缀 / 后缀：清理时只认这一种，别误删用户自己放的东西。
BATTLE_LOG_PREFIX = "对战日志_"
BATTLE_LOG_SUFFIX = ".txt"


def new_battle_log_path(now: Optional[datetime.datetime] = None) -> Path:
    """生成一份新的对战日志路径：``logs/对战日志_YYYYmmdd_HHMMSS.txt``。

    命名集中在这里，避免 log_overlay 与本模块各写一份。
    """
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d_%H%M%S")
    return LOG_DIR / f"{BATTLE_LOG_PREFIX}{stamp}{BATTLE_LOG_SUFFIX}"


def is_battle_log(name: str) -> bool:
    """名字是不是「保存日志」写的对战日志（清理范围就靠它）。"""
    text = str(name or "")
    return text.startswith(BATTLE_LOG_PREFIX) and text.endswith(BATTLE_LOG_SUFFIX)


def format_size(num_bytes) -> str:
    """人类可读的体积：``0 B`` / ``999 B`` / ``512.0 KB`` / ``214.2 MB`` / ``1.2 GB``。"""
    try:
        value = float(num_bytes or 0)
    except (TypeError, ValueError):
        return "—"
    if value < 0:
        value = 0.0
    if value < 1024:
        return f"{int(value)} B"
    for unit, step in (("KB", 1024.0), ("MB", 1024.0 ** 2), ("GB", 1024.0 ** 3),
                       ("TB", 1024.0 ** 4)):
        if value < step * 1024 or unit == "TB":
            return f"{value / step:.1f} {unit}"
    return f"{value:.1f} TB"       # pragma: no cover - 到不了


def _safe_stat_size(path: Path) -> int:
    """取文件大小；读不到按 0 算（别让一个坏文件毁掉整次统计）。"""
    try:
        if path.is_file():
            return int(path.stat().st_size)
    except OSError:
        pass
    return 0


def _safe_dir(log_dir: Path) -> Path:
    """把目录解析成绝对路径（统计/清理前的防呆，不存在的目录返回原值）。"""
    try:
        return Path(log_dir).resolve()
    except OSError:
        return Path(log_dir)


def scan(log_dir: Optional[Path] = None,
         runtime_log: Optional[Path] = None) -> dict:
    """统计脚本日志：对战日志个数/体积 + 运行日志体积。

    返回 ``{files, bytes, battle_files, battle_bytes, runtime_bytes, newest}``。
    目录不存在、单个条目 stat 失败都按"没有"处理，绝不抛异常。
    """
    battle_dir = _safe_dir(log_dir or LOG_DIR)
    runtime = Path(runtime_log or RUNTIME_LOG)
    result = {"files": 0, "bytes": 0, "battle_files": 0, "battle_bytes": 0,
              "runtime_bytes": 0, "newest": None}
    newest: Optional[Path] = None
    newest_key = None
    try:
        entries = list(os.scandir(battle_dir))
    except OSError:
        entries = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        if not is_battle_log(entry.name):
            continue
        try:
            info = entry.stat()
            size = int(info.st_size)
            mtime = float(info.st_mtime)
        except OSError:
            continue
        result["battle_files"] += 1
        result["battle_bytes"] += size
        # 名字里就是时间戳：同一秒写出来的多份用名字兜底，保证"最新一个"
        # 永远是名字最大的那份（否则同一秒保存会随机挑）。
        key = (mtime, entry.name)
        if newest_key is None or key > newest_key:
            newest_key = key
            newest = Path(entry.path)
    runtime_bytes = _safe_stat_size(runtime)
    result["runtime_bytes"] = runtime_bytes
    result["files"] = result["battle_files"] + (1 if runtime_bytes else 0)
    result["bytes"] = result["battle_bytes"] + runtime_bytes
    result["newest"] = newest
    return result


def clear(log_dir: Optional[Path] = None, runtime_log: Optional[Path] = None,
          logger: Optional[Callable[[str], None]] = None) -> dict:
    """删掉脚本自己写的日志：``logs\\对战日志_*.txt`` + 运行日志。

    只 ``unlink`` 单文件，**从不 rmtree、从不删其它文件**；单个文件失败只记录
    不中断（典型场景：用户正用记事本打开某份对战日志）。运行日志删不掉时兜底
    截断成 0 字节。返回 ``{ok, removed, freed_bytes, failed, errors, skipped}``。
    """
    battle_dir = _safe_dir(log_dir or LOG_DIR)
    runtime = Path(runtime_log or RUNTIME_LOG)
    removed = 0
    freed = 0
    failed: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    def note(text: str) -> None:
        if logger is not None:
            try:
                logger(text)
            except Exception:
                pass

    try:
        entries = list(os.scandir(battle_dir))
    except OSError:
        entries = []
    for entry in entries:
        try:
            if not entry.is_file():
                if is_battle_log(entry.name):
                    skipped.append(entry.name)
                continue
        except OSError:
            continue
        if not is_battle_log(entry.name):
            skipped.append(entry.name)      # 不匹配的不动，也不报错
            continue
        size = 0
        try:
            size = int(entry.stat().st_size)
        except OSError:
            pass
        try:
            Path(entry.path).unlink()
        except OSError as exc:
            failed.append(entry.name)
            errors.append(f"{entry.name}: {type(exc).__name__}: {exc}")
            continue
        removed += 1
        freed += size
    # 单个文件不逐条播报：一次清理动辄 200 份，日志里只需要一行汇总
    # （失败项会由调用方单独报）。下面的运行日志删掉/截断会单独说一句。

    if runtime.exists():
        size = _safe_stat_size(runtime)
        try:
            runtime.unlink()
            removed += 1
            freed += size
            note(f"已删除 {runtime.name}")
        except OSError as exc:
            # 删不掉（被占用等）：退一步把它清空，至少不再占空间。
            try:
                runtime.write_text("", encoding="utf-8")
                removed += 1
                freed += size
                note(f"已清空 {runtime.name}（删不掉，可能正被打开）")
            except OSError as exc2:
                failed.append(runtime.name)
                errors.append(f"{runtime.name}: {type(exc).__name__}: {exc}"
                              f" / 截断也失败: {type(exc2).__name__}: {exc2}")

    return {"ok": not failed, "removed": removed, "freed_bytes": freed,
            "failed": failed, "errors": errors, "skipped": skipped}


def _default_opener(path) -> None:
    os.startfile(str(path))       # noqa: S606 - Windows 专用，故意用系统默认方式


def _default_runner(argv) -> None:
    # explorer 正常也会返回 1，这里不看返回码，也不等它结束。
    subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)


def open_location(log_dir: Optional[Path] = None,
                  opener: Optional[Callable] = None,
                  runner: Optional[Callable] = None) -> dict:
    """打开日志所在位置：有对战日志就选中最新那份，否则直接打开目录。

    选文件用 ``explorer /select,<path>``；目录不存在就先建出来（不然
    Explorer 会弹"找不到"）。返回 ``{ok, opened, target, error}``。
    """
    battle_dir = _safe_dir(log_dir or LOG_DIR)
    open_path = opener or _default_opener
    run = runner or _default_runner
    newest = scan(battle_dir).get("newest")
    try:
        if newest is not None:
            run(["explorer", f"/select,{newest}"])
            return {"ok": True, "opened": "file", "target": str(newest),
                    "error": ""}
        battle_dir.mkdir(parents=True, exist_ok=True)
        open_path(battle_dir)
        return {"ok": True, "opened": "folder", "target": str(battle_dir),
                "error": ""}
    except Exception as exc:
        return {"ok": False, "opened": None, "target": str(battle_dir),
                "error": f"{type(exc).__name__}: {exc}"}
