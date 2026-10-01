"""Execute exactly one validated recommendation and verify its result."""

from dataclasses import dataclass
from enum import Enum
import time

from src.flow.hand_animation_delay import HandAnimationDelay
from src.recommendation_models import ActionKind
from src.safety.recommendation_validator import ConsumedActionStore


# 「回合号还没记录过」的哨兵：不能用 None，因为有些状态里回合号就是 None。
_UNSET_TURN = object()


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
                 draw_extra_delay_per_card=0.0):
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
        # 「活人感」（可选）：盒子意见执行完后的随机延时 + 鼠标在手牌区悬停。
        # 返回 True 表示已接管本次延时，就不再叠加固定的 post_action_delay。
        # 只有走到这里的“识别盒子意见并执行完”的对局动作才会触发它。
        self.post_action_pause = post_action_pause
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

    def run_player_turn_step(self):
        try:
            state, revision = self.state_supplier()
            self.hand_animation_delay.observe(
                getattr(state, "hand_entry_count", 0))
            # 回合开始的常规抽 1 张不算；这里处理“抽了 2 张以上”和上一步动作
            # 触发、日志晚几拍才写出来的抽牌。
            self._account_hand_entries(state)
            if not state.is_my_turn:
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
            proposed = self.parser.parse(
                evidence, state.game_num_turns_in_play, revision)
            if proposed.action == ActionKind.MULLIGAN:
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
                return FlowStepResult(
                    FlowStepStatus.RETRY, "recommendation_changed")
            validation = self.validator.validate(
                proposed, adapted, stable_frame, current_frame,
                current_evidence,
                fresh_state, fresh_revision, "PLAYER_TURN", self.consumed,
                self.stopped())
            if not validation.accepted:
                return FlowStepResult(FlowStepStatus.RETRY, validation.code)
            adapted, key = validation.value
            print(f"[推荐] {proposed.normalized_instruction}")
            print("[执行] 开始点击。")
            result = self.controller.execute(adapted.manual_action, fresh_state)
            if not result.executed or result.recovery_needed:
                return FlowStepResult(FlowStepStatus.RETRY,
                                      "execution_failed")
            # 本回合已经动过手：之后再出现的手牌增加都是“操作触发的抽牌”，
            # 不能再当成回合开始那张免费的。
            self._acted_this_turn = True
            # 操作结束后延时再开始下一轮截图+OCR（盒子更新面板留时间）。
            # 通过 controller.output 推送延时行，让浮窗底部计时表显示该延时。
            # 「活人感」开启时由 post_action_pause 接管（随机 0.5~3s + 手牌悬停），
            # 此时不再叠加固定延时。
            paused = False
            if self.post_action_pause is not None:
                try:
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
                return FlowStepResult(
                    FlowStepStatus.OBSERVE,
                    "waiting_recommendation_update")
            return FlowStepResult(
                FlowStepStatus.RETRY, "result_not_confirmed")
        except Exception as exc:
            return FlowStepResult(
                FlowStepStatus.RETRY,
                f"{type(exc).__name__}:{exc}")

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
    def _entity_by_id(collection, entity_id):
        return next((entity for entity in collection
                     if getattr(entity, "entity_id", None) == entity_id), None)
