# -*- coding: utf-8 -*-
"""炉石「重置」四件小事：定位路径 / 清空日志目录 / 中止进程 / 重新拉起。

用途：日志浮窗的「重启炉石」按钮。触发场景是**日志状态和游戏实际对不上**——
例如上一局残留的 Power.log 被 find_latest_power_log() 当成“最新”，脚本明明
没在游戏却一直显示在换牌。这时把炉石彻底重启、日志目录清空，状态就回到干净
起点。

设计要点：
  * 本模块只做四个纯函数，**编排交给调用方**
    （web_ui._restart_hearthstone_worker），这样每一步都能单独测；
  * 删除范围**只有 log_root 的直接子项**，并且永远保留 Log.config；resolve_paths
    会拒绝盘符根这类危险路径，绝不递归到上级目录；
  * 进程检测复用 src.safety.hearthstone_liveness（纯 ctypes，不引新依赖）；
    kill 之后必须**等到进程真的消失**才算成功；
  * taskkill / startfile / 进程检测 / sleep 全部可注入，方便单测。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

# 炉石可执行文件名（与 src/safety/hearthstone_liveness 的进程名口径一致）。
EXE_NAME = "Hearthstone.exe"
# 清空日志目录时**永远保留**的名字。
KEEP_NAMES = frozenset({"Log.config"})
# 中止后等进程消失、启动后等进程出现的超时（秒）与轮询间隔。
KILL_TIMEOUT = 20.0
LAUNCH_TIMEOUT = 60.0
POLL_INTERVAL = 0.5


def _process_running() -> Optional[bool]:
    """Hearthstone.exe 是否在运行；枚举失败返回 None（未知）。"""
    try:
        from src.safety.hearthstone_liveness import hearthstone_process_running
        return hearthstone_process_running()
    except Exception:
        return None


def _probe(process_running: Optional[Callable[[], Optional[bool]]]):
    """跑一次进程检测，异常统一当成“未知”。"""
    try:
        return process_running()
    except Exception:
        return None


def _default_opener(path: str) -> None:
    os.startfile(path)          # noqa: S606 - Windows 专用，故意用系统默认方式


def resolve_paths(log_root) -> dict:
    """从日志目录推出炉石安装目录与 exe 路径。

    返回 ``{ok, log_root, exe, exe_found, error}``：
      * log_root 为空 / 不存在 / 是盘符根 → ok=False（绝不对危险路径动手）；
      * exe = log_root 的上级目录下的 Hearthstone.exe（存在与否另报 exe_found）。
    """
    result = {"ok": False, "log_root": None, "exe": None, "exe_found": False,
              "error": ""}
    raw = str(log_root or "").strip()
    if not raw:
        result["error"] = "没有配置炉石日志目录"
        return result
    try:
        root = Path(raw).resolve()
    except Exception as exc:
        result["error"] = f"日志目录无法解析：{exc}"
        return result
    if not root.is_dir():
        result["error"] = f"日志目录不存在：{root}"
        return result
    if root.parent == root:                     # 盘符根，例如 D:\
        result["error"] = f"拒绝操作盘符根目录：{root}"
        return result
    exe = root.parent / EXE_NAME
    result.update({"ok": True, "log_root": root, "exe": exe,
                   "exe_found": exe.is_file()})
    return result


def list_entries(log_root) -> list[Path]:
    """列出日志目录的直接子项（只列一层，用于汇报“清掉了什么”）。"""
    try:
        return sorted(Path(log_root).iterdir())
    except Exception:
        return []


def clear_logs(log_root, keep=KEEP_NAMES,
               logger: Optional[Callable[[str], None]] = None) -> dict:
    """清空日志目录内容（保留 keep 名单里的文件）。

    只删 log_root 的**直接子项**：目录用 rmtree，文件用 unlink；单个条目失败
    只记录不中断，最后用 ok 汇总。返回
    ``{ok, removed, skipped, failed, errors}``。
    """
    info = resolve_paths(log_root)
    if not info["ok"]:
        return {"ok": False, "removed": 0, "skipped": [], "failed": [],
                "errors": [info["error"]]}
    root = info["log_root"]
    keep_lower = {str(name).lower() for name in keep}
    removed = 0
    skipped: list[str] = []
    failed: list[str] = []
    errors: list[str] = []
    for entry in list_entries(root):
        if entry.name.lower() in keep_lower:
            skipped.append(entry.name)
            continue
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError as exc:
            failed.append(entry.name)
            errors.append(f"{entry.name}: {exc}")
            continue
        removed += 1
        if logger is not None:
            try:
                logger(f"已删除 {entry.name}")
            except Exception:
                pass
    return {"ok": not failed, "removed": removed, "skipped": skipped,
            "failed": failed, "errors": errors}


def kill_hearthstone(runner: Optional[Callable] = None,
                     process_running: Optional[Callable] = None,
                     sleeper: Callable[[float], None] = time.sleep,
                     timeout: float = KILL_TIMEOUT,
                     exe_name: str = EXE_NAME) -> dict:
    """强制结束炉石进程，并等它真的消失。

    ``process_running()`` 返回 True/False/None（None = 读不到进程列表）。
    已经没在运行 → ok=True/killed=False；taskkill 之后超时仍在 → ok=False。
    """
    run = runner or subprocess.run
    probe = process_running or _process_running
    state = _probe(probe)
    if state is False:
        return {"ok": True, "killed": False, "running": False,
                "message": "炉石没有在运行，跳过中止"}
    try:
        # stdout/stderr 一律丢弃：这里是后台线程，不要占管道、也不要刷屏。
        run(["taskkill", "/F", "/T", "/IM", exe_name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    except Exception as exc:
        return {"ok": False, "killed": False, "running": True,
                "error": f"执行 taskkill 失败：{exc}"}

    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if _probe(probe) is False:
            return {"ok": True, "killed": True, "running": False,
                    "message": f"{exe_name} 已退出"}
        if time.monotonic() >= deadline:
            return {"ok": False, "killed": True, "running": True,
                    "error": f"{exe_name} 在 {float(timeout):.0f}s 内没有退出"
                             "（可能权限不足或被其它程序占用）"}
        sleeper(POLL_INTERVAL)


def launch_hearthstone(exe=None, opener: Optional[Callable] = None,
                       fallback: Optional[Callable] = None,
                       process_running: Optional[Callable] = None,
                       sleeper: Callable[[float], None] = time.sleep,
                       timeout: float = LAUNCH_TIMEOUT,
                       wait: bool = True) -> dict:
    """重新拉起炉石：优先直接启动 exe，找不到 exe 时走 fallback（战网入口）。

    fallback 返回值判 False 视为“没起来”（click.enter_HS 找不到战网时返回
    False）。返回 ``{ok, started, running, message}/{ok, error}``。
    """
    open_path = opener or _default_opener
    probe = process_running or _process_running
    started = "exe"
    if exe is not None and Path(exe).is_file():
        try:
            open_path(str(exe))
        except Exception as exc:
            return {"ok": False, "started": None, "running": None,
                    "error": f"启动炉石失败：{exc}"}
    elif fallback is not None:
        started = "launcher"
        try:
            result = fallback()
        except Exception as exc:
            return {"ok": False, "started": None, "running": None,
                    "error": f"通过战网启动炉石失败：{exc}"}
        if result is False:
            return {"ok": False, "started": None, "running": None,
                    "error": "没找到战网窗口，无法启动炉石"}
    else:
        return {"ok": False, "started": None, "running": None,
                "error": "找不到 Hearthstone.exe，且没有可用的战网入口"}

    if not wait:
        return {"ok": True, "started": started, "running": None,
                "message": "已发出启动命令"}
    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        if _probe(probe) is True:
            return {"ok": True, "started": started, "running": True,
                    "message": "炉石已启动"}
        sleeper(POLL_INTERVAL)
    return {"ok": True, "started": started, "running": False,
            "warning": f"已发出启动命令，但 {float(timeout):.0f}s 内还没检测到"
                       f" {EXE_NAME}（起得慢的话再等一会儿即可）"}
