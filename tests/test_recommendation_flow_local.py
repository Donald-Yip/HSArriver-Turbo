# -*- coding: utf-8 -*-
"""Local deck-strategy flow path (search-driven, no recommendation-panel OCR).

Pins the behaviour added for R18:

* ``box_ai=False`` uses the injected planner and never touches capture/reader;
* execution/pending/timeout-retry follow the same confirmation rules as the box
  path (3s unconfirmed -> discard the consumption mark and re-dispatch);
* joint mode can pick the box first step when the local line is not clearly
  better, and writes the per-game summary to ``ai.txt``.
"""

import os
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace

from hslegend.application import recommendation_flow as flow_module
from hslegend.application.recommendation_flow import (
    FlowStepStatus,
    RecommendationFlow,
)
from hslegend.domain import ActionKind, ProposedAction, SlotRef


class _Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class _State:
    def __init__(self):
        self.is_end = False
        self.is_my_turn = True
        self.game_num_turns_in_play = 5
        self.hand_entry_count = 0
        self.my_hand_cards = [
            SimpleNamespace(card_id="TIME_025", entity_id="c1",
                            current_cost=1, cardtype="MINION"),
        ]
        self.my_minions = []
        self.oppo_minions = []
        self.my_locations = []
        self.oppo_locations = []
        self.my_total_mana = 3
        self.my_used_mana = 0
        self.my_temp_mana = 0
        self.my_hero = SimpleNamespace(entity_id="h1", health=30, exhausted=0)
        self.oppo_hero = SimpleNamespace(entity_id="h2", health=30, exhausted=0)
        self.my_hero_power = None
        self.my_hero_powers = []
        self.my_weapon = None
        self.oppo_weapon = None
        self.my_graveyard = []
        self.my_dead_card_ids = set()
        self.discover_choice_count = None
        self.general_choice_cards = {}
        self.prepared_entity_ids = set()
        self.blocked_action_keys = set()
        self.acted_this_turn = False

    @property
    def my_last_mana(self):
        return self.my_total_mana - self.my_used_mana + self.my_temp_mana


class _NoCapture:
    def capture(self, ocr_panel_ok=False):
        raise AssertionError("local path must not capture the screen")

    @staticmethod
    def crop_recommendation(frame):
        raise AssertionError("local path must not crop")


class _NoReader:
    def read(self, *args, **kwargs):
        raise AssertionError("local path must not OCR")

    def read_frame(self, *args, **kwargs):
        raise AssertionError("local path must not OCR")


class _Planner:
    def __init__(self, script, joint=None):
        self.script = list(script)
        self.joint = joint
        self.calls = []
        self.joint_calls = []

    def plan(self, state, cost, stopped=None):
        self.calls.append((cost, set(state.blocked_action_keys)))
        actions = [
            replace(
                item,
                turn_number=state.game_num_turns_in_play,
                log_revision=getattr(state, "log_revision", 10),
            )
            for item in self.script.pop(0)
        ]
        return actions, 11.2

    def joint_plan(self, state, actions, score, box_action):
        self.joint_calls.append((score, box_action))
        return self.joint

    @staticmethod
    def describe(action):
        return action.normalized_instruction

    @staticmethod
    def identity_key(state, action):
        return ("k",)


class _Controller:
    def __init__(self, state, holder):
        self.state = state
        self.holder = holder
        self.executed = []

    @staticmethod
    def output(_message):
        pass

    def execute(self, action, _state):
        self.executed.append(action)
        if action.action == ActionKind.PLAY_CARD:
            self.state.my_hand_cards = []
            self.holder["rev"] += 1
        elif action.action == ActionKind.END_TURN:
            self.state.is_my_turn = False
            self.holder["rev"] += 1
        elif action.action == ActionKind.ATTACK:
            pass                      # 故意不满足后置条件（模拟点击被丢掉）
        return SimpleNamespace(executed=True, cancelled=False,
                               recovery_needed=False)


class _Adapter:
    @staticmethod
    def __call__(proposed, _state):
        postcondition = ("hand_card_left"
                         if proposed.action == ActionKind.PLAY_CARD
                         else "turn_changed"
                         if proposed.action == ActionKind.END_TURN
                         else "combat_state_changed")
        return SimpleNamespace(
            manual_action=proposed,
            source_entity_id=proposed.entity_id,
            target_entity_id=None,
            postcondition=postcondition,
        )


def _action(action=ActionKind.PLAY_CARD, instruction="打出手牌1号随从",
            entity_id="c1", source=None):
    return ProposedAction(
        action_id="a1", frame_id="search:5:10", created_at=0.0,
        turn_number=5, log_revision=10,
        raw_instruction=instruction, normalized_instruction=instruction,
        action=action,
        source=source or SlotRef("hand", "friendly", 1),
        card_type="MINION", cost=1, entity_id=entity_id,
        ocr_confidence=1.0, semantic_confidence=1.0,
    )


def _build(state, planner, clock=None, box_ai=False, joint=False,
           capture=None, reader=None, parser=None):
    holder = {"rev": 10}
    controller = _Controller(state, holder)

    def supplier():
        state.log_revision = holder["rev"]
        return state, holder["rev"]

    flow = RecommendationFlow(
        capture=capture or _NoCapture(),
        reader=reader or _NoReader(),
        parser=parser,
        state_supplier=supplier,
        adapter=_Adapter(),
        validator=None,
        controller=controller,
        sleep=lambda _seconds: None,
        clock=clock or _Clock(),
        box_ai=box_ai,
        planner=planner,
        joint=joint,
        strategy_name="动物园术",
    )
    return flow, controller, holder


class LocalFlowTests(unittest.TestCase):
    def test_local_action_executes_then_waits_for_confirmation(self):
        state = _State()
        planner = _Planner([[_action()], [_action(ActionKind.END_TURN, "结束回合")]])
        flow, controller, _holder = _build(state, planner)

        result = flow.run_player_turn_step()

        self.assertEqual(FlowStepStatus.OBSERVE, result.status)
        self.assertEqual("waiting_recommendation_update", result.diagnostics)
        self.assertEqual(1, len(controller.executed))
        self.assertIsNotNone(flow.pending_action)
        self.assertEqual(1, len(planner.calls))
        self.assertEqual(11.2, flow.last_plan["score"])

    def test_confirmation_clears_pending_and_searches_again(self):
        state = _State()
        planner = _Planner([[_action()], [_action(ActionKind.END_TURN, "结束回合")]])
        flow, controller, _holder = _build(state, planner)

        flow.run_player_turn_step()
        result = flow.run_player_turn_step()

        self.assertEqual(FlowStepStatus.OBSERVE, result.status)
        self.assertEqual(2, len(controller.executed))
        self.assertIsNotNone(flow.pending_action)
        self.assertEqual("结束回合", flow.pending_action.instruction)

    def test_unconfirmed_action_retries_after_timeout_then_stops(self):
        clock = _Clock()
        state = _State()
        attack = _action(
            ActionKind.ATTACK, "我方1号随从攻击敌方英雄",
            entity_id="m1", source=SlotRef("board_slot", "friendly", 1))
        planner = _Planner([[attack], [attack], [attack]])
        flow, controller, _holder = _build(state, planner, clock=clock)

        first = flow.run_player_turn_step()
        self.assertEqual("awaiting_action_confirmation", first.diagnostics)
        self.assertEqual(1, len(controller.executed))

        clock.now += 4.0
        retry = flow.run_player_turn_step()
        self.assertEqual("awaiting_action_confirmation", retry.diagnostics)
        self.assertEqual(1, flow._local_retry_count)
        self.assertEqual(1, len(controller.executed))

        # 重试计数保留，下一步重新搜索并重击同一个动作。
        retry2 = flow.run_player_turn_step()
        self.assertEqual(2, len(controller.executed))
        self.assertEqual(1, flow.pending_action.attempts)

        clock.now += 4.0
        flow.run_player_turn_step()
        self.assertEqual(2, flow._local_retry_count)
        self.assertEqual(2, len(controller.executed))

        flow.run_player_turn_step()
        self.assertEqual(3, len(controller.executed))
        self.assertEqual(2, flow.pending_action.attempts)

        clock.now += 4.0
        capped = flow.run_player_turn_step()
        self.assertEqual("awaiting_action_confirmation", capped.diagnostics)
        self.assertEqual(3, len(controller.executed))

    def test_local_path_never_captures_or_ocrs(self):
        state = _State()
        planner = _Planner([[_action()]])
        flow, _controller, _holder = _build(state, planner)

        result = flow.run_player_turn_step()

        self.assertEqual(FlowStepStatus.OBSERVE, result.status)

    def test_missing_planner_falls_back_to_box(self):
        state = _State()
        flow, _controller, _holder = _build(state, planner=None, box_ai=False)
        self.assertTrue(flow.box_ai)


class JointFlowTests(unittest.TestCase):
    def test_box_choice_is_executed_and_summarized(self):
        state = _State()
        box_action = _action(
            ActionKind.PLAY_CARD, "打出手牌1号随从", entity_id="c1")
        joint = {
            "local": {"actions": [], "score": 5.0},
            "box": {"actions": [], "score": 9.0},
            "winner": "box", "chosen": "box", "margin": -0.4,
            "fallback": None, "diff": True, "outcome": "box",
        }
        planner = _Planner([[_action()]], joint=joint)
        capture = SimpleNamespace(
            capture=lambda ocr_panel_ok=False: SimpleNamespace(frame_id="f1"),
            crop_recommendation=lambda frame: frame,
        )
        reader = SimpleNamespace(
            read_frame=lambda frame, roi: SimpleNamespace(
                normalized_text="打出手牌1号随从", frame_id="f1"),
        )
        parser = SimpleNamespace(
            parse=lambda evidence, turn, revision: box_action)
        flow, controller, _holder = _build(
            state, planner, joint=True, capture=capture, reader=reader,
            parser=parser)

        result = flow.run_player_turn_step()

        self.assertEqual(FlowStepStatus.OBSERVE, result.status)
        self.assertEqual(1, len(controller.executed))
        self.assertEqual(1, flow._joint_stats["diff"])
        self.assertEqual(1, flow._joint_stats["box"])
        self.assertEqual(1, len(planner.joint_calls))

    def test_flush_joint_log_writes_ai_txt(self):
        state = _State()
        planner = _Planner([[_action()]])
        flow, _controller, _holder = _build(state, planner)
        flow._joint_stats = {"diff": 2, "local": 1, "box": 3, "same": 2}

        with tempfile.TemporaryDirectory() as tmp:
            class _FakeFile:
                def __init__(self, path):
                    self.path = path

                def open(self, mode="a", encoding=None):
                    return open(self.path, mode, encoding=encoding)

            class _FakeRoot:
                def __truediv__(self, name):
                    return _FakeFile(os.path.join(tmp, name))

            class _FakePath:
                def __init__(self, *parts):
                    self.parts = parts

                def resolve(self):
                    return self

                @property
                def parents(self):
                    return [None, None, None, _FakeRoot()]

            original = flow_module.Path
            flow_module.Path = _FakePath
            try:
                line = flow.flush_joint_log()
            finally:
                flow_module.Path = original

            self.assertIsNotNone(line)
            with open(os.path.join(tmp, "ai.txt"),
                      encoding="utf-8") as handle:
                content = handle.read()
            self.assertIn("本地胜出 1 次", content)
            self.assertIn("盒子胜出 3 次", content)
            self.assertIn("打法差异 2 次", content)
            self.assertEqual(
                {"diff": 0, "local": 0, "box": 0, "same": 0},
                flow._joint_stats)


if __name__ == "__main__":
    unittest.main()
