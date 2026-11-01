"""Legacy adapter: local deck-strategy planner built on ``search`` / ``ai``.

The application layer only knows ``StrategyPlannerPort``; this anti-corruption
module is the single place that imports the root-level search stack. It also
owns the joint-mode comparison (box first step vs local whole-turn line) so the
flow never touches search internals.
"""

from __future__ import annotations

from contextlib import nullcontext

import config
from ai.deck_strategy import deck_strategy
from search.search import (
    Search,
    action_identity_key,
    ai_action_from_dict,
    complete_sequence,
    joint_margin,
    joint_prefers_local,
    normalize_action_dict,
    rank_sequence_detailed,
    use_strategy,
)
from search.serialize import (
    describe_action,
    joint_actions_consistent,
    joint_outcome_label,
    resolve_action_sequence,
    serialize_action,
)


def _finite(value):
    """把得分收敛成可写进 JSON 的数字；NaN/inf/非数字返回 None。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _box_action_cost(state, data):
    """盒子动作的真实法力消耗（盒子解析出的 ProposedAction 没有 cost）。"""
    data = data or {}
    action = str(data.get("action") or "").lower()
    source = data.get("source") or {}
    kind = str(source.get("kind") or "")
    if action == "play_card" and kind in ("hand", "hand_slot"):
        try:
            index = int(source.get("index")) - 1
        except (TypeError, ValueError):
            return None
        cards = getattr(state, "my_hand_cards", ()) or ()
        if not 0 <= index < len(cards):
            return None
        try:
            return max(0, int(getattr(cards[index], "current_cost", 0) or 0))
        except (TypeError, ValueError):
            return None
    if action == "use_hero_power":
        power = getattr(state, "my_hero_power", None)
        try:
            return max(0, int(getattr(power, "current_cost", 0) or 0))
        except (TypeError, ValueError):
            return None
    return None


class LegacyStrategyPlanner:
    """Implements ``StrategyPlannerPort`` on top of the ported search stack.

    ``strategy_key`` 为空 = 沿用网页「策略选择」的全局策略（本地主模式）。
    非空 = 只在本规划器内部用指定策略（盒子失效兜底：全局策略可能是 hsbox，
    ``current_strategy()`` 会返回 None、搜索评分恒 0，所以必须显式 override）。
    """

    def __init__(self, time_limit=None, strategy_key=None):
        self._time_limit = time_limit
        self._strategy_key = strategy_key

    def _limit(self):
        if self._time_limit is not None:
            return float(self._time_limit)
        try:
            return float(config.search_time_limit_setting())
        except Exception:
            return 5.0

    def _strategy_context(self):
        """本规划器使用的策略上下文（显式 key 时覆盖 current_strategy）。"""
        if self._strategy_key is None:
            return nullcontext()
        strategy = deck_strategy(self._strategy_key)
        if strategy is None:
            raise RuntimeError(
                f"fallback_strategy_unavailable: {self._strategy_key}")
        return use_strategy(strategy)

    def plan(self, state, cost, stopped=None):
        with self._strategy_context():
            actions, score = Search().search(
                state, int(cost or 0), stopped=stopped,
                time_limit=self._limit())
        return list(actions or ()), _finite(score)

    def joint_plan(self, state, actions, score, box_action):
        """本地整回合 vs 盒子首步（补齐后续）的整回合比较。

        返回的 dict 里 `chosen` 是最终采用方；`diff`/`outcome` 供对局小结统计。
        """
        with self._strategy_context():
            return self._joint_plan(state, actions, score, box_action)

    def _joint_plan(self, state, actions, score, box_action):
        local_dicts = resolve_action_sequence(state, actions)
        local_score = _finite(score)
        try:
            rescored = _finite(rank_sequence_detailed(state, local_dicts)[0])
        except Exception:
            rescored = None
        if rescored is not None:
            local_score = rescored
        plan = {
            "local": {"actions": local_dicts, "score": local_score},
            "box": None,
            "winner": "local",
            "margin": None,
            "chosen": "local",
            "fallback": None,
            "diff": False,
            "outcome": "same",
        }
        if box_action is None:
            plan["fallback"] = "box_unavailable"
            return plan
        try:
            root = Search()._prepare_ai_state(state)
            data = normalize_action_dict(serialize_action(box_action, state))
            cost = _box_action_cost(state, data)
            if cost is not None:
                data["cost"] = cost
            base_action = ai_action_from_dict(root, data)
        except Exception:
            base_action = None
        if base_action is None:
            plan["fallback"] = "box_invalid"
            return plan
        # 盒子首步和本地首步是同一个动作时，本地整回合序列本身就是这条首步
        # 的合法延续，直接采用本地序列（避免补齐搜索把首步的动作分丢掉）。
        first_local = local_dicts[0] if local_dicts else None
        if (local_score is not None and isinstance(first_local, dict)
                and isinstance(data, dict)
                and _action_matches(first_local, data)):
            plan["box"] = {
                "instruction": (getattr(box_action, "normalized_instruction", "")
                                or ""),
                "actions": list(local_dicts),
                "score": local_score,
                "prefix_len": 1,
            }
            plan["winner"] = "box"
            plan["chosen"] = "box"
            plan["margin"] = 0.0
            plan["outcome"] = joint_outcome_label(local_score, local_score)
            return plan
        full, box_score = complete_sequence(root, [base_action])
        box_score = _finite(box_score)
        if not full or box_score is None or local_score is None:
            plan["fallback"] = "box_incomplete"
            return plan
        box_actions = resolve_action_sequence(state, full)
        plan["box"] = {
            "instruction": (getattr(box_action, "normalized_instruction", "")
                            or ""),
            "actions": box_actions,
            "score": box_score,
            "prefix_len": 1,
        }
        winner = ("local" if joint_prefers_local(local_score, box_score)
                  else "box")
        plan["winner"] = winner
        plan["chosen"] = winner
        plan["margin"] = _finite(joint_margin(local_score, box_score))
        plan["diff"] = not joint_actions_consistent(local_dicts, box_actions)
        plan["outcome"] = joint_outcome_label(local_score, box_score)
        return plan

    def describe(self, action):
        return describe_action(action)

    def identity_key(self, state, action):
        try:
            return action_identity_key(state, action)
        except Exception:
            return (
                getattr(getattr(action, "action", None), "value", ""),
                str(getattr(action, "entity_id", "") or ""),
                str(getattr(action, "target_entity_id", "") or ""),
                str(getattr(action, "choice_card_id", "") or ""),
            )


__all__ = [
    "LegacyStrategyPlanner",
    "joint_actions_consistent",
    "joint_outcome_label",
]
