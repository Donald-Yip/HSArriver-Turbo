"""Execute exactly one validated recommendation and verify its result."""

from dataclasses import dataclass
from enum import Enum
import inspect
import time

from src.flow.hand_animation_delay import HandAnimationDelay
from src.recommendation_models import ActionKind
from src.safety.recommendation_validator import ConsumedActionStore


# 「回合号还没记录过」的哨兵：不能用 None，因为有些状态里回合号就是 None。
_UNSET_TURN = object()


# 同一条推荐连续失败这么多次就熔断：不再反复点同一条，打一条带失败码的
# 警告后只观察（等盒子/回合变化再继续）。以前是无限重试，线上反馈的“卡住”
# 就是一条读不懂/点不动的推荐被重试到烧绳。
SAME_RECOMMENDATION_RETRY_LIMIT = 10


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
    RETRY = "retry"


@dataclass(frozen=True)
class FlowStepResult:
    status: FlowStepStatus
    diagnostics: str = ""


class RecommendationFlow:
    def __init__(self, capture, reader, parser, state_supplier, adapter,
                 validator, controller, sleep=time.sleep,
                 clock=time.monotonic,
                 result_timeout=5.0, stopped=lambda: False,
                 consumed=None, post_action_delay=0.0,
                 post_action_pause=None,
                 hand_animation_delay=None,
                 draw_extra_delay_per_card=0.0,
                 same_recommendation_retry_limit=None):
        self.capture = capture
        self.reader = reader
        self.parser = parser
        self.state_supplier = state_supplier
        self.adapter = adapter
        self.validator = validator
        self.controller = controller
        self.sleep = sleep
        self.clock = clock
        # Kept for constructor compatibility; result verification no longer
        # waits for a timeout window, it checks once and retries immediately.
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
        # 同一条推荐连续失败计数：连续失败达到上限就熔断（见 _retry）。
        self.same_recommendation_retry_limit = (
            SAME_RECOMMENDATION_RETRY_LIMIT
            if same_recommendation_retry_limit is None
            else max(1, int(same_recommendation_retry_limit)))
        self._retry_signature = None
        self._retry_count = 0
        self._stuck_signature = None
        # 当前这一步的“推荐指纹”，失败时用来判断是不是同一条推荐。
        self._step_signature = None
        self._step_turn = None
        self._step_parse_failed = False
        # 最近一次已经打到日志里的 OCR 原文：同一段原文只留一次，
        # 重试几十次时不再刷屏（见 _log_ocr_raw）。
        self._last_logged_ocr_text = None

    # -------------------------------------------------- 熔断：同一条推荐反复失败
    def _note_step_recommendation(self, proposed):
        """记下这一步“是哪条推荐”：动作 + 目标 + 回合（回合变了重新计数）。"""
        self._step_signature = (
            "rec",
            self._step_turn,
            str(getattr(proposed, "normalized_instruction", "") or ""),
            repr(getattr(proposed, "target", None)),
        )
        # 面板重新读通 = 上一段“读不懂”的插曲结束：允许下一段插曲再留一次
        # 原文（否则同一段原文会在之后的反馈里被去重掉、raw 又变空的）。
        self._last_logged_ocr_text = None

    def _clear_retry_tracking(self):
        self._retry_signature = None
        self._retry_count = 0
        self._stuck_signature = None

    def _is_stuck_recommendation(self):
        """当前这条推荐已经熔断过了吗（是就别再点）。"""
        return (self._stuck_signature is not None
                and self._step_signature == self._stuck_signature)

    def _retry(self, code):
        """一次失败：同一条推荐连续失败到上限就熔断，只观察不再重复点。

        熔断后返回 OBSERVE（不是 RETRY），上层就不再执行同一条推荐；等盒子
        推荐文本或回合变化后自动恢复（指纹变了计数清零）。熔断那一刻打一条
        带失败码的 WARN，方便下一条反馈直接定位是哪一行读不懂。
        """
        signature = self._step_signature
        if signature is None:
            if self._step_parse_failed:
                # 解析失败时拿不到 ProposedAction，按「回合」累计“读不懂面板”。
                # 不把失败码算进指纹：面板闪烁时失败码会在
                # unsupported_spell_target / ambiguous_actions 之间跳，每跳一次
                # 就清零的话熔断永远触发不了（2026-10-27 反馈连试 82 次、43 秒）。
                # 也不能拿 OCR 原文当指纹：OCR 在「已方/己方」上抖一下就会清零。
                signature = ("parse", self._step_turn)
            else:
                # 读推荐本身失败（面板读不稳/状态取不到）：按失败码区分。
                signature = ("fail", self._step_turn, str(code))
        if signature != self._retry_signature:
            self._retry_signature = signature
            self._retry_count = 0
            self._stuck_signature = None
        self._retry_count += 1
        if self._retry_count >= self.same_recommendation_retry_limit:
            if self._stuck_signature != signature:
                self._stuck_signature = signature
                self._warn_stuck(code)
            return FlowStepResult(FlowStepStatus.OBSERVE,
                                  "recommendation_stuck")
        return FlowStepResult(FlowStepStatus.RETRY, code)

    def _warn_stuck(self, code):
        """熔断告警（只打一次）：回合、失败次数、失败码、推荐指令。

        OCR 原文由 `_log_ocr_raw` 单独留痕（`[推荐] 原文：` 前缀才会被
        battle_record 归到 recommendation_raw、进反馈包的 raw 字段），
        这里不再重复打，也不带 `[推荐]` 前缀，避免被当成一条新推荐指令。
        """
        instruction = ""
        if self._step_signature and len(self._step_signature) > 2:
            instruction = str(self._step_signature[2] or "")
        message = (f"回合 {self._step_turn} 同一条推荐连续失败 "
                   f"{self._retry_count} 次（{code}），本回合不再重试、"
                   f"只等盒子/回合变化")
        if instruction:
            message += f"；推荐：{instruction}"
        try:
            from print_info import warn_print
        except Exception:
            warn_print = print
        try:
            warn_print(message)
        except Exception:
            pass

    def _log_ocr_raw(self, text):
        """把这一次读到的盒子面板原文留痕（失败时用）。

        前缀必须是 `[推荐] 原文：`：battle_record 只把这种行归成
        recommendation_raw，web_ui 才会把它放进反馈上下文（错误.json 的
        `raw`）。之前解析失败打的是 `[推荐] OCR原文：`，会被当成一条**新的
        推荐指令**，于是反馈包里反而一条原文都没有（2026-10-27 反馈的
        `"raw": []` 就是这么来的）。

        同一段原文只打一次：面板没变时会连续重试几十次，重复打印只会刷屏。
        面板重新读通（`_note_step_recommendation`）后清零，下一次“读不懂”
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

    def run_player_turn_step(self):
        # 每一步都重建“这条推荐是谁”的指纹；失败时 _retry 用它判断是不是同一条。
        self._step_signature = None
        self._step_turn = None
        self._step_parse_failed = False
        try:
            state, revision = self.state_supplier()
            self._step_turn = getattr(state, "game_num_turns_in_play", None)
            self.hand_animation_delay.observe(
                getattr(state, "hand_entry_count", 0))
            # 回合开始的常规抽 1 张不算；这里处理“抽了 2 张以上”和上一步动作
            # 触发、日志晚几拍才写出来的抽牌。
            self._account_hand_entries(state)
            if not state.is_my_turn:
                self._clear_retry_tracking()
                return FlowStepResult(FlowStepStatus.OBSERVE, "opponent_turn")
            if (self.waiting_turn_number is not None
                    and self.waiting_turn_number
                    != state.game_num_turns_in_play):
                self._clear_waiting()
            observed = {}
            def frame_supplier():
                observed["frame"] = self.capture.capture(ocr_panel_ok=True)
                return observed["frame"]
            evidence = self.reader.read(
                frame_supplier, self.capture.crop_recommendation)
            stable_frame = observed["frame"]
            if self.waiting_instruction is not None:
                self._clear_waiting()
            try:
                proposed = self.parser.parse(
                    evidence, state.game_num_turns_in_play, revision)
            except Exception:
                # 解析失败也把 OCR 原文留痕（[推荐] 原文： → 进反馈包的 raw）。
                # 2026-10-24「目标是已方2号位」、2026-10-27「目标是已方1号位」
                # 都是靠这段原文才定位到 OCR 把「己」认成「已」的。
                self._step_parse_failed = True
                self._log_ocr_raw(getattr(evidence, "normalized_text", ""))
                raise
            # 先留痕再适配：适配抛错时也要能看到"盒子到底写了什么"。线上出现过
            # 连续 3090 次 choose_one_name_not_unique 却一条原文都没留下的情况，
            # 就是因为这行原来打在适配之后。
            self._note_step_recommendation(proposed)
            if self._is_stuck_recommendation():
                # 熔断过的那条又出现了：这次一下都不点，等盒子/回合变化。
                # （以前熔断只把状态改成 OBSERVE，但每次还会重新执行一遍。）
                return FlowStepResult(
                    FlowStepStatus.OBSERVE, "recommendation_stuck")
            print(f"[推荐] {proposed.normalized_instruction}")
            raw_text = getattr(proposed, "raw_instruction", "") or ""
            if raw_text and raw_text != proposed.normalized_instruction:
                for line in raw_text.splitlines():
                    if line.strip():
                        print(f"[推荐] 原文：{line.strip()}")
            if proposed.action == ActionKind.MULLIGAN:
                self._clear_retry_tracking()
                return FlowStepResult(
                    FlowStepStatus.OBSERVE,
                    "stale_mulligan_recommendation")
            adapted = self.adapter(proposed, state)
            fresh_state, fresh_revision = self._wait_for_hand_animation()
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
            print("[执行] 开始点击。")
            result = self.controller.execute(adapted.manual_action, fresh_state)
            if not result.executed or result.recovery_needed:
                return self._retry("execution_failed")
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
            # 动作执行后立刻查一次手牌有没有变多（这个操作触发了抽牌）：
            # 查到了就按张数追加延时；还没写进日志就留到下一步再查。
            # 关闭该功能（0）时不做这次额外读取，保持原有节奏。
            if self.draw_extra_delay_per_card > 0:
                self._account_hand_entries(self.state_supplier()[0])
            verified = self._verify_result(
                proposed, adapted, current_frame,
                fresh_state, fresh_revision)
            if verified == ResultStatus.COMPLETED:
                self.consumed.mark_consumed(key)
                self._clear_retry_tracking()
                return FlowStepResult(FlowStepStatus.EXECUTED)
            if verified == ResultStatus.WAITING_RECOMMENDATION:
                # A discover click is not complete while Power.log still
                # exposes a choice UI.  Keep it retryable until that UI
                # closes; all other confirmed actions retain duplicate
                # execution protection.
                if proposed.action != ActionKind.CHOOSE_DISCOVER:
                    self.consumed.mark_consumed(key)
                self.waiting_instruction = proposed.normalized_instruction
                self.waiting_panel_hash = current_frame.perceptual_hash
                self.waiting_turn_number = proposed.turn_number
                self.waiting_action_kind = proposed.action
                self._clear_retry_tracking()
                return FlowStepResult(
                    FlowStepStatus.OBSERVE,
                    "waiting_recommendation_update")
            return self._retry("result_not_confirmed")
        except Exception as exc:
            return self._retry(f"{type(exc).__name__}:{exc}")

    def _wait_for_hand_animation(self):
        """等待手牌入场动画完成，再取最新状态做校验（上游性能调优逻辑）。"""
        while True:
            state, revision = self.state_supplier()
            self.hand_animation_delay.observe(
                getattr(state, "hand_entry_count", 0))
            remaining = self.hand_animation_delay.remaining()
            if remaining <= 0:
                return state, revision
            self.sleep(remaining)

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
                       before_revision):
        """Check the result once right after executing; no timeout waiting.

        A changed recommendation text confirms completion. A discover
        action or a satisfied log postcondition waits for the next
        recommendation. Anything else returns RETRY immediately so the
        caller re-executes the same action with fresh evidence instead of
        waiting for a result window.
        """
        before_instruction = proposed.normalized_instruction
        after_state, after_revision = self.state_supplier()
        log_changed = (after_revision > before_revision
                       and self._postcondition(
                           adapted.postcondition, before_state, after_state,
                           adapted.source_entity_id,
                           adapted.target_entity_id))
        recommendation_changed = False
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
        if proposed.action == ActionKind.CHOOSE_DISCOVER:
            return ResultStatus.WAITING_RECOMMENDATION
        if proposed.action == ActionKind.SELECT_TARGET:
            # 选项结算后的“点目标”：点一下游戏就会继续结算，面板文本可能
            # 稍后才更新。和发现一样先 WAITING，避免同一目标被连续重点。
            return ResultStatus.WAITING_RECOMMENDATION
        if proposed.action == ActionKind.USE_ABILITY:
            # 古神技能：结算一定有日志痕迹（伤害/召唤/加 Buff/扣蓝）。
            # 日志一点没动 = 面板没弹或点空了，这时重试是安全的；日志动了就
            # 当已发动、标记 consumed 等盒子更新 —— 绝不立刻重复点同一个随从
            # （技能用过后它就是个普通随从了，再点它可能变成一次攻击）。
            return (ResultStatus.WAITING_RECOMMENDATION if log_changed
                    else ResultStatus.RETRY)
        if log_changed:
            return ResultStatus.WAITING_RECOMMENDATION
        return ResultStatus.RETRY

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
