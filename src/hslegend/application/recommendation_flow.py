"""Execute exactly one validated recommendation and verify its result."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import inspect
from pathlib import Path
import time

from hslegend.application.hand_animation import HandAnimationDelay
from hslegend.domain import ActionKind
from hslegend.application.consumed_actions import ConsumedActionStore


# 「回合号还没记录过」的哨兵：不能用 None，因为有些状态里回合号就是 None。
_UNSET_TURN = object()

# 同一条推荐在一个回合里最多连续执行几次：风怒 2 击、超级风怒 4 击都够用，
# 只防盒子面板卡死（一直给同一行）时无限重复点同一个动作。
_MAX_IDENTICAL_REPEATS = 6

# 重试前刷新的等待上限（秒）：日志/状态类重试先等日志 revision 前进，避免拿
# 上一步的旧场面重算动作（用户 2026-10-31：「重试前要刷新各个信息再重试」）。
RETRY_REFRESH_TIMEOUT = 1.5

# 刷新轮询间隔（秒）：每次睡这么久再问一次状态，直到 revision 前进或超时。
_RETRY_REFRESH_INTERVAL = 0.2

# “状态类”重试码：说明日志/场面已经变了，重试前必须先刷新日志状态，并清掉
# OCR 帧缓存强制下一次重读面板；其它失败码（如 recommendation_not_stable）
# 不等待、不清缓存，保持原有节奏。
_RETRY_REFRESH_CODES = frozenset({
    "revision_changed",
    "duplicate_board_position",
    "turn_changed",
})

# 这些 OBSERVE 码说明盒子面板没有给出下一步可执行推荐（常见于盒子停止刷新）。
# 只有它们参与“超时无进展”兜底；正常的等待动作确认/回合未就绪不在此列。
_FALLBACK_STALL_CODES = frozenset({
    "waiting_recommendation_update",
})


def _accepts_action_kind(hook) -> bool:
    """挂点是否接受 action_kind 参数（老挂点只接受 0 个参数）。

    按函数签名判断，而不是靠 TypeError 兜底——挂点内部真正的 TypeError 会被
    误当成“参数不对”，反而把错误吞掉。
    """
    try:
        parameters = inspect.signature(hook).parameters
    except (TypeError, ValueError):
        return False
    for parameter in parameters.values():
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY,
                              inspect.Parameter.POSITIONAL_OR_KEYWORD,
                              inspect.Parameter.VAR_POSITIONAL):
            return True
    return False


class FlowStepStatus(str, Enum):
    EXECUTED = "executed"
    OBSERVE = "observe"
    RETRY = "retry"


class ResultStatus(str, Enum):
    COMPLETED = "completed"
    WAITING_RECOMMENDATION = "waiting_recommendation"
    UNCONFIRMED = "unconfirmed"
    RETRY = "retry"


@dataclass(frozen=True)
class FlowStepResult:
    status: FlowStepStatus
    diagnostics: str = ""


@dataclass
class PendingRecommendationAction:
    """A dispatched action whose result cannot yet be confirmed safely."""

    instruction: str
    turn_number: int
    action_kind: ActionKind
    source_entity_id: str | None
    target_entity_id: str | None
    postcondition: str
    before_state: object
    before_revision: int
    phase: str = "await_action_confirmation"
    # 已重试次数（同一个待确认动作）：到上限后只等盒子更新推荐，不再重击。
    attempts: int = 0
    # 到这个时刻（monotonic）仍未确认，就认为这次点击被客户端丢掉了。
    deadline: float = 0.0


class RecommendationFlow:
    def __init__(self, capture, reader, parser, state_supplier, adapter,
                 validator, controller, sleep=time.sleep,
                 clock=time.monotonic,
                 result_timeout=5.0, stopped=lambda: False,
                 consumed=None, post_action_delay=0.0,
                 post_action_pause=None,
                 hand_animation_delay=None,
                 draw_extra_delay_per_card=0.0,
                 unconfirmed_retry_delay=3.0,
                 pending_timeout=3.0,
                 max_pending_retries=2,
                 box_ai=True,
                 planner=None,
                 joint=False,
                 strategy_name="",
                 fallback_planner=None,
                 fallback_enabled=None,
                 fallback_fail_streak=6,
                 fallback_stall_seconds=8.0,
                 fallback_strategy_name=""):
        self.capture = capture
        self.reader = reader
        self.parser = parser
        self.state_supplier = state_supplier
        self.adapter = adapter
        self.validator = validator
        self.controller = controller
        self.sleep = sleep
        self.clock = clock
        # Kept for constructor compatibility; callers now wait in pending state
        # instead of replaying an unconfirmed action.
        self.result_timeout = result_timeout
        self.stopped = stopped
        self.consumed = consumed or ConsumedActionStore()
        self.post_action_delay = post_action_delay
        # 「活人感」（可选）：盒子意见执行完后的随机延时 + 鼠标悬停。
        # 返回 True 表示已接管本次延时，就不再叠加固定的 post_action_delay。
        # 只有走到这里的“识别盒子意见并执行完”的对局动作才会触发它。
        self.post_action_pause = post_action_pause
        # 挂点是否接受 action_kind（新挂点会据此决定“看手牌”还是“看场面”）。
        self._pause_takes_action_kind = (
            post_action_pause is not None
            and _accepts_action_kind(post_action_pause))
        # 「抽牌额外延时」（秒/张，网页「延时设置」可调）。判定看手牌入场数：
        #   * 每回合开始那一下的常规抽 1 张不算（那 1 张是免费的）；
        #   * 同一回合里再多抽的（比如开局一次抽 2 张）要算；
        #   * 回合内任何操作触发的抽牌，每张都算。
        self.draw_extra_delay_per_card = float(draw_extra_delay_per_card or 0.0)
        self.unconfirmed_retry_delay = max(
            0.0, float(unconfirmed_retry_delay or 0.0))
        # 待确认动作的等待上限：点击发出后客户端可能还在播动画，InputManager
        # 会把这一下直接丢掉（实测日志 "You clicked on something that is not
        # being handled by InputManager"）。没有上限时 pending 会一直等下去，
        # 同一段推荐每 0.3s 刷一遍，整回合卡死（用户 2026-10-30 实测）。
        # 超过 pending_timeout 仍未确认就按「这一下没生效」重试，最多重试
        # max_pending_retries 次；之后只等盒子更新推荐，不再重击。
        self.pending_timeout = max(0.0, float(pending_timeout or 0.0))
        self.max_pending_retries = max(0, int(max_pending_retries or 0))
        self._seen_entry_count = None
        # 免费那张只让给“我方这个回合还没执行过动作时”出现的第一次手牌增加，
        # 这样 Power.log 晚几拍、出牌抽到的牌先被看到时，也不会被误当成免费那张。
        self._entry_turn = _UNSET_TURN
        self._entry_turn_exempt_used = False
        self._acted_this_turn = False
        self.hand_animation_delay = (
            hand_animation_delay
            if hand_animation_delay is not None
            else HandAnimationDelay(clock=clock))
        self.waiting_instruction = None
        self.waiting_panel_hash = None
        self.waiting_turn_number = None
        self.waiting_action_kind = None
        self.pending_action = None
        # 同一条推荐连续重做的计数（每回合重置）。盒子在「风怒第二击」「连续打
        # 同一号位」时会给出完全相同的推荐行：确认上一步真的生效后必须再执行一次，
        # 不能当成“已完成”干等面板更新（用户 2026-10-31 实测：风怒只打了一下）。
        self._repeat_counts = {}
        self._repeat_turn = _UNSET_TURN
        # 最近一次已经打到日志里的 OCR 原文：同一段原文只留一次，
        # 重试几十次时不再刷屏（见 _log_ocr_raw）。
        self._last_logged_ocr_text = None
        # 本次 run_player_turn_step 入口读到的日志 revision：状态类重试
        # （revision_changed/turn_changed/…）要用来判断日志有没有前进。
        self._last_revision = None
        # ------------------------------------------------------------ 决策源
        # box_ai=True：现有 OCR 路径；False：本地卡组策略（planner）。
        # planner 缺失时强制回退盒子，避免“选了本地却没有搜索器”把流程带崩。
        self.planner = planner
        self.box_ai = bool(box_ai) or planner is None
        if not box_ai and planner is None:
            print("[SYS] 本地策略缺少搜索器，本局回退盒子AI。")
        # 联合模式：本地卡组时读盒子首步，与本地整回合最优比较。
        self.joint = bool(joint) and not self.box_ai and planner is not None
        self.strategy_name = str(strategy_name or "")
        # ------------------------------------------------------------ 盒子失效兜底
        # 盒子AI 路径连续失败/长时间无进展时，用 fallback_planner（显式策略）
        # 接管一步；盒子恢复可执行推荐后自动切回。默认不启用。
        self.fallback_planner = fallback_planner
        self.fallback_enabled = (
            bool(fallback_enabled) if fallback_enabled is not None
            else fallback_planner is not None)
        if self.fallback_enabled and fallback_planner is None:
            self.fallback_enabled = False
        self.fallback_fail_streak = max(1, int(fallback_fail_streak or 1))
        self.fallback_stall_seconds = max(
            0.0, float(fallback_stall_seconds or 0.0))
        self.fallback_strategy_name = str(fallback_strategy_name or "")
        # 兜底计数/状态：每个我方回合重置；pending 动作期间绝不触发。
        self._box_fail_streak = 0
        self._box_progress_at = 0.0
        self._box_progress_turn = _UNSET_TURN
        self._box_was_my_turn = False
        self._fallback_active = False
        self._fallback_failures = 0
        self._fallback_disabled_turn = None
        # 兜底需要一份最新快照；入口/等待手牌动画/重试刷新时顺手记录，避免
        # 每轮多读一次 StrategyState。
        self._latest_state = None
        self._latest_revision = None
        # 浮窗「推荐打法」：最近一次本地搜索的最高分 + 最佳动作序列。
        self.last_plan = None
        # 本回合同一动作的失败计数/拉黑（本地路径，6 次后跳过该动作）。
        self._failed_local_actions = {}
        self._blocked_local_actions = set()
        # 预备过的实体（本回合不能重复预备/交易）。
        self._prepared_entity_ids = set()
        # 本地待确认动作的重试计数（跨 pending 清空后仍保留，最多 max 次）。
        self._local_retry_key = None
        self._local_retry_count = 0
        # 联合模式对局小结（每局一行写 ai.txt）。
        self._joint_stats = {"diff": 0, "local": 0, "box": 0, "same": 0}
        self._joint_seen = set()
        self._box_note_warned = False

    def _note_readable_recommendation(self):
        """A readable recommendation starts a new raw-evidence episode."""
        self._last_logged_ocr_text = None

    def _retry(self, code):
        """Keep retrying until the next read becomes executable.

        Turn-transition animations can leave a stale recommendation visible for
        many seconds.  Failure counts are therefore not interpreted as a stuck
        recommendation and never suppress later retries.

        状态类失败（日志 revision / 场面 / 回合已经变了）说明手里这份信息已过期：
        先等日志 revision 前进并清掉 OCR 帧缓存，让上层重读，而不是拿旧场面
        重算动作（用户 2026-10-31：「每次操作重试前都要刷新各个信息」）。
        """
        if code in _RETRY_REFRESH_CODES:
            self._refresh_before_retry(self._last_revision, timeout=0.8)
            clear_cache = getattr(self.reader, "clear_cache", None)
            if clear_cache is not None:
                try:
                    clear_cache()
                except Exception:
                    pass
        return FlowStepResult(FlowStepStatus.RETRY, code)

    def _remember_state(self, state, revision):
        """记录最近一次拿到的战局快照（只给兜底用，不改 `_last_revision` 语义）。"""
        self._latest_state = state
        self._latest_revision = revision

    def _refresh_before_retry(self, min_revision, timeout=None):
        """重试前先刷新日志状态，最多等 ``timeout`` 秒让 revision 前进。

        先取一次 ``(state, revision)``；revision 还没超过 ``min_revision`` 时
        sleep ``_RETRY_REFRESH_INTERVAL`` 再取，直到 revision 前进 / 等待超过
        上限 / ``stopped()``；返回最新的 ``(state, revision)``。真的等待过才打
        一行诊断（A→B + 等待时长），让日志能区分“刷新过”和“还在用旧信息”。

        等待时长按实际 sleep 累加，不依赖注入的 clock：测试里冻结的假时钟也
        不会让这里空转。
        """
        limit = (RETRY_REFRESH_TIMEOUT if timeout is None
                 else max(0.0, float(timeout)))
        state, revision = self.state_supplier()
        waited = 0.0
        advanced_from = revision if min_revision is None else min_revision
        while (revision <= advanced_from and waited < limit
               and not self.stopped()):
            self.sleep(_RETRY_REFRESH_INTERVAL)
            waited += _RETRY_REFRESH_INTERVAL
            state, revision = self.state_supplier()
        if waited > 0:
            self._announce(
                f"[SYS] 重试前刷新：日志 revision {advanced_from}→{revision}"
                f"（等待 {waited:.1f}s）")
        self._remember_state(state, revision)
        return state, revision

    def _log_ocr_raw(self, text):
        """把这一次读到的盒子面板原文留痕（失败时用）。

        前缀必须是 `[推荐] 原文：`：battle_record 只把这种行归成
        recommendation_raw，web_ui 才会把它放进反馈上下文（错误.json 的
        `raw`）。之前解析失败打的是 `[推荐] OCR原文：`，会被当成一条**新的
        推荐指令**，于是反馈包里反而一条原文都没有（2026-10-27 反馈的
        `"raw": []` 就是这么来的）。

        同一段原文只打一次：面板没变时会连续重试几十次，重复打印只会刷屏。
        面板重新读通（`_note_readable_recommendation`）后清零，下一次“读不懂”
        插曲还会各自留一份，新的反馈包仍然有原文。每种预处理/抖动后的新
        文本也各自留一次，最多 12 行。
        """
        text = str(text or "")
        if not text or text == self._last_logged_ocr_text:
            return
        self._last_logged_ocr_text = text
        printed = 0
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            print(f"[推荐] 原文：{line}")
            printed += 1
            if printed >= 12:
                break

    def run_player_turn_step(self, allowed_action_kinds=None):
        """走一次决策源；盒子路径失败时按阈值切本地兜底（R18K）。"""
        result = self._run_decision_source(allowed_action_kinds)
        return self._maybe_box_fallback(result, allowed_action_kinds)

    def _run_decision_source(self, allowed_action_kinds=None):
        try:
            if self.stopped():
                return FlowStepResult(FlowStepStatus.OBSERVE, "stopped")
            state, revision = self.state_supplier()
            self._last_revision = revision
            self._remember_state(state, revision)
            self.hand_animation_delay.observe(
                getattr(state, "hand_entry_count", 0))
            # 回合开始的常规抽 1 张不算；这里处理“抽了 2 张以上”和上一步动作
            # 触发、日志晚几拍才写出来的抽牌。
            self._account_hand_entries(state)
            if not state.is_my_turn:
                self._clear_pending()
                return FlowStepResult(FlowStepStatus.OBSERVE, "opponent_turn")
            if (self.waiting_turn_number is not None
                    and self.waiting_turn_number
                    != state.game_num_turns_in_play):
                self._clear_waiting()
            if (self.pending_action is not None
                    and self.pending_action.turn_number
                    != state.game_num_turns_in_play):
                self._clear_pending()
            if not self.box_ai:
                return self._run_local_step(
                    state, revision, allowed_action_kinds)
            observed = {}
            def frame_supplier():
                observed["frame"] = self.capture.capture(ocr_panel_ok=True)
                return observed["frame"]
            evidence = self.reader.read(
                frame_supplier, self.capture.crop_recommendation)
            stable_frame = observed["frame"]
            try:
                proposed = self.parser.parse(
                    evidence, state.game_num_turns_in_play, revision)
            except Exception:
                # 解析失败也把 OCR 原文留痕（[推荐] 原文： → 进反馈包的 raw）。
                # 2026-10-24「目标是已方2号位」、2026-10-27「目标是已方1号位」
                # 都是靠这段原文才定位到 OCR 把「己」认成「已」的。
                self._log_ocr_raw(getattr(evidence, "normalized_text", ""))
                if self.pending_action is not None:
                    return FlowStepResult(
                        FlowStepStatus.OBSERVE,
                        "awaiting_action_confirmation")
                raise
            # 先留痕再适配：适配抛错时也要能看到"盒子到底写了什么"。线上出现过
            # 连续 3090 次 choose_one_name_not_unique 却一条原文都没留下的情况，
            # 就是因为这行原来打在适配之后。
            self._note_readable_recommendation()
            pending = self.pending_action
            same_pending = self._matches_pending(pending, proposed)
            if not same_pending:
                print(f"[推荐] {proposed.normalized_instruction}")
                raw_text = getattr(proposed, "raw_instruction", "") or ""
                if raw_text and raw_text != proposed.normalized_instruction:
                    for line in raw_text.splitlines():
                        if line.strip():
                            print(f"[推荐] 原文：{line.strip()}")
            if proposed.action == ActionKind.MULLIGAN:
                return FlowStepResult(
                    FlowStepStatus.OBSERVE,
                    "stale_mulligan_recommendation")
            if (allowed_action_kinds is not None
                    and proposed.action not in allowed_action_kinds):
                return FlowStepResult(
                    FlowStepStatus.OBSERVE, "turn_not_ready")
            next_attempts = 0
            # 先刷新状态再构造动作：以前盒子路径用入口时读到的旧 state 调
            # adapter，实体 id/坐标/postcondition 与随后校验用的 state 不是
            # 同一份（本地策略路径一直是先 fresh_state 再 adapter）。用户
            # 2026-10-31：「每次操作重试前都要刷新各个信息再重试」。
            fresh_state, fresh_revision = self._wait_for_hand_animation()
            if self.stopped():
                return FlowStepResult(FlowStepStatus.OBSERVE, "stopped")
            if pending is not None:
                if not same_pending:
                    self._clear_pending()
                else:
                    confirmed = (
                        fresh_revision > pending.before_revision
                        and self._postcondition(
                            pending.postcondition,
                            pending.before_state,
                            fresh_state,
                            pending.source_entity_id,
                            pending.target_entity_id,
                        )
                    )
                    if confirmed and self._repeat_allowed(proposed, fresh_state):
                        # 盒子又把同一条推荐给了一遍，而且上一步确实生效：
                        # 这是「再做一次」（风怒第二击、连续打同一号位），不是
                        # 「已完成」。解除消费标记后立刻再执行，不等盒子更新
                        # 面板、也不等动作结果确认（用户 2026-10-31 要求）。
                        self._note_repeat(proposed)
                        self.consumed.discard(
                            self._pending_consumed_key(pending))
                        self._clear_pending()
                    elif confirmed:
                        return FlowStepResult(
                            FlowStepStatus.OBSERVE,
                            "waiting_recommendation_update")
                    elif (self.pending_timeout > 0
                            and self.clock() >= pending.deadline
                            and pending.attempts < self.max_pending_retries):
                        # 点击没被客户端处理（动画期 InputManager 会丢弃）。但
                        # 也可能只是 Power.log 写得慢、动作其实已经生效：先刷新
                        # 日志状态并重判后置条件，真的已生效就不再点第二次。
                        fresh_state, fresh_revision = (
                            self._refresh_before_retry(
                                pending.before_revision))
                        retried_confirmed = (
                            fresh_revision > pending.before_revision
                            and self._postcondition(
                                pending.postcondition,
                                pending.before_state,
                                fresh_state,
                                pending.source_entity_id,
                                pending.target_entity_id,
                            )
                        )
                        if retried_confirmed:
                            # 动作其实已经生效：标记已消费并等盒子更新推荐。
                            self.consumed.mark_consumed(
                                self._pending_consumed_key(pending))
                            self._clear_pending()
                            return FlowStepResult(
                                FlowStepStatus.OBSERVE,
                                "waiting_recommendation_update")
                        next_attempts = pending.attempts + 1
                        self.consumed.discard(
                            self._pending_consumed_key(pending))
                        self._announce(
                            f"[SYS] 动作 {self.pending_timeout:.0f}s 未确认，"
                            f"重新执行（第 {next_attempts} 次）")
                        self._clear_pending()
                    else:
                        return FlowStepResult(
                            FlowStepStatus.OBSERVE,
                            "awaiting_action_confirmation")
            adapted = self.adapter(proposed, fresh_state)
            consumed_key = self.consumed.key_for(proposed, adapted)
            if (self.consumed.contains(consumed_key)
                    and proposed.action == ActionKind.ATTACK
                    and self._repeat_allowed(proposed, fresh_state)):
                # 没留下 pending（上一步被判定为完成）但盒子还在给同一击：
                # 风怒第二击同样是合法动作，解除消费标记后照常执行。
                self._note_repeat(proposed)
                self.consumed.discard(consumed_key)
            current_frame = self.capture.capture(ocr_panel_ok=True)
            current_evidence = self.reader.read_frame(
                current_frame, self.capture.crop_recommendation)
            if (current_evidence.normalized_text
                    != evidence.normalized_text):
                return self._retry("recommendation_changed")
            validation = self.validator.validate(
                proposed, adapted, stable_frame, current_frame,
                current_evidence,
                fresh_state, fresh_revision, "PLAYER_TURN", self.consumed,
                self.stopped())
            if not validation.accepted:
                return self._retry(validation.code)
            adapted, key = validation.value
            # 执行日志写清楚“执行了什么”：适配层把描述放在纯数据字段
            # description 里（应用层不 import 适配层，只 getattr 读字符串）。
            print(f"[执行] {self._execution_text(adapted, proposed)}")
            result = self.controller.execute(adapted.manual_action, fresh_state)
            if getattr(result, "cancelled", False):
                return FlowStepResult(FlowStepStatus.OBSERVE, "stopped")
            if not result.executed and not result.recovery_needed:
                return self._retry("execution_failed")
            retryable_unconfirmed = proposed.action in (
                ActionKind.ASSEMBLE_CHOICE, ActionKind.USE_ABILITY)
            defer_consumption = retryable_unconfirmed
            if not defer_consumption:
                self.consumed.mark_consumed(key)
            if not result.executed:
                if defer_consumption:
                    return self._retry("execution_failed")
                self._set_pending(proposed, adapted, fresh_state, fresh_revision,
                                  attempts=next_attempts)
                return FlowStepResult(
                    FlowStepStatus.OBSERVE, "awaiting_action_confirmation")
            self._print_execution_result(result)
            # 本回合已经动过手：之后再出现的手牌增加都是“操作触发的抽牌”，
            # 不能再当成回合开始那张免费的。
            self._acted_this_turn = True
            # 操作结束后延时再开始下一轮截图+OCR（盒子更新面板留时间）。
            # 通过 controller.output 推送延时行，让浮窗底部计时表显示该延时。
            # 「活人感」开启时由 post_action_pause 接管（随机 0.5~3s + 鼠标悬停），
            # 此时不再叠加固定延时。把刚执行完的 action_kind 一起传过去：攻击之后
            # 就不再盯手牌，改看己方随从行（新挂点才收这个参数，老的照旧 0 参数调）。
            paused = False
            if self.post_action_pause is not None:
                try:
                    if self._pause_takes_action_kind:
                        paused = bool(self.post_action_pause(proposed.action))
                    else:
                        paused = bool(self.post_action_pause())
                except Exception:
                    paused = False
            if not paused and self.post_action_delay:
                output = getattr(self.controller, "output", None)
                if output is not None:
                    try:
                        output(
                            f"[SYS] 操作结束，延时 "
                            f"{self.post_action_delay:.1f}s 后继续")
                    except Exception:
                        pass
                self.sleep(self.post_action_delay)
            if self.stopped():
                return FlowStepResult(FlowStepStatus.OBSERVE, "stopped")
            # 动作执行后立刻查一次手牌有没有变多（这个操作触发了抽牌）：
            # 查到了就按张数追加延时；还没写进日志就留到下一步再查。
            # 关闭该功能（0）时不做这次额外读取，保持原有节奏。
            if self.draw_extra_delay_per_card > 0:
                self._account_hand_entries(self.state_supplier()[0])
            verified = self._verify_result(
                proposed, adapted, current_frame,
                fresh_state, fresh_revision)
            if verified == ResultStatus.COMPLETED:
                if defer_consumption:
                    self.consumed.mark_consumed(key)
                return FlowStepResult(FlowStepStatus.EXECUTED)
            if verified in (ResultStatus.WAITING_RECOMMENDATION,
                            ResultStatus.UNCONFIRMED):
                if defer_consumption:
                    if verified == ResultStatus.WAITING_RECOMMENDATION:
                        self.consumed.mark_consumed(key)
                        return FlowStepResult(
                            FlowStepStatus.OBSERVE,
                            "waiting_recommendation_update")
                    if self.unconfirmed_retry_delay:
                        output = getattr(self.controller, "output", None)
                        if output is not None:
                            output(
                                "[SYS] 动作暂未确认，"
                                f"{self.unconfirmed_retry_delay:.1f}s 后重试")
                        self.sleep(self.unconfirmed_retry_delay)
                    if self.stopped():
                        return FlowStepResult(FlowStepStatus.OBSERVE, "stopped")
                    after_state, after_revision = self.state_supplier()
                    if (after_revision > fresh_revision
                            and self._postcondition(
                                adapted.postcondition, fresh_state, after_state,
                                adapted.source_entity_id,
                                adapted.target_entity_id)):
                        self.consumed.mark_consumed(key)
                        return FlowStepResult(
                            FlowStepStatus.OBSERVE,
                            "waiting_recommendation_update")
                    return self._retry("result_not_confirmed")
                self._set_pending(proposed, adapted, fresh_state, fresh_revision,
                                  attempts=next_attempts)
                return FlowStepResult(
                    FlowStepStatus.OBSERVE,
                    "waiting_recommendation_update"
                    if verified == ResultStatus.WAITING_RECOMMENDATION
                    else "awaiting_action_confirmation")
            return self._retry("result_not_confirmed")
        except Exception as exc:
            return self._retry(f"{type(exc).__name__}:{exc}")

    # ------------------------------------------------------------ 盒子失效兜底
    def _maybe_box_fallback(self, result, allowed_action_kinds=None):
        """盒子路径失败时，按阈值切本地兜底策略；盒子恢复后自动切回。

        触发条件（只看当前我方回合）：
          * 连续 RETRY 达到 fallback_fail_streak（兜底中只需 2 次，给盒子
            更新面板留一次机会）；
          * 或连续 fallback_stall_seconds 秒没有成功执行动作，且当前结果是
            RETRY / waiting_recommendation_update。

        绝不触发：本地模式、兜底关闭、对手回合、待确认动作、已停止、本回合
        本地兜底已连续失败 3 次。
        """
        if not (self.box_ai and self.fallback_enabled
                and self.fallback_planner is not None):
            return result
        if self.stopped():
            return result
        state = self._latest_state
        if state is None:
            return result
        if not getattr(state, "is_my_turn", False):
            self._box_was_my_turn = False
            return result

        turn = getattr(state, "game_num_turns_in_play", None)
        if (not self._box_was_my_turn
                or self._box_progress_turn is _UNSET_TURN
                or turn != self._box_progress_turn):
            # 新我方回合：清零所有兜底计数，重新给盒子机会。
            self._box_was_my_turn = True
            self._box_progress_turn = turn
            self._box_progress_at = self.clock()
            self._box_fail_streak = 0
            self._fallback_active = False
            self._fallback_failures = 0
        if result.status is FlowStepStatus.EXECUTED:
            self._box_fail_streak = 0
            self._box_progress_at = self.clock()
            if self._fallback_active:
                self._fallback_active = False
                self._announce("[兜底] 盒子推荐恢复，切回盒子AI。")
            return result
        if (self._fallback_disabled_turn == turn
                and not (self._fallback_active
                         and self.pending_action is not None)):
            return result
        if self.pending_action is not None:
            # 盒子自己执行的动作在待确认时继续等盒子，不抢。
            # 但兜底动作的待确认在盒子 OCR 失败时必须交给本地路径确认：
            # 盒子路径先读 OCR 再判 pending，OCR 失败时永远走不到确认逻辑，
            # 不接管的话本地动作会一直挂在 pending 上。
            if not (self._fallback_active
                    and result.status is FlowStepStatus.RETRY):
                return result
            reason = "本地兜底动作待确认，盒子仍不可用"
        else:
            reason = None
            if (result.status is FlowStepStatus.RETRY
                    and result.diagnostics != "stopped"):
                self._box_fail_streak += 1
                threshold = (2 if self._fallback_active
                             else self.fallback_fail_streak)
                if self._box_fail_streak >= threshold:
                    reason = f"连续 {self._box_fail_streak} 次未读到可执行推荐"

            stalled_seconds = self.clock() - self._box_progress_at
            stalled = (self.fallback_stall_seconds > 0
                       and stalled_seconds >= self.fallback_stall_seconds)
            if reason is None and stalled and (
                    result.status is FlowStepStatus.RETRY
                    or (result.status is FlowStepStatus.OBSERVE
                        and result.diagnostics in _FALLBACK_STALL_CODES)):
                reason = f"{stalled_seconds:.0f}s 没有成功执行盒子推荐"
        if reason is None:
            return result

        self._fallback_active = True
        self._announce(
            f"[兜底] 盒子推荐失效（{reason}）→ 改用本地「"
            f"{self.fallback_strategy_name or '通用'}」策略。")
        try:
            fallback_state, fallback_revision = self.state_supplier()
        except Exception as exc:
            self._announce(
                "[兜底] 读取最新战局失败，继续等盒子："
                f"{type(exc).__name__}: {exc}")
            return result
        self._remember_state(fallback_state, fallback_revision)
        fallback = self._run_local_step(
            fallback_state, fallback_revision, allowed_action_kinds,
            planner=self.fallback_planner)
        if fallback.status is FlowStepStatus.EXECUTED:
            self._box_progress_at = self.clock()
            self._box_fail_streak = 0
        elif fallback.status is FlowStepStatus.RETRY:
            self._fallback_failures += 1
            if self._fallback_failures >= 3:
                self._fallback_disabled_turn = turn
                self._announce(
                    "[兜底] 本地兜底连续失败，本回合不再接管，继续等盒子。")
        return fallback

    # ------------------------------------------------------------ 本地策略路径
    def _run_local_step(self, state, revision, allowed_action_kinds,
                        planner=None):
        """本地卡组策略：search 出整回合最优，执行首步；不读推荐面板。

        `planner` 缺省用构造时的 planner（本地主模式）；兜底时显式传
        fallback_planner，执行链路完全复用。
        """
        planner = planner or self.planner
        if planner is None:
            return self._retry("local_strategy_unavailable")
        # 1) 待确认动作：只读日志后置条件，不跑搜索（搜索很贵，不能每 0.3s 一次）。
        pending = self.pending_action
        if pending is not None:
            confirmed = (
                revision > pending.before_revision
                and self._postcondition(
                    pending.postcondition, pending.before_state, state,
                    pending.source_entity_id, pending.target_entity_id)
            )
            if confirmed:
                self._clear_pending()
            elif (self.pending_timeout > 0
                    and self.clock() >= pending.deadline
                    and pending.attempts < self.max_pending_retries):
                # 点击没被客户端处理：解除消费标记，下一步重新搜索并重击。
                self._local_retry_key = (
                    pending.turn_number, pending.instruction)
                self._local_retry_count = pending.attempts + 1
                self.consumed.discard(self._pending_consumed_key(pending))
                self._announce(
                    f"[SYS] 动作 {self.pending_timeout:.0f}s 未确认，"
                    f"重新执行（第 {self._local_retry_count} 次）")
                self._clear_pending()
                return FlowStepResult(
                    FlowStepStatus.OBSERVE, "awaiting_action_confirmation")
            else:
                return FlowStepResult(
                    FlowStepStatus.OBSERVE, "awaiting_action_confirmation")
        # 2) 搜索（把本回合的失败拉黑/预备实体/已动作信号带给搜索）。
        try:
            state.blocked_action_keys = set(self._blocked_local_actions)
            state.prepared_entity_ids = set(self._prepared_entity_ids)
            state.acted_this_turn = self._acted_this_turn
        except Exception:
            pass
        box_note = None
        if self.joint:
            box_note = self._capture_box_recommendation(state, revision)
        try:
            actions, score = planner.plan(
                state, getattr(state, "my_last_mana", 0),
                stopped=self.stopped)
        except Exception as exc:
            self._announce(
                f"[SYS] 本地策略搜索失败：{type(exc).__name__}: {exc}")
            return self._retry("search_failed")
        if not actions:
            return self._retry("no_local_action")
        self.last_plan = {
            "turn": getattr(state, "game_num_turns_in_play", None),
            "score": score,
            "steps": [planner.describe(item) for item in actions],
        }
        chosen = actions[0]
        if self.joint:
            chosen = self._choose_joint_action(
                state, actions, score, box_note)
        print(f"[推荐] {chosen.normalized_instruction} 最佳评分 {score}")
        if (allowed_action_kinds is not None
                and chosen.action not in allowed_action_kinds):
            return FlowStepResult(FlowStepStatus.OBSERVE, "turn_not_ready")
        if chosen.action == ActionKind.MULLIGAN:
            return FlowStepResult(
                FlowStepStatus.OBSERVE, "stale_mulligan_recommendation")
        next_attempts = 0
        key = (chosen.turn_number, chosen.normalized_instruction)
        if self._local_retry_key == key:
            next_attempts = self._local_retry_count
        else:
            self._local_retry_key = None
            self._local_retry_count = 0
        fresh_state, fresh_revision = self._wait_for_hand_animation()
        if self.stopped():
            return FlowStepResult(FlowStepStatus.OBSERVE, "stopped")
        if chosen.turn_number != fresh_state.game_num_turns_in_play:
            return self._retry("turn_changed")
        if revision != fresh_revision or chosen.log_revision != revision:
            return self._retry("revision_changed")
        if (not getattr(fresh_state, "is_my_turn", False)
                and getattr(fresh_state, "discover_choice_count", None)
                not in (1, 2, 3, 4)):
            return FlowStepResult(FlowStepStatus.OBSERVE, "opponent_turn")
        adapted = self.adapter(chosen, fresh_state)
        key = self.consumed.key_for(chosen, adapted)
        if self.consumed.contains(key):
            return FlowStepResult(FlowStepStatus.OBSERVE, "already_consumed")
        print(f"[执行] {self._execution_text(adapted, chosen)}")
        result = self.controller.execute(adapted.manual_action, fresh_state)
        if getattr(result, "cancelled", False):
            return FlowStepResult(FlowStepStatus.OBSERVE, "stopped")
        if not result.executed and not result.recovery_needed:
            self._record_local_failure(chosen, adapted)
            return self._retry("execution_failed")
        self._print_execution_result(result)
        self.consumed.mark_consumed(key)
        self._acted_this_turn = True
        if (chosen.action == ActionKind.TRADE_CARD
                and chosen.normalized_instruction.startswith("预备")
                and adapted.source_entity_id is not None):
            self._remember_prepared_card(chosen, adapted, fresh_state)
        paused = False
        if self.post_action_pause is not None:
            try:
                if self._pause_takes_action_kind:
                    paused = bool(self.post_action_pause(chosen.action))
                else:
                    paused = bool(self.post_action_pause())
            except Exception:
                paused = False
        if not paused and self.post_action_delay:
            output = getattr(self.controller, "output", None)
            if output is not None:
                try:
                    output(
                        f"[SYS] 操作结束，延时 "
                        f"{self.post_action_delay:.1f}s 后继续")
                except Exception:
                    pass
            self.sleep(self.post_action_delay)
        if self.stopped():
            return FlowStepResult(FlowStepStatus.OBSERVE, "stopped")
        if self.draw_extra_delay_per_card > 0:
            self._account_hand_entries(self.state_supplier()[0])
        verified = self._verify_result(
            chosen, adapted, None, fresh_state, fresh_revision,
            check_recommendation=False)
        if verified == ResultStatus.COMPLETED:
            return FlowStepResult(FlowStepStatus.EXECUTED)
        if verified in (ResultStatus.WAITING_RECOMMENDATION,
                        ResultStatus.UNCONFIRMED):
            self._set_pending(chosen, adapted, fresh_state, fresh_revision,
                              attempts=next_attempts)
            return FlowStepResult(
                FlowStepStatus.OBSERVE,
                "waiting_recommendation_update"
                if verified == ResultStatus.WAITING_RECOMMENDATION
                else "awaiting_action_confirmation")
        return self._retry("result_not_confirmed")

    def _choose_joint_action(self, state, actions, score, box_note):
        """联合模式：本地整回合 vs 盒子首步（补齐）；返回最终采用的动作。"""
        plan = None
        try:
            plan = self.planner.joint_plan(state, actions, score, box_note)
        except Exception as exc:
            print(f"[联合] 方案比较失败，改用本地：{type(exc).__name__}: {exc}")
        if plan is not None:
            self._note_joint(state, plan)
            if (getattr(state, "discover_choice_count", None) in (1, 2, 3, 4)
                    and box_note is not None
                    and box_note.action == ActionKind.CHOOSE_DISCOVER):
                # 菜单里唯一合法输入是“点哪个选项”，本地整回合比较无意义。
                print("[联合] 发现/选择菜单 → 采用 盒子选项")
                return box_note
            box = plan.get("box") or {}
            if plan.get("chosen") == "box" and box_note is not None:
                print(f"[联合] 本地 {(plan.get('local') or {}).get('score')} vs "
                      f"盒子 {box.get('score')} → 采用 盒子")
                return box_note
            if box:
                print(f"[联合] 本地 {(plan.get('local') or {}).get('score')} vs "
                      f"盒子 {box.get('score')} → 采用 本地")
        return actions[0]

    def _capture_box_recommendation(self, state, revision):
        """联合模式：顺手读一次盒子面板推荐（读不到不影响对局）。"""
        try:
            frame = self.capture.capture(ocr_panel_ok=True)
            evidence = self.reader.read_frame(
                frame, self.capture.crop_recommendation)
            if not getattr(evidence, "normalized_text", ""):
                return None
            proposed = self.parser.parse(
                evidence, state.game_num_turns_in_play, revision)
            if proposed.action == ActionKind.MULLIGAN:
                return None
            return proposed
        except Exception as exc:
            if not self._box_note_warned:
                self._box_note_warned = True
                print(f"[联合] 盒子推荐读取失败（不影响对局）："
                      f"{type(exc).__name__}: {exc}")
            return None

    def _note_joint(self, state, plan):
        """记一个联合模式决策点（每局汇总，对局结束写 ai.txt）。"""
        try:
            key = (int(getattr(state, "game_num_turns_in_play", 0) or 0),
                   int(getattr(state, "log_revision", 0) or 0))
        except (TypeError, ValueError):
            return
        if key in self._joint_seen:
            return
        self._joint_seen.add(key)
        plan = plan or {}
        if plan.get("diff"):
            self._joint_stats["diff"] += 1
        outcome = plan.get("outcome") or "same"
        if outcome not in self._joint_stats:
            outcome = "same"
        self._joint_stats[outcome] += 1

    def _joint_battle_name(self):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{stamp}_{self.strategy_name}" if self.strategy_name else stamp

    def flush_joint_log(self):
        """把本局联合模式小结追加到仓库根目录 ai.txt；没有数据返回 None。"""
        counts = dict(self._joint_stats)
        total = counts["local"] + counts["box"] + counts["same"]
        if total <= 0:
            return None
        line = (f"{self._joint_battle_name()}"
                f" | 打法差异 {counts['diff']} 次"
                f" | 本地胜出 {counts['local']} 次"
                f" | 盒子胜出 {counts['box']} 次"
                f" | 差不多 {counts['same']} 次"
                f" | 共 {total} 个决策点")
        try:
            path = Path(__file__).resolve().parents[3] / "ai.txt"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception as exc:
            print(f"[联合] 写 ai.txt 失败（不影响对局）："
                  f"{type(exc).__name__}: {exc}")
            return None
        self._joint_stats = {"diff": 0, "local": 0, "box": 0, "same": 0}
        self._joint_seen.clear()
        print(f"[联合] {line}")
        return line

    @staticmethod
    def _slot_identity(slot):
        if slot is None:
            return ""
        return f"{slot.kind}:{slot.owner or ''}:{slot.index or ''}"

    def _action_identity(self, proposed, adapted):
        """同一动作的去重键：动作类型 + 实体（或位置）+ 目标。"""
        source_entity_id = (
            (getattr(adapted, "source_entity_id", None)
             if adapted is not None else None)
            or getattr(proposed, "entity_id", None))
        target_entity_id = (
            (getattr(adapted, "target_entity_id", None)
             if adapted is not None else None)
            or getattr(proposed, "target_entity_id", None))
        source_key = (str(source_entity_id) if source_entity_id
                      else self._slot_identity(proposed.source))
        target_key = (str(target_entity_id) if target_entity_id
                      else self._slot_identity(proposed.target))
        return (proposed.action.value, source_key, target_key,
                getattr(proposed, "choice_card_id", None) or "")

    def _record_local_failure(self, proposed, adapted):
        """同一动作连续失败 6 次后本回合跳过（等动画播完再重试的兜底）。"""
        identity = self._action_identity(proposed, adapted)
        attempts = self._failed_local_actions.get(identity, 0) + 1
        self._failed_local_actions[identity] = attempts
        if attempts >= 6 and identity not in self._blocked_local_actions:
            self._blocked_local_actions.add(identity)
            print(f"[兜底] 相同动作连续失败 {attempts} 次，本回合跳过："
                  f"{proposed.normalized_instruction}")
        return attempts

    def _remember_prepared_card(self, proposed, adapted, before_state):
        """记住预备后的手牌实体（Power.log 可能换新 entity id）。"""
        source_id = str(adapted.source_entity_id)
        before_card = next(
            (card for card in before_state.my_hand_cards
             if str(getattr(card, "entity_id", "")) == source_id), None)
        if before_card is None:
            self._prepared_entity_ids.add(source_id)
            return
        try:
            expected_cost = max(
                0, int(before_card.current_cost)
                - int(before_state.my_last_mana) - 1)
        except (TypeError, ValueError, AttributeError):
            expected_cost = None
        after_state, _ = self.state_supplier()
        after_cards = list(getattr(after_state, "my_hand_cards", ()) or ())
        if any(str(getattr(card, "entity_id", "")) == source_id
               for card in after_cards):
            self._prepared_entity_ids.add(source_id)
            return
        old_ids = {str(getattr(card, "entity_id", ""))
                   for card in before_state.my_hand_cards}
        candidates = [
            card for card in after_cards
            if getattr(card, "card_id", None)
            == getattr(before_card, "card_id", None)
            and str(getattr(card, "entity_id", "")) not in old_ids
            and (expected_cost is None
                 or getattr(card, "current_cost", None) == expected_cost)
        ]
        if len(candidates) == 1:
            self._prepared_entity_ids.add(
                str(getattr(candidates[0], "entity_id", "")))
        else:
            self._prepared_entity_ids.add(source_id)

    def _wait_for_hand_animation(self):
        """等待手牌入场动画完成，再取最新状态做校验（上游性能调优逻辑）。"""
        stopped = getattr(self, "stopped", lambda: False)
        while True:
            if stopped():
                state, revision = self.state_supplier()
                self._remember_state(state, revision)
                return state, revision
            state, revision = self.state_supplier()
            self.hand_animation_delay.observe(
                getattr(state, "hand_entry_count", 0))
            remaining = self.hand_animation_delay.remaining()
            if remaining <= 0:
                self._remember_state(state, revision)
                return state, revision
            self.sleep(remaining)
            if stopped():
                state, revision = self.state_supplier()
                self._remember_state(state, revision)
                return state, revision

    # -------------------------------------------------- 抽牌额外延时（回合内抽牌）
    @staticmethod
    def _hand_entry_count(state):
        """手牌入场计数（Power.log 里 ZONE→HAND 的累计次数）。"""
        try:
            return int(getattr(state, "hand_entry_count", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _account_hand_entries(self, state):
        """我方回合手牌变多 = 抽到牌 → 每张追加「抽牌额外延时」。

        规则（按需求）：
          * 每回合开始那一下的常规抽 1 张不算（本回合我方还没动作过时让掉 1 张）；
          * 同一回合里再多抽的（开局一次抽 2 张）算；
          * 回合内任何操作触发的抽牌，每张都算。
        对手回合的抽牌不归我们延时；换局导致计数清零时重新开始记账。
        该功能关闭（0 秒/张）时直接不记账。
        """
        if self.draw_extra_delay_per_card <= 0:
            return 0
        count = self._hand_entry_count(state)
        previous = self._seen_entry_count
        self._seen_entry_count = count

        turn = getattr(state, "game_num_turns_in_play", None)
        if self._entry_turn is _UNSET_TURN or turn != self._entry_turn:
            # 新回合（或新的一局）：免费那张与“本回合动作记录”都重新开始。
            self._entry_turn = turn
            self._entry_turn_exempt_used = False
            self._acted_this_turn = False

        if previous is None or count == previous:
            return 0
        if count < previous:                     # 换局/计数清零
            return 0
        if not getattr(state, "is_my_turn", False):
            return 0

        gained = count - previous
        if not self._entry_turn_exempt_used and not self._acted_this_turn:
            # 我方本回合还没动作过 → 这次增加是回合开始的常规抽牌，让掉 1 张。
            self._entry_turn_exempt_used = True
            gained -= 1
        if gained <= 0:
            return 0
        extra = gained * self.draw_extra_delay_per_card
        if extra <= 0:
            return gained
        self._announce(f"[SYS] 抽到 {gained} 张牌，额外延时 {extra:.1f}s 后继续")
        self.sleep(extra)
        return gained

    def _announce(self, text):
        """把延时说明打到控制台与浮窗（浮窗按「延时」关键字收）。

        只走**一个**通道：ManualController.output 默认就是 print，而 web_ui 用
        _TeeStream 把 stdout 转进 _log → log_overlay.push。原来 print + output
        各推一次，同一行会在浮窗里出现两条完全一样的日志（抽牌额外延时实测）。
        """
        output = getattr(self.controller, "output", None)
        if output is not None:
            try:
                output(text)
            except Exception:
                pass
            return
        try:
            print(text)
        except Exception:
            pass

    @staticmethod
    def _execution_text(adapted, proposed):
        """执行日志里“执行了什么”：适配描述优先，缺失时退回推荐原文。

        应用层不 import 适配层（分层约束），只 getattr 读纯数据字段；旧测试
        注入的动作对象没有 description 时同样走兜底。
        """
        description = str(getattr(adapted, "description", "") or "")
        return description or str(
            getattr(proposed, "normalized_instruction", "") or "")

    @staticmethod
    def _print_execution_result(result):
        """点击成功后补一行执行结果（``message`` 为空则不打印）。"""
        if not getattr(result, "executed", False):
            return
        message = str(getattr(result, "message", "") or "")
        if message:
            print(f"[SYS] 执行结果：{message}")

    def _frame_for(self, frame_id):
        # Capture implementations may retain the stable reader's current frame.
        current = getattr(self.capture, "last_frame", None)
        if current is not None and current.frame_id == frame_id:
            return current
        # Tests and simple suppliers can provide a fresh identity-equivalent frame.
        return type("FrameProxy", (), {
            "frame_id": frame_id, "desktop_size": (1920, 1080), "dpi": 96,
            "window_handle": 1, "foreground": True, "panel_visible": True,
        })()

    def _verify_result(self, proposed, adapted, before_frame, before_state,
                       before_revision, check_recommendation=True):
        """Check the result once right after executing; no timeout waiting.

        A changed recommendation text confirms completion. A discover
        action or a satisfied log postcondition waits for the next
        recommendation. Anything else is UNCONFIRMED and must not be replayed
        before new evidence authorizes another attempt.

        本地策略路径没有推荐面板可读，`check_recommendation=False` 只看日志。
        """
        before_instruction = proposed.normalized_instruction
        after_state, after_revision = self.state_supplier()
        log_changed = (after_revision > before_revision
                       and self._postcondition(
                           adapted.postcondition, before_state, after_state,
                           adapted.source_entity_id,
                           adapted.target_entity_id))
        recommendation_changed = False
        if check_recommendation:
            try:
                after_frame = self.capture.capture(ocr_panel_ok=True)
                if after_frame.exact_hash != before_frame.exact_hash:
                    supplied = False
                    def frame_supplier():
                        nonlocal supplied
                        if not supplied:
                            supplied = True
                            return after_frame
                        return self.capture.capture(ocr_panel_ok=True)
                    after_evidence = self.reader.read(
                        frame_supplier, self.capture.crop_recommendation)
                    recommendation_changed = (
                        after_evidence.normalized_text != before_instruction)
            except Exception:
                recommendation_changed = False
        if recommendation_changed:
            return ResultStatus.COMPLETED
        if proposed.action in (ActionKind.TIMELINE_UNDO,
                               ActionKind.TIMELINE_KEEP):
            # 回溯/维持只影响 HSAng 侧 UI，不改动 Power.log：面板文本变了才算
            # 完成；没变也不能立刻 RETRY 重击（会二次撤销/误操作）。返回
            # WAITING 让上层标记已消费并等待盒子更新推荐。
            return ResultStatus.WAITING_RECOMMENDATION
        if proposed.action == ActionKind.ASSEMBLE_CHOICE:
            # 拼装点击发出后若日志和面板都没变化，说明四选一没点中；
            # 交给上层延时后重试。
            return (ResultStatus.WAITING_RECOMMENDATION if log_changed
                    else ResultStatus.UNCONFIRMED)
        if proposed.action == ActionKind.CHOOSE_DISCOVER:
            return ResultStatus.WAITING_RECOMMENDATION
        if proposed.action == ActionKind.SELECT_TARGET:
            # 选项结算后的“点目标”：点一下游戏就会继续结算，面板文本可能
            # 稍后才更新。和发现一样先 WAITING，避免同一目标被连续重点。
            return ResultStatus.WAITING_RECOMMENDATION
        if proposed.action == ActionKind.USE_ABILITY:
            # 古神技能：结算一定有日志痕迹（伤害/召唤/加 Buff/扣蓝）。
            # 日志一点没动也只能进入待确认；日志动了就当已发动并等盒子更新。
            # 没看到日志就先延时重试；看到日志才算已发动。
            return (ResultStatus.WAITING_RECOMMENDATION if log_changed
                    else ResultStatus.UNCONFIRMED)
        if log_changed:
            return ResultStatus.WAITING_RECOMMENDATION
        return ResultStatus.UNCONFIRMED

    @staticmethod
    def _board_entry(state, source):
        """按盒子号位（1 基，随从+地标按 zone_pos 排序）取己方场上的实体。"""
        if getattr(source, "kind", None) != "board_slot":
            return None
        index = getattr(source, "index", 0) or 0
        entries = (list(getattr(state, "my_minions", ()) or ())
                   + list(getattr(state, "my_locations", ()) or ()))
        entries.sort(key=lambda entry: getattr(entry, "zone_pos", 0) or 0)
        if not 1 <= index <= len(entries):
            return None
        return entries[index - 1]

    @classmethod
    def _attack_source_available(cls, proposed, state) -> bool:
        """同一条攻击推荐又出现时，日志里的攻击者还能不能再打一次。

        风怒随从第一击后 EXHAUSTED 仍是 0（第二击才置 1，实测 2026-10-31
        Power.log：影焰猎豹第一击只写 NUM_ATTACKS_THIS_TURN=1，第二击才写
        EXHAUSTED=1），盒子在两次攻击之间给的是同一行推荐。
        """
        source = getattr(proposed, "source", None)
        if getattr(source, "kind", None) == "hero":
            hero = getattr(state, "my_hero", None)
            return hero is not None and getattr(hero, "exhausted", 1) == 0
        entry = cls._board_entry(state, source)
        if entry is None:
            return False
        return (getattr(entry, "exhausted", 1) == 0
                and not getattr(entry, "frozen", 0)
                and not getattr(entry, "cant_attack", 0)
                and not getattr(entry, "dormant", 0))

    @classmethod
    def _ability_source_available(cls, proposed, state) -> bool:
        """古神本体（克苏恩，帝国之眼）每回合最多激活 2 次技能。

        第一次技能后盒子会给同一行推荐，第二次点完就不能再点了。
        """
        entry = cls._board_entry(state, getattr(proposed, "source", None))
        if entry is None:
            return False
        return getattr(entry, "ability_uses_turn", 0) < 2

    @staticmethod
    def _repeat_key(proposed) -> tuple:
        source = getattr(proposed, "source", None)
        target = getattr(proposed, "target", None)
        return (
            proposed.turn_number,
            proposed.normalized_instruction,
            getattr(source, "kind", None),
            getattr(source, "index", None),
            getattr(target, "kind", None),
            getattr(target, "index", None),
        )

    def _repeat_allowed(self, proposed, state) -> bool:
        """同一条推荐能不能马上再执行一次。

        只在“上一步确实生效”（confirmed）时才会问这个问题：盒子把同一行推荐
        又给一遍，说明它还要再做一次（风怒第二击、连续打同一号位、古神技能
        两次），不是动作已完成。只放行“再做一次一定还是同一个动作”的类型：
        攻击要求攻击者还能出手、古神技能要求本回合还有次数；面板/手势类动作
        （结束回合、发现、拼装、选目标…）重复执行容易点空，交给原有的超时重试。
        """
        if self._repeat_turn != proposed.turn_number:
            self._repeat_turn = proposed.turn_number
            self._repeat_counts = {}
        if proposed.action == ActionKind.ATTACK:
            if not self._attack_source_available(proposed, state):
                return False
        elif proposed.action == ActionKind.USE_ABILITY:
            if not self._ability_source_available(proposed, state):
                return False
        elif proposed.action not in (ActionKind.PLAY_CARD,
                                     ActionKind.TRADE_CARD):
            return False
        return self._repeat_counts.get(
            self._repeat_key(proposed), 0) < _MAX_IDENTICAL_REPEATS

    def _note_repeat(self, proposed) -> None:
        key = self._repeat_key(proposed)
        self._repeat_counts[key] = self._repeat_counts.get(key, 0) + 1

    @staticmethod
    def _matches_pending(pending, proposed) -> bool:
        """当前读取到的推荐是不是待确认的那一条（同回合、同指令）。"""
        return (pending is not None
                and pending.turn_number == proposed.turn_number
                and pending.instruction == proposed.normalized_instruction)

    @staticmethod
    def _pending_consumed_key(pending) -> tuple:
        """与 ConsumedActionStore.key_for 同构，用来在重试前解除消费标记。"""
        return (
            pending.turn_number,
            pending.instruction,
            pending.source_entity_id,
            pending.target_entity_id,
            pending.postcondition,
        )

    def _set_pending(self, proposed, adapted, before_state, before_revision,
                     attempts=0):
        self.pending_action = PendingRecommendationAction(
            instruction=proposed.normalized_instruction,
            turn_number=proposed.turn_number,
            action_kind=proposed.action,
            source_entity_id=adapted.source_entity_id,
            target_entity_id=adapted.target_entity_id,
            postcondition=adapted.postcondition,
            before_state=before_state,
            before_revision=before_revision,
            attempts=attempts,
            deadline=self.clock() + self.pending_timeout,
        )

    def _clear_pending(self):
        self.pending_action = None

    def _clear_waiting(self):
        self.waiting_instruction = None
        self.waiting_panel_hash = None
        self.waiting_turn_number = None
        self.waiting_action_kind = None

    @staticmethod
    def _hash_distance(left, right):
        if not left or not right or len(left) != len(right):
            return float("inf")
        try:
            return sum(
                (int(a, 16) ^ int(b, 16)).bit_count()
                for a, b in zip(left, right))
        except ValueError:
            return 0 if left == right else float("inf")

    @staticmethod
    def _postcondition(kind, before, after, source_entity_id=None,
                       target_entity_id=None):
        if kind == "turn_changed":
            return (after.game_num_turns_in_play != before.game_num_turns_in_play
                    or not after.is_my_turn)
        if kind == "hand_card_left":
            return source_entity_id not in {
                getattr(card, "entity_id", None)
                for card in after.my_hand_cards}
        if kind == "tempt_mana_spent":
            # 预备：卡牌拖进牌库后马上回到手牌（实体可能换新 id），判定依据是
            # 剩余法力被消耗（预备消耗全部剩余法力）或手牌内容发生变化。
            if after.my_last_mana != before.my_last_mana:
                return True
            before_ids = {getattr(card, "entity_id", None)
                          for card in before.my_hand_cards}
            after_ids = {getattr(card, "entity_id", None)
                         for card in after.my_hand_cards}
            return before_ids != after_ids
        if kind == "hero_power_changed":
            return (getattr(after.my_hero_power, "exhausted", 0)
                    != getattr(before.my_hero_power, "exhausted", 0)
                    or after.my_last_mana != before.my_last_mana)
        if kind == "choice_resolved":
            if (getattr(before, "discover_choice_count", None) in (1, 2, 3, 4)
                    and getattr(after, "discover_choice_count", None) is None):
                return True
            before_hand = tuple(
                getattr(card, "entity_id", None)
                for card in before.my_hand_cards)
            after_hand = tuple(
                getattr(card, "entity_id", None)
                for card in after.my_hand_cards)
            return before_hand != after_hand
        if kind == "location_changed":
            old = RecommendationFlow._entity_by_id(
                before.my_locations, source_entity_id)
            new = RecommendationFlow._entity_by_id(
                after.my_locations, source_entity_id)
            return new is None or (
                getattr(new, "action_cooldown", 0)
                != getattr(old, "action_cooldown", 0)
                or getattr(new, "damage", 0) != getattr(old, "damage", 0))
        if kind == "starship_launched":
            old = RecommendationFlow._entity_by_id(
                before.my_minions, source_entity_id)
            new = RecommendationFlow._entity_by_id(
                after.my_minions, source_entity_id)
            return old is not None and (
                new is None
                or getattr(new, "card_id", None)
                != getattr(old, "card_id", None))
        if kind == "ability_used":
            # 古神技能四个效果都一定看得见：扣蓝（领域/仆从要 2 费）、召唤
            # 随从、加 Buff、造成伤害/吸血。任一变化即算"技能真的结算了"。
            if (getattr(after, "my_last_mana", None)
                    != getattr(before, "my_last_mana", None)):
                return True
            for attr in ("my_minions", "oppo_minions"):
                if (RecommendationFlow._minion_stats(before, attr)
                        != RecommendationFlow._minion_stats(after, attr)):
                    return True
            return (getattr(getattr(after, "my_hero", None), "health", None)
                    != getattr(getattr(before, "my_hero", None), "health",
                               None))
        if kind == "combat_state_changed":
            old_source = RecommendationFlow._entity_by_id(
                before.my_minions, source_entity_id)
            new_source = RecommendationFlow._entity_by_id(
                after.my_minions, source_entity_id)
            old_target = RecommendationFlow._entity_by_id(
                before.oppo_minions, target_entity_id)
            new_target = RecommendationFlow._entity_by_id(
                after.oppo_minions, target_entity_id)
            return (new_source is None or new_target is None
                    or getattr(new_source, "exhausted", 0)
                    != getattr(old_source, "exhausted", 0)
                    or getattr(new_target, "damage", 0)
                    != getattr(old_target, "damage", 0))
        if kind == "hero_combat_state_changed":
            old_hero = getattr(before, "my_hero", None)
            new_hero = getattr(after, "my_hero", None)
            old_target = RecommendationFlow._entity_by_id(
                before.oppo_minions, target_entity_id)
            new_target = RecommendationFlow._entity_by_id(
                after.oppo_minions, target_entity_id)
            return (new_hero is None or new_target is None
                    or getattr(new_hero, "exhausted", 0)
                    != getattr(old_hero, "exhausted", 0)
                    or getattr(new_hero, "damage", 0)
                    != getattr(old_hero, "damage", 0)
                    or getattr(new_target, "damage", 0)
                    != getattr(old_target, "damage", 0))
        return False

    @staticmethod
    def _minion_stats(state, attr):
        """场上随从的 {实体 id: (伤害, 血量)}，用来判断技能有没有改场面。"""
        return {
            getattr(entity, "entity_id", None): (
                getattr(entity, "damage", None),
                getattr(entity, "health", None))
            for entity in (getattr(state, attr, ()) or ())
        }

    @staticmethod
    def _entity_by_id(collection, entity_id):
        return next((entity for entity in collection
                     if getattr(entity, "entity_id", None) == entity_id), None)
