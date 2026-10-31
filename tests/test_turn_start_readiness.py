# -*- coding: utf-8 -*-
"""我方回合开始后，以右侧「结束回合」按钮作为可操作信号。"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import FSM_action
import screen_regions
from hslegend.domain import ActionKind
from src.flow.recommendation_flow import FlowStepStatus
from src.recommendation_models import OcrLine


class RecordingFlow:
    def __init__(self, box_ai=True):
        self.calls = 0
        self.allowed_actions = []
        self.box_ai = box_ai
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
        self.saved_text = FSM_action._turn_end_last_text
        FSM_action.recommendation_config = SimpleNamespace(
            turn_end_button_roi=(1390, 430, 1710, 570),
        )
        FSM_action._turn_end_last_text = ""

    def tearDown(self):
        FSM_action.recommendation_config = self.saved_config
        FSM_action._turn_end_last_text = self.saved_text

    def _probe(self, lines):
        with patch.object(FSM_action, "_ocr_lines", return_value=lines):
            return FSM_action.end_turn_button_present()

    def test_end_turn_label_variants_are_ready(self):
        cases = {
            "完整": [OcrLine("结束回合", 0.99)],
            # 客户区缩放时 ROI 会把最后一个字裁掉：实测读到「结束回」（用户 2026-10-31）。
            "截断": [OcrLine("01:07", 0.98), OcrLine("结束回", 0.90)],
            # OCR 也可能把「结束回合」拆成两行。
            "拆行": [OcrLine("结束", 0.95), OcrLine("回合", 0.95)],
            "英文": [OcrLine("End Turn", 0.95)],
        }
        for name, lines in cases.items():
            with self.subTest(case=name):
                self.assertTrue(self._probe(lines))

    def test_opponent_label_and_timer_are_not_ready(self):
        self.assertFalse(self._probe([OcrLine("对手回合", 0.99)]))
        self.assertFalse(self._probe([OcrLine("01:07", 0.99)]))

    def test_short_or_low_confidence_read_is_not_ready(self):
        # 只读到「结束」太短，可能是动画中的半截字：不认。
        self.assertFalse(self._probe([OcrLine("结束", 0.99)]))
        self.assertFalse(self._probe([OcrLine("结束回", 0.30)]))

    def test_fallback_box_is_tried_when_the_main_box_misses(self):
        boxes = []

        def fake_ocr(box, _scale, _tag):
            boxes.append(box)
            if box == FSM_action._TURN_END_FALLBACK_ROI:
                return [OcrLine("结束回合", 0.92)]
            return [OcrLine("对手回合", 0.90)]

        with patch.object(FSM_action, "_ocr_lines", side_effect=fake_ocr):
            self.assertTrue(FSM_action.end_turn_button_present())

        self.assertEqual(2, len(boxes))
        self.assertEqual(FSM_action._TURN_END_FALLBACK_ROI, boxes[1])

    def test_main_box_success_skips_the_fallback(self):
        boxes = []

        def fake_ocr(box, _scale, _tag):
            boxes.append(box)
            return [OcrLine("结束回合", 0.92)]

        with patch.object(FSM_action, "_ocr_lines", side_effect=fake_ocr):
            self.assertTrue(FSM_action.end_turn_button_present())

        self.assertEqual(1, len(boxes))

    def test_failure_records_the_text_read_for_the_diagnostic(self):
        self.assertFalse(self._probe([OcrLine("对手回合", 0.90)]))

        self.assertIn("对手回合", FSM_action._turn_end_last_text)


class _TurnGateFixture(unittest.TestCase):
    """共用夹具：我方回合、右侧「结束回合」按钮由用例决定读不读得到。"""

    box_ai = True

    def setUp(self):
        self.saved = (
            FSM_action.recommendation_flow,
            FSM_action.recommendation_config,
            getattr(FSM_action, "player_turn_ready_key", None),
            getattr(FSM_action, "_turn_ready_probe_key", None),
            getattr(FSM_action, "_turn_ready_next_probe_at", 0.0),
            getattr(FSM_action, "_modal_choice_probe_key", None),
            getattr(FSM_action, "_modal_choice_next_probe_at", 0.0),
            getattr(FSM_action, "last_automation_diagnostic", None),
        )
        self.saved_choice = self._choice_fields()
        self.flow = RecordingFlow(box_ai=self.box_ai)
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
        FSM_action.last_automation_diagnostic = None
        self._reset_choice_panel()

    def tearDown(self):
        (FSM_action.recommendation_flow,
         FSM_action.recommendation_config,
         FSM_action.player_turn_ready_key,
         FSM_action._turn_ready_probe_key,
         FSM_action._turn_ready_next_probe_at,
         FSM_action._modal_choice_probe_key,
         FSM_action._modal_choice_next_probe_at,
         FSM_action.last_automation_diagnostic) = self.saved
        self._restore_choice_fields(self.saved_choice)

    @staticmethod
    def _choice_fields():
        log = FSM_action.log_state
        return (log.my_player_id, log.general_choice_id,
                log.general_choice_player, log.general_choice_indexes,
                log.general_choice_cards, log.general_choice_entity_ids,
                log.general_choice_ready)

    @staticmethod
    def _restore_choice_fields(saved):
        (log_id, choice_id, choice_player, indexes, cards, entity_ids,
         ready) = saved
        log = FSM_action.log_state
        log.my_player_id = log_id
        log.general_choice_id = choice_id
        log.general_choice_player = choice_player
        log.general_choice_indexes = indexes
        log.general_choice_cards = cards
        log.general_choice_entity_ids = entity_ids
        log.general_choice_ready = ready

    def _reset_choice_panel(self):
        log = FSM_action.log_state
        log.my_player_id = "2"
        log.general_choice_id = None
        log.general_choice_player = None
        log.general_choice_indexes = set()
        log.general_choice_cards = {}
        log.general_choice_entity_ids = {}
        log.general_choice_ready = False

    def _open_choice_panel(self, ready=True):
        """日志确认我方「进行选择」面板开着（克苏恩仪式四选一，实测同款）。"""
        log = FSM_action.log_state
        log.general_choice_id = "4"
        log.general_choice_player = log.my_player_id
        log.general_choice_indexes = {0, 1, 2, 3}
        log.general_choice_cards = {
            0: "BE_013t1", 1: "BE_013t2", 2: "BE_013t3", 3: "BE_013t4"}
        log.general_choice_entity_ids = {0: "95", 1: "96", 2: "97", 3: "98"}
        log.general_choice_ready = ready

    def _step(self, probe, now, report_diagnostics=False):
        with (
            patch.object(FSM_action, "refresh_snapshot",
                         return_value=active_snapshot()),
            patch.object(FSM_action, "_maybe_surrender_by_rules",
                         return_value=None),
            patch.object(FSM_action, "_maybe_concede", return_value=False),
            (nullcontext() if report_diagnostics
             else patch.object(FSM_action, "_report_automation_diagnostic")),
            patch.object(FSM_action.manual_controller, "output") as output,
            patch.object(FSM_action, "_sleep_with_delay") as sleep,
            patch.object(FSM_action.time, "monotonic", return_value=now),
            patch.object(FSM_action, "end_turn_button_present", probe,
                         create=True),
        ):
            result = FSM_action.run_automatic_battle_step()
        self.outputs = [call.args[0] for call in output.call_args_list]
        return result, sleep


class TurnStartGateTests(_TurnGateFixture):
    def test_recommendation_is_blocked_until_end_turn_is_visible(self):
        probe = Mock(return_value=False)

        first, sleep = self._step(probe, 0.0)
        sleep.assert_not_called()
        self.assertIsNone(first)
        self.assertEqual(1, self.flow.calls)
        self.assertEqual(
            (frozenset((ActionKind.USE_ABILITY,)),),
            tuple(self.flow.allowed_actions),
        )
        self.assertEqual(1, probe.call_count)

        second, _ = self._step(probe, 1.0)
        self.assertIsNone(second)
        self.assertEqual(1, self.flow.calls)
        self.assertEqual(1, probe.call_count)

        probe.return_value = True
        third, _ = self._step(probe, 2.0)
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

    def test_waiting_diagnostic_includes_the_text_ocr_read(self):
        def probe():
            FSM_action._turn_end_last_text = "对手回合 / 01:07"
            return False

        self._step(probe, 0.0, report_diagnostics=True)

        self.assertTrue(any("OCR 读到：对手回合 / 01:07" in message
                            for message in self.outputs))


class ChoicePanelGateTests(_TurnGateFixture):
    """「进行选择」面板盖住右侧按钮：不能干等，要放行选项点击。"""

    def test_choice_panel_opens_the_gate_for_option_clicks(self):
        self._open_choice_panel()

        result, _ = self._step(Mock(return_value=False), 0.0)

        self.assertIsNone(result)
        self.assertEqual(1, self.flow.calls)
        self.assertEqual(
            frozenset((ActionKind.USE_ABILITY, ActionKind.CHOOSE_DISCOVER,
                       ActionKind.ASSEMBLE_CHOICE)),
            self.flow.allowed_actions[0],
        )

        # 选项行先到、ChoiceCardMgr BEGIN 后到：这期间也要放行。
        self.flow.allowed_actions.clear()
        self._open_choice_panel(ready=False)

        self._step(Mock(return_value=False), 3.0)

        self.assertIn(ActionKind.CHOOSE_DISCOVER,
                      self.flow.allowed_actions[0])

    def test_choice_panel_diagnostic_mentions_the_covered_button(self):
        self._open_choice_panel()

        self._step(Mock(return_value=False), 0.0, report_diagnostics=True)

        self.assertTrue(any("「进行选择」面板" in message
                            for message in self.outputs))

    def test_no_choice_panel_keeps_the_plain_gate(self):
        self._step(Mock(return_value=False), 0.0)

        self.assertEqual(frozenset((ActionKind.USE_ABILITY,)),
                         self.flow.allowed_actions[0])


class LocalFlowChoiceGateTests(_TurnGateFixture):
    box_ai = False

    def test_local_strategy_may_click_the_choice_menu(self):
        self._step(Mock(return_value=False), 0.0)

        self.assertEqual(
            frozenset((ActionKind.USE_ABILITY, ActionKind.CHOOSE_DISCOVER)),
            self.flow.allowed_actions[0],
        )


class CalibrationRegistrationTests(unittest.TestCase):
    def test_turn_end_button_defaults_and_calibration_target_agree(self):
        from config import RecommendationConfig

        field = RecommendationConfig.__dataclass_fields__["turn_end_button_roi"]
        self.assertEqual(screen_regions.TURN_END_BUTTON_REGION, field.default)
        self.assertEqual(screen_regions.TURN_END_BUTTON_REGION,
                         FSM_action._TURN_END_BUTTON_ROI)

        targets = screen_regions.calibration_targets(SimpleNamespace(
            turn_end_button_roi=(1400, 440, 1700, 560),
        ))
        target = next(t for t in targets if t["key"] == "turn_end_button")

        self.assertEqual("turn_end_button_roi", target["config_key"])
        self.assertEqual((1400, 440, 1700, 560), target["box"])


if __name__ == "__main__":
    unittest.main()
