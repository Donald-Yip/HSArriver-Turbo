# -*- coding: utf-8 -*-
"""中断重启看门狗：日志长时间不变 → 重启炉石并重新开始。

和 `hearthstone_liveness` 的分工
------------------------------------------------------------------
两者都在看 ``Power.log`` 的 mtime，但**处置方式不同**：

* ``hearthstone_liveness``：判定"无响应"后**停止**自动化并醒目告警，等人来处理。
  适合有人在电脑前、希望"出事别乱动"的场景。
* ``RestartWatchdog``（本模块）：判定"卡住"后**主动重启炉石并把这一局重开**，
  自己爬回来。适合无人值守通宵挂机。

同一台机器上两者看的是同一个信号，所以**规则是"看门狗接管"**：看门狗开关
打开时，由它处理"日志停滞"，`hearthstone_liveness` 那条自动停止不再触发
（接线见 ``FSM_action.check_hearthstone_liveness``）。开关关闭时行为与从前
完全一致，零回归。

判定条件（三个都要满足才重启）
------------------------------------------------------------------
1. 开关 ``enabled`` 为真（默认**关**，要用户显式打开）；
2. **自动化正在运行**（没在跑的时候日志安静是正常的，不重启）；
3. ``Power.log`` 停滞 ``>= stale_seconds``（默认 300s = 5 分钟）。

限流（防无限重启）
------------------------------------------------------------------
滑动窗口 ``window_seconds``（默认 10 分钟）内最多重启 ``max_restarts``
（默认 3）次。超限不再重启，转为"停止并告警"—— 反复重启说明这不是一两次
抖动，继续重启只会烧资源、也可能触发游戏风控。

所有外部依赖（配置、日志年龄、自动化状态、时钟、重启动作）都可注入，
判定逻辑用纯 Python 单测覆盖，不依赖本机是否真跑着炉石。
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

# 自动化"在跑"的判定：默认问 web_ui 的 CTRL（唯一权威来源）。
def _default_automation_running() -> bool:
    try:
        import web_ui
        with web_ui.CTRL.lock:
            return web_ui.CTRL.automation_thread is not None
    except Exception:
        return False


def _default_log_age() -> Optional[float]:
    """最新 Power.log 距今多少秒没有更新；拿不到返回 None。"""
    try:
        from src.safety.hearthstone_liveness import default_monitor
        return default_monitor().log_age()
    except Exception:
        return None


def _default_settings() -> dict:
    try:
        from config import watchdog_settings
        return watchdog_settings()
    except Exception:
        return {"enabled": False, "stale_seconds": 300.0, "max_restarts": 3,
                "window_seconds": 600.0, "check_seconds": 15.0,
                "settle_seconds": 120.0}


def _default_clock() -> float:
    return time.monotonic()


class RestartWatchdog:
    """日志停滞看门狗（线程安全；重启动作异步执行，不阻塞调用方）。

    用法：``watchdog.poll()`` 在状态机主循环里定期调用（内部自带节流，
    不会每次都真的判定）；返回需要展示/记录的事件或 None。

    ``poll()`` 只做判定与**触发**，真正的重启交给 `restarter`（异步），
    因为重启要跑几十秒，绝不能在主循环里同步等。
    """

    def __init__(self,
                 settings_provider: Optional[Callable[[], dict]] = None,
                 log_age_provider: Optional[Callable[[], Optional[float]]] = None,
                 automation_running: Optional[Callable[[], bool]] = None,
                 restarter: Optional[Callable[[dict], bool]] = None,
                 clock: Optional[Callable[[], float]] = None,
                 sleep: Optional[Callable[[float], None]] = None):
        self._settings_provider = settings_provider or _default_settings
        self._log_age_provider = log_age_provider or _default_log_age
        self._automation_running = automation_running or _default_automation_running
        self._restarter = restarter
        self._clock = clock or _default_clock
        self._sleep = sleep or time.sleep

        self._lock = threading.RLock()
        # 滑动窗口里的重启时刻（monotonic）。
        self._restart_times: list[float] = []
        # 重启后给日志"重新写起来"的宽限期截止时刻；在此之前不判定。
        self._settle_until: float = 0.0
        # 上次真正判定/重启的时刻，用于节流。
        self._last_check: float = -1e9
        # 已上报过的告警 kind（去重，避免刷屏）。
        self._reported: set[str] = set()
        # 最近一次事件文案，供网页/浮窗显示。
        self._last_event: Optional[str] = None
        # 累计重启次数（不随窗口滑出而清零，仅供展示）。
        self._total_restarts: int = 0
        self._last_restart_at: Optional[float] = None
        self._exhausted: bool = False

    # ------------------------------------------------------------ 状态查询
    def settings(self) -> dict:
        try:
            cfg = dict(self._settings_provider() or {})
        except Exception:
            cfg = dict(_default_settings())
        merged = dict(_default_settings())
        merged.update(cfg)
        return merged

    def restarts_in_window(self) -> int:
        """当前滑动窗口内已重启次数（会顺手剔除过期的记录）。"""
        cfg = self.settings()
        window = float(cfg.get("window_seconds", 600.0))
        now = self._clock()
        with self._lock:
            self._restart_times = [t for t in self._restart_times
                                   if now - t < window]
            return len(self._restart_times)

    def reset(self) -> None:
        """新一轮自动化开始时调用：忘记上一轮的限流计数与告警。"""
        with self._lock:
            self._restart_times.clear()
            self._settle_until = 0.0
            self._last_check = -1e9
            self._reported.clear()
            self._last_event = None
            self._total_restarts = 0
            self._last_restart_at = None
            self._exhausted = False
    def note_restarted(self) -> None:
        """外部（手动重启炉石）成功后调用：重新开始计时，避免马上又判停滞。"""
        cfg = self.settings()
        settle = float(cfg.get("settle_seconds", 120.0))
        with self._lock:
            self._restart_times.append(self._clock())
            self._total_restarts += 1
            self._last_restart_at = time.time()
            self._settle_until = self._clock() + settle
            self._reported.discard("restart")

    def state(self) -> dict:
        """给网页/浮窗/CLI 的状态快照。"""
        cfg = self.settings()
        now = self._clock()
        with self._lock:
            settle_left = max(0.0, self._settle_until - now)
            exhausted = self._exhausted
            last_event = self._last_event
            total = self._total_restarts
            last_at = self._last_restart_at
        running = False
        try:
            running = bool(self._automation_running())
        except Exception:
            running = False
        age = None
        try:
            age = self._log_age_provider()
        except Exception:
            age = None
        in_window = self.restarts_in_window()
        limit = int(cfg.get("max_restarts", 3))

        if not cfg.get("enabled", False):
            status = "disabled"
        elif exhausted:
            status = "exhausted"
        elif settle_left > 0:
            status = "settling"
        elif not running:
            status = "idle"
        elif age is None:
            status = "unknown"
        elif age >= float(cfg.get("stale_seconds", 300.0)):
            status = "stale"
        else:
            status = "ok"

        return {
            "enabled": bool(cfg.get("enabled", False)),
            "status": status,
            "stale_seconds": float(cfg.get("stale_seconds", 300.0)),
            "max_restarts": limit,
            "window_seconds": float(cfg.get("window_seconds", 600.0)),
            "check_seconds": float(cfg.get("check_seconds", 15.0)),
            "settle_seconds": float(cfg.get("settle_seconds", 120.0)),
            "restarts_in_window": in_window,
            "restarts_total": total,
            "restarts_left": max(0, limit - in_window),
            "last_restart_at": last_at,
            "settle_left": settle_left,
            "log_age": age,
            "automation_running": running,
            "message": last_event,
        }

    # ------------------------------------------------------------ 判定
    def _event(self, kind: str, message: str) -> Optional[dict]:
        """按 kind 去重：同一种情况只上报一次，避免主循环刷屏。"""
        with self._lock:
            if kind in self._reported:
                return None
            self._reported.add(kind)
            self._last_event = message
        return {"kind": kind, "message": message,
                "restart": kind == "restart"}

    def _trigger_restart(self, age: float, cfg: dict) -> Optional[dict]:
        """触发一次重启（异步）。返回事件；调用方负责把事件写日志。

        先**记账再派发**：失败的重启同样在烧资源（尝试杀进程/拉客户端），
        所以也要占掉配额，否则一个坏掉的重启通道会被每 15 秒重试一次，永远刷。
        """
        settle = float(cfg.get("settle_seconds", 120.0))
        with self._lock:
            self._exhausted = False
            self._restart_times.append(self._clock())
            self._total_restarts += 1
            self._last_restart_at = time.time()
            self._settle_until = self._clock() + settle
            self._reported.discard("stale")
            self._reported.discard("restart")
            self._reported.discard("restart_failed")
            count = len(self._restart_times)
            limit = int(cfg.get("max_restarts", 3))

        try:
            started = (bool(self._restarter(cfg))
                       if self._restarter is not None else False)
        except Exception:
            started = False

        if not started:
            return self._event(
                "restart_failed",
                f"日志已停滞 {age:.0f}s，尝试重启炉石但**触发失败**"
                f"（没有可用的重启通道，或上一次重启还没结束），请手动处理。"
                f"本次已计入配额（{count}/{limit}）。")

        message = (f"日志已停滞 {age:.0f}s（≥ {cfg.get('stale_seconds'):.0f}s），"
                   f"已触发第 {count}/{limit} 次重启炉石："
                   f"重启完成后会自动重新进入对战。")
        with self._lock:
            self._last_event = message
        return {"kind": "restart", "message": message, "restart": True}

    def poll(self) -> Optional[dict]:
        """判定一次（内部按 ``check_seconds`` 节流）；返回事件或 None。

        事件形如 ``{"kind": "restart"|"exhausted"|"stale"|..., "message": str}``。
        """
        cfg = self.settings()
        if not cfg.get("enabled", False):
            return None

        now = self._clock()
        interval = float(cfg.get("check_seconds", 15.0))
        # 宽限期（刚重启完，日志还没来得及写）独立于节流：只要还没到点就直接
        # 返回，**并且不推进 `_last_check`** —— 否则宽限期结束后的第一次判定
        # 会因为"距上次判定还不到 check_seconds"被白等一个间隔。
        with self._lock:
            if now < self._settle_until:
                return None
            if now - self._last_check < interval:
                return None
            self._last_check = now

        try:
            running = bool(self._automation_running())
        except Exception:
            running = False
        if not running:
            return None

        try:
            age = self._log_age_provider()
        except Exception:
            age = None
        # 没有日志文件时**不判定**：可能是还没打第一局，或日志目录没配好，
        # 当成"卡住"去重启只会白折腾。
        if age is None:
            return None

        stale_at = float(cfg.get("stale_seconds", 300.0))
        if age < stale_at:
            with self._lock:
                self._reported.discard("stale")
                self._reported.discard("exhausted")
                if self._exhausted:
                    self._exhausted = False
            return None

        # 到了这里=确认卡住。先看限流。
        limit = int(cfg.get("max_restarts", 3))
        in_window = self.restarts_in_window()
        if in_window >= limit:
            with self._lock:
                self._exhausted = True
            window = float(cfg.get("window_seconds", 600.0))
            return self._event(
                "exhausted",
                f"日志已停滞 {age:.0f}s，但 {window / 60:.0f} 分钟内已重启 "
                f"{in_window} 次（达到上限 {limit}），不再重启以免反复折腾。"
                f"已停止等待人工处理。")

        return self._trigger_restart(age, cfg)


_DEFAULT_WATCHDOG: Optional[RestartWatchdog] = None
_WATCHDOG_LOCK = threading.Lock()


def default_watchdog() -> RestartWatchdog:
    """进程内共享的看门狗（状态机与 Web 界面看到的是同一份判定）。"""
    global _DEFAULT_WATCHDOG
    with _WATCHDOG_LOCK:
        if _DEFAULT_WATCHDOG is None:
            _DEFAULT_WATCHDOG = RestartWatchdog()
        return _DEFAULT_WATCHDOG
