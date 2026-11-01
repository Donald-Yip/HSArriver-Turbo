# -*- coding: utf-8 -*-
"""通用策略（ai/通用/通用.py）的评分、换牌与搜索回归测试。

覆盖：
* 策略能被注册表发现，且按现有本地策略的形式提供
  choose / rankStaticState / rankActions / rankState；
* 线性特征：随从身材/关键字、低血权重、低敌血加分、威胁等级、斩杀/阵亡；
* rankActions 只做溢出伤害惩罚（Silverfish lostDamage 的通用部分）；
* choose 换掉高费牌、至少保留一张、不换幸运币；
* 搜索在空状态/有可出随从/真实回放 fixture 上都能工作。
"""

from __future__ import annotations

import importlib
import math
import unittest
from types import SimpleNamespace

import ai.deck_strategy as deck_strategy_module
import search.search
from hslegend.domain import ActionKind
from search.ai_action import AiAction, AiSlot
from search.ai_state import AiState
from tests.support.strategy_fixtures import load_fixture, state_from_dict


def _hero_dict(card_id: str, *, is_mine: bool, health: int = 30) -> dict:
    return {
        "cardtype": "HERO",
        "entity_id": "1" if is_mine else "2",
        "card_id": card_id,
        "zone": "PLAY",
        "zone_pos": 0,
        "current_cost": 0,
        "overload": 0,
        "is_mine": is_mine,
        "max_health": health,
        "damage": 0,
        "attack": 0,
        "armor": 0,
        "exhausted": 0,
        "frozen": 0,
        "cant_attack": 0,
    }


def _minion_dict(*, entity_id="10", card_id="TEST_MINION", zone="PLAY",
                 zone_pos=1, attack=2, max_health=2, **tags) -> dict:
    data = {
        "cardtype": "MINION",
        "entity_id": entity_id,
        "card_id": card_id,
        "zone": zone,
        "zone_pos": zone_pos,
        "current_cost": 2,
        "overload": 0,
        "is_mine": True,
        "attack": attack,
        "max_health": max_health,
        "damage": 0,
        "exhausted": 0,
        "num_turns_in_play": 2,
    }
    data.update(tags)
    return data


def _state_dict(**overrides) -> dict:
    data = {
        "is_end": False,
        "is_my_turn": True,
        "game_num_turns_in_play": 5,
        "log_revision": 1,
        "mana": {"total": 2, "used": 0, "temp": 0},
        "my_hero": _hero_dict("HERO_01", is_mine=True),
        "oppo_hero": _hero_dict("HERO_02", is_mine=False),
        "my_minions": [],
        "oppo_minions": [],
        "my_locations": [],
        "oppo_locations": [],
        "my_hand_cards": [],
    }
    data.update(overrides)
    return data


class _State:
    """SimpleNamespace 版 AiState 替身：只给 ordered_board/评分要读的字段。"""

    def __init__(self, **kwargs):
        self.my_minions = kwargs.pop("my_minions", [])
        self.oppo_minions = kwargs.pop("oppo_minions", [])
        self.my_locations = kwargs.pop("my_locations", [])
        self.oppo_locations = kwargs.pop("oppo_locations", [])
        self.my_hand_cards = kwargs.pop("my_hand_cards", [])
        self.my_hero = kwargs.pop("my_hero", None)
        self.oppo_hero = kwargs.pop("oppo_hero", None)
        self.my_weapon = kwargs.pop("my_weapon", None)
        self.oppo_weapon = kwargs.pop("oppo_weapon", None)
        self.my_total_mana = kwargs.pop("my_total_mana", 0)
        self.my_used_mana = kwargs.pop("my_used_mana", 0)
        self.my_temp_mana = kwargs.pop("my_temp_mana", 0)
        self.game_num_turns_in_play = kwargs.pop("game_num_turns_in_play", 5)
        self.log_revision = kwargs.pop("log_revision", 1)
        self.discover_choice_count = kwargs.pop("discover_choice_count", None)
        self.general_choice_cards = kwargs.pop("general_choice_cards", {})
        self.prepared_entity_ids = kwargs.pop("prepared_entity_ids", set())
        self.blocked_action_keys = kwargs.pop("blocked_action_keys", set())


def _entity(zone_pos, attack, health, **tags):
    entity = SimpleNamespace(
        entity_id=str(zone_pos), card_id="TEST_MINION", zone="PLAY",
        zone_pos=zone_pos, attack=attack, max_health=health, damage=0,
        health=health, cardtype="MINION")
    for key, value in tags.items():
        setattr(entity, key, value)
    return entity


def _hero(health, attack=0):
    return SimpleNamespace(health=health, max_health=health, damage=0,
                           armor=0, attack=attack, cardtype="HERO",
                           entity_id="h", zone_pos=0)


class GenericStrategyRegistryTests(unittest.TestCase):
    def test_registry_lists_and_loads_generic_strategy(self):
        folders = deck_strategy_module.strategy_folders(refresh=True)
        self.assertIn("通用", folders)

        strategy = deck_strategy_module.deck_strategy("通用")
        self.assertIsNotNone(strategy)
        self.assertTrue(callable(strategy.choose))
        self.assertTrue(callable(strategy.rankStaticState))
        self.assertTrue(callable(strategy.rankActions))
        self.assertTrue(callable(strategy.rankState))
        self.assertIs(True, getattr(strategy, "RANKS_FROM_SEQUENCE_START", False))

    def test_generic_module_has_no_card_ids(self):
        module = importlib.import_module("ai.通用.通用")
        with open(module.__file__, encoding="utf-8") as handle:
            source = handle.read()
        # 通用策略约定：不出现任何具体 card_id 常量（避免变成卡组策略）。
        for token in ("CARD_ID_", "CARD_IDS_", "CARD_ID "):
            self.assertNotIn(token, source)


class GenericStaticScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strategy = deck_strategy_module.deck_strategy("通用")
        cls.module = importlib.import_module("ai.通用.通用")

    def test_bigger_minion_scores_higher(self):
        small = state_from_dict(_state_dict(my_minions=[_minion_dict(attack=1, max_health=1)]))
        big = state_from_dict(_state_dict(my_minions=[_minion_dict(attack=3, max_health=4)]))
        self.assertGreater(self.strategy.rankStaticState(big),
                           self.strategy.rankStaticState(small))

    def test_keywords_add_value(self):
        plain = state_from_dict(_state_dict(my_minions=[_minion_dict(taunt=0, divine_shield=0)]))
        keywords = state_from_dict(_state_dict(my_minions=[_minion_dict(
            taunt=1, divine_shield=1, windfury=1, poisonous=1)]))
        self.assertGreater(self.strategy.rankStaticState(keywords),
                           self.strategy.rankStaticState(plain))

    def test_enemy_minions_reduce_score(self):
        without = state_from_dict(_state_dict())
        with_enemy = state_from_dict(_state_dict(
            oppo_minions=[dict(_minion_dict(), is_mine=False)]))
        self.assertLess(self.strategy.rankStaticState(with_enemy),
                        self.strategy.rankStaticState(without))

    def test_threat_level_uses_enemy_board_damage(self):
        green = state_from_dict(_state_dict(my_hero=_hero_dict("H", is_mine=True, health=30)))
        yellow = state_from_dict(_state_dict(
            my_hero=_hero_dict("H", is_mine=True, health=12),
            oppo_minions=[dict(_minion_dict(attack=5, max_health=5), is_mine=False)]))
        red = state_from_dict(_state_dict(
            my_hero=_hero_dict("H", is_mine=True, health=4),
            oppo_minions=[dict(_minion_dict(attack=6, max_health=6), is_mine=False)]))
        self.assertEqual("green", self.module._threat_level(green))
        self.assertEqual("yellow", self.module._threat_level(yellow))
        self.assertEqual("red", self.module._threat_level(red))

    def test_taunt_value_rises_with_threat_level(self):
        minion = _entity(1, 1, 1, taunt=1)
        green = self.module._minion_value(minion, self.module.TAUNT_BONUS_GREEN)
        yellow = self.module._minion_value(minion, self.module.TAUNT_BONUS_YELLOW)
        red = self.module._minion_value(minion, self.module.TAUNT_BONUS_RED)
        self.assertLess(green, yellow)
        self.assertLess(yellow, red)

    def test_lethal_and_dead_short_circuit(self):
        lethal = state_from_dict(_state_dict(
            oppo_hero=_hero_dict("H", is_mine=False, health=0)))
        dead = state_from_dict(_state_dict(
            my_hero=_hero_dict("H", is_mine=True, health=0)))
        self.assertEqual(self.module.LETHAL_SCORE,
                         self.strategy.rankStaticState(lethal))
        self.assertEqual(self.module.DEAD_SCORE,
                         self.strategy.rankStaticState(dead))

    def test_low_enemy_health_adds_finisher_bonus(self):
        low = state_from_dict(_state_dict(
            oppo_hero=_hero_dict("H", is_mine=False, health=5)))
        high = state_from_dict(_state_dict(
            oppo_hero=_hero_dict("H", is_mine=False, health=20)))
        low_score = self.strategy.rankStaticState(low)
        high_score = self.strategy.rankStaticState(high)
        expected_gap = (self.module.LOW_ENEMY_HP_THRESHOLD - 5) \
            * self.module.LOW_ENEMY_HP_BONUS
        # 低血额外加分 + 英雄生命价值差（两次评分都含敌方生命项）。
        self.assertGreater(low_score, high_score)
        self.assertGreaterEqual(
            low_score - high_score,
            expected_gap - 1e-6)


class GenericRankActionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strategy = deck_strategy_module.deck_strategy("通用")

    def _attack_state(self, *, target_health=1, target_shield=0):
        attacker = _entity(1, 3, 3)
        target = _entity(1, 1, target_health, divine_shield=target_shield)
        return _State(my_minions=[attacker], oppo_minions=[target],
                      my_hero=_hero(30), oppo_hero=_hero(30))

    def test_overkill_is_penalized(self):
        state = self._attack_state(target_health=1)
        action = AiAction(
            ActionKind.ATTACK,
            source=AiSlot("board_slot", "friendly", 1),
            target=AiSlot("board_slot", "enemy", 1))
        self.assertLess(self.strategy.rankActions([state], [action]), 0.0)

    def test_exact_kill_has_no_penalty(self):
        state = self._attack_state(target_health=3)
        action = AiAction(
            ActionKind.ATTACK,
            source=AiSlot("board_slot", "friendly", 1),
            target=AiSlot("board_slot", "enemy", 1))
        self.assertEqual(0.0, self.strategy.rankActions([state], [action]))

    def test_face_attack_has_no_penalty(self):
        state = self._attack_state(target_health=1)
        action = AiAction(
            ActionKind.ATTACK,
            source=AiSlot("board_slot", "friendly", 1),
            target=AiSlot("hero", "enemy", None))
        self.assertEqual(0.0, self.strategy.rankActions([state], [action]))

    def test_divine_shield_target_has_no_penalty(self):
        state = self._attack_state(target_health=1, target_shield=1)
        action = AiAction(
            ActionKind.ATTACK,
            source=AiSlot("board_slot", "friendly", 1),
            target=AiSlot("board_slot", "enemy", 1))
        self.assertEqual(0.0, self.strategy.rankActions([state], [action]))


class GenericChooseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strategy = deck_strategy_module.deck_strategy("通用")

    def test_replaces_expensive_cards_keeps_cheapest_and_coin(self):
        hand = [
            SimpleNamespace(current_cost=1, is_coin=False),
            SimpleNamespace(current_cost=6, is_coin=False),
            SimpleNamespace(current_cost=7, is_coin=False),
            SimpleNamespace(current_cost=0, is_coin=True),
        ]
        state = SimpleNamespace(my_hand_cards=hand)
        self.assertEqual([1, 2], self.strategy.choose(state))

    def test_keeps_everything_when_all_cheap(self):
        state = SimpleNamespace(my_hand_cards=[
            SimpleNamespace(current_cost=1, is_coin=False),
            SimpleNamespace(current_cost=3, is_coin=False),
            SimpleNamespace(current_cost=4, is_coin=False),
        ])
        self.assertEqual([], self.strategy.choose(state))

    def test_never_replaces_the_only_card(self):
        state = SimpleNamespace(my_hand_cards=[
            SimpleNamespace(current_cost=9, is_coin=False),
        ])
        self.assertEqual([], self.strategy.choose(state))


class GenericSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.strategy = deck_strategy_module.deck_strategy("通用")

    def _search(self, data, mana):
        state = data if hasattr(data, "my_minions") else state_from_dict(data)
        with search.search.use_strategy(self.strategy):
            return search.search.Search().search(
                state, mana, time_limit=20.0)

    def test_empty_state_returns_end_turn(self):
        actions, score = self._search(_state_dict(mana={"total": 0, "used": 0, "temp": 0}), 0)
        self.assertTrue(actions)
        self.assertIs(ActionKind.END_TURN, actions[0].action)
        self.assertTrue(math.isfinite(score))

    def test_playable_minion_is_played_not_end_turn(self):
        data = _state_dict(my_hand_cards=[_minion_dict(
            entity_id="c1", zone="HAND", zone_pos=1, attack=2, max_health=2)])
        actions, score = self._search(data, 2)
        self.assertTrue(actions)
        self.assertIs(ActionKind.PLAY_CARD, actions[0].action)
        self.assertTrue(math.isfinite(score))

    def test_zoo_fixture_search_is_legal_and_does_not_mutate_state(self):
        _, state = load_fixture("zoo_turn4.json")
        before = (state.my_total_mana, state.my_used_mana,
                  [(m.attack, m.health) for m in state.my_minions],
                  [(c.card_id, c.current_cost) for c in state.my_hand_cards])
        actions, score = self._search(state, state.my_last_mana)
        self.assertTrue(actions)
        self.assertTrue(math.isfinite(score))
        self.assertTrue(all(isinstance(item.action, ActionKind) for item in actions))
        self.assertEqual(before, (
            state.my_total_mana, state.my_used_mana,
            [(m.attack, m.health) for m in state.my_minions],
            [(c.card_id, c.current_cost) for c in state.my_hand_cards]))

    def test_rank_state_accepts_duck_typed_strategy_state(self):
        from tests.support.strategy_fixtures import load_fixture
        _, state = load_fixture("zoo_turn4.json")
        score = self.strategy.rankState(state, [])
        self.assertTrue(math.isfinite(score))


if __name__ == "__main__":
    unittest.main()
