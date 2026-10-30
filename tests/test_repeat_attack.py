# -*- coding: utf-8 -*-
"""同一条推荐没变、但动作确实还能再做：必须再执行，不能干等盒子更新。

实测 2026-10-31：风怒随从第一击后 EXHAUSTED 仍是 0（第二击才置 1），
盒子在两次攻击之间给的是同一行「操作N号位随从攻击 / 目标是对方M号位」，
旧流程把它当成“已完成、等面板更新”，整回合只打了一下。
"""

import copy
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from manual_controller import ActionExecutionResult
from src.flow.recommendation_flow import FlowStepStatus, RecommendationFlow
from src.game_state.recommendation_adapter import AdaptedAction
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_models import FrameEvidence, OcrEvidence


ATTACK_TEXT = "操作1号位随从攻击\n目标是对方1号位"


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
    """盒子面板一直显示同一行（风怒两击之间的实测表现）。"""

    def __init__(self, text=ATTACK_TEXT):
        self.text = text

    def read(self, frame_supplier, _roi):
        current = frame_supplier()
        return OcrEvidence(
            frame_id=current.frame_id, created_at=0.0, lines=(),
            normalized_text=self.text, confidence=0.95,
            backend="test", preprocessing="test")

    def read_frame(self, current_frame, _roi):
        return OcrEvidence(
            frame_id=current_frame.frame_id, created_at=0.0, lines=(),
            normalized_text=self.text, confidence=0.95,
            backend="test", preprocessing="test")


class AttackAdapter:
    def __call__(self, _proposed, _state):
        return AdaptedAction(SimpleNamespace(), "301", "401",
                             "combat_state_changed")


class AcceptingValidator:
    def validate(self, proposed, adapted, *args, **kwargs):
        return SimpleNamespace(accepted=True, value=(adapted, "key"))


class AttackState:
    def __init__(self, windfury=True):
        self.is_my_turn = True
        self.game_num_turns_in_play = 5
        self.hand_entry_count = 0
        self.my_hand_cards = []
        self.my_locations = []
        self.oppo_locations = []
        self.windfury = windfury
        self.attacks = 0
        self.minion = SimpleNamespace(
            entity_id="301", zone_pos=1, exhausted=0, frozen=0,
            cant_attack=0, dormant=0)
        self.target = SimpleNamespace(
            entity_id="401", zone_pos=1, damage=0, health=30)
        self.my_minions = [self.minion]
        self.oppo_minions = [self.target]
        self.my_hero = SimpleNamespace(entity_id="h1", exhausted=0)

    def apply_attack(self):
        """按实测日志模拟：风怒第一击不置 EXHAUSTED，第二击才置 1。"""
        self.attacks += 1
        self.target.damage += 2
        if not self.windfury or self.attacks >= 2:
            self.minion.exhausted = 1


class RecordingAttackController:
    def __init__(self, state):
        self.state = state
        self.executed = 0
        self.revision = 10

    @staticmethod
    def output(_message):
        pass

    def execute(self, _action, _state):
        self.executed += 1
        self.revision += 1
        self.state.apply_attack()
        return ActionExecutionResult(True, "已操作随从攻击")


def build_flow(state, controller):
    def supplier():
        # 真实 state_supplier 每次都返回新的 StrategyState 快照；假状态必须
        # 同样拷贝，否则 before_state 和 after 是同一个可变对象，后置条件
        # 永远比较不出差异。
        return copy.deepcopy(state), controller.revision

    return RecommendationFlow(
        capture=CyclingCapture(), reader=FixedReader(),
        parser=RecommendationParser(),
        state_supplier=supplier,
        adapter=AttackAdapter(),
        validator=AcceptingValidator(),
        controller=controller,
        sleep=lambda _s: None,
        clock=lambda: 100.0,
    )


def run_step(flow):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        return flow.run_player_turn_step(), buffer.getvalue()


class WindfuryRepeatTests(unittest.TestCase):
    def test_windfury_second_attack_is_executed_without_waiting(self):
        state = AttackState(windfury=True)
        controller = RecordingAttackController(state)
        flow = build_flow(state, controller)

        first, _ = run_step(flow)
        second, _ = run_step(flow)

        self.assertEqual(FlowStepStatus.OBSERVE, first.status)
        self.assertEqual(FlowStepStatus.OBSERVE, second.status)
        self.assertEqual(2, controller.executed)
        self.assertEqual("waiting_recommendation_update",
                         second.diagnostics)

    def test_exhausted_attacker_is_not_repeated(self):
        state = AttackState(windfury=False)
        controller = RecordingAttackController(state)
        flow = build_flow(state, controller)

        first, _ = run_step(flow)
        second, _ = run_step(flow)

        self.assertEqual(FlowStepStatus.OBSERVE, first.status)
        self.assertEqual(FlowStepStatus.OBSERVE, second.status)
        self.assertEqual(1, controller.executed)
        self.assertEqual("waiting_recommendation_update",
                         second.diagnostics)

    def test_identical_attack_is_not_repeated_forever(self):
        """攻击者一直“还能打”（盒子面板卡住）时也要有上限，不能无限点。"""
        state = AttackState(windfury=True)

        def always_attackable():
            state.attacks += 1
            state.target.damage += 2

        state.apply_attack = always_attackable
        controller = RecordingAttackController(state)
        flow = build_flow(state, controller)

        for _ in range(12):
            run_step(flow)

        self.assertLessEqual(controller.executed, 7)
        self.assertGreaterEqual(controller.executed, 2)


class PlayAdapter:
    def __call__(self, _proposed, state):
        card = state.my_hand_cards[0]
        return AdaptedAction(SimpleNamespace(), card.entity_id, None,
                             "hand_card_left")


class PlayState:
    def __init__(self):
        self.is_my_turn = True
        self.game_num_turns_in_play = 5
        self.hand_entry_count = 0
        self.my_hand_cards = [SimpleNamespace(entity_id="501", zone_pos=1),
                              SimpleNamespace(entity_id="502", zone_pos=2),
                              SimpleNamespace(entity_id="503", zone_pos=3)]
        self.my_minions = []
        self.my_locations = []
        self.oppo_minions = []
        self.oppo_locations = []
        self.my_hero = SimpleNamespace(entity_id="h1", exhausted=0)


class RecordingPlayController:
    def __init__(self, state):
        self.state = state
        self.executed = 0
        self.revision = 10

    @staticmethod
    def output(_message):
        pass

    def execute(self, _action, _state):
        self.executed += 1
        self.revision += 1
        self.state.my_hand_cards.pop(0)
        return ActionExecutionResult(True, "已打出随从")


def build_play_flow(state, controller):
    def supplier():
        return copy.deepcopy(state), controller.revision

    return RecommendationFlow(
        capture=CyclingCapture(), reader=FixedReader("打出1号位随从"),
        parser=RecommendationParser(),
        state_supplier=supplier,
        adapter=PlayAdapter(),
        validator=AcceptingValidator(),
        controller=controller,
        sleep=lambda _s: None,
        clock=lambda: 100.0,
    )


class RepeatPlayTests(unittest.TestCase):
    def test_same_slot_play_repeats_while_the_panel_text_is_unchanged(self):
        """连续打同一个号位：盒子两行推荐的动作行完全一样（只有卡名变），
        旧流程会卡在「等待盒子更新推荐」，实际应该继续出牌。"""
        state = PlayState()
        controller = RecordingPlayController(state)
        flow = build_play_flow(state, controller)

        run_step(flow)
        run_step(flow)

        self.assertEqual(2, controller.executed)


if __name__ == "__main__":
    unittest.main()
