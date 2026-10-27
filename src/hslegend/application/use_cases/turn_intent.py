"""Pure turn-instruction parsing over one immutable game snapshot."""

from __future__ import annotations

import re
from dataclasses import dataclass

from hslegend.domain import ActionIntent, EntityRef, GameSnapshot


_PLAY_CARD = re.compile(r"^打出([1-9]\d*)号位(随从|法术|武器|地标|英雄)$")
_MINION_ATTACK = re.compile(r"^操作([1-9]\d*)号位随从攻击$")
_HERO_ATTACK = "操作我方英雄攻击"
_LOCATION = re.compile(r"^操作([1-9]\d*)号位地标$")
_HERO_POWER = "使用英雄技能"
_END_TURN = "结束回合"
_TARGET_SLOT = re.compile(
    r"^目标是(对方|敌方|我方|己方)([1-9]\d*)号位(随从|地标|武器|英雄)?$"
)
_TARGET_HERO = re.compile(r"^目标是(对方|敌方|我方|己方)英雄$")
_DESTINATION = re.compile(r"^放置于我方([1-9]\d*)号位$")
_ENEMY_SIDES = frozenset({"对方", "敌方"})


class TurnIntentError(ValueError):
    """Stable, fail-closed reason that a turn instruction is unusable."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class _ActionSpec:
    action_type: str
    slot: int | None = None
    source_kind: str = ""


class TurnIntentParser:
    """Parse the exact MVP turn grammar against one snapshot.

    Text slots are one-based logical positions sorted by ``zone_pos``. Hand and
    board indexes are therefore deterministic even when snapshots expose
    non-contiguous or unordered zone positions.
    """

    def parse(self, snapshot: GameSnapshot, instruction: str) -> ActionIntent:
        if not isinstance(snapshot, GameSnapshot):
            raise TypeError("snapshot must be a GameSnapshot")
        if not isinstance(instruction, str):
            raise TypeError("instruction must be text")

        lines = [line.strip() for line in instruction.splitlines() if line.strip()]
        if not lines:
            raise TurnIntentError("empty_instruction")

        action_spec = self._parse_action(lines[0])
        action_count = sum(1 for line in lines if self._is_action_line(line))
        if action_count > 1:
            raise TurnIntentError("multiple_actions")

        target: EntityRef | None = None
        destination: int | None = None
        for line in lines[1:]:
            if _TARGET_SLOT.fullmatch(line) or _TARGET_HERO.fullmatch(line):
                if target is not None:
                    raise TurnIntentError("duplicate_target")
                target = self._resolve_target(snapshot, line)
            elif _DESTINATION.fullmatch(line):
                if destination is not None:
                    raise TurnIntentError("duplicate_destination")
                destination = self._parse_destination(snapshot, line)
            else:
                raise TurnIntentError("unsupported_instruction")

        return self._build_intent(snapshot, action_spec, target, destination)

    def _parse_action(self, line: str) -> _ActionSpec:
        if line == _END_TURN:
            return _ActionSpec("end_turn")
        if line == _HERO_POWER:
            return _ActionSpec("hero_power", source_kind="hero")
        if line == _HERO_ATTACK:
            return _ActionSpec("attack", source_kind="hero")
        match = _PLAY_CARD.fullmatch(line)
        if match is not None:
            return _ActionSpec("play_card", int(match.group(1)), "hand")
        match = _MINION_ATTACK.fullmatch(line)
        if match is not None:
            return _ActionSpec("attack", int(match.group(1)), "board")
        match = _LOCATION.fullmatch(line)
        if match is not None:
            return _ActionSpec("use_location", int(match.group(1)), "board")
        raise TurnIntentError("unsupported_instruction")

    @staticmethod
    def _is_action_line(line: str) -> bool:
        return bool(
            line == _END_TURN
            or line == _HERO_POWER
            or line == _HERO_ATTACK
            or _PLAY_CARD.fullmatch(line)
            or _MINION_ATTACK.fullmatch(line)
            or _LOCATION.fullmatch(line)
        )

    def _build_intent(
        self,
        snapshot: GameSnapshot,
        spec: _ActionSpec,
        target: EntityRef | None,
        destination: int | None,
    ) -> ActionIntent:
        if spec.action_type == "end_turn":
            if target is not None:
                raise TurnIntentError("target_not_allowed")
            if destination is not None:
                raise TurnIntentError("destination_not_allowed")
            return ActionIntent(action_type="end_turn")

        if spec.source_kind == "hero":
            source = self._required_entity(
                snapshot.my_player.hero,
                expected_side="friendly",
                expected_zone="hero",
                reason_code="source_hero_missing",
            )
            choice_index = None
        elif spec.source_kind == "hand":
            source = self._slot_entity(
                snapshot.my_player.hand,
                spec.slot,
                "source_slot_invalid",
                expected_side="friendly",
                expected_zone="hand",
            )
            choice_index = spec.slot - 1 if spec.slot is not None else None
        elif spec.source_kind == "board":
            source = self._slot_entity(
                snapshot.my_player.board,
                spec.slot,
                "source_slot_invalid",
                expected_side="friendly",
                expected_zone="board",
            )
            choice_index = spec.slot - 1 if spec.slot is not None else None
        else:
            raise TurnIntentError("unsupported_instruction")

        if spec.action_type == "attack" and target is None:
            raise TurnIntentError("target_required")
        if destination is not None and spec.action_type != "play_card":
            raise TurnIntentError("destination_not_allowed")
        if target is not None and spec.action_type == "end_turn":
            raise TurnIntentError("target_not_allowed")

        card_id = (
            source.card_id
            if spec.action_type in {"play_card", "use_location"}
            else None
        )
        return ActionIntent(
            action_type=spec.action_type,
            source=source,
            target=target,
            card_id=card_id,
            choice_index=choice_index,
            destination=destination,
        )

    def _resolve_target(self, snapshot: GameSnapshot, line: str) -> EntityRef:
        hero_match = _TARGET_HERO.fullmatch(line)
        if hero_match is not None:
            side = hero_match.group(1)
            enemy = side in _ENEMY_SIDES
            player = snapshot.opponent if enemy else snapshot.my_player
            return self._required_entity(
                player.hero,
                expected_side="opponent" if enemy else "friendly",
                expected_zone="hero",
                reason_code="target_hero_missing",
            )

        match = _TARGET_SLOT.fullmatch(line)
        if match is None:
            raise TurnIntentError("unsupported_instruction")
        side, raw_slot, kind = match.groups()
        enemy = side in _ENEMY_SIDES
        player = snapshot.opponent if enemy else snapshot.my_player
        expected_side = "opponent" if enemy else "friendly"

        if kind == "英雄":
            if int(raw_slot) != 1:
                raise TurnIntentError("target_slot_invalid")
            return self._required_entity(
                player.hero,
                expected_side=expected_side,
                expected_zone="hero",
                reason_code="target_hero_missing",
            )
        if kind == "武器":
            if int(raw_slot) != 1:
                raise TurnIntentError("target_slot_invalid")
            return self._required_entity(
                player.weapon,
                expected_side=expected_side,
                expected_zone="weapon",
                reason_code="target_weapon_missing",
            )
        return self._slot_entity(
            player.board,
            int(raw_slot),
            "target_slot_invalid",
            expected_side=expected_side,
            expected_zone="board",
        )

    def _parse_destination(self, snapshot: GameSnapshot, line: str) -> int:
        match = _DESTINATION.fullmatch(line)
        if match is None:
            raise TurnIntentError("unsupported_instruction")
        slot = int(match.group(1))
        # Placement may append after the final board entity, hence N <= size + 1.
        if slot > len(snapshot.my_player.board) + 1:
            raise TurnIntentError("destination_slot_invalid")
        return slot - 1

    @staticmethod
    def _required_entity(
        entity: EntityRef | None,
        *,
        expected_side: str,
        expected_zone: str,
        reason_code: str,
    ) -> EntityRef:
        if entity is None:
            raise TurnIntentError(reason_code)
        if (
            entity.side.strip().lower() != expected_side
            or entity.zone.strip().lower() != expected_zone
        ):
            raise TurnIntentError("invalid_slot_ownership")
        return entity

    @classmethod
    def _slot_entity(
        cls,
        entities: tuple[EntityRef, ...],
        slot: int | None,
        reason_code: str,
        *,
        expected_side: str,
        expected_zone: str,
    ) -> EntityRef:
        if slot is None or slot < 1:
            raise TurnIntentError(reason_code)
        for entity in entities:
            cls._required_entity(
                entity,
                expected_side=expected_side,
                expected_zone=expected_zone,
                reason_code="invalid_slot_ownership",
            )
        ordered = sorted(entities, key=lambda entity: (entity.zone_pos, entity.entity_id))
        if any(
            left.zone_pos == right.zone_pos
            for left, right in zip(ordered, ordered[1:])
        ):
            raise TurnIntentError("ambiguous_slot")
        if slot > len(ordered):
            raise TurnIntentError(reason_code)
        return ordered[slot - 1]


def parse_turn_intent(snapshot: GameSnapshot, instruction: str) -> ActionIntent:
    """Convenience wrapper for one deterministic parse."""

    return TurnIntentParser().parse(snapshot, instruction)


__all__ = ["TurnIntentError", "TurnIntentParser", "parse_turn_intent"]
