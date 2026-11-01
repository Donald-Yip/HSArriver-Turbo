# -*- coding: utf-8 -*-
"""守护监督器（原「常驻模式」）：无人值守时把炉石传说 / 自动化自己拉回来。

2026-11-01 三卡合并：原来的「🩺 存活检测」「🔄 中断重启」「🛰️ 常驻模式」看的是
同一批信号（进程还在不在、Power.log 有没有更新、自动化是否停摆），却各自带一套
阈值/计数器，互相打架。现在统一由本模块判定：

* 炉石进程消失 → 重启炉石并恢复；
* 自动化停摆 → 重新启动自动化（必要时先装配进对战）；
* 日志停滞（对局中 / 其它界面两档）→ 重启炉石 → 重进对战 → 重新开局；
* 统一滑动窗口配额、统一提前告警（``warn_seconds``）、统一手动停止语义；
* 本模块**不要求自动化已经在跑**——它自己负责调 ``start_automation()`` 把自动化
  拉起来，所以不能复用"必须自动化运行中才判定"的旧看门狗门控。
* 守护关闭/暂停时，炉石进程消失与对局中日志停滞由
  ``FSM_action.check_hearthstone_liveness`` 走最小安全网（告警 + 停止自动化）。

定时窗口挂起（``hold_reason``）
------------------------------------------------------------------
构造时可注入 ``hold_reason`` 回调（返回 ``str | None``）。``poll_once`` 在
开关检查之后、任何动作判定之前调用它：非空表示"定时窗口还没开始"，监督器
进入 ``waiting_schedule`` 待机——不派发恢复/启动、不占配额、不改 settle；
同一 reason 只记一次事件（reason 变化时重新记录）。回调恢复为空后立刻回到
``running`` 并继续正常判定（下一次判定不等 ``check_seconds``）。回调抛异常
按"不挂起"处理。用户 ``pause()`` / 硬 ``stop()`` / 配额耗尽 / 错误等状态优先，
不会被挂起回调覆盖。

判定顺序（每次 ``poll_once``）
------------------------------------------------------------------
1. 开关关闭 / 已禁用 / 已取消 / 出错 / 暂停 / 配额耗尽 / 恢复中 → 不判定；
1.5 ``hold_reason`` 非空 → 进入/保持 ``waiting_schedule``，不判定也不动作；
2. 倒计时（``start_delay_seconds``）没到 → 不判定；
3. 宽限期（``settle_seconds``）内 → 不判定；
4. 自动化被外部停止（``stopped_by`` 非空）→ 转 ``paused``，不自动拉起；
5. 炉石进程消失连续 ≥ 6s → 触发恢复（``reason="process_gone"``）；
6. 自动化没在跑 → 调 ``start_automation()``（10 分钟内最多 3 次，超限告警+error）；
7. 对局中 / 非对局界面分别用 ``stale_in_game_seconds`` / ``stale_other_seconds``
   判定日志停滞，达到阈值 → 触发恢复（``reason="log_stale"``）；
   达到 ``warn_seconds``（0=关）但未到阈值 → 只报一次 ``stale_warning`` 事件，
   不动作、不占配额（原「存活检测」的提前告警档并入此处）。

限流（防无限重启）
------------------------------------------------------------------
滑动窗口 ``window_seconds`` 内最多恢复 ``max_restarts`` 次；**先记账再派发**，
派发失败同样占配额（失败的重启也在烧资源，不能让坏通道被无限重试）。
超限后进入 ``paused_exhausted`` 并告警，等待人工处理（不退出控制台）。

``dry_run``
------------------------------------------------------------------
只做判定并返回事件（kind 以 ``would_`` 开头），不调用 ``recover`` /
``start_automation``，不占配额、不改状态、不告警。

可测性
------------------------------------------------------------------
本模块**模块级只 import 标准库**，不 import web_ui / FSM_action /
manual_controller / click / config；配置、日志年龄、进程/自动化状态、
恢复动作、告警、时钟、sleep 全部由构造注入（见 ``__init__``），
因此可以在没有 Windows / 炉石的环境里用假时钟单测。
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

# ---------------------------------------------------------------- 固定阈值
# 炉石进程消失后连续这么多秒都没回来才动作（防重启/更新时的瞬时抖动）。
PROCESS_GONE_GRACE_SECONDS = 6.0
# 自动化"拉起"请求的限流：10 分钟内最多 3 次，超限转 error 等人工。
START_RATE_MAX = 3
START_RATE_WINDOW_SECONDS = 600.0
# 后台循环的 sleep 切片（秒）：每 1s 一个切片，停止响应不超过约 1s。
THREAD_SLEEP_SECONDS = 1.0
# 循环内异常告警的限流间隔（秒），避免异常风暴刷屏。
ALERT_MIN_INTERVAL_SECONDS = 60.0
# 允许"拉起自动化"的活动状态；其余（paused/paused_exhausted/error/disabled/
# cancelled）说明用户或限流已叫停，判定与派发之间即使状态变了也不能再派发。
AUTOMATION_STARTABLE_STATES = ("countdown", "starting", "running")

# 兜底配置：真正的默认值在 config.DEFAULT_RESIDENT。这里放一份副本只是让
# 监督器在"注入的 settings 缺字段"时仍能工作（也便于脱离 config 单测）。
_FALLBACK_SETTINGS = {
    "enabled": False,
    "mode": "standard",
    "deck_index": None,
    "stale_in_game_seconds": 180.0,
    "stale_other_seconds": 300.0,
    "warn_seconds": 120.0,
    "max_restarts": 3,
    "window_seconds": 600.0,
    "check_seconds": 5.0,
    "settle_seconds": 120.0,
    "start_delay_seconds": 5.0,
    "dry_run": False,
}


class ResidentSupervisor:
    """常驻监督器（线程安全；恢复动作异步派发，不阻塞判定）。

    状态机
    ------------------------------------------------------------------
    ``disabled``          开关关闭 / 被 ``disable()`` 强制关闭
    ``countdown``         开启后倒计时（``start_delay_seconds``）
    ``waiting_schedule``  定时窗口未开始（``hold_reason`` 非空），常驻待机
    ``starting``          倒计时结束，正在等自动化真正跑起来
    ``running``           正常监督中
    ``recovering``        已派发恢复，等外部 ``note_recovered()`` 收尾
    ``paused``            用户暂停（或外部停止自动化后自动暂停）
    ``paused_exhausted``  滑动窗口配额耗尽，停止自动恢复等人工
    ``error``             自动化连续拉不起来 / 恢复失败，停止等人工
    ``cancelled``         被 ``stop()`` 硬取消

    主要用法
    ------------------------------------------------------------------
    * ``start()``：启动后台 daemon 线程（幂等）；配置 enabled 时进入倒计时。
    * ``poll_once()``：判定一次，返回事件 dict 或 None（线程里循环调用）。
    * ``note_recovered(ok)``：外部恢复流程结束时回调，收尾 ``recovering``。
    * ``note_automation_started()``：外部（手动/任意来源）刚启动自动化时回调，
      开启 ``settle_seconds`` 宽限期，让暂停/空闲期间累积的旧日志年龄不参与
      卡死判定（不占配额、不记事件、不改状态）。
    * ``pause()`` / ``request_pause()``：手动暂停；恢复中只记待生效。
    * ``resume()``：从暂停/暂停耗尽/错误/取消恢复监督（重新倒计时）。
    * ``disable()``：关闭常驻（不再判定），线程保持存活等下次开启。
    * ``state()``：给网页/浮窗/CLI 的状态快照（JSON 友好）。
    """

    def __init__(self,
                 *,
                 settings,
                 log_age,
                 in_game,
                 process_running,
                 automation_running,
                 stopped_by,
                 start_automation,
                 recover,
                 alert,
                 clock=time.monotonic,
                 sleep=time.sleep,
                 hold_reason: Optional[Callable[[], Optional[str]]] = None,
                 warn: Optional[Callable[[str], None]] = None):
        # 注入依赖（全部可替换成假实现，便于单测）。
        self._settings = settings
        self._log_age = log_age
        self._in_game = in_game
        self._process_running = process_running
        self._automation_running = automation_running
        self._stopped_by = stopped_by
        self._start_automation = start_automation
        self._recover = recover
        self._alert_cb = alert
        self._warn_cb = warn
        self._clock = clock
        self._sleep = sleep
        # 定时窗口挂起回调：返回非空字符串表示"窗口还没开始"，常驻待机。
        self._hold_reason = hold_reason

        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # ------------------------------------------------------ 运行态
        self._state = "disabled"
        self._forced_disabled = False
        self._cancelled = False
        self._countdown_until = 0.0
        self._settle_until = 0.0
        self._last_check = -1e9
        # 滑动窗口内的恢复时刻 / 自动化启动请求时刻（都是 self._clock()）。
        self._restart_times: list = []
        self._start_times: list = []
        self._total_restarts = 0
        self._process_gone_since: Optional[float] = None
        # 本轮停滞是否已经报过「提前告警」（每次停滞 episode 只报一次）。
        self._stale_warned = False
        # 恢复中收到暂停请求：等 note_recovered() 收尾后再生效。
        self._pause_after_recover = False
        self._paused_reason: Optional[str] = None
        # 定时窗口挂起原因（仅 state == "waiting_schedule" 时非 None），用于去重。
        self._waiting_reason: Optional[str] = None
        # 最近一次事件（kind + 人话），供 state()/网页/浮窗展示。
        self._last_event: Optional[str] = None
        self._last_message: Optional[str] = None
        self._last_alert_at = -1e9

        # 配置为 enabled 时，构造即进入倒计时（开机后即生效）。
        cfg = self._cfg()
        if cfg["enabled"]:
            with self._lock:
                self._enter_countdown_locked(self._clock(), cfg)

    # ------------------------------------------------------------ 配置读取
    def _cfg(self) -> dict:
        """读取注入的配置并补默认值/做基本收敛。

        正常情况下注入的是 ``config.resident_settings``（已归一化）；这里再兜
        一层，是为了让部分字段缺失的假配置（单测）不会把判定逻辑带崩。
        """
        try:
            raw = self._settings() if callable(self._settings) else self._settings
        except Exception:
            raw = {}
        cfg = dict(_FALLBACK_SETTINGS)
        if isinstance(raw, dict):
            for key in cfg:
                if raw.get(key) is not None:
                    cfg[key] = raw[key]
        try:
            cfg["enabled"] = bool(cfg["enabled"])
            cfg["mode"] = str(cfg["mode"])
            deck = cfg["deck_index"]
            cfg["deck_index"] = (int(deck)
                                 if deck is not None and 1 <= int(deck) <= 9 else None)
            cfg["stale_in_game_seconds"] = float(cfg["stale_in_game_seconds"])
            cfg["stale_other_seconds"] = float(cfg["stale_other_seconds"])
            # 提前告警：0 = 关闭；否则夹在 (0, 对局中阈值] 内才有意义。
            cfg["warn_seconds"] = max(0.0, float(cfg["warn_seconds"]))
            cfg["max_restarts"] = max(1, int(cfg["max_restarts"]))
            cfg["window_seconds"] = float(cfg["window_seconds"])
            # 单测会传 0 让节流失效；真实配置由 config.resident_settings 收敛。
            cfg["check_seconds"] = max(0.0, float(cfg["check_seconds"]))
            cfg["settle_seconds"] = max(0.0, float(cfg["settle_seconds"]))
            cfg["start_delay_seconds"] = max(0.0, float(cfg["start_delay_seconds"]))
            cfg["dry_run"] = bool(cfg["dry_run"])
        except (TypeError, ValueError):
            return dict(_FALLBACK_SETTINGS)
        # 非对局界面的日志阈值不能比对局中还小；窗口不能短于停滞阈值。
        if cfg["stale_other_seconds"] < cfg["stale_in_game_seconds"]:
            cfg["stale_other_seconds"] = cfg["stale_in_game_seconds"]
        if cfg["warn_seconds"] > cfg["stale_in_game_seconds"]:
            cfg["warn_seconds"] = cfg["stale_in_game_seconds"]
        if cfg["window_seconds"] < cfg["stale_other_seconds"]:
            cfg["window_seconds"] = cfg["stale_other_seconds"]
        return cfg

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        """启动后台 daemon 线程（幂等）；配置 enabled 时进入倒计时。

        若已在倒计时/监督/恢复中，重复调用不会重置倒计时（避免把用户刚
        开的 5s 等待无限续期）。
        """
        self._ensure_thread()
        cfg = self._cfg()
        with self._lock:
            self._forced_disabled = False
            self._cancelled = False
            if not cfg["enabled"]:
                if self._state not in ("countdown", "starting", "running", "recovering"):
                    self._state = "disabled"
                    self._waiting_reason = None
                return
            if self._state in ("countdown", "starting", "running", "recovering"):
                return
            self._paused_reason = None
            self._pause_after_recover = False
            self._enter_countdown_locked(self._clock(), cfg)

    def stop(self) -> None:
        """硬取消：停止线程、进入 cancelled（再开需 resume()/start()）。"""
        with self._lock:
            self._cancelled = True
            self._state = "cancelled"
            self._paused_reason = None
            self._pause_after_recover = False
            self._waiting_reason = None
            self._stop_event.set()
            thread = self._thread
            self._thread = None
        if (thread is not None and thread.is_alive()
                and thread is not threading.current_thread()):
            thread.join(timeout=2.0)

    def disable(self) -> None:
        """关闭常驻：不再判定（线程保持存活，配置重新 enabled 后可再开）。"""
        with self._lock:
            self._forced_disabled = True
            self._state = "disabled"
            self._countdown_until = 0.0
            self._paused_reason = None
            self._pause_after_recover = False
            self._waiting_reason = None

    def resume(self) -> None:
        """从暂停/暂停耗尽/错误/取消/定时窗口待机回到监督（重新走倒计时）。"""
        self._ensure_thread()
        cfg = self._cfg()
        now = self._clock()
        with self._lock:
            self._forced_disabled = False
            self._cancelled = False
            self._paused_reason = None
            self._pause_after_recover = False
            if not cfg["enabled"]:
                self._state = "disabled"
                self._waiting_reason = None
                return
            if self._state in ("countdown", "starting", "running", "recovering"):
                return
            self._enter_countdown_locked(now, cfg)

    def pause(self, reason: str = "paused") -> None:
        """手动暂停。

        ``recovering`` 中只记 ``pause_after_recover``（等恢复流程收尾后才真正
        暂停，否则会把正在跑的恢复动作晾在半路）；其他活动状态立即转 paused。
        """
        reason_text = str(reason) if reason else "paused"
        with self._lock:
            if self._state == "recovering":
                self._pause_after_recover = True
                self._paused_reason = reason_text
                self._record_event_locked(
                    "pause_requested",
                    f"常驻收到暂停请求（{reason_text}），本次恢复结束后生效。")
                return
            if self._state not in ("countdown", "starting", "running",
                                   "waiting_schedule", "paused", "disabled"):
                return
            self._state = "paused"
            self._countdown_until = 0.0
            self._paused_reason = reason_text
            self._waiting_reason = None
            self._record_event_locked("paused", f"常驻已暂停：{reason_text}。")

    def request_pause(self, reason: str = "paused") -> None:
        """外部（用户停止/取消）请求暂停。

        语义与 ``pause()`` 一致；单独保留这个方法是为了让调用点表达"请求"
        而非"已经暂停"，后续若引入协作式延迟生效也不影响接口。
        """
        self.pause(reason)

    def note_recovered(self, ok: bool, message: Optional[str] = None) -> None:
        """外部恢复流程结束回调：``ok`` 决定回到 running 还是 error。

        成功时开启 ``settle_seconds`` 宽限期（给日志重新写起来），并消费
        ``pause_after_recover`` —— 恢复期间用户按下的暂停在这里才真正生效。

        ``message`` 可选：装配层可以传入带「本次运行第 N 次恢复成功」的文案，
        会原样记进 ``last_event``/``state.message``，浮窗「最近事件」与日志直接显示。
        """
        cfg = self._cfg()
        now = self._clock()
        with self._lock:
            if ok:
                self._state = "running"
                self._settle_until = now + float(cfg["settle_seconds"])
                self._last_check = -1e9
                self._process_gone_since = None
                self._stale_warned = False
                text = str(message) if message else "常驻自动恢复完成，重新开始监督。"
                self._record_event_locked("recovered", text)
                if self._pause_after_recover:
                    self._pause_after_recover = False
                    reason = self._paused_reason or "user_pause"
                    self._state = "paused"
                    self._record_event_locked("paused", f"常驻已暂停：{reason}。")
                else:
                    self._paused_reason = None
            else:
                self._state = "error"
                self._pause_after_recover = False
                self._record_event_locked(
                    "recover_failed",
                    "常驻自动恢复失败，已停止自愈并告警；请处理后用 "
                    "hsctl resident on 或浮窗「开启常驻」重新恢复。")

    def note_automation_started(self) -> None:
        """外部刚刚（重新）启动了自动化：进入 ``settle_seconds`` 宽限期。

        暂停/空闲期间 Power.log 会攒下很大的"日志年龄"，手动点「开始对战」
        后日志要过一会儿才重新写起来；若不进宽限期，常驻第一次 poll 就会
        把这段旧年龄当成"日志卡死"并立刻接管/装配。这里只压宽限期与判定
        节流，**不重置配额、不记事件、不改状态**（避免刷屏，也避免覆盖用户
        刚设好的暂停/错误状态）。
        """
        cfg = self._cfg()
        now = self._clock()
        with self._lock:
            self._settle_until = now + float(cfg["settle_seconds"])
            self._last_check = -1e9
            self._process_gone_since = None
            self._stale_warned = False

    # ------------------------------------------------------------ 判定
    def poll_once(self) -> Optional[dict]:
        """判定一次；返回需要展示/记录的事件 dict，或 None。

        事件形如 ``{"kind": "restart"|"restart_failed"|"quota_exhausted"|
        "automation_started"|"start_failed"|"paused"|"countdown_done"|
        "waiting_schedule"|"recovered"|"error"|"would_*", "reason": str|None,
        "message": str}``。
        """
        cfg = self._cfg()
        if not cfg["enabled"]:
            with self._lock:
                if self._state != "disabled":
                    self._state = "disabled"
                    self._countdown_until = 0.0
                    self._paused_reason = None
                self._waiting_reason = None
            return None

        now = self._clock()
        # 定时窗口未开始：常驻待机，不派发任何动作、不占配额、不改 settle。
        hold_reason = self._current_hold_reason()
        if hold_reason is not None:
            return self._wait_for_schedule(cfg, hold_reason)

        countdown_finished = False
        with self._lock:
            if self._forced_disabled or self._cancelled or self._state == "cancelled":
                return None
            if self._state == "waiting_schedule":
                # 定时窗口开始：退出待机，回到 running 并立即判定（不等
                # check_seconds），记录一次恢复事件。settle/配额保持不变。
                self._state = "running"
                self._waiting_reason = None
                self._last_check = -1e9
                self._record_event_locked(
                    "resume", "定时窗口已到，常驻恢复监督。")
            if self._state in ("error", "paused", "paused_exhausted", "recovering"):
                return None
            if self._state == "disabled":
                # 配置被外部改成 enabled=True（例如 Web 保存后还没轮到 resume）：
                # 自动开一轮倒计时，避免常驻线程空转。
                self._enter_countdown_locked(now, cfg)
            if self._state == "countdown":
                if now < self._countdown_until:
                    return None
                self._state = "starting"
                self._last_check = -1e9
                countdown_finished = True
            if now < self._settle_until:
                return None
            if not countdown_finished:
                interval = float(cfg["check_seconds"])
                if (now - self._last_check) < interval:
                    return None
                self._last_check = now
            else:
                # 倒计时刚结束：下一次 poll（约 1s 后）立刻判定，不再等一个
                # check_seconds，否则用户开了常驻还要再愣神几秒。
                self._last_check = -1e9

        if countdown_finished:
            message = (f"常驻倒计时结束（{cfg['start_delay_seconds']:.0f}s），"
                       f"开始监督。")
            self._record_event("countdown_done", message)
            return {"kind": "countdown_done", "reason": None,
                    "dry_run": bool(cfg["dry_run"]), "message": message}

        # 外部已停止自动化（用户点了停止/取消）→ 常驻让位，转暂停而不是拉起。
        stopped = self._safe_value(self._stopped_by, None)
        if stopped:
            reason = str(stopped)
            if bool(cfg["dry_run"]):
                message = f"自动化已被停止（{reason}），dry_run：不改变状态，仅报告。"
                self._record_event("would_pause", message)
                return {"kind": "would_pause", "reason": reason,
                        "dry_run": True, "message": message}
            self.pause(reason)
            with self._lock:
                message = self._last_message or f"常驻已暂停：{reason}。"
            return {"kind": "paused", "reason": reason, "message": message}

        # 1) 炉石进程消失：连续 ≥6s 才动作（防重启/更新时的瞬时抖动）。
        try:
            process_alive = bool(self._process_running())
        except Exception:
            process_alive = True
        if not process_alive:
            with self._lock:
                if self._process_gone_since is None:
                    self._process_gone_since = now
                gone_for = now - self._process_gone_since
            if gone_for < PROCESS_GONE_GRACE_SECONDS:
                # 进程刚消失，先等它自己回来；此时也不要急着"拉起自动化"。
                return None
            with self._lock:
                self._process_gone_since = None
            return self._trigger(cfg, "process_gone",
                                 f"炉石进程已消失 {gone_for:.0f}s")
        with self._lock:
            self._process_gone_since = None

        # 2) 自动化没在跑：常驻的职责就是把它拉起来（10 分钟最多 3 次）。
        try:
            automation_running = bool(self._automation_running())
        except Exception:
            automation_running = True
        if not automation_running:
            return self._maybe_start_automation(cfg, now)

        with self._lock:
            if self._state == "starting":
                self._state = "running"

        # 3) 日志停滞：对局中 / 非对局界面用不同阈值。
        try:
            in_game = bool(self._in_game())
        except Exception:
            in_game = False
        try:
            age = self._log_age()
        except Exception:
            age = None
        if age is None:
            return None
        try:
            age = float(age)
        except (TypeError, ValueError):
            return None
        threshold = (cfg["stale_in_game_seconds"] if in_game
                     else cfg["stale_other_seconds"])
        if age >= float(threshold):
            where = "对局中" if in_game else "非对局界面"
            return self._trigger(
                cfg, "log_stale",
                f"{where}日志已停滞 {age:.0f}s（阈值 {float(threshold):.0f}s）")
        # 2.5) 提前告警（原「存活检测」的告警档并入守护）：达到 warn 但未到处理
        # 阈值 → 只告警一次、不动作、不占配额；日志重新写起来后自动复位。
        # dry_run 的语义是"不告警、不动作、只返回事件"，所以试运行不发这条。
        warn_limit = float(cfg["warn_seconds"])
        if warn_limit > 0.0 and age >= warn_limit and not bool(cfg["dry_run"]):
            with self._lock:
                fresh = not self._stale_warned
                if fresh:
                    self._stale_warned = True
                    where = "对局中" if in_game else "非对局界面"
                    message = (
                        f"{where}日志已停滞 {age:.0f}s（提前告警阈值 "
                        f"{warn_limit:.0f}s；达到 {float(threshold):.0f}s 将自动恢复）"
                    )
                    self._record_event_locked("stale_warning", message)
            if fresh:
                self._warn(message)
                return {"kind": "stale_warning", "reason": "log_stale",
                        "dry_run": bool(cfg["dry_run"]), "message": message}
            return None
        with self._lock:
            self._stale_warned = False
        return None

    # ------------------------------------------------------------ 内部动作
    def _wait_for_schedule(self, cfg: dict,
                           hold_reason: str) -> Optional[dict]:
        """定时窗口未开始：进入/保持 ``waiting_schedule`` 待机。

        待机期间**不派发任何动作、不占用配额、不改 settle**；同一 reason 只
        记一次事件（reason 变化时重新记录）。用户暂停 / 配额耗尽 / 错误 /
        恢复中这些状态优先，不被待机覆盖。
        """
        with self._lock:
            if self._forced_disabled or self._cancelled or self._state == "cancelled":
                return None
            if (self._state == "waiting_schedule"
                    and self._waiting_reason == hold_reason):
                return None
            if self._state not in ("disabled", "countdown", "starting",
                                   "running", "waiting_schedule"):
                # paused / paused_exhausted / error / recovering：人工或限流
                # 的决定优先，不能被挂起回调悄悄改回等待态。
                return None
            self._state = "waiting_schedule"
            self._waiting_reason = hold_reason
            message = f"{hold_reason}，常驻待机"
            self._record_event_locked("waiting_schedule", message)
        return {"kind": "waiting_schedule", "reason": hold_reason,
                "dry_run": bool(cfg["dry_run"]), "message": message}

    def _current_hold_reason(self) -> Optional[str]:
        """调用注入的 ``hold_reason``；未注入 / 空串 / 异常都按"不挂起"处理。"""
        if self._hold_reason is None:
            return None
        try:
            value = self._hold_reason()
        except Exception:
            return None
        if not value:
            return None
        text = str(value).strip()
        return text or None

    def _maybe_start_automation(self, cfg: dict, now: float) -> Optional[dict]:
        """自动化未运行时尝试拉起（带 10 分钟 3 次限流 + dry_run 不派发）。"""
        if bool(cfg["dry_run"]):
            message = "自动化未运行（dry_run：不派发启动，仅报告）。"
            self._record_event("would_start_automation", message)
            return {"kind": "would_start_automation", "reason": None,
                    "dry_run": True, "message": message}

        # 决策前先确认状态仍允许拉起：poll 开头读到"自动化没在跑"之后，用户
        # 可能已经暂停/关闭/急停；此时绝不能把状态改回 starting 并派发。
        with self._lock:
            if self._state not in AUTOMATION_STARTABLE_STATES:
                return None
            self._start_times = [t for t in self._start_times
                                 if now - t < START_RATE_WINDOW_SECONDS]
            attempts = len(self._start_times)
            if attempts >= START_RATE_MAX:
                self._state = "error"
                message = (f"自动化已停止，且 {START_RATE_WINDOW_SECONDS / 60:.0f} 分钟内"
                           f"已尝试启动 {attempts} 次（上限 {START_RATE_MAX}），"
                           f"常驻停止自动拉起，等待人工处理。")
                self._record_event_locked("error", message)
                appended = False
            else:
                self._start_times.append(now)
                appended = True
                message = ""
        if attempts >= START_RATE_MAX:
            self._alert(message)
            return {"kind": "error", "reason": None, "dry_run": False,
                    "message": message}

        with self._lock:
            # 派发前最后确认一次：记账到这里之间用户仍可能按了暂停/关闭。
            # 没真正派发就不该占配额，把刚记的这次退回去。
            if self._state not in AUTOMATION_STARTABLE_STATES:
                if appended:
                    try:
                        self._start_times.remove(now)
                    except ValueError:
                        pass
                return None
            self._state = "starting"
        try:
            started = bool(self._start_automation())
        except Exception as exc:  # noqa: BLE001 - 外部动作失败不能让线程死
            started = False
            self._alert_throttled(f"常驻启动自动化时异常：{exc!r}")
        if started:
            message = "检测到自动化未运行，已请求启动。"
            self._record_event("automation_started", message)
            return {"kind": "automation_started", "reason": None,
                    "dry_run": False, "message": message}
        message = "检测到自动化未运行，但启动请求未成功；稍后会重试。"
        self._record_event("start_failed", message)
        self._alert(message)
        return {"kind": "start_failed", "reason": None, "dry_run": False,
                "message": message}

    def _trigger(self, cfg: dict, reason: str, detail: str) -> dict:
        """触发一次自动恢复：先查配额、先记账，再派发（dry_run 只报告）。"""
        now = self._clock()
        dry_run = bool(cfg["dry_run"])
        with self._lock:
            window = float(cfg["window_seconds"])
            limit = max(1, int(cfg["max_restarts"]))
            self._restart_times = [t for t in self._restart_times
                                   if now - t < window]
            in_window = len(self._restart_times)

            if dry_run and in_window >= limit:
                message = (f"{detail}；但窗口内已恢复 {in_window}/{limit} 次"
                           f"（dry_run：不派发，仅报告）。")
                self._record_event_locked("would_quota_exhausted", message)
                return {"kind": "would_quota_exhausted", "reason": reason,
                        "dry_run": True, "message": message}

            if in_window >= limit:
                self._state = "paused_exhausted"
                message = (f"{detail}；但 {window / 60:.0f} 分钟内已恢复 "
                           f"{in_window}/{limit} 次（达到上限），"
                           f"常驻已暂停自动恢复，等待人工处理。")
                self._record_event_locked("quota_exhausted", message)
                should_alert = True
                event = {"kind": "quota_exhausted", "reason": reason,
                         "dry_run": False, "message": message}
            elif dry_run:
                message = f"{detail}（dry_run：不派发恢复，仅报告）。"
                self._record_event_locked("would_restart", message)
                return {"kind": "would_restart", "reason": reason,
                        "dry_run": True, "message": message}
            else:
                # 先记账再派发：派发失败也占配额，坏通道不能被无限重试。
                self._restart_times.append(now)
                self._restart_times = [t for t in self._restart_times
                                       if now - t < window]
                count = len(self._restart_times)
                self._total_restarts += 1
                self._state = "recovering"
                self._settle_until = now + float(cfg["settle_seconds"])
                self._last_check = -1e9
                message = (f"{detail}，已触发第 {count}/{limit} 次自动恢复"
                           f"（窗口 {window / 60:.0f} 分钟）。")
                self._record_event_locked("restart", message)
                should_alert = False
                event = {"kind": "restart", "reason": reason, "restart": True,
                         "dry_run": False, "count": count, "limit": limit,
                         "message": message}
        if should_alert:
            self._alert(event["message"])
            return event

        try:
            recovered = bool(self._recover(cfg, reason))
        except Exception as exc:  # noqa: BLE001 - 外部动作失败不能让线程死
            recovered = False
            self._alert_throttled(f"常驻恢复派发异常：{exc!r}")
        if recovered:
            return event
        with self._lock:
            self._state = "running"
            message = f"{detail}，但自动恢复派发失败，已计入配额，稍后重试。"
            self._record_event_locked("restart_failed", message)
        self._alert(message)
        return {"kind": "restart_failed", "reason": reason, "restart": False,
                "dry_run": False, "message": message}

    # ------------------------------------------------------------ 状态快照
    def state(self) -> dict:
        """给网页/浮窗/CLI 的状态快照（JSON 友好）。"""
        cfg = self._cfg()
        now = self._clock()
        with self._lock:
            status = self._state
            self._restart_times = [t for t in self._restart_times
                                   if now - t < float(cfg["window_seconds"])]
            in_window = len(self._restart_times)
            settle_left = max(0.0, self._settle_until - now)
            paused_reason = self._paused_reason
            waiting_reason = self._waiting_reason
            last_event = self._last_event
            message = self._last_message
            total = self._total_restarts
        enabled = bool(cfg["enabled"])
        log_age = None
        in_game = False
        if enabled:
            # 关闭时不问日志/进对局状态：避免白跑一次截图/进程枚举。
            try:
                log_age = self._log_age()
            except Exception:
                log_age = None
            if log_age is not None:
                try:
                    log_age = float(log_age)
                except (TypeError, ValueError):
                    log_age = None
            try:
                in_game = bool(self._in_game())
            except Exception:
                in_game = False
        limit = max(1, int(cfg["max_restarts"]))
        return {
            "enabled": enabled,
            "state": status,
            "mode": cfg["mode"],
            "deck_index": cfg["deck_index"],
            "stale_in_game_seconds": float(cfg["stale_in_game_seconds"]),
            "stale_other_seconds": float(cfg["stale_other_seconds"]),
            "warn_seconds": float(cfg["warn_seconds"]),
            "warned": bool(self._stale_warned),
            "max_restarts": limit,
            "window_seconds": float(cfg["window_seconds"]),
            "check_seconds": float(cfg["check_seconds"]),
            "settle_seconds": float(cfg["settle_seconds"]),
            "start_delay_seconds": float(cfg["start_delay_seconds"]),
            "dry_run": bool(cfg["dry_run"]),
            "restarts_in_window": in_window,
            "restarts_total": total,
            "restarts_left": max(0, limit - in_window),
            "settle_left": settle_left,
            "log_age": log_age,
            "in_game": in_game,
            "recovering": status == "recovering",
            "paused_reason": paused_reason,
            "waiting_reason": waiting_reason,
            "last_event": last_event,
            "message": message,
        }

    # ------------------------------------------------------------ 线程
    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event = threading.Event()
            self._thread = threading.Thread(
                target=self._run, name="resident-supervisor", daemon=True)
            thread = self._thread
        thread.start()

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as exc:  # noqa: BLE001 - 线程必须活着
                # 判定/外部依赖出的任何异常都不能杀死常驻线程，只限流告警。
                self._alert_throttled(
                    f"常驻监督循环异常（已忽略，线程继续）：{exc!r}")
            if self._stop_event.is_set():
                break
            self._sleep(THREAD_SLEEP_SECONDS)

    # ------------------------------------------------------------ 工具
    def _enter_countdown_locked(self, now: float, cfg: dict) -> None:
        delay = float(cfg["start_delay_seconds"])
        self._countdown_until = now + delay
        self._last_check = -1e9
        self._process_gone_since = None
        self._stale_warned = False
        self._waiting_reason = None
        self._state = "countdown" if delay > 0 else "starting"

    def _record_event_locked(self, kind: str, message: str) -> None:
        self._last_event = str(kind)
        self._last_message = str(message)

    def _record_event(self, kind: str, message: str) -> None:
        with self._lock:
            self._record_event_locked(kind, message)

    @staticmethod
    def _safe_value(provider, default):
        try:
            return provider()
        except Exception:
            return default

    def _alert(self, message: str) -> None:
        try:
            self._alert_cb(str(message))
        except Exception:
            # 告警通道自己坏了也不能影响监督器。
            pass

    def _warn(self, message: str) -> None:
        """提前告警（只提示、不动作）：注入了 warn 回调就用它，否则退回告警通道。"""
        callback = self._warn_cb or self._alert_cb
        try:
            callback(str(message))
        except Exception:
            pass

    def _alert_throttled(self, message: str) -> None:
        now = self._clock()
        with self._lock:
            if now - self._last_alert_at < ALERT_MIN_INTERVAL_SECONDS:
                return
            self._last_alert_at = now
        self._alert(message)
