"""Execute all validated mulligan slots and confirm immediately.

留牌意见有两个来源（由网页「基础配置 → 策略选择」决定），点击/校验/确认完全共用：

  * ``run()``       —— 盒子AI：读推荐面板 OCR 解析出的「替换N号位卡牌」（1 基）；
  * ``run_local()`` —— 本地卡组：对应卡组策略的 ``choose()``（0 基手牌下标）。
"""

from dataclasses import dataclass
from enum import Enum
import time


class MulliganStatus(str, Enum):
    CONFIRMED = "confirmed"
    CONCEDE = "concede"


@dataclass(frozen=True)
class MulliganResult:
    status: MulliganStatus
    selected_zero_based_slots: tuple[int, ...] = ()
    diagnostics: str = ""


# 换牌界面可点击的起手位：手牌数 -> 可替换张数。
# 先手 3 张（3 个位置都能换）；后手 4 张 + 幸运币 = 5 张，幸运币在最右侧、
# 不可替换，所以只有前 4 个位置点得到（与 click.STARTING_CARD_X 坐标表对应）。
CLICKABLE_STARTING_CARDS = {3: 3, 5: 4}


class MulliganFlow:
    def __init__(self, executor, action_supplier, state_supplier,
                 action_context=None, stopped=lambda: False,
                 sleep=time.sleep, pre_action_delay=0.0,
                 first_delay=None, retry_delay=None, confirm_ready=None,
                 local_chooser=None):
        self.executor = executor
        self.action_supplier = action_supplier
        self.state_supplier = state_supplier
        self.stopped = stopped
        self.sleep = sleep
        self.first_delay = first_delay if first_delay is not None else pre_action_delay
        self.retry_delay = retry_delay if retry_delay is not None else pre_action_delay
        # 换牌面板“确认”按钮是否就绪的检测回调（None = 不启用前置检测）。
        # 用于“先确认面板在再执行换牌”：按钮不在场就不点击，避免面板未
        # 就绪时盲点。典型实现见 FSM_action.confirm_button_present()。
        self.confirm_ready = confirm_ready
        # 本地卡组策略留牌（网页选本地卡组时走 run_local）：各卡组策略的
        # choose(state)，返回需要换掉的 0 基手牌下标。None = 没有可用策略，
        # 此时按“全部保留”提交（见 _local_selection）。
        self.local_chooser = local_chooser
        # 换牌是开局固定流程，**不接「活人感」**：提交后不再随机延时/看手牌
        # （用户实测反馈：换牌后对着手牌区乱晃既不像真人也没意义）。对局内的
        # 活人感只由 RecommendationFlow 的 post_action_pause 接管。
        self._delay_done = False
        # “没有可用策略”的提示只打一次，避免外层每轮重试都刷屏。
        self._local_chooser_logged = False
        if action_context is None:
            from contextlib import nullcontext
            action_context = nullcontext
        self.action_context = action_context

    def run(self):
        try:
            initial = self.state_supplier()
            action = self.action_supplier()
            current_action = self.action_supplier()
            fresh = self.state_supplier()
            if self.stopped():
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics="stopped")
            if self._identity(initial) != self._identity(fresh):
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics="hand_changed")
            # 换牌阶段 Power.log 会持续写入新行，log_revision 必然漂移；
            # 若要求 initial==fresh 的 revision 完全一致，会在“识别到点击”
            # 之前就频繁返回 revision_changed，导致换牌卡住。这里只要求
            # 手牌指纹与对局阶段稳定，不再要求日志版本号一致。
            if (getattr(fresh, "is_end", False)
                    or getattr(fresh, "game_num_turns_in_play", 0) != 0):
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics="mulligan_stage_changed")
            if (current_action.normalized_instruction
                    != action.normalized_instruction
                    or current_action.mulligan_slots != action.mulligan_slots):
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics="recommendation_changed")
            count = len(fresh.my_hand_cards)
            selected = tuple(sorted({slot - 1 for slot in action.mulligan_slots}))
            clickable_count = {3: 3, 5: 4}.get(count, 0)
            if count not in (3, 5) or any(
                    index < 0 or index >= clickable_count
                    for index in selected):
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics="mulligan_slot_invalid")
            # 前置门：确认按钮在场才执行换牌。按钮不在 → 说明面板未就绪
            # 或已提交，交给外层继续轮询，不盲目点击。
            if self.confirm_ready is not None and not self.confirm_ready():
                return MulliganResult(
                    MulliganStatus.CONCEDE,
                    diagnostics="confirm_button_absent")
            delay = self.retry_delay if self._delay_done else self.first_delay
            if delay > 0:
                print(f"已识别换牌建议，等待 {delay:.0f}s 后执行……")
                self.sleep(delay)
            self._delay_done = True
            with self.action_context():
                for index in selected:
                    self.executor.replace_starting_card(index, count)
                self.executor.commit_choose_card()
            return MulliganResult(MulliganStatus.CONFIRMED, selected)
        except Exception as exc:
            try:
                self.executor.cancel_click()
            except Exception:
                pass
            return MulliganResult(
                MulliganStatus.CONCEDE,
                diagnostics=f"{type(exc).__name__}:{exc}")

    def run_local(self, chooser=None):
        """本地卡组策略留牌：用卡组策略的 choose() 决定换哪几张。

        网页「基础配置 → 策略选择」选了本地卡组时由 FSM_action 调用，
        代替 run()（读盒子推荐面板 OCR）。两条路径只有“留牌意见从哪来”不同：

          * run()       —— OCR 盒子面板 → 解析出的 mulligan_slots（1 基）；
          * run_local() —— chooser(state)，各卡组策略的 choose()（0 基）。

        其余完全一致：手牌与对局阶段稳定 → 下标合法 → 确认按钮在场 → 逐个
        点击替换 + 提交确认。返回的 selected_zero_based_slots 语义也相同
        （0 基下标，直接对应点击位）。chooser 缺省用构造时传入的
        self.local_chooser；两者都没有时按“全部保留”提交。
        """
        try:
            chooser = chooser if chooser is not None else self.local_chooser
            # 本地路径不读盒子推荐面板，所以没有 action_supplier 的两次读取；
            # 但“点之前手牌必须稳定”这一条不能省：手牌还在变时按旧手牌算出
            # 来的下标可能指向别的牌。这里同样采样两次手牌做指纹比对。
            initial = self.state_supplier()
            fresh = self.state_supplier()
            if self.stopped():
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics="stopped")
            if self._identity(initial) != self._identity(fresh):
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics="hand_changed")
            # 与 run() 同口径：换牌阶段 Power.log 会持续写入新行，log_revision
            # 必然漂移，只要求手牌指纹与对局阶段稳定。
            if (getattr(fresh, "is_end", False)
                    or getattr(fresh, "game_num_turns_in_play", 0) != 0):
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics="mulligan_stage_changed")
            count = len(fresh.my_hand_cards)
            if count not in CLICKABLE_STARTING_CARDS:
                # 起手布局不是 3/5 张时点位的含义未知，不猜（下一轮再看）。
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics="mulligan_slot_invalid")
            selected = self._local_selection(chooser, fresh)
            clickable_count = CLICKABLE_STARTING_CARDS[count]
            if any(index < 0 or index >= clickable_count
                   for index in selected):
                # 卡组策略给出的位置在手里、却点不到（例如 5 张手牌的幸运币
                # 位）。宁可本轮不点、交给外层重试，也不能按错位置换错牌。
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics="mulligan_slot_invalid")
            failure = self._execute_selection(selected, count)
            if failure is not None:
                return MulliganResult(MulliganStatus.CONCEDE,
                                      diagnostics=failure)
            return MulliganResult(MulliganStatus.CONFIRMED, selected)
        except Exception as exc:
            try:
                self.executor.cancel_click()
            except Exception:
                pass
            return MulliganResult(
                MulliganStatus.CONCEDE,
                diagnostics=f"{type(exc).__name__}:{exc}")

    def _execute_selection(self, selected, count):
        """确认按钮门禁 + 延时 + 逐个替换 + 提交确认（本地留牌路径）。

        返回 None 表示已执行；返回字符串表示未执行的原因（交给外层重试）。
        换牌刻意**不接「活人感」**：提交后不做随机延时/手牌悬停（理由见
        __init__ 里的注释）。
        """
        # 前置门：确认按钮在场才执行换牌。按钮不在 → 说明面板未就绪
        # 或已提交，交给外层继续轮询，不盲目点击。
        if self.confirm_ready is not None and not self.confirm_ready():
            return "confirm_button_absent"
        delay = self.retry_delay if self._delay_done else self.first_delay
        if delay > 0:
            print(f"换牌方案已确定（换掉 {len(selected)} 张），"
                  f"等待 {delay:.0f}s 后执行……")
            self.sleep(delay)
        self._delay_done = True
        with self.action_context():
            for index in selected:
                self.executor.replace_starting_card(index, count)
            self.executor.commit_choose_card()
        return None

    def _local_selection(self, chooser, state):
        """把卡组策略 choose() 的返回值收敛成可点击的 0 基下标元组。

        choose() 只回答“想换哪几张”，能不能点到由执行层负责。这里先做本层
        过滤：去重、排序，丢掉非整数、不在当前手牌范围（0 基）内以及幸运币
        位置——宁可少换一张，也不能按错位置换错牌。返回的下标随后还会在
        run_local 里按 CLICKABLE_STARTING_CARDS 再校验一次（与盒子路径同一
        口径）：在手里但换牌界面点不到的位置直接判无效、本轮不点。

        chooser 为 None（没有可用策略）或执行抛异常时退化为“全部保留”：
        换牌界面绝不能因为策略问题把整局卡死，下一轮再试。
        """
        if chooser is None:
            if not self._local_chooser_logged:
                self._local_chooser_logged = True
                print("[本地留牌] 没有可用的卡组策略 choose()，本局全部保留。")
            return ()
        try:
            chosen = chooser(state) or ()
        except Exception as exc:
            # 策略抛异常不能把整局卡在换牌界面：退化为“全部保留”，下轮再试。
            print(f"[本地留牌] 卡组策略 choose() 执行失败，本局全部保留：{exc}")
            return ()
        hand = state.my_hand_cards
        selected = set()
        dropped = []
        for index in chosen:
            if (not isinstance(index, int)
                    or not 0 <= index < len(hand)
                    or getattr(hand[index], "is_coin", False)):
                dropped.append(index)
                continue
            selected.add(index)
        if dropped:
            print(f"[本地留牌] 忽略不可替换的位置（幸运币/越界）：{dropped}")
        return tuple(sorted(selected))

    def reset_delay(self):
        """每局换牌开始时调用：首次用 ready_delay，重试用 retry_delay。"""
        self._delay_done = False

    @staticmethod
    def _identity(state):
        return tuple((card.card_id, getattr(card, "entity_id", None))
                     for card in state.my_hand_cards)
