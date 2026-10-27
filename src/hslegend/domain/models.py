"""Immutable game observation contracts."""

from __future__ import annotations

from dataclasses import dataclass

from .primitives import PrimitiveMixin


SUPPORTED_SCHEMA_VERSIONS = frozenset({1})


@dataclass(frozen=True, slots=True)
class EntityRef(PrimitiveMixin):
    """Stable semantic identity for an entity in one snapshot."""

    entity_id: int
    card_id: str
    side: str
    zone: str
    zone_pos: int


@dataclass(frozen=True, slots=True)
class PlayerView(PrimitiveMixin):
    """Player state needed by declarative plans and postconditions."""

    player_id: int
    side: str
    name: str = ""
    hero: EntityRef | None = None
    mana: int = 0
    max_mana: int = 0
    health: int = 0
    armor: int = 0
    hand: tuple[EntityRef, ...] = ()
    board: tuple[EntityRef, ...] = ()
    deck: tuple[EntityRef, ...] = ()
    weapon: EntityRef | None = None
    secrets: tuple[EntityRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "hand", tuple(self.hand))
        object.__setattr__(self, "board", tuple(self.board))
        object.__setattr__(self, "deck", tuple(self.deck))
        object.__setattr__(self, "secrets", tuple(self.secrets))

    def referenced_entities(self) -> tuple[EntityRef, ...]:
        """Return every referenced entity, including duplicate ids."""

        return tuple(
            entity
            for entity in (
                self.hero,
                self.weapon,
                *self.hand,
                *self.board,
                *self.deck,
                *self.secrets,
            )
            if entity is not None
        )

    def entities(self) -> tuple[EntityRef, ...]:
        values: list[EntityRef] = []
        seen_ids: set[int] = set()
        for entity in self.referenced_entities():
            if entity.entity_id not in seen_ids:
                seen_ids.add(entity.entity_id)
                values.append(entity)
        return tuple(values)


@dataclass(frozen=True, slots=True)
class ChoiceView(PrimitiveMixin):
    """A normalized choice surface exposed by the game."""

    choice_id: str
    options: tuple[EntityRef, ...] = ()
    choice_type: str = "choice"
    min_choices: int = 0
    max_choices: int = 1
    source_entity_id: int | None = None
    is_required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", tuple(self.options))


@dataclass(frozen=True, slots=True)
class GameSnapshot(PrimitiveMixin):
    """Immutable observation and authoritative revision of a game."""

    schema_version: int
    snapshot_id: str
    log_revision: int
    game_generation: int
    observed_at: float
    turn_number: int
    is_my_turn: bool
    is_end: bool
    my_player: PlayerView
    opponent: PlayerView
    choices: tuple[ChoiceView, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "choices", tuple(self.choices))
        seen_ids: set[int] = set()
        for entity in (
            *self.my_player.referenced_entities(),
            *self.opponent.referenced_entities(),
            *(option for choice in self.choices for option in choice.options),
        ):
            if entity.entity_id in seen_ids:
                raise ValueError(f"duplicate entity_id: {entity.entity_id}")
            seen_ids.add(entity.entity_id)

    def all_entities(self) -> tuple[EntityRef, ...]:
        """Return entities in deterministic player/zone/choice order."""

        values: list[EntityRef] = []
        seen_ids: set[int] = set()
        for entity in (*self.my_player.entities(), *self.opponent.entities()):
            if entity.entity_id not in seen_ids:
                seen_ids.add(entity.entity_id)
                values.append(entity)
        for choice in self.choices:
            for option in choice.options:
                if option.entity_id not in seen_ids:
                    seen_ids.add(option.entity_id)
                    values.append(option)
        return tuple(values)

    def entity(self, entity_id: int) -> EntityRef | None:
        for entity in self.all_entities():
            if entity.entity_id == entity_id:
                return entity
        return None

    def entities_for_side(self, side: str) -> tuple[EntityRef, ...]:
        normalized = _normalize(side)
        return tuple(
            entity for entity in self.all_entities() if _normalize(entity.side) == normalized
        )

    def entities_in_zone(self, zone: str) -> tuple[EntityRef, ...]:
        normalized = _normalize(zone)
        return tuple(
            entity for entity in self.all_entities() if _normalize(entity.zone) == normalized
        )


def _normalize(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip().lower()
