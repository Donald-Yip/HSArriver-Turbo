# -*- coding: utf-8 -*-
"""重试前刷新全部信息：不再拿旧场面重算动作。

用户 2026-10-31 反馈：「有的时候对场面的认识还是会出错，每次操作重试前，都要
刷新各个信息，然后再重试，而不是一直用之前的信息」。本文件锁住盒子AI路径的三处
修复（R18I）：

1. 面板读取/解析之后先 ``_wait_for_hand_animation()`` 拿刷新后的 state，再调
   ``adapter``；校验和执行用的也是同一份 state（以前 adapter 用入口旧 state）；
2. 待确认超时重试前先刷新日志状态并重判 postcondition：动作其实已生效时只标记
   已消费，不再点第二次；
3. ``_retry`` 对状态类失败码（revision_changed / duplicate_board_position /
   turn_changed）先等日志 revision 前进，并清掉 OCR 帧缓存强制重读。
"""

import unittest
from types import SimpleNamespace

from manual_controller import ActionExecutionResult, EndTurnAction
from hslegend.application.recommendation_flow import (
    RETRY_REFRESH_TIMEOUT,
    FlowStepStatus,
    RecommendationFlow,
)
from src.game_state.recommendation_adapter import AdaptedAction
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_models import FrameEvidence, OcrEvidence


def frame(frame_id):
    return FrameEvidence(
        frame_id=frame_id, captured_at=0.0, desktop_size=(1920, 1080),
        dpi=96, window_handle=1, foreground=True,
        recommendation_roi=(7, 32, 278, 970), exact_hash=frame_id,
        perceptual_hash="0" * 64, panel_visible=True)


class CyclingCapture:
    def __init__(self):
        self.count = 0

    def capture(self, ocr_panel_ok=False):
        self.count += 1
        return frame(f"f{self.count}")

    @staticmethod
    def crop_recommendation(current_frame):
        return current_frame


class FixedReader:
    """盒子面板一直显示「结束回合」；同时记录 clear_cache 调用次数。"""

    def __init__(self, text="结束回合"):
        self.text = text
        self.cleared = 0

    def read(self, frame_supplier, _roi):
        current = frame_supplier()
        return self._evidence(current.frame_id)

    def read_frame(self, current_frame, _roi):
        return self._evidence(current_frame.frame_id)

    def clear_cache(self):
        self.cleared += 1

    def _evidence(self, frame_id):
        return OcrEvidence(
            frame_id=frame_id, created_at=0.0, lines=(),
            normalized_text=self.text, confidence=0.95,
            backend="test", preprocessing="test")


class EndTurnAdapter:
    def __init__(self):
        self.states = []

    def __call__(self, _proposed, state):
        self.states.append(state)
        return AdaptedAction(EndTurnAction(), None, None, "turn_changed")


class KeyedValidator:
    """和 RecommendationValidator 一样用 consumed key 拒绝重复消费。"""

    def __init__(self):
        self.states = []

    def validate(self, proposed, adapted, *args):
        consumed = args[6]
        key = (proposed.turn_number, proposed.normalized_instruction,
               adapted.source_entity_id, adapted.target_entity_id,
               adapted.postcondition)
        if consumed.contains(key):
            return SimpleNamespace(accepted=False, code="already_consumed")
        return SimpleNamespace(accepted=True, value=(adapted, key))


class TrackingValidator(KeyedValidator):
    def validate(self, proposed, adapted, *args):
        self.states.append(args[3])   # fresh_state
        return super().validate(proposed, adapted, *args)


class RecordingController:
    """执行成功但不改日志（模拟“点击被丢掉/日志写得慢”）。"""

    def __init__(self):
        self.executed = []
        self.executed_states = []
        self.outputs = []

    def output(self, message):
        self.outputs.append(message)

    def execute(self, action, state):
        self.executed.append(action)
        self.executed_states.append(state)
        return ActionExecutionResult(True, "executed")


class FakeClock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


def active_state(turn=5, tag="active"):
    state = SimpleNamespace(
        is_my_turn=True, game_num_turns_in_play=turn, hand_entry_count=0,
        my_hand_cards=[], my_minions=[], oppo_minions=[],
        my_locations=[], oppo_locations=[])
    state.tag = tag
    return state


def ended_state(turn=6, tag="ended"):
    state = active_state(turn, tag)
    state.is_my_turn = False
    return state


class SnapshotSupplier:
    """每次调用返回一个新的状态快照（模拟真实 state_supplier 的拷贝语义）。"""

    def __init__(self, revision=10):
        self.revision = revision
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return active_state(tag=f"snap-{self.calls}"), self.revision


class CatchUpSupplier:
    """前 N 次调用返回旧 revision，之后日志“追上”到新 revision。"""

    def __init__(self, catch_up_in=None):
        self.state = active_state()
        self.revision = 10
        self.catch_up_in = catch_up_in

    def __call__(self):
        if self.catch_up_in is not None:
            if self.catch_up_in <= 0:
                self.state = ended_state()
                self.revision = 11
            self.catch_up_in -= 1
        return self.state, self.revision


def build_flow(reader, adapter, validator, supplier, controller,
               clock=None, sleep=None, stopped=None):
    kwargs = {}
    if stopped is not None:
        kwargs["stopped"] = stopped
    return RecommendationFlow(
        capture=CyclingCapture(),
        reader=reader,
        parser=RecommendationParser(),
        state_supplier=supplier,
        adapter=adapter,
        validator=validator,
        controller=controller,
        sleep=sleep if sleep is not None else (lambda _seconds: None),
        clock=clock or FakeClock(),
        **kwargs)


class AdaptWithRefreshedStateTests(unittest.TestCase):
    def test_adapter_and_validator_use_the_state_read_after_the_panel(self):
        """入口 state A、刷新后 state B：adapter/validator 都必须收到 B。"""
        supplier = SnapshotSupplier()
        adapter = EndTurnAdapter()
        validator = TrackingValidator()
        flow = build_flow(FixedReader(), adapter, validator, supplier,
                          RecordingController())

        flow.run_player_turn_step()

        # supplier 第 1 次是入口快照（snap-1），第 2 次是面板后刷新（snap-2）。
        self.assertEqual(1, len(adapter.states))
        self.assertEqual("snap-2", adapter.states[0].tag)
        self.assertIs(adapter.states[0], validator.states[0])


class RetryAfterRefreshTests(unittest.TestCase):
    def test_timeout_with_action_already_applied_does_not_click_twice(self):
        clock = FakeClock()
        supplier = CatchUpSupplier()
        controller = RecordingController()
        sleeps = []
        flow = build_flow(FixedReader(), EndTurnAdapter(), KeyedValidator(),
                          supplier, controller, clock=clock,
                          sleep=sleeps.append)

        first = flow.run_player_turn_step()
        self.assertEqual(FlowStepStatus.OBSERVE, first.status)
        self.assertEqual(1, len(controller.executed))
        self.assertIsNotNone(flow.pending_action)

        # 3s 未确认；但日志这时追上了：动作其实已经生效。
        clock.now += 3.0
        supplier.catch_up_in = 3
        sleeps.clear()
        controller.outputs.clear()
        second = flow.run_player_turn_step()

        self.assertEqual(FlowStepStatus.OBSERVE, second.status)
        self.assertEqual("waiting_recommendation_update", second.diagnostics)
        self.assertEqual(1, len(controller.executed))     # 没有再点第二次
        self.assertIsNone(flow.pending_action)
        self.assertEqual([0.2], sleeps)                   # 等了一拍日志
        self.assertTrue(any("重试前刷新：日志 revision 10→11" in line
                            for line in controller.outputs))
        self.assertFalse(any("重新执行" in line for line in controller.outputs))

    def test_timeout_without_progress_retries_once_with_the_refreshed_state(self):
        clock = FakeClock()
        supplier = SnapshotSupplier()
        adapter = EndTurnAdapter()
        controller = RecordingController()
        sleeps = []
        flow = build_flow(FixedReader(), adapter, KeyedValidator(), supplier,
                          controller, clock=clock, sleep=sleeps.append)

        flow.run_player_turn_step()
        self.assertEqual(1, len(controller.executed))
        self.assertIsNotNone(flow.pending_action)

        clock.now += 3.0
        sleeps.clear()
        result = flow.run_player_turn_step()

        self.assertEqual(FlowStepStatus.OBSERVE, result.status)
        self.assertEqual("awaiting_action_confirmation", result.diagnostics)
        self.assertEqual(2, len(controller.executed))     # 只重执行一次
        self.assertEqual(1, flow.pending_action.attempts)
        self.assertEqual([0.2] * 8, sleeps)               # 等满 1.5s 上限
        # 重执行用的 state 就是刷新后最新那份，而不是入口快照。
        self.assertIs(adapter.states[1], controller.executed_states[1])
        self.assertIsNot(controller.executed_states[0],
                         controller.executed_states[1])
        # 第一次执行用的也是面板后刷新的 snap-2（不是入口 snap-1）。
        self.assertEqual("snap-2", controller.executed_states[0].tag)


class RetryRefreshTests(unittest.TestCase):
    def test_revision_changed_waits_for_the_revision_to_advance(self):
        supplier = CatchUpSupplier(catch_up_in=1)
        reader = FixedReader()
        sleeps = []
        controller = RecordingController()
        flow = build_flow(reader, EndTurnAdapter(), KeyedValidator(), supplier,
                          controller, sleep=sleeps.append)
        flow._last_revision = 10
        sleeps.clear()
        controller.outputs.clear()

        result = flow._retry("revision_changed")

        self.assertEqual(FlowStepStatus.RETRY, result.status)
        self.assertEqual("revision_changed", result.diagnostics)
        self.assertEqual([0.2], sleeps)
        self.assertEqual(1, reader.cleared)              # 强制下一次重读面板
        self.assertTrue(any("重试前刷新：日志 revision 10→11" in line
                            for line in controller.outputs))

    def test_unrelated_retry_code_does_not_wait_or_clear_the_cache(self):
        supplier = CatchUpSupplier(catch_up_in=1)
        reader = FixedReader()
        sleeps = []
        flow = build_flow(reader, EndTurnAdapter(), KeyedValidator(), supplier,
                          RecordingController(), sleep=sleeps.append)
        flow._last_revision = 10
        sleeps.clear()

        result = flow._retry("recommendation_not_stable")

        self.assertEqual(FlowStepStatus.RETRY, result.status)
        self.assertEqual([], sleeps)
        self.assertEqual(0, reader.cleared)

    def test_refresh_stops_at_the_timeout(self):
        supplier = SnapshotSupplier()
        sleeps = []
        controller = RecordingController()
        flow = build_flow(FixedReader(), EndTurnAdapter(), KeyedValidator(),
                          supplier, controller, sleep=sleeps.append)

        state, revision = flow._refresh_before_retry(10)

        self.assertEqual(10, revision)
        self.assertEqual(8, len(sleeps))
        self.assertTrue(all(seconds == 0.2 for seconds in sleeps))
        self.assertTrue(any("（等待 1.6s）" in line
                            for line in controller.outputs))
        self.assertIsNotNone(state)
        self.assertEqual(1.5, RETRY_REFRESH_TIMEOUT)

    def test_refresh_returns_immediately_when_revision_already_advanced(self):
        supplier = SnapshotSupplier(revision=12)
        sleeps = []
        controller = RecordingController()
        flow = build_flow(FixedReader(), EndTurnAdapter(), KeyedValidator(),
                          supplier, controller, sleep=sleeps.append)

        _state, revision = flow._refresh_before_retry(10)

        self.assertEqual(12, revision)
        self.assertEqual([], sleeps)
        self.assertEqual([], controller.outputs)

    def test_refresh_stops_early_when_stopped(self):
        supplier = SnapshotSupplier()
        sleeps = []
        controller = RecordingController()
        flow = build_flow(FixedReader(), EndTurnAdapter(), KeyedValidator(),
                          supplier, controller, sleep=sleeps.append,
                          stopped=lambda: True)

        _state, revision = flow._refresh_before_retry(10)

        self.assertEqual(10, revision)
        self.assertEqual([], sleeps)
        self.assertEqual([], controller.outputs)


if __name__ == "__main__":
    unittest.main()
