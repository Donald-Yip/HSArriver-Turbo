"""Plan generation through a deterministic plugin registry."""

from __future__ import annotations

from hslegend.application.ports import CardCatalogPort
from hslegend.domain import ActionIntent, ActionPlan, GameSnapshot

from .contracts import PlanContext
from .registry import PluginRegistry


class PlanBuilderError(RuntimeError):
    """Base class for plan-generation failures."""


class MissingCardDefinitionError(PlanBuilderError):
    """Raised when an intent names a card absent from the catalog."""


class InvalidPluginPlanError(PlanBuilderError):
    """Raised when a plugin does not return a declarative ActionPlan."""


class PlanBuilder:
    """Resolve one plugin and build a snapshot-pinned declarative plan."""

    def __init__(self, registry: PluginRegistry, card_catalog: CardCatalogPort) -> None:
        if not isinstance(registry, PluginRegistry):
            raise TypeError("registry must be a PluginRegistry")
        self._registry = registry
        self._card_catalog = card_catalog

    @property
    def registry(self) -> PluginRegistry:
        return self._registry

    def build(self, snapshot: GameSnapshot, intent: ActionIntent) -> ActionPlan:
        if not isinstance(snapshot, GameSnapshot):
            raise TypeError("snapshot must be a GameSnapshot")
        if not isinstance(intent, ActionIntent):
            raise TypeError("intent must be an ActionIntent")

        card = None
        if intent.card_id is not None:
            card = self._card_catalog.get_card(intent.card_id)
            if card is None:
                raise MissingCardDefinitionError(
                    f"card not found in catalog: {intent.card_id}"
                )

        context = PlanContext(snapshot=snapshot, intent=intent, card=card)
        plugin = self._registry.resolve(context)
        plan = plugin.build(context)
        if not isinstance(plan, ActionPlan):
            raise InvalidPluginPlanError(
                f"plugin {plugin.plugin_id!r} returned {type(plan).__name__}"
            )
        return plan


__all__ = [
    "InvalidPluginPlanError",
    "MissingCardDefinitionError",
    "PlanBuilder",
    "PlanBuilderError",
]
