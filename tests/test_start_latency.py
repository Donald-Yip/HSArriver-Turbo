# -*- coding: utf-8 -*-
"""按「开始」后要快：进入对战链路里不能再有固定长等待。

回归背景（用户反馈）：浮窗点「开始对战」后按钮已经变成「对局进行中」，但鼠标要
等好一会（十几秒）才动。根因是进入对战链路上的几段固定等待：

  * MainMenuAction 先 sleep(3)、ChoosingHeroAction 先 sleep(2)；
  * battle_entry 每 3 秒才识别一次界面；
  * 段位读到数字后**无条件**再等 0.6s 复核一次（哪怕这个名次根本不会触发动作）。

这里锁住四件事：① 两个固定 sleep 不再回来；② 启动里程碑会带相对时间打一次；
③ 段位复核只在读数会触发 stop/keep 时才做；④ CTRL.starting 真的会在启动期间置位、
结束（含异常）清掉，浮窗据此显示「启动中…」。
"""

import inspect
import unittest
from unittest.mock import patch

import FSM_action
import log_overlay
import web_ui


class _SyncThread:
    """把 api_start 起的后台线程变成同步执行，便于断言。"""

    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return False


# ================================================================ 固定等待
class NoFixedSleepTests(unittest.TestCase):
    def test_main_menu_action_does_not_wait_three_seconds(self):
        source = inspect.getsource(FSM_action.MainMenuAction)
        self.assertNotIn("time.sleep(3)", source,
                         "主界面已经认出，enter_battle 自己会等，不能再死等 3 秒")

    def test_choosing_hero_action_does_not_wait_two_seconds(self):
        source = inspect.getsource(FSM_action.ChoosingHeroAction)
        self.assertNotIn("time.sleep(2)", source,
                         "段位读取本来就有重试，不能再死等 2 秒")

    def test_both_entry_states_log_a_milestone(self):
        self.assertIn("_start_milestone", inspect.getsource(FSM_action.MainMenuAction))
        self.assertIn("_start_milestone",
                      inspect.getsource(FSM_action.ChoosingHeroAction))


# ================================================================ 启动里程碑
class StartMilestoneTests(unittest.TestCase):
    def setUp(self):
        FSM_action._START_MILESTONES.clear()

    def tearDown(self):
        FSM_action._START_MILESTONES.clear()

    def test_milestone_is_logged_once_with_relative_time(self):
        printed = []
        with (
            patch.object(FSM_action, "_START_T0", 100.0),
            patch.object(FSM_action.time, "monotonic", return_value=102.5),
            patch.object(FSM_action, "sys_print",
                         side_effect=lambda text: printed.append(text)),
        ):
            FSM_action._start_milestone("初始状态=Main Menu")
            FSM_action._start_milestone("初始状态=Main Menu")   # 同一阶段只打一次

        self.assertEqual(1, len(printed))
        self.assertIn("+2.5s", printed[0])
        self.assertIn("初始状态=Main Menu", printed[0])
        self.assertIn("进入对战", printed[0])

    def test_milestone_silently_ignored_without_start_clock(self):
        printed = []
        with (
            patch.object(FSM_action, "_START_T0", None),
            patch.object(FSM_action, "sys_print",
                         side_effect=lambda text: printed.append(text)),
        ):
            FSM_action._start_milestone("初始状态=Main Menu")
        self.assertEqual([], printed)


# ================================================================ 段位复核门禁
def _keep_config(**overrides):
    cfg = {"enabled": True, "mode": "legend_number", "legend_number": 905,
           "action": "keep"}
    cfg.update(overrides)
    return cfg


class RankConfirmGateTests(unittest.TestCase):
    def setUp(self):
        FSM_action._rank_stop_triggered = False
        FSM_action._rank_last_read = None
        FSM_action._rank_last_phase = None

    def _read(self, readings, **kwargs):
        reads = iter(readings)
        calls = []
        sleeps = []

        def fake_read(**ignored):
            calls.append(1)
            return next(reads)

        with (
            patch.object(FSM_action, "rank_stop_settings",
                         return_value=_keep_config(**kwargs)),
            patch.object(FSM_action, "read_rank_region", side_effect=fake_read),
            patch.object(FSM_action.manual_controller, "output"),
            patch.object(FSM_action.time, "sleep",
                         side_effect=lambda seconds: sleeps.append(seconds)),
        ):
            action = FSM_action._read_rank_target(
                attempts=4, wait=0.6, label="选择套牌界面", confirm=True)
        return action, calls, sleeps

    def test_reading_that_triggers_nothing_is_not_re_read(self):
        """名次 12618 > 目标 905：什么都不会做，不该再白读一次 OCR。"""
        action, calls, sleeps = self._read(
            [{"number": 12618, "legend": True, "raw": "12618"}])

        self.assertEqual("", action)
        self.assertEqual(1, len(calls), "不会触发动作的读数不该复核")
        self.assertEqual([], sleeps)

    def test_reading_that_would_trigger_keep_is_still_confirmed(self):
        """名次 100 < 目标 905：会开局即投降，必须复核一次才放心。"""
        action, calls, sleeps = self._read([
            {"number": 100, "legend": True, "raw": "100"},
            {"number": 100, "legend": True, "raw": "100"},
        ])

        self.assertEqual("keep", action)
        self.assertEqual(2, len(calls))
        self.assertTrue(sleeps, "复核前要等 _POST_GAME_RANK_CONFIRM_WAIT")

    def test_one_off_misread_still_cannot_trigger_a_concede(self):
        """会触发动作但复核读不到数字：按「还没读到」处理，绝不投降。"""
        action, calls, _sleeps = self._read([
            {"number": 100, "legend": True, "raw": "100"},
            {"number": None, "legend": False, "raw": None},
        ])

        self.assertEqual("", action)
        self.assertEqual(2, len(calls))


# ================================================================ CTRL.starting
class StartingFlagTests(unittest.TestCase):
    def setUp(self):
        self._saved = (web_ui.CTRL.starting, web_ui.CTRL.automation_thread,
                       web_ui.CTRL.fsm, web_ui.CTRL.prepared)
        web_ui.CTRL.starting = False
        web_ui.CTRL.automation_thread = None

    def tearDown(self):
        (web_ui.CTRL.starting, web_ui.CTRL.automation_thread,
         web_ui.CTRL.fsm, web_ui.CTRL.prepared) = self._saved

    def _patches(self, start_side_effect):
        return (
            patch.object(web_ui, "load_config",
                         return_value={"name": "TestUser#12345",
                                       "log_root": "."}),
            patch.object(web_ui, "_persist_schedule"),
            patch.object(web_ui, "_log"),
            patch.object(web_ui, "_start_automation",
                         side_effect=start_side_effect),
            patch.object(web_ui.threading, "Thread", _SyncThread),
        )

    def test_starting_is_set_during_boot_and_cleared_afterwards(self):
        seen = {}

        def fake_start_automation(reset_stats=True):
            seen["during"] = web_ui.CTRL.starting
            return True, "ok"

        patches = self._patches(fake_start_automation)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = web_ui.api_start({})

        self.assertTrue(result["ok"])
        self.assertTrue(seen.get("during"),
                        "启动期间网页/浮窗要能显示「启动中…」")
        self.assertFalse(web_ui.CTRL.starting, "启动结束后必须清掉")

    def test_starting_is_cleared_even_when_boot_raises(self):
        patches = self._patches(RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                web_ui.api_start({})

        self.assertFalse(web_ui.CTRL.starting)

    def test_second_start_is_refused_while_starting(self):
        web_ui.CTRL.starting = True

        result = web_ui.api_start({})

        self.assertFalse(result["ok"])
        self.assertIn("启动中", result["error"])


# ================================================================ 浮窗反馈
class OverlayStartingFeedbackTests(unittest.TestCase):
    def test_overlay_renders_starting_text(self):
        source = inspect.getsource(log_overlay._run)
        self.assertIn("_IS_STARTING", source)
        self.assertIn("启动中…", source)

    def test_start_accepts_the_is_starting_callback(self):
        self.assertIn("is_starting", inspect.getsource(log_overlay.start))

    def test_web_ui_passes_the_starting_callback_to_the_overlay(self):
        captured = {}

        class _Overlay:
            @staticmethod
            def start(**kwargs):
                captured.update(kwargs)
                return "started"

        with (
            patch.object(web_ui, "log_overlay", _Overlay),
            patch.object(web_ui, "_verify_overlay_opened_async"),
        ):
            web_ui._bind_overlay()

        self.assertIn("is_starting", captured)
        self.assertIs(captured["is_starting"], web_ui._overlay_is_starting)

    def test_starting_callback_reads_the_controller_flag(self):
        with patch.object(web_ui.CTRL, "starting", True):
            self.assertTrue(web_ui._overlay_is_starting())
        with patch.object(web_ui.CTRL, "starting", False):
            self.assertFalse(web_ui._overlay_is_starting())


if __name__ == "__main__":
    unittest.main()
