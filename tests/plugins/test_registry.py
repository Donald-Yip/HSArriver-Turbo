from __future__ import annotations

import unittest

from hslegend.application.planning import (
    PlanContext,
    PlanPlugin,
    PluginConflictError,
    PluginNotFoundError,
    PluginRegistry,
)
from hslegend.domain import (
    ActionIntent,
    ActionPlan,
    CardCapabilitySet,
    CardDefinition,
    PlanStep,
)
from hslegend.plugins import EndTurnPlugin, build_default_registry
from tests.domain.support import snapshot


def plan_for(context: PlanContext, plugin_id: str) -> ActionPlan:
    step = PlanStep(f"{plugin_id}-step", context.intent)
    return ActionPlan(
        plan_id=f"{plugin_id}-plan",
        snapshot_id=context.snapshot.snapshot_id,
        snapshot_revision=context.snapshot.log_revision,
        game_generation=context.snapshot.game_generation,
        intent=context.intent,
        steps=(step,),
    )


class NamedPlugin:
    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id

    def build(self, context: PlanContext) -> ActionPlan:
        return plan_for(context, self.plugin_id)


class PluginRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.snapshot = snapshot()
        self.context = PlanContext(
            self.snapshot,
            ActionIntent("play_card", card_id="card-1", intent_id="intent-1"),
            CardDefinition(
                "card-1",
                capabilities=CardCapabilitySet(targetable=True),
            ),
        )

    def test_exact_card_beats_capability_family_and_default(self) -> None:
        registry = PluginRegistry()
        registry.register(NamedPlugin("default"), default=True)
        registry.register(NamedPlugin("family"), families=("play_card",))
        registry.register(NamedPlugin("capability"), capabilities=("targetable",))
        registry.register(NamedPlugin("exact"), card_ids=("card-1",))

        self.assertEqual("exact", registry.resolve(self.context).plugin_id)

    def test_ambiguous_matches_fail_closed(self) -> None:
        registry = PluginRegistry()
        registry.register(NamedPlugin("a"), families=("play_card",))
        registry.register(NamedPlugin("b"), capabilities=("targetable",))

        with self.assertRaises(PluginConflictError):
            registry.resolve(self.context)

    def test_same_tier_duplicate_fails_closed(self) -> None:
        registry = PluginRegistry()
        registry.register(NamedPlugin("a"), families=("play_card",))
        registry.register(NamedPlugin("b"), families=("play_card",))

        with self.assertRaises(PluginConflictError):
            registry.resolve(self.context)

    def test_default_used_only_when_no_specific_plugin_matches(self) -> None:
        registry = PluginRegistry()
        registry.register(NamedPlugin("default"), default=True)

        self.assertEqual("default", registry.resolve(self.context).plugin_id)

    def test_no_match_is_explicit(self) -> None:
        with self.assertRaises(PluginNotFoundError):
            PluginRegistry().resolve(self.context)

    def test_plugin_protocol_is_runtime_checkable(self) -> None:
        self.assertIsInstance(EndTurnPlugin(), PlanPlugin)
        self.assertIsInstance(build_default_registry(), PluginRegistry)


if __name__ == "__main__":
    unittest.main()
