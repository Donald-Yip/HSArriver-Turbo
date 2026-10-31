# -*- coding: utf-8 -*-
"""R18 本地搜索：黄金回放样例 + 三套卡组的空场面最小状态。

* 黄金样例 ``tests/fixtures/strategy/zoo_turn4.json``：动物园术 2 费回合，
  手牌 6 号位 TIME_025（暮光时空跃迁者）首步必须是 ``play_card``。
* 合成空状态：无手牌/无场面/无法力时，三套卡组都要给出合法的
  （至少一个 END_TURN）动作序列，且搜索不修改传入的真实战局。
"""

import math
import unittest

import ai.deck_strategy
import search.search
from hslegend.domain import ActionKind
from tests.support.strategy_fixtures import load_fixture, state_from_dict


EXPECTED_FOLDERS = ["任务牧", "动物园术", "强化学习动物园术"]
ZOO_FIXTURE = "zoo_turn4.json"


def _hero_dict(card_id, *, is_mine):
    return {
        "cardtype": "HERO",
        "entity_id": "1" if is_mine else "2",
        "card_id": card_id,
        "zone": "PLAY",
        "zone_pos": 0,
        "current_cost": 0,
        "overload": 0,
        "is_mine": is_mine,
        "max_health": 30,
        "damage": 0,
        "attack": 0,
        "armor": 0,
        "exhausted": 0,
        "frozen": 0,
        "cant_attack": 0,
    }


def empty_state_dict():
    """无手牌、无场面、0 法力、双方英雄都在场的合成状态。"""
    return {
        "is_end": False,
        "is_my_turn": True,
        "game_num_turns_in_play": 5,
        "log_revision": 1,
        "mana": {"total": 0, "used": 0, "temp": 0},
        "my_hero": _hero_dict("HERO_01", is_mine=True),
        "oppo_hero": _hero_dict("HERO_02", is_mine=False),
        "my_minions": [],
        "oppo_minions": [],
        "my_locations": [],
        "oppo_locations": [],
        "my_hand_cards": [],
    }


def _entity_snapshot(entity):
    if entity is None:
        return None
    return (
        getattr(entity, "entity_id", None),
        getattr(entity, "card_id", None),
        getattr(entity, "zone", None),
        getattr(entity, "zone_pos", None),
        getattr(entity, "attack", None),
        getattr(entity, "damage", None),
        getattr(entity, "max_health", None),
    )


def state_snapshot(state):
    """搜索前后对比用：法力、手牌、双方场面与英雄的可见字段。"""
    return (
        state.my_total_mana,
        state.my_used_mana,
        state.my_temp_mana,
        state.my_last_mana,
        tuple(_entity_snapshot(card) for card in state.my_hand_cards),
        tuple(_entity_snapshot(minion) for minion in state.my_minions),
        tuple(_entity_snapshot(minion) for minion in state.oppo_minions),
        tuple(_entity_snapshot(location) for location in state.my_locations),
        tuple(_entity_snapshot(location) for location in state.oppo_locations),
        _entity_snapshot(state.my_hero),
        _entity_snapshot(state.oppo_hero),
    )


def search_with(strategy, state):
    """在指定卡组策略下跑一次整回合搜索。"""
    with search.search.use_strategy(strategy):
        return search.search.Search().search(state, state.my_last_mana)


class ZooTurn4GoldenTests(unittest.TestCase):
    """Joint0.1 黄金样例：动物园术 turn4 首步打出手牌 6 号位。"""

    @classmethod
    def setUpClass(cls):
        cls.strategy = ai.deck_strategy.deck_strategy("动物园术")

    def test_golden_turn4_is_deterministic_and_does_not_mutate_state(self):
        _, state = load_fixture(ZOO_FIXTURE)
        before = state_snapshot(state)

        actions, score = search_with(self.strategy, state)

        self.assertTrue(actions)
        first = actions[0]
        self.assertIs(ActionKind.PLAY_CARD, first.action)
        self.assertIsNotNone(first.source)
        self.assertEqual("hand", first.source.kind)
        self.assertEqual(6, first.source.index)
        self.assertEqual("MINION", first.card_type)
        self.assertIn("暮光时空跃迁者", first.normalized_instruction)
        self.assertTrue(math.isfinite(score))
        # 搜索必须在模拟副本上推演，真实战局一点都不能被改（否则执行层会错位）。
        self.assertEqual(before, state_snapshot(state))

        _, replay_state = load_fixture(ZOO_FIXTURE)
        replay_actions, replay_score = search_with(self.strategy, replay_state)

        self.assertEqual((first.action, first.source, first.target),
                         (replay_actions[0].action, replay_actions[0].source,
                          replay_actions[0].target))
        self.assertAlmostEqual(score, replay_score, places=6)


class EmptyStateStrategyTests(unittest.TestCase):
    """三套卡组在无手牌/无场面/0 法力时都必须给出可执行序列。"""

    def test_every_deck_returns_legal_actions_on_empty_state(self):
        for key in EXPECTED_FOLDERS:
            with self.subTest(deck=key):
                strategy = ai.deck_strategy.deck_strategy(key)
                self.assertIsNotNone(strategy)

                state = state_from_dict(empty_state_dict())
                actions, score = search_with(strategy, state)

                self.assertTrue(actions)
                kinds = [action.action for action in actions]
                self.assertTrue(all(isinstance(kind, ActionKind)
                                    for kind in kinds))
                self.assertTrue(math.isfinite(score))
                # 无资源可打：最后一个动作是结束回合，或正好只有一个结束回合。
                self.assertTrue(
                    kinds[-1] is ActionKind.END_TURN
                    or kinds.count(ActionKind.END_TURN) == 1)
                # 搜索不碰真实战局。
                self.assertEqual((0, 0, 0),
                                 (state.my_total_mana, state.my_used_mana,
                                  state.my_temp_mana))
                self.assertEqual([], state.my_hand_cards)
                self.assertEqual([], state.my_minions)
                self.assertEqual([], state.oppo_minions)

        # 基础候选动作也必须包含结束回合（空状态的搜索兜底依赖它）。
        strategy = ai.deck_strategy.deck_strategy("动物园术")
        with search.search.use_strategy(strategy):
            candidates = search.search.candidate_actions(
                state_from_dict(empty_state_dict()))

        self.assertIsInstance(candidates, list)
        self.assertTrue(any(action.action is ActionKind.END_TURN
                            for action in candidates))


if __name__ == "__main__":
    unittest.main()
