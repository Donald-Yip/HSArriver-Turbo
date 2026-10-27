"""Power.log feeding and immutable snapshot mapping for the native bridge."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from log_op import LOG_CONTAINER_ERROR, LOG_CONTAINER_INFO
from log_state import LogState, update_state

from hslegend.domain import ChoiceView, EntityRef, GameSnapshot, PlayerView


class LegacyPowerLogError(RuntimeError):
    """Typed failure while pumping or mapping the legacy Power.log state."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        message = reason_code if not detail else f"{reason_code}: {detail}"
        super().__init__(message)


def _int_value(value: Any, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise LegacyPowerLogError(
            f"invalid_{field_name}", f"{field_name} must be an integer"
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise LegacyPowerLogError(
            f"invalid_{field_name}", f"{field_name} must be an integer"
        ) from exc
    if parsed < minimum:
        raise LegacyPowerLogError(
            f"invalid_{field_name}",
            f"{field_name} must be at least {minimum}",
        )
    return parsed


def _non_negative_clock(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LegacyPowerLogError("invalid_clock", "clock must return a number")
    timestamp = float(value)
    if not math.isfinite(timestamp) or timestamp < 0.0:
        raise LegacyPowerLogError(
            "invalid_clock", "clock must return a finite non-negative number"
        )
    return timestamp


def _tag(entity: Any, name: str, default: str | None = None) -> str | None:
    tags = getattr(entity, "tag_dict", None)
    if not isinstance(tags, Mapping):
        raise LegacyPowerLogError("invalid_entity", "entity has no tag dictionary")
    if name not in tags:
        return default
    value = tags.get(name)
    if value is None:
        return default
    return str(value).strip()


def _required_tag(entity: Any, name: str) -> str:
    value = _tag(entity, name)
    if not value:
        raise LegacyPowerLogError(
            "missing_entity_tag", f"entity is missing required tag {name}"
        )
    return value


def _entity_id(value: Any) -> int:
    return _int_value(value, "entity_id", minimum=1)


def _card_id(entity: Any) -> str:
    value = getattr(entity, "card_id", "")
    return str(value).strip() if value is not None else ""


def _iter_card_entities(state: Any) -> Iterable[tuple[int, Any]]:
    entities = getattr(state, "entity_dict", None)
    if not isinstance(entities, Mapping):
        raise LegacyPowerLogError("invalid_state", "state has no entity dictionary")
    for raw_id, entity in entities.items():
        if not hasattr(entity, "card_id"):
            continue
        yield _entity_id(raw_id), entity


@dataclass(frozen=True, slots=True)
class LegacyPowerLogPump:
    """Consume one pending legacy log container on every pump call."""

    containers: Iterable[Any]
    state: LogState | None = None
    updater: Callable[[Any, Any], Any] = update_state
    clock: Callable[[], float] = time.monotonic
    last_error: str = field(default="", init=False)

    def __post_init__(self) -> None:
        if not callable(self.updater):
            raise TypeError("updater must be callable")
        if not callable(self.clock):
            raise TypeError("clock must be callable")
        object.__setattr__(self, "containers", iter(self.containers))
        if self.state is None:
            object.__setattr__(self, "state", LogState())
        elif not isinstance(getattr(self.state, "entity_dict", None), Mapping):
            raise TypeError("state must expose a legacy LogState shape")
        object.__setattr__(self, "last_error", "")

    def pump(self) -> LogState:
        """Pump exactly one yielded container and return the current state."""

        state = self.state
        assert state is not None
        try:
            container = next(self.containers)  # type: ignore[arg-type]
        except StopIteration as exc:
            raise LegacyPowerLogError(
                "log_source_exhausted", "the scripted Power.log source ended"
            ) from exc
        except Exception as exc:
            raise LegacyPowerLogError("log_read_failed", str(exc)) from exc

        log_type = getattr(container, "log_type", None)
        if log_type == LOG_CONTAINER_ERROR:
            object.__setattr__(self, "last_error", "power_log_unavailable")
            return state
        if log_type != LOG_CONTAINER_INFO:
            raise LegacyPowerLogError(
                "invalid_log_container",
                f"unsupported legacy log container type: {log_type!r}",
            )

        session_id = str(getattr(container, "session_id", "") or "").strip()
        if session_id and session_id != getattr(state, "session_id", ""):
            if getattr(state, "session_id", "") not in ("", "unknown-session"):
                state = type(state)()
                object.__setattr__(self, "state", state)
            state.session_id = session_id

        messages = getattr(container, "message_list", None)
        if messages is None:
            raise LegacyPowerLogError(
                "invalid_log_container", "log container has no message_list"
            )
        for message in messages:
            try:
                accepted = self.updater(state, message)
            except Exception as exc:
                raise LegacyPowerLogError("log_update_failed", str(exc)) from exc
            if accepted is False:
                raise LegacyPowerLogError(
                    "invalid_log_update", "legacy state rejected a Power.log line"
                )
        object.__setattr__(self, "last_error", "")
        return state


class LegacyLogStateSnapshotMapper:
    """Map the pumped mutable ``LogState`` to one immutable ``GameSnapshot``."""

    def __init__(
        self,
        pump: LegacyPowerLogPump,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(pump, LegacyPowerLogPump):
            raise TypeError("pump must be a LegacyPowerLogPump")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._pump = pump
        self._clock = clock

    def revision(self) -> int:
        state = self._pump.pump()
        if self._pump.last_error:
            raise LegacyPowerLogError(
                "power_log_unavailable",
                "Power.log is unavailable; retained state is not authoritative",
            )
        return _int_value(getattr(state, "revision", None), "revision")

    def current(self) -> GameSnapshot:
        state = self._pump.pump()
        if self._pump.last_error:
            raise LegacyPowerLogError(
                "power_log_unavailable",
                "Power.log is unavailable; retained state is not authoritative",
            )
        return self._map_state(state)

    def _map_state(self, state: Any) -> GameSnapshot:
        if not getattr(state, "available", False):
            raise LegacyPowerLogError(
                "game_state_unavailable", "Power.log has no active game entity"
            )
        my_pid, oppo_pid = self._player_ids(state)
        my_player = self._player_view(state, my_pid, "friendly")
        opponent = self._player_view(state, oppo_pid, "opponent")
        choices = self._choices(state, my_player)
        revision = _int_value(getattr(state, "revision", None), "revision")
        generation = _int_value(
            getattr(state, "game_generation", None), "game_generation"
        )
        session = str(getattr(state, "session_id", "unknown-session") or "unknown-session")
        try:
            turn_number = _int_value(
                getattr(state, "game_num_turns_in_play", None), "turn_number"
            )
            is_my_turn = bool(getattr(state, "is_my_turn"))
            is_end = bool(getattr(state, "is_end"))
        except LegacyPowerLogError:
            raise
        except Exception as exc:
            raise LegacyPowerLogError("invalid_game_state", str(exc)) from exc
        try:
            return GameSnapshot(
                schema_version=1,
                snapshot_id=f"powerlog:{session}:{generation}:{revision}",
                log_revision=revision,
                game_generation=generation,
                observed_at=_non_negative_clock(self._clock()),
                turn_number=turn_number,
                is_my_turn=is_my_turn,
                is_end=is_end,
                my_player=my_player,
                opponent=opponent,
                choices=choices,
            )
        except (TypeError, ValueError) as exc:
            raise LegacyPowerLogError("invalid_snapshot", str(exc)) from exc

    @staticmethod
    def _player_ids(state: Any) -> tuple[str, str]:
        mapping = getattr(state, "player_id_map_dict", None)
        if not isinstance(mapping, Mapping):
            raise LegacyPowerLogError(
                "invalid_player_mapping", "player_id_map_dict is missing"
            )
        my_pid = str(getattr(state, "my_player_id", "") or "")
        oppo_pid = str(getattr(state, "oppo_player_id", "") or "")
        if not my_pid or not oppo_pid or my_pid == "0" or oppo_pid == "0":
            raise LegacyPowerLogError(
                "missing_player_identity", "both local and opponent player ids are required"
            )
        if my_pid == oppo_pid:
            raise LegacyPowerLogError(
                "invalid_player_mapping", "local and opponent player ids must differ"
            )
        for pid in (my_pid, oppo_pid):
            if pid not in mapping:
                raise LegacyPowerLogError(
                    "missing_player_entity", f"player id {pid} has no entity mapping"
                )
            if str(mapping[pid]) not in getattr(state, "entity_dict", {}):
                raise LegacyPowerLogError(
                    "missing_player_entity", f"mapped entity for player {pid} is absent"
                )
        return my_pid, oppo_pid

    def _player_view(self, state: Any, player_id: str, side: str) -> PlayerView:
        entity_id = state.player_id_map_dict[player_id]
        player_entity = state.entity_dict[str(entity_id)]
        player_names = getattr(state, "player_names", {}) or {}
        name = str(player_names.get(player_id, "") or "").strip()
        if not name:
            name = str(
                getattr(state, "my_name" if side == "friendly" else "oppo_name", "") or ""
            ).strip()

        owned: list[tuple[str, EntityRef]] = []
        for entity_id_int, entity in _iter_card_entities(state):
            controller = _tag(entity, "CONTROLLER")
            zone = (_tag(entity, "ZONE", "") or "").upper()
            if controller is None:
                if zone in {"HAND", "DECK", "PLAY", "SECRET", "WEAPON"}:
                    raise LegacyPowerLogError(
                        "missing_entity_ownership",
                        f"entity {entity_id_int} in zone {zone} has no CONTROLLER",
                    )
                continue
            if controller not in (state.my_player_id, state.oppo_player_id):
                if zone in {"HAND", "DECK", "PLAY", "SECRET", "WEAPON"}:
                    raise LegacyPowerLogError(
                        "invalid_entity_ownership",
                        f"entity {entity_id_int} has unknown controller {controller}",
                    )
                continue
            expected_side = (
                "friendly" if controller == state.my_player_id else "opponent"
            )
            if expected_side != side:
                continue
            if zone not in {"HAND", "DECK", "PLAY", "SECRET", "WEAPON"}:
                continue
            card_type = (_tag(entity, "CARDTYPE", "") or "").upper()
            if zone == "PLAY" and card_type not in {
                "HERO",
                "WEAPON",
                "MINION",
                "LOCATION",
            }:
                # Hero powers and enchantments are not board entities.
                continue
            if not _card_id(entity) and side == "friendly":
                raise LegacyPowerLogError(
                    "missing_card_id", f"friendly entity {entity_id_int} has no card id"
                )
            owned.append((zone, self._entity_ref(entity_id_int, entity, side, zone)))

        heroes = [
            ref
            for zone, ref in owned
            if _is_hero(state, ref.entity_id, zone)
        ]
        if len(heroes) != 1:
            raise LegacyPowerLogError(
                "invalid_hero",
                f"expected one {side} hero, found {len(heroes)}",
            )
        hero = heroes[0]
        hand = _sorted_zone(owned, "HAND", "hand")
        board = _sorted_zone(owned, "PLAY", "board", exclude_hero=True, state=state)
        deck = _sorted_zone(owned, "DECK", "deck")
        secrets = _sorted_zone(owned, "SECRET", "secret")
        weapons = _sorted_zone(
            owned, "PLAY", "weapon", card_type="WEAPON", state=state
        )
        if len(weapons) > 1:
            raise LegacyPowerLogError(
                "invalid_weapon", f"expected at most one {side} weapon"
            )
        _reject_duplicate_positions(hand, "hand")
        _reject_duplicate_positions(board, "board")
        _reject_duplicate_positions(secrets, "secret")

        hero_entity = state.entity_dict[str(hero.entity_id)]
        health_max = _int_value(_required_tag(hero_entity, "HEALTH"), "health")
        damage = _int_value(_tag(hero_entity, "DAMAGE", "0"), "damage")
        health = health_max - damage
        if health < 0:
            raise LegacyPowerLogError("invalid_health", "hero health is negative")
        armor = _int_value(_tag(hero_entity, "ARMOR", "0"), "armor")

        resources = _int_value(
            _required_tag(player_entity, "RESOURCES"), "max_mana"
        )
        used = _int_value(_tag(player_entity, "RESOURCES_USED", "0"), "mana_used")
        mana = resources - used
        if mana < 0:
            raise LegacyPowerLogError("invalid_mana", "player mana is negative")

        return PlayerView(
            player_id=_int_value(player_id, "player_id", minimum=1),
            side=side,
            name=name,
            hero=hero,
            mana=mana,
            max_mana=resources,
            health=health,
            armor=armor,
            hand=hand,
            board=board,
            deck=deck,
            weapon=weapons[0] if weapons else None,
            secrets=secrets,
        )

    @staticmethod
    def _entity_ref(entity_id: int, entity: Any, side: str, zone: str) -> EntityRef:
        card_id = _card_id(entity)
        if zone == "PLAY":
            card_type = (_tag(entity, "CARDTYPE", "") or "").upper()
            if card_type == "HERO":
                semantic_zone = "hero"
                zone_pos = _int_value(_tag(entity, "ZONE_POSITION", "0"), "zone_pos")
            elif card_type == "WEAPON":
                semantic_zone = "weapon"
                zone_pos = _int_value(_tag(entity, "ZONE_POSITION", "0"), "zone_pos")
            else:
                semantic_zone = "board"
                zone_pos = _int_value(
                    _required_tag(entity, "ZONE_POSITION"), "zone_pos"
                )
        elif zone == "SECRET":
            semantic_zone = "secret"
            zone_pos = _int_value(
                _required_tag(entity, "ZONE_POSITION"), "zone_pos"
            )
        else:
            semantic_zone = zone.lower()
            zone_pos = _int_value(
                _required_tag(entity, "ZONE_POSITION"), "zone_pos"
            )
        return EntityRef(entity_id, card_id, side, semantic_zone, zone_pos)

    def _choices(self, state: Any, my_player: PlayerView) -> tuple[ChoiceView, ...]:
        choices: list[ChoiceView] = []
        step = str(getattr(state, "game_step", "") or "").upper()
        mulligan = "MULLIGAN" in step
        if mulligan:
            choices.append(
                ChoiceView(
                    choice_id=f"mulligan:{getattr(state, 'revision', 0)}",
                    options=(),
                    choice_type="mulligan",
                    min_choices=0,
                    max_choices=len(my_player.hand),
                    is_required=True,
                )
            )

        choice_id = getattr(state, "general_choice_id", None)
        indexes = getattr(state, "general_choice_indexes", set()) or set()
        ready = bool(getattr(state, "general_choice_ready", False))
        owner = getattr(state, "general_choice_player", None)
        local_owner = owner in (None, "") or str(owner) == str(state.my_player_id)
        if choice_id is not None and indexes and ready and local_owner and not mulligan:
            options = self._general_choice_options(
                state, indexes, int(my_player.player_id)
            )
            try:
                discover = getattr(state, "discover_choice_count") is not None
            except Exception as exc:
                raise LegacyPowerLogError("invalid_choice", str(exc)) from exc
            choices.append(
                ChoiceView(
                    choice_id=str(choice_id),
                    options=options,
                    choice_type="discover" if discover else "choice",
                    min_choices=1,
                    max_choices=1,
                    source_entity_id=None,
                    is_required=True,
                )
            )

        power_options = getattr(state, "power_options", {}) or {}
        current_parent = getattr(state, "current_power_option_id", None)
        if current_parent is not None and current_parent in power_options:
            payload = power_options[current_parent]
            suboptions = payload.get("choices", {}) if isinstance(payload, Mapping) else {}
            options = self._power_options(state, suboptions, int(my_player.player_id))
            if options:
                choices.append(
                    ChoiceView(
                        choice_id=f"power-choice:{current_parent}:{getattr(state, 'revision', 0)}",
                        options=options,
                        choice_type="choice",
                        min_choices=1,
                        max_choices=1,
                        source_entity_id=_optional_int(current_parent),
                        is_required=True,
                    )
                )
        return tuple(choices)

    def _general_choice_options(
        self, state: Any, indexes: Iterable[Any], my_player_id: int
    ) -> tuple[EntityRef, ...]:
        options: list[EntityRef] = []
        entity_ids = getattr(state, "general_choice_entity_ids", {}) or {}
        cards = getattr(state, "general_choice_cards", {}) or {}
        normalized_indexes = tuple(
            sorted(_int_value(raw_index, "choice_index") for raw_index in indexes)
        )
        for index in normalized_indexes:
            entity_id = _entity_id(_choice_mapping_get(entity_ids, index))
            existing = state.entity_dict.get(str(entity_id))
            if existing is not None:
                ref = self._ref_for_existing(state, entity_id, existing, my_player_id)
            else:
                card_id = str(_choice_mapping_get(cards, index) or "").strip()
                if not card_id:
                    raise LegacyPowerLogError(
                        "missing_choice_card",
                        f"choice {index} has no card id or existing entity",
                    )
                ref = EntityRef(entity_id, card_id, "friendly", "choice", index + 1)
            options.append(ref)
        return tuple(options)

    def _power_options(
        self, state: Any, suboptions: Mapping[Any, Any], my_player_id: int
    ) -> tuple[EntityRef, ...]:
        options: list[EntityRef] = []
        normalized_suboptions = sorted(
            (
                _int_value(raw_index, "choice_index"),
                info,
            )
            for raw_index, info in suboptions.items()
        )
        for index, info in normalized_suboptions:
            if not isinstance(info, Mapping):
                raise LegacyPowerLogError("invalid_choice", "power option is malformed")
            entity_id = _entity_id(info.get("entity_id"))
            existing = state.entity_dict.get(str(entity_id))
            if existing is not None:
                ref = self._ref_for_existing(state, entity_id, existing, my_player_id)
            else:
                card_id = str(info.get("card_id") or "").strip()
                if not card_id:
                    raise LegacyPowerLogError(
                        "missing_choice_card",
                        f"power option {index} has no card id or existing entity",
                    )
                player = str(info.get("player") or my_player_id)
                side = "friendly" if player == str(my_player_id) else "opponent"
                ref = EntityRef(
                    entity_id,
                    card_id,
                    side,
                    "choice",
                    index + 1,
                )
            options.append(ref)
        return tuple(options)

    def _ref_for_existing(
        self, state: Any, entity_id: int, entity: Any, my_player_id: int
    ) -> EntityRef:
        del my_player_id
        if not _card_id(entity):
            raise LegacyPowerLogError(
                "missing_choice_card",
                f"choice entity {entity_id} has no card id",
            )
        controller = _tag(entity, "CONTROLLER")
        if controller is None:
            raise LegacyPowerLogError(
                "missing_entity_ownership", f"choice entity {entity_id} has no controller"
            )
        if controller not in (state.my_player_id, state.oppo_player_id):
            raise LegacyPowerLogError(
                "invalid_entity_ownership",
                f"choice entity {entity_id} has unknown controller {controller}",
            )
        side = "friendly" if controller == state.my_player_id else "opponent"
        zone = (_tag(entity, "ZONE", "") or "").upper()
        if zone in {"HAND", "DECK", "PLAY", "SECRET", "WEAPON"}:
            return self._entity_ref(entity_id, entity, side, zone)
        return EntityRef(
            entity_id,
            _card_id(entity),
            side,
            "choice",
            _int_value(_tag(entity, "ZONE_POSITION", "0"), "zone_pos"),
        )


def _is_hero(state: Any, entity_id: int, zone: str) -> bool:
    if zone != "PLAY":
        return False
    entity = state.entity_dict[str(entity_id)]
    return (_tag(entity, "CARDTYPE", "") or "").upper() == "HERO"


def _sorted_zone(
    owned: Iterable[tuple[str, EntityRef]],
    legacy_zone: str,
    semantic_zone: str,
    *,
    exclude_hero: bool = False,
    card_type: str | None = None,
    state: Any = None,
) -> tuple[EntityRef, ...]:
    values = []
    for zone, ref in owned:
        if zone != legacy_zone or ref.zone != semantic_zone:
            continue
        if exclude_hero and state is not None and _is_hero(state, ref.entity_id, zone):
            continue
        if card_type is not None:
            entity = state.entity_dict[str(ref.entity_id)] if state is not None else None
            if entity is None or (_tag(entity, "CARDTYPE", "") or "").upper() != card_type:
                continue
        values.append(ref)
    return tuple(sorted(values, key=lambda item: (item.zone_pos, item.entity_id)))


def _reject_duplicate_positions(entities: Iterable[EntityRef], zone: str) -> None:
    ordered = sorted(entities, key=lambda item: (item.zone_pos, item.entity_id))
    for left, right in zip(ordered, ordered[1:]):
        if left.zone_pos == right.zone_pos:
            raise LegacyPowerLogError(
                "ambiguous_zone_position",
                f"duplicate {zone} zone position {left.zone_pos}",
            )


def _choice_mapping_get(mapping: Mapping[Any, Any], index: int) -> Any:
    if index in mapping:
        return mapping[index]
    return mapping.get(str(index))


def _optional_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return _int_value(value, "entity_id", minimum=1)


__all__ = [
    "LegacyLogStateSnapshotMapper",
    "LegacyPowerLogError",
    "LegacyPowerLogPump",
]
