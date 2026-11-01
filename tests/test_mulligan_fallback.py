# -*- coding: utf-8 -*-
"""换牌兜底：MulliganFallbackTracker + MulliganFlow.mark_panel_ready 回归测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.flow.mulligan_flow import (
    MulliganFallbackTracker,
    MulliganFlow,
    MulliganStatus,
)


class MulliganFallbackTrackerTests(unittest.TestCase):
    def test_activates_at_threshold(self):
        tracker = MulliganFallbackTracker(fail_streak=2)
        self.assertFalse(tracker.active)
        self.assertFalse(tracker.note_box_failure())
        self.assertTrue(tracker.note_box_failure())
        self.assertTrue(tracker.active)

    def test_success_resets_failure_count(self):
        tracker = MulliganFallbackTracker(fail_streak=2)
        tracker.note_box_failure()
        tracker.note_box_success()
        self.assertFalse(tracker.note_box_failure())   # 重新从 1 开始
        self.assertTrue(tracker.note_box_failure())

    def test_reset_clears_state(self):
        tracker = MulliganFallbackTracker(fail_streak=1)
        self.assertTrue(tracker.note_box_failure())
        tracker.reset()
        self.assertFalse(tracker.active)
        self.assertTrue(tracker.note_box_failure())   # 重置后重新从 1 开始


class _Executor:
    def __init__(self):
        self.replaced = []
        self.commits = 0
        self.cancels = 0

    def replace_starting_card(self, index, count):
        self.replaced.append((index, count))

    def commit_choose_card(self):
        self.commits += 1

    def cancel_click(self):
        self.cancels += 1


def _state(count=3):
    cards = [SimpleNamespace(card_id=f"CARD_{i}", entity_id=str(i))
             for i in range(count)]
    return SimpleNamespace(
        my_hand_cards=cards, is_end=False, game_num_turns_in_play=0)


def _flow(sleeps, *, first_delay=20.0):
    return MulliganFlow(
        executor=_Executor(),
        action_supplier=lambda: None,
        state_supplier=lambda: _state(),
        sleep=lambda seconds: sleeps.append(seconds),
        first_delay=first_delay,
        retry_delay=0.0,
        local_chooser=lambda _state: [],
    )


class MulliganMarkReadyTests(unittest.TestCase):
    def test_mark_panel_ready_skips_the_ready_delay(self):
        sleeps = []
        flow = _flow(sleeps)
        result = flow.run_local()
        self.assertIs(MulliganStatus.CONFIRMED, result.status)
        self.assertEqual([20.0], sleeps)

    def test_marked_flow_does_not_wait_again(self):
        sleeps = []
        flow = _flow(sleeps)
        flow.mark_panel_ready()
        result = flow.run_local()
        self.assertIs(MulliganStatus.CONFIRMED, result.status)
        self.assertEqual([], sleeps)          # 兜底接管时不再等 20s


if __name__ == "__main__":
    unittest.main()
