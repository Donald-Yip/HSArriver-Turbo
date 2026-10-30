# -*- coding: utf-8 -*-
"""Replay fixtures for the local deck-strategy engine.

The annotation sample shipped with Joint0.1 (``tests/fixtures/strategy/
zoo_turn4.json``) is a serialized ``StrategyState`` from a real zoo-warlock
turn. These helpers rebuild a duck-typed state object from that JSON so tests
can run the real search/strategy code without a live game.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from constants.constants import (
    CARD_HERO,
    CARD_HERO_POWER,
    CARD_LOCATION,
    CARD_MINION,
    CARD_SPELL,
    CARD_WEAPON,
)
from strategy_entity import (
    StrategyHero,
    StrategyHeroPower,
    StrategyLocation,
    StrategyMinion,
    StrategySpell,
    StrategyWeapon,
)


FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "strategy"


def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def deserialize_entity(data):
    """JSON dict -> StrategyMinion / Spell / Weapon / Hero / Location / HeroPower."""
    if not data:
        return None
    cardtype = data.get("cardtype")
    common = {
        "entity_id": data.get("entity_id"),
        "card_id": data.get("card_id"),
        "zone": data.get("zone") or "PLAY",
        "zone_pos": _to_int(data.get("zone_pos"), 0),
        "current_cost": _to_int(data.get("current_cost"), 0),
        "overload": _to_int(data.get("overload"), 0),
        "is_mine": bool(data.get("is_mine", True)),
    }
    if cardtype == CARD_MINION:
        return StrategyMinion(
            **common,
            attack=_to_int(data.get("attack")),
            max_health=_to_int(data.get("max_health")),
            damage=_to_int(data.get("damage")),
            taunt=_to_int(data.get("taunt")),
            divine_shield=_to_int(data.get("divine_shield")),
            stealth=_to_int(data.get("stealth")),
            windfury=_to_int(data.get("windfury")),
            poisonous=_to_int(data.get("poisonous")),
            life_steal=_to_int(data.get("life_steal")),
            spell_power=_to_int(data.get("spell_power")),
            freeze=_to_int(data.get("freeze")),
            battlecry=_to_int(data.get("battlecry")),
            not_targeted_by_spell=_to_int(data.get("not_targeted_by_spell")),
            not_targeted_by_power=_to_int(data.get("not_targeted_by_power")),
            charge=_to_int(data.get("charge")),
            rush=_to_int(data.get("rush")),
            attackable_by_rush=_to_int(data.get("attackable_by_rush")),
            frozen=_to_int(data.get("frozen")),
            dormant=_to_int(data.get("dormant")),
            untouchable=_to_int(data.get("untouchable")),
            immune=_to_int(data.get("immune")),
            reborn=bool(data.get("reborn", 0)),
            cant_attack=_to_int(data.get("cant_attack")),
            exhausted=_to_int(data.get("exhausted"), 1),
            num_turns_in_play=_to_int(data.get("num_turns_in_play"), 1),
            temporary=_to_int(data.get("temporary")),
            created_by=str(data.get("created_by") or ""),
        )
    if cardtype == CARD_WEAPON:
        return StrategyWeapon(
            **common,
            attack=_to_int(data.get("attack")),
            durability=_to_int(data.get("durability")),
            damage=_to_int(data.get("damage")),
            windfury=_to_int(data.get("windfury")),
        )
    if cardtype == CARD_HERO:
        return StrategyHero(
            **common,
            max_health=_to_int(data.get("max_health")),
            damage=_to_int(data.get("damage")),
            stealth=_to_int(data.get("stealth")),
            immune=_to_int(data.get("immune")),
            not_targeted_by_spell=_to_int(data.get("not_targeted_by_spell")),
            not_targeted_by_power=_to_int(data.get("not_targeted_by_power")),
            armor=_to_int(data.get("armor")),
            attack=_to_int(data.get("attack")),
            exhausted=_to_int(data.get("exhausted"), 1),
            frozen=_to_int(data.get("frozen")),
            cant_attack=_to_int(data.get("cant_attack")),
        )
    if cardtype == CARD_SPELL:
        return StrategySpell(
            **common,
            temporary=_to_int(data.get("temporary")),
            created_by=str(data.get("created_by") or ""),
        )
    if cardtype == CARD_LOCATION:
        return StrategyLocation(
            **common,
            max_health=_to_int(data.get("max_health")),
            damage=_to_int(data.get("damage")),
            action_cooldown=_to_int(data.get("action_cooldown")),
            exhausted=_to_int(data.get("exhausted")),
            just_played=_to_int(data.get("just_played")),
        )
    if cardtype == CARD_HERO_POWER:
        return StrategyHeroPower(
            **common, exhausted=_to_int(data.get("exhausted")))
    return None


class _GraveyardCard:
    """Graveyard placeholder: deck context only reads ``card_id``."""

    def __init__(self, data):
        self.entity_id = data.get("entity_id")
        self.card_id = data.get("card_id")
        self.zone = "GRAVEYARD"
        self.zone_pos = data.get("zone_pos", 0)
        self.is_mine = True
        self.cardtype = None

    @property
    def name(self):
        return ""


class FixtureState(SimpleNamespace):
    """Duck-typed StrategyState stand-in (same fields the search reads)."""

    @property
    def my_last_mana(self):
        return self.my_total_mana - self.my_used_mana + self.my_temp_mana

    @property
    def my_minion_num(self):
        return len(self.my_minions)

    @property
    def oppo_minion_num(self):
        return len(self.oppo_minions)

    @property
    def my_board_slot_num(self):
        return len(self.my_minions) + len(self.my_locations)

    @property
    def oppo_board_slot_num(self):
        return len(self.oppo_minions) + len(self.oppo_locations)

    @property
    def my_hand_card_num(self):
        return len(self.my_hand_cards)


def state_from_dict(data):
    """JSON state -> FixtureState usable by ``search.search`` / deck strategies."""
    data = data or {}
    mana = data.get("mana") or {}
    general = data.get("general_choice_cards") or {}
    try:
        general = {int(key): value for key, value in general.items()}
    except (TypeError, ValueError):
        general = {}
    state = FixtureState(
        is_end=bool(data.get("is_end", False)),
        is_my_turn=bool(data.get("is_my_turn", True)),
        game_step=data.get("game_step") or "",
        acted_this_turn=bool(data.get("acted_this_turn", False)),
        game_num_turns_in_play=_to_int(data.get("game_num_turns_in_play")),
        log_revision=_to_int(data.get("log_revision")),
        my_total_mana=_to_int(mana.get("total")),
        my_used_mana=_to_int(mana.get("used")),
        my_temp_mana=_to_int(mana.get("temp")),
        my_hero=deserialize_entity(data.get("my_hero")),
        my_hero_power=deserialize_entity(data.get("my_hero_power")),
        my_hero_powers=[],
        my_weapon=deserialize_entity(data.get("my_weapon")),
        oppo_hero=deserialize_entity(data.get("oppo_hero")),
        oppo_hero_power=deserialize_entity(data.get("oppo_hero_power")),
        oppo_hero_powers=[],
        oppo_weapon=deserialize_entity(data.get("oppo_weapon")),
        my_minions=[deserialize_entity(item)
                    for item in (data.get("my_minions") or ())],
        oppo_minions=[deserialize_entity(item)
                      for item in (data.get("oppo_minions") or ())],
        my_locations=[deserialize_entity(item)
                      for item in (data.get("my_locations") or ())],
        oppo_locations=[deserialize_entity(item)
                        for item in (data.get("oppo_locations") or ())],
        my_hand_cards=[deserialize_entity(item)
                       for item in (data.get("my_hand_cards") or ())],
        my_graveyard=[_GraveyardCard(item)
                      for item in (data.get("my_graveyard") or ())],
        my_dead_card_ids=set(data.get("my_dead_card_ids") or ()),
        discover_choice_count=data.get("discover_choice_count"),
        discover_choice_pending=False,
        general_choice_cards=general,
        oppo_hand_card_num=_to_int(data.get("oppo_hand_card_num")),
        hand_entry_count=_to_int(data.get("hand_entry_count")),
        start_of_game_card_count=_to_int(
            data.get("start_of_game_card_count")),
        prepared_entity_ids=set(),
        blocked_action_keys=set(),
    )
    state.my_minions = [x for x in state.my_minions if x is not None]
    state.oppo_minions = [x for x in state.oppo_minions if x is not None]
    state.my_locations = [x for x in state.my_locations if x is not None]
    state.oppo_locations = [x for x in state.oppo_locations if x is not None]
    state.my_hand_cards = [x for x in state.my_hand_cards if x is not None]
    return state


def load_fixture(name):
    """Load a fixture file and return (raw_dict, FixtureState)."""
    path = FIXTURE_DIR / name
    data = json.loads(path.read_text(encoding="utf-8"))
    return data, state_from_dict(data.get("state") or data)
