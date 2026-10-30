# -*- coding: utf-8 -*-
"""本地卡组策略留牌（MulliganFlow.run_local）回归测试。

网页「基础配置 → 策略选择」选本地卡组时，留牌意见来自卡组策略 ``choose()``
（0 基手牌下标），不再读盒子推荐面板 OCR。两条路径的执行语义必须一致：

    手牌稳定 → 对局阶段仍是换牌 → 下标点得到 → 确认按钮在场 → 逐个点击 + 提交

这里用假 executor 把“到底点了哪几个位置、有没有提交”钉死，顺带覆盖策略缺失/
抛异常（退化为全部保留，绝不卡死换牌界面）以及各种该重试的 CONCEDE 原因。
"""
import unittest
from types import SimpleNamespace

from src.flow.mulligan_flow import MulliganFlow, MulliganStatus


def _cards(count, prefix="CARD"):
    """起手手牌：只需 card_id + entity_id 就能算手牌指纹。"""
    return [SimpleNamespace(card_id=f"{prefix}_{index}", entity_id=str(index))
            for index in range(count)]


def _state(count, prefix="CARD", turns=0, is_end=False):
    return SimpleNamespace(
        my_hand_cards=_cards(count, prefix),
        log_revision=8,
        is_end=is_end,
        game_num_turns_in_play=turns,
    )


class _Supplier:
    """按顺序返回预置快照；用完后重复最后一个（模拟两次采样读到同一状态）。"""

    def __init__(self, *states):
        self._states = list(states)
        self._index = 0

    def __call__(self):
        value = self._states[min(self._index, len(self._states) - 1)]
        self._index += 1
        return value


class _Executor:
    """记录换牌点击顺序，不碰真实鼠标。"""

    def __init__(self):
        self.calls = []

    def replace_starting_card(self, index, count):
        self.calls.append(("replace", index, count))

    def commit_choose_card(self):
        self.calls.append(("commit",))

    def cancel_click(self):
        self.calls.append(("cancel",))


class LocalMulliganTests(unittest.TestCase):
    def _flow(self, executor, *states, chooser=None, confirm_ready=None,
              stopped=lambda: False):
        """本地路径的 flow：action_supplier 故意给 None（本地留牌不读 OCR）。"""
        return MulliganFlow(
            executor=executor,
            action_supplier=None,
            state_supplier=_Supplier(*states),
            stopped=stopped,
            sleep=lambda _seconds: None,
            confirm_ready=confirm_ready if confirm_ready is not None
            else (lambda: True),
            local_chooser=chooser,
        )

    def test_chooser_replaces_one_of_three_and_commits(self):
        executor = _Executor()
        flow = self._flow(executor, _state(3))

        result = flow.run_local(lambda _state: (1,))

        self.assertEqual(MulliganStatus.CONFIRMED, result.status)
        self.assertEqual((1,), result.selected_zero_based_slots)
        # 先点第 2 个位置，再提交确认；手牌数原样传给点击层。
        self.assertEqual([("replace", 1, 3), ("commit",)], executor.calls)

    def test_chooser_replaces_two_of_five(self):
        executor = _Executor()
        flow = self._flow(executor, _state(5))

        result = flow.run_local(lambda _state: (0, 3))

        self.assertEqual(MulliganStatus.CONFIRMED, result.status)
        self.assertEqual((0, 3), result.selected_zero_based_slots)
        # 5 张手牌只有前 4 个位置点得到，0/3 都在范围内。
        self.assertEqual(
            [("replace", 0, 5), ("replace", 3, 5), ("commit",)],
            executor.calls)

    def test_unclickable_index_is_invalid_and_never_clicked(self):
        executor = _Executor()
        flow = self._flow(executor, _state(5))

        # 第 5 个位置（0 基下标 4）是幸运币位，换牌界面点不到。
        result = flow.run_local(lambda _state: (4,))

        self.assertEqual(MulliganStatus.CONCEDE, result.status)
        self.assertEqual("mulligan_slot_invalid", result.diagnostics)
        self.assertEqual([], executor.calls)

    def test_missing_chooser_keeps_everything_but_still_commits(self):
        executor = _Executor()
        flow = self._flow(executor, _state(3))

        result = flow.run_local()

        self.assertEqual(MulliganStatus.CONFIRMED, result.status)
        self.assertEqual((), result.selected_zero_based_slots)
        # 一张都不换，也必须点“确认”把换牌阶段走完。
        self.assertEqual([("commit",)], executor.calls)

    def test_chooser_from_constructor_is_used_by_default(self):
        executor = _Executor()
        flow = self._flow(executor, _state(3), chooser=lambda _state: (2,))

        result = flow.run_local()

        self.assertEqual(MulliganStatus.CONFIRMED, result.status)
        self.assertEqual((2,), result.selected_zero_based_slots)
        self.assertEqual([("replace", 2, 3), ("commit",)], executor.calls)

    def test_chooser_exception_falls_back_to_keep_all(self):
        executor = _Executor()
        flow = self._flow(executor, _state(3))

        def chooser(_state):
            raise RuntimeError("策略内部错误")

        result = flow.run_local(chooser)

        # 策略炸了不能把整局卡在换牌界面：全部保留并提交。
        self.assertEqual(MulliganStatus.CONFIRMED, result.status)
        self.assertEqual((), result.selected_zero_based_slots)
        self.assertEqual([("commit",)], executor.calls)

    def test_confirm_button_absent_blocks_all_clicks(self):
        executor = _Executor()
        flow = self._flow(executor, _state(3), confirm_ready=lambda: False)

        result = flow.run_local(lambda _state: (1,))

        self.assertEqual(MulliganStatus.CONCEDE, result.status)
        self.assertEqual("confirm_button_absent", result.diagnostics)
        self.assertEqual([], executor.calls)

    def test_hand_changed_between_samples_blocks_clicks(self):
        executor = _Executor()
        flow = self._flow(executor, _state(3), _state(3, prefix="OTHER"))

        result = flow.run_local(lambda _state: (0,))

        self.assertEqual(MulliganStatus.CONCEDE, result.status)
        self.assertEqual("hand_changed", result.diagnostics)
        self.assertEqual([], executor.calls)

    def test_play_has_started_blocks_clicks(self):
        executor = _Executor()
        flow = self._flow(executor, _state(3, turns=1))

        result = flow.run_local(lambda _state: (0,))

        self.assertEqual(MulliganStatus.CONCEDE, result.status)
        self.assertEqual("mulligan_stage_changed", result.diagnostics)
        self.assertEqual([], executor.calls)

    def test_stopped_blocks_clicks(self):
        executor = _Executor()
        flow = self._flow(executor, _state(3), stopped=lambda: True)

        result = flow.run_local(lambda _state: (0,))

        self.assertEqual(MulliganStatus.CONCEDE, result.status)
        self.assertEqual("stopped", result.diagnostics)
        self.assertEqual([], executor.calls)


class LocalMulliganSelectionTests(unittest.TestCase):
    """_local_selection 的收敛规则（去重/排序/丢非法值）。"""

    def _flow(self):
        return MulliganFlow(
            executor=_Executor(),
            action_supplier=None,
            state_supplier=_Supplier(_state(3)),
            local_chooser=lambda _state: (),
        )

    def test_selection_is_deduped_and_sorted(self):
        flow = self._flow()
        state = _state(3)

        self.assertEqual((0, 2), flow._local_selection(
            lambda _state: (2, 0, 2, 0), state))

    def test_invalid_values_are_dropped_instead_of_clicked(self):
        flow = self._flow()
        state = _state(3)

        # 非整数、负数、超出当前手牌范围、幸运币位都不点。
        self.assertEqual((1,), flow._local_selection(
            lambda _state: (None, "2", -1, 7, 1), state))

    def test_coin_position_is_never_replaced(self):
        flow = self._flow()
        cards = _cards(5)
        cards[-1].is_coin = True
        state = SimpleNamespace(my_hand_cards=cards, is_end=False,
                                game_num_turns_in_play=0, log_revision=8)

        self.assertEqual((0,), flow._local_selection(
            lambda _state: (0, 4), state))


class LocalMulliganDelayTests(unittest.TestCase):
    """reset_delay 对本地路径同样有效：每局首次用 first_delay，重试用 retry_delay。"""

    def test_reset_delay_restores_the_per_game_first_delay(self):
        executor = _Executor()
        sleeps = []
        flow = MulliganFlow(
            executor=executor,
            action_supplier=None,
            state_supplier=_Supplier(_state(3)),
            sleep=sleeps.append,
            first_delay=1.0,
            retry_delay=5.0,
            confirm_ready=lambda: True,
            local_chooser=lambda _state: (0,),
        )

        flow.run_local()
        flow.run_local()          # 同一局的重试：用 retry_delay
        flow.reset_delay()        # 新的一局：回到 first_delay
        flow.run_local()

        self.assertEqual([1.0, 5.0, 1.0], sleeps)


if __name__ == "__main__":
    unittest.main()
