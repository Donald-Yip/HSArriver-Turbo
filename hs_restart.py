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
  * 重新拉起时**优先走战网**：战网没开就先启动 Battle.net Launcher.exe，等战网
    窗口出来再点「开始游戏」；只有连战网都用不上才直接启动 Hearthstone.exe。
    启动成功的判据是**游戏窗口出现**（不是“进程存在”）——直启 exe 时进程会
    秒起秒退，只看进程就会误报成功，用户看到的是“重启一半又关了”；
  * taskkill / startfile / 进程检测 / 窗口检测 / sleep 全部可注入，方便单测。
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
# 战网启动器：战网没开时得先把它拉起来，炉石只有被战网点起来才有登录态
# （直启 Hearthstone.exe 会秒起秒退，表现就是“重启一半又关了”）。
BATTLENET_EXE_NAME = "Battle.net Launcher.exe"
# 清空日志目录时**永远保留**的名字。
KEEP_NAMES = frozenset({"Log.config"})
# 中止后等进程消失、启动后等游戏窗口的超时（秒）与轮询间隔。
KILL_TIMEOUT = 20.0
# 等"游戏窗口出现"的总超时：炉石从点到出窗口常常要 10~40s（慢盘更久）。
LAUNCH_TIMEOUT = 45.0
# 等"战网窗口出现"的超时；战网起来后还要给它一点时间把「开始游戏」按钮画出来。
BATTLENET_TIMEOUT = 60.0
BATTLENET_WARMUP = 6.0
# 点「开始游戏」之前先等这么久：战网刚起来（或刚切到前台）时按钮还没画好，
# 点太快会点空，表现为“点了没反应/炉石没起来”。
BEFORE_CLICK_DELAY = 5.0
# 等游戏窗口期间重试点「开始游戏」的间隔（战网的按钮偶尔第一下没反应）。
LAUNCH_RETRY_AFTER = 15.0
# 拿不到窗口检测时的兜底判据：进程要稳定存活这么久才算真的起来了。
LAUNCH_SETTLE = 6.0
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


def _battlenet_candidates(log_root: Path) -> list[Path]:
    """战网启动器可能的位置：先按“炉石安装目录的上一级”推，再看两个默认安装位置。"""
    candidates = [log_root.parent.parent / BATTLENET_EXE_NAME]
    for env_name in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base) / "Battle.net" / BATTLENET_EXE_NAME)
    return candidates


def resolve_paths(log_root) -> dict:
    """从日志目录推出炉石与战网的安装路径。

    返回 ``{ok, log_root, exe, exe_found, battlenet_exe, battlenet_found, error}``：
      * log_root 为空 / 不存在 / 是盘符根 → ok=False（绝不对危险路径动手）；
      * exe = log_root 的上级目录下的 Hearthstone.exe（存在与否另报 exe_found）；
      * battlenet_exe = 战网启动器（有就用它把战网拉起来再点「开始游戏」）。
    """
    result = {"ok": False, "log_root": None, "exe": None, "exe_found": False,
              "battlenet_exe": None, "battlenet_found": False, "error": ""}
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
    battlenet = None
    for candidate in _battlenet_candidates(root):
        try:
            if candidate.is_file():
                battlenet = candidate
                break
        except OSError:
            continue
    result.update({"ok": True, "log_root": root, "exe": exe,
                   "exe_found": exe.is_file(),
                   "battlenet_exe": battlenet,
                   "battlenet_found": battlenet is not None})
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


def _start_with_launcher(launcher) -> Optional[str]:
    """走战网入口（脚本平时就是这么拉炉石）。返回 None 成功，否则错误文字。"""
    try:
        result = launcher()
    except Exception as exc:
        return f"调用战网入口失败：{exc}"
    if result is False:
        return "没找到战网窗口，无法从战网启动"
    return None


def _start_with_exe(exe, opener) -> Optional[str]:
    try:
        opener(str(exe))
    except Exception as exc:
        return f"启动 {EXE_NAME} 失败：{exc}"
    return None


def _alive_for(probe, sleeper, seconds) -> bool:
    """进程是否稳定存活这么多秒。"""
    deadline = time.monotonic() + max(0.0, float(seconds))
    while time.monotonic() < deadline:
        if _probe(probe) is False:
            return False
        sleeper(POLL_INTERVAL)
    return True


def _wait_until_ready(probe, window_ready, sleeper, timeout, settle,
                      retry: Optional[Callable] = None,
                      retry_after: float = LAUNCH_RETRY_AFTER,
                      announce_retry: Optional[Callable[[int, float], None]] = None
                      ) -> tuple[bool, str]:
    """等炉石真的起来，返回 (是否成功, 失败原因)。

    判据优先级：**游戏窗口出现** > 进程稳定存活 settle 秒。
    只看“进程存在”是不够的：Hearthstone.exe 直启时进程会瞬间出现又退出，
    那时候报“已启动”，用户看到的就是“重启一半又关了”。所以进程起来过又
    马上消失会**立刻**判失败，不再干等到超时。

    ``retry`` 是等待期间定期重试的动作（例如再点一次战网的「开始游戏」）；
    ``announce_retry`` 用来在浮窗里播报“还差多久重试”（进度条 + 文字）。
    """
    deadline = time.monotonic() + max(0.0, float(timeout))
    appeared = False
    interval = max(1.0, float(retry_after))
    retry_count = 0
    next_retry = time.monotonic() + interval
    if retry is not None and announce_retry is not None:
        # 点完第一下就先说清楚“多久没反应会再点一次”。
        announce_retry(retry_count + 1, interval)
    while time.monotonic() < deadline:
        if window_ready is not None:
            try:
                if window_ready():
                    return True, ""
            except Exception:
                pass
        state = _probe(probe)
        if state is True:
            appeared = True
            if window_ready is None and _alive_for(probe, sleeper, settle):
                return True, ""
        elif state is False and appeared:
            return False, "进程起来后马上又退出了（炉石没能被正常拉起）"
        if window_ready is None and appeared:
            return False, f"进程存活不到 {float(settle):.0f}s 就退出了"
        if retry is not None and time.monotonic() >= next_retry:
            retry_count += 1
            next_retry = time.monotonic() + interval
            try:
                retry()
            except Exception:
                pass
            # 播报下一轮倒计时（时间不够就不再播了，免得进度条空转）。
            if (announce_retry is not None
                    and time.monotonic() + interval < deadline):
                announce_retry(retry_count + 1, interval)
        sleeper(POLL_INTERVAL)
    if appeared:
        return False, f"{float(timeout):.0f}s 内没等到炉石游戏窗口"
    return False, f"{float(timeout):.0f}s 内没看到 {EXE_NAME} 进程"


def _wait_true(predicate, sleeper, timeout) -> bool:
    """等某个判断变真（例如“战网窗口出现了”）。"""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while time.monotonic() < deadline:
        try:
            if predicate():
                return True
        except Exception:
            pass
        sleeper(POLL_INTERVAL)
    return False


def launch_hearthstone(exe=None, battlenet_exe=None,
                       launcher: Optional[Callable] = None,
                       battlenet_ready: Optional[Callable] = None,
                       opener: Optional[Callable] = None,
                       process_running: Optional[Callable] = None,
                       window_ready: Optional[Callable] = None,
                       sleeper: Callable[[float], None] = time.sleep,
                       timeout: float = LAUNCH_TIMEOUT,
                       battlenet_timeout: float = BATTLENET_TIMEOUT,
                       before_click_delay: float = BEFORE_CLICK_DELAY,
                       retry_after: float = LAUNCH_RETRY_AFTER,
                       settle: float = LAUNCH_SETTLE,
                       logger: Optional[Callable[[str], None]] = None,
                       wait: bool = True) -> dict:
    """重新拉起炉石：**优先走战网「开始游戏」**（与脚本平时拉起炉石的方式一致），
    只有在连战网都用不上时才直接启动 Hearthstone.exe。

    链路：战网没开 → 先启动 ``Battle.net Launcher.exe`` → 等 ``battlenet_ready``
    为真 → **延时 ``before_click_delay`` 秒**（战网按钮画好之前点会点空）→
    调 ``launcher``（click.enter_HS，点「开始游戏」）→ 等 ``window_ready``
    （游戏窗口出现），等窗口期间每 ``LAUNCH_RETRY_AFTER`` 秒重试点一次，
    每次重试前也会播报倒计时。

    ``window_ready`` 才是启动成功的判据，光有进程不算：直启 Hearthstone.exe 时
    进程会瞬间出现又退出，只看进程就会报“已启动”，用户看到的是“重启一半又关了”。
    两种方式都失败时返回 ``ok=False`` 并带明确原因。

    进度用 ``logger`` 播报；其中“延时 X s 后……”这种行会驱动浮窗底部的延时
    进度条（log_overlay 解析「延时 X s 后」），所以延时与重试时间在浮窗里看得见。
    """
    open_path = opener or _default_opener
    probe = process_running or _process_running

    attempts: list[tuple[str, object]] = []
    if launcher is not None:
        attempts.append(("launcher", launcher))
    if exe is not None and Path(exe).is_file():
        attempts.append(("exe", exe))
    if not attempts:
        return {"ok": False, "started": None, "running": None,
                "error": f"找不到 {EXE_NAME}，也没有可用的战网入口"}

    def note(text):
        if logger is not None:
            try:
                logger(text)
            except Exception:
                pass

    def delay(seconds, what):
        """播报并等待一段延时：这行会驱动浮窗底部的进度条。"""
        seconds = max(0.0, float(seconds))
        if seconds <= 0:
            return
        note(f"延时 {seconds:.1f}s 后{what}")
        sleeper(seconds)
        note("延时结束")

    def _ensure_battlenet() -> Optional[str]:
        """战网没开就先把它拉起来；返回 None 表示“战网可用”。"""
        if battlenet_ready is not None:
            try:
                if battlenet_ready():
                    return None
            except Exception:
                pass
        if battlenet_exe is None or not Path(battlenet_exe).is_file():
            return "战网没有运行，也找不到 Battle.net Launcher.exe"
        note("战网没有运行，先启动战网（炉石要靠它才有登录态）……")
        problem = _start_with_exe(battlenet_exe, open_path)
        if problem is not None:
            return problem
        if battlenet_ready is None:
            delay(BATTLENET_WARMUP, "等战网把「开始游戏」按钮画出来")
            return None
        if not _wait_true(battlenet_ready, sleeper, battlenet_timeout):
            return f"{float(battlenet_timeout):.0f}s 内等待战网窗口出现超时"
        delay(BATTLENET_WARMUP, "等战网把「开始游戏」按钮画出来")
        return None

    errors = []
    for kind, target in attempts:
        label = "战网「开始游戏」" if kind == "launcher" else f"直接启动 {EXE_NAME}"
        if kind == "launcher":
            problem = _ensure_battlenet()
            if problem is not None:
                errors.append(f"{label}：{problem}")
                note(f"{label}不可用：{problem}")
                continue
            # 战网刚起来/刚切到前台时按钮还没画好，点太快会点空。
            delay(before_click_delay, "点战网「开始游戏」")
        note(f"正在通过{label}拉起炉石……")
        if kind == "launcher":
            problem = _start_with_launcher(target)
        else:
            problem = _start_with_exe(target, open_path)
        if problem is not None:
            errors.append(f"{label}：{problem}")
            note(f"{label}失败：{problem}")
            continue
        if not wait:
            return {"ok": True, "started": kind, "running": None,
                    "message": f"已通过{label}发出启动命令"}

        if kind == "launcher":
            note(f"等待炉石游戏窗口出现：最多 {float(timeout):.0f}s，"
                 f"每 {float(retry_after):.0f}s 重试点一次「开始游戏」")
            retry = (lambda: _start_with_launcher(target))

            def announce_retry(count, seconds):
                note(f"延时 {float(seconds):.1f}s 后重试点「开始游戏」"
                     f"（第 {count} 次）")

            ok, reason = _wait_until_ready(probe, window_ready, sleeper, timeout,
                                           settle, retry=retry,
                                           retry_after=retry_after,
                                           announce_retry=announce_retry)
            note("延时结束")        # 收掉可能还在跑的倒计时进度条
        else:
            ok, reason = _wait_until_ready(probe, window_ready, sleeper, timeout,
                                           settle)
        if ok:
            return {"ok": True, "started": kind, "running": True,
                    "message": f"炉石已启动（{label}）"}
        errors.append(f"{label}：{reason}")
        note(f"{label}没起来：{reason}")

    return {"ok": False, "started": None, "running": False,
            "error": "；".join(errors)}
