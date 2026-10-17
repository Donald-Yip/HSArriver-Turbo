"""卡组识别投降：配置校验、编译、求值（含"只判对方"的方向性）。

全部纯函数 + 合成卡库，毫秒级，不碰炉石、不碰网络。
"""

import unittest

import card_catalog
import surrender_rules as sr
from log_op import (LineInfoContainer, LOG_LINE_BLOCK_START_TRIGGER,
                    LOG_LINE_CREATE_GAME, LOG_LINE_SHOW_ENTITY,
                    LOG_LINE_TAG_CHANGE)


# ---------------------------------------------------------------- 合成环境
def _card(card_id, name, card_class="PRIEST", card_type="MINION"):
    return {"id": card_id, "name": name, "cardClass": card_class,
            "type": card_type, "cost": 3, "set": "TEST"}


CATALOG = card_catalog.CardCatalog([
    _card("JAIL_430", "裂魂者阿扎莉娜", "PRIEST", "MINION"),
    _card("REV_246", "神秘访客", "PRIEST", "MINION"),
    _card("CORE_REV_246", "神秘访客", "PRIEST", "MINION"),
    _card("CS2_029", "火球术", "MAGE", "SPELL"),
])


class FakeEntity:
    def __init__(self, card_id="", controller="", zone="", turn=0,
                 hero_entity="", hero_class="", cardtype=""):
        self.card_id = card_id
        self.tag_dict = {}
        self.set("CONTROLLER", controller)
        self.set("ZONE", zone)
        if turn:
            self.set("TURN", str(turn))
        if hero_entity:
            self.set("HERO_ENTITY", str(hero_entity))
        if hero_class:
            self.set("CLASS", hero_class)
        if cardtype:
            self.set("CARDTYPE", cardtype)

    def set(self, tag, value):
        self.tag_dict[tag] = str(value)

    def query_tag(self, tag, default="0"):
        return self.tag_dict.get(tag, default)


class FakeLogState:
    """够用的 LogState 替身：my_player_id / entity_dict / 双方玩家实体。"""

    def __init__(self, my_pid="1", my_turn=0, oppo_turn=0,
                 oppo_hero_class="PRIEST", oppo_hero="JAIL_430"):
        self.game_generation = 1
        self.my_player_id = my_pid
        self.oppo_player_id = str(3 - int(my_pid))
        self.entity_dict = {}
        # 玩家实体 1/3（炉石里玩家实体 id 通常是 2/3，这里无所谓）
        self.entity_dict["101"] = FakeEntity(controller=my_pid,
                                             turn=my_turn, zone="PLAY")
        self.entity_dict["102"] = FakeEntity(controller=self.oppo_player_id,
                                             turn=oppo_turn, zone="PLAY")
        # 对方英雄：CLASS 才是权威职业
        self.entity_dict["201"] = FakeEntity(
            card_id=oppo_hero, controller=self.oppo_player_id,
            zone="PLAY", hero_class=oppo_hero_class, cardtype="HERO")
        self.entity_dict["102"].set("HERO_ENTITY", "201")
        self.entity_dict["101"].set("HERO_ENTITY", "202")
        self.entity_dict["202"] = FakeEntity(
            card_id="HERO_01", controller=my_pid, zone="PLAY",
            hero_class="WARRIOR", cardtype="HERO")

    @property
    def my_entity(self):
        return self.entity_dict["101"]

    @property
    def oppo_entity(self):
        return self.entity_dict["102"]

    def add(self, entity_id, card_id, controller, zone="PLAY"):
        self.entity_dict[str(entity_id)] = FakeEntity(
            card_id=card_id, controller=controller, zone=zone)
        return self.entity_dict[str(entity_id)]


def _event(card_id, side, round_turn=3, entity_id="900"):
    return sr.CardEvent(card_id=card_id, entity_id=entity_id, side=side,
                        round=round_turn, my_turn=round_turn,
                        oppo_turn=round_turn - 1)


def _facts(played=(), revealed=(), start_of_game=()):
    facts = sr.MatchFacts()
    for item in played:
        facts.add_played(item)
    for item in revealed:
        facts.add_revealed(item)
    for item in start_of_game:
        facts.add_start_of_game(item)
    return facts


def _config(when, hero_class="PRIEST", enabled=True):
    return {
        "enabled": enabled,
        "decks": [{
            "id": "priest_alizana",
            "name": "牧师 · 阿扎莉娜 + 神秘访客",
            "enabled": True,
            "class": hero_class,
            "when": when,
        }],
    }


def _ctx(facts, opponent_class="PRIEST", round_turn=3, log_state=None):
    return sr.EvalContext(facts=facts, opponent_class=opponent_class,
                          my_turn=round_turn, oppo_turn=round_turn - 1,
                          round_turn=round_turn, log_state=log_state)


USER_EXAMPLE_WHEN = {
    "all": [
        {"type": "seen", "card": "裂魂者阿扎莉娜"},
        {"type": "played", "card": "神秘访客",
         "within": {"field": "round", "max": 4}},
    ]
}


# ---------------------------------------------------------------- 校验
class NormalizeTests(unittest.TestCase):
    def test_user_example_is_valid_and_defaults_to_opponent(self):
        result = sr.normalize_config(_config(USER_EXAMPLE_WHEN))
        self.assertTrue(result["ok"], result["errors"])
        leaves = result["config"]["decks"][0]["when"]["all"]
        self.assertEqual(leaves[0]["side"], "opponent")
        self.assertEqual(leaves[1]["side"], "opponent")
        self.assertEqual(result["config"]["decks"][0]["class"], "PRIEST")

    def test_and_or_aliases_become_all_any(self):
        result = sr.normalize_config(_config({"and": [
            {"type": "played", "card": "神秘访客"}]}))
        self.assertTrue(result["ok"])
        self.assertIn("all", result["config"]["decks"][0]["when"])
        result = sr.normalize_config(_config({"or": [
            {"type": "played", "card": "神秘访客"}]}))
        self.assertIn("any", result["config"]["decks"][0]["when"])

    def test_unknown_leaf_type_reports_path(self):
        result = sr.normalize_config(_config({"all": [
            {"type": "played", "card": "神秘访客"},
            {"type": "magic", "card": "神秘访客"}]}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["path"], "decks[0].when.all[1].type")

    def test_mixed_logic_keys_rejected(self):
        result = sr.normalize_config(_config({"all": [
            {"type": "played", "card": "神秘访客"}],
            "any": [{"type": "played", "card": "神秘访客"}]}))
        self.assertFalse(result["ok"])
        self.assertIn("只能有一个逻辑键", result["errors"][0]["message"])

    def test_empty_card_rejected(self):
        result = sr.normalize_config(_config({"type": "played", "card": []}))
        self.assertFalse(result["ok"])
        self.assertTrue(any("至少要写一张卡" in item["message"]
                            for item in result["errors"]))

    def test_bad_class_rejected_instead_of_silently_ignored(self):
        result = sr.normalize_config(_config(USER_EXAMPLE_WHEN,
                                            hero_class="牧师家"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["path"], "decks[0].class")

    def test_reversed_within_rejected(self):
        result = sr.normalize_config(_config({"type": "played",
                                             "card": "神秘访客",
                                             "within": {"min": 5, "max": 2}}))
        self.assertFalse(result["ok"])
        self.assertIn("写反了", result["errors"][0]["message"])

    def test_negative_at_least_rejected(self):
        result = sr.normalize_config(_config({"type": "played",
                                             "card": "神秘访客",
                                             "at_least": -1}))
        self.assertFalse(result["ok"])

    def test_unknown_field_rejected(self):
        result = sr.normalize_config(_config({"type": "played",
                                             "card": "神秘访客",
                                             "typo_field": 1}))
        self.assertFalse(result["ok"])
        self.assertIn("不认识的字段", result["errors"][0]["message"])

    def test_warns_when_no_event_leaf(self):
        result = sr.normalize_config(_config({"type": "turn", "max": 3}))
        self.assertTrue(result["ok"])
        self.assertTrue(any("开局就可能" in item
                            for item in result["warnings"]))

    def test_side_self_must_be_explicit(self):
        result = sr.normalize_config(_config({"type": "played",
                                             "card": "神秘访客",
                                             "side": "self"}))
        self.assertEqual(result["config"]["decks"][0]["when"]["side"], "self")


# ---------------------------------------------------------------- 编译
class CompileTests(unittest.TestCase):
    def test_card_name_compiles_to_all_versions(self):
        result = sr.normalize_config(_config({"type": "revealed",
                                             "card": "神秘访客"}))
        decks, errors, corrected = sr.compile_config(result["config"],
                                                     CATALOG)
        self.assertEqual(errors, [])
        leaf = decks[0].expr[1]
        self.assertEqual(leaf.card_ids, {"REV_246", "CORE_REV_246"})
        self.assertEqual(corrected, [])

    def test_misspelled_card_is_corrected_and_reported(self):
        result = sr.normalize_config(_config({"type": "revealed",
                                             "card": "裂魂者阿莉扎娜"}))
        decks, errors, corrected = sr.compile_config(result["config"],
                                                     CATALOG)
        self.assertEqual(errors, [])
        self.assertEqual(decks[0].expr[1].card_ids, {"JAIL_430"})
        self.assertEqual(corrected[0]["to"], "裂魂者阿扎莉娜")

    def test_unknown_card_is_compile_error(self):
        result = sr.normalize_config(_config({"type": "revealed",
                                             "card": "根本没有这张卡"}))
        decks, errors, _corrected = sr.compile_config(result["config"],
                                                      CATALOG)
        self.assertEqual(decks, [])
        self.assertIn("card", errors[0]["path"])


# ---------------------------------------------------------------- 求值
class EvaluateTests(unittest.TestCase):
    def setUp(self):
        normalized = sr.normalize_config(_config(USER_EXAMPLE_WHEN))
        self.decks, errors, _ = sr.compile_config(normalized["config"],
                                                  CATALOG)
        self.assertEqual(errors, [])

    def test_user_example_fires_at_round_3(self):
        facts = _facts(played=[_event("REV_246", "opponent", 3)],
                       revealed=[_event("JAIL_430", "opponent", 1)])
        hit = sr.evaluate_decks(self.decks, _ctx(facts, "PRIEST", 3))
        self.assertIsNotNone(hit)
        self.assertEqual(hit.id, "priest_alizana")

    def test_round_4_still_fires(self):
        facts = _facts(played=[_event("REV_246", "opponent", 4)],
                       revealed=[_event("JAIL_430", "opponent", 1)])
        self.assertIsNotNone(sr.evaluate_decks(self.decks, _ctx(facts, "PRIEST", 4)))

    def test_round_5_does_not_fire(self):
        facts = _facts(played=[_event("REV_246", "opponent", 5)],
                       revealed=[_event("JAIL_430", "opponent", 1)])
        self.assertIsNone(sr.evaluate_decks(self.decks, _ctx(facts, "PRIEST", 5)))

    def test_wrong_class_does_not_fire(self):
        facts = _facts(played=[_event("REV_246", "opponent", 3)],
                       revealed=[_event("JAIL_430", "opponent", 1)])
        self.assertIsNone(sr.evaluate_decks(self.decks, _ctx(facts, "MAGE", 3)))

    def test_unknown_class_does_not_fire(self):
        facts = _facts(played=[_event("REV_246", "opponent", 3)],
                       revealed=[_event("JAIL_430", "opponent", 1)])
        self.assertIsNone(sr.evaluate_decks(self.decks, _ctx(facts, "", 3)))

    def test_missing_reveal_does_not_fire(self):
        facts = _facts(played=[_event("REV_246", "opponent", 3)])
        self.assertIsNone(sr.evaluate_decks(self.decks, _ctx(facts, "PRIEST", 3)))


class DirectionTests(unittest.TestCase):
    """用户硬要求：只判对方。我方有这些卡，绝不许投降。"""

    def setUp(self):
        normalized = sr.normalize_config(_config(USER_EXAMPLE_WHEN))
        self.decks, _errors, _ = sr.compile_config(normalized["config"],
                                                   CATALOG)

    def test_self_cards_do_not_trigger(self):
        facts = _facts(played=[_event("REV_246", "self", 3)],
                       revealed=[_event("JAIL_430", "self", 1)])
        self.assertIsNone(sr.evaluate_decks(self.decks, _ctx(facts, "PRIEST", 3)))

    def test_self_start_of_game_does_not_trigger(self):
        facts = _facts(start_of_game=[_event("JAIL_430", "self", 0)])
        normalized = sr.normalize_config(_config(
            {"type": "start_of_game", "card": "裂魂者阿扎莉娜"}))
        decks, _errors, _ = sr.compile_config(normalized["config"], CATALOG)
        self.assertIsNone(sr.evaluate_decks(decks, _ctx(facts, "PRIEST", 1)))

    def test_opponent_start_of_game_triggers(self):
        facts = _facts(start_of_game=[_event("JAIL_430", "opponent", 0)])
        normalized = sr.normalize_config(_config(
            {"type": "start_of_game", "card": "裂魂者阿扎莉娜"}))
        decks, _errors, _ = sr.compile_config(normalized["config"], CATALOG)
        self.assertIsNotNone(sr.evaluate_decks(decks, _ctx(facts, "PRIEST", 1)))

    def test_unknown_side_never_trigger(self):
        # 归属判不出来（my_player_id 未定）时，事件 side 为空 → 不成立。
        facts = _facts(played=[_event("REV_246", "", 3)])
        self.assertIsNone(sr.evaluate_decks(self.decks, _ctx(facts, "PRIEST", 3)))


class LogicTests(unittest.TestCase):
    def _decks(self, when, hero_class=""):
        normalized = sr.normalize_config(_config(when, hero_class=hero_class))
        decks, errors, _ = sr.compile_config(normalized["config"], CATALOG)
        self.assertEqual(errors, [])
        return decks

    def test_any_branch(self):
        decks = self._decks({"any": [
            {"type": "played", "card": "神秘访客"},
            {"type": "played", "card": "火球术"},
        ]})
        facts = _facts(played=[_event("CS2_029", "opponent", 2)])
        self.assertIsNotNone(sr.evaluate_decks(decks, _ctx(facts, "", 2)))

    def test_not_branch(self):
        decks = self._decks({"all": [
            {"type": "played", "card": "神秘访客"},
            {"not": {"type": "played", "card": "火球术"}},
        ]})
        ok = _facts(played=[_event("REV_246", "opponent", 2)])
        self.assertIsNotNone(sr.evaluate_decks(decks, _ctx(ok, "", 2)))
        blocked = _facts(played=[_event("REV_246", "opponent", 2),
                                 _event("CS2_029", "opponent", 2)])
        self.assertIsNone(sr.evaluate_decks(decks, _ctx(blocked, "", 2)))

    def test_at_least_counts_distinct_cards(self):
        decks = self._decks({"type": "played",
                             "card": ["神秘访客", "火球术"],
                             "at_least": 2})
        one = _facts(played=[_event("REV_246", "opponent", 2)])
        self.assertIsNone(sr.evaluate_decks(decks, _ctx(one, "", 2)))
        # 同一张卡打三次也只算一张
        same = _facts(played=[_event("REV_246", "opponent", 2),
                              _event("REV_246", "opponent", 3),
                              _event("REV_246", "opponent", 4)])
        self.assertIsNone(sr.evaluate_decks(decks, _ctx(same, "", 4)))
        two = _facts(played=[_event("REV_246", "opponent", 2),
                             _event("CS2_029", "opponent", 2)])
        self.assertIsNotNone(sr.evaluate_decks(decks, _ctx(two, "", 2)))

    def test_decks_are_or_ed_and_first_wins(self):
        raw = {"enabled": True, "decks": [
            {"id": "a", "name": "第一套",
             "when": {"type": "played", "card": "神秘访客"}},
            {"id": "b", "name": "第二套",
             "when": {"type": "played", "card": "神秘访客"}},
        ]}
        normalized = sr.normalize_config(raw)
        decks, _errors, _ = sr.compile_config(normalized["config"], CATALOG)
        facts = _facts(played=[_event("REV_246", "opponent", 2)])
        hit = sr.evaluate_decks(decks, _ctx(facts, "", 2))
        self.assertEqual(hit.id, "a")

    def test_disabled_deck_still_compiled_but_caller_filters(self):
        # enabled 由调用方（/api 的启用开关）决定；这里只确认不报错。
        normalized = sr.normalize_config(_config(USER_EXAMPLE_WHEN,
                                                 enabled=False))
        self.assertTrue(normalized["ok"])


# ---------------------------------------------------------------- 引擎
class EngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = sr.SurrenderRuleEngine()
        self.log_state = FakeLogState(my_turn=3, oppo_turn=2)

    def test_opponent_class_from_class_tag(self):
        self.assertEqual(self.engine.opponent_class(self.log_state), "PRIEST")

    def test_played_event_recorded_on_hand_to_play(self):
        entity = self.log_state.add(301, "REV_246", "2", zone="PLAY")
        self.engine.observe(self.log_state,
                            LineInfoContainer(LOG_LINE_TAG_CHANGE,
                                              entity="301", tag="ZONE",
                                              value="PLAY"),
                            prev_zone="HAND")
        self.assertEqual(len(self.engine.facts.played), 1)
        self.assertEqual(self.engine.facts.played[0].card_id, "REV_246")
        self.assertEqual(self.engine.facts.played[0].side, "opponent")

    def test_draw_or_summon_does_not_count_as_played(self):
        self.log_state.add(302, "REV_246", "2", zone="PLAY")
        for prev in ("DECK", "SETASIDE", None):
            self.engine.observe(self.log_state,
                                LineInfoContainer(LOG_LINE_TAG_CHANGE,
                                                  entity="302", tag="ZONE",
                                                  value="PLAY"),
                                prev_zone=prev)
        self.assertEqual(self.engine.facts.played, [])

    def test_self_play_is_recorded_as_self(self):
        self.log_state.add(303, "REV_246", "1", zone="PLAY")
        self.engine.observe(self.log_state,
                            LineInfoContainer(LOG_LINE_TAG_CHANGE,
                                              entity="303", tag="ZONE",
                                              value="PLAY"),
                            prev_zone="HAND")
        self.assertEqual(self.engine.facts.played[0].side, "self")

    def test_show_entity_marks_revealed_with_side(self):
        self.log_state.add(304, "", "2", zone="DECK")
        self.engine.observe(self.log_state,
                            LineInfoContainer(LOG_LINE_SHOW_ENTITY,
                                              entity="304", card="JAIL_430"))
        self.assertIn("JAIL_430", self.engine.facts.revealed)
        self.assertEqual(self.engine.facts.revealed["JAIL_430"].side,
                         "opponent")

    def test_start_of_game_card_is_recorded_even_though_never_played(self):
        # 阿扎莉娜：开局 BLOCK_START TRIGGER（cardId 空）→ SHOW_ENTITY 补 id
        self.log_state.add(305, "", "2", zone="DECK")
        self.engine.observe(self.log_state,
                            LineInfoContainer(LOG_LINE_BLOCK_START_TRIGGER,
                                              entity_id="305"))
        self.engine.observe(self.log_state,
                            LineInfoContainer(LOG_LINE_SHOW_ENTITY,
                                              entity="305", card="JAIL_430"))
        self.assertIn("JAIL_430", self.engine.facts.start_of_game)
        self.assertEqual(self.engine.facts.start_of_game["JAIL_430"].side,
                         "opponent")
        self.assertEqual(self.engine.facts.played, [])

    def test_create_game_resets_facts(self):
        self.log_state.add(306, "REV_246", "2", zone="PLAY")
        self.engine.observe(self.log_state,
                            LineInfoContainer(LOG_LINE_TAG_CHANGE,
                                              entity="306", tag="ZONE",
                                              value="PLAY"),
                            prev_zone="HAND")
        self.assertEqual(len(self.engine.facts.played), 1)
        self.engine.observe(self.log_state,
                            LineInfoContainer(LOG_LINE_CREATE_GAME))
        self.assertEqual(self.engine.facts.played, [])

    def test_check_fires_once_and_remembers_hit(self):
        cfg = _config(USER_EXAMPLE_WHEN)
        self.log_state.add(307, "REV_246", "2", zone="PLAY")
        self.engine.observe(self.log_state,
                            LineInfoContainer(LOG_LINE_TAG_CHANGE,
                                              entity="307", tag="ZONE",
                                              value="PLAY"),
                            prev_zone="HAND")
        self.engine.observe(self.log_state,
                            LineInfoContainer(LOG_LINE_SHOW_ENTITY,
                                              entity="307", card="REV_246"))
        hit = self.engine.check(cfg, self.log_state)
        self.assertIsNone(hit)  # 还差"出现过阿扎莉娜"

        self.log_state.add(308, "JAIL_430", "2", zone="DECK")
        self.engine.observe(self.log_state,
                            LineInfoContainer(LOG_LINE_SHOW_ENTITY,
                                              entity="308", card="JAIL_430"))
        hit = self.engine.check(cfg, self.log_state)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["action"], "concede")
        self.assertTrue(any("对方职业" in line for line in hit["evidence"]))
        # 同一局只触发一次
        self.assertIsNone(self.engine.check(cfg, self.log_state))
        self.assertEqual(sr.SurrenderRuleEngine.status(self.engine,
                                                       self.log_state)["hit"]["name"],
                         "牧师 · 阿扎莉娜 + 神秘访客")

    def test_disabled_config_never_fires(self):
        cfg = _config(USER_EXAMPLE_WHEN, enabled=False)
        self.log_state.add(309, "REV_246", "2", zone="PLAY")
        self.engine.observe(self.log_state,
                            LineInfoContainer(LOG_LINE_TAG_CHANGE,
                                              entity="309", tag="ZONE",
                                              value="PLAY"),
                            prev_zone="HAND")
        self.assertIsNone(self.engine.check(cfg, self.log_state))

    def test_broken_config_fails_safe(self):
        cfg = {"enabled": True, "decks": [{"id": "x", "when": {"type": "??"}}]}
        self.assertEqual(self.engine.configure(cfg), [])

    def test_status_reports_progress(self):
        cfg = _config(USER_EXAMPLE_WHEN)
        self.engine.configure(cfg)
        self.log_state.add(310, "JAIL_430", "2", zone="DECK")
        self.engine.observe(self.log_state,
                            LineInfoContainer(LOG_LINE_SHOW_ENTITY,
                                              entity="310", card="JAIL_430"))
        status = self.engine.status(self.log_state)
        self.assertEqual(status["opponent_class"], "PRIEST")
        deck = status["decks"][0]
        self.assertEqual(deck["total"], 2)
        self.assertEqual(deck["done"], 1)      # 只满足"出现过"
        self.assertFalse(deck["matched"])

    def test_preview_does_not_trigger_state(self):
        cfg = _config(USER_EXAMPLE_WHEN)
        self.log_state.add(311, "JAIL_430", "2", zone="DECK")
        self.engine.observe(self.log_state,
                            LineInfoContainer(LOG_LINE_SHOW_ENTITY,
                                              entity="311", card="JAIL_430"))
        result = self.engine.preview(cfg, self.log_state)
        self.assertTrue(result["ok"])
        self.assertFalse(result["would_fire_now"])
        self.assertFalse(self.engine.status(self.log_state)["triggered"])


if __name__ == "__main__":
    unittest.main()
