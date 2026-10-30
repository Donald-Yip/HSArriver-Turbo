# -*- coding: utf-8 -*-
"""我方回合开始后，以右侧「结束回合」按钮作为可操作信号。"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import FSM_action
import screen_regions
from hslegend.domain import ActionKind
from src.flow.recommendation_flow import FlowStepStatus
from src.recommendation_models import OcrLine


class RecordingFlow:
    def __init__(self):
        self.calls = 0
        self.allowed_actions = []
        self.result = SimpleNamespace(
            status=FlowStepStatus.OBSERVE,
            diagnostics="turn_not_ready",
        )

    def run_player_turn_step(self, allowed_action_kinds=None):
        self.calls += 1
        self.allowed_actions.append(allowed_action_kinds)
        return self.result


def active_snapshot(turn=5):
    return SimpleNamespace(
        is_end=False,
        is_my_turn=True,
        game_num_turns_in_play=turn,
        start_of_game_card_count=0,
    )


class EndTurnButtonProbeTests(unittest.TestCase):
    def setUp(self):
        self.saved_config = FSM_action.recommendation_config
        FSM_action.recommendation_config = SimpleNamespace(
            turn_end_button_roi=(1390, 430, 1710, 570),
        )

    def tearDown(self):
        FSM_action.recommendation_config = self.saved_config

    def test_opponent_turn_label_does_not_count_as_ready(self):
        with patch.object(
            FSM_action,
            "_ocr_lines",
            return_value=[OcrLine("对手回合", 0.99)],
        ):
            self.assertFalse(FSM_action.end_turn_button_present())

    def test_end_turn_label_is_the_ready_signal(self):
        with patch.object(
            FSM_action,
            "_ocr_lines",
            return_value=[OcrLine("结束回合", 0.99)],
        ):
            self.assertTrue(FSM_action.end_turn_button_present())


class TurnStartGateTests(unittest.TestCase):
    def setUp(self):
        self.saved = (
            FSM_action.recommendation_flow,
            FSM_action.recommendation_config,
            getattr(FSM_action, "player_turn_ready_key", None),
            getattr(FSM_action, "_turn_ready_probe_key", None),
            getattr(FSM_action, "_turn_ready_next_probe_at", 0.0),
            getattr(FSM_action, "_modal_choice_probe_key", None),
            getattr(FSM_action, "_modal_choice_next_probe_at", 0.0),
        )
        self.flow = RecordingFlow()
        FSM_action.recommendation_flow = self.flow
        FSM_action.recommendation_config = SimpleNamespace(
            turn_start_probe_interval_seconds=2.0,
            modal_choice_probe_interval_seconds=3.0,
        )
        FSM_action.player_turn_ready_key = None
        FSM_action._turn_ready_probe_key = None
        FSM_action._turn_ready_next_probe_at = 0.0
        FSM_action._modal_choice_probe_key = None
        FSM_action._modal_choice_next_probe_at = 0.0

    def tearDown(self):
        (FSM_action.recommendation_flow,
         FSM_action.recommendation_config,
         FSM_action.player_turn_ready_key,
         FSM_action._turn_ready_probe_key,
         FSM_action._turn_ready_next_probe_at,
         FSM_action._modal_choice_probe_key,
         FSM_action._modal_choice_next_probe_at) = self.saved

    def _step(self, probe, now):
        with (
            patch.object(FSM_action, "refresh_snapshot",
                         return_value=active_snapshot()),
            patch.object(FSM_action, "_maybe_surrender_by_rules",
                         return_value=None),
            patch.object(FSM_action, "_maybe_concede", return_value=False),
            patch.object(FSM_action, "_report_automation_diagnostic"),
            patch.object(FSM_action.manual_controller, "output"),
            patch.object(FSM_action, "_sleep_with_delay") as sleep,
            patch.object(FSM_action.time, "monotonic", return_value=now),
            patch.object(FSM_action, "end_turn_button_present", probe,
                         create=True),
        ):
            result = FSM_action.run_automatic_battle_step()
        return result, sleep

    def test_recommendation_is_blocked_until_end_turn_is_visible(self):
        probe = Mock(return_value=False)
        clock = [0.0]

        first, sleep = self._step(probe, clock[0])
        sleep.assert_not_called()
        self.assertIsNone(first)
        self.assertEqual(1, self.flow.calls)
        self.assertEqual(
            (frozenset((ActionKind.USE_ABILITY,)),),
            tuple(self.flow.allowed_actions),
        )
        self.assertEqual(1, probe.call_count)

        clock[0] = 1.0
        second, _ = self._step(probe, clock[0])
        self.assertIsNone(second)
        self.assertEqual(1, self.flow.calls)
        self.assertEqual(1, probe.call_count)

        clock[0] = 2.0
        probe.return_value = True
        third, _ = self._step(probe, clock[0])
        self.assertIsNone(third)
        self.assertEqual(2, self.flow.calls)
        self.assertIsNone(self.flow.allowed_actions[-1])
        self.assertEqual(2, probe.call_count)

    def test_forced_ability_is_allowed_while_end_turn_is_covered(self):
        probe = Mock(return_value=False)
        self.flow.result = SimpleNamespace(status=FlowStepStatus.EXECUTED)

        result, _ = self._step(probe, 0.0)

        self.assertIsNone(result)
        self.assertEqual(1, self.flow.calls)
        self.assertEqual(
            frozenset((ActionKind.USE_ABILITY,)),
            self.flow.allowed_actions[0],
        )


class CalibrationRegistrationTests(unittest.TestCase):
    def test_turn_end_button_defaults_stay_in_sync(self):
        from config import RecommendationConfig

        field = RecommendationConfig.__dataclass_fields__["turn_end_button_roi"]
        self.assertEqual(screen_regions.TURN_END_BUTTON_REGION, field.default)
        self.assertEqual(screen_regions.TURN_END_BUTTON_REGION,
                         FSM_action._TURN_END_BUTTON_ROI)

    def test_turn_end_button_is_a_calibratable_region(self):
        targets = screen_regions.calibration_targets(SimpleNamespace(
            turn_end_button_roi=(1400, 440, 1700, 560),
        ))
        target = next(t for t in targets if t["key"] == "turn_end_button")

        self.assertEqual("turn_end_button_roi", target["config_key"])
        self.assertEqual((1400, 440, 1700, 560), target["box"])


if __name__ == "__main__":
    unittest.main()