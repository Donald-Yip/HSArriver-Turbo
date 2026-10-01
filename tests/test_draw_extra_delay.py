# -*- coding: utf-8 -*-
"""抽牌额外延时：回合开始的常规抽 1 张不算，其余抽牌每张延时 1s。"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.flow.recommendation_flow import RecommendationFlow


class FakeClock:
    def __init__(self, now=10.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_flow(draw_extra=1.0):
    """只挂上抽牌记账需要的零件，其余用不到。"""
    from src.flow.recommendation_flow import _UNSET_TURN
    flow = object.__new__(RecommendationFlow)
    flow.draw_extra_delay_per_card = draw_extra
    flow._seen_entry_count = None
    flow._entry_turn = _UNSET_TURN
    flow._entry_turn_exempt_used = False
    flow._acted_this_turn = False
    flow.controller = SimpleNamespace(output=None)
    sleeps = []
    flow.sleep = sleeps.append
    return flow, sleeps


def state(entries, my_turn=True, turn=3):
    return SimpleNamespace(hand_entry_count=entries, is_my_turn=my_turn,
                           game_num_turns_in_play=turn)


class TurnStartDrawTests(unittest.TestCase):
    def test_first_observation_only_sets_the_baseline(self):
        flow, sleeps = make_flow()

        flow._account_hand_entries(state(10))

        self.assertEqual([], sleeps)

    def test_routine_single_turn_start_draw_is_not_delayed(self):
        flow, sleeps = make_flow()
        flow._account_hand_entries(state(10))

        flow._account_hand_entries(state(11))      # 回合开始常规抽 1 张

        self.assertEqual([], sleeps)

    def test_turn_start_draw_of_two_cards_is_delayed_for_the_extra_card(self):
        flow, sleeps = make_flow()
        flow._account_hand_entries(state(10))

        flow._account_hand_entries(state(12))      # 一次抽 2 张：多出来那 1 张要等

        self.assertEqual([1.0], sleeps)

    def test_turn_start_draw_of_three_cards_delays_two(self):
        flow, sleeps = make_flow()
        flow._account_hand_entries(state(10))

        flow._account_hand_entries(state(13))

        self.assertEqual([2.0], sleeps)

    def test_every_new_turn_gets_its_own_free_card(self):
        flow, sleeps = make_flow()
        flow._account_hand_entries(state(10, turn=3))
        flow._account_hand_entries(state(11, turn=3))     # 第 3 回合：免费

        flow._account_hand_entries(state(11, turn=4, my_turn=False))
        flow._account_hand_entries(state(12, turn=5))     # 第 5 回合：免费

        self.assertEqual([], sleeps)


class InTurnDrawTests(unittest.TestCase):
    def test_draw_triggered_mid_turn_delays_one_second_per_card(self):
        flow, sleeps = make_flow()
        flow._account_hand_entries(state(10))
        flow._account_hand_entries(state(11))             # 回合开始的免费那张
        flow._acted_this_turn = True                      # 我打出了一张牌

        flow._account_hand_entries(state(12))             # 这张牌抽了 1 张

        self.assertEqual([1.0], sleeps)

    def test_draw_caused_by_my_action_is_never_the_free_one(self):
        """回合开始那张还没写进日志时，出牌抽到的牌也要延时（不能顶替免费名额）。"""
        flow, sleeps = make_flow()
        flow._account_hand_entries(state(10))             # 基线
        flow._acted_this_turn = True                      # 先出牌

        flow._account_hand_entries(state(11))             # 出牌抽到的 1 张

        self.assertEqual([1.0], sleeps)

    def test_mid_turn_draw_of_two_cards_delays_two_seconds(self):
        flow, sleeps = make_flow()
        flow._account_hand_entries(state(10))
        flow._account_hand_entries(state(11))
        flow._acted_this_turn = True

        flow._account_hand_entries(state(13))

        self.assertEqual([2.0], sleeps)

    def test_delay_scales_with_the_configured_seconds(self):
        flow, sleeps = make_flow(draw_extra=0.5)
        flow._account_hand_entries(state(10))
        flow._account_hand_entries(state(11))
        flow._acted_this_turn = True

        flow._account_hand_entries(state(13))

        self.assertEqual([1.0], sleeps)

    def test_zero_setting_disables_the_extra_delay(self):
        flow, sleeps = make_flow(draw_extra=0.0)
        flow._account_hand_entries(state(10))
        flow._account_hand_entries(state(11))
        flow._acted_this_turn = True

        flow._account_hand_entries(state(13))

        self.assertEqual([], sleeps)

    def test_extra_delay_is_announced_to_the_overlay(self):
        flow, sleeps = make_flow()
        output = []
        flow.controller = SimpleNamespace(output=output.append)
        flow._account_hand_entries(state(10))
        flow._account_hand_entries(state(11))
        flow._acted_this_turn = True

        flow._account_hand_entries(state(12))

        self.assertEqual([1.0], sleeps)
        self.assertEqual(1, len(output))
        self.assertIn("抽到 1 张牌", output[0])
        self.assertIn("延时", output[0])

    def test_delay_is_announced_exactly_once(self):
        """回归：print 与 controller.output 各推一次，浮窗会看到两条一样的日志。

        ManualController.output 默认就是 print，而 web_ui 用 _TeeStream 把 stdout
        转进日志浮窗，所以 _announce 只能走一个通道。
        """
        flow, sleeps = make_flow()
        output = []
        printed = []
        flow.controller = SimpleNamespace(output=output.append)
        flow._account_hand_entries(state(10))
        flow._account_hand_entries(state(11))
        flow._acted_this_turn = True

        with patch("builtins.print",
                   side_effect=lambda *a, **k: printed.append(a)):
            flow._account_hand_entries(state(13))

        self.assertEqual([2.0], sleeps)
        self.assertEqual(1, len(output) + len(printed))
        self.assertEqual(1, len(output))
        self.assertEqual([], printed)

    def test_announce_falls_back_to_print_without_an_output_hook(self):
        flow, _sleeps = make_flow()
        flow.controller = SimpleNamespace(output=None)
        printed = []

        with patch("builtins.print",
                   side_effect=lambda *a, **k: printed.append(a)):
            flow._announce("[SYS] 测试")

        self.assertEqual(1, len(printed))

    def test_missing_output_hook_is_not_fatal(self):
        flow, sleeps = make_flow()
        flow.controller = None

        flow._account_hand_entries(state(10))
        flow._account_hand_entries(state(11))
        flow._acted_this_turn = True

        flow._account_hand_entries(state(12))

        self.assertEqual([1.0], sleeps)


class OpponentAndResetTests(unittest.TestCase):
    def test_opponent_turn_draws_are_ignored(self):
        flow, sleeps = make_flow()
        flow._account_hand_entries(state(10))
        flow._account_hand_entries(state(11))

        flow._account_hand_entries(state(12, my_turn=False))

        self.assertEqual([], sleeps)

    def test_counter_reset_starts_a_fresh_game(self):
        flow, sleeps = make_flow()
        flow._account_hand_entries(state(30, turn=9))
        flow._account_hand_entries(state(31, turn=9))

        flow._account_hand_entries(state(0, turn=1))       # 换局，计数清零
        flow._account_hand_entries(state(1, turn=1))       # 新局回合开始的免费那张

        self.assertEqual([], sleeps)

    def test_state_without_turn_number_still_exempts_one_card(self):
        flow, sleeps = make_flow()
        flow._account_hand_entries(state(10, turn=None))

        flow._account_hand_entries(state(11, turn=None))

        self.assertEqual([], sleeps)

    def test_missing_hand_entry_count_is_treated_as_zero(self):
        flow, sleeps = make_flow()

        flow._account_hand_entries(SimpleNamespace(is_my_turn=True))
        flow._account_hand_entries(state(1))

        self.assertEqual([], sleeps)


class FlowIntegrationTests(unittest.TestCase):
    """整条流程跑一遍：出牌后抽到 2 张 → 真的多等 2s（守住调用点）。"""

    @staticmethod
    def _frame(frame_id, exact_hash):
        from src.recommendation_models import FrameEvidence
        return FrameEvidence(
            frame_id=frame_id, captured_at=0.0, desktop_size=(1920, 1080),
            dpi=96, window_handle=1, foreground=True,
            recommendation_roi=(7, 32, 278, 970), exact_hash=exact_hash,
            perceptual_hash="0" * 64, panel_visible=True)

    def _step(self, entries_after):
        from manual_controller import ActionExecutionResult
        from src.game_state.recommendation_adapter import adapt_action
        from src.parser.recommendation_parser import RecommendationParser
        from src.recommendation_models import OcrEvidence
        from src.safety.recommendation_validator import RecommendationValidator

        def evidence(frame_id, text):
            return OcrEvidence(
                frame_id=frame_id, created_at=0.0, lines=(),
                normalized_text=text, confidence=0.90, backend="test",
                preprocessing="test")

        class Capture:
            def __init__(self):
                self.frames = iter((self._frame("stable", "before"),
                                    self._frame("current", "before"),
                                    self._frame("after", "after")))

            @staticmethod
            def _frame(frame_id, exact_hash):
                return FlowIntegrationTests._frame(frame_id, exact_hash)

            def capture(self, ocr_panel_ok=False):
                return next(self.frames)

            @staticmethod
            def crop_recommendation(current_frame):
                return current_frame

        class Reader:
            def __init__(self):
                self.read_count = 0

            def read(self, frame_supplier, _roi_supplier):
                current = frame_supplier()
                self.read_count += 1
                if self.read_count == 1:
                    return evidence(current.frame_id, "结束回合")
                return evidence(current.frame_id, "等待对手操作")

            @staticmethod
            def read_frame(current, _roi_supplier):
                return evidence(current.frame_id, "结束回合")

        clock = FakeClock()
        active = state(10)
        after = state(entries_after)
        states = iter(((active, 10), (active, 10), (after, 11), (after, 11)))
        events = []

        def sleep(seconds):
            if seconds > 0:
                events.append(("sleep", round(seconds, 3)))
            clock.advance(seconds)

        class Controller:
            output = None

            @staticmethod
            def execute(_action, _state):
                events.append(("execute",))
                return ActionExecutionResult(True, "executed")

        flow = RecommendationFlow(
            capture=Capture(), reader=Reader(),
            parser=RecommendationParser(),
            state_supplier=lambda: next(states),
            adapter=adapt_action, validator=RecommendationValidator(),
            controller=Controller(), sleep=sleep, clock=clock,
            draw_extra_delay_per_card=1.0)

        return flow.run_player_turn_step(), events

    def test_draw_of_two_cards_after_playing_waits_two_extra_seconds(self):
        result, events = self._step(entries_after=12)

        self.assertEqual("executed", result.status.value)
        self.assertEqual([("execute",), ("sleep", 2.0)], events)

    def test_single_extra_card_waits_one_extra_second(self):
        result, events = self._step(entries_after=11)

        self.assertEqual("executed", result.status.value)
        self.assertEqual([("execute",), ("sleep", 1.0)], events)

    def test_no_draw_means_no_extra_wait(self):
        result, events = self._step(entries_after=10)

        self.assertEqual("executed", result.status.value)
        self.assertEqual([("execute",)], events)


if __name__ == "__main__":
    unittest.main()
