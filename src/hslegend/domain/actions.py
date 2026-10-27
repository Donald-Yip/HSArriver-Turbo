"""Declarative action contracts without coordinates or callbacks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import EntityRef
from .primitives import PrimitiveMixin, deep_freeze_primitive


@dataclass(frozen=True, slots=True)
class ActionIntent(PrimitiveMixin):
    """Semantic action requested by a plan."""

    action_type: str
    source: EntityRef | None = None
    target: EntityRef | None = None
    card_id: str | None = None
    choice_index: int | None = None
    destination: int | None = None
    intent_id: str = ""

    def __post_init__(self) -> None:
        if self.destination is not None:
            if type(self.destination) is not int:
                raise TypeError("destination must be an int or None")
            if self.destination < 0:
                raise ValueError("destination must be non-negative")


@dataclass(frozen=True, slots=True)
class Condition(PrimitiveMixin):
    """Declarative condition evaluated only from snapshots.

    Supported kinds are intentionally small:
    ``entity_exists``, ``entity_absent``, ``entity_zone``, ``entity_zone_pos``,
    ``zone_count``, ``choice_count``, ``turn_number``, ``is_end``,
    ``is_my_turn``, ``player_mana``, ``player_health``,
    ``revision_advanced``, and ``snapshot_changed``.
    """

    kind: str
    entity_id: int | None = None
    side: str | None = None
    zone: str | None = None
    expected: Any = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected", deep_freeze_primitive(self.expected))


@dataclass(frozen=True, slots=True)
class PlanStep(PrimitiveMixin):
    """One declarative step in an ordered plan."""

    step_id: str
    intent: ActionIntent
    preconditions: tuple[Condition, ...] = ()
    postconditions: tuple[Condition, ...] = ()
    timeout_seconds: float = 10.0
    wait_for_change: bool = True

    def __post_init__(self) -> None:
        if type(self.wait_for_change) is not bool:
            raise TypeError("wait_for_change must be a bool")
        object.__setattr__(self, "preconditions", tuple(self.preconditions))
        object.__setattr__(self, "postconditions", tuple(self.postconditions))
        if not self.wait_for_change and self.postconditions:
            raise ValueError("non-waiting step cannot have postconditions")


@dataclass(frozen=True, slots=True)
class ActionPlan(PrimitiveMixin):
    """Snapshot-pinned ordered plan safe to hand to the application layer."""

    plan_id: str
    snapshot_id: str
    snapshot_revision: int
    game_generation: int
    intent: ActionIntent
    steps: tuple[PlanStep, ...]
    preconditions: tuple[Condition, ...] = ()
    postconditions: tuple[Condition, ...] = ()
    timeout_seconds: float = 10.0
    schema_version: int = 1
    confirmation_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.game_generation, int) or isinstance(
            self.game_generation, bool
        ):
            raise TypeError("game_generation must be an int")
        if type(self.confirmation_required) is not bool:
            raise TypeError("confirmation_required must be a bool")
        object.__setattr__(self, "steps", tuple(self.steps))
        object.__setattr__(self, "preconditions", tuple(self.preconditions))
        object.__setattr__(self, "postconditions", tuple(self.postconditions))
