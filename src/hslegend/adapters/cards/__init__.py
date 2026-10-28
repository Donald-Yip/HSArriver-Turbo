"""Read-only card catalog and unified card semantics."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from hslegend.domain import (
    CardCapabilitySet,
    CardDefinition,
    TargetScope,
    card_names_close,
    card_names_match,
    normalize_card_name,
)

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_RULES_PATH = ROOT / "rules" / "card_rules.json"

_CHOOSE_IN_HAND = re.compile(
    r"选择[^，。；\n]{0,6}?手牌中(?:的)?一(?:张|个)"
    r"|选择一张你?的?手牌")
_CHOOSE_IN_HAND_OR_BOARD = re.compile(
    r"选择[^，。；\n]{0,6}?手牌中?或(?:战场|场上)")
_CHOOSE_HAND_SUBSET = re.compile(
    r"你手牌中的(?:\d+|[一二三四五六七八九十]+)张(?:牌)?(?:，|,)?选(?:择)?一?张")
_CHOOSE_FROM_HAND = re.compile(
    r"(?:从|由)你(?:的)?(?:\d+|[一二三四五六七八九十]+)张手牌中[^。；\n]{0,6}选(?:择)?")
_SPECIFY_HAND_CARD = re.compile(
    r"使[^，。；\n]{0,6}你手牌中一(?:张|个)[^，。；\n]{0,12}(?:获得|法力值消耗)")
_AUTOMATIC_PICK = re.compile(
    r"随机|一张随机|在你的英雄攻击后|在你的回合结束时|在你的回合开始时|每当"
    r"|在[^，。；\n]{0,6}后，")
_CARD_TYPE_WORDS = (
    ("法术", "SPELL"),
    ("随从", "MINION"),
    ("地标", "LOCATION"),
    ("武器", "WEAPON"),
    ("英雄", "HERO"),
)
_OPTION_SUFFIX = re.compile(r"^[A-Za-z]$")
_OPTION_TYPES = frozenset({"SPELL", "MINION", "WEAPON", "LOCATION", "HERO"})
_OLD_GOD_KEYWORD = "<b>古神</b>"


class CardCatalogError(ValueError):
    """Raised when card source data or measured rules are malformed."""


@dataclass(frozen=True, slots=True)
class _CatalogState:
    definitions: Mapping[str, CardDefinition]
    version_hash: str


def _text_field(
    card: Mapping[str, Any],
    names: tuple[str, ...],
    *,
    default: str = "",
    required: bool = False,
) -> str:
    for name in names:
        if name not in card:
            continue
        value = card[name]
        if value is None:
            if required:
                raise CardCatalogError(f"card {name} must be a string")
            return default
        if not isinstance(value, str):
            raise CardCatalogError(f"card {name} must be a string")
        if required and not value.strip():
            raise CardCatalogError(f"card {name} must be non-empty")
        return value.strip() if required else value
    if required:
        raise CardCatalogError(f"card {names[0]} is required")
    return default


def _name(card: Mapping[str, Any]) -> str:
    value = card.get("name", "")
    if not isinstance(value, str):
        raise CardCatalogError("card name must be a string")
    return value


def _cost(card: Mapping[str, Any]) -> int:
    value = card["cost"] if "cost" in card else card.get("mana_cost", 0)
    if value is None:
        return 0
    if type(value) is not int or value < 0:
        raise CardCatalogError("card cost must be a non-negative integer or null")
    return value


def _mechanics(value: Any) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise CardCatalogError("card mechanics must be a sequence of strings")
    normalized = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise CardCatalogError("card mechanics must be non-empty strings")
        normalized.add(item.strip().upper())
    return frozenset(normalized)


def _describe(card: Mapping[str, Any]) -> str:
    return f"{card.get('targetingArrowText') or ''}\n{card.get('text') or ''}"


def _sentence_of(text: str, match: re.Match[str]) -> str:
    start = max((text.rfind(ch, 0, match.start()) for ch in "。；！\n"), default=-1)
    ends = [pos for pos in (text.find(ch, match.end()) for ch in "。；！\n") if pos != -1]
    return text[start + 1:min(ends) if ends else len(text)]


def _is_automatic_pick(text: str, match: re.Match[str]) -> bool:
    return bool(_AUTOMATIC_PICK.search(_sentence_of(text, match)))


def _match_hand_pattern(text: str) -> re.Match[str] | None:
    for pattern in (
        _CHOOSE_IN_HAND,
        _CHOOSE_HAND_SUBSET,
        _CHOOSE_FROM_HAND,
        _SPECIFY_HAND_CARD,
    ):
        match = pattern.search(text)
        if match is not None and not _is_automatic_pick(text, match):
            return match
    return None


def _cardtypes_in(text: str, match: re.Match[str]) -> tuple[str, ...]:
    sentence = _sentence_of(text, match)
    hand_at = sentence.find("手牌")
    head = sentence[hand_at + 2:] if hand_at >= 0 else sentence
    return tuple(name for word, name in _CARD_TYPE_WORDS if word in head)


def _target_scope_and_types(
    card: Mapping[str, Any], targetable: bool
) -> tuple[TargetScope, tuple[str, ...]]:
    text = _describe(card)
    if _CHOOSE_IN_HAND_OR_BOARD.search(text):
        return TargetScope.HAND_OR_BOARD, ()
    match = _match_hand_pattern(text)
    if match is not None:
        return TargetScope.HAND, _cardtypes_in(text, match)
    if targetable:
        return TargetScope.BOARD, ()
    return TargetScope.NONE, ()


def _capabilities(
    card: Mapping[str, Any], mechanics: frozenset[str]
) -> CardCapabilitySet:
    card_type = _text_field(card, ("type", "card_type")).upper()
    text = _text_field(card, ("text",))
    lowered = text.lower()
    arrow = _text_field(card, ("targetingArrowText",)).strip()
    targeting_mechanic = bool(mechanics & {"TARGETED", "TARGET"})
    targeting_phrase = any(
        phrase in lowered
        for phrase in (
            "deal ", "destroy ", "restore ", "give ", "summon ",
            "choose a ", "select a ", "target ",
        )
    )
    targetable = bool(arrow) or targeting_mechanic or targeting_phrase
    hand_target = targetable and (
        "hand" in arrow.lower()
        or ("in your hand" in lowered and any(
            word in lowered for word in ("give ", "buff ", "target ")
        ))
    )
    return CardCapabilitySet(
        targetable=targetable,
        hand_target=hand_target,
        board_target=targetable and not hand_target,
        choose_one="CHOOSE_ONE" in mechanics or lowered.startswith("choose one"),
        discover="DISCOVER" in mechanics or "discover" in lowered,
        starship="STARSHIP" in mechanics or "starship" in lowered,
        location=card_type == "LOCATION" or "LOCATION" in mechanics,
        trade=bool(mechanics & {"TRADE", "TRADEABLE"})
        or "tradeable" in lowered
        or bool(re.search(r"\btrade\b", lowered)),
        timeline="TIMELINE" in mechanics or "timeline" in lowered,
    )


def _rule_strings(card_id: str, value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise CardCatalogError(f"{field} for {card_id} must be non-empty strings")
    return tuple(item.strip() for item in value)


def _validate_rule(card_id: str, rule: Mapping[str, Any]) -> None:
    allowed = {
        "target_scope",
        "target_card_types",
        "choose_one_options",
        "ability_panel",
    }
    unknown = set(rule) - allowed
    if unknown:
        raise CardCatalogError(f"unknown rule keys for {card_id}: {sorted(unknown)}")


def _derive_choose_one_options(
    card: Mapping[str, Any], ids: set[str], types: Mapping[str, str]
) -> tuple[str, ...]:
    card_id = str(card["id"])
    if "CHOOSE_ONE" not in _mechanics(card.get("mechanics")):
        return ()
    return tuple(sorted(
        child for child in ids
        if len(child) == len(card_id) + 1
        and child.startswith(card_id)
        and _OPTION_SUFFIX.fullmatch(child[-1])
        and types.get(child) in _OPTION_TYPES
    ))


def _definition(
    card: Any,
    *,
    ids: set[str],
    types: Mapping[str, str],
    rule: Mapping[str, Any] | None,
) -> CardDefinition:
    if not isinstance(card, dict):
        raise CardCatalogError("each card entry must be a JSON object")
    card_id = _text_field(card, ("id", "card_id"), required=True)
    capabilities = _capabilities(card, _mechanics(card.get("mechanics")))
    scope, target_types = _target_scope_and_types(card, capabilities.targetable)
    choose_one = _derive_choose_one_options(card, ids, types)
    panel: tuple[str, ...] = ()
    if rule is not None:
        _validate_rule(card_id, rule)
        if "target_scope" in rule:
            try:
                scope = TargetScope(rule["target_scope"])
            except (TypeError, ValueError) as exc:
                raise CardCatalogError(f"invalid target_scope for {card_id}") from exc
        if "target_card_types" in rule:
            target_types = _rule_strings(card_id, rule["target_card_types"], "target_card_types")
        if "choose_one_options" in rule:
            choose_one = _rule_strings(card_id, rule["choose_one_options"], "choose_one_options")
        if "ability_panel" in rule:
            panel = _rule_strings(card_id, rule["ability_panel"], "ability_panel")
    if len(set(choose_one)) != len(choose_one):
        raise CardCatalogError(f"duplicate choose-one child for {card_id}")
    if len(set(panel)) != len(panel):
        raise CardCatalogError(f"duplicate ability panel child for {card_id}")
    for child in choose_one:
        if child not in ids:
            raise CardCatalogError(f"unknown choose-one child: {child}")
    for child in panel:
        if child not in ids:
            raise CardCatalogError(f"unknown ability panel child: {child}")
    return CardDefinition(
        card_id=card_id,
        name=_name(card),
        mana_cost=_cost(card),
        card_type=_text_field(card, ("type", "card_type")),
        text=_text_field(card, ("text",)),
        capabilities=capabilities,
        target_scope=scope,
        target_card_types=target_types,
        choose_one_options=choose_one,
        ability_panel=panel,
    )


@lru_cache(maxsize=None)
def _catalog_lock(key: str) -> threading.Lock:
    return threading.Lock()


@lru_cache(maxsize=None)
def _load_catalog_cached(path_key: str, rules_key: str) -> _CatalogState:
    path = Path(path_key)
    try:
        source = path.read_bytes()
    except OSError as exc:
        raise CardCatalogError(f"cannot read card catalog: {path}") from exc
    try:
        parsed = json.loads(source.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CardCatalogError(f"invalid card catalog JSON: {path}") from exc
    if not isinstance(parsed, list):
        raise CardCatalogError("card catalog root must be a JSON array")
    rules: dict[str, Mapping[str, Any]] = {}
    if rules_key:
        rules_path = Path(rules_key)
        try:
            payload = json.loads(rules_path.read_bytes().decode("utf-8-sig"))
        except OSError as exc:
            raise CardCatalogError(f"cannot read card rules: {rules_path}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CardCatalogError(f"invalid card rules JSON: {rules_path}") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise CardCatalogError("card rules must be an object with schema_version 1")
        raw_rules = payload.get("cards", {})
        if not isinstance(raw_rules, dict):
            raise CardCatalogError("card rules cards must be an object")
        for card_id, value in raw_rules.items():
            if not isinstance(card_id, str) or not isinstance(value, dict):
                raise CardCatalogError("card rules must map card ids to objects")
            rules[card_id] = value
    ids = {str(item["id"]) for item in parsed if isinstance(item, dict) and "id" in item}
    unknown = set(rules) - ids
    if unknown:
        raise CardCatalogError(f"unknown card rule entries: {sorted(unknown)}")
    types = {
        str(item["id"]): _text_field(item, ("type", "card_type")).upper()
        for item in parsed
        if isinstance(item, dict) and "id" in item
    }
    definitions: dict[str, CardDefinition] = {}
    for entry in parsed:
        card_id = str(entry.get("id")) if isinstance(entry, dict) else ""
        definition = _definition(
            entry, ids=ids, types=types, rule=rules.get(card_id)
        )
        if definition.card_id in definitions:
            raise CardCatalogError(f"duplicate card id: {definition.card_id}")
        definitions[definition.card_id] = definition
    return _CatalogState(
        MappingProxyType(definitions),
        hashlib.sha256(source).hexdigest(),
    )


def _load_catalog(path_key: str, rules_key: str) -> _CatalogState:
    with _catalog_lock(path_key + "|" + rules_key):
        return _load_catalog_cached(path_key, rules_key)


class JsonCardCatalog:
    """Process-cached, read-only JSON card catalog."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        rules_path: str | os.PathLike[str] | None = None,
    ) -> None:
        resolved = Path(path).expanduser().resolve(strict=False)
        self._path_key = os.path.normcase(str(resolved))
        self._rules_key = "" if rules_path is None else os.path.normcase(
            str(Path(rules_path).expanduser().resolve(strict=False))
        )
        self._state = _load_catalog(self._path_key, self._rules_key)

    def get_card(self, card_id: str) -> CardDefinition | None:
        return self._state.definitions.get(card_id)

    @property
    def version_hash(self) -> str:
        return self._state.version_hash

    @property
    def definitions(self) -> Mapping[str, CardDefinition]:
        return self._state.definitions


class JsonCardSemantics(JsonCardCatalog):
    """Card queries used by recommendation parsing and adaptation."""

    def card_text(self, card_id: str | None) -> str:
        card = self.get_card(card_id or "")
        return card.text if card is not None else ""

    def is_old_god_card(self, card_id: str | None) -> bool:
        text = self.card_text(card_id).strip()
        return bool(text and text.splitlines()[0].strip().startswith(_OLD_GOD_KEYWORD))

    def is_starship_card(self, card_id: str | None) -> bool:
        card = self.get_card(card_id or "")
        return bool(card and card.capabilities.starship)

    def is_choose_one_card(self, card_id: str | None) -> bool:
        card = self.get_card(card_id or "")
        return bool(card and card.capabilities.choose_one)

    def choose_one_option_card_ids(self, card_id: str | None) -> frozenset[str]:
        card = self.get_card(card_id or "")
        return frozenset(card.choose_one_options) if card else frozenset()

    def choose_one_card_name(self, card_id: str | None) -> str | None:
        card = self.get_card(card_id or "")
        return card.name if card is not None and card.capabilities.choose_one else None

    def ability_panel(self, card_id: str | None) -> tuple[str, ...]:
        card = self.get_card(card_id or "")
        return card.ability_panel if card else ()

    def skill_panel(self, card_id: str | None) -> tuple[tuple[str, str], ...]:
        result = []
        for child in self.ability_panel(card_id):
            card = self.get_card(child)
            if card is not None:
                result.append((card.name, child))
        return tuple(result)

    def skill_index(self, card_id: str | None, name: str | None) -> int | None:
        if not name:
            return None
        wanted = normalize_card_name(name)
        names = tuple(item[0] for item in self.skill_panel(card_id))
        exact = [
            index for index, option in enumerate(names)
            if normalize_card_name(option) == wanted
        ]
        if len(exact) == 1:
            return exact[0]
        if exact:
            return None
        near = [
            index for index, option in enumerate(names)
            if card_names_close(wanted, normalize_card_name(option))
        ]
        return near[0] if len(near) == 1 else None

    def hand_target_rule(self, card_id: str | None) -> dict[str, object]:
        card = self.get_card(card_id or "")
        scope = card.target_scope if card is not None else TargetScope.NONE
        if scope is TargetScope.HAND:
            return {"kind": "hand", "cardtypes": frozenset(card.target_card_types)}
        if scope is TargetScope.HAND_OR_BOARD:
            return {"kind": "hand_or_board", "cardtypes": frozenset(card.target_card_types)}
        return {"kind": "none", "cardtypes": frozenset()}

    def is_friendly_hand_target_card(self, card_id: str | None) -> bool:
        return self.hand_target_rule(card_id)["kind"] == "hand"

    def is_hand_or_board_target_card(self, card_id: str | None) -> bool:
        return self.hand_target_rule(card_id)["kind"] == "hand_or_board"

    def allows_hand_target(self, card_id: str | None) -> bool:
        return self.hand_target_rule(card_id)["kind"] != "none"

    def hand_target_allowed_cardtypes(self, card_id: str | None) -> frozenset[str]:
        value = self.hand_target_rule(card_id)["cardtypes"]
        return frozenset(value)  # type: ignore[arg-type]

    def hand_target_cardtype_mismatch(
        self, card_id: str | None, hand_card: object
    ) -> bool:
        allowed = self.hand_target_allowed_cardtypes(card_id)
        cardtype = getattr(hand_card, "cardtype", None)
        if not allowed or not cardtype:
            return False
        return str(cardtype).upper() not in allowed


@lru_cache(maxsize=None)
def default_card_semantics(
    cards_path: str | os.PathLike[str] = ROOT / "cards.json",
    rules_path: str | os.PathLike[str] = DEFAULT_RULES_PATH,
) -> JsonCardSemantics:
    return JsonCardSemantics(cards_path, rules_path=rules_path)


def card_names_are_same(left: object, right: object) -> bool:
    return card_names_match(left, right)


__all__ = [
    "CardCatalogError",
    "DEFAULT_RULES_PATH",
    "JsonCardCatalog",
    "JsonCardSemantics",
    "ROOT",
    "card_names_are_same",
    "default_card_semantics",
]
