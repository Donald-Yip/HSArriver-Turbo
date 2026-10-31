# -*- coding: utf-8 -*-
"""我方回合内的「扫描心跳」与空状态诊断（R18H）。

用户 2026-10-31：「直到扫描到回合结束并点击，否则应该一直扫描并执行」。
本文件锁住三件事：

1. ``FSM_action._scan_tick``：我方回合内每 2s 至少读一次屏；对手回合/未进局不读；
   探针异常不外溢；
2. ``FSM_action._scan_aware_sleep``：流程内的等待（操作后延时/抽牌延时/结果等待）
   也带心跳，且**不把配置的延时拉长**；
3. ``FSM_action.automation_phase_state`` / ``web_ui._overlay_ocr_phase``：
   浮窗空状态要能说明“当前卡在哪一步、日志多久没更新”。
"""

import unittest
from types import SimpleNamespace
from unittest import mock

import FSM_action
import web_ui


class ScanTickTests(unittest.TestCase):
    def setUp(self):
        self.saved = (
            FSM_action._my_turn_active,
            FSM_action._last_scan_tick_at,
            FSM_action.log_state.game_entity_id,
        )
        FSM_action._my_turn_active = True
        FSM_action._last_scan_tick_at = 0.0
        FSM_action.log_state.game_entity_id = 1

    def tearDown(self):
        (FSM_action._my_turn_active,
         FSM_action._last_scan_tick_at,
         FSM_action.log_state.game_entity_id) = self.saved

    def test_tick_probes_at_most_once_per_interval(self):
        calls = []
        clock = [100.0]
        with (
            mock.patch.object(
                FSM_action, "end_turn_button_present",
                side_effect=lambda: calls.append(1)),
            mock.patch.object(FSM_action.time, "monotonic",
                              side_effect=lambda: clock[0]),
        ):
            FSM_action._scan_tick(force=True)      # 首次探测
            FSM_action._scan_tick()                # 同一瞬间不重复
            clock[0] = 101.0
            FSM_action._scan_tick()                # 间隔未到
            clock[0] = 102.5
            FSM_action._scan_tick()                # 间隔已到

        self.assertEqual(2, len(calls))

    def test_opponent_turn_or_pregame_never_probes(self):
        def probe(my_turn, game_entity_id):
            calls = []
            FSM_action._my_turn_active = my_turn
            FSM_action.log_state.game_entity_id = game_entity_id
            with mock.patch.object(
                    FSM_action, "end_turn_button_present",
                    side_effect=lambda: calls.append(1)):
                FSM_action._scan_tick(force=True)
            return calls

        cases = (
            ("opponent turn", dict(my_turn=False, game_entity_id=1)),
            ("before the game", dict(my_turn=True, game_entity_id=0)),
        )
        for label, kwargs in cases:
            with self.subTest(case=label):
                self.assertEqual([], probe(**kwargs))

    def test_probe_failure_does_not_escape(self):
        with mock.patch.object(
                FSM_action, "end_turn_button_present",
                side_effect=RuntimeError("ocr boom")):
            FSM_action._scan_tick(force=True)  # 不抛


class ScanAwareSleepTests(unittest.TestCase):
    def test_wait_ticks_without_stretching_the_delay(self):
        clock = [0.0]
        ticks = []

        def fake_sleep(seconds):
            clock[0] += seconds

        with (
            mock.patch.object(FSM_action, "_scan_tick",
                              side_effect=lambda force=False: ticks.append(clock[0])),
            mock.patch.object(FSM_action.time, "sleep", side_effect=fake_sleep),
            mock.patch.object(FSM_action.time, "monotonic",
                              side_effect=lambda: clock[0]),
            mock.patch.object(FSM_action.shutdown_event, "is_set",
                              return_value=False),
        ):
            FSM_action._scan_aware_sleep(3.0)

        self.assertGreaterEqual(len(ticks), 6)
        self.assertAlmostEqual(3.0, clock[0], places=3)

    def test_stop_request_ends_the_wait_early(self):
        clock = [0.0]
        with (
            mock.patch.object(FSM_action, "_scan_tick"),
            mock.patch.object(FSM_action.time, "sleep",
                              side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)),
            mock.patch.object(FSM_action.time, "monotonic",
                              side_effect=lambda: clock[0]),
            mock.patch.object(FSM_action.shutdown_event, "is_set",
                              return_value=True),
        ):
            FSM_action._scan_aware_sleep(10.0)

        self.assertAlmostEqual(0.0, clock[0], places=3)

    def test_bad_value_is_treated_as_zero(self):
        with mock.patch.object(FSM_action, "_scan_tick") as tick:
            FSM_action._scan_aware_sleep("bad")
            FSM_action._scan_aware_sleep(None)

        tick.assert_not_called()


class BattleStepHeartbeatTests(unittest.TestCase):
    """run_automatic_battle_step：我方回合强制先扫一次，对手回合把表清零。"""

    def setUp(self):
        self.saved = (
            FSM_action._my_turn_active,
            FSM_action._last_scan_tick_at,
            FSM_action.recommendation_flow,
        )
        FSM_action._my_turn_active = False
        FSM_action._last_scan_tick_at = 123.0
        FSM_action.recommendation_flow = None

    def tearDown(self):
        (FSM_action._my_turn_active,
         FSM_action._last_scan_tick_at,
         FSM_action.recommendation_flow) = self.saved

    def _step(self, is_my_turn):
        snapshot = SimpleNamespace(
            is_end=False, is_my_turn=is_my_turn,
            game_num_turns_in_play=5, start_of_game_card_count=0)
        with (
            mock.patch.object(FSM_action, "refresh_snapshot", return_value=snapshot),
            mock.patch.object(FSM_action, "_maybe_surrender_by_rules",
                              return_value=None),
            mock.patch.object(FSM_action, "_human_like_opponent_minions"),
            mock.patch.object(FSM_action, "_report_automation_diagnostic"),
            mock.patch.object(FSM_action, "_player_turn_ready", return_value=False),
            mock.patch.object(FSM_action, "_scan_tick") as tick,
        ):
            FSM_action.run_automatic_battle_step()
        return tick

    def test_heartbeat_tracks_my_turn(self):
        tick = self._step(is_my_turn=True)

        self.assertTrue(FSM_action._my_turn_active)
        tick.assert_called_with(force=True)

        FSM_action._last_scan_tick_at = 123.0
        tick = self._step(is_my_turn=False)

        self.assertFalse(FSM_action._my_turn_active)
        self.assertEqual(0.0, FSM_action._last_scan_tick_at)
        tick.assert_not_called()


class AutomationPhaseStateTests(unittest.TestCase):
    def setUp(self):
        self.saved = (
            FSM_action.last_automation_diagnostic,
            FSM_action._log_revision_at,
        )
        FSM_action.last_automation_diagnostic = None
        FSM_action._log_revision_at = None

    def tearDown(self):
        (FSM_action.last_automation_diagnostic,
         FSM_action._log_revision_at) = self.saved

    def test_known_codes_and_prefixes_get_chinese_labels(self):
        cases = (
            ("opponent_turn", "等待对手操作"),
            ("waiting_turn_end_button", "等待「结束回合」按钮"),
            ("power_log_unavailable", "Power.log 暂不可用"),
            ("observe:opponent_turn", "等待对手操作"),
            ("retry:awaiting_action_confirmation", "等待动作结果确认"),
        )
        for code, label in cases:
            with self.subTest(code=code):
                FSM_action.last_automation_diagnostic = code

                self.assertEqual(label,
                                 FSM_action.automation_phase_state()["label"])

    def test_unknown_code_and_missing_code(self):
        FSM_action.last_automation_diagnostic = "weird_code"
        state = FSM_action.automation_phase_state()
        self.assertEqual("weird_code", state["label"])
        self.assertEqual("weird_code", state["code"])

        FSM_action.last_automation_diagnostic = None
        self.assertEqual("", FSM_action.automation_phase_state()["label"])

    def test_log_idle_seconds_follow_the_revision_clock(self):
        FSM_action.last_automation_diagnostic = "opponent_turn"
        with mock.patch.object(FSM_action.time, "monotonic", return_value=100.0):
            self.assertIsNone(FSM_action.automation_phase_state()["log_idle_s"])
            FSM_action._log_revision_at = 95.0
            self.assertAlmostEqual(
                5.0, FSM_action.automation_phase_state()["log_idle_s"])


class WebPhaseWiringTests(unittest.TestCase):
    def test_phase_callback_forwards_state_and_swallows_errors(self):
        payload = {"code": "opponent_turn", "label": "等待对手操作",
                   "log_idle_s": 3.0}
        saved = web_ui.CTRL.fsm
        try:
            web_ui.CTRL.fsm = SimpleNamespace(
                automation_phase_state=lambda: payload)
            self.assertEqual(payload, web_ui._overlay_ocr_phase())

            web_ui.CTRL.fsm = None
            self.assertIsNone(web_ui._overlay_ocr_phase())

            def boom():
                raise RuntimeError("boom")

            web_ui.CTRL.fsm = SimpleNamespace(automation_phase_state=boom)
            self.assertIsNone(web_ui._overlay_ocr_phase())
        finally:
            web_ui.CTRL.fsm = saved

    def test_bind_overlay_passes_the_phase_callback(self):
        bound = {}
        overlay = SimpleNamespace(
            start=lambda **kwargs: bound.update(kwargs),
            is_running=lambda: False)
        with (
            mock.patch.object(web_ui, "log_overlay", overlay),
            mock.patch.object(web_ui, "_verify_overlay_opened_async"),
        ):
            web_ui._bind_overlay()

        self.assertIn("phase_callback", bound)
        self.assertIs(bound["phase_callback"], web_ui._overlay_ocr_phase)


if __name__ == "__main__":
    unittest.main()
