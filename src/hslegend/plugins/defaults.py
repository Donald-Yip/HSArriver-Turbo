"""Static MVP plugins returning declarative action plans."""

from __future__ import annotations

from hslegend.application.planning import PlanContext
from hslegend.domain import ActionPlan, Condition, PlanStep


class PluginBuildError(ValueError):
    """Raised when a context lacks information needed for a declarative plan."""


def _plan(
    context: PlanContext,
    plugin_id: str,
    *,
    preconditions: tuple[Condition, ...] = (),
    postconditions: tuple[Condition, ...] = (),
) -> ActionPlan:
    stem = context.intent.intent_id or (
        f"{context.intent.action_type}:{context.snapshot.snapshot_id}"
    )
    step = PlanStep(
        step_id=f"{plugin_id}:{stem}:step-1",
        intent=context.intent,
        preconditions=preconditions,
        postconditions=postconditions,
        timeout_seconds=10.0,
    )
    return ActionPlan(
        plan_id=f"{plugin_id}:{stem}",
        snapshot_id=context.snapshot.snapshot_id,
        snapshot_revision=context.snapshot.log_revision,
        game_generation=context.snapshot.game_generation,
        intent=context.intent,
        steps=(step,),
        preconditions=preconditions,
        postconditions=postconditions,
        timeout_seconds=10.0,
    )


def _entity_exists(entity: object | None) -> tuple[Condition, ...]:
    entity_id = getattr(entity, "entity_id", None)
    return (Condition("entity_exists", entity_id=entity_id),) if entity_id is not None else ()


def _my_turn() -> tuple[Condition, ...]:
    return (Condition("is_my_turn", expected=True),)


class MulliganPlugin:
    plugin_id = "mulligan"

    def build(self, context: PlanContext) -> ActionPlan:
        return _plan(
            context,
            self.plugin_id,
            preconditions=_my_turn(),
            postconditions=(Condition("snapshot_changed"),),
        )


class PlayCardPlugin:
    plugin_id = "play-card"

    def build(self, context: PlanContext) -> ActionPlan:
        if context.intent.card_id is None and context.card is None:
            raise PluginBuildError("play_card requires a card_id or card definition")
        return _plan(
            context,
            self.plugin_id,
            preconditions=(*_my_turn(), *_entity_exists(context.intent.source)),
            postconditions=(Condition("snapshot_changed"),),
        )


class TargetedCardPlugin:
    plugin_id = "targeted-card"

    def build(self, context: PlanContext) -> ActionPlan:
        if context.intent.target is None:
            raise PluginBuildError("targeted card requires a target entity")
        return _plan(
            context,
            self.plugin_id,
            preconditions=(
                *_my_turn(),
                *_entity_exists(context.intent.source),
                *_entity_exists(context.intent.target),
            ),
            postconditions=(Condition("snapshot_changed"),),
        )


class AttackPlugin:
    plugin_id = "attack"

    def build(self, context: PlanContext) -> ActionPlan:
        if context.intent.source is None or context.intent.target is None:
            raise PluginBuildError("attack requires source and target entities")
        return _plan(
            context,
            self.plugin_id,
            preconditions=(
                *_my_turn(),
                *_entity_exists(context.intent.source),
                *_entity_exists(context.intent.target),
            ),
            postconditions=(Condition("snapshot_changed"),),
        )


class HeroPowerPlugin:
    plugin_id = "hero-power"

    def build(self, context: PlanContext) -> ActionPlan:
        return _plan(
            context,
            self.plugin_id,
            preconditions=(*_my_turn(), *_entity_exists(context.intent.source)),
            postconditions=(Condition("snapshot_changed"),),
        )


class LocationPlugin:
    plugin_id = "location"

    def build(self, context: PlanContext) -> ActionPlan:
        return _plan(
            context,
            self.plugin_id,
            preconditions=(*_my_turn(), *_entity_exists(context.intent.source)),
            postconditions=(Condition("snapshot_changed"),),
        )


class EndTurnPlugin:
    plugin_id = "end-turn"

    def build(self, context: PlanContext) -> ActionPlan:
        return _plan(
            context,
            self.plugin_id,
            preconditions=_my_turn(),
            postconditions=(Condition("is_my_turn", expected=False),),
        )


__all__ = [
    "AttackPlugin",
    "EndTurnPlugin",
    "HeroPowerPlugin",
    "LocationPlugin",
    "MulliganPlugin",
    "PlayCardPlugin",
    "PluginBuildError",
    "TargetedCardPlugin",
]
