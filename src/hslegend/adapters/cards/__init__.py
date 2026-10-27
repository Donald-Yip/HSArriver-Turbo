"""Read-only JSON card catalog adapter."""

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

from hslegend.domain import CardCapabilitySet, CardDefinition


class CardCatalogError(ValueError):
    """Raised when card source data is missing or malformed."""


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
    if "name" not in card:
        return ""
    value = card["name"]
    if not isinstance(value, str):
        raise CardCatalogError("card name must be a string")
    return value


def _cost(card: Mapping[str, Any]) -> int:
    if "cost" in card:
        value = card["cost"]
    elif "mana_cost" in card:
        value = card["mana_cost"]
    else:
        return 0
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
    normalized: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise CardCatalogError("card mechanics must be non-empty strings")
        normalized.add(item.strip().upper())
    return frozenset(normalized)


def _capabilities(card: Mapping[str, Any], mechanics: frozenset[str]) -> CardCapabilitySet:
    card_type = _text_field(card, ("type", "card_type")).upper()
    text = _text_field(card, ("text",))
    lowered = text.lower()
    arrow = _text_field(card, ("targetingArrowText",)).strip()
    arrow_lower = arrow.lower()
    targeting_mechanic = bool(mechanics & {"TARGETED", "TARGET"})
    targeting_phrase = any(
        phrase in lowered
        for phrase in (
            "deal ",
            "destroy ",
            "restore ",
            "give ",
            "summon ",
            "choose a ",
            "select a ",
            "target ",
        )
    )
    targetable = bool(arrow) or targeting_mechanic or targeting_phrase
    hand_target = targetable and (
        "hand" in arrow_lower
        or ("in your hand" in lowered and any(word in lowered for word in ("give ", "buff ", "target ")))
    )
    board_target = targetable and not hand_target
    return CardCapabilitySet(
        targetable=targetable,
        hand_target=hand_target,
        board_target=board_target,
        choose_one="CHOOSE_ONE" in mechanics or lowered.startswith("choose one"),
        discover="DISCOVER" in mechanics or "discover" in lowered,
        starship="STARSHIP" in mechanics or "starship" in lowered,
        location=card_type == "LOCATION" or "LOCATION" in mechanics,
        trade=bool(mechanics & {"TRADE", "TRADEABLE"})
        or "tradeable" in lowered
        or bool(re.search(r"\btrade\b", lowered)),
        timeline="TIMELINE" in mechanics or "timeline" in lowered,
    )


def _definition(card: Any) -> CardDefinition:
    if not isinstance(card, dict):
        raise CardCatalogError("each card entry must be a JSON object")
    card_id = _text_field(card, ("id", "card_id"), required=True)
    mechanics = _mechanics(card.get("mechanics"))
    card_type = _text_field(card, ("type", "card_type"))
    return CardDefinition(
        card_id=card_id,
        name=_name(card),
        mana_cost=_cost(card),
        card_type=card_type,
        text=_text_field(card, ("text",)),
        capabilities=_capabilities(card, mechanics),
    )


@lru_cache(maxsize=None)
def _catalog_lock(path_key: str) -> threading.Lock:
    return threading.Lock()


@lru_cache(maxsize=None)
def _load_catalog_cached(path_key: str) -> _CatalogState:
    path = Path(path_key)
    try:
        source = path.read_bytes()
    except OSError as exc:
        raise CardCatalogError(f"cannot read card catalog: {path}") from exc
    version_hash = hashlib.sha256(source).hexdigest()
    try:
        parsed = json.loads(source.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CardCatalogError(f"invalid card catalog JSON: {path}") from exc
    if not isinstance(parsed, list):
        raise CardCatalogError("card catalog root must be a JSON array")
    definitions: dict[str, CardDefinition] = {}
    for entry in parsed:
        definition = _definition(entry)
        if definition.card_id in definitions:
            raise CardCatalogError(f"duplicate card id: {definition.card_id}")
        definitions[definition.card_id] = definition
    return _CatalogState(MappingProxyType(definitions), version_hash)


def _load_catalog(path_key: str) -> _CatalogState:
    with _catalog_lock(path_key):
        return _load_catalog_cached(path_key)


class JsonCardCatalog:
    """Process-cached, read-only JSON card catalog."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        resolved = Path(path).expanduser().resolve(strict=False)
        self._path_key = os.path.normcase(str(resolved))
        self._state = _load_catalog(self._path_key)

    def get_card(self, card_id: str) -> CardDefinition | None:
        return self._state.definitions.get(card_id)

    @property
    def version_hash(self) -> str:
        return self._state.version_hash


__all__ = ["CardCatalogError", "JsonCardCatalog"]
