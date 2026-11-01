# -*- coding: utf-8 -*-
"""守护「是否真的会动手」：``web_ui._resident_will_act`` 与 FSM 的让位判断。

三卡合并（2026-11-01）后，「存活检测」「中断重启」都并入了「守护」：
FSM 只剩 ``_guardian_takes_over()``（等价旧的 ``_resident_takes_over``），存活
检测在守护动手时让位、否则自己告警+停止。这里锁两件事：

1. ``web_ui._resident_will_act()`` 只在 enabled、非 dry_run、状态属于
   countdown/starting/running/recovering 时为真；
2. ``FSM_action._guardian_takes_over()`` 用这个结果决定存活检测是否让位
   （含旧版 web_ui 没有该函数 / 回调抛异常时的兼容兜底）。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import FSM_action
import web_ui


class _FakeSupervisor:
    """只实现 state() 的假监督器（真的判定逻辑在 supervisor 单测里覆盖）。"""

    def __init__(self, state: dict):
        self._state = dict(state)

    def state(self) -> dict:
        return dict(self._state)


class ResidentWillActTests(unittest.TestCase):
    def setUp(self):
        self._saved = web_ui.RESIDENT
        self.addCleanup(self._restore)

    def _restore(self):
        web_ui.RESIDENT = self._saved

    def _set(self, **overrides):
        state = {"enabled": True, "state": "running", "dry_run": False}
        state.update(overrides)
        web_ui.RESIDENT = _FakeSupervisor(state)

    def test_active_states_act(self):
        for state in ("countdown", "starting", "running", "recovering"):
            with self.subTest(state=state):
                self._set(state=state)
                self.assertTrue(web_ui._resident_will_act())

    def test_paused_or_error_states_do_not_act(self):
        for state in ("paused", "paused_exhausted", "error", "disabled", "cancelled"):
            with self.subTest(state=state):
                self._set(state=state)
                self.assertFalse(web_ui._resident_will_act())

    def test_dry_run_or_disabled_does_not_act(self):
        self._set(dry_run=True)
        self.assertFalse(web_ui._resident_will_act())
        self._set(enabled=False)
        self.assertFalse(web_ui._resident_will_act())

    def test_missing_supervisor_does_not_act(self):
        web_ui.RESIDENT = None
        self.assertFalse(web_ui._resident_will_act())


class GuardianDelegationTests(unittest.TestCase):
    """FSM 的「存活检测是否让位」开关要跟着 _resident_will_act 走。"""

    def test_true_when_resident_will_act(self):
        with patch.object(web_ui, "_resident_will_act", return_value=True):
            self.assertTrue(FSM_action._guardian_takes_over())

    def test_false_when_resident_paused(self):
        with patch.object(web_ui, "_resident_will_act", return_value=False):
            self.assertFalse(FSM_action._guardian_takes_over())

    def test_legacy_web_ui_without_helper_returns_false(self):
        saved = web_ui._resident_will_act
        del web_ui._resident_will_act
        try:
            self.assertFalse(FSM_action._guardian_takes_over())
        finally:
            web_ui._resident_will_act = saved

    def test_helper_exception_returns_false(self):
        with patch.object(web_ui, "_resident_will_act",
                          side_effect=RuntimeError("boom")):
            self.assertFalse(FSM_action._guardian_takes_over())

    def test_no_web_ui_module_returns_false(self):
        import sys

        saved = sys.modules.pop("web_ui", None)
        try:
            self.assertFalse(FSM_action._guardian_takes_over())
        finally:
            if saved is not None:
                sys.modules["web_ui"] = saved


if __name__ == "__main__":
    unittest.main()
