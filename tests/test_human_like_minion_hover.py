"""活人感（进阶）：对手场上出现新随从时，鼠标在该随从上随机悬停 0.2~1.5s。

只移动、绝不点击；只在对手回合生效（那时脚本本就在空转等对手），
一次最多悬停 OPPO_MINION_HOVER_PER_BATCH 个新随从，避免一口气铺场时长时间发呆。
"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import click as hearthstone_click
import config


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


def settings(**overrides):
    cfg = dict(config.DEFAULT_HUMAN_LIKE)
    cfg.update(overrides)
    return cfg


class OpponentMinionHoverPointTests(unittest.TestCase):
    def test_matches_choose_opponent_minion_coordinates(self):
        # 与 click.choose_opponent_minion 用的是同一套坐标口径
        for index, num in ((0, 1), (0, 3), (2, 3), (6, 7)):
            expected_x = 960 - (num - 1) * 70 + index * 140
            point = hearthstone_click.opponent_minion_hover_point(
                index, num, jitter=False)
            self.assertEqual((expected_x, hearthstone_click.OPPO_MINION_HOVER_Y),
                             point)

    def test_jitter_stays_near_the_minion(self):
        with patch.object(hearthstone_click.random, "randint",
                          return_value=hearthstone_click.OPPO_MINION_HOVER_JITTER):
            point = hearthstone_click.opponent_minion_hover_point(0, 1)
        self.assertEqual(
            (960 + hearthstone_click.OPPO_MINION_HOVER_JITTER,
             hearthstone_click.OPPO_MINION_HOVER_Y
             + hearthstone_click.OPPO_MINION_HOVER_JITTER),
            point)


class HoverOpponentMinionTests(unittest.TestCase):
    def setUp(self):
        self.mouse = RecordingMouse()
        self.printed = []
        self.slept = []
        self._patches = [
            patch.object(hearthstone_click, "Controller", return_value=self.mouse),
            patch.object(hearthstone_click.time, "sleep",
                         side_effect=lambda s: self.slept.append(s)),
            patch.object(hearthstone_click, "sys_print",
                         side_effect=lambda line: self.printed.append(line)),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in self._patches:
            item.stop()

    def test_hover_moves_then_resets_and_never_clicks(self):
        duration = hearthstone_click.hover_opponent_minion(
            1, 3, self.mouse, settings(minion_hover_min=0.2, minion_hover_max=1.5),
            duration=0.9)

        self.assertEqual(0.9, duration)
        moves = self.mouse.moves
        self.assertEqual(2, len(moves))
        self.assertEqual(hearthstone_click.MOUSE_RESET_POS, moves[-1])
        self.assertEqual([0.9], self.slept)
        self.assertEqual([], [e for e in self.mouse.events if e[0] != "position"])
        self.assertIn("活人感 延时 0.9s 后", self.printed[0])
        self.assertIn("悬停对手新随从", self.printed[0])
        self.assertIn("延时结束", self.printed[1])

    def test_random_duration_within_configured_range(self):
        for _ in range(30):
            hearthstone_click.hover_opponent_minion(
                0, 1, self.mouse,
                settings(minion_hover_min=0.2, minion_hover_max=1.5))
        for value in self.slept:
            self.assertGreaterEqual(value, 0.2)
            self.assertLessEqual(value, 1.5)

    def test_falls_back_to_defaults_when_keys_missing(self):
        # 老配置里没有 minion_hover_* 时不应抛 KeyError
        hearthstone_click.hover_opponent_minion(
            0, 1, self.mouse, {"enabled": True})
        self.assertEqual(1, len(self.slept))
        self.assertGreaterEqual(self.slept[0],
                                config.DEFAULT_HUMAN_LIKE["minion_hover_min"])
        self.assertLessEqual(self.slept[0],
                             config.DEFAULT_HUMAN_LIKE["minion_hover_max"])


class FakeMinion:
    def __init__(self, entity_id):
        self.entity_id = entity_id


class FakeSnapshot:
    def __init__(self, ids, is_my_turn=False):
        self.oppo_minions = [FakeMinion(i) for i in ids]
        self.is_my_turn = is_my_turn
        self.is_end = False
        self.game_num_turns_in_play = 3
        self.start_of_game_card_count = 0


class HumanLikeOpponentMinionWatchTests(unittest.TestCase):
    """FSM_action 的挂点：只在对手回合、只对新随从、且开启时才悬停。"""

    def setUp(self):
        import FSM_action
        self.fsm = FSM_action
        self._saved = FSM_action._oppo_minion_ids
        FSM_action._oppo_minion_ids = None
        self.calls = []

    def tearDown(self):
        self.fsm._oppo_minion_ids = self._saved

    def _run(self, snapshot, enabled=True, minion_switch=None):
        settings = {"enabled": enabled}
        if minion_switch is not None:
            settings["minion_hover_enabled"] = minion_switch
        with (
            patch.object(self.fsm, "human_like_settings",
                         return_value=settings),
            patch.object(self.fsm.click, "hover_opponent_minion",
                         side_effect=lambda i, n: self.calls.append((i, n))),
        ):
            return self.fsm._human_like_opponent_minions(snapshot)

    def test_first_snapshot_only_builds_baseline(self):
        hovered = self._run(FakeSnapshot([11, 12]))

        self.assertEqual(0, hovered)
        self.assertEqual([], self.calls)
        self.assertEqual({11, 12}, self.fsm._oppo_minion_ids)

    def test_new_minion_on_opponent_turn_is_hovered(self):
        self._run(FakeSnapshot([11, 12]))
        hovered = self._run(FakeSnapshot([11, 12, 13]))

        self.assertEqual(1, hovered)
        self.assertEqual([(2, 3)], self.calls)      # 第 3 个随从、场上共 3 个

    def test_same_minions_are_not_hovered_twice(self):
        self._run(FakeSnapshot([11]))
        self.assertEqual(0, self._run(FakeSnapshot([11])))
        self.assertEqual([], self.calls)

    def test_minion_appearing_on_my_turn_is_not_hovered(self):
        self._run(FakeSnapshot([11]))
        hovered = self._run(FakeSnapshot([11, 12], is_my_turn=True))

        self.assertEqual(0, hovered)
        self.assertEqual([], self.calls)
        # 基线已更新：回到对手回合也不会补悬停（避免打断自己的操作节奏）
        self.assertEqual(0, self._run(FakeSnapshot([11, 12])))
        self.assertEqual([], self.calls)

    def test_disabled_does_not_hover(self):
        self._run(FakeSnapshot([11]))
        hovered = self._run(FakeSnapshot([11, 12]), enabled=False)

        self.assertEqual(0, hovered)
        self.assertEqual([], self.calls)

    def test_minion_switch_off_does_not_hover_but_keeps_the_baseline(self):
        """「看随从」关掉：不动鼠标，但基线照旧更新（开关中途打开不补悬停）。"""
        self._run(FakeSnapshot([11]))
        hovered = self._run(FakeSnapshot([11, 12]), minion_switch=False)

        self.assertEqual(0, hovered)
        self.assertEqual([], self.calls)
        self.assertEqual({11, 12}, self.fsm._oppo_minion_ids)
        # 打开开关后，老随从不会补悬停，只有真正的新随从才悬停。
        self.assertEqual(0, self._run(FakeSnapshot([11, 12]), minion_switch=True))
        self.assertEqual(1, self._run(FakeSnapshot([11, 12, 13]),
                                      minion_switch=True))
        self.assertEqual([(2, 3)], self.calls)

    def test_missing_minion_switch_defaults_to_on(self):
        self._run(FakeSnapshot([11]))
        hovered = self._run(FakeSnapshot([11, 12]))

        self.assertEqual(1, hovered)
        self.assertEqual([(1, 2)], self.calls)

    def test_batch_size_is_capped(self):
        self._run(FakeSnapshot([]))
        hovered = self._run(FakeSnapshot([1, 2, 3, 4, 5]))

        self.assertEqual(self.fsm.OPPO_MINION_HOVER_PER_BATCH, hovered)
        self.assertEqual([(0, 5), (1, 5), (2, 5)], self.calls)

    def test_hover_failure_does_not_break_the_loop(self):
        self._run(FakeSnapshot([11]))

        def explode(index, num):
            self.calls.append((index, num))
            raise RuntimeError("鼠标异常")

        with (
            patch.object(self.fsm, "human_like_settings",
                         return_value={"enabled": True}),
            patch.object(self.fsm.click, "hover_opponent_minion",
                         side_effect=explode),
            patch.object(self.fsm, "print", create=True),
        ):
            hovered = self.fsm._human_like_opponent_minions(
                FakeSnapshot([11, 12, 13]))

        self.assertEqual(0, hovered)                 # 一个都没成功
        self.assertEqual([(1, 3)], self.calls)       # 失败后不再继续
        self.assertEqual({11, 12, 13}, self.fsm._oppo_minion_ids)


class BattleStepWiringTests(unittest.TestCase):
    """对局主循环的挂点：对手回合拿到新随从时，悬停真的会被触发。"""

    def setUp(self):
        import FSM_action
        self.fsm = FSM_action
        self._saved = (FSM_action._oppo_minion_ids,
                       FSM_action.refresh_snapshot,
                       FSM_action.recommendation_flow,
                       FSM_action.recommendation_config)
        FSM_action._oppo_minion_ids = None
        FSM_action.recommendation_flow = None
        FSM_action.recommendation_config = SimpleNamespace(
            pre_action_delay_seconds=1.0, first_turn_per_card_delay_seconds=0.0)
        self.calls = []

    def tearDown(self):
        (self.fsm._oppo_minion_ids,
         self.fsm.refresh_snapshot,
         self.fsm.recommendation_flow,
         self.fsm.recommendation_config) = self._saved

    def _step(self, snapshot, enabled=True):
        self.fsm.refresh_snapshot = lambda: snapshot
        with (
            patch.object(self.fsm, "human_like_settings",
                         return_value={"enabled": enabled}),
            patch.object(self.fsm.click, "hover_opponent_minion",
                         side_effect=lambda i, n: self.calls.append((i, n))),
            patch.object(self.fsm, "_report_automation_diagnostic"),
            patch.object(self.fsm, "_maybe_concede", return_value=False),
            patch.object(self.fsm, "_sleep_with_delay"),
            patch.object(self.fsm, "run_manual_battle_step", return_value=None),
        ):
            return self.fsm.run_automatic_battle_step()

    def test_battle_loop_hovers_a_brand_new_opponent_minion(self):
        self._step(FakeSnapshot([11], is_my_turn=False))     # 建立基线
        self.assertEqual([], self.calls)

        self._step(FakeSnapshot([11, 12], is_my_turn=False))

        self.assertEqual([(1, 2)], self.calls)

    def test_battle_loop_does_not_hover_on_my_turn(self):
        self._step(FakeSnapshot([11], is_my_turn=False))

        self._step(FakeSnapshot([11, 12], is_my_turn=True))

        self.assertEqual([], self.calls)


class ConfigDefaultsTests(unittest.TestCase):
    def test_defaults_are_the_documented_range(self):
        self.assertEqual(0.2, config.DEFAULT_HUMAN_LIKE["minion_hover_min"])
        self.assertEqual(1.5, config.DEFAULT_HUMAN_LIKE["minion_hover_max"])

    def test_both_hover_switches_default_to_on(self):
        self.assertTrue(config.DEFAULT_HUMAN_LIKE["hand_hover_enabled"])
        self.assertTrue(config.DEFAULT_HUMAN_LIKE["minion_hover_enabled"])

    def test_settings_normalise_the_two_switches(self):
        import json
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        try:
            path = Path(tmp.name) / "ui_config.json"
            path.write_text(json.dumps({"human_like": {
                "enabled": 1, "hand_hover_enabled": 0,
                "minion_hover_enabled": ""}}), encoding="utf8")
            with patch.object(config, "CONFIG_PATH", path):
                cfg = config.human_like_settings()
        finally:
            tmp.cleanup()

        self.assertTrue(cfg["enabled"])
        self.assertFalse(cfg["hand_hover_enabled"])
        self.assertFalse(cfg["minion_hover_enabled"])

    def test_settings_clamp_minion_range(self):
        import json
        import tempfile
        from pathlib import Path

        tmp = tempfile.TemporaryDirectory()
        try:
            path = Path(tmp.name) / "ui_config.json"
            path.write_text(json.dumps({"human_like": {
                "enabled": True, "minion_hover_min": -2,
                "minion_hover_max": 0}}), encoding="utf8")
            with patch.object(config, "CONFIG_PATH", path):
                cfg = config.human_like_settings()
        finally:
            tmp.cleanup()

        self.assertEqual(0.05, cfg["minion_hover_min"])
        self.assertEqual(0.05, cfg["minion_hover_max"])   # 上限不低于下限


class WebPageTests(unittest.TestCase):
    """网页「🎭 活人感」卡片：3 行、3 个勾，并能把两个开关发给后端。"""

    def setUp(self):
        path = Path(__file__).resolve().parent.parent / "web" / "index.html"
        self.html = path.read_text(encoding="utf8")

    def test_two_hover_switches_exist(self):
        self.assertIn('id="chkHlHand"', self.html)
        self.assertIn('id="chkHlMinion"', self.html)
        self.assertIn('id="chkHumanLike"', self.html)

    def test_rows_are_labelled(self):
        self.assertIn("看卡牌", self.html)
        self.assertIn("看随从", self.html)
        # 老的一整行挤 7 个控件时用的长标签不该再出现
        self.assertNotIn("对手新随从悬停下限", self.html)

    def test_page_sends_and_renders_the_switches(self):
        self.assertIn('hand_hover_enabled: $("chkHlHand").checked', self.html)
        self.assertIn('minion_hover_enabled: $("chkHlMinion").checked', self.html)
        self.assertIn(
            '$("chkHlHand").checked = !!s.human_like.hand_hover_enabled',
            self.html)
        self.assertIn(
            '$("chkHlMinion").checked = !!s.human_like.minion_hover_enabled',
            self.html)

    def test_master_switch_row_has_no_numbers(self):
        """第 1 行只有总开关：随机延时上下限被挪到「看卡牌」那一行。"""
        start = self.html.index('id="chkHumanLike"')
        row_start = self.html.rindex('<div class="input-row">', 0, start)
        row_end = self.html.index("</div>", start)
        row = self.html[row_start:row_end]
        self.assertNotIn("inpHlMin", row)


if __name__ == "__main__":
    unittest.main()
