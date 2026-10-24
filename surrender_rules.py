"""卡组识别投降：配置校验 + 对局事实追踪 + 规则求值。

需求原话："在某个回合识别到某张卡牌时投降，可以是『或』『非』『和』判断"，
并且"和或非只是用来判断某些卡组，因为某些卡组具有特征性卡牌，可以让用户
配置多个卡组"。所以这里是一个**卡组库**：每个卡组用特征卡表达式描述，
任一卡组命中就投降。

三条硬规则（都是用户明确要求的，改动前先看这里）：

1. **只判对手**。所有条件默认 `side="opponent"`，职业比的也是对方的 CLASS。
   我方的卡再多也绝不触发（见 `tests/test_surrender_direction.py`）。
2. **只认"出现"**。阿扎莉娜这类开局生效卡（`START_OF_GAME_KEYWORD`）
   永远不会被"打出"，只在开局于牌库中触发并 SHOW_ENTITY 揭示，
   所以必须同时追踪 played / revealed / start_of_game 三类事件。
3. **判定不受回合归属限制**。用户例子是"对方回合打出某卡"，若只在
   我方回合判定就永远抓不到；调用方要在 `is_my_turn` 之前调用 `check()`。

配置坏掉 = 不投降（fail-safe），绝不影响既有的「AI 胜率低自动投降」。
"""

import json
import threading
from dataclasses import dataclass, field
from typing import Iterable, Optional

import card_catalog

from log_op import (
    LOG_LINE_BLOCK_START_TRIGGER,
    LOG_LINE_CREATE_GAME,
    LOG_LINE_FULL_ENTITY,
    LOG_LINE_SHOW_ENTITY,
    LOG_LINE_TAG,
    LOG_LINE_TAG_CHANGE,
)

# 配置规模上限：防止手写/程序生成的配置把主循环拖死。
MAX_DECKS = 32
MAX_DEPTH = 8
MAX_LEAVES_PER_DECK = 64
# 单局事件上限：正常一局最多几十次"打出"，500 足够且能防脏日志刷爆内存。
MAX_EVENTS = 500

SIDE_OPPONENT = "opponent"
SIDE_SELF = "self"
VALID_SIDES = (SIDE_OPPONENT, SIDE_SELF)

# 叶子类型 → 是否需要卡牌
CARD_LEAF_TYPES = ("played", "revealed", "seen", "start_of_game",
                   "played_or_revealed", "in_zone")
VALID_LEAF_TYPES = CARD_LEAF_TYPES + ("turn",)
# 这些类型真的需要"事件"才算数（保存时用来提示"这规则开局就会触发"）。
EVENT_LEAF_TYPES = ("played", "revealed", "seen", "start_of_game",
                    "played_or_revealed")

VALID_ZONES = ("PLAY", "GRAVEYARD", "HAND", "DECK", "SETASIDE", "ANY")
VALID_ACTIONS = ("concede",)

DEFAULT_AUTO_SURRENDER = {"enabled": False, "aliases": {}, "decks": []}


# ---------------------------------------------------------------- 事实
@dataclass
class CardEvent:
    """一次"这张卡被我们看到了"的记录。

    `side` 记不住时留空字符串：SHOW_ENTITY 那行里还没有 CONTROLLER 标签，
    要等紧随其后的 tag 块；求值时再按 entity_id 回查一次（见 `resolve_side`）。
    """

    card_id: str
    entity_id: str
    side: str = ""
    round: int = 0
    my_turn: int = 0
    oppo_turn: int = 0
    game_turn: int = 0
    at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "card_id": self.card_id,
            "side": self.side,
            "round": self.round,
        }


@dataclass
class MatchFacts:
    """本局已经发生的事实（按 game_generation 自动作废）。"""

    generation: int = -1
    played: list = field(default_factory=list)
    revealed: dict = field(default_factory=dict)   # card_id -> CardEvent
    start_of_game: dict = field(default_factory=dict)  # card_id -> CardEvent
    dropped: int = 0

    def reset(self, generation: int = -1):
        self.generation = generation
        self.played = []
        self.revealed = {}
        self.start_of_game = {}
        self.dropped = 0

    def add_played(self, event: CardEvent):
        if len(self.played) >= MAX_EVENTS:
            self.dropped += 1
            return
        self.played.append(event)

    def add_revealed(self, event: CardEvent):
        # 同一张卡只记首次（"本局出现过"是单调事实）。
        self.revealed.setdefault(event.card_id, event)

    def add_start_of_game(self, event: CardEvent):
        self.start_of_game.setdefault(event.card_id, event)

    def played_card_ids(self, side: Optional[str] = None) -> set:
        return {event.card_id for event in self.played
                if side is None or event.side == side}

    def snapshot(self) -> dict:
        return {
            "generation": self.generation,
            "played": [event.to_dict() for event in self.played[-20:]],
            "revealed": [event.to_dict()
                         for event in list(self.revealed.values())[-20:]],
            "start_of_game": [event.to_dict()
                              for event in self.start_of_game.values()],
            "dropped": self.dropped,
        }


# ---------------------------------------------------------------- 编译产物
@dataclass(frozen=True)
class Leaf:
    type: str
    side: str = SIDE_OPPONENT
    card_ids: frozenset = frozenset()
    card_names: tuple = ()
    zone: str = "ANY"
    card_type: str = ""
    within: Optional[tuple] = None      # (min, max)，按 round 比较
    at_least: int = 0
    at_most: int = 0
    turn_field: str = "round"
    turn_min: Optional[int] = None
    turn_max: Optional[int] = None

    def label(self) -> str:
        """给人看的一句话（日志/网页进度用）。"""
        side_text = "对方" if self.side == SIDE_OPPONENT else "我方"
        names = "、".join(self.card_names) or "（未填卡牌）"
        if self.type == "turn":
            field_zh = {"round": "回合", "my_turn": "我方回合",
                        "oppo_turn": "对方回合",
                        "game_turn": "游戏总回合"}.get(self.turn_field,
                                                      self.turn_field)
            low = "" if self.turn_min is None else str(self.turn_min)
            high = "" if self.turn_max is None else str(self.turn_max)
            return f"{field_zh} {low}–{high}"
        if self.type == "played":
            text = f"{side_text}打出过 {names}"
        elif self.type in ("revealed", "seen"):
            text = f"{side_text}出现过 {names}"
        elif self.type == "start_of_game":
            text = f"{side_text}开局生效卡 {names}"
        elif self.type == "played_or_revealed":
            text = f"{side_text}打出或出现过 {names}"
        elif self.type == "in_zone":
            zone_zh = {"PLAY": "场上", "GRAVEYARD": "墓地", "HAND": "手上",
                       "DECK": "牌库", "SETASIDE": "备用区",
                       "ANY": "任何区域"}.get(self.zone, self.zone)
            text = f"{side_text}{zone_zh}有 {names}"
        else:
            text = f"{side_text} {names}"
        if self.at_least > 0:
            text += f"（至少 {self.at_least} 张）"
        if self.within:
            low, high = self.within
            bounds = []
            if low is not None:
                bounds.append(f"≥{low}")
            if high is not None:
                bounds.append(f"≤{high}")
            if bounds:
                text += f"（回合 {' 且 '.join(bounds)}）"
        return text


@dataclass(frozen=True)
class CompiledDeck:
    id: str
    name: str
    hero_class: str
    action: str
    expr: tuple


# ---------------------------------------------------------------- 校验/规范化
def _error(errors, path, message):
    errors.append({"path": path, "message": message})


def _as_int(value, default=None):
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_side(raw, path, errors) -> str:
    if raw is None or raw == "":
        return SIDE_OPPONENT
    text = str(raw).strip().lower()
    if text in ("opponent", "enemy", "对方", "对手"):
        return SIDE_OPPONENT
    if text in ("self", "me", "mine", "我方", "自己"):
        return SIDE_SELF
    _error(errors, path, f"side 只能是 opponent / self，收到 {raw!r}")
    return SIDE_OPPONENT


def _normalize_within(raw, path, errors) -> Optional[tuple]:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        _error(errors, path, "within 必须是一个对象，例如 {\"max\": 4}")
        return None
    field_name = str(raw.get("field") or "round").strip()
    if field_name not in ("round", "my_turn", "oppo_turn", "game_turn"):
        _error(errors, path + ".field", f"不支持的回合字段 {field_name!r}")
        return None
    low = _as_int(raw.get("min"), None)
    high = _as_int(raw.get("max"), None)
    if raw.get("min") is not None and low is None:
        _error(errors, path + ".min", "min 必须是整数")
    if raw.get("max") is not None and high is None:
        _error(errors, path + ".max", "max 必须是整数")
    if low is not None and high is not None and low > high:
        _error(errors, path, f"回合范围写反了：min={low} > max={high}")
    if field_name != "round":
        # within 只按 round 比较（用户口语的"前四回合"就是 round），
        # 其它字段留给 turn 叶子用，避免两套语义打架。
        _error(errors, path + ".field",
               "within.field 目前只支持 round；要按我方/对方回合请用 turn 叶子")
    return (low, high)


def _normalize_cards(raw, path, errors) -> list:
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        _error(errors, path, "card 必须是卡牌名或卡牌名数组")
        return []
    result = []
    for index, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            _error(errors, f"{path}[{index}]", "卡牌名必须是非空字符串")
            continue
        result.append(item.strip())
    if not result:
        _error(errors, path, "至少要写一张卡")
    return result


def _normalize_node(raw, path, errors, depth, counter):
    """把一个逻辑节点规范化；返回 (node, leaf_count)。"""
    if depth > MAX_DEPTH:
        _error(errors, path, f"嵌套太深了（上限 {MAX_DEPTH} 层）")
        return None, 0
    if not isinstance(raw, dict):
        _error(errors, path, "条件必须是对象，例如 {\"all\": [...]}")
        return None, 0

    # 逻辑节点：all / any / not（也接受 and / or 写法并规范化）
    lowered = {}
    for key, value in raw.items():
        alias = {"and": "all", "or": "any"}.get(key, key)
        lowered[alias] = value

    logic_keys = [key for key in ("all", "any", "not") if key in lowered]
    if len(logic_keys) > 1:
        _error(errors, path, "一个对象里只能有一个逻辑键（all / any / not）")
        return None, 0

    if logic_keys:
        key = logic_keys[0]
        value = lowered[key]
        if key == "not":
            node, count = _normalize_node(value, path + ".not", errors,
                                          depth + 1, counter)
            return ({"not": node}, count) if node is not None else (None, 0)
        if not isinstance(value, (list, tuple)) or not value:
            _error(errors, path + "." + key, f"{key} 需要非空数组")
            return None, 0
        children = []
        total = 0
        for index, child in enumerate(value):
            node, count = _normalize_node(
                child, f"{path}.{key}[{index}]", errors, depth + 1, counter)
            if node is not None:
                children.append(node)
                total += count
        if not children:
            return None, 0
        return {key: children}, total

    # 叶子
    leaf_type = str(lowered.get("type") or "").strip()
    if leaf_type not in VALID_LEAF_TYPES:
        _error(errors, path + ".type",
               f"未知条件类型 {leaf_type!r}；可用：{'/'.join(VALID_LEAF_TYPES)}")
        return None, 0

    counter["leaves"] += 1
    if counter["leaves"] > MAX_LEAVES_PER_DECK:
        _error(errors, path,
               f"单个卡组的条件太多（上限 {MAX_LEAVES_PER_DECK} 条）")
        return None, 0

    node = {"type": leaf_type}

    if leaf_type == "turn":
        field_name = str(lowered.get("field") or "round").strip()
        if field_name not in ("round", "my_turn", "oppo_turn", "game_turn"):
            _error(errors, path + ".field", f"不支持的回合字段 {field_name!r}")
        low = _as_int(lowered.get("min"), None)
        high = _as_int(lowered.get("max"), None)
        if low is None and high is None:
            _error(errors, path, "turn 条件至少要写 min 或 max")
        if low is not None and high is not None and low > high:
            _error(errors, path, f"回合范围写反了：min={low} > max={high}")
        # turn 判的是"当前回合数"，卡牌/区域/计数这些字段对它没有意义。
        # 以前这里直接 return，让下面那段 at_least / 未知字段校验变成了死代码，
        # 写错了也悄悄生效；现在显式拒绝（side 仍允许，兼容历史配置）。
        wasted = sorted(set(lowered) - {"type", "field", "min", "max", "side"})
        if wasted:
            _error(errors, path,
                   f"turn 条件不支持这些字段：{'、'.join(wasted)}")
        node.update({"field": field_name, "min": low, "max": high})
        return node, 1

    node["side"] = _normalize_side(lowered.get("side"), path + ".side", errors)
    node["card"] = _normalize_cards(lowered.get("card"), path + ".card", errors)
    node["card_type"] = str(lowered.get("card_type") or "").strip().upper()

    if leaf_type in ("in_zone", "played_or_revealed", "revealed", "seen",
                     "start_of_game", "played"):
        zone = str(lowered.get("zone") or "").strip().upper()
        if leaf_type == "in_zone":
            zone = zone or "PLAY"
            if zone not in VALID_ZONES:
                _error(errors, path + ".zone",
                       f"zone 只能是 {'/'.join(VALID_ZONES)}")
        elif zone:
            _error(errors, path + ".zone",
                   f"zone 只对 in_zone 有效；{leaf_type} 不需要写 zone")
        if leaf_type == "in_zone":
            node["zone"] = zone

    within = _normalize_within(lowered.get("within"), path + ".within", errors)
    if within is not None:
        if leaf_type not in ("played", "revealed", "seen",
                             "played_or_revealed", "start_of_game"):
            _error(errors, path + ".within",
                   f"{leaf_type} 不支持 within（只有事件类条件才有回合窗口）")
        else:
            # 只写用户真填了的边界，别把 "min": null 塞进 ui_config.json
            entry = {}
            if within[0] is not None:
                entry["min"] = within[0]
            if within[1] is not None:
                entry["max"] = within[1]
            if entry:
                node["within"] = entry

    at_least = _as_int(lowered.get("at_least"), 0) or 0
    at_most = _as_int(lowered.get("at_most"), 0) or 0
    if at_least < 0 or at_most < 0:
        _error(errors, path, "at_least / at_most 不能是负数")
    if at_least and at_most and at_least > at_most:
        _error(errors, path, f"at_least({at_least}) 不能大于 at_most({at_most})")
    if (at_least or at_most) and leaf_type == "turn":
        _error(errors, path, "turn 条件不支持 at_least / at_most")
    if at_least:
        node["at_least"] = at_least
    if at_most:
        node["at_most"] = at_most

    unknown = set(lowered) - {"type", "side", "card", "card_type", "zone",
                              "within", "at_least", "at_most", "field",
                              "min", "max"}
    if unknown:
        _error(errors, path, f"不认识的字段：{'、'.join(sorted(unknown))}")
    return node, 1


def normalize_config(raw, catalog=None) -> dict:
    """把用户配置校验/规范化。

    返回 ``{"ok", "config", "errors", "warnings", "corrected"}``：
      * ``config`` 是**规范化后**的配置（卡名已纠正、字段已补默认值），
        网页拿它回填，用户就能看到"名字被改成了什么"；
      * ``errors`` 每项带 ``path``（如 ``decks[0].when.all[1].card``），
        网页据此把输入框标红；
      * 有 error 时 ``ok=False``，调用方**不要**写盘（保持上一份好配置）。
    """
    errors = []
    warnings = []
    corrected = []

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return {"ok": False, "config": dict(DEFAULT_AUTO_SURRENDER),
                "errors": [{"path": "", "message": "auto_surrender 必须是一个对象"}],
                "warnings": [], "corrected": []}

    result = {"enabled": bool(raw.get("enabled", False))}

    aliases = raw.get("aliases") or {}
    if not isinstance(aliases, dict):
        _error(errors, "aliases", "aliases 必须是「别名 → 正式卡名」的对象")
        aliases = {}
    clean_aliases = {}
    for key, value in aliases.items():
        if not isinstance(key, str) or not isinstance(value, str):
            _error(errors, "aliases", "aliases 的键和值都必须是字符串")
            continue
        clean_aliases[key.strip()] = value.strip()
    result["aliases"] = clean_aliases

    decks = raw.get("decks")
    if decks is None:
        decks = []
    if not isinstance(decks, (list, tuple)):
        _error(errors, "decks", "decks 必须是数组（每项一套卡组）")
        decks = []
    if len(decks) > MAX_DECKS:
        _error(errors, "decks", f"最多配置 {MAX_DECKS} 套卡组")
        decks = list(decks)[:MAX_DECKS]

    seen_ids = set()
    clean_decks = []
    for index, deck in enumerate(decks):
        path = f"decks[{index}]"
        if not isinstance(deck, dict):
            _error(errors, path, "每套卡组必须是一个对象")
            continue
        deck_id = str(deck.get("id") or f"deck{index + 1}").strip()
        if deck_id in seen_ids:
            _error(errors, path + ".id", f"卡组 id 重复：{deck_id}")
        seen_ids.add(deck_id)
        name = str(deck.get("name") or deck_id).strip()

        hero_class = ""
        raw_class = deck.get("class")
        if raw_class not in (None, ""):
            parsed = card_catalog.parse_class(raw_class)
            if parsed is None:
                _error(errors, path + ".class",
                       f"不认识的职业 {raw_class!r}（可写「牧师」或 PRIEST）")
            else:
                hero_class = parsed

        action = str(deck.get("action") or "concede").strip().lower()
        if action not in VALID_ACTIONS:
            _error(errors, path + ".action",
                   f"不支持的 action {action!r}（目前只有 concede 投降）")

        when = deck.get("when")
        counter = {"leaves": 0}
        node, leaves = _normalize_node(when, path + ".when", errors, 0, counter)
        if node is None:
            continue
        if leaves and not _has_event_leaf(decks[index].get("when")):
            warnings.append(
                f"卡组「{name}」没有任何『打出/出现』类条件，"
                f"开局就可能立刻成立并投降，请确认这是你要的。")

        clean_decks.append({
            "id": deck_id,
            "name": name,
            "enabled": bool(deck.get("enabled", True)),
            "class": hero_class,
            "action": action,
            "when": node,
        })

    result["decks"] = clean_decks
    if not clean_decks:
        warnings.append("还没有任何卡组规则，即使打开开关也不会触发。")

    return {
        "ok": not errors,
        "config": result,
        "errors": errors,
        "warnings": warnings,
        "corrected": corrected,
    }


def _has_event_leaf(raw) -> bool:
    """递归看有没有事件类叶子（played/revealed/... 或 turn 之外的计数）。"""
    if not isinstance(raw, dict):
        return False
    leaf_type = str(raw.get("type") or "")
    if leaf_type in EVENT_LEAF_TYPES:
        return True
    for key in ("all", "any", "and", "or"):
        children = raw.get(key)
        if isinstance(children, (list, tuple)):
            if any(_has_event_leaf(child) for child in children):
                return True
    if "not" in raw:
        return _has_event_leaf(raw.get("not"))
    # 没有 type 的节点也可能是 {"any": [...]} 之类，上面已经覆盖。
    return False


# ---------------------------------------------------------------- 编译
def _compile_node(raw, path, errors, catalog, aliases, corrected):
    if "all" in raw or "any" in raw or "not" in raw:
        for key in ("all", "any"):
            if key in raw:
                children = []
                for index, child in enumerate(raw[key]):
                    compiled = _compile_node(
                        child, f"{path}.{key}[{index}]", errors, catalog,
                        aliases, corrected)
                    if compiled is not None:
                        children.append(compiled)
                return (key, tuple(children))
        compiled = _compile_node(raw["not"], path + ".not", errors,
                                 catalog, aliases, corrected)
        return ("not", compiled) if compiled is not None else None

    leaf_type = raw.get("type")
    if leaf_type == "turn":
        return ("leaf", Leaf(type="turn",
                             turn_field=raw.get("field") or "round",
                             turn_min=raw.get("min"),
                             turn_max=raw.get("max")))

    card_ids = set()
    card_names = []
    for index, name in enumerate(raw.get("card") or []):
        lookup = catalog.lookup(name, aliases=aliases,
                                card_type=raw.get("card_type") or None)
        if not lookup.ok:
            detail = lookup.error or f"找不到卡牌「{name}」"
            if lookup.suggestions:
                # 把候选直接写进原因里：网页/CLI 都能照着改，不用再点一层
                detail += "；候选：" + "、".join(lookup.suggestions[:3])
            _error(errors, f"{path}.card[{index}]", detail)
            continue
        if lookup.corrected_from and lookup.corrected_from != lookup.name:
            corrected.append({"from": lookup.corrected_from,
                              "to": lookup.name,
                              "path": f"{path}.card[{index}]"})
        card_names.append(lookup.name)
        card_ids.update(lookup.card_ids)
    if not card_ids and leaf_type in CARD_LEAF_TYPES:
        return None

    within = raw.get("within") or {}
    within_tuple = None
    if within:
        within_tuple = (within.get("min"), within.get("max"))

    return ("leaf", Leaf(
        type=leaf_type,
        side=raw.get("side") or SIDE_OPPONENT,
        card_ids=frozenset(card_ids),
        card_names=tuple(card_names),
        zone=raw.get("zone") or "ANY",
        card_type=raw.get("card_type") or "",
        within=within_tuple,
        at_least=int(raw.get("at_least") or 0),
        at_most=int(raw.get("at_most") or 0),
    ))


def compile_config(config, catalog=None):
    """规范化后的配置 → 可求值的规则树。

    返回 ``(decks, errors, corrected)``；``corrected`` 记录"卡名被自动纠正"
    的条目，网页据此提示用户「你写的 X 已改成 Y」。
    """
    catalog = catalog or card_catalog.default_catalog()
    if catalog is None:
        return [], [{"path": "",
                     "message": f"卡牌库不可用：{card_catalog.catalog_error()}"}], []
    aliases = (config or {}).get("aliases") or {}
    errors = []
    corrected = []
    decks = []
    for index, deck in enumerate((config or {}).get("decks") or []):
        path = f"decks[{index}].when"
        expr = _compile_node(deck.get("when") or {}, path, errors,
                             catalog, aliases, corrected)
        if expr is None:
            continue
        decks.append(CompiledDeck(
            id=deck.get("id") or f"deck{index + 1}",
            name=deck.get("name") or "",
            hero_class=deck.get("class") or "",
            action=deck.get("action") or "concede",
            expr=expr,
        ))
    return decks, errors, corrected


def iter_leaves(expr):
    """深度优先遍历所有叶子（日志里逐项列证据用）。"""
    if expr is None:
        return
    kind = expr[0]
    if kind == "leaf":
        yield expr[1]
        return
    if kind == "not":
        yield from iter_leaves(expr[1])
        return
    for child in expr[1]:
        yield from iter_leaves(child)


def iter_leaves_with_polarity(expr, negated: bool = False):
    """同 ``iter_leaves``，但同时给出"这叶子是否被「非」包着"。

    原来列证据时对每个叶子直接 ``eval_leaf``，一旦条件里有 ``not``，
    证据的 ✓/✗ 就跟真实判定相反了。
    """
    if expr is None:
        return
    kind = expr[0]
    if kind == "leaf":
        yield expr[1], negated
        return
    if kind == "not":
        yield from iter_leaves_with_polarity(expr[1], not negated)
        return
    for child in expr[1]:
        yield from iter_leaves_with_polarity(child, negated)


def describe_expr(expr) -> str:
    """把编译后的条件树写回一句中文（CLI/日志展示用）。

    例：``(对方开局生效卡 裂魂者阿扎莉娜 且 对方打出过 神秘访客（回合 ≤4）)``
    """
    if expr is None:
        return "（无条件）"
    kind = expr[0]
    if kind == "leaf":
        return expr[1].label()
    if kind == "not":
        return "非 " + describe_expr(expr[1])
    joiner = " 且 " if kind == "all" else " 或 "
    children = list(expr[1])
    if not children:
        return "（无条件）"
    text = joiner.join(describe_expr(child) for child in children)
    return text if len(children) == 1 else f"({text})"


# ---------------------------------------------------------------- 求值上下文
class EvalContext:
    """一次求值所需的全部事实（尽量少查 log_state）。"""

    def __init__(self, facts: MatchFacts, opponent_class="", my_turn=0,
                 oppo_turn=0, round_turn=0, log_state=None):
        self.facts = facts
        self.opponent_class = opponent_class or ""
        self.my_turn = my_turn
        self.oppo_turn = oppo_turn
        self.round = round_turn
        self.game_turn = max(my_turn + oppo_turn, round_turn)
        self.log_state = log_state
        self._zone_cache = {}

    def zone_of(self, card_id, side=None) -> set:
        """某张卡当前在哪些区域（只用于 in_zone）。"""
        if self.log_state is None:
            return set()
        cache_key = (card_id, side)
        zones = self._zone_cache.get(cache_key)
        if zones is None:
            zones = set()
            for entity in self.log_state.entity_dict.values():
                if getattr(entity, "card_id", "") != card_id:
                    continue
                if side and self._entity_side(entity) != side:
                    continue
                zones.add(str(entity.query_tag("ZONE", "")))
            self._zone_cache[cache_key] = zones
        return zones

    def _entity_side(self, entity) -> str:
        return _side_from_controller(
            entity.query_tag("CONTROLLER", ""),
            getattr(self.log_state, "my_player_id", ""))


def _side_from_controller(controller, my_player_id) -> str:
    """CONTROLLER 比对己方 PlayerID。判不出来返回空串（条件按不成立处理）。"""
    controller = str(controller or "").strip()
    my_player_id = str(my_player_id or "").strip()
    if not controller or my_player_id in ("", "0"):
        # 还没确定"我是几号玩家"时，任何归属判断都不可信 —— 宁可判未知。
        return ""
    return SIDE_OPPONENT if controller != my_player_id else SIDE_SELF


def resolve_side(log_state, event: CardEvent) -> str:
    if event.side in VALID_SIDES:
        return event.side
    if log_state is None:
        return ""
    entity = log_state.entity_dict.get(str(event.entity_id))
    if entity is None:
        return ""
    return _side_from_controller(
        entity.query_tag("CONTROLLER", ""),
        getattr(log_state, "my_player_id", ""))


def _within_ok(within, round_turn) -> bool:
    if not within:
        return True
    low, high = within
    if low is not None and round_turn < low:
        return False
    if high is not None and round_turn > high:
        return False
    return True


def _count_matching_events(events: Iterable[CardEvent], card_ids, side,
                           within, log_state) -> int:
    """统计"这些卡里有几张命中过"（同一张卡多次只算一张）。"""
    matched = set()
    for event in events:
        if event.card_id not in card_ids:
            continue
        event_side = resolve_side(log_state, event)
        if event_side != side:
            continue
        if not _within_ok(within, event.round):
            continue
        matched.add(event.card_id)
    return len(matched)


def eval_leaf(leaf: Leaf, ctx: EvalContext) -> bool:
    facts = ctx.facts
    log_state = ctx.log_state

    if leaf.type == "turn":
        value = {
            "round": ctx.round,
            "my_turn": ctx.my_turn,
            "oppo_turn": ctx.oppo_turn,
            "game_turn": ctx.game_turn,
        }.get(leaf.turn_field, ctx.round)
        if leaf.turn_min is not None and value < leaf.turn_min:
            return False
        if leaf.turn_max is not None and value > leaf.turn_max:
            return False
        return True

    side = leaf.side

    if leaf.type == "played":
        count = _count_matching_events(facts.played, leaf.card_ids, side,
                                       leaf.within, log_state)
    elif leaf.type == "revealed" or leaf.type == "seen":
        count = _count_matching_events(facts.revealed.values(), leaf.card_ids,
                                       side, leaf.within, log_state)
    elif leaf.type == "start_of_game":
        count = _count_matching_events(facts.start_of_game.values(),
                                       leaf.card_ids, side, leaf.within,
                                       log_state)
    elif leaf.type == "played_or_revealed":
        events = list(facts.played) + list(facts.revealed.values())
        count = _count_matching_events(events, leaf.card_ids, side,
                                       leaf.within, log_state)
    elif leaf.type == "in_zone":
        count = 0
        for card_id in leaf.card_ids:
            zones = ctx.zone_of(card_id, side)
            if leaf.zone == "ANY":
                if zones:
                    count += 1
            elif leaf.zone in zones:
                count += 1
    else:
        return False

    # at_least 与 at_most 可以同时写（"2~3 张"）：以前 at_least 先 return，
    # 导致 at_most 永远走不到，范围上限被忽略。
    if count < (leaf.at_least or 1):
        return False
    if leaf.at_most and count > leaf.at_most:
        return False
    return True


def eval_node(expr, ctx: EvalContext) -> bool:
    if expr is None:
        return False
    kind = expr[0]
    if kind == "leaf":
        return eval_leaf(expr[1], ctx)
    if kind == "not":
        return not eval_node(expr[1], ctx)
    if kind == "all":
        return all(eval_node(child, ctx) for child in expr[1])
    if kind == "any":
        return any(eval_node(child, ctx) for child in expr[1])
    return False


def eval_tree(expr, ctx: EvalContext) -> dict:
    """像 ``eval_node`` 一样求值，但**保留结构**（网页状态栏显示每组的真假）。

    返回 ``{"op": "leaf|not|all|any", "ok": bool, ...}``：叶子带 ``text``，
    组带 ``children``。语义与 ``eval_node`` 完全一致，只是不做短路拍平。
    """
    if expr is None:
        return {"op": "leaf", "ok": False, "text": "（无条件）"}
    kind = expr[0]
    if kind == "leaf":
        leaf = expr[1]
        try:
            ok = bool(eval_leaf(leaf, ctx))
        except Exception:
            ok = False
        return {"op": "leaf", "ok": ok, "text": leaf.label()}
    if kind == "not":
        child = eval_tree(expr[1], ctx)
        return {"op": "not", "ok": not child["ok"], "children": [child]}
    children = [eval_tree(child, ctx) for child in expr[1]]
    if kind == "all":
        ok = bool(children) and all(item["ok"] for item in children)
    else:
        ok = any(item["ok"] for item in children)
    return {"op": kind, "ok": ok, "children": children}


def evaluate_decks(decks, ctx: EvalContext):
    """任一卡组命中就返回它（数组顺序短路）；都没命中返回 None。"""
    for deck in decks:
        if deck.hero_class and ctx.opponent_class != deck.hero_class:
            continue
        if eval_node(deck.expr, ctx):
            return deck
    return None


# ---------------------------------------------------------------- 引擎
class SurrenderRuleEngine:
    """把"日志行"变成"事实"，再拿配置去求值。

    线程安全：自动化线程写事实、网页/浮层读状态，都用同一把锁。
    """

    def __init__(self):
        self._lock = threading.RLock()
        self.facts = MatchFacts()
        self._pending_start_of_game = set()
        self._triggered = False
        self._hit = None
        self._compiled = None
        self._compiled_key = None
        self._compiled_errors = []

    # ------------------------------------------------------------ 事实采集
    def reset(self):
        with self._lock:
            self.facts.reset()
            self._pending_start_of_game.clear()
            self._triggered = False
            self._hit = None

    def observe(self, log_state, container, prev_zone=None):
        """吃一行日志。任何异常都不能影响主循环，所以内部全包了 try。"""
        try:
            self._observe(log_state, container, prev_zone)
        except Exception:
            pass

    def _observe(self, log_state, container, prev_zone=None):
        line_type = getattr(container, "line_type", None)
        info = getattr(container, "info_dict", {}) or {}
        generation = getattr(log_state, "game_generation", 0)

        with self._lock:
            if line_type == LOG_LINE_CREATE_GAME:
                self.facts.reset(generation)
                self._pending_start_of_game.clear()
                self._triggered = False
                self._hit = None
                return

            if self.facts.generation != generation:
                # 新的一局（哪怕没看到 CREATE_GAME，例如读到的是半截日志）。
                self.facts.reset(generation)
                self._pending_start_of_game.clear()
                self._triggered = False
                self._hit = None

            if line_type == LOG_LINE_BLOCK_START_TRIGGER:
                entity_id = str(info.get("entity_id") or "")
                if entity_id:
                    self._pending_start_of_game.add(entity_id)
                return

            if line_type in (LOG_LINE_SHOW_ENTITY, LOG_LINE_FULL_ENTITY):
                self._record_revealed(log_state, info)
                return

            if line_type == LOG_LINE_TAG_CHANGE:
                if str(info.get("tag") or "") != "ZONE":
                    return
                if str(info.get("value") or "") != "PLAY":
                    return
                if prev_zone != "HAND":
                    # 召唤/衍生/牌库直接上场都不算"打出"。
                    return
                self._record_played(log_state, str(info.get("entity") or ""))
                return

            if line_type == LOG_LINE_TAG:
                # 对手的牌没有 TAG_CHANGE ZONE=PLAY 行：日志先 SHOW_ENTITY
                # 揭示 CardID，再在同一个 tag 块里写 tag=ZONE value=PLAY，
                # 实体由 update_state 的 current_update_id 指向。不在这里补记，
                # 所有"对方打出过 X"的规则永远不会命中（实测：对手打出冰霜
                # 新星，facts.played 里一条对手事件都没有）。
                if str(info.get("tag") or "") != "ZONE":
                    return
                if str(info.get("value") or "") != "PLAY":
                    return
                if prev_zone != "HAND":
                    return
                entity_id = str(getattr(log_state, "current_update_id", "") or "")
                if entity_id:
                    self._record_played(log_state, entity_id)
                return

    def _entity_card_id(self, log_state, entity_id) -> str:
        entity = log_state.entity_dict.get(str(entity_id))
        if entity is None:
            return ""
        return str(getattr(entity, "card_id", "") or "")

    def _turns(self, log_state):
        my_turn = oppo_turn = 0
        try:
            my_turn = int(log_state.my_entity.query_tag("TURN", "0") or 0)
        except Exception:
            my_turn = 0
        try:
            oppo_turn = int(log_state.oppo_entity.query_tag("TURN", "0") or 0)
        except Exception:
            oppo_turn = 0
        return my_turn, oppo_turn

    def _event(self, log_state, entity_id, card_id) -> CardEvent:
        my_turn, oppo_turn = self._turns(log_state)
        side = ""
        entity = log_state.entity_dict.get(str(entity_id))
        if entity is not None:
            side = _side_from_controller(
                entity.query_tag("CONTROLLER", ""),
                getattr(log_state, "my_player_id", ""))
        return CardEvent(
            card_id=card_id,
            entity_id=str(entity_id),
            side=side,
            round=max(my_turn, oppo_turn),
            my_turn=my_turn,
            oppo_turn=oppo_turn,
            game_turn=my_turn + oppo_turn,
            at=0.0,
        )

    def _record_revealed(self, log_state, info):
        entity_id = str(info.get("entity") or "")
        card_id = str(info.get("card") or "").strip()
        if not card_id:
            card_id = self._entity_card_id(log_state, entity_id)
        if not card_id:
            return
        event = self._event(log_state, entity_id, card_id)
        self.facts.add_revealed(event)
        if entity_id in self._pending_start_of_game:
            self.facts.add_start_of_game(event)
            self._pending_start_of_game.discard(entity_id)

    def _record_played(self, log_state, entity_id):
        card_id = self._entity_card_id(log_state, entity_id)
        if not card_id:
            return
        event = self._event(log_state, entity_id, card_id)
        if event.side not in VALID_SIDES:
            # 归属未知时先记下，求值时按 entity_id 再解析一次。
            event.side = ""
        self.facts.add_played(event)

    # ------------------------------------------------------------ 配置
    def configure(self, raw_config):
        """传入最新的 auto_surrender 配置；内部按内容签名缓存编译结果。"""
        signature = json.dumps(raw_config or {}, sort_keys=True,
                               ensure_ascii=False)
        if signature == self._compiled_key:
            return self._compiled
        normalized = normalize_config(raw_config)
        self._compiled_errors = list(normalized["errors"])
        if not normalized["ok"]:
            # 配置坏了 = 不投降（fail-safe），不要拿半份规则去点认输。
            self._compiled = []
            self._compiled_key = signature
            return self._compiled
        decks, compile_errors, _corrected = compile_config(normalized["config"])
        self._compiled_errors = compile_errors
        self._compiled = [] if compile_errors else decks
        self._compiled_key = signature
        return self._compiled

    # ------------------------------------------------------------ 查询/判定
    def opponent_class(self, log_state) -> str:
        """对方职业：优先 log_state 的 CLASS 标签，缺失才退回卡库。"""
        try:
            oppo = log_state.oppo_entity
            hero_id = oppo.query_tag("HERO_ENTITY", "")
            hero = log_state.entity_dict.get(str(hero_id)) if hero_id else None
            if hero is not None:
                hero_class = str(hero.query_tag("CLASS", "") or "").strip()
                if hero_class and hero_class != "NEUTRAL":
                    return hero_class
                # 多职业英雄/皮肤可能给 NEUTRAL，退回卡库查它的 cardClass。
                card_class = card_catalog.default_catalog().class_of(
                    getattr(hero, "card_id", "")) if card_catalog.default_catalog() else ""
                if card_class:
                    return card_class
                return hero_class
        except Exception:
            pass
        return ""

    def build_context(self, log_state, opponent_class=None) -> EvalContext:
        my_turn, oppo_turn = self._turns(log_state)
        return EvalContext(
            facts=self.facts,
            opponent_class=(self.opponent_class(log_state)
                            if opponent_class is None else opponent_class),
            my_turn=my_turn,
            oppo_turn=oppo_turn,
            round_turn=max(my_turn, oppo_turn),
            log_state=log_state,
        )

    def check(self, raw_config, log_state) -> Optional[dict]:
        """判定一次。命中返回 ``{"deck_id","name","action","evidence"}``。"""
        with self._lock:
            if self._triggered:
                return None
            decks = self.configure(raw_config)
            if not decks:
                return None
            ctx = self.build_context(log_state)
            hit = evaluate_decks(decks, ctx)
            if hit is None:
                return None
            self._triggered = True
            self._hit = {
                "deck_id": hit.id,
                "name": hit.name,
                "action": hit.action,
                "opponent_class": ctx.opponent_class,
                "round": ctx.round,
                "evidence": self._evidence(hit, ctx),
            }
            return self._hit

    def preview(self, raw_config, log_state) -> dict:
        """保存配置时试跑：当前这一局会不会**立刻**触发（不真的投降）。"""
        normalized = normalize_config(raw_config)
        if not normalized["ok"]:
            return {"ok": False, "errors": normalized["errors"],
                    "would_fire_now": False}
        decks, compile_errors, corrected = compile_config(
            normalized["config"])
        if compile_errors:
            return {"ok": False, "errors": compile_errors,
                    "would_fire_now": False}
        base = {"ok": True, "errors": [],
                "warnings": normalized["warnings"],
                "corrected": corrected,
                "config": normalized["config"]}
        ctx = self.build_context(log_state) if log_state is not None else None
        if ctx is None:
            return dict(base, would_fire_now=False)
        hit = evaluate_decks(decks, ctx)
        return dict(base, would_fire_now=hit is not None,
                    hit=({"deck_id": hit.id, "name": hit.name}
                         if hit else None))

    def _evidence(self, deck: CompiledDeck, ctx: EvalContext) -> list:
        """命中时逐项列出证据（日志里给人看）。"""
        lines = []
        if deck.hero_class:
            ok = ctx.opponent_class == deck.hero_class
            lines.append(
                f"对方职业={deck.hero_class}"
                f"（实际 {ctx.opponent_class or '未知'}）{'✓' if ok else '✗'}")
        for leaf, negated in iter_leaves_with_polarity(deck.expr):
            ok = eval_leaf(leaf, ctx)
            prefix = "非 " if negated else ""
            lines.append(f"{prefix}{leaf.label()} {'✓' if ok else '✗'}")
        try:
            overall = bool(eval_node(deck.expr, ctx))
        except Exception:
            overall = False
        lines.append(f"整体条件：{describe_expr(deck.expr)} "
                     f"{'✓' if overall else '✗'}")
        return lines

    def status(self, log_state=None) -> dict:
        """给网页/浮窗看的状态：命中了什么、每套卡组满足到哪一步。"""
        with self._lock:
            data = {
                "triggered": self._triggered,
                "hit": self._hit,
                "compile_errors": list(self._compiled_errors),
                "facts": self.facts.snapshot(),
            }
            if log_state is None or not self._compiled:
                data["decks"] = []
                return data
            try:
                ctx = self.build_context(log_state)
            except Exception:
                data["decks"] = []
                return data
            data["opponent_class"] = ctx.opponent_class
            data["round"] = ctx.round
            data["my_turn"] = ctx.my_turn
            data["oppo_turn"] = ctx.oppo_turn

            # 事实列表（网页状态行显示"对方打出过什么"）
            played_by_oppo = []
            for event in self.facts.played:
                if resolve_side(log_state, event) == SIDE_OPPONENT:
                    played_by_oppo.append({"card_id": event.card_id,
                                           "round": event.round})
            data["opponent_played"] = played_by_oppo

            revealed_by_oppo = []
            for event in self.facts.revealed.values():
                if resolve_side(log_state, event) == SIDE_OPPONENT:
                    revealed_by_oppo.append({"card_id": event.card_id,
                                             "round": event.round})
            data["opponent_revealed"] = revealed_by_oppo

            decks = []
            for deck in self._compiled:
                leaves = list(iter_leaves(deck.expr))
                checks = []
                for leaf in leaves:
                    try:
                        ok = eval_leaf(leaf, ctx)
                    except Exception:
                        ok = False
                    checks.append({"text": leaf.label(), "ok": bool(ok)})
                class_ok = (not deck.hero_class
                            or ctx.opponent_class == deck.hero_class)
                # 命中必须用**真实表达式**求值：以前把树拍平后 all(叶子)，
                # 带「或」的卡组只要有一支没满足就显示未命中（引擎判定是对的、
                # 状态栏是错的）。checks/done/total 保持扁平契约不变。
                try:
                    expr_ok = bool(eval_node(deck.expr, ctx))
                except Exception:
                    expr_ok = False
                try:
                    tree = eval_tree(deck.expr, ctx)
                except Exception:
                    tree = {"op": "leaf", "ok": False, "text": "（求值失败）"}
                matched = class_ok and expr_ok
                decks.append({
                    "id": deck.id,
                    "name": deck.name,
                    "class": deck.hero_class,
                    "class_ok": class_ok,
                    "matched": bool(matched),
                    "checks": checks,
                    "tree": tree,
                    "done": sum(1 for item in checks if item["ok"]),
                    "total": len(checks),
                })
            data["decks"] = decks
            return data


engine = SurrenderRuleEngine()
