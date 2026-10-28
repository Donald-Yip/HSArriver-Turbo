import unittest
from types import SimpleNamespace

import FSM_action
from src.flow.mulligan_flow import MulliganFlow, MulliganStatus
from src.recommendation_config import RecommendationConfig


class MulliganTimingTests(unittest.TestCase):
    def test_each_new_game_waits_ready_delay_before_mulligan_work(self):
        original_flow = FSM_action.auto_mulligan_flow
        original_config = FSM_action.recommendation_config
        original_generation = FSM_action.mulligan_delay_generation
        original_refresh = FSM_action.refresh_snapshot
        original_print = FSM_action.print_out
        original_sleep = FSM_action.time.sleep
        original_manual_controller = FSM_action.manual_controller
        original_log_generation = FSM_action.log_state.game_generation
        elapsed = [0.0]

        def sleep(seconds):
            elapsed[0] += seconds

        try:
            # The per-game ready delay applies even if automation falls back
            # to the manual mulligan path.
            FSM_action.auto_mulligan_flow = None
            FSM_action.recommendation_config = RecommendationConfig()
            FSM_action.mulligan_delay_generation = None
            FSM_action.print_out = lambda: None
            FSM_action.time.sleep = sleep
            FSM_action.manual_controller = SimpleNamespace(
                choose_mulligan=lambda _snapshot: (),
                mulligan_is_current=lambda _before, _after: False,
                output=lambda _message: None,
            )

            # Re-entering the same generation represents a same-game retry;
            # generation 102 represents the following game. Two distinct
            # generations each wait the configured ready delay once.
            expected = 2.0 * RecommendationConfig().mulligan_ready_delay_seconds
            for generation in (101, 101, 102):
                snapshots = iter((
                    SimpleNamespace(is_end=False,
                                    game_num_turns_in_play=0),
                    SimpleNamespace(is_end=False,
                                    game_num_turns_in_play=1),
                ))
                FSM_action.log_state.game_generation = generation
                FSM_action.refresh_snapshot = lambda: next(snapshots)

                result = FSM_action.ChoosingCardAction()

                self.assertEqual(FSM_action.FSM_BATTLING, result)

            self.assertEqual(expected, elapsed[0])
        finally:
            FSM_action.auto_mulligan_flow = original_flow
            FSM_action.recommendation_config = original_config
            FSM_action.mulligan_delay_generation = original_generation
            FSM_action.refresh_snapshot = original_refresh
            FSM_action.print_out = original_print
            FSM_action.time.sleep = original_sleep
            FSM_action.manual_controller = original_manual_controller
            FSM_action.log_state.game_generation = original_log_generation

    def test_production_flow_uses_configured_mulligan_delays(self):
        names = (
            "auto_mulligan_flow",
            "recommendation_flow",
            "recommendation_config",
            "recommendation_capture",
            "mulligan_capture",
            "recommendation_parser",
            "recommendation_reader",
            "mulligan_reader",
            "recommendation_validator",
        )
        originals = {name: getattr(FSM_action, name) for name in names}
        try:
            # Pre-populate the expensive OCR collaborators: initialization
            # only needs to wire them into the production flow for this test.
            FSM_action.recommendation_parser = object()
            FSM_action.recommendation_reader = object()
            FSM_action.mulligan_reader = object()
            FSM_action.recommendation_validator = object()

            FSM_action.initialize_recommendation_automation()

            post_ocr = RecommendationConfig().mulligan_post_ocr_delay_seconds
            self.assertEqual(post_ocr, FSM_action.auto_mulligan_flow.first_delay)
            self.assertEqual(post_ocr, FSM_action.auto_mulligan_flow.retry_delay)
            # 换牌 OCR 用独立的大 ROI，回合推荐仍用小 ROI。
            self.assertEqual(
                FSM_action.recommendation_config.recommendation_roi,
                FSM_action.recommendation_capture.roi)
            self.assertEqual(
                FSM_action.recommendation_config.mulligan_recommendation_roi,
                FSM_action.mulligan_capture.roi)
            self.assertNotEqual(
                FSM_action.recommendation_capture.roi,
                FSM_action.mulligan_capture.roi)
        finally:
            for name, value in originals.items():
                setattr(FSM_action, name, value)

    def test_successful_ocr_clicks_without_an_additional_delay(self):
        cards = [
            SimpleNamespace(card_id=f"CARD_{index}", entity_id=str(index))
            for index in range(3)
        ]
        state = SimpleNamespace(
            my_hand_cards=cards,
            log_revision=8,
            is_end=False,
            game_num_turns_in_play=0,
        )
        action = SimpleNamespace(
            normalized_instruction="替换1号位卡牌",
            mulligan_slots=(1,),
        )
        elapsed = [0.0]

        class Executor:
            def replace_starting_card(self, _index, _count):
                pass

            def commit_choose_card(self):
                pass

            def cancel_click(self):
                pass

        flow = MulliganFlow(
            executor=Executor(),
            action_supplier=lambda: action,
            state_supplier=lambda: state,
            sleep=lambda seconds: elapsed.__setitem__(
                0, elapsed[0] + seconds),
        )

        result = flow.run()

        self.assertEqual(MulliganStatus.CONFIRMED, result.status)
        self.assertEqual(0.0, elapsed[0])


class ConfirmPanelReadyWaitTests(unittest.TestCase):
    """换牌前等待到点后先等「确认」按钮画出来，不再探一次就判"未检测到"。"""

    def setUp(self):
        self._quitting = FSM_action.quitting_flag
        FSM_action.quitting_flag = False

    def tearDown(self):
        FSM_action.quitting_flag = self._quitting

    def test_panel_rendering_late_is_picked_up_without_the_retry_penalty(self):
        readings = []
        sleeps = []

        def probe():
            readings.append(1)
            return len(readings) >= 3

        self.assertTrue(FSM_action._wait_confirm_panel_ready(
            timeout=6.0, interval=0.4, probe=probe, sleep=sleeps.append))
        # 前两次没画好只等 0.4s，第三次出现即返回（不再白等 5s）
        self.assertEqual(3, len(readings))
        self.assertEqual([0.4, 0.4], sleeps)

    def test_panel_that_never_renders_falls_back_after_the_wait_window(self):
        readings = []

        def probe():
            readings.append(1)
            return False

        self.assertFalse(FSM_action._wait_confirm_panel_ready(
            timeout=2.0, interval=0.5, probe=probe, sleep=lambda _s: None))
        # 2.0s / 0.5s = 4 个间隔 → 5 次探测，之后交回重试逻辑
        self.assertEqual(5, len(readings))

    def test_already_ready_panel_costs_no_extra_wait(self):
        sleeps = []
        self.assertTrue(FSM_action._wait_confirm_panel_ready(
            timeout=6.0, interval=0.4, probe=lambda: True,
            sleep=sleeps.append))
        self.assertEqual([], sleeps)


if __name__ == "__main__":
    unittest.main()
