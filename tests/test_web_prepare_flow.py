"""点「开始运行」只做准备（开浮窗 + 切炉石前台），绝不自动开始对战。"""

import types
import unittest
from pathlib import Path
from unittest.mock import patch

import web_ui


class _SyncThread:
    """把 api_prepare 起的前台切换线程变成同步执行，便于断言。"""

    def __init__(self, target=None, args=(), kwargs=None, **_ignored):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self._saved = (web_ui.CTRL.prepared, web_ui.CTRL.automation_thread,
                       web_ui.CTRL.starting)

    def tearDown(self):
        (web_ui.CTRL.prepared, web_ui.CTRL.automation_thread,
         web_ui.CTRL.starting) = self._saved

    def test_prepare_never_starts_automation(self):
        started, bound, foreground = [], [], []
        overlay = types.SimpleNamespace(is_running=lambda: False)

        with (
            patch.object(web_ui, "log_overlay", overlay),
            patch.object(web_ui, "api_start",
                         side_effect=lambda body=None: started.append(body)),
            patch.object(web_ui, "_bind_overlay", side_effect=lambda: bound.append(1)),
            patch.object(web_ui, "_bring_hearthstone_foreground",
                         side_effect=lambda: foreground.append(1)),
            patch.object(web_ui.threading, "Thread", _SyncThread),
            patch.object(web_ui, "_log"),
        ):
            web_ui.CTRL.automation_thread = None
            web_ui.CTRL.starting = False
            web_ui.CTRL.prepared = False
            result = web_ui.api_prepare({})

        self.assertTrue(result["ok"])
        self.assertTrue(result["prepared"])
        self.assertEqual([], started)          # 关键：没有自动开始对战
        self.assertEqual(1, len(bound))        # 浮窗已开启
        self.assertEqual(1, len(foreground))   # 已切炉石前台
        self.assertTrue(web_ui.CTRL.prepared)

    def test_prepare_skips_overlay_when_already_open(self):
        bound = []
        overlay = types.SimpleNamespace(is_running=lambda: True)

        with (
            patch.object(web_ui, "log_overlay", overlay),
            patch.object(web_ui, "_bind_overlay", side_effect=lambda: bound.append(1)),
            patch.object(web_ui, "_bring_hearthstone_foreground"),
            patch.object(web_ui.threading, "Thread", _SyncThread),
            patch.object(web_ui, "_log"),
        ):
            web_ui.CTRL.automation_thread = None
            web_ui.CTRL.starting = False
            web_ui.api_prepare({})

        self.assertEqual([], bound)

    def test_prepare_refused_while_running(self):
        web_ui.CTRL.automation_thread = object()

        result = web_ui.api_prepare({})

        self.assertFalse(result["ok"])
        self.assertIn("运行中", result["error"])

    def test_prepare_refused_while_starting(self):
        web_ui.CTRL.automation_thread = None
        web_ui.CTRL.starting = True

        result = web_ui.api_prepare({})

        self.assertFalse(result["ok"])
        self.assertIn("运行中", result["error"])

    def test_status_exposes_prepared_flag(self):
        web_ui.CTRL.prepared = False
        self.assertFalse(web_ui.status_snapshot()["prepared"])
        web_ui.CTRL.prepared = True
        self.assertTrue(web_ui.status_snapshot()["prepared"])

    def test_prepare_reopens_the_overlay_without_starting(self):
        """退出浮窗 → 再点「开始运行」：只把浮窗开回来，绝不开始对战。"""
        started, bound = [], []
        overlay = types.SimpleNamespace(is_running=lambda: False)

        with (
            patch.object(web_ui, "log_overlay", overlay),
            patch.object(web_ui, "api_start",
                         side_effect=lambda body=None: started.append(body)),
            patch.object(web_ui, "_bind_overlay",
                         side_effect=lambda: bound.append(1)),
            patch.object(web_ui, "_bring_hearthstone_foreground"),
            patch.object(web_ui.threading, "Thread", _SyncThread),
            patch.object(web_ui, "_log"),
        ):
            web_ui.CTRL.automation_thread = None
            web_ui.CTRL.starting = False
            web_ui.CTRL.prepared = False        # 刚点过「退出浮窗」
            result = web_ui.api_prepare({})

        self.assertTrue(result["ok"])
        self.assertEqual(1, len(bound))         # 浮窗重新开出来
        self.assertEqual([], started)           # 关键：没有开始操作
        self.assertTrue(web_ui.CTRL.prepared)   # 就绪 → 按钮变「开始对战」


class OverlayStateSyncTests(unittest.TestCase):
    """就绪状态跟着浮窗走：浮窗关掉 → 回到「开始运行」，重开 → 「开始对战」。"""

    def setUp(self):
        self._saved = (web_ui.CTRL.prepared, web_ui.CTRL.automation_thread,
                       web_ui.CTRL.starting)

    def tearDown(self):
        (web_ui.CTRL.prepared, web_ui.CTRL.automation_thread,
         web_ui.CTRL.starting) = self._saved

    def test_exit_overlay_button_resets_prepared(self):
        logged = []
        web_ui.CTRL.prepared = True

        with patch.object(web_ui, "_log",
                          side_effect=lambda level, msg: logged.append((level, msg))):
            web_ui._overlay_exit_overlay()

        self.assertFalse(web_ui.CTRL.prepared)
        self.assertFalse(web_ui.status_snapshot()["prepared"])
        self.assertIn("日志浮窗", logged[0][1])
        # 告诉用户下一步该点哪个按钮，别再以为“点一下就直接开打”
        self.assertIn("开始运行", logged[0][1])
        self.assertIn("开始对战", logged[0][1])

    def test_toggle_overlay_off_resets_prepared(self):
        stopped = []
        overlay = types.SimpleNamespace(is_running=lambda: True,
                                        stop=lambda: stopped.append(1))
        web_ui.CTRL.prepared = True

        with patch.object(web_ui, "log_overlay", overlay):
            result = web_ui.api_toggle_overlay({})

        self.assertEqual([1], stopped)
        self.assertFalse(result["enabled"])
        self.assertFalse(web_ui.CTRL.prepared)
        self.assertIn("开始运行", result["message"])

    def test_toggle_overlay_on_marks_prepared(self):
        bound = []
        overlay = types.SimpleNamespace(is_running=lambda: False)
        web_ui.CTRL.prepared = False

        with (
            patch.object(web_ui, "log_overlay", overlay),
            patch.object(web_ui, "_bind_overlay",
                         side_effect=lambda: bound.append(1)),
        ):
            result = web_ui.api_toggle_overlay({})

        self.assertEqual(1, len(bound))
        self.assertTrue(result["enabled"])
        self.assertTrue(web_ui.CTRL.prepared)
        self.assertIn("开始对战", result["message"])

    def test_toggle_overlay_without_the_module_is_refused(self):
        web_ui.CTRL.prepared = True

        with patch.object(web_ui, "log_overlay", None):
            result = web_ui.api_toggle_overlay({})

        self.assertFalse(result["ok"])
        self.assertTrue(web_ui.CTRL.prepared)      # 没动就绪状态

    def test_status_exposes_overlay_running(self):
        with patch.object(web_ui, "log_overlay",
                          types.SimpleNamespace(is_running=lambda: True)):
            self.assertTrue(web_ui.status_snapshot()["overlay_running"])

        with patch.object(web_ui, "log_overlay",
                          types.SimpleNamespace(is_running=lambda: False)):
            self.assertFalse(web_ui.status_snapshot()["overlay_running"])

        with patch.object(web_ui, "log_overlay", None):
            self.assertFalse(web_ui.status_snapshot()["overlay_running"])

    def test_status_tolerates_a_broken_overlay_module(self):
        def _boom():
            raise RuntimeError("浮窗线程崩了")

        with patch.object(web_ui, "log_overlay",
                          types.SimpleNamespace(is_running=_boom)):
            self.assertFalse(web_ui.status_snapshot()["overlay_running"])


class OverlayPageTests(unittest.TestCase):
    """网页要跟着状态走：🪟 按钮文字、切换后立即刷新、按钮语义提示。"""

    def setUp(self):
        path = Path(__file__).resolve().parent.parent / "web" / "index.html"
        self.html = path.read_text(encoding="utf8")

    def test_overlay_button_label_follows_status(self):
        self.assertIn('$("btnOverlay").textContent = "🪟 日志浮窗（" '
                      '+ (s.overlay_running ? "开" : "关") + "）"',
                      self.html)

    def test_toggle_refreshes_the_status_immediately(self):
        handler = self.html[self.html.index('$("btnOverlay")'):]
        handler = handler[:handler.index("$(\"btnStart\")")]
        self.assertIn("refreshStatus()", handler)

    def test_start_button_still_depends_on_prepared(self):
        self.assertIn('s.prepared ? "⚔️ 开始对战" : "🪄 开始运行（准备）"',
                      self.html)

    def test_hint_explains_the_two_step_after_exit_overlay(self):
        self.assertIn("关掉浮窗会回到", self.html)
        self.assertIn("未就绪", self.html)


class StartAfterPrepareTests(unittest.TestCase):
    """就绪之后再点「开始对战」仍然走原本的启动逻辑（不清零开关不变）。"""

    def setUp(self):
        self._saved = (web_ui.CTRL.prepared, web_ui.CTRL.automation_thread,
                       web_ui.CTRL.starting)

    def tearDown(self):
        (web_ui.CTRL.prepared, web_ui.CTRL.automation_thread,
         web_ui.CTRL.starting) = self._saved

    def test_start_after_prepare_moves_to_playing(self):
        fake = types.ModuleType("FSM_action")
        fake.game_count, fake.win_count, fake.concede_count = 5, 3, 1
        fake.quitting_flag = False
        fake.stop_after_current_game = False
        fake.FSM_state = ""
        fake.time_begin = 0.0
        fake.print_info_init = lambda: None
        fake.init = lambda: None
        fake.AutoHS_automata = lambda: None
        fake.print_info_close = lambda: None

        with (
            patch.dict("sys.modules", {"FSM_action": fake}),
            patch.object(web_ui, "load_config",
                         return_value={"name": "TestUser#12345", "log_root": "."}),
            patch.object(web_ui, "_apply_constants"),
            patch.object(web_ui, "_bind_overlay"),
            patch.object(web_ui, "_stdout_capture_start"),
            patch.object(web_ui, "_stdout_capture_stop"),
            patch.object(web_ui, "_register_hotkey"),
            patch.object(web_ui, "_remove_hotkey"),
            patch.object(web_ui, "_log"),
            patch.object(web_ui.threading, "Thread", _SyncThread),
        ):
            web_ui.CTRL.automation_thread = None
            web_ui.CTRL.starting = False
            web_ui.CTRL.prepared = True
            ok, _msg = web_ui._start_automation(reset_stats=True)

        self.assertTrue(ok)
        self.assertTrue(web_ui.CTRL.prepared)
        # 就绪后真正开打走的是原启动逻辑（这里 fake 线程瞬间跑完，
        # 所以最终回到 idle；关键是启动确实执行了且战绩按“重新开始”清零）
        self.assertEqual((0, 0, 0), (fake.game_count, fake.win_count,
                                     fake.concede_count))
        self.assertIn(web_ui.CTRL.phase, ("playing", "idle"))


if __name__ == "__main__":
    unittest.main()
