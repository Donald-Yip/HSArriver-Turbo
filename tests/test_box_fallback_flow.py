# -*- coding: utf-8 -*-
"""R18K 盒子推荐失效兜底：RecommendationFlow 的状态机回归测试。

用假的 capture/reader/parser/validator/adapter/controller 与假时钟，不需要真实
OCR/窗口。覆盖：连续失败触发、超时触发、兜底关闭、待确认/对手回合/停止不触发、
盒子恢复切回、本地兜底连续失败后本回合放弃、换回合重置。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from hslegend.application.recommendation_flow import (
    FlowStepStatus,
    PendingRecommendationAction,
    RecommendationFlow,
)
from hslegend.domain import ActionKind
from src.game_state.recommendation_adapter import AdaptedAction


class _State:
    def __init__(self, turn=5, my_turn=True):
        self.is_end = False
        self.is_my_turn = my_turn
        self.game_num_turns_in_play = turn
        self.hand_entry_count = 0
        self.my_total_mana = 3
        self.my_used_mana = 0
        self.my_temp_mana = 0
        self.my_hand_cards = [
            SimpleNamespace(entity_id="c1", card_id="TEST_CARD",
                            current_cost=1, cardtype="MINION"),
        ]
        self.my_minions = []
        self.oppo_minions = []
        self.my_locations = []
        self.oppo_locations = []
        self.my_hero = SimpleNamespace(entity_id="h1", health=30, exhausted=0)
        self.oppo_hero = SimpleNamespace(entity_id="h2", health=30, exhausted=0)
        self.my_hero_power = None
        self.my_weapon = None
        self.oppo_weapon = None
        self.discover_choice_count = None
        self.general_choice_cards = {}
        self.prepared_entity_ids = set()
        self.blocked_action_keys = set()
        self.acted_this_turn = False
        self.log_revision = 10

    @property
    def my_last_mana(self):
        return self.my_total_mana - self.my_used_mana + self.my_temp_mana


class _World:
    def __init__(self, state=None):
        self.state = state or _State()
        self.revision = self.state.log_revision

    def supplier(self):
        return self.state, self.revision

    def after_execute(self):
        self.revision += 1
        self.state.my_hand_cards = [
            card for card in self.state.my_hand_cards
            if getattr(card, "entity_id", None) != "c1"]


class _Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class _Capture:
    def __init__(self):
        self.count = 0

    def capture(self, ocr_panel_ok=False):
        self.count += 1
        return SimpleNamespace(
            frame_id=f"f{self.count}", exact_hash=f"h{self.count}",
            desktop_size=(1920, 1080), dpi=96, window_handle=1,
            foreground=True, panel_visible=True)

    @staticmethod
    def crop_recommendation(frame):
        return frame


class _Reader:
    """可切换失败/成功的假 reader：read 依次返回 texts，read_frame 固定。"""

    def __init__(self, texts=("R1", "R2"), frame_text="R1", fail=False):
        self.texts = list(texts)
        self.frame_text = frame_text
        self.fail = fail

    def read(self, frame_supplier, _roi):
        if self.fail:
            raise RuntimeError("recommendation_not_stable")
        frame = frame_supplier()
        text = self.texts.pop(0) if len(self.texts) > 1 else self.texts[0]
        return SimpleNamespace(normalized_text=text, frame_id=frame.frame_id)

    def read_frame(self, _frame, _roi):
        return SimpleNamespace(normalized_text=self.frame_text, frame_id="f")


class _Parser:
    def parse(self, _evidence, turn, revision):
        return SimpleNamespace(
            action=ActionKind.END_TURN,
            normalized_instruction="盒子推荐",
            raw_instruction="盒子推荐",
            turn_number=turn,
            log_revision=revision,
            source=None, target=None,
            entity_id=None, target_entity_id=None,
            choice_card_id=None,
        )


class _Validator:
    def validate(self, _proposed, adapted, *_args, **_kwargs):
        return SimpleNamespace(accepted=True, value=(adapted, "k"))


class _Adapter:
    def __call__(self, proposed, _state):
        if getattr(proposed, "normalized_instruction", "") == "本地兜底":
            return AdaptedAction(None, "c1", None, "hand_card_left")
        return AdaptedAction(None, None, None, "turn_changed")


class _Controller:
    def __init__(self, world):
        self.world = world
        self.executed = []
        self.messages = []

    def output(self, message):
        self.messages.append(str(message))

    def execute(self, _action, _state):
        self.executed.append(_action)
        self.world.after_execute()
        return SimpleNamespace(executed=True, cancelled=False,
                               recovery_needed=False)


class _Planner:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def plan(self, state, cost, stopped=None):
        self.calls += 1
        if self.fail:
            raise RuntimeError("local_search_boom")
        return ([SimpleNamespace(
            action=ActionKind.END_TURN,
            turn_number=state.game_num_turns_in_play,
            log_revision=state.log_revision,
            normalized_instruction="本地兜底",
            source=None, target=None,
        )], 1.0)

    @staticmethod
    def describe(action):
        return action.normalized_instruction


def _build_flow(*, world=None, reader=None, planner=None, fail_streak=2,
                stall_seconds=0.0, enabled=True, stopped=lambda: False,
                clock=None):
    world = world or _World()
    reader = reader or _Reader(fail=True)
    planner = planner or _Planner()
    controller = _Controller(world)
    flow = RecommendationFlow(
        capture=_Capture(),
        reader=reader,
        parser=_Parser(),
        state_supplier=world.supplier,
        adapter=_Adapter(),
        validator=_Validator(),
        controller=controller,
        sleep=lambda _seconds: None,
        clock=clock or _Clock(),
        stopped=stopped,
        box_ai=True,
        planner=None,
        fallback_planner=planner,
        fallback_enabled=enabled,
        fallback_fail_streak=fail_streak,
        fallback_stall_seconds=stall_seconds,
        fallback_strategy_name="通用",
    )
    return flow, world, reader, planner, controller


class BoxFallbackTriggerTests(unittest.TestCase):
    def test_consecutive_retries_switch_to_fallback(self):
        flow, world, _reader, planner, controller = _build_flow(fail_streak=2)

        first = flow.run_player_turn_step()
        self.assertIs(FlowStepStatus.RETRY, first.status)
        self.assertEqual(0, planner.calls)

        second = flow.run_player_turn_step()      # 第 2 次失败即触发兜底
        # 本地动作执行后日志已变，但还要等盒子更新推荐：OBSERVE 也是成功接管。
        self.assertIn(second.status, (FlowStepStatus.EXECUTED,
                                      FlowStepStatus.OBSERVE))
        self.assertEqual(1, planner.calls)
        self.assertEqual(1, len(controller.executed))
        self.assertTrue(any("[兜底]" in m for m in controller.messages))
        self.assertIsNotNone(flow.last_plan)

    def test_stall_timeout_triggers_even_before_fail_streak(self):
        clock = _Clock(100.0)
        flow, _world, _reader, planner, _controller = _build_flow(
            fail_streak=6, stall_seconds=8.0, clock=clock)
        self.assertIs(FlowStepStatus.RETRY, flow.run_player_turn_step().status)
        clock.now += 10.0
        result = flow.run_player_turn_step()
        self.assertIn(result.status, (FlowStepStatus.EXECUTED,
                                      FlowStepStatus.OBSERVE))
        self.assertEqual(1, planner.calls)

    def test_disabled_fallback_keeps_retrying(self):
        flow, _world, _reader, planner, _controller = _build_flow(
            enabled=False, fail_streak=1)
        for _ in range(4):
            self.assertIs(FlowStepStatus.RETRY,
                          flow.run_player_turn_step().status)
        self.assertEqual(0, planner.calls)

    def test_pending_action_blocks_fallback(self):
        flow, world, _reader, planner, _controller = _build_flow(fail_streak=1)
        flow.pending_action = PendingRecommendationAction(
            instruction="等待确认", turn_number=5,
            action_kind=ActionKind.END_TURN,
            source_entity_id=None, target_entity_id=None,
            postcondition="turn_changed", before_state=world.state,
            before_revision=world.revision, deadline=999.0)
        result = flow.run_player_turn_step()
        self.assertIs(FlowStepStatus.RETRY, result.status)
        self.assertEqual(0, planner.calls)

    def test_opponent_turn_never_falls_back(self):
        world = _World(_State(my_turn=False))
        flow, _world, _reader, planner, _controller = _build_flow(
            world=world, fail_streak=1)
        result = flow.run_player_turn_step()
        self.assertIs(FlowStepStatus.OBSERVE, result.status)
        self.assertEqual("opponent_turn", result.diagnostics)
        self.assertEqual(0, planner.calls)

    def test_stopped_never_falls_back(self):
        flow, _world, _reader, planner, _controller = _build_flow(
            fail_streak=1, stopped=lambda: True)
        result = flow.run_player_turn_step()
        self.assertIs(FlowStepStatus.OBSERVE, result.status)
        self.assertEqual("stopped", result.diagnostics)
        self.assertEqual(0, planner.calls)

    def test_new_turn_resets_counters(self):
        world = _World()
        flow, _world, _reader, planner, _controller = _build_flow(
            world=world, fail_streak=2)
        self.assertIs(FlowStepStatus.RETRY, flow.run_player_turn_step().status)
        world.state.game_num_turns_in_play = 6
        self.assertIs(FlowStepStatus.RETRY, flow.run_player_turn_step().status)
        self.assertEqual(0, planner.calls)      # 新回合重新计数
        third = flow.run_player_turn_step()
        self.assertIn(third.status, (FlowStepStatus.EXECUTED,
                                     FlowStepStatus.OBSERVE))
        self.assertEqual(1, planner.calls)


class BoxFallbackRecoveryTests(unittest.TestCase):
    def test_box_recovery_switches_back(self):
        reader = _Reader(fail=True)
        flow, _world, _reader, planner, controller = _build_flow(
            reader=reader, fail_streak=1)
        fallback_result = flow.run_player_turn_step()
        self.assertIn(fallback_result.status, (FlowStepStatus.EXECUTED,
                                              FlowStepStatus.OBSERVE))
        self.assertEqual(1, planner.calls)
        self.assertTrue(flow._fallback_active)

        reader.fail = False                      # 盒子恢复
        result = flow.run_player_turn_step()
        self.assertIs(FlowStepStatus.EXECUTED, result.status)
        self.assertFalse(flow._fallback_active)
        self.assertEqual(1, planner.calls)       # 恢复后不再走本地
        self.assertTrue(any("切回盒子AI" in m for m in controller.messages))


class BoxFallbackFailureCapTests(unittest.TestCase):
    def test_local_failures_stop_fallback_for_the_turn(self):
        planner = _Planner(fail=True)
        flow, _world, _reader, _planner, _controller = _build_flow(
            planner=planner, fail_streak=1)
        for _ in range(5):
            result = flow.run_player_turn_step()
            self.assertIs(FlowStepStatus.RETRY, result.status)
        # 本地连续失败 3 次后，本回合不再接管。
        self.assertEqual(3, planner.calls)
        self.assertIsNotNone(flow._fallback_disabled_turn)


if __name__ == "__main__":
    unittest.main()
