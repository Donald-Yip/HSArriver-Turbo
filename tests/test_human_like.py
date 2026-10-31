"""活人感（可选）：操作后 0.5~3s 随机延时，期间鼠标在手牌区随机悬停（每处 1s）。"""

import json
import inspect
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import click as hearthstone_click
import config
from src.recommendation_models import ActionKind


class RecordingMouse:
    """只记录事件，不真的动系统鼠标。"""

    def __init__(self):
        self.events = []

    @property
    def position(self):
        return None

    @position.setter
    def position(self, value):
        self.events.append(("position", value))

    def press(self, button):
        self.events.append(("press", button))

    def release(self, button):
        self.events.append(("release", button))

    @property
    def moves(self):
        return [v for kind, v in self.events if kind == "position"]


class FakeClock:
    """把 time.sleep / time.monotonic 换成可控时钟，测试不用真的等秒。"""

    def __init__(self):
        self.now = 0.0
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def monotonic(self):
        return self.now


class HumanLikeSettingsTests(unittest.TestCase):
    def _with_config(self, payload):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "ui_config.json"
        if payload is not None:
            path.write_text(json.dumps(payload), encoding="utf8")
        return tmp, path

    def _load(self, payload):
        tmp, path = self._with_config(payload)
        try:
            with patch.object(config, "CONFIG_PATH", path):
                return config.human_like_settings()
        finally:
            tmp.cleanup()

    def test_falls_back_to_defaults(self):
        cases = {
            "no_section": {"name": "x"},
            "bad_type": {"human_like": {"enabled": True,
                                        "post_delay_min": "很快"}},
        }
        for name, payload in cases.items():
            with self.subTest(case=name):
                self.assertEqual(config.DEFAULT_HUMAN_LIKE,
                                 self._load(payload))

    def test_reads_and_clamps_saved_values(self):
        saved = self._load({"human_like": {
            "enabled": True, "post_delay_min": 0.8,
            "post_delay_max": 2.5, "hover_min": 0.3, "hover_max": 0.9}})

        self.assertTrue(saved["enabled"])
        self.assertEqual(0.8, saved["post_delay_min"])
        self.assertEqual(2.5, saved["post_delay_max"])
        self.assertEqual(0.3, saved["hover_min"])
        self.assertEqual(0.9, saved["hover_max"])

        clamped = self._load({"human_like": {
            "enabled": 1, "post_delay_min": -5,
            "post_delay_max": -1, "hover_min": -3, "hover_max": 0}})

        self.assertTrue(clamped["enabled"])
        self.assertEqual(0.0, clamped["post_delay_min"])
        self.assertEqual(0.0, clamped["post_delay_max"])   # 上限不低于下限
        self.assertEqual(0.05, clamped["hover_min"])   # 悬停下限不为 0
        self.assertEqual(0.05, clamped["hover_max"])   # 上限不低于下限
        self.assertGreaterEqual(clamped["post_delay_max"],
                                clamped["post_delay_min"])
        self.assertGreaterEqual(clamped["hover_max"], clamped["hover_min"])


class HumanLikePauseTests(unittest.TestCase):
    def test_global_stop_exits_without_moving_the_mouse(self):
        stopped = threading.Event()
        stopped.set()
        mouse = RecordingMouse()

        total = hearthstone_click.human_like_pause(
            mouse,
            {"post_delay_min": 1.0, "post_delay_max": 1.0,
             "hover_min": 0.2, "hover_max": 0.2},
            stop_event=stopped,
        )

        self.assertEqual(0.0, total)
        self.assertEqual([], mouse.events)

    def setUp(self):
        self.clock = FakeClock()
        self.mouse = RecordingMouse()
        self.printed = []
        self._patches = [
            patch.object(hearthstone_click, "Controller", return_value=self.mouse),
            patch.object(hearthstone_click.time, "sleep", self.clock.sleep),
            patch.object(hearthstone_click.time, "monotonic", self.clock.monotonic),
            patch.object(hearthstone_click, "sys_print",
                         side_effect=lambda line: self.printed.append(line)),
            # 鼠标位置现在要经 display_mapping 换算成屏幕坐标；这里注入"读不到
            # 客户区"，让断言用的是客户区相对坐标（不受测试机窗口位置影响）。
            patch.object(hearthstone_click, "_display_mapping", return_value=None),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in self._patches:
            item.stop()

    def _settings(self, lo=0.5, hi=3.0, h_lo=0.2, h_hi=1.0):
        return {"enabled": True, "post_delay_min": lo,
                "post_delay_max": hi, "hover_min": h_lo, "hover_max": h_hi}

    @staticmethod
    def _valid_hand_x():
        xs = set()
        for size in range(3, 9):
            xs.update(hearthstone_click.HAND_CARD_X[size])
        return xs

    def _run_pause(self, seq):
        """用给定的 uniform 返回值序列跑一次；先清掉上一次的时钟/事件/日志。"""
        self.clock.now = 0.0
        del self.clock.slept[:]
        del self.mouse.events[:]
        del self.printed[:]
        with patch.object(hearthstone_click.random, "uniform", side_effect=seq):
            return hearthstone_click.human_like_pause(self.mouse, self._settings())

    def test_random_pause_and_hovers(self):
        valid_x = self._valid_hand_x()

        # 3 处悬停（总延时 3s，每处 1s）+ 最后复位 1 次
        with self.subTest(case="three_hovers_and_reset"):
            total = self._run_pause([3.0, 1.0, 1.0, 1.0])

            self.assertEqual(3.0, total)
            moves = self.mouse.moves
            self.assertEqual(4, len(moves))
            self.assertEqual(hearthstone_click.MOUSE_RESET_POS, moves[-1])
            for point in moves[:-1]:
                x, y = point
                self.assertEqual(hearthstone_click.HAND_HOVER_Y, y)
                self.assertIn(x, valid_x)
            # 每次悬停 1s（总时长刚好 3s）
            self.assertEqual([1.0, 1.0, 1.0], self.clock.slept)
            # 只移动，绝不点击
            self.assertEqual([], [e for e in self.mouse.events
                                  if e[0] != "position"])
            # 第一行是延时行（浮窗进度条靠它驱动），第二行是收尾标记（清空进度条）
            self.assertEqual(2, len(self.printed))
            self.assertIn("活人感 延时 3.0s 后", self.printed[0])
            self.assertIn("延时结束", self.printed[1])

        with self.subTest(case="short_pause_hovers_once"):
            total = self._run_pause([0.5, 1.0])

            self.assertEqual(0.5, total)
            moves = self.mouse.moves
            self.assertEqual(2, len(moves))               # 1 处悬停 + 复位
            self.assertEqual(hearthstone_click.MOUSE_RESET_POS, moves[-1])
            self.assertEqual([0.5], self.clock.slept)     # 只停到延时结束

        with self.subTest(case="hover_duration_is_random_per_hover"):
            seq = [3.0, 0.2, 0.9, 0.5, 0.7, 0.3, 0.6, 0.4]
            self._run_pause(seq)

            sleeps = self.clock.slept
            self.assertGreaterEqual(len(sleeps), 2)
            self.assertGreater(len(set(sleeps)), 1,
                               f"每处悬停时长应各自随机：{sleeps}")
            for value in sleeps:
                self.assertLessEqual(value, 1.0)      # 不超过设置的上限
            self.assertAlmostEqual(3.0, self.clock.now, places=3)

        with self.subTest(case="successive_hovers_prefer_different_slots"):
            self._run_pause([3.0, 1.0, 1.0, 1.0])

            hovers = self.mouse.moves[:-1]
            for before, after in zip(hovers, hovers[1:]):
                self.assertNotEqual(before, after)

        with self.subTest(case="hover_points_always_on_the_hand_row"):
            self._run_pause([1.0, 0.5, 0.5])

            for point in self.mouse.moves[:-1]:
                self.assertEqual(hearthstone_click.HAND_HOVER_Y, point[1])
            self.assertIn("手牌区", self.printed[0])


class ParkMouseTests(unittest.TestCase):
    """复位本身永远走“0.1s + 复位到左上角”，活人感不再挂在这里。

    否则匹配对手、选卡组、错误弹窗取消这类非推荐动作也会被拖慢，
    而活人感只应对局中识别盒子意见并执行完之后生效。
    """

    def test_park_is_plain_even_when_human_like_enabled(self):
        with self.subTest(case="park_mouse"):
            mouse = RecordingMouse()
            called = []

            with (
                patch.object(hearthstone_click, "human_like_settings",
                             return_value={"enabled": True, "post_delay_min": 0.5,
                                           "post_delay_max": 3.0,
                                           "hover_min": 0.2, "hover_max": 1.0}),
                patch.object(hearthstone_click, "human_like_pause",
                             side_effect=lambda m=None, s=None: called.append(1)),
                patch.object(hearthstone_click, "_display_mapping", return_value=None),
                patch.object(hearthstone_click.time, "sleep") as sleep,
            ):
                hearthstone_click.park_mouse(mouse)

            self.assertEqual([], called)             # 不再自动触发活人感
            sleep.assert_called_once_with(0.1)
            self.assertEqual([("position", hearthstone_click.MOUSE_RESET_POS)],
                             mouse.events)

        with self.subTest(case="action_session"):
            mouse = RecordingMouse()
            called = []

            with (
                patch.object(hearthstone_click, "human_like_settings",
                             return_value={"enabled": True}),
                patch.object(hearthstone_click, "human_like_pause",
                             side_effect=lambda m=None, s=None: called.append(1)),
                patch.object(hearthstone_click, "Controller", return_value=mouse),
                patch.object(hearthstone_click, "_display_mapping", return_value=None),
                patch.object(hearthstone_click.time, "sleep") as sleep,
            ):
                with hearthstone_click.hearthstone_action_session():
                    pass

            self.assertEqual([], called)
            sleep.assert_called_once_with(0.1)
            self.assertEqual([("position", hearthstone_click.MOUSE_RESET_POS)],
                             mouse.events)


class PostActionPauseHookTests(unittest.TestCase):
    """FSM_action 的挂点：只在开启时接管，且只由流程层调用。"""

    def setUp(self):
        import FSM_action
        self.fsm = FSM_action
        self.areas = []

    def _pause(self, action_kind=None, cfg=None):
        if cfg is None:
            cfg = {"enabled": True, "hand_hover_enabled": True}
        with (
            patch.object(self.fsm, "human_like_settings", return_value=cfg),
            patch.object(self.fsm.click, "human_like_pause",
                         side_effect=lambda **_kwargs: self.areas.append(1)),
        ):
            return self.fsm._human_like_post_action_pause(action_kind)

    def test_pause_gate_by_action_kind(self):
        """攻击/星舰发射/换牌/点目标/古神技能不看手牌，其余动作才看。

        ActionKind 是 str 枚举，日志/适配层传普通字符串也要认。
        """
        skipped = (ActionKind.ATTACK, "attack",
                   ActionKind.LAUNCH_STARSHIP, "launch_starship",
                   ActionKind.MULLIGAN,
                   ActionKind.SELECT_TARGET,
                   ActionKind.USE_ABILITY)
        for kind in skipped:
            with self.subTest(kind=kind):
                self.areas.clear()
                self.assertFalse(self._pause(kind))
                self.assertEqual([], self.areas)

        paused = (ActionKind.PLAY_CARD, ActionKind.USE_HERO_POWER,
                  ActionKind.TRADE_CARD, ActionKind.USE_LOCATION,
                  ActionKind.END_TURN, None)
        for kind in paused:
            with self.subTest(kind=kind):
                self.areas.clear()
                self.assertTrue(self._pause(kind))
                self.assertEqual([1], self.areas)

    def test_pause_gate_by_settings(self):
        with self.subTest(case="enabled_runs_pause_and_takes_over"):
            self.areas.clear()
            self.assertTrue(self._pause())
            self.assertEqual([1], self.areas)      # 非攻击动作：看手牌

        with self.subTest(case="disabled"):
            self.areas.clear()
            self.assertFalse(self._pause(
                cfg={"enabled": False, "hand_hover_enabled": True}))
            self.assertEqual([], self.areas)

        # 「看卡牌」是随机延时 + 手牌悬停的共同开关：关掉后走固定操作后延时。
        with self.subTest(case="hand_hover_off"):
            self.areas.clear()
            self.assertFalse(self._pause(
                cfg={"enabled": True, "hand_hover_enabled": False}))
            self.assertEqual([], self.areas)

        # 老配置里没有 hand_hover_enabled 时按“开”处理，行为与升级前一致。
        with self.subTest(case="missing_hand_key_defaults_to_on"):
            self.areas.clear()
            with (
                patch.object(self.fsm, "human_like_settings",
                             return_value={"enabled": True}),
                patch.object(self.fsm.click, "human_like_pause",
                             side_effect=lambda **_kwargs: self.areas.append(1)),
            ):
                handled = self.fsm._human_like_post_action_pause()

            self.assertTrue(handled)
            self.assertEqual([1], self.areas)

        with self.subTest(case="failure_falls_back_to_fixed_delay"):
            with (
                patch.object(self.fsm, "human_like_settings",
                             return_value={"enabled": True,
                                           "hand_hover_enabled": True}),
                patch.object(self.fsm.click, "human_like_pause",
                             side_effect=RuntimeError("鼠标异常")),
            ):
                handled = self.fsm._human_like_post_action_pause()

            self.assertFalse(handled)

        # 老调用方式（0 参数）保持可用（例如其它流程复用同一个挂点）。
        with self.subTest(case="callable_without_arguments"):
            parameter = inspect.signature(
                self.fsm._human_like_post_action_pause).parameters["action_kind"]
            self.assertIsNone(parameter.default)

            self.areas.clear()
            self.assertTrue(self._pause())


class WebHumanLikeTests(unittest.TestCase):
    def setUp(self):
        self._saved_thread = web_ui_thread()
        import web_ui
        self.web_ui = web_ui
        self._saved = web_ui.CTRL.automation_thread

    def tearDown(self):
        self.web_ui.CTRL.automation_thread = self._saved

    def _status(self, saved):
        with patch.object(self.web_ui, "load_config", return_value=saved):
            return self.web_ui.status_snapshot()

    def _save(self, payload):
        saved = {}
        with (
            patch.object(self.web_ui, "load_config", return_value={}),
            patch.object(self.web_ui, "save_config",
                         side_effect=lambda cfg: saved.update(cfg)),
            patch.object(self.web_ui, "_log"),
            patch.object(self.web_ui.CTRL, "automation_thread", None),
        ):
            result = self.web_ui.api_save_human_like(payload)
        return result, saved

    def test_status_reflects_config(self):
        # 不能依赖用户真实的 ui_config.json：显式让 load_config 返回“没有 human_like 段”
        with self.subTest(case="defaults"):
            snapshot = self._status({})
            self.assertEqual(self.web_ui.DEFAULT_HUMAN_LIKE,
                             {k: snapshot["human_like"][k]
                              for k in self.web_ui.DEFAULT_HUMAN_LIKE})

        with self.subTest(case="saved_section"):
            snapshot = self._status({"human_like": {
                "enabled": True, "post_delay_min": 0.5,
                "post_delay_max": 3.0, "hover_min": 0.2, "hover_max": 1.0}})
            self.assertTrue(snapshot["human_like"]["enabled"])

        with self.subTest(case="two_switches"):
            snapshot = self._status({"human_like": {
                "enabled": True, "hand_hover_enabled": False,
                "minion_hover_enabled": True}})
            self.assertFalse(snapshot["human_like"]["hand_hover_enabled"])
            self.assertTrue(snapshot["human_like"]["minion_hover_enabled"])

    def test_saves_and_clamps(self):
        with self.subTest(case="clamps_delay_and_hover_ranges"):
            result, saved = self._save({
                "enabled": True, "post_delay_min": 5, "post_delay_max": 1,
                "hover_min": 1.5, "hover_max": 0.3})

            self.assertTrue(result["ok"])
            hl = saved["human_like"]
            self.assertTrue(hl["enabled"])
            self.assertEqual(5.0, hl["post_delay_min"])
            self.assertEqual(5.0, hl["post_delay_max"])   # 上限被抬到不低于下限
            self.assertEqual(1.5, hl["hover_min"])
            self.assertEqual(1.5, hl["hover_max"])        # 悬停上限同样不低于下限
            self.assertNotIn("hover_seconds", hl)         # 旧的固定悬停字段被清掉

        with self.subTest(case="minion_hover_range"):
            result, saved = self._save({
                "enabled": True, "minion_hover_min": 1.2,
                "minion_hover_max": 0.3})

            self.assertTrue(result["ok"])
            hl = saved["human_like"]
            self.assertEqual(1.2, hl["minion_hover_min"])
            self.assertEqual(1.2, hl["minion_hover_max"])   # 上限不低于下限

        # 「看卡牌」「看随从」各存各的，互不影响。
        with self.subTest(case="two_hover_switches"):
            result, saved = self._save({
                "enabled": True, "hand_hover_enabled": False,
                "minion_hover_enabled": True})

            self.assertTrue(result["ok"])
            hl = saved["human_like"]
            self.assertFalse(hl["hand_hover_enabled"])
            self.assertTrue(hl["minion_hover_enabled"])
            self.assertTrue(hl["enabled"])

        # 不传两个开关时按默认开启保存，行为与升级前一致。
        with self.subTest(case="missing_switch_keys_default_to_on"):
            result, saved = self._save({"enabled": True})

            self.assertTrue(result["ok"])
            hl = saved["human_like"]
            self.assertTrue(hl["hand_hover_enabled"])
            self.assertTrue(hl["minion_hover_enabled"])

        with self.subTest(case="rejects_non_numeric"):
            result, _saved = self._save({"post_delay_min": "慢"})

            self.assertFalse(result["ok"])
            self.assertIn("数字", result["error"])


def web_ui_thread():
    """占位：保证 setUp 里先导入 web_ui 再取旧值。"""
    import web_ui
    return web_ui.CTRL.automation_thread


if __name__ == "__main__":
    unittest.main()
