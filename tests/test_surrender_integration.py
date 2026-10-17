"""卡组识别投降接入主循环的集成测试（不碰真炉石、不截图）。

重点锁三条容易回归的行为：

* **对手回合也要判定**：特征卡往往是对手在自己回合打出的，若判定放在
  `is_my_turn` 之后就永远错过（用户最初的需求就是这个场景）。
* **只判对方**：我方有同样的卡绝不能触发（方向性硬要求）。
* **一局只点一次认输**，且旧「AI 胜率低投降」的文案不受影响。
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import FSM_action
import surrender_rules as sr

CONFIG = {
    "enabled": True,
    "decks": [{
        "id": "priest_alizana",
        "name": "牧师 · 阿扎莉娜 + 神秘访客",
        "class": "PRIEST",
        "when": {"all": [
            {"type": "seen", "card": "裂魂者阿扎莉娜"},
            {"type": "played", "card": "神秘访客",
             "within": {"field": "round", "max": 4}},
        ]},
    }],
}


class _Entity:
    def __init__(self, card_id="", controller="", hero_entity=""):
        self.card_id = card_id
        self.tags = {}
        if controller:
            self.tags["CONTROLLER"] = str(controller)
        if hero_entity:
            self.tags["HERO_ENTITY"] = str(hero_entity)

    def query_tag(self, tag, default="0"):
        return self.tags.get(tag, default)


class _LogState:
    """对手是牧师；玩家实体 id 用真实游戏的 2/3 形态。"""

    def __init__(self, my_turn=2, oppo_turn=3, oppo_class="PRIEST"):
        self.game_generation = 7
        self.my_player_id = "1"
        self.oppo_player_id = "2"
        self.entity_dict = {
            "2": _Entity(controller="1", hero_entity="11"),
            "3": _Entity(controller="2", hero_entity="12"),
            "11": _Entity(card_id="HERO_01", controller="1"),
            "12": _Entity(card_id="HERO_09", controller="2"),
        }
        self.entity_dict["12"].tags["CLASS"] = oppo_class
        self.entity_dict["2"].tags["TURN"] = str(my_turn)
        self.entity_dict["3"].tags["TURN"] = str(oppo_turn)

    @property
    def my_entity(self):
        return self.entity_dict["2"]

    @property
    def oppo_entity(self):
        return self.entity_dict["3"]


def _event(card_id, side, round_turn=3, entity_id="900"):
    return sr.CardEvent(card_id=card_id, entity_id=entity_id, side=side,
                        round=round_turn)


class _Base(unittest.TestCase):
    def setUp(self):
        self._saved = {
            "log_state": FSM_action.log_state,
            "quitting": FSM_action.quitting_flag,
            "concede": FSM_action._concede_triggered,
            "name": FSM_action._name_match_result,
            "warned": FSM_action._NAME_MISMATCH_SURRENDER_WARNED,
        }
        FSM_action.log_state = _LogState()
        FSM_action.quitting_flag = False
        FSM_action._concede_triggered = False
        FSM_action._name_match_result = None
        FSM_action._NAME_MISMATCH_SURRENDER_WARNED = None
        FSM_action.surrender_rules.engine.reset()
        self.logs = []
        self.concedes = []

    def tearDown(self):
        FSM_action.log_state = self._saved["log_state"]
        FSM_action.quitting_flag = self._saved["quitting"]
        FSM_action._concede_triggered = self._saved["concede"]
        FSM_action._name_match_result = self._saved["name"]
        FSM_action._NAME_MISMATCH_SURRENDER_WARNED = self._saved["warned"]
        FSM_action.surrender_rules.engine.reset()

    # ---------------------------------------------------------- helpers
    def _facts(self, played=(), revealed=()):
        facts = FSM_action.surrender_rules.engine.facts
        for event in played:
            facts.add_played(event)
        for event in revealed:
            facts.add_revealed(event)

    def _snapshot(self, is_my_turn=False, turn=4):
        return SimpleNamespace(is_end=False, is_my_turn=is_my_turn,
                               game_num_turns_in_play=turn)

    def _step(self, snapshot=None):
        """走一遍主循环（只到判定那一步就返回，不真的去操作游戏）。"""
        with (
            patch.object(FSM_action, "refresh_snapshot",
                         return_value=snapshot or self._snapshot()),
            patch.object(FSM_action, "_load_surrender_config",
                         return_value=dict(CONFIG)),
            patch.object(FSM_action, "_do_concede",
                         side_effect=lambda reason=None:
                         self.concedes.append(reason)),
            patch.object(FSM_action, "_report_automation_diagnostic"),
            patch.object(FSM_action, "_human_like_opponent_minions"),
            patch.object(FSM_action.manual_controller, "output",
                         side_effect=self.logs.append),
        ):
            return FSM_action.run_automatic_battle_step()


class OpponentTurnTests(_Base):
    def test_opponent_turn_still_triggers_concede(self):
        # 用户场景：对方在第 3 回合打出神秘访客，此刻轮到对方。
        self._facts(played=[_event("REV_246", "opponent", 3)],
                    revealed=[_event("JAIL_430", "opponent", 0)])
        result = self._step(self._snapshot(is_my_turn=False, turn=3))
        self.assertIsNone(result)
        self.assertEqual(1, len(self.concedes))
        self.assertIn("牧师 · 阿扎莉娜 + 神秘访客", self.concedes[0])
        self.assertTrue(FSM_action._concede_triggered)

    def test_log_mentions_opponent_and_evidence(self):
        self._facts(played=[_event("REV_246", "opponent", 3)],
                    revealed=[_event("JAIL_430", "opponent", 0)])
        self._step(self._snapshot(is_my_turn=False, turn=3))
        joined = "\n".join(self.logs)
        self.assertIn("识别为对方卡组", joined)
        self.assertIn("对方职业", joined)

    def test_only_concedes_once_per_game(self):
        self._facts(played=[_event("REV_246", "opponent", 3)],
                    revealed=[_event("JAIL_430", "opponent", 0)])
        self._step(self._snapshot(is_my_turn=False, turn=3))
        self._step(self._snapshot(is_my_turn=False, turn=3))
        self.assertEqual(1, len(self.concedes))


class DirectionTests(_Base):
    def test_self_cards_never_concede(self):
        self._facts(played=[_event("REV_246", "self", 3)],
                    revealed=[_event("JAIL_430", "self", 0)])
        self._step(self._snapshot(is_my_turn=False, turn=3))
        self.assertEqual([], self.concedes)
        self.assertFalse(FSM_action._concede_triggered)

    def test_self_and_opponent_mixed_only_counts_opponent_side(self):
        # 我方也有一张，但对方没有 → 仍然不触发。
        self._facts(played=[_event("REV_246", "self", 3)],
                    revealed=[_event("JAIL_430", "opponent", 0)])
        self._step(self._snapshot(is_my_turn=False, turn=3))
        self.assertEqual([], self.concedes)


class GuardTests(_Base):
    def test_name_mismatch_pauses_rule_surrender(self):
        FSM_action._name_match_result = {"matched": False, "config": "A#1",
                                         "players": {"1": "B#2"}}
        self._facts(played=[_event("REV_246", "opponent", 3)],
                    revealed=[_event("JAIL_430", "opponent", 0)])
        self._step(self._snapshot(is_my_turn=False, turn=3))
        self.assertEqual([], self.concedes)
        self.assertTrue(any("暂停判定" in line for line in self.logs))

    def test_name_mismatch_warns_only_once_per_game(self):
        FSM_action._name_match_result = {"matched": False, "config": "A#1",
                                         "players": {"1": "B#2"}}
        self._step(self._snapshot(is_my_turn=False, turn=3))
        self._step(self._snapshot(is_my_turn=False, turn=3))
        self.assertEqual(1, len([line for line in self.logs
                                 if "暂停判定" in line]))

    def test_disabled_switch_does_nothing(self):
        disabled = dict(CONFIG, enabled=False)
        self._facts(played=[_event("REV_246", "opponent", 3)],
                    revealed=[_event("JAIL_430", "opponent", 0)])
        with (
            patch.object(FSM_action, "refresh_snapshot",
                         return_value=self._snapshot(is_my_turn=False, turn=3)),
            patch.object(FSM_action, "_load_surrender_config",
                         return_value=disabled),
            patch.object(FSM_action, "_do_concede",
                         side_effect=lambda reason=None:
                         self.concedes.append(reason)),
            patch.object(FSM_action, "_report_automation_diagnostic"),
            patch.object(FSM_action, "_human_like_opponent_minions"),
            patch.object(FSM_action.manual_controller, "output",
                         side_effect=self.logs.append),
        ):
            FSM_action.run_automatic_battle_step()
        self.assertEqual([], self.concedes)

    def test_quitting_flag_blocks_evaluation(self):
        FSM_action.quitting_flag = True
        self._facts(played=[_event("REV_246", "opponent", 3)],
                    revealed=[_event("JAIL_430", "opponent", 0)])
        self.assertIsNone(FSM_action._maybe_surrender_by_rules())
        self.assertEqual([], self.concedes)


class LegacyConcedeTextTests(unittest.TestCase):
    """旧的胜率投降文案必须原样保留（用户看惯了这句）。"""

    def test_default_reason_keeps_old_message(self):
        logs = []
        with (
            patch.object(FSM_action.manual_controller, "output",
                         side_effect=logs.append),
            patch.object(FSM_action.click, "hearthstone_action_session"),
            patch.object(FSM_action.click, "click_setting"),
            patch.object(FSM_action.click, "click_concede"),
            patch.object(FSM_action.time, "sleep"),
        ):
            FSM_action._do_concede()
        self.assertTrue(any("持续低胜率，开始自动认输" in line for line in logs))

    def test_custom_reason_is_used(self):
        logs = []
        with (
            patch.object(FSM_action.manual_controller, "output",
                         side_effect=logs.append),
            patch.object(FSM_action.click, "hearthstone_action_session"),
            patch.object(FSM_action.click, "click_setting"),
            patch.object(FSM_action.click, "click_concede"),
            patch.object(FSM_action.time, "sleep"),
        ):
            FSM_action._do_concede(reason="识别为对方卡组「测试」")
        self.assertTrue(any("识别为对方卡组「测试」，开始自动认输" in line
                            for line in logs))


class StateApiTests(_Base):
    def test_surrender_rule_state_reports_progress(self):
        self._facts(revealed=[_event("JAIL_430", "opponent", 0)])
        with patch.object(FSM_action, "_load_surrender_config",
                          return_value=dict(CONFIG)):
            data = FSM_action.surrender_rule_state()
        self.assertTrue(data["config"]["enabled"])
        deck = data["state"]["decks"][0]
        self.assertEqual(deck["total"], 2)
        self.assertEqual(deck["done"], 1)
        self.assertFalse(deck["matched"])


if __name__ == "__main__":
    unittest.main()
