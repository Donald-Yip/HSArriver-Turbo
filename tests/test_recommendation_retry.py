# -*- coding: utf-8 -*-
"""Repeated recommendation failures must keep retrying.

Regression: opponent end-turn animations can leave an old recommendation on
screen long after control returns. A per-recommendation circuit breaker turns
that normal transition into a dead turn, so failures always stay ``RETRY``
until the recommendation/state actually becomes executable.

OCR raw evidence is still deduplicated within one unreadable episode to avoid
flooding the log, and is emitted again after a later readable episode.
"""

import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

import battle_record
from manual_controller import (
    ActionExecutionResult, AssembleChoiceAction, EndTurnAction,
)
from src.flow.recommendation_flow import FlowStepStatus, RecommendationFlow
from src.game_state.recommendation_adapter import AdaptedAction
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_models import FrameEvidence, OcrEvidence


PANEL_TEXT = "\n".join([
    "打法参考A", "打出3号位法术", "漂浮教程",
    "目标是己方1号位", "克拉克斯西宾",
])
PARSE_FAIL_TEXT = "\n".join([
    "打法参考A", "打出2号位法术", "目标是已方随从",
])
PARSE_FAIL_TEXT_JITTER = PARSE_FAIL_TEXT + "\n推荐打法Beta"


def frame(frame_id):
    return FrameEvidence(
        frame_id=frame_id, captured_at=0.0, desktop_size=(1920, 1080),
        dpi=96, window_handle=1, foreground=True,
        recommendation_roi=(7, 32, 278, 970), exact_hash=frame_id,
        perceptual_hash="0" * 64, panel_visible=True)


def evidence(frame_id, text):
    return OcrEvidence(
        frame_id=frame_id, created_at=0.0, lines=(),
        normalized_text=str(text), confidence=0.95,
        backend="test", preprocessing="test")


class CyclingCapture:
    def __init__(self):
        self.count = 0

    def capture(self, ocr_panel_ok=False):
        self.count += 1
        return frame(f"f{self.count}")

    @staticmethod
    def crop_recommendation(current_frame):
        return current_frame


class ScriptedReader:
    def __init__(self, texts):
        self.texts = list(texts)

    def _next(self):
        if len(self.texts) > 1:
            return self.texts.pop(0)
        return self.texts[0]

    def read(self, frame_supplier, _roi):
        current = frame_supplier()
        return evidence(current.frame_id, self._next())

    def read_frame(self, current_frame, _roi):
        return evidence(current_frame.frame_id, self._next())


class ExplodingAdapter:
    def __call__(self, proposed, state):
        raise ValueError("boom: adapter rejected")


class AcceptingAdapter:
    def __call__(self, proposed, state):
        return AdaptedAction(EndTurnAction(), None, None, "turn_changed")


class AssemblyAdapter:
    def __call__(self, _proposed, _state):
        return AdaptedAction(
            AssembleChoiceAction(1), None, None, "choice_resolved")


class AcceptingValidator:
    def validate(self, proposed, adapted, *args, **kwargs):
        return SimpleNamespace(accepted=True, value=(adapted, "k"))


class RecordingController:
    def __init__(self):
        self.executed = []

    @staticmethod
    def output(_message):
        pass

    def execute(self, action, _state):
        self.executed.append(action)
        return ActionExecutionResult(True, "executed")


def active_state(turn=5):
    return SimpleNamespace(
        is_my_turn=True, game_num_turns_in_play=turn, hand_entry_count=0,
        my_hand_cards=[], my_minions=[], oppo_minions=[],
        my_locations=[], oppo_locations=[])


def build_flow(reader, adapter=None, turn=5, controller=None):
    return RecommendationFlow(
        capture=CyclingCapture(), reader=reader,
        parser=RecommendationParser(),
        state_supplier=lambda: (active_state(turn), 10),
        adapter=adapter or ExplodingAdapter(),
        validator=AcceptingValidator(),
        controller=controller or RecordingController(),
        sleep=lambda _s: None)


def run_step(flow):
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        result = flow.run_player_turn_step()
    return result, buffer.getvalue()


class RecommendationRetryTests(unittest.TestCase):
    def test_same_parse_failure_retries_past_old_limit(self):
        flow = build_flow(ScriptedReader([PANEL_TEXT]))

        results = [run_step(flow)[0] for _ in range(15)]

        self.assertTrue(all(result.status is FlowStepStatus.RETRY
                            for result in results))
        self.assertNotIn(
            FlowStepStatus.OBSERVE,
            [result.status for result in results],
        )

    def test_flickering_parse_failures_keep_retrying(self):
        reader = ScriptedReader([PARSE_FAIL_TEXT])
        flow = build_flow(reader)

        for text in (PARSE_FAIL_TEXT, PARSE_FAIL_TEXT_JITTER,
                     PARSE_FAIL_TEXT, PARSE_FAIL_TEXT_JITTER) * 4:
            reader.texts = [text]
            self.assertEqual(FlowStepStatus.RETRY, run_step(flow)[0].status)

    def test_same_adapter_failure_keeps_retrying(self):
        flow = build_flow(ScriptedReader(["结束回合"]))

        results = [run_step(flow)[0] for _ in range(15)]

        self.assertTrue(all(result.status is FlowStepStatus.RETRY
                            for result in results))
        self.assertTrue(all("boom: adapter rejected" in result.diagnostics
                            for result in results))

    def test_missed_assembly_click_keeps_recommendation_retryable(self):
        controller = RecordingController()
        flow = build_flow(
            ScriptedReader(["拼装\n左二\n中一"]),
            adapter=AssemblyAdapter(),
            controller=controller,
        )

        results = [run_step(flow)[0] for _ in range(3)]

        self.assertTrue(all(result.status is FlowStepStatus.RETRY
                            for result in results))
        self.assertEqual(3, len(controller.executed))
        self.assertIsNone(flow.pending_action)

    def test_unconfirmed_action_executes_once_then_waits_for_confirmation(self):
        controller = RecordingController()
        flow = build_flow(
            ScriptedReader(["结束回合"]),
            adapter=AcceptingAdapter(),
            controller=controller,
        )

        results = [run_step(flow)[0] for _ in range(12)]

        self.assertEqual(1, len(controller.executed))
        self.assertTrue(all(result.status is FlowStepStatus.OBSERVE
                            for result in results))
        self.assertEqual("awaiting_action_confirmation",
                         results[-1].diagnostics)

    def test_changed_recommendation_clears_pending_without_replaying_old_action(self):
        reader = ScriptedReader(["结束回合"])
        controller = RecordingController()
        flow = build_flow(reader, adapter=AcceptingAdapter(),
                          controller=controller)

        first = run_step(flow)[0]
        self.assertEqual(FlowStepStatus.OBSERVE, first.status)
        self.assertEqual(1, len(controller.executed))

        reader.texts = ["打出1号位随从"]
        second = run_step(flow)[0]

        self.assertEqual(FlowStepStatus.OBSERVE, second.status)
        self.assertEqual(2, len(controller.executed))

    def test_readable_recommendation_executes_after_long_transition(self):
        reader = ScriptedReader([PARSE_FAIL_TEXT])
        controller = RecordingController()
        flow = build_flow(reader, controller=controller)

        for _ in range(12):
            self.assertEqual(FlowStepStatus.RETRY, run_step(flow)[0].status)
        self.assertEqual([], controller.executed)

        reader.texts = ["结束回合", "结束回合", "结束回合"]
        flow.adapter = AcceptingAdapter()
        result, _ = run_step(flow)

        self.assertEqual(FlowStepStatus.OBSERVE, result.status)
        self.assertEqual(1, len(controller.executed))

    def test_parse_failure_raw_is_logged_once_per_episode(self):
        reader = ScriptedReader([PARSE_FAIL_TEXT])
        controller = RecordingController()
        flow = build_flow(reader, adapter=AcceptingAdapter(),
                          controller=controller)

        first = run_step(flow)[1]
        second = run_step(flow)[1]

        self.assertIn("[推荐] 原文：目标是已方随从", first)
        self.assertEqual(1, first.count("[推荐] 原文：目标是已方随从"))
        self.assertNotIn("[推荐] 原文：目标是已方随从", second)
        self.assertEqual(
            "recommendation_raw",
            battle_record.classify("[推荐] 原文：目标是已方随从")["kind"],
        )

        reader.texts = ["结束回合"]
        run_step(flow)
        reader.texts = [PARSE_FAIL_TEXT]
        later = run_step(flow)[1]
        self.assertIn("[推荐] 原文：目标是已方随从", later)

    def test_repeated_failures_never_emit_stuck_warning(self):
        flow = build_flow(ScriptedReader([PANEL_TEXT]))

        outputs = [run_step(flow)[1] for _ in range(20)]

        self.assertFalse(any("同一条推荐连续失败" in output
                             for output in outputs))
        self.assertFalse(any("recommendation_stuck" in output
                             for output in outputs))


if __name__ == "__main__":
    unittest.main()
