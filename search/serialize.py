"""动作序列化（联合模式与浮窗「推荐打法」用，不依赖标注台）。

Joint0.1 里这段逻辑在 `src/annotation/store.py`；标注台本期不移植，所以把
「动作 -> JSON dict / 动作序列重放补全」这两件事单独放在 search 包里，供
联合模式比较与浮窗展示复用。语义与标注台一致：

* `serialize_action`：AiAction / ProposedAction -> dict（盒子/本地同一口径）；
* `resolve_action_sequence`：在模拟局面上逐步重放，补全手牌动作的卡牌身份
  （序列里第 2 步以后手牌号位会左移，不能拿初始局面回查）；
* `describe_action`：一行中文描述（浮窗「推荐打法」）。
"""

from __future__ import annotations

from search.search import (
    Search,
    ai_action_from_dict,
    apply_action,
    joint_prefers_local,
)


_PLAN_ACTION_LABELS = {
    "play_card": "打出",
    "attack": "攻击",
    "use_hero_power": "使用英雄技能",
    "use_minion_ability": "使用随从技能",
    "choose_discover": "选择发现",
    "end_turn": "结束回合",
}


def _slot_close(left, right) -> bool:
    """两个位置是否兼容：只在两边都写明时才比较。"""
    if not isinstance(left, dict) or not isinstance(right, dict):
        return True
    kind_alias = {"hand_slot": "hand", "board": "board_slot",
                  "discover_slot": "discover"}
    left_kind = kind_alias.get(str(left.get("kind") or ""),
                               str(left.get("kind") or ""))
    right_kind = kind_alias.get(str(right.get("kind") or ""),
                                str(right.get("kind") or ""))
    if left_kind and right_kind and left_kind != right_kind:
        return False
    left_owner = str(left.get("owner") or "")
    right_owner = str(right.get("owner") or "")
    if left_owner and right_owner and left_owner != right_owner:
        return False
    left_index = left.get("index")
    right_index = right.get("index")
    if (left_index is not None and right_index is not None
            and str(left_index) != str(right_index)):
        return False
    return True


def _action_matches(left, right) -> bool:
    """两个动作是否同一打法：只在两边都写明的字段上比较。

    盒子面板常多写实体 id 或落位（「放置于我方1号位」），本地搜索往往不写；
    这类只在一边出现的字段不能算差异。destination 只表示落位，不参与比较。
    """
    if (str(left.get("action") or "").lower()
            != str(right.get("action") or "").lower()):
        return False
    for field in ("card_id", "entity_id"):
        one = str(left.get(field) or "")
        two = str(right.get(field) or "")
        if one and two and one != two:
            return False
    if not _slot_close(left.get("source"), right.get("source")):
        return False
    if not _slot_close(left.get("target"), right.get("target")):
        return False
    for field in ("choice_card_id", "choice_name"):
        one = str(left.get(field) or "")
        two = str(right.get(field) or "")
        if one and two and one != two:
            return False
    return True


def _is_play_action(item) -> bool:
    """「结束回合」不算可比较的动作（补齐搜索会在序列尾部补一条 end_turn）。"""
    return str(item.get("action") or "").lower() != "end_turn"


def joint_actions_consistent(local_actions, box_actions) -> bool:
    """两套打法是否一致：忽略动作顺序，忽略只在一侧写明的字段。"""
    local = [item for item in (local_actions or ())
             if isinstance(item, dict) and _is_play_action(item)]
    box = [item for item in (box_actions or ())
           if isinstance(item, dict) and _is_play_action(item)]
    if len(local) != len(box):
        return False
    remaining = list(box)
    for item in local:
        for index, other in enumerate(remaining):
            if _action_matches(item, other):
                remaining.pop(index)
                break
        else:
            return False
    return True


def joint_outcome_label(local_score, box_score) -> str:
    """联合口径归属：local（本地高 20% 以上）/ box / same。"""
    if joint_prefers_local(local_score, box_score):
        return "local"
    if joint_prefers_local(box_score, local_score):
        return "box"
    return "same"


def _plain(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _card_name(card_id):
    if not card_id:
        return ""
    try:
        from json_op import query_json_dict

        return query_json_dict(str(card_id)) or ""
    except Exception:
        return ""


def _slot_dict(slot):
    if slot is None:
        return None
    if isinstance(slot, dict):
        return {
            "kind": slot.get("kind"),
            "owner": slot.get("owner"),
            "index": slot.get("index"),
        }
    return {
        "kind": getattr(slot, "kind", None),
        "owner": getattr(slot, "owner", None),
        "index": _plain(getattr(slot, "index", None)),
    }


def _action_kind(action):
    kind = getattr(action, "action", None)
    return getattr(kind, "value", None) or str(kind or "")


def serialize_action(action, state=None):
    """AiAction / ProposedAction -> JSON dict（联合模式与测试共用）。"""
    if action is None:
        return None
    if isinstance(action, dict):
        return dict(action)
    card = getattr(action, "card", None)
    card_id = getattr(action, "card_id", None)
    if card_id is None and card is not None:
        card_id = getattr(card, "card_id", None)
    if card_id is None:
        # ProposedAction 通过 source 指向手牌时，从状态里补 card_id。
        source = getattr(action, "source", None)
        try:
            if state is not None and source is not None:
                if source.kind in ("hand", "hand_slot"):
                    card_id = state.my_hand_cards[source.index - 1].card_id
        except Exception:
            pass
    card_name = getattr(card, "name", "") if card is not None else ""
    if not card_name:
        card_name = _card_name(card_id)
    instruction = (getattr(action, "instruction", None)
                   or getattr(action, "normalized_instruction", None)
                   or getattr(action, "raw_instruction", None) or "")
    return {
        "action": _action_kind(action),
        "instruction": instruction,
        "card_id": _plain(card_id),
        "card_name": card_name,
        "card_type": _plain(getattr(action, "card_type", None)),
        "cost": _to_int(getattr(action, "cost", 0)),
        "choice_name": _plain(getattr(action, "choice_name", None)),
        "choice_card_id": _plain(getattr(action, "choice_card_id", None)),
        "entity_id": _plain(getattr(action, "entity_id", None)),
        "target_entity_id": _plain(getattr(action, "target_entity_id", None)),
        "source": _slot_dict(getattr(action, "source", None)),
        "target": _slot_dict(getattr(action, "target", None)),
        "destination": _slot_dict(getattr(action, "destination", None)),
    }


def resolve_action_sequence(state, actions, replay_from_state=True):
    """把一条动作序列按顺序重放，补全手牌动作的卡牌身份（见模块 docstring）。"""
    items = []
    replay = None
    if replay_from_state:
        try:
            replay = Search()._prepare_ai_state(state)
        except Exception:
            replay = None
    for raw in (actions or ()):
        item = (dict(raw) if isinstance(raw, dict)
                else serialize_action(raw, state))
        if not item:
            continue
        if replay is not None:
            data = dict(item)
            source = data.get("source") or {}
            if isinstance(source, dict) and source.get("kind") in ("hand",
                                                                   "hand_slot"):
                for key in ("card_id", "card_name", "entity_id"):
                    data.pop(key, None)
            try:
                action = ai_action_from_dict(replay, data)
                if action is not None:
                    item = serialize_action(action, replay) or item
                    replay = apply_action(replay, action)
            except Exception:
                replay = None
        items.append(item)
    return items


def describe_action(action, state=None):
    """一行中文描述（浮窗「推荐打法」用）。"""
    item = serialize_action(action, state) or {}
    text = str(item.get("instruction") or "").strip()
    if not text:
        action_kind = str(item.get("action") or "").lower()
        text = _PLAN_ACTION_LABELS.get(action_kind, action_kind or "动作")
        name = str(item.get("card_name") or "").strip()
        if name:
            text = f"{text} {name}"
    cost = _to_int(item.get("cost"), 0)
    return f"{text} · {cost}费".replace("\n", " ").strip()


__all__ = [
    "describe_action",
    "joint_actions_consistent",
    "joint_outcome_label",
    "resolve_action_sequence",
    "serialize_action",
]
