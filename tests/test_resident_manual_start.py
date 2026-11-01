"""手动「开始对战」不得被常驻当成日志卡死 / 抢启动（用户反馈回归）。

背景：常驻监督下用户人为暂停很久（Power.log 的年龄很大）再点「开始对战」：
- api_start 的 boot 窗口里 ``CTRL.starting=True``，但 ``automation_thread`` 还没挂上；
- 旧实现 ``_resident_automation_running()`` 不看 starting → 监督器以为自动化没在跑，
  ``_resident_start_automation()`` 直接走「进对战→模式→卡组」装配，和用户抢鼠标；
- 启动完成后第一次 poll 又可能拿暂停期间累积的 log 年龄判 stale，触发恢复。

修复：starting 也算“正在跑”；启动时通知监督器 ``note_automation_started()``，
让暂停期间的空档在 settle 宽限期内不参与卡死判定。
"""

import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import web_ui


class _Clock:
    """可推进的假时钟（监督器全部用它，避免真 sleep）。"""

    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += float(seconds)


class _NoStartThread:
    """_start_automation 起的后台线程替身：只接管参数，不执行 worker。"""

    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        return None


class _SyncThread:
    """把 api_start 起的 boot 线程变成同步执行。"""

    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)

    def is_alive(self):
        return False


def _fake_fsm():
    """最小可用的 FSM_action 替身（不碰炉石、不点鼠标）。"""
    module = types.ModuleType("FSM_action")
    module.game_count = 0
    module.win_count = 0
    module.concede_count = 0
    module.quitting_flag = False
    module.stop_after_current_game = False
    module.FSM_state = ""
    module.time_begin = 0.0
    module.print_info_init = lambda: None
    module.print_info_close = lambda: None
    module.init = lambda: None
    module.AutoHS_automata = lambda: None
    return module


class _WebStateCase(unittest.TestCase):
    """保存/恢复 web_ui 全局运行态，测试之间互不污染。"""

    def setUp(self):
        self._saved = {
            "thread": web_ui.CTRL.automation_thread,
            "starting": web_ui.CTRL.starting,
            "fsm": web_ui.CTRL.fsm,
            "phase": web_ui.CTRL.phase,
            "stopped_by": web_ui.CTRL.stopped_by,
            "resident": web_ui.RESIDENT,
            "arm_pending": web_ui._RESIDENT_ARM_PENDING,
            "arm_running": web_ui._RESIDENT_ARM_RUNNING,
        }
        web_ui.CTRL.automation_thread = None
        web_ui.CTRL.starting = False
        web_ui.CTRL.fsm = None
        web_ui.CTRL.phase = "idle"
        web_ui.CTRL.stopped_by = None
        web_ui.RESIDENT = None
        web_ui._RESIDENT_ARM_PENDING = False
        web_ui._RESIDENT_ARM_RUNNING = False

    def tearDown(self):
        web_ui.CTRL.automation_thread = self._saved["thread"]
        web_ui.CTRL.starting = self._saved["starting"]
        web_ui.CTRL.fsm = self._saved["fsm"]
        web_ui.CTRL.phase = self._saved["phase"]
        web_ui.CTRL.stopped_by = self._saved["stopped_by"]
        web_ui.RESIDENT = self._saved["resident"]
        web_ui._RESIDENT_ARM_PENDING = self._saved["arm_pending"]
        web_ui._RESIDENT_ARM_RUNNING = self._saved["arm_running"]


# ------------------------------------------------- starting 也算“在跑”
class AutomationRunningSignalTests(_WebStateCase):
    def test_starting_counts_as_automation_running(self):
        web_ui.CTRL.automation_thread = None
        web_ui.CTRL.starting = True
        self.assertTrue(web_ui._resident_automation_running())

    def test_arm_running_counts_as_automation_running(self):
        web_ui.CTRL.automation_thread = None
        web_ui.CTRL.starting = False
        web_ui._RESIDENT_ARM_RUNNING = True
        self.assertTrue(web_ui._resident_automation_running())

    def test_idle_is_not_running(self):
        web_ui.CTRL.automation_thread = None
        web_ui.CTRL.starting = False
        web_ui._RESIDENT_ARM_RUNNING = False
        self.assertFalse(web_ui._resident_automation_running())


class StartAutomationGuardTests(_WebStateCase):
    def test_start_automation_does_not_take_over_while_starting(self):
        web_ui.CTRL.starting = True
        web_ui._RESIDENT_ARM_PENDING = True
        logs = []
        with (
            patch.object(web_ui, "_resident_arm_dispatch") as dispatch,
            patch.object(web_ui, "api_start") as api,
            patch.object(web_ui, "_log",
                         side_effect=lambda level, msg: logs.append((level, msg))),
        ):
            self.assertTrue(web_ui._resident_start_automation())

        dispatch.assert_not_called()          # 不装配/不点鼠标
        api.assert_not_called()               # 也不盲目 api_start
        self.assertTrue(web_ui._RESIDENT_ARM_PENDING)   # 不动用户的装配待办
        self.assertTrue(any("先不接管" in msg for _level, msg in logs))


# ------------------------------------------------- 启动即进入 settle
class NoteAutomationStartedTests(_WebStateCase):
    def test_note_helper_calls_new_supervisor_method(self):
        noted = []
        web_ui.RESIDENT = SimpleNamespace(
            note_automation_started=lambda: noted.append(1))
        with patch.object(web_ui, "_log"):
            web_ui._resident_note_automation_started()
        self.assertEqual([1], noted)

    def test_note_helper_noops_on_old_supervisor_without_method(self):
        web_ui.RESIDENT = SimpleNamespace()     # R1 未落盘的旧监督器
        with patch.object(web_ui, "_log") as log:
            web_ui._resident_note_automation_started()
        log.assert_not_called()

    def test_note_helper_swallows_exceptions(self):
        def _boom():
            raise RuntimeError("settle boom")

        web_ui.RESIDENT = SimpleNamespace(note_automation_started=_boom)
        logs = []
        with patch.object(web_ui, "_log",
                          side_effect=lambda level, msg: logs.append((level, msg))):
            web_ui._resident_note_automation_started()
        self.assertTrue(any(level == "WARN" for level, _msg in logs))

    def test_note_helper_does_not_lazily_create_supervisor(self):
        web_ui.RESIDENT = None
        with patch.object(web_ui, "_resident_supervisor") as lazy:
            web_ui._resident_note_automation_started()
        lazy.assert_not_called()

    def test_start_automation_notifies_settle(self):
        """手动启动成功路径（_start_automation）必须通知常驻进入 settle。"""
        noted = []
        web_ui.RESIDENT = SimpleNamespace(
            note_automation_started=lambda: noted.append(1))
        fake = _fake_fsm()
        with (
            patch.dict(sys.modules, {"FSM_action": fake}),
            patch.object(web_ui, "load_config",
                         return_value={"name": "TestUser#12345",
                                       "log_root": "."}),
            patch.object(web_ui, "_apply_constants"),
            patch.object(web_ui, "_begin_battle_session"),
            patch.object(web_ui, "_stdout_capture_start"),
            patch.object(web_ui, "_register_hotkey"),
            patch.object(web_ui, "_log"),
            patch.object(web_ui.threading, "Thread", _NoStartThread),
        ):
            ok, _msg = web_ui._start_automation()

        self.assertTrue(ok)
        self.assertEqual([1], noted)
        self.assertIsNotNone(web_ui.CTRL.automation_thread)

    def test_manual_api_start_notifies_settle(self):
        """手动「开始对战」在 api_start 置 starting 后立刻 note，boot 线程再 note 一次。

        两处 note 都是刷新 settle（幂等、不记事件/不占配额）：第一处闭合「starting
        已生效但 boot 线程还没进 _start_automation」的窄窗口，第二处覆盖直接调用
        _start_automation 的其它入口（如定时任务旧路径）。
        """
        noted = []
        web_ui.RESIDENT = SimpleNamespace(
            note_automation_started=lambda: noted.append(1))
        fake = _fake_fsm()
        with (
            patch.dict(sys.modules, {"FSM_action": fake}),
            patch.object(web_ui, "load_config",
                         return_value={"name": "TestUser#12345",
                                       "log_root": "."}),
            patch.object(web_ui, "_apply_constants"),
            patch.object(web_ui, "_begin_battle_session"),
            patch.object(web_ui, "_stdout_capture_start"),
            patch.object(web_ui, "_register_hotkey"),
            patch.object(web_ui, "_log"),
            patch.object(web_ui, "_persist_schedule"),
            patch.object(web_ui, "_automation_worker"),
            patch.object(web_ui.os.path, "isdir", return_value=True),
            patch.object(web_ui.threading, "Thread", _SyncThread),
        ):
            result = web_ui.api_start({})

        self.assertTrue(result["ok"])
        self.assertEqual([1, 1], noted, "api_start 与 _start_automation 各 note 一次")


# ------------------------------------------------- 场景：暂停很久 → 手动开始
class ManualStartSettleScenarioTests(_WebStateCase):
    def _real_supervisor(self, clock, recovered, starts):
        from src.safety.resident_supervisor import ResidentSupervisor

        settings = {
            "enabled": True,
            "mode": "standard",
            "deck_index": None,
            "stale_in_game_seconds": 180.0,
            "stale_other_seconds": 300.0,
            "max_restarts": 3,
            "window_seconds": 600.0,
            "check_seconds": 0.0,
            "settle_seconds": 60.0,
            "start_delay_seconds": 0.0,
            "dry_run": False,
        }
        supervisor = ResidentSupervisor(
            settings=lambda: dict(settings),
            log_age=lambda: 10000.0,       # 暂停很久：日志年龄巨大
            in_game=lambda: False,
            process_running=lambda: True,
            automation_running=lambda: web_ui._resident_automation_running(),
            stopped_by=lambda: web_ui.CTRL.stopped_by,
            start_automation=lambda: starts.append(1) or True,
            recover=lambda cfg, reason: recovered.append(reason) or False,
            alert=lambda message: None,
            clock=clock,
        )
        if not callable(getattr(supervisor, "note_automation_started", None)):
            # R1(task-10) 尚未落盘时的最小契约垫片：settle 语义与 R1 一致。
            def _note():
                with supervisor._lock:
                    cfg = supervisor._cfg()
                    supervisor._settle_until = clock() + float(
                        cfg["settle_seconds"])
                    supervisor._last_check = -1e9
                    supervisor._process_gone_since = None

            supervisor.note_automation_started = _note
        return supervisor

    def test_manual_start_settle_suppresses_stale_then_expires(self):
        clock = _Clock(1000.0)
        recovered = []
        starts = []
        supervisor = self._real_supervisor(clock, recovered, starts)
        web_ui.RESIDENT = supervisor

        # 用户点「开始对战」：api_start 置 starting，_start_automation 入口通知 settle。
        web_ui.CTRL.starting = True
        web_ui.CTRL.automation_thread = object()
        web_ui._resident_note_automation_started()

        # boot 窗口（starting 中）：监督器认为自动化在跑，不抢启动；settle 内不判卡死。
        self.assertIsNone(supervisor.poll_once())
        self.assertEqual([], starts)
        self.assertEqual([], recovered)

        # 线程已挂上、starting 清掉；settle 未过期 → 大 log_age 仍不算卡死。
        web_ui.CTRL.starting = False
        clock.advance(30.0)
        self.assertIsNone(supervisor.poll_once())
        self.assertEqual([], recovered)
        self.assertEqual([], starts)

        # settle 过期后：大 log_age 正常触发恢复（常驻继续监督）。
        clock.advance(60.0)
        event = supervisor.poll_once()
        self.assertIn(event["kind"], ("restart", "restart_failed"))
        self.assertEqual(["log_stale"], recovered)
        self.assertEqual([], starts)      # 自动化在跑，不会重复启动


if __name__ == "__main__":
    unittest.main()
