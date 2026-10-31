import unittest
from types import SimpleNamespace

from manual_controller import ActionExecutionResult
from src.flow.recommendation_flow import FlowStepStatus, RecommendationFlow
from src.game_state.recommendation_adapter import adapt_action
from src.parser.recommendation_parser import RecommendationParser
from src.recommendation_models import FrameEvidence, OcrEvidence
from src.safety.recommendation_validator import RecommendationValidator


class FakeClock:
    def __init__(self, now=10.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class HandAnimationDelayTests(unittest.TestCase):
    def test_deadline_extends_per_new_hand_entry_and_resets_per_game(self):
        from src.flow.hand_animation_delay import HandAnimationDelay

        clock = FakeClock()
        delay = HandAnimationDelay(clock=clock)

        delay.observe(20)
        self.assertEqual(0.0, delay.remaining())

        delay.observe(22)
        self.assertAlmostEqual(2.0, delay.remaining())

        clock.advance(0.4)
        delay.observe(23)
        self.assertAlmostEqual(2.6, delay.remaining())

        # Re-observing the same count is not a new entry.
        clock.advance(0.2)
        delay.observe(23)
        self.assertAlmostEqual(2.4, delay.remaining())

        # A counter reset (new game) clears the previous deadline.
        delay.observe(0)
        self.assertEqual(0.0, delay.remaining())

    def test_flow_waits_one_second_per_new_hand_entity_before_clicking(self):
        def frame(frame_id, exact_hash):
            return FrameEvidence(
                frame_id=frame_id,
                captured_at=0.0,
                desktop_size=(1920, 1080),
                dpi=96,
                window_handle=1,
                foreground=True,
                recommendation_roi=(7, 32, 278, 970),
                exact_hash=exact_hash,
                perceptual_hash="0" * 64,
                panel_visible=True,
            )

        def evidence(frame_id):
            return OcrEvidence(
                frame_id=frame_id,
                created_at=0.0,
                lines=(),
                normalized_text="结束回合",
                confidence=0.90,
                backend="test",
                preprocessing="test",
            )

        class Capture:
            def __init__(self):
                self.frames = iter((
                    frame("stable", "before"),
                    frame("current", "before"),
                    frame("after", "after"),
                ))

            def capture(self, ocr_panel_ok=False):
                return next(self.frames)

            @staticmethod
            def crop_recommendation(current_frame):
                return current_frame

        class Reader:
            def __init__(self):
                self.read_count = 0

            def read(self, frame_supplier, _roi_supplier):
                current_frame = frame_supplier()
                self.read_count += 1
                current_evidence = evidence(current_frame.frame_id)
                if self.read_count == 1:
                    return current_evidence
                return OcrEvidence(
                    frame_id=current_evidence.frame_id,
                    created_at=0.0,
                    lines=(),
                    normalized_text="等待对手操作",
                    confidence=0.90,
                    backend="test",
                    preprocessing="test",
                )

            @staticmethod
            def read_frame(current_frame, _roi_supplier):
                return evidence(current_frame.frame_id)

        clock = FakeClock()
        active = SimpleNamespace(
            is_my_turn=True,
            game_num_turns_in_play=3,
            my_hand_cards=[],
            hand_entry_count=12,
        )
        ended = SimpleNamespace(
            is_my_turn=False,
            game_num_turns_in_play=4,
            my_hand_cards=active.my_hand_cards,
            hand_entry_count=active.hand_entry_count,
        )
        states = iter((
            (active, 10),
            (active, 10),
            (active, 10),
            (ended, 11),
        ))
        events = []

        def sleep(seconds):
            if seconds > 0:
                events.append(("sleep", seconds))
            clock.advance(seconds)

        class Controller:
            @staticmethod
            def execute(_action, _state):
                events.append(("execute",))
                return ActionExecutionResult(True, "executed")

        flow = RecommendationFlow(
            capture=Capture(),
            reader=Reader(),
            parser=RecommendationParser(),
            state_supplier=lambda: next(states),
            adapter=adapt_action,
            validator=RecommendationValidator(),
            controller=Controller(),
            sleep=sleep,
            clock=clock,
        )

        # The first observation is a historical baseline; the draws detected
        # while waiting (10 -> 12) extend the deadline, so the click waits 2s.
        flow.hand_animation_delay.observe(10)

        result = flow.run_player_turn_step()

        self.assertEqual(FlowStepStatus.EXECUTED, result.status)
        self.assertEqual([("sleep", 2.0), ("execute",)], events)


if __name__ == "__main__":
    unittest.main()
