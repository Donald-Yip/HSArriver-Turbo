# -*- coding: utf-8 -*-
"""「取消」必须真的停手 —— 用户 2026-10-17 反馈的回归测试。

现象：手动「进入对战」跑到一半用户喊停（Ctrl+C / 网页 / agent），控制台那个
后台线程**照样一遍遍点画面中心**，直到 180s 超时才停。根因是三条链路都缺：

1. `api_enter_battle` 起后台线程时没把 `should_abort` 传下去，默认 `lambda: False`；
2. `api_stop` 只管 FSM，自动化没在跑时直接回「当前没有正在运行的自动化」；
3. CLI 收到 KeyboardInterrupt 只是本地退出，没通知控制台 —— 真正的点击在
   控制台那边，杀本地进程等于什么都没停。

这里把修复钉住：全局急停开关 + `/api/cancel` + `hsctl cancel` + Ctrl+C 兜底。
"""
from __future__ import annotations

import contextlib
import io
import json
import threading
import unittest
import unittest.mock
from unittest.mock import patch

import battle_entry
import hsctl
import web_ui

from tests.test_hsctl import _FakeConsole, run_cli


class _Clock:
    """假时钟：`sleep` 只推进时间，`monotonic` 读它，永不真等。"""

    def __init__(self, cancel_after_sleeps=None):
        self.t = 0.0
        self.sleeps = 0
        self.cancel_after_sleeps = cancel_after_sleeps

    def sleep(self, seconds):
        self.t += float(seconds)
        self.sleeps += 1
        if (self.cancel_after_sleeps is not None
                and self.sleeps >= self.cancel_after_sleeps):
            battle_entry.request_cancel()

    def monotonic(self):
        return self.t


class _Clicks:
    """记录被点过的坐标。

    **不要实现 `__len__`**：被测代码写的是 `click_at_fn or click_at`，空记录器
    会被当成假值、退回真实点击（会真的点屏幕）。所以这里只保留 `.points`。
    """

    def __init__(self):
        self.points = []

    def __call__(self, x, y):
        self.points.append((int(x), int(y)))


def _unknown_frame(*_args, **_kwargs):
    """给 `capture` 用：永远返回一帧（于是循环会继续轮询）。

    注意 `capture` 的契约是 `(frame, mapping)` 元组 —— 返回裸对象会让
    `_capture_frame` 解包失败、当成"抓不到"，循环就变成死等。
    """
    return object(), None


def _no_frame(*_args, **_kwargs):
    return None, None


def _cancel_off(test):
    """每个用例前后都把急停开关清干净，避免互相污染。"""
    battle_entry.clear_cancel()
    test.addCleanup(battle_entry.clear_cancel)


class CancelSwitchTests(unittest.TestCase):
    """急停开关本身：置位/清除的语义。"""

    def setUp(self):
        _cancel_off(self)

    def test_default_is_not_cancelled(self):
        self.assertFalse(battle_entry.is_cancelled())

    def test_request_and_clear(self):
        battle_entry.request_cancel()
        self.assertTrue(battle_entry.is_cancelled())
        battle_entry.clear_cancel()
        self.assertFalse(battle_entry.is_cancelled())


class WaitLoopCancelTests(unittest.TestCase):
    """等待循环：一旦取消就退出，且**不再点**任何坐标。"""

    def setUp(self):
        _cancel_off(self)

    def test_wait_for_main_menu_aborts_and_stops_clicking(self):
        clock = _Clock(cancel_after_sleeps=6)
        clicks = _Clicks()
        with patch.object(battle_entry, "identify",
                          return_value={"state": "unknown"}):
            result = battle_entry.wait_for_main_menu(
                timeout=180.0, interval=0.5,
                capture=_unknown_frame, click_at_fn=clicks,
                sleep=clock.sleep, clock=clock.monotonic)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "aborted")
        # 取消发生在第 6 次 sleep（约 3.0s）：中心点最多被点 2 下，绝不是 60 下。
        self.assertLessEqual(len(clicks.points), 3)
        self.assertLess(clock.t, 10.0)

    def test_no_click_after_cancel_is_observed(self):
        """已经置位时：一次都不点，立刻返回。"""
        battle_entry.request_cancel()
        clicks = _Clicks()
        result = battle_entry.wait_for_main_menu(
            timeout=180.0, interval=0.5, capture=_unknown_frame,
            click_at_fn=clicks, sleep=_Clock().sleep,
            clock=_Clock().monotonic)
        self.assertEqual(result["reason"], "aborted")
        self.assertEqual(len(clicks.points), 0)

    def test_wait_for_deck_select_is_cancellable_too(self):
        clock = _Clock(cancel_after_sleeps=4)
        clicks = _Clicks()
        with patch.object(battle_entry, "identify",
                          return_value={"state": "unknown"}):
            result = battle_entry.wait_for_deck_select(
                timeout=90.0, interval=0.5, capture=_unknown_frame,
                click_at_fn=clicks, sleep=clock.sleep, clock=clock.monotonic)
        self.assertEqual(result["reason"], "aborted")
        self.assertLessEqual(len(clicks.points), 3)


class EnterBattleCancelTests(unittest.TestCase):
    """`enter_battle` 的 `stage` 必须如实报 aborted，别把取消说成超时。"""

    def setUp(self):
        _cancel_off(self)

    def test_cancelled_before_start_returns_aborted(self):
        battle_entry.request_cancel()
        result = battle_entry.enter_battle(
            capture=_no_frame, click_at_fn=_Clicks(),
            window_present=lambda: True,
            sleep=_Clock().sleep, clock=_Clock().monotonic)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "aborted")

    def test_cancel_between_waits_does_not_click_traditional(self):
        """认到主界面后、点「传统对战」之前取消 → 那一下绝不许点出去。"""
        clicks = _Clicks()
        ui = {"state": "main_menu", "traditional": {"point": (960, 300),
                                                    "score": 0.9}}

        def fake_identify(*_args, **_kwargs):
            return ui

        # 第一次 wait 认到主界面（此时还没取消），随后在点之前置位。
        battle_entry.clear_cancel()
        with patch.object(battle_entry, "identify", side_effect=fake_identify), \
                patch.object(battle_entry, "wait_for_main_menu",
                             return_value={"ok": True, "ui": ui,
                                           "message": "已识别到主界面"}):
            def clicker(x, y):
                clicks(x, y)
                battle_entry.request_cancel()   # 点完立刻喊停

            result = battle_entry.enter_battle(
                capture=_unknown_frame, click_at_fn=clicker,
                window_present=lambda: True,
                sleep=_Clock().sleep, clock=_Clock().monotonic)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "aborted")
        # 只点了「传统对战」那一下，之后没再点任何东西（中心点一下都没点）。
        self.assertEqual(clicks.points, [(960, 300)])


class DeckClickCancelTests(unittest.TestCase):
    """取消状态下不允许点卡组（防止刚喊停又点出去一格）。"""

    def setUp(self):
        _cancel_off(self)

    def test_select_deck_refuses_when_cancelled(self):
        battle_entry.request_cancel()
        clicks = _Clicks()
        result = battle_entry.select_deck(3, capture=_no_frame,
                                          click_at_fn=clicks)
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("cancelled"))
        self.assertEqual(len(clicks.points), 0)


class CancelApiTests(unittest.TestCase):
    """`/api/cancel`：只列真正在跑的东西，不乱置位。"""

    def setUp(self):
        _cancel_off(self)
        self.saved_enter = dict(web_ui._ENTER_STATE)
        self.saved_restart = dict(web_ui._RESTART_STATE)
        self.saved_thread = web_ui.CTRL.automation_thread
        self.saved_fsm = web_ui.CTRL.fsm
        self.addCleanup(self._restore)
        with patch.object(web_ui, "_log"):
            pass

    def _restore(self):
        web_ui._ENTER_STATE.update(self.saved_enter)
        web_ui._RESTART_STATE.update(self.saved_restart)
        web_ui.CTRL.automation_thread = self.saved_thread
        web_ui.CTRL.fsm = self.saved_fsm

    def test_idle_cancel_reports_nothing_and_keeps_switch_clean(self):
        web_ui._ENTER_STATE["running"] = False
        web_ui._RESTART_STATE["running"] = False
        web_ui.CTRL.automation_thread = None
        with patch.object(web_ui, "_log"):
            result = web_ui.api_cancel({})
        self.assertTrue(result["ok"])
        self.assertEqual(result["cancelled"], [])
        self.assertIn("没有正在跑的动作", result["message"])
        # 关键：空闲时喊停不能给下一次单击留个"已取消"的坑。
        self.assertFalse(battle_entry.is_cancelled())

    def test_cancel_flips_switch_while_enter_battle_runs(self):
        web_ui._ENTER_STATE["running"] = True
        with patch.object(web_ui, "_log"):
            result = web_ui.api_cancel({})
        self.assertTrue(battle_entry.is_cancelled())
        self.assertTrue(any("进入对战" in item for item in result["cancelled"]))

    def test_cancel_stops_running_fsm(self):
        class _Fsm:
            def __init__(self):
                self.called = False

            def request_immediate_stop(self):
                self.called = True

        fsm = _Fsm()
        web_ui.CTRL.automation_thread = object()
        web_ui.CTRL.fsm = fsm
        with patch.object(web_ui, "_log"):
            result = web_ui.api_cancel({})
        self.assertTrue(fsm.called)
        self.assertTrue(any("自动化" in item for item in result["cancelled"]))


class EnterBattleFastPathTests(unittest.TestCase):
    """已经站在「选择套牌」界面时不该再等主界面（更不该点画面中心）。"""

    def setUp(self):
        _cancel_off(self)

    def test_already_at_deck_select_goes_straight_to_deck_and_start(self):
        ui = {"state": "deck_select",
              "deck_title": {"point": (700, 130), "score": 1.0},
              "start": {"point": (1500, 900), "score": 0.92},
              "traditional": {"point": (900, 300), "score": 0.08}}
        clicks = _Clicks()
        started = []

        def fake_start(**kwargs):
            started.append(True)
            return {"ok": True, "message": "已点击「开始」"}

        with patch.object(battle_entry, "identify", return_value=ui), \
                patch.object(battle_entry, "deck_slot_point",
                             return_value=(1000, 700)), \
                patch.object(battle_entry, "plate_pixels",
                             return_value=[999] * 9):
            result = battle_entry.enter_battle(
                deck_index=9, click_start=True, capture=_unknown_frame,
                click_at_fn=clicks, start_clicker=fake_start,
                window_present=lambda: True,
                sleep=_Clock().sleep, clock=_Clock().monotonic)

        self.assertTrue(result["ok"])
        self.assertEqual(result["stage"], "done")
        stages = [step["stage"] for step in result["steps"]]
        self.assertIn("already_deck_select", stages)
        self.assertNotIn("wait_main_menu", stages)
        self.assertNotIn("click_traditional", stages)
        # 全程只点了第 9 格那一下，没有任何"点画面中心"的探测。
        self.assertEqual(clicks.points, [(1000, 700)])
        self.assertEqual(started, [True])


class CtrlQHotkeyTests(unittest.TestCase):
    """Ctrl+Q = 全局急停（用户定的：任何地方按都要停，不要用 Ctrl+C）。

    历史坑：热键只在自动化启动时注册、自动化一停就注销，于是「手动进对战正在
    连点画面中心」时按 Ctrl+Q 完全没反应。这里钉住三条：
    ① 回调走统一急停通道；② 注册是幂等的、随控制台常驻；③ 自动化结束不注销。
    """

    def setUp(self):
        _cancel_off(self)
        self.saved_enter = dict(web_ui._ENTER_STATE)
        self.saved_thread = web_ui.CTRL.automation_thread
        self.saved_fsm = web_ui.CTRL.fsm
        self.saved_hotkey = web_ui.CTRL.hotkey_registered
        self.addCleanup(self._restore)

    def _restore(self):
        web_ui._ENTER_STATE.update(self.saved_enter)
        web_ui.CTRL.automation_thread = self.saved_thread
        web_ui.CTRL.fsm = self.saved_fsm
        web_ui.CTRL.hotkey_registered = self.saved_hotkey

    def test_ctrl_q_stops_manual_enter_battle(self):
        web_ui._ENTER_STATE["running"] = True
        web_ui.CTRL.automation_thread = None
        with patch.object(web_ui, "_log") as logger, \
                patch.object(web_ui, "_persist_schedule"):
            web_ui._on_ctrl_q()
        # 关键：手动流程真的被叫停（旧实现只停 FSM，这里一点用都没有）。
        self.assertTrue(battle_entry.is_cancelled())
        messages = [c.args[1] for c in logger.call_args_list]
        self.assertTrue(any("Ctrl+Q" in m for m in messages))

    def test_ctrl_q_when_idle_reports_nothing_and_keeps_switch_clean(self):
        web_ui._ENTER_STATE["running"] = False
        web_ui._RESTART_STATE["running"] = False
        web_ui.CTRL.automation_thread = None
        with patch.object(web_ui, "_log"), \
                patch.object(web_ui, "_persist_schedule"):
            web_ui._on_ctrl_q()
        self.assertFalse(battle_entry.is_cancelled())

    def test_register_hotkey_is_idempotent(self):
        calls = []

        class _FakeKeyboard:
            def remove_hotkey(self, name):
                calls.append(("remove", name))

            def add_hotkey(self, name, callback):
                calls.append(("add", name))

        with patch.dict("sys.modules", {"keyboard": _FakeKeyboard()}), \
                patch.object(web_ui, "_log"), \
                patch.object(web_ui, "_on_ctrl_q", lambda: None):
            web_ui._register_hotkey()
            first = list(calls)
            web_ui._register_hotkey()
        self.assertEqual([("remove", "ctrl+q"), ("add", "ctrl+q")], first)
        self.assertEqual(4, len(calls), "重复注册要先摘掉旧的，不能叠两条")
        self.assertTrue(web_ui.CTRL.hotkey_registered)

    def test_hotkey_stays_after_automation_ends(self):
        """自动化跑完不能把 Ctrl+Q 一起注销（那段时间正是最需要它的时候）。"""

        class _Fsm:
            game_count = 0
            win_count = 0
            concede_count = 0

            def AutoHS_automata(self):
                return None

            def print_info_close(self):
                return None

        removed = []
        with patch.object(web_ui, "_bind_overlay"), \
                patch.object(web_ui, "_stdout_capture_stop"), \
                patch.object(web_ui, "_log"), \
                patch.object(web_ui, "_persist_schedule"), \
                patch.object(web_ui, "_remove_hotkey",
                             side_effect=lambda: removed.append(True)):
            web_ui._automation_worker(_Fsm())
        self.assertEqual([], removed)


class CancelCliTests(unittest.TestCase):
    """命令行侧：`hsctl cancel` 与 Ctrl+C 兜底。"""

    def setUp(self):
        _cancel_off(self)
        self.console = _FakeConsole({})
        self.addCleanup(self.console.close)

    def _base(self):
        return ["--port", str(self.console.port)]

    def test_cancel_posts_api_cancel(self):
        self.console.httpd.routes["/api/cancel"] = {
            "ok": True, "cancelled": ["进入对战（等待/点击循环）"],
            "message": "已取消：进入对战（等待/点击循环）"}
        code, out, _ = run_cli([*self._base(), "cancel"])
        self.assertEqual(code, hsctl.EXIT_OK)
        self.assertIn("已取消", out)
        self.assertEqual([p[1] for p in self.console.posts()], ["/api/cancel"])

    def test_ctrl_c_during_enter_battle_sends_cancel(self):
        self.console.httpd.routes["/api/enter_battle"] = {
            "ok": True, "message": "已开始进入对战",
            "enter_battle": {"running": True, "stage": "wait_main_menu"}}
        self.console.httpd.routes["/api/cancel"] = {
            "ok": True, "cancelled": ["进入对战（等待/点击循环）"],
            "message": "已取消：进入对战（等待/点击循环）"}

        def _interrupt(_seconds):
            raise KeyboardInterrupt

        with patch.object(hsctl.time, "sleep", side_effect=_interrupt):
            code, _, err = run_cli([*self._base(), "enter-battle"])
        self.assertEqual(code, hsctl.EXIT_API)
        self.assertIn("叫停", err)
        self.assertIn("/api/cancel", [p[1] for p in self.console.posts()])

    def test_enter_battle_does_not_override_console_interval(self):
        """CLI 不再默认下发 interval=3.0（那会把控制台的 0.5s 轮询盖掉）。"""
        self.console.httpd.routes["/api/enter_battle"] = {
            "ok": True, "message": "已开始",
            "enter_battle": {"running": False, "stage": "done", "ok": True}}
        code, _, _ = run_cli([*self._base(), "enter-battle", "--no-wait"])
        self.assertEqual(code, hsctl.EXIT_OK)
        body = self.console.posts()[-1][2]
        self.assertNotIn("interval", body)

    def test_explicit_interval_is_still_forwarded(self):
        self.console.httpd.routes["/api/enter_battle"] = {
            "ok": True, "message": "已开始",
            "enter_battle": {"running": False, "stage": "done", "ok": True}}
        run_cli([*self._base(), "enter-battle", "--interval", "2",
                 "--no-wait"])
        self.assertEqual(self.console.posts()[-1][2]["interval"], 2.0)


if __name__ == "__main__":
    unittest.main()
