"""Pure target resolution and plan/postcondition rules."""

from __future__ import annotations

import math
from typing import Iterable

from .actions import ActionPlan, Condition
from .cards import CardDefinition
from .models import (
    SUPPORTED_SCHEMA_VERSIONS,
    EntityRef,
    GameSnapshot,
    PlayerView,
)
from .results import PostconditionResult, ValidationResult


def _normalize(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip().lower()


def _valid_timeout(value: object) -> bool:
    try:
        return math.isfinite(value) and value > 0  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


class TargetResolver:
    """Resolve entities from one immutable snapshot."""

    def resolve(
        self,
        snapshot: GameSnapshot,
        *,
        side: str | None = None,
        zone: str | None = None,
        zone_pos: int | None = None,
        entity_id: int | None = None,
    ) -> tuple[EntityRef, ...]:
        normalized_side = _normalize(side) if side is not None else None
        normalized_zone = _normalize(zone) if zone is not None else None
        matches: list[EntityRef] = []
        for entity in snapshot.all_entities():
            if entity_id is not None and entity.entity_id != entity_id:
                continue
            if normalized_side is not None and _normalize(entity.side) != normalized_side:
                continue
            if normalized_zone is not None and _normalize(entity.zone) != normalized_zone:
                continue
            if zone_pos is not None and entity.zone_pos != zone_pos:
                continue
            matches.append(entity)
        return tuple(matches)

    def find_one(
        self,
        snapshot: GameSnapshot,
        *,
        side: str | None = None,
        zone: str | None = None,
        zone_pos: int | None = None,
        entity_id: int | None = None,
    ) -> EntityRef | None:
        matches = self.resolve(
            snapshot,
            side=side,
            zone=zone,
            zone_pos=zone_pos,
            entity_id=entity_id,
        )
        return matches[0] if matches else None

    def contains(
        self, snapshot: GameSnapshot, target: EntityRef, *, exact: bool = True
    ) -> bool:
        observed = snapshot.entity(target.entity_id)
        if observed is None:
            return False
        if exact:
            return observed == target
        return observed.entity_id == target.entity_id


class PlanRuleValidator:
    """Validate a snapshot-pinned declarative plan without side effects."""

    def __init__(self, resolver: TargetResolver | None = None) -> None:
        self._resolver = resolver or TargetResolver()

    @property
    def resolver(self) -> TargetResolver:
        return self._resolver

    def validate(
        self,
        plan: ActionPlan,
        snapshot: GameSnapshot,
        *,
        card: CardDefinition | None = None,
    ) -> ValidationResult:
        if plan.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            return ValidationResult.reject(
                "unsupported_plan_schema", str(plan.schema_version)
            )
        if snapshot.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
            return ValidationResult.reject(
                "unsupported_snapshot_schema", str(snapshot.schema_version)
            )
        if plan.schema_version != snapshot.schema_version:
            return ValidationResult.reject("schema_mismatch")
        if (
            plan.snapshot_id != snapshot.snapshot_id
            or plan.snapshot_revision != snapshot.log_revision
            or plan.game_generation != snapshot.game_generation
        ):
            return ValidationResult.reject("stale_snapshot")
        if not plan.steps:
            return ValidationResult.reject("empty_plan")
        if not _valid_timeout(plan.timeout_seconds) or any(
            not _valid_timeout(step.timeout_seconds) for step in plan.steps
        ):
            return ValidationResult.reject("invalid_timeout")
        step_ids = [step.step_id for step in plan.steps]
        if len(step_ids) != len(set(step_ids)):
            return ValidationResult.reject("duplicate_step_id")

        if card is not None and plan.intent.card_id not in (None, card.card_id):
            return ValidationResult.reject("card_mismatch", card.card_id)
        if plan.intent.card_id is not None:
            for step in plan.steps:
                if step.intent.card_id not in (None, plan.intent.card_id):
                    return ValidationResult.reject(
                        "step_card_mismatch", step.step_id, plan.intent.card_id
                    )
        if card is not None:
            for step in plan.steps:
                if step.intent.card_id not in (None, card.card_id):
                    return ValidationResult.reject(
                        "step_card_mismatch", step.step_id, card.card_id
                    )

        invalid_source = self._invalid_entity(snapshot, plan.intent.source)
        if invalid_source is not None:
            return ValidationResult.reject("invalid_source", invalid_source)
        invalid_target = self._invalid_entity(snapshot, plan.intent.target)
        if invalid_target is not None:
            return ValidationResult.reject("invalid_target", invalid_target)

        for step in plan.steps:
            invalid_source = self._invalid_entity(snapshot, step.intent.source)
            if invalid_source is not None:
                return ValidationResult.reject("invalid_source", invalid_source)
            invalid_target = self._invalid_entity(snapshot, step.intent.target)
            if invalid_target is not None:
                return ValidationResult.reject("invalid_target", invalid_target)

        initial_conditions = (*plan.preconditions, *plan.steps[0].preconditions)
        for index, condition in enumerate(initial_conditions):
            matched, error = _condition_result(condition, snapshot)
            if error is not None:
                return ValidationResult.reject(error, str(index), condition.kind)
            if not matched:
                return ValidationResult.reject(
                    "precondition_failed", str(index), condition.kind
                )
        return ValidationResult.allow()

    def _invalid_entity(
        self, snapshot: GameSnapshot, entity: EntityRef | None
    ) -> str | None:
        if entity is None:
            return None
        if self._resolver.contains(snapshot, entity):
            return None
        observed = snapshot.entity(entity.entity_id)
        if observed is None:
            return f"entity_not_found:{entity.entity_id}"
        return f"entity_changed:{entity.entity_id}"


class PostconditionEvaluator:
    """Judge declarative postconditions from before/after snapshots only."""

    def evaluate(
        self,
        before: GameSnapshot,
        after: GameSnapshot,
        conditions: Iterable[Condition] = (),
    ) -> PostconditionResult:
        condition_tuple = tuple(conditions)
        evidence = (before.snapshot_id, after.snapshot_id)
        if before.schema_version != after.schema_version:
            return PostconditionResult.fail("schema_mismatch", *evidence)
        if (
            before.schema_version not in SUPPORTED_SCHEMA_VERSIONS
            or after.schema_version not in SUPPORTED_SCHEMA_VERSIONS
        ):
            return PostconditionResult.fail("unsupported_snapshot_schema", *evidence)
        if before.game_generation != after.game_generation:
            return PostconditionResult.fail("game_generation_changed", *evidence)
        if after.log_revision < before.log_revision:
            return PostconditionResult.fail("snapshot_regressed", *evidence)
        if after.log_revision == before.log_revision:
            return PostconditionResult.unconfirmed("snapshot_unchanged", *evidence)

        failed: list[int] = []
        for index, condition in enumerate(condition_tuple):
            matched, error = _condition_result(condition, after, before=before)
            if error is not None:
                return PostconditionResult.fail(
                    error,
                    *evidence,
                    failed_condition_indexes=(index,),
                )
            if not matched:
                failed.append(index)
        if failed:
            return PostconditionResult.fail(
                "postcondition_failed",
                *evidence,
                failed_condition_indexes=tuple(failed),
            )
        return PostconditionResult.confirm(after.snapshot_id)


def _condition_result(
    condition: Condition,
    snapshot: GameSnapshot,
    *,
    before: GameSnapshot | None = None,
) -> tuple[bool, str | None]:
    kind = condition.kind
    if kind == "revision_advanced":
        if before is None:
            return False, "unsupported_precondition"
        return snapshot.log_revision > before.log_revision, None
    if kind == "snapshot_changed":
        if before is None:
            return False, "unsupported_precondition"
        return (
            snapshot.snapshot_id != before.snapshot_id
            or snapshot.log_revision != before.log_revision
        ), None
    if kind == "entity_exists":
        return _entity_exists(snapshot, condition), None
    if kind == "entity_absent":
        return not _entity_exists(snapshot, condition), None
    if kind == "entity_zone":
        entity = _condition_entity(snapshot, condition)
        return (
            entity is not None
            and condition.expected is not None
            and _normalize(entity.zone) == _normalize(condition.expected)
        ), None
    if kind == "entity_zone_pos":
        entity = _condition_entity(snapshot, condition)
        return entity is not None and entity.zone_pos == condition.expected, None
    if kind == "zone_count":
        side = _player_for_side(snapshot, condition.side)
        if side is None or condition.zone is None:
            return False, None
        count = sum(
            1
            for entity in side.entities()
            if _normalize(entity.zone) == _normalize(condition.zone)
        )
        return count == condition.expected, None
    if kind == "choice_count":
        return len(snapshot.choices) == condition.expected, None
    if kind == "turn_number":
        return snapshot.turn_number == condition.expected, None
    if kind == "is_end":
        return snapshot.is_end == condition.expected, None
    if kind == "is_my_turn":
        return snapshot.is_my_turn == condition.expected, None
    if kind == "player_mana":
        player = _player_for_side(snapshot, condition.side)
        return player is not None and player.mana == condition.expected, None
    if kind == "player_health":
        player = _player_for_side(snapshot, condition.side)
        return player is not None and player.health == condition.expected, None
    return False, "unsupported_condition"


def _entity_exists(snapshot: GameSnapshot, condition: Condition) -> bool:
    return _condition_entity(snapshot, condition) is not None


def _condition_entity(
    snapshot: GameSnapshot, condition: Condition
) -> EntityRef | None:
    if condition.entity_id is None:
        return None
    return snapshot.entity(condition.entity_id)


def _player_for_side(
    snapshot: GameSnapshot, requested_side: str | None
) -> PlayerView | None:
    if requested_side is None:
        return None
    normalized = _normalize(requested_side)
    if _normalize(snapshot.my_player.side) == normalized:
        return snapshot.my_player
    if _normalize(snapshot.opponent.side) == normalized:
        return snapshot.opponent
    return None
