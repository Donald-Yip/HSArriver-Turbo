"""Static plugin registry for the MVP action families."""

from __future__ import annotations

from hslegend.application.planning import PlanContext, PluginRegistry

from .defaults import (
    AttackPlugin,
    EndTurnPlugin,
    HeroPowerPlugin,
    LocationPlugin,
    MulliganPlugin,
    PlayCardPlugin,
    TargetedCardPlugin,
)


def _play_family(context: PlanContext) -> bool:
    return context.intent.action_type.strip().lower() == "play_card"


def _targeted(context: PlanContext) -> bool:
    action = context.intent.action_type.strip().lower()
    return action in {"play_card", "targeted_card"} and context.is_targeted


def _location(context: PlanContext) -> bool:
    action = context.intent.action_type.strip().lower()
    return action in {
        "play_card",
        "play_location",
        "location",
        "use_location",
    } and context.capability("location")


def _plain_play(context: PlanContext) -> bool:
    return (
        _play_family(context)
        and not context.is_targeted
        and not context.capability("location")
    )


def register_default_plugins(registry: PluginRegistry) -> PluginRegistry:
    """Register exactly one deterministic plugin for each MVP family."""

    if not isinstance(registry, PluginRegistry):
        raise TypeError("registry must be a PluginRegistry")

    registry.register(
        MulliganPlugin(),
        families=("mulligan", "mulligan_keep", "mulligan_replace"),
    )
    registry.register(
        TargetedCardPlugin(),
        capabilities=("targetable", "hand_target", "board_target"),
        families=("targeted_card",),
        predicate=_targeted,
    )
    registry.register(
        LocationPlugin(),
        capabilities=("location",),
        families=("play_location", "location", "use_location"),
        predicate=_location,
    )
    registry.register(
        PlayCardPlugin(),
        families=("play_card",),
        predicate=_plain_play,
    )
    registry.register(AttackPlugin(), families=("attack",))
    registry.register(
        HeroPowerPlugin(), families=("hero_power", "hero-power")
    )
    registry.register(EndTurnPlugin(), families=("end_turn", "end-turn"))
    return registry


def build_default_registry() -> PluginRegistry:
    return register_default_plugins(PluginRegistry())


__all__ = ["build_default_registry", "register_default_plugins"]
